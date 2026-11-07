from .i18n import tr
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import stat
import time
import traceback
from uuid import uuid4
from pathlib import Path

from .naming import normalize_name
from .subprocess_utils import OperationInterrupted, run_cmd_capture, run_cmd_live, run_cmd_live_capture


CONDA_NAME_PATTERN = r"[A-Za-z0-9_][A-Za-z0-9._-]*"


class SandboxInventoryError(ValueError):
    pass


def classify_failure(message):
    lowered = str(message).lower()
    categories = (
        ("network", ("could not fetch url", "sslerror", "certificate_verify_failed", "condahttperror", "connectionerror", "connection refused", "proxyerror")),
        ("permission", ("permission denied", "access is denied", "winerror 5", "notwritableerror")),
        ("timeout", ("timed out", "timeoutexpired")),
        ("python-compatibility", ("requires-python", "requires a different python", "pin on python", "python version")),
        ("dependency-conflict", ("resolutionimpossible", "unsatisfiableerror", "could not solve", "conflicting dependencies", "incompatible")),
        ("candidate-unavailable", ("no matching distribution", "could not find a version", "packagesnotfounderror", "does not exist")),
        ("unsupported-wheel", ("not a supported wheel", "unsupportedwheel", "none of the wheel's tags")),
        ("binary-runtime", ("dll load failed", "undefined symbol", "numpy.dtype size changed", "wrong elf class", "winerror 193")),
        ("missing-import", ("modulenotfounderror", "no module named")),
        ("import-api-mismatch", ("cannot import name", "attributeerror")),
    )
    for category, patterns in categories:
        if any(pattern in lowered for pattern in patterns):
            return category
    return "unknown"


def _run_stage(command, *, stage, report, env=None):
    code, output, error = run_cmd_live_capture(command, env=env)
    report.setdefault("stages", []).append({"stage": stage, "command": command, "returncode": code,
                                            "stdout": output, "stderr": error})
    if code:
        report["failure_category"] = classify_failure(error + "\n" + output)
        raise ValueError(f"{stage} failed ({report['failure_category']}); original environment unchanged")


def _site_directory(python, *, expected_prefix=None):
    query = "import json, sys, sysconfig; print(json.dumps({'prefix': sys.prefix, 'purelib': sysconfig.get_path('purelib')}))"
    code, output, error = run_cmd_capture([str(python), "-I", "-c", query], timeout=30)
    if code:
        raise SandboxInventoryError("cannot query target Python installation paths: " + (error or output).strip())
    try:
        paths = json.loads(output)
        if not isinstance(paths, dict) or not all(isinstance(paths.get(key), str) and paths[key] for key in ("prefix", "purelib")):
            raise ValueError("missing prefix/purelib")
        prefix = Path(paths["prefix"])
        site = Path(paths["purelib"])
        if not prefix.is_absolute() or not site.is_absolute():
            raise ValueError("relative installation path")
        prefix = prefix.resolve()
        site = site.resolve()
        if expected_prefix is not None and prefix != Path(expected_prefix).resolve():
            raise ValueError("target Python belongs to a different environment")
        if not site.is_relative_to(prefix) or site == prefix:
            raise ValueError("purelib escapes target environment")
    except (OSError, TypeError, ValueError) as exc:
        raise SandboxInventoryError("invalid target Python installation paths: " + str(exc)) from exc
    return str(site)


