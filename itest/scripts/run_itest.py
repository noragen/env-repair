import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SCENARIOS = ROOT / "scenarios"
ENVS_DIR = ROOT / "envs"
RUNS_DIR = ROOT / "runs"
REPORTS_DIR = ROOT / "reports"

PREFIX = "envrepair_itest_"


def _maybe_wrap_windows_bat(cmd):
    """On Windows, mamba/conda are often .bat shims. Wrap via cmd.exe so it resolves."""
    if os.name != "nt" or not cmd:
        return cmd
    base = os.path.basename(cmd[0]).lower()
    if base in {"mamba", "mamba.bat", "conda", "conda.bat"}:
        return ["cmd", "/d", "/c", subprocess.list2cmdline(cmd)]
    return cmd


def _run(cmd, *, cwd=None, env=None, capture=True):
    # Always show the command in logs
    print("[cmd]", subprocess.list2cmdline(cmd))
    cmd2 = _maybe_wrap_windows_bat(cmd)
    if capture:
        p = subprocess.run(cmd2, cwd=cwd, env=env, text=True, capture_output=True)
        return p.returncode, p.stdout, p.stderr
    p = subprocess.run(cmd2, cwd=cwd, env=env)
    return p.returncode, "", ""


def _run_json(cmd, *, cwd=None, env=None):
    rc, out, err = _run(cmd, cwd=cwd, env=env, capture=True)
    data = None
    if out.strip():
        try:
            data = json.loads(out)
        except Exception:
            data = None
    return rc, data, out, err