def _inventory(prefix, python, *, targets=None):
    from .discovery import get_site_packages
    from .verify_imports import _read_metadata_name, get_toplevel_imports

    prefix = Path(prefix).resolve()
    records = []
    record_paths = {}
    for path in sorted((prefix / "conda-meta").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SandboxInventoryError(f"unreadable conda package record: {path}: {exc}") from exc
        if not isinstance(record, dict):
            raise SandboxInventoryError(f"invalid conda package record: {path}")
        records.append(record)
        record_paths[id(record)] = path
    requested = None if targets is None else {normalize_name(name) for name in targets}
    packages = []
    owners = {}
    for record in records:
        files = record.get("files") or []
        if not isinstance(files, list) or any(not isinstance(name, str) for name in files):
            raise SandboxInventoryError(f"invalid conda ownership paths: {record_paths[id(record)]}")
        for name in files:
            owners[str((prefix / name).resolve()).lower()] = record
    selected_records = {}
    for site in get_site_packages(python):
        for dist in Path(site).glob("*.dist-info"):
            name = _read_metadata_name(dist)
            if requested is not None and (not name or normalize_name(name) not in requested):
                continue
            if not name:
                raise ValueError(f"unreadable metadata: {dist}")
            if normalize_name(name) == "env-repair":
                continue
            owner = owners.get(str((dist / "METADATA").resolve()).lower())
            if owner:
                if not isinstance(owner.get("name"), str):
                    raise SandboxInventoryError(f"invalid conda package record: {record_paths[id(owner)]}")
                selected_records[owner["name"]] = owner
            packages.append((dist, name, owner is not None))
    if requested is not None:
        found = {normalize_name(name) for _, name, _ in packages}
        if requested - found:
            raise ValueError("requested target distributions not found: " + ", ".join(sorted(requested - found)))
        records = list(selected_records.values())
    for record in records:
        name = record.get("name")
        version = record.get("version")
        if not isinstance(name, str) or not isinstance(version, str) or not re.fullmatch(CONDA_NAME_PATTERN, name) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version):
            raise SandboxInventoryError(f"invalid conda package identity in {record_paths[id(record)]}: name={name!r}, version={version!r}")
        channel = str(record.get("channel", ""))
        url = str(record.get("url", ""))
        if url.startswith("file:") or channel.startswith("file:") or channel in {"local", "<unknown>"} or os.path.isabs(channel):
            raise ValueError(f"local/unknown conda artifact cannot be reconstructed from official sources: {record['name']}")
    selected = packages
    packages = []
    for dist, name, managed in selected:
            direct = dist / "direct_url.json"
            installer = dist / "INSTALLER"
            local = direct.exists() or not installer.exists() or installer.read_text().strip() != "pip"
            metadata = dist / "METADATA"
            version = next((line[9:].strip() for line in metadata.read_text(encoding="utf-8").splitlines()
                            if line.startswith("Version: ")), "")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version):
                raise ValueError(f"invalid package identity: {dist}")
            packages.append({"name": name, "version": version, "dist": str(dist),
                             "kind": "conda" if managed else "local" if local else "pypi",
                             "metadata_hash": hashlib.sha256(metadata.read_bytes()).hexdigest(),
                             "imports": get_toplevel_imports(dist)})
            if not managed and local:
                record_path = dist / "RECORD"
                hashes = {}
                if record_path.is_file():
                    with record_path.open(encoding="utf-8", newline="") as stream:
                        for row in csv.reader(stream):
                            if row:
                                path = dist.parent / row[0].replace("\\", "/")
                                if path.resolve().is_relative_to(dist.parent.resolve()) and path.is_file():
                                    hashes[row[0]] = hashlib.sha256(path.read_bytes()).hexdigest()
                packages[-1]["local_hashes"] = hashes
    pinned = prefix / "conda-meta" / "pinned"
    return {"records": sorted(records, key=lambda item: item["name"]),
            "packages": sorted(packages, key=lambda item: (item["name"], item["dist"])),
            "pins": pinned.read_text(encoding="utf-8") if requested is None and pinned.exists() else None}


def _fingerprint(inventory):
    return hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()


def _copy_local(package, site):
    dist = Path(package["dist"])
    source = dist.parent.resolve()
    site = Path(site).resolve()
    record = dist / "RECORD"
    if not record.is_file():
        raise ValueError(f"local package has no RECORD: {package['name']}")
    copies = []
    with record.open(encoding="utf-8", newline="") as stream:
        for row in csv.reader(stream):
            if not row:
                continue
            relative = Path(row[0].replace("\\", "/"))
            original = source / relative
            target = site / relative
            if relative.is_absolute() or ".." in relative.parts or not original.resolve().is_relative_to(source):
                raise ValueError(f"local RECORD escapes site-packages: {row[0]}")
            if not target.resolve().is_relative_to(site):
                raise ValueError(f"local destination escapes site-packages: {row[0]}")
            if original.is_symlink() or not original.is_file():
                raise ValueError(f"unsafe/missing/overlapping local file: {row[0]}")
            if target.exists():
                if target.is_symlink() or not target.is_file() or original.read_bytes() != target.read_bytes():
                    raise ValueError(f"overlapping local file: {row[0]}")
                continue
            if relative.suffix.lower() in {".pth", ".egg-link"}:
                raise ValueError("local startup hooks/editable installs cannot be copied safely")
            copies.append((original, target))
    if not record.read_text(encoding="utf-8").strip():
        raise ValueError("empty local RECORD")
    for original, target in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)


def _checks(python, packages, extra_imports=()):
    failures = []
    code, output, error = run_cmd_capture([str(python), "-I", "-m", "pip", "check"], timeout=120)
    if code:
        failures.append({"import": "pip check", "error": error or output})
    imports = set(extra_imports) | {name for package in packages for name in package["imports"]}
    for name in sorted(imports):
        if not all(part.isidentifier() for part in name.split(".")):
            failures.append({"import": name, "error": "invalid import target"})
            continue
        print(tr('Sandbox import check: {value0}', value0=name), flush=True)
        try:
            code, output, error = run_cmd_capture([str(python), "-I", "-c", f"import importlib; importlib.import_module({name!r})"], timeout=90)
            if code:
                failures.append({"import": name, "error": error or output})
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append({"import": name, "error": str(exc)})
    return failures


def _report_directory(run_id):
    return Path.cwd() / ".env_repair" / "reports" / "sandboxes" / run_id


def _cleanup_workspace(workspace, *, source, token, manager):
    workspace = Path(workspace)
    source = Path(source).resolve()
    result = {"ok": False, "path": str(workspace), "attempts": 0}
    try:
        resolved = workspace.resolve()
        marker = workspace / ".env-repair-owned"
        if (workspace.is_symlink() or resolved != workspace or resolved == resolved.parent
                or resolved == Path(tempfile.gettempdir()).resolve()
                or source.is_relative_to(resolved) or resolved.is_relative_to(source)
                or marker.is_symlink() or marker.read_text(encoding="utf-8") != token):
            raise ValueError("unsafe sandbox cleanup path or ownership marker; refusing removal")
        prefix = workspace / "environment"
        if prefix.exists() and (prefix.is_symlink() or prefix.resolve().parent != resolved):
            raise ValueError("sandbox environment points outside its workspace; refusing removal")
        if (prefix / "conda-meta").is_dir():
            try:
                code, output, error = run_cmd_capture(
                    [manager, "env", "remove", "-y", "-p", str(prefix)], timeout=120)
                result["manager_remove"] = {"returncode": code, "stdout": output, "stderr": error}
            except (OSError, subprocess.TimeoutExpired, OperationInterrupted, KeyboardInterrupt) as exc:
                result["manager_remove"] = {"error": str(exc)}

        def remove_readonly(function, filename, error):
            path = Path(filename)
            if not path.resolve().is_relative_to(resolved) or path.is_symlink():
                raise error[1] if isinstance(error, tuple) else error
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            function(filename)

        for attempt in range(3):
            result["attempts"] = attempt + 1
            try:
                if sys.version_info >= (3, 12):
                    shutil.rmtree(workspace, onexc=remove_readonly)
                else:
                    shutil.rmtree(workspace, onerror=remove_readonly)
                result["ok"] = not workspace.exists()
                break
            except OSError as exc:
                result["error"] = str(exc)
                if attempt < 2:
                    time.sleep(0.2 * (attempt + 1))
        if result["ok"]:
            result.pop("error", None)
    except (OSError, ValueError) as exc:
        result["error"] = str(exc)
    return result