def _ensure_dirs():
    for d in [SCENARIOS, ENVS_DIR, RUNS_DIR, REPORTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def list_scenarios():
    _ensure_dirs()
    items = sorted(SCENARIOS.glob("*.json"))
    for p in items:
        data = json.loads(p.read_text(encoding="utf-8"))
        print(f"{data.get('id')}: {data.get('description')}")


def _mamba_exe():
    return "mamba"


def _env_path(scenario_id: str) -> Path:
    return ENVS_DIR / scenario_id


def _env_name(scenario_id: str) -> str:
    return PREFIX + scenario_id.lower()


def create_env(*, scenario, use_names: bool):
    py = scenario.get("python", "3.10")
    conda_pkgs = scenario.get("conda_packages") or []
    cmd = [_mamba_exe(), "create", "-y", "--json"]
    if use_names:
        cmd += ["-n", _env_name(scenario["id"])]
    else:
        cmd += ["-p", str(_env_path(scenario["id"])).replace("/", "\\")]
    cmd += [f"python={py}"]
    cmd += list(conda_pkgs)
    rc, _data, out, err = _run_json(cmd)
    return rc, out, err


def run_pip(*, scenario_id: str, use_names: bool, args):
    cmd = [_mamba_exe(), "run"]
    if use_names:
        cmd += ["-n", _env_name(scenario_id)]
    else:
        cmd += ["-p", str(_env_path(scenario_id)).replace("/", "\\")]
    cmd += ["python", "-m", "pip"] + list(args)
    return _run(cmd)


def find_site_packages(*, scenario_id: str, use_names: bool) -> Path:
    # For path-based conda envs on Windows, site-packages is deterministic.
    if os.name == "nt" and not use_names:
        return _env_path(scenario_id) / "Lib" / "site-packages"

    # Fallback: ask Python for site-packages
    cmd = [_mamba_exe(), "run"]
    if use_names:
        cmd += ["-n", _env_name(scenario_id)]
    else:
        cmd += ["-p", str(_env_path(scenario_id)).replace("/", "\\")]
    cmd += ["python", "-c", "import site; print(site.getsitepackages()[0])"]
    rc, out, err = _run(cmd)
    if rc != 0:
        raise RuntimeError(err)
    sp = out.strip().splitlines()[-1].strip()
    return Path(sp)


def poison_delete_file(*, scenario, use_names: bool):
    scenario_id = scenario["id"]
    sp = find_site_packages(scenario_id=scenario_id, use_names=use_names)
    rel = (scenario.get("poison") or {}).get("site_packages_rel")
    if not rel:
        raise RuntimeError("poison.site_packages_rel missing")
    target = sp / rel
    if not target.exists():
        raise RuntimeError(f"Target to delete not found: {target}")
    backup = target.with_suffix(target.suffix + ".bak")
    if backup.exists():
        backup.unlink()
    target.replace(backup)
    return {"deleted": str(target), "backup": str(backup)}


def poison_corrupt_conda_meta(*, scenario, use_names: bool):
    scenario_id = scenario["id"]
    envp = _env_path(scenario_id) if not use_names else None
    if use_names:
        raise RuntimeError("corrupt_conda_meta currently supports path envs only")
    cm = envp / "conda-meta"
    glob_pat = (scenario.get("poison") or {}).get("target_glob") or "*.json"
    matches = sorted(cm.glob(glob_pat))
    if not matches:
        raise RuntimeError(f"No conda-meta match for {glob_pat} in {cm}")
    target = matches[0]
    data = target.read_bytes()
    backup = target.with_suffix(target.suffix + ".bak")
    backup.write_bytes(data)
    corruption = (scenario.get("poison") or {}).get("corruption", "truncate")

    if corruption == "truncate":
        target.write_bytes(data[: max(10, len(data) // 10)])
    elif corruption == "invalid_json":
        target.write_text("{not-json", encoding="utf-8")
    elif corruption == "remove_depends":
        import json as _json
        obj = _json.loads(data.decode("utf-8"))
        if isinstance(obj, dict) and "depends" in obj:
            obj.pop("depends", None)
        target.write_text(_json.dumps(obj, indent=2), encoding="utf-8")
    else:
        raise RuntimeError(f"Unknown corruption mode: {corruption}")

    return {"target": str(target), "backup": str(backup), "corruption": corruption}


def poison_duplicate_dist_info(*, scenario, use_names: bool):
    scenario_id = scenario["id"]
    sp = find_site_packages(scenario_id=scenario_id, use_names=use_names)
    poison = scenario["poison"]
    glob_pat = poison["target_dist_info_glob"]
    fake_ver = poison.get("fake_version", "9.9.9")

    matches = sorted(sp.glob(glob_pat))
    if not matches:
        raise RuntimeError(f"No dist-info match for {glob_pat} in {sp}")
    src = matches[0]

    # Create a second dist-info with a different version string to trigger scan_dist_info().
    # Example: requests-2.31.0.dist-info -> requests-9.9.9.dist-info
    base = src.name[:-len(".dist-info")]
    if "-" not in base:
        raise RuntimeError(f"Unexpected dist-info name: {src.name}")
    name, _ver = base.rsplit("-", 1)
    dst_name = f"{name}-{fake_ver}.dist-info"
    dst = sp / dst_name

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return {"src": str(src), "dst": str(dst)}


def poison_install_no_deps(*, scenario, use_names: bool):
    poison = scenario.get("poison") or {}
    specs = poison.get("specs") or []
    if not isinstance(specs, list) or not specs:
        raise RuntimeError("poison.specs missing for install_no_deps")

    cmd = [_mamba_exe(), "install", "-y", "--no-deps", "--json"]
    if use_names:
        cmd += ["-n", _env_name(scenario["id"])]
    else:
        cmd += ["-p", str(_env_path(scenario["id"])).replace("/", "\\")]
    cmd += list(specs)

    rc, data, out, err = _run_json(cmd)
    if rc != 0:
        raise RuntimeError(f"install_no_deps failed\n{err or out}")
    linked = ((((data or {}).get("actions") or {}).get("LINK")) or [])
    return {"specs": list(specs), "linked": len(linked)}


def repair_install_specs(*, scenario, use_names: bool):
    repair = scenario.get("repair") or {}
    specs = repair.get("specs") or []
    if not isinstance(specs, list) or not specs:
        raise RuntimeError("repair.specs missing for install_specs")

    cmd = [_mamba_exe(), "install", "-y", "--json"]
    if repair.get("force_reinstall"):
        cmd.append("--force-reinstall")
    if use_names:
        cmd += ["-n", _env_name(scenario["id"])]
    else:
        cmd += ["-p", str(_env_path(scenario["id"])).replace("/", "\\")]
    cmd += list(specs)
    rc, _data, out, err = _run_json(cmd)
    return rc, out, err


def probe_solver_inconsistent(*, scenario_id: str, use_names: bool):
    """
    Probe for conda solver inconsistency warnings via dry-run.
    Returns dict with bool `inconsistent` and parsed `packages`.
    """
    env_arg = _env_name(scenario_id) if use_names else str(_env_path(scenario_id)).replace("/", "\\")
    if shutil.which("conda"):
        cmd = ["conda", "install", "-y", "--dry-run", "--solver", "classic", "--json", "python"]
        cmd += ["-n", env_arg] if use_names else ["-p", env_arg]
    else:
        cmd = [_mamba_exe(), "install", "-y", "--dry-run", "--json", "python"]
        cmd += ["-n", env_arg] if use_names else ["-p", env_arg]

    rc, data, out, err = _run_json(cmd)
    blob = "\n".join(
        part
        for part in [out, err, json.dumps(data) if isinstance(data, (dict, list)) else ""]
        if part
    )
    text = blob.lower()
    inconsistent = ("environment is inconsistent" in text) or ("the environment is inconsistent" in text)

    pkgs = []
    seen = set()
    for line in blob.splitlines():
        if "is causing the inconsistency" in line.lower():
            m = re.search(r"::([a-z0-9_.-]+)==", line, flags=re.I)
            if m:
                name = m.group(1)
                if name not in seen:
                    seen.add(name)
                    pkgs.append(name)

    return {
        "command": cmd,
        "rc": rc,
        "inconsistent": bool(inconsistent),
        "packages": pkgs,
        "stdout": out[-1200:],
        "stderr": err[-1200:],
    }


def run_env_repair_once(*, scenario_id: str, use_names: bool, subcmd=None, args=None):
    args = args or []
    env_target = _env_name(scenario_id) if use_names else str(_env_path(scenario_id))
    cmd = [sys.executable, str(REPO / "env_repair.py"), "--env"]
    cmd.append(env_target)
    if subcmd:
        cmd.append(subcmd)
        step_args = list(args)
        if "--env" not in step_args:
            step_args = ["--env", env_target] + step_args
        cmd += step_args
    else:
        cmd += list(args)
    if "--json" not in cmd:
        cmd.append("--json")
    env = dict(os.environ)
    # Force non-interactive approval inside env-repair for CI/itest runs.
    env["ENV_REPAIR_AUTO_YES"] = "1"
    return _run(cmd, cwd=str(REPO), env=env)


def run_env_repair(*, scenario, use_names: bool):
    scenario_id = scenario["id"]
    er = scenario.get("env_repair", {}) or {}

    pipeline = er.get("pipeline")
    if pipeline and isinstance(pipeline, list):
        # run steps sequentially
        last = (0, "", "")
        for step in pipeline:
            if not isinstance(step, dict):
                continue
            subcmd = step.get("subcommand")
            args = step.get("args") or []
            ok_rc = step.get("ok_rc")
            last = run_env_repair_once(scenario_id=scenario_id, use_names=use_names, subcmd=subcmd, args=args)
            if ok_rc is None:
                # default behavior: any nonzero rc fails
                if last[0] != 0:
                    return last
            else:
                # allow scenarios where a subcommand is expected to report problems (e.g. rc=1)
                allowed = set(ok_rc) if isinstance(ok_rc, list) else {int(ok_rc)}
                if last[0] not in allowed:
                    return last
        return last

    # Backwards compatible single-step config
    subcmd = er.get("subcommand")
    args = er.get("args") or []
    return run_env_repair_once(scenario_id=scenario_id, use_names=use_names, subcmd=subcmd, args=args)


def scan_env_repair(*, scenario_id: str, use_names: bool):
    cmd = [sys.executable, str(REPO / "env_repair.py"), "--env"]
    cmd.append(_env_name(scenario_id) if use_names else str(_env_path(scenario_id)))
    cmd += ["--json"]
    rc, out, err = _run(cmd, cwd=str(REPO))
    report = None
    if out.strip():
        try:
            report = json.loads(out)
        except Exception:
            report = None
    return rc, report, out, err


def count_issue(report, issue_type: str) -> int:
    if not report:
        return 0
    # env-repair --json prints the raw report, which is a list of env objects.
    envs = report if isinstance(report, list) else (report.get("envs") or [])
    cnt = 0
    for e in envs:
        if not isinstance(e, dict):
            continue
        for issue in (e.get("issues") or []):
            if isinstance(issue, dict) and issue.get("type") == issue_type:
                cnt += 1
    return cnt


def cleanup_env(*, scenario_id: str, use_names: bool):
    if use_names:
        name = _env_name(scenario_id)
        if not name.startswith(PREFIX):
            raise RuntimeError("Refusing to delete env not in prefix")
        rc, _data, out, err = _run_json([_mamba_exe(), "env", "remove", "-y", "--json", "-n", name])
        return rc, out, err
    path = _env_path(scenario_id)
    # Safety: only delete within itest/envs
    path = path.resolve()
    base = ENVS_DIR.resolve()
    if base not in path.parents:
        raise RuntimeError(f"Refusing to delete outside itest envs: {path}")
    if path.exists():
        shutil.rmtree(path)
    return 0, "", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--scenario", help="Scenario ID")
    ap.add_argument("--use-names", action="store_true", help="Use -n env_name instead of -p path")
    ap.add_argument("--keep-env", action="store_true")
    ap.add_argument("--summarize", action="store_true", help="Update itest/reports/summary.{json,md} after run")
    args = ap.parse_args()

    _ensure_dirs()

    if args.list:
        return list_scenarios()

    if not args.scenario:
        raise SystemExit("--scenario required")

    scen_path = SCENARIOS / f"{args.scenario}.json"
    if not scen_path.exists():
        raise SystemExit(f"Scenario not found: {scen_path}")
    scenario = json.loads(scen_path.read_text(encoding="utf-8"))

    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + args.scenario
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "scenario": scenario,
        "run_id": run_id,
        "use_names": bool(args.use_names),
        "steps": [],
    }

    def step(name, fn, *, ok_rc=None):
        t0 = time.time()
        rc, out, err = fn()
        report["steps"].append({"name": name, "rc": rc, "seconds": round(time.time()-t0, 3), "stdout": out[-4000:], "stderr": err[-4000:]})
        if ok_rc is None:
            if rc != 0:
                raise RuntimeError(f"Step failed: {name}\n{err}")
            return
        allowed = set(ok_rc) if isinstance(ok_rc, list) else {int(ok_rc)}
        if rc not in allowed:
            raise RuntimeError(f"Step failed: {name} (rc={rc} not in {sorted(allowed)})\n{err}")

    try:
        # 1) create
        step("create_env", lambda: create_env(scenario=scenario, use_names=args.use_names))

        # 2) pip install
        pip_pkgs = scenario.get("pip_packages") or []
        if pip_pkgs:
            step(
                "pip_install",
                lambda: run_pip(scenario_id=scenario["id"], use_names=args.use_names, args=["install"] + pip_pkgs),
            )

        # 3) poison
        poison_meta = {}
        pkind = (scenario.get("poison") or {}).get("kind")
        if pkind == "duplicate_dist_info":
            poison_meta = poison_duplicate_dist_info(scenario=scenario, use_names=args.use_names)
        elif pkind == "delete_file":
            poison_meta = poison_delete_file(scenario=scenario, use_names=args.use_names)
        elif pkind == "corrupt_conda_meta":
            poison_meta = poison_corrupt_conda_meta(scenario=scenario, use_names=args.use_names)
        elif pkind == "install_no_deps":
            poison_meta = poison_install_no_deps(scenario=scenario, use_names=args.use_names)
        report["poison"] = poison_meta

        # 4) scan before
        rc, rep_before, out, err = scan_env_repair(scenario_id=scenario["id"], use_names=args.use_names)
        report["scan_before"] = rep_before
        report["counts_before"] = {
            "duplicate-dist-info": count_issue(rep_before, "duplicate-dist-info"),
            "conda-meta-invalid-json": count_issue(rep_before, "conda-meta-invalid-json"),
            "conda-meta-missing-depends": count_issue(rep_before, "conda-meta-missing-depends"),
        }

        vcfg = scenario.get("verify", {}) or {}
        expected_issues = vcfg.get("expectedIssues") if isinstance(vcfg.get("expectedIssues"), list) else []
        expected_types = {i.get("type") for i in expected_issues if isinstance(i, dict) and i.get("type")}
        if "solver-inconsistent" in expected_types:
            probe_before = probe_solver_inconsistent(scenario_id=scenario["id"], use_names=args.use_names)
            report["solver_probe_before"] = probe_before
            report["counts_before"]["solver-inconsistent"] = 1 if probe_before.get("inconsistent") else 0

        # 5) fix
        # If the scenario pipeline declares ok_rc on the last step (e.g. verify-imports expected to report failures),
        # allow those return codes at the itest level.
        allowed_rc = None
        pipeline_cfg = ((scenario.get("env_repair") or {}).get("pipeline") or [])
        if isinstance(pipeline_cfg, list) and pipeline_cfg:
            last_step = pipeline_cfg[-1] if isinstance(pipeline_cfg[-1], dict) else None
            if last_step and last_step.get("ok_rc") is not None:
                allowed_rc = last_step.get("ok_rc")
        step("env_repair_fix", lambda: run_env_repair(scenario=scenario, use_names=args.use_names), ok_rc=allowed_rc)

        # Optional deterministic repair phase after fix-inconsistent (scenario-driven).
        repair_cfg = scenario.get("repair") or {}
        repair_kind = repair_cfg.get("kind")
        if repair_kind == "install_specs":
            step("repair_install_specs", lambda: repair_install_specs(scenario=scenario, use_names=args.use_names))

        # Optional: if a pipeline step is expected to return nonzero (e.g. verify-imports finds failures),
        # record that for assertions.
        pipeline = (scenario.get("env_repair") or {}).get("pipeline")
        if pipeline and isinstance(pipeline, list):
            # Only the last step's rc is currently returned by run_env_repair().
            report["env_repair_pipeline"] = pipeline

        # 6) scan after
        rc, rep_after, out, err = scan_env_repair(scenario_id=scenario["id"], use_names=args.use_names)
        report["scan_after"] = rep_after
        report["counts_after"] = {
            "duplicate-dist-info": count_issue(rep_after, "duplicate-dist-info"),
            "conda-meta-invalid-json": count_issue(rep_after, "conda-meta-invalid-json"),
            "conda-meta-missing-depends": count_issue(rep_after, "conda-meta-missing-depends"),
        }
        if "solver-inconsistent" in expected_types:
            probe_after = probe_solver_inconsistent(scenario_id=scenario["id"], use_names=args.use_names)
            report["solver_probe_after"] = probe_after
            report["counts_after"]["solver-inconsistent"] = 1 if probe_after.get("inconsistent") else 0

        # 7) verify
        ok = True
        issue = vcfg.get("expect_issue_cleared")
        if issue:
            ok = ok and (report["counts_after"].get(issue, 0) == 0)
        issue_present = vcfg.get("expect_issue_present")
        if issue_present:
            ok = ok and (report["counts_before"].get(issue_present, 0) > 0)

        # Optional: assert that the env-repair pipeline (last step) returned a specific rc.
        # Useful when verify-imports is expected to find failures (rc=1) but the itest should still be considered ok.
        expect_pipeline_rc = vcfg.get("expect_verify_imports_rc")
        if expect_pipeline_rc is not None:
            # env_repair_fix is the step that runs the whole pipeline.
            step_rc = None
            for s in report.get("steps") or []:
                if s.get("name") == "env_repair_fix":
                    step_rc = s.get("rc")
            report["verify_pipeline_rc"] = {"expected": int(expect_pipeline_rc), "actual": step_rc}
            ok = ok and (step_rc == int(expect_pipeline_rc))

        # Optional: verify a specific import works after repair.
        import_ok = vcfg.get("import_ok")
        if import_ok:
            # Prefer calling the env's python directly (more robust than `mamba run` quoting on Windows).
            if os.name == "nt" and not args.use_names:
                py_exe = _env_path(scenario["id"]) / "python.exe"
                cmd = [str(py_exe), "-c", f"import {import_ok}; print(0)"]
            else:
                cmd = [_mamba_exe(), "run"]
                if args.use_names:
                    cmd += ["-n", _env_name(scenario["id"])]
                else:
                    cmd += ["-p", str(_env_path(scenario["id"])).replace("/", "\\")]
                cmd += ["python", "-c", f"import {import_ok}; print(0)"]

            rc, out, err = _run(cmd)
            report["verify_import"] = {"module": import_ok, "rc": rc, "stdout": out[-500:], "stderr": err[-500:]}
            ok = ok and (rc == 0)

        # Extended issue checks.
        if expected_issues:
            issue_checks = []
            for item in expected_issues:
                if not isinstance(item, dict):
                    continue
                itype = item.get("type")
                if not itype:
                    continue
                b = report["counts_before"].get(itype, 0)
                a = report["counts_after"].get(itype, 0)
                item_ok = True
                before_cfg = item.get("before") if isinstance(item.get("before"), dict) else {}
                after_cfg = item.get("after") if isinstance(item.get("after"), dict) else {}
                if "min" in before_cfg:
                    item_ok = item_ok and (b >= int(before_cfg["min"]))
                if "max" in before_cfg:
                    item_ok = item_ok and (b <= int(before_cfg["max"]))
                if "eq" in before_cfg:
                    item_ok = item_ok and (b == int(before_cfg["eq"]))
                if "min" in after_cfg:
                    item_ok = item_ok and (a >= int(after_cfg["min"]))
                if "max" in after_cfg:
                    item_ok = item_ok and (a <= int(after_cfg["max"]))
                if "eq" in after_cfg:
                    item_ok = item_ok and (a == int(after_cfg["eq"]))
                issue_checks.append({"type": itype, "before": b, "after": a, "ok": item_ok, "rule": item})
                ok = ok and item_ok
            report["verify_expected_issues"] = issue_checks

        report["ok"] = bool(ok)

    except Exception as e:
        report["ok"] = False
        report["error"] = str(e)

    # Always write report, even on failure.
    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    md = []
    md.append(f"# itest report: {run_id}\n")
    md.append(f"- scenario: {scenario['id']}\n")
    md.append(f"- ok: {report.get('ok')}\n")
    if report.get("error"):
        md.append(f"- error: {report['error']}\n")
    md.append("\n## Counts\n")
    cb = report.get("counts_before") or {}
    ca = report.get("counts_after") or {}
    md.append(f"- duplicate-dist-info: {cb.get('duplicate-dist-info', 'n/a')} -> {ca.get('duplicate-dist-info', 'n/a')}\n")
    md.append(f"- conda-meta-invalid-json: {cb.get('conda-meta-invalid-json', 'n/a')} -> {ca.get('conda-meta-invalid-json', 'n/a')}\n")
    md.append("\n## Steps\n")
    for s in report.get('steps') or []:
        md.append(f"- {s['name']}: rc={s['rc']} ({s['seconds']}s)\n")
    (run_dir / "report.md").write_text("".join(md), encoding="utf-8")

    if not args.keep_env:
        try:
            cleanup_env(scenario_id=scenario["id"], use_names=args.use_names)
        except Exception as e:
            report.setdefault("cleanup_error", str(e))

    if getattr(args, "summarize", False):
        try:
            # Call as a separate script to avoid package/import issues.
            p = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "summarize.py")],
                text=True,
                capture_output=True,
                check=False,
            )
            if p.returncode != 0:
                report["summarize_error"] = {
                    "rc": p.returncode,
                    "stdout": (p.stdout or "")[-2000:],
                    "stderr": (p.stderr or "")[-2000:],
                }
        except Exception as e:
            report["summarize_error"] = {"exception": str(e)}

        # Persist summarize status as part of the run report.
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("DONE", run_id, "ok=", report.get("ok"))


if __name__ == "__main__":
    main()