def _finish_sandbox(report, workspace, *, token):
    report_dir = _report_directory(token).resolve()
    try:
        if report_dir.is_relative_to(workspace):
            raise ValueError("sandbox report must be stored outside the temporary workspace")
        report_dir.mkdir(parents=True, exist_ok=True)
        report["report_path"] = str(report_dir / "migration.json")
        for name in ("requirements.txt", "explicit.txt"):
            artifact = workspace / name
            if artifact.is_file() and not artifact.is_symlink():
                shutil.copy2(artifact, report_dir / name)
    except (OSError, ValueError) as exc:
        report["report_error"] = str(exc)
    finally:
        print(tr("Cleaning up temporary sandbox: {value0}", value0=workspace), flush=True)
        report["validation_ok"] = bool(report["ok"])
        cleanup = _cleanup_workspace(workspace, source=report["source"], token=token, manager=report["manager"])
        report["cleanup"] = cleanup
        if not cleanup["ok"]:
            report["ok"] = False
            report["failure_category"] = "cleanup"
            print(tr("Sandbox cleanup failed: {value0}", value0=tr(cleanup.get("error") or workspace)))
    if report.get("report_path"):
        try:
            Path(report["report_path"]).write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(tr("Sandbox report: {value0}", value0=report["report_path"]))
        except OSError as exc:
            report["report_error"] = str(exc)
    if report.get("report_error"):
        print(tr("Sandbox report could not be saved: {value0}", value0=report["report_error"]))


def test_packages(*, env_path, python_exe, manager, channels, python_spec, targets=(), conda_specs=(), pypi_specs=(), extra_imports=()):
    for spec in list(conda_specs) + list(pypi_specs):
        if not re.fullmatch(CONDA_NAME_PATTERN + r"(?:[=<>!~]+[A-Za-z0-9.*+!_-]+(?:,[=<>!~]+[A-Za-z0-9.*+!_-]+)*)?", spec):
            raise ValueError("unsupported package specification; use names/version constraints, not URLs or options")
        if normalize_name(re.split(r"[=<>!~]", spec)[0]) == "env-repair":
            raise ValueError("env-repair is excluded from sandbox migration")
    if not re.fullmatch(r"python=3\.[0-9]+", python_spec):
        raise ValueError("invalid Python target")
    if not (targets or conda_specs or pypi_specs):
        raise ValueError("at least one target package is required")
    workspace = Path(tempfile.mkdtemp(prefix="env-repair-packages-")).resolve()
    token = uuid4().hex
    prefix = workspace / "environment"
    report = {"ok": False, "workspace": str(workspace), "source": str(Path(env_path).resolve()),
              "python_spec": python_spec, "conda_specs": list(conda_specs), "pypi_specs": list(pypi_specs),
              "extra_imports": list(extra_imports), "manager": manager, "channels": channels,
              "scope": "target-packages", "targets": list(targets), "apply_blocked": True,
              "limitation": "Only the target combination is tested; original-environment downgrade compatibility is not established."}
    print(tr('Target-package sandbox: {value0}', value0=workspace), flush=True)
    try:
        (workspace / ".env-repair-owned").write_text(token, encoding="utf-8")
        inventory = _inventory(env_path, python_exe, targets=targets)
        report.update(fingerprint=_fingerprint(inventory), inventory=inventory)
        conda_overrides = {normalize_name(re.split(r"[=<>!~]", spec)[0]) for spec in conda_specs}
        pip_overrides = {normalize_name(re.split(r"[=<>!~]", spec)[0]) for spec in pypi_specs}
        specs = [f"{record['name']}={record['version']}" for record in inventory["records"]
                 if normalize_name(record["name"]) not in {"python", "python-abi", "env-repair"} | conda_overrides]
        specs.extend([python_spec, "pip"] + list(conda_specs))
        spec_file = workspace / "requirements.txt"
        spec_file.write_text("\n".join(sorted(set(specs))) + "\n", encoding="utf-8")
        command = [manager, "create", "-y", "-p", str(prefix)]
        for channel in channels:
            command.extend(["-c", channel])
        command.extend(["--file", str(spec_file)])
        _run_stage(command, stage="conda-create", report=report)
        python = prefix / ("python.exe" if os.name == "nt" else "bin/python")
        wheels = workspace / "wheels"
        wheels.mkdir()
        requirements = [f"{item['name']}=={item['version']}" for item in inventory["packages"]
                        if item["kind"] == "pypi" and normalize_name(item["name"]) not in pip_overrides] + list(pypi_specs)
        if requirements:
            pip_env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PIP_")}
            pip_env["PIP_CONFIG_FILE"] = os.devnull
            download = [str(python), "-I", "-m", "pip", "--isolated", "download", "--index-url", "https://pypi.org/simple",
                        "--only-binary=:all:", "-d", str(wheels)] + requirements
            _run_stage(download, stage="pypi-wheel-download", report=report, env=pip_env)
            install = [str(python), "-I", "-m", "pip", "--isolated", "install", "--no-index",
                       "--force-reinstall", "--find-links", str(wheels)] + requirements
            _run_stage(install, stage="pypi-install", report=report, env=pip_env)
        local_packages = [item for item in inventory["packages"] if item["kind"] == "local"]
        if local_packages:
            site = _site_directory(python, expected_prefix=prefix)
            report["local_copy_site"] = site
            for item in local_packages:
                _copy_local(item, site)
        sandbox_inventory = _inventory(prefix, str(python))
        print(tr("Sandbox installation finished; checking installed packages and imports..."), flush=True)
        failures = _checks(python, sandbox_inventory["packages"], extra_imports)
        for failure in failures:
            failure["category"] = classify_failure(failure.get("error"))
        report.update(failures=failures, tested_packages=sandbox_inventory["packages"])
        if failures:
            raise ValueError("sandbox validation failed; inspect migration.json, including copied local packages")
        report["tested_conda_packages"] = [
            {key: record[key] for key in ("name", "version", "build", "channel", "url", "sha256") if key in record}
            for record in sandbox_inventory["records"]
        ]
        try:
            code, explicit, error = run_cmd_capture([manager, "list", "-p", str(prefix), "--explicit"], timeout=120)
            urls = [line.strip() for line in explicit.splitlines() if line.strip().startswith(("https://", "http://"))]
            if not code and "@EXPLICIT" not in explicit and urls and len(urls) == len(sandbox_inventory["records"]):
                explicit = "@EXPLICIT\n" + "\n".join(urls) + "\n"
            if code or "@EXPLICIT" not in explicit:
                report["export_warning"] = error or "manager returned no explicit package manifest; tested package inventory retained"
            else:
                (workspace / "explicit.txt").write_text(explicit, encoding="utf-8")
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            report["export_warning"] = str(exc)
        if report.get("export_warning"):
            print(tr("Optional package export unavailable; validation succeeded and the tested inventory is retained in the report."))
        report.update(ok=True, requirements=requirements)
        print(tr("Sandbox validation succeeded for {value0}.", value0=python_spec))
        print(tr(report["limitation"]))
    except SandboxInventoryError as exc:
        report.update(error=str(exc), failure_category="inventory")
        print(tr('Migration not approved: {value0}', value0=tr(exc)))
    except (KeyboardInterrupt, OperationInterrupted):
        report.update(ok=False, interrupted=True, failure_category="interrupted")
        raise
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        report["error"] = str(exc)
        report.setdefault("failure_category", classify_failure(exc))
        print(tr('Migration not approved: {value0}', value0=tr(exc)))
    except Exception as exc:
        report.update(ok=False, error=f"{type(exc).__name__}: {exc}", failure_category="internal-error",
                      traceback=traceback.format_exc())
        print(tr('Migration not approved: {value0}', value0=report["error"]))
    finally:
        _finish_sandbox(report, workspace, token=token)
    return report


def apply_migration(report_path):
    report_path = Path(report_path).resolve()
    workspace = report_path.parent
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("ok") is not True:
        raise ValueError("sandbox has not passed validation")
    if report.get("scope") == "target-packages":
        raise ValueError("target-only sandbox cannot authorize a downgrade of the original environment")
    if report.get("apply_blocked") or any(normalize_name(item["name"]) in {"conda", "mamba", "micromamba"}
                                          for item in report["inventory"]["records"]):
        raise ValueError("base/manager environment: automatic Python migration is not supported; use a separate environment")
    source = Path(report["source"]).resolve()
    if Path(sys.prefix).resolve().is_relative_to(source):
        raise ValueError("run this command from an independent Python environment, not the migration target")
    python = source / ("python.exe" if os.name == "nt" else "bin/python")
    if _fingerprint(_inventory(source, str(python))) != report["fingerprint"]:
        raise ValueError("source package inventory changed since the sandbox test; repeat the test")
    manager = report["manager"]
    command = [manager, "install", "-y", "-p", str(source), "--file", str(workspace / "explicit.txt")]
    code, output, error = run_cmd_capture(command + ["--dry-run", "--json"], timeout=120)
    if code:
        raise ValueError(error or output)
    preview = json.loads(output)
    if preview.get("success") is not True or not isinstance(preview.get("actions"), dict):
        raise ValueError("missing target transaction preview")
    actions = preview["actions"]
    linked = {normalize_name(item["name"]) for item in actions.get("LINK", [])}
    unlinked = {normalize_name(item["name"]) for item in actions.get("UNLINK", [])}
    if "env-repair" in linked | unlinked or unlinked - linked:
        raise ValueError("target transaction would remove packages or alter env-repair; repeat planning")
    for kind in ("UNLINK", "LINK"):
        for item in actions.get(kind, []):
            print(f"{kind}: {item['name']} {item['version']}")
    if input(tr('Apply this tested Python/Qt migration to the ORIGINAL environment? [y/N] ')).strip().lower() not in {"y", "yes", "j", "ja"}:
        return {"ok": False, "reason": "declined"}
    for arguments, filename in (([manager, "list", "-p", str(source), "--explicit"], "before-conda.txt"),
                                ([str(python), "-m", "pip", "freeze", "--all"], "before-pip.txt")):
        code, output, error = run_cmd_capture(arguments, timeout=120)
        if code:
            raise ValueError(error or "cannot save pre-migration inventory")
        (workspace / filename).write_text(output, encoding="utf-8")
    result = {"ok": False, "workspace": str(workspace)}
    try:
        if run_cmd_live(command):
            raise ValueError("target conda migration failed; inspect snapshot; no automatic rollback")
        if report["requirements"]:
            install = [str(python), "-I", "-m", "pip", "--isolated", "install", "--no-index", "--no-deps",
                       "--force-reinstall", "--find-links", str(workspace / "wheels")] + report["requirements"]
            if run_cmd_live(install):
                raise ValueError("target pip relink failed; environment requires attention")
        sandbox_python = workspace / "environment" / ("python.exe" if os.name == "nt" else "bin/python")
        local_packages = [item for item in report["inventory"]["packages"] if item["kind"] == "local"]
        if local_packages:
            sandbox_site = _site_directory(sandbox_python, expected_prefix=workspace / "environment")
            target_site = _site_directory(python, expected_prefix=source)
            for item in local_packages:
                copied = dict(item, dist=str(Path(sandbox_site) / Path(item["dist"]).name))
                _copy_local(copied, target_site)
        failures = _checks(python, report["inventory"]["packages"], report.get("extra_imports", ()))
        result.update(ok=not failures, failures=failures)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result["error"] = str(exc)
    (workspace / "applied.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def configure_parser(parser):
    parser.add_argument("--env", dest="env_single", required=True)
    parser.add_argument("--target", action="append", default=[], help=tr('Installed distribution to test at its current version'))
    parser.add_argument("--conda-package", action="append", default=[], help=tr('Conda package/version constraint to test'))
    parser.add_argument("--pypi-package", action="append", default=[], help=tr('PyPI package/version constraint to test (binary wheels only)'))
    parser.add_argument("--python-version", help=tr('Sandbox Python major.minor; default: target environment version'))
    parser.add_argument("--check-import", action="append", default=[])
    parser.add_argument("--channel", action="append", default=[])
    parser.add_argument("--manager", choices=["mamba", "conda", "micromamba"])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--debug", action="store_true")


def run_sandbox(args):
    import contextlib

    from .discovery import discover_envs, get_python_exe, select_envs

    try:
        envs, base, discovered_manager = discover_envs(show_json_output=False)
        targets = select_envs(envs, [args.env_single], base)
        if len(targets) != 1:
            raise ValueError("select exactly one source environment")
        python = get_python_exe(targets[0])
        if not python:
            raise ValueError("missing source Python")
        version = args.python_version
        if not version:
            code, version, error = run_cmd_capture([python, "-I", "-c", "import sys; print('%s.%s' % sys.version_info[:2])"], timeout=30)
            if code:
                raise ValueError(error or "cannot determine source Python version")
            version = version.strip()
        manager = args.manager or discovered_manager
        if manager not in {"mamba", "conda", "micromamba"}:
            raise ValueError("a conda-compatible manager is required to create the sandbox")
        with contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext():
            report = test_packages(env_path=targets[0], python_exe=python, manager=manager,
                                   channels=args.channel, python_spec="python=" + version, targets=args.target,
                                   conda_specs=args.conda_package, pypi_specs=args.pypi_package, extra_imports=args.check_import)
        return {"ok": report["ok"], "exit_code": 0 if report["ok"] else 1, "report": report}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        if not args.json:
            print(tr("Sandbox could not be started: {value0}", value0=tr(exc)))
        return {"ok": False, "exit_code": 1, "report": {"ok": False, "error": str(exc), "failure_category": classify_failure(exc)}}


def recover_import_failures(failures, *, env_path, python_exe, manager, channels, python_version, actions=(), quiet=False):
    import contextlib

    from .verify_imports import _read_metadata_name

    result = {"action": "automatic_package_sandbox", "ok": False, "original_repaired": False,
              "scope": "target-packages", "attempts": [], "skipped": []}
    if manager not in {"mamba", "conda", "micromamba"}:
        result["reason"] = "no supported manager for automatic sandbox creation"
        return result
    if not isinstance(python_version, str) or not re.fullmatch(r"3\.[0-9]+", python_version):
        result["reason"] = "cannot determine source Python version safely"
        return result
    qt_selection = next((action.get("selected") for action in actions
                         if action.get("action") == "qt_binding_selection" and action.get("selected")), None)
    previous = [action.get("sandbox") for action in actions if isinstance(action.get("sandbox"), dict)]
    previous.extend(attempt.get("sandbox") for action in actions for attempt in action.get("attempts", [])
                    if isinstance(attempt, dict) and isinstance(attempt.get("sandbox"), dict))
    tested_targets = {normalize_name(name) for report in previous for name in report.get("targets", [])}
    targets = set()
    extra_specs = []
    extra_imports = []
    for failure in failures:
        dist = failure.get("dist_path")
        name = _read_metadata_name(dist) if isinstance(dist, Path) else None
        if not name or normalize_name(name) == "env-repair":
            result["skipped"].append({"import": failure.get("import"), "reason": "unidentified/protected distribution"})
            continue
        if normalize_name(name) in tested_targets:
            result["skipped"].append({"package": name, "reason": "already tested by recovery in this run"})
            continue
        if "qtpy.QtBindingsNotFoundError" in str(failure.get("error")):
            if not qt_selection:
                result["skipped"].append({"package": name, "reason": "Qt backend requires explicit user selection"})
                continue
            from .qt_repair import QT_BINDINGS

            _, provider, module = QT_BINDINGS[qt_selection]
            extra_specs = [provider]
            extra_imports = [module + ".QtCore", module + ".QtWidgets"]
        targets.add(name)
    if not targets:
        result["reason"] = "no untested eligible targets"
        return result
    minor = int(python_version.split(".")[1])
    versions = [python_version] + [f"3.{candidate}" for candidate in range(minor - 1, max(8, minor - 3), -1)]
    with contextlib.redirect_stdout(sys.stderr) if quiet else contextlib.nullcontext():
        print(tr('Automatic fallback: testing unresolved target packages in a minimal sandbox; original unchanged.'))
        for version in versions:
            try:
                report = test_packages(env_path=env_path, python_exe=python_exe, manager=manager,
                                       channels=channels, python_spec="python=" + version, targets=sorted(targets),
                                       conda_specs=extra_specs, extra_imports=extra_imports)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                report = {"ok": False, "error": str(exc), "failure_category": classify_failure(exc)}
            result["attempts"].append({"python": version, "sandbox": report})
            if report["ok"]:
                result.update(ok=True, sandbox=report)
                print(tr('Target combination works in the sandbox; original failures remain unresolved. No automatic downgrade/removal.'))
                break
            if report.get("failure_category") in {"network", "permission", "timeout"}:
                break
            if report.get("failure_category") not in {"python-compatibility", "dependency-conflict", "candidate-unavailable", "unsupported-wheel", "binary-runtime", "missing-import", "import-api-mismatch"} and not report.get("failures"):
                break
    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description=tr('Test target packages and their dependency closure without modifying the source environment'))
    configure_parser(parser)
    args = parser.parse_args()
    result = run_sandbox(args)
    print(json.dumps(result["report"], indent=2))
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
