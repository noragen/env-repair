import concurrent.futures
import os
import json
import subprocess
from pathlib import Path

from .conda_ops import conda_install, conda_install_capture, conda_remove, get_env_package_entries, is_conda_env
from .conda_config import load_conda_channels
from .discovery import discover_envs, get_python_exe, select_envs, which
from .naming import normalize_name
from .pip_ops import pip_get_version, pip_reinstall, pip_uninstall
from .progress import Progress
from .subprocess_utils import run_json_cmd

CRITICAL_PACKAGES = {
    "pip",
    "setuptools",
    "wheel",
    "numpy",
    "scipy",
    "pandas",
    "matplotlib",
    "requests",
    "certifi",
    "ssl",
}

CRITICAL_PIP_PACKAGES = {"pip", "setuptools", "wheel"}


def _verify_imports_blacklist_path():
    return Path(".env_repair") / "verify_imports_blacklist.json"


def _load_verify_imports_blacklist():
    path = _verify_imports_blacklist_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    blocked = data.get("blocked")
    if not isinstance(blocked, dict):
        return {}
    out = {}
    for pyver, names in blocked.items():
        if not isinstance(pyver, str) or not isinstance(names, dict):
            continue
        out.setdefault(pyver, {})
        for name, meta in names.items():
            if not isinstance(name, str):
                continue
            out[pyver][normalize_name(name)] = meta if isinstance(meta, dict) else {}
    return out


def _save_verify_imports_blacklist(blocked):
    path = _verify_imports_blacklist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"blocked": blocked}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _blacklist_add(blocked, *, pyver, conda_name, reason):
    if not pyver or not conda_name:
        return False
    blocked.setdefault(pyver, {})
    blocked[pyver].setdefault(
        normalize_name(conda_name),
        {
            "conda": conda_name,
            "reason": reason,
        },
    )
    return True


def _read_metadata_name(dist_info):
    meta = dist_info / "METADATA"
    if not meta.exists():
        return None
    try:
        for line in meta.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line:
                break
            if line.lower().startswith("name:"):
                val = line.split(":", 1)[1].strip()
                return val or None
    except OSError:
        return None
    return None


def _installed_by(dist_info):
    installer_path = dist_info / "INSTALLER"
    if installer_path.exists():
        content = installer_path.read_text(encoding="utf-8", errors="ignore").strip().lower()
        if "conda" in content:
            return "conda"
        if "pip" in content:
            return "pip"
    if (dist_info / "direct_url.json").exists():
        return "pip"
    return "unknown"


def _classify_dist(dist_info, *, conda_entries_by_name):
    dist_name = _read_metadata_name(dist_info) or dist_info.name.split("-")[0]
    key = normalize_name(dist_name)
    entry = conda_entries_by_name.get(key)
    if entry:
        channel = (entry.get("channel") or "").lower()
        if "pypi" in channel:
            return "pip", entry.get("name") or dist_name
        return "conda", entry.get("name") or dist_name
    return _installed_by(dist_info), dist_name


def _extract_replacement_from_search_json(search_json, *, target):
    """
    Extract the "replacement" package name from a conda/mamba search --json payload.

    We interpret a "replacement" as: a dependency list containing the target package.
    """
    if not isinstance(search_json, dict):
        return None
    result = search_json.get("result") or {}
    pkgs = result.get("pkgs") or []
    if not isinstance(pkgs, list):
        return None
    for pkg in pkgs:
        if not isinstance(pkg, dict):
            continue
        deps = pkg.get("depends") or []
        if not isinstance(deps, list):
            continue
        for dep in deps:
            if not isinstance(dep, str):
                continue
            name = dep.split(" ", 1)[0].strip()
            if name == target:
                return target
    return None


def _maybe_replace_deprecated_wrapper(conda_name, *, manager, debug):
    """
    Handle known deprecated wrapper packages by installing their new backend instead.

    Currently supported:
      - conda-token -> anaconda-auth (conda-token >=0.7.0 depends on it)
    """
    key = normalize_name(conda_name)
    if key != "conda-token":
        return None

    if manager not in ("conda", "mamba"):
        return None

    # Search in defaults first because conda-token/anaconda-auth are hosted on pkgs/main.
    data = run_json_cmd([manager, "search", "-c", "defaults", "conda-token", "--json"], show_json_output=debug)
    if not data:
        return None
    if _extract_replacement_from_search_json(data, target="anaconda-auth"):
        return {"install": "anaconda-auth", "remove": "conda-token"}
    return None


def _collect_wrapper_removals(plan, *, conda_installed):
    """
    Determine which planned uninstall items we can execute after successful installs.

    `conda_installed` is a set of conda package names that were successfully installed/reinstalled in this run.
    """
    conda_remove_pkgs = []
    pip_remove_pkgs = []
    installed_norm = {normalize_name(p) for p in (conda_installed or set())}

    for item in plan or []:
        uninstall = item.get("uninstall") or {}
        remove_name = uninstall.get("name")
        remove_kind = uninstall.get("kind")
        required = item.get("name")  # the replacement we attempted to install
        if not remove_name or not remove_kind or not required:
            continue
        if normalize_name(required) not in installed_norm:
            continue
        if remove_kind == "conda":
            conda_remove_pkgs.append(remove_name)
        elif remove_kind == "pip":
            pip_remove_pkgs.append(remove_name)

    return sorted(set(conda_remove_pkgs)), sorted(set(pip_remove_pkgs))


def _collect_explicit_removals(plan):
    """
    Collect removal-only actions requested by the fix planner.

    These do not depend on installing a replacement in the same run; they are executed directly.
    """
    conda_rm = []
    pip_rm = []
    for item in plan or []:
        if item.get("kind") != "remove":
            continue
        uninstall = item.get("uninstall") or {}
        name = uninstall.get("name")
        kind = uninstall.get("kind")
        if not name or not kind:
            continue
        if kind == "conda":
            conda_rm.append(name)
        elif kind == "pip":
            pip_rm.append(name)
    return sorted(set(conda_rm)), sorted(set(pip_rm))


def _is_removed_collections_mapping_error(error):
    if not error:
        return False
    e = str(error)
    return ("cannot import name 'Mapping' from 'collections'" in e) or ('cannot import name "Mapping" from "collections"' in e)


def _is_boto_vendored_six_moves_error(error):
    if not error:
        return False
    return "No module named 'boto.vendored.six.moves'" in str(error)


def _extract_missing_module_name(error):
    if not error:
        return None
    e = str(error)
    # Common forms:
    #   ModuleNotFoundError: No module named 'numpy'
    #   ModuleNotFoundError: No module named numpy
    marker = "No module named"
    if marker not in e:
        return None
    try:
        tail = e.split(marker, 1)[1].strip()
    except Exception:
        return None
    if not tail:
        return None
    # Strip leading ':' or quotes
    if tail.startswith(":"):
        tail = tail[1:].strip()
    if tail.startswith("'") or tail.startswith('"'):
        q = tail[0]
        end = tail.find(q, 1)
        if end > 1:
            tail = tail[1:end]
    # Only take the first token/module path component (numpy.linalg -> numpy)
    mod = tail.split()[0].strip().strip("'\"")
    if not mod:
        return None
    return mod.split(".", 1)[0]


def _python_major_minor(python_exe):
    try:
        proc = subprocess.run(
            [python_exe, "-c", "import sys;print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    val = (proc.stdout or "").strip()
    return val or None


def _extract_solver_offenders(output_text):
    """
    Best-effort parse of libmamba solver output to extract offending specs.
    """
    import re

    if not output_text:
        return set()
    offenders = set()
    # Lines like: "└─ tables =* * does not exist"
    for m in re.finditer(r"(?:^|\n)\s*[├└]─\s*([A-Za-z0-9_.-]+)\s", output_text):
        offenders.add(m.group(1))
    return offenders


def _should_skip_failure(f):
    err = (f.get("error") or "").lower()
    name = (f.get("import") or "").lower()
    if os.name == "nt":
        if name == "sh" and ("not supported on windows" in err or "windows" in err):
            return True
        if "only supported on linux" in err or "only supported on macos" in err:
            return True
        if "no module named 'fcntl'" in err or "no module named fcntl" in err:
            return True
        if name == "ptyprocess" and ("no module named 'pty'" in err or "no module named pty" in err):
            return True
    return False


def _is_idna_ssl_match_hostname_error(err):
    if not err:
        return False
    e = err.lower()
    return "idna_ssl" in e and "ssl" in e and "has no attribute" in e and "match_hostname" in e


def _is_jedi_common_shadowing_error(err):
    if not err:
        return False
    e = err.lower()
    return (
        "cannot import name" in e
        and "from 'jedi.common'" in e
        and ("jedi\\common\\__init__.py" in e or "jedi/common/__init__.py" in e)
    )


def _conda_pkg_has_site_packages_files(prefix, conda_name):
    """
    Return True if conda-meta for `conda_name` lists any Lib/site-packages files.
    Useful to distinguish non-Python conda packages that can't fix a Python import.
    """
    try:
        meta_dir = Path(prefix) / "conda-meta"
        pats = sorted(meta_dir.glob(f"{conda_name}-*.json"))
        if not pats:
            return False
        meta_path = pats[-1]
        data = json.loads(meta_path.read_text(encoding="utf-8", errors="ignore"))
        files = data.get("files") or []
        for f in files:
            if isinstance(f, str) and "Lib/site-packages" in f.replace("\\", "/"):
                return True
    except Exception:
        return False
    return False


def _cleanup_jedi_common_pkg_dir(python_exe):
    """
    If both `jedi/common.py` and `jedi/common/` exist, the package directory can shadow the module.
    In broken environments this can make `from jedi.common import indent_block/monkeypatch` fail.
    Remove the `jedi/common/` directory when `jedi/common.py` is present.
    """
    try:
        import shutil
        from .discovery import get_site_packages

        for sp in get_site_packages(python_exe) or []:
            sp = Path(sp)
            module_path = sp / "jedi" / "common.py"
            pkg_dir = sp / "jedi" / "common"
            if module_path.exists() and pkg_dir.exists() and pkg_dir.is_dir():
                shutil.rmtree(pkg_dir, ignore_errors=True)
                return True
        return True
    except Exception:
        return False


def attempt_fix(failures, python_exe, env_path, manager, *, base_prefix, debug):
    if not failures:
        return {"ok": True, "actions": []}

    is_conda = is_conda_env(env_path)
    entries = get_env_package_entries(env_path, manager, show_json_output=debug) if (is_conda and manager) else []
    conda_entries_by_name = {normalize_name(e.get("name") or ""): e for e in entries if isinstance(e, dict)}
    initially_installed = set(conda_entries_by_name.keys())
    pyver = _python_major_minor(python_exe) or "unknown"
    blacklist = _load_verify_imports_blacklist()
    blocked_for_py = blacklist.get(pyver) or {}
    blocked_names = set(blocked_for_py.keys())
    configured_channels = load_conda_channels(base_prefix=base_prefix, has_conda=which("conda") is not None, show_json_output=debug)
    nodefaults = "nodefaults" in configured_channels
    has_defaults = ("defaults" in configured_channels) or ("anaconda" in configured_channels)

    # Stage 0: If failures are caused by a missing dependency module, fix that first.
    missing = {}
    for f in failures:
        mod = _extract_missing_module_name(f.get("error"))
        if mod:
            missing[normalize_name(mod)] = missing.get(normalize_name(mod), 0) + 1
    if missing:
        conda_targets = []
        pip_targets = []
        for mod_norm in sorted(missing.keys()):
            entry = conda_entries_by_name.get(mod_norm) or {}
            if entry:
                conda_name = entry.get("name") or mod_norm
                # Some conda packages (e.g. flatbuffers) are non-Python libs and won't fix a missing import.
                if manager and not _conda_pkg_has_site_packages_files(env_path, conda_name):
                    pip_targets.append(mod_norm)
                else:
                    conda_targets.append(conda_name)
                continue
            # If listed as a pip entry via conda list (channel pypi), treat as pip.
            if mod_norm in initially_installed and ((entry.get("channel") or "").lower() == "pypi"):
                pip_targets.append(entry.get("name") or mod_norm)
        if conda_targets or pip_targets:
            print("\nPriority: missing modules detected; repairing dependencies first...")
            if conda_targets and manager:
                conda_targets = sorted(set(conda_targets))
                print("Conda dependency targets:", ", ".join(conda_targets))
                ok_dep = conda_install(
                    env_path,
                    conda_targets,
                    manager,
                    configured_channels,
                    ignore_pinned=False,
                    force_reinstall=True,
                )
                if not ok_dep:
                    actions = [{"action": "conda_dependency_reinstall", "ok": False, "packages": conda_targets}]
                    return {"ok": False, "plan": [], "actions": actions}
            if pip_targets:
                pip_targets = sorted(set(pip_targets))
                print("Pip dependency targets:", ", ".join(pip_targets))
                for pkg in pip_targets:
                    pip_reinstall(
                        python_exe,
                        pkg,
                        no_deps=bool(is_conda),
                        only_binary=os.name == "nt",
                        ignore_installed=bool(is_conda),
                    )

            # Recheck failures after dependency repair and keep only remaining ones.
            still = []
            for f in failures:
                imp = f.get("import")
                if not imp or not isinstance(imp, str):
                    still.append(f)
                    continue
                ok, err = check_import(imp, python_exe)
                if not ok:
                    f["error"] = err
                    still.append(f)
            failures = still
            if not failures:
                return {"ok": True, "plan": [], "actions": [{"action": "dependency_repair_only", "ok": True}]}

    by_dist = {}
    for f in failures:
        dist_info = f.get("dist_path")
        if isinstance(dist_info, Path):
            by_dist.setdefault(dist_info, f)

    plan = []
    actions = []
    for dist_info, f in sorted(by_dist.items(), key=lambda kv: str(kv[0]).lower()):
        failed_import = f.get("import")
        if _should_skip_failure(f):
            plan.append({"dist": dist_info.name, "kind": "skip", "name": None, "import": failed_import, "reason": "platform/expected"})
            continue
        kind, name = _classify_dist(dist_info, conda_entries_by_name=conda_entries_by_name)

        # The verify-imports blacklist is about conda solver failures. Do not block pip repairs.
        if kind == "conda" and name and normalize_name(name) in blocked_names:
            # If an import is crashing the interpreter, prefer removing the package rather than looping forever.
            err_lower = (f.get("error") or "").lower()
            if "fatal python error" in err_lower:
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "remove",
                        "name": None,
                        "import": failed_import,
                        "reason": f"blacklisted for python {pyver} + fatal import crash; removing package",
                        "uninstall": {"kind": "conda", "name": name},
                    }
                )
                continue
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "skip",
                    "name": None,
                    "import": failed_import,
                    "reason": f"blacklisted for python {pyver} (previous solver failure)",
                }
            )
            continue

        # idna_ssl is a legacy compatibility package; on modern Python it can break due to removed APIs.
        if name and normalize_name(name) in ("idna-ssl", "idna_ssl") and _is_idna_ssl_match_hostname_error(f.get("error")):
            uninstall_kind = "conda" if kind == "conda" else "pip"
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "remove",
                    "name": None,
                    "import": failed_import,
                    "reason": "obsolete/incompatible: idna_ssl relies on ssl.match_hostname (removed); removing",
                    "uninstall": {"kind": uninstall_kind, "name": name},
                }
            )
            continue

        # Jedi should be importable on Windows; if `jedi/common/` shadows `jedi/common.py`, clean it up.
        if name and normalize_name(name) == "jedi" and _is_jedi_common_shadowing_error(f.get("error")):
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "cleanup",
                    "name": "jedi-common-dir",
                    "import": failed_import,
                    "reason": "jedi.common shadowing: remove jedi/common/ so jedi/common.py wins",
                }
            )
            continue

        # If a conda package cannot possibly fix a missing Python module (no site-packages files),
        # fall back to pip for the import name.
        missing_mod = _extract_missing_module_name(f.get("error"))
        if kind == "conda" and name and missing_mod and normalize_name(missing_mod) == normalize_name(failed_import or ""):
            if not _conda_pkg_has_site_packages_files(env_path, name):
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "pip",
                        "name": missing_mod,
                        "import": failed_import,
                        "reason": f"conda '{name}' has no site-packages files; installing '{missing_mod}' via pip",
                    }
                )
                continue

        # attrdict is archived/inactive and breaks on modern Python because collections.Mapping was removed.
        # Replace it with attrdict3 (drop-in fork) when we see this signature.
        if name and normalize_name(name) == "attrdict" and _is_removed_collections_mapping_error(f.get("error")):
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "conda",
                    "name": "attrdict3",
                    "import": failed_import,
                    "reason": "deprecated/broken: replace attrdict with attrdict3 (collections.Mapping removed)",
                    "uninstall": {"kind": "conda" if kind == "conda" else "pip", "name": "attrdict"},
                }
            )
            continue

        # Handle deprecated wrappers even if defaults are disabled:
        # - if the replacement is already installed, just remove the wrapper
        # - otherwise try to install the replacement and then remove the wrapper
        if name and normalize_name(name) == "conda-token":
            uninstall_kind = "conda" if kind == "conda" else "pip"
            if normalize_name("anaconda-auth") in initially_installed:
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "remove",
                        "name": None,
                        "import": failed_import,
                        "reason": "deprecated-wrapper: conda-token already replaced; remove conda-token (no reinstall)",
                        "uninstall": {"kind": uninstall_kind, "name": "conda-token"},
                    }
                )
                continue
            replacement = _maybe_replace_deprecated_wrapper(name, manager=manager, debug=debug)
            if replacement:
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": replacement["install"],
                        "import": failed_import,
                        "reason": f"deprecated-wrapper: {name} -> {replacement['install']}",
                        "uninstall": {"kind": uninstall_kind, "name": replacement["remove"]},
                    }
                )
                continue

        # boto2 can be effectively unfixable on modern Python; this specific error indicates its vendored six can't provide moves.
        # boto3 is the successor (but does not provide the `boto` module). If boto3 is present, remove boto2 to stop the loop.
        if name and normalize_name(name) == "boto" and _is_boto_vendored_six_moves_error(f.get("error")):
            if normalize_name("boto3") in initially_installed:
                uninstall_kind = "conda" if kind == "conda" else "pip"
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "remove",
                        "name": None,
                        "import": failed_import,
                        "reason": "unfixable: boto (boto2) fails (vendored six.moves); remove boto (boto3 already present)",
                        "uninstall": {"kind": uninstall_kind, "name": "boto"},
                    }
                )
                continue
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "skip",
                    "name": None,
                    "import": failed_import,
                    "reason": "unfixable: boto (boto2) fails (vendored six.moves); migrate to boto3 or pin older Python",
                }
            )
            continue

        if kind == "conda" and nodefaults and not has_defaults:
            entry = conda_entries_by_name.get(normalize_name(name or "")) or {}
            ch = (entry.get("channel") or "").lower()
            if ch.startswith("pkgs"):
                # Even if defaults are disabled, we can still try to reinstall from the configured channels
                # (typically conda-forge). If that fails, the solver-offender logic will blacklist it.
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": name,
                        "import": failed_import,
                        "reason": "defaults-disabled: attempting reinstall from configured channels",
                    }
                )
                continue
        plan.append({"dist": dist_info.name, "kind": kind, "name": name, "import": failed_import, "reason": None})

    def _print_fix_plan(plan_items):
        items = list(plan_items or [])
        if not items:
            return
        print("\nFix plan:")
        for item in items:
            kind = item.get("kind") or "?"
            name = item.get("name")
            dist = item.get("dist") or "?"
            reason = item.get("reason")
            uninstall = item.get("uninstall") or {}
            remove_name = uninstall.get("name")
            remove_kind = uninstall.get("kind")
            if kind == "remove" and remove_name and remove_kind:
                line = f"  - {dist}: remove via {remove_kind}: {remove_name}"
                if reason:
                    line += f" [{reason}]"
            else:
                line = f"  - {dist}: {kind}"
                if name:
                    line += f" -> {name}"
                if reason:
                    line += f" [{reason}]"
                if remove_name and remove_kind:
                    line += f" (then remove via {remove_kind}: {remove_name})"
            print(line)

    _print_fix_plan(plan)

    ok_all = True

    # Explicit removals (no install dependency).
    explicit_conda_rm, explicit_pip_rm = _collect_explicit_removals(plan)
    if explicit_conda_rm:
        if is_conda and manager:
            refreshed = get_env_package_entries(env_path, manager, show_json_output=debug)
            present = {normalize_name((e or {}).get("name") or "") for e in refreshed if isinstance(e, dict)}
            conda_rm_present = [p for p in explicit_conda_rm if normalize_name(p) in present]
            if conda_rm_present:
                print(f"\nRemoving {len(conda_rm_present)} package(s) via {manager} (remove-only)...")
                print("Conda remove targets:", ", ".join(conda_rm_present))
                ok_rm = conda_remove(env_path, conda_rm_present, manager)
                ok_all = ok_all and ok_rm
                actions.append({"action": "conda_remove", "count": len(conda_rm_present), "ok": ok_rm, "packages": conda_rm_present})
        else:
            ok_all = False
            actions.append({"action": "error", "reason": "remove-only requested but no conda/mamba manager available"})
    if explicit_pip_rm and python_exe:
        print(f"\nRemoving {len(explicit_pip_rm)} package(s) via pip (remove-only)...")
        print("Pip remove targets:", ", ".join(explicit_pip_rm))
        ok_rm = pip_uninstall(python_exe, explicit_pip_rm)
        ok_all = ok_all and ok_rm
        actions.append({"action": "pip_uninstall", "count": len(explicit_pip_rm), "ok": ok_rm, "packages": explicit_pip_rm})

    cleanup_items = [p for p in plan if p.get("kind") == "cleanup" and p.get("name")]
    for item in cleanup_items:
        if normalize_name(item.get("name") or "") != "jedi-common-dir":
            continue
        print("\nCleaning up jedi/common shadowing (site-packages)...")
        ok_clean = _cleanup_jedi_common_pkg_dir(python_exe)
        ok_all = ok_all and ok_clean
        actions.append({"action": "cleanup_jedi_common_dir", "ok": ok_clean})

    def _install_conda_batched(packages):
        """
        Try to install all packages in one go for speed.

        If the solver fails, bisect into batches (never single-package installs),
        so we can still make progress without going fully one-by-one.
        """
        pkgs = sorted({p for p in packages if p})
        if not pkgs:
            return True, [], []

        def _try(group):
            ok_group, out, err = conda_install_capture(
                env_path,
                group,
                manager,
                configured_channels,
                ignore_pinned=False,
                force_reinstall=True,
            )
            if ok_group:
                return group, []

            # If libmamba explicitly names offender specs, blacklist and retry without them.
            offenders = sorted({p for p in _extract_solver_offenders((out or "") + "\n" + (err or "")) if p in set(group)})
            if offenders:
                for off in offenders:
                    _blacklist_add(blacklist, pyver=pyver, conda_name=off, reason="solver_incompatible")
                _save_verify_imports_blacklist(blacklist)
                remaining = [p for p in group if p not in set(offenders)]
                if len(remaining) >= 2:
                    print(f"Solver failed; skipping {len(offenders)} incompatible package(s): {', '.join(offenders)}")
                    ok2, _out2, _err2 = conda_install_capture(
                        env_path,
                        remaining,
                        manager,
                        configured_channels,
                        ignore_pinned=False,
                        force_reinstall=True,
                    )
                    if ok2:
                        return remaining, offenders
                # If we can't retry meaningfully, keep original failure.
            if len(group) <= 2:
                # Never split into single-package installs; if 1-2 fail, mark as failed.
                return [], group
            mid = len(group) // 2
            left = group[:mid]
            right = group[mid:]
            # If a split would create a singleton, don't go further.
            if len(left) < 2 or len(right) < 2:
                return [], group
            ok_left, bad_left = _try(left)
            ok_right, bad_right = _try(right)
            return ok_left + ok_right, bad_left + bad_right

        installed, failed = _try(pkgs)
        installed = sorted(set(installed))
        failed = sorted(set(failed))
        return len(failed) == 0, installed, failed

    def _relink_after_conda_remove(plan_items, *, installed_set):
        """
        After `conda remove`, re-force-reinstall any replacement packages that may share files
        with the removed package(s). This mitigates conda's lack of per-file ownership tracking.
        """
        if not manager:
            return True
        to_relink = []
        installed_norm = {normalize_name(p) for p in (installed_set or set())}
        for item in plan_items or []:
            uninstall = item.get("uninstall") or {}
            if uninstall.get("kind") != "conda":
                continue
            replacement = item.get("name")
            if not replacement:
                continue
            if normalize_name(replacement) in installed_norm:
                to_relink.append(replacement)
        pkgs = sorted(set(to_relink))
        if not pkgs:
            return True
        print(f"\nRelinking {len(pkgs)} replacement package(s) via {manager} (--force-reinstall)...")
        print("Conda relink targets:", ", ".join(pkgs))
        return conda_install(env_path, pkgs, manager, configured_channels, ignore_pinned=False, force_reinstall=True)

    conda_items = [p for p in plan if p["kind"] == "conda" and p["name"]]
    if conda_items and manager:
        pkgs = [p["name"] for p in conda_items]
        print(f"\nReinstalling {len(pkgs)} packages via {manager} (batched)...")
        print("Conda targets:", ", ".join(sorted(set(pkgs))))
        ok, installed, failed = _install_conda_batched(pkgs)
        ok_all = ok_all and ok
        actions.append({"action": "conda_reinstall_batch", "count": len(pkgs), "ok": ok, "installed": installed, "failed": failed})

        # If the replacement was installed successfully, remove deprecated wrappers.
        conda_rm, pip_rm = _collect_wrapper_removals(plan, conda_installed=set(installed) | initially_installed)
        if conda_rm:
            refreshed = get_env_package_entries(env_path, manager, show_json_output=debug) if is_conda else []
            present = {normalize_name((e or {}).get("name") or "") for e in refreshed if isinstance(e, dict)}
            conda_rm_present = [p for p in conda_rm if normalize_name(p) in present]
            if conda_rm_present:
                print(f"\nRemoving {len(conda_rm_present)} deprecated wrapper package(s) via {manager}...")
                ok_rm = conda_remove(env_path, conda_rm_present, manager)
                ok_all = ok_all and ok_rm
                actions.append({"action": "conda_remove", "count": len(conda_rm_present), "ok": ok_rm, "packages": conda_rm_present})
                if ok_rm:
                    ok_relink = _relink_after_conda_remove(plan, installed_set=set(installed) | initially_installed)
                    ok_all = ok_all and ok_relink
                    actions.append({"action": "conda_relink", "ok": ok_relink})
        if pip_rm and python_exe:
            print(f"\nRemoving {len(pip_rm)} deprecated wrapper package(s) via pip...")
            ok_rm = pip_uninstall(python_exe, pip_rm)
            ok_all = ok_all and ok_rm
            actions.append({"action": "pip_uninstall", "count": len(pip_rm), "ok": ok_rm, "packages": pip_rm})

    # Wrapper removals that don't require installing anything (replacement already present).
    conda_rm, pip_rm = _collect_wrapper_removals(plan, conda_installed=initially_installed)
    if conda_rm and manager:
        refreshed = get_env_package_entries(env_path, manager, show_json_output=debug) if is_conda else []
        present = {normalize_name((e or {}).get("name") or "") for e in refreshed if isinstance(e, dict)}
        conda_rm_present = [p for p in conda_rm if normalize_name(p) in present]
        if conda_rm_present:
            print(f"\nRemoving {len(conda_rm_present)} deprecated wrapper package(s) via {manager}...")
            ok_rm = conda_remove(env_path, conda_rm_present, manager)
            ok_all = ok_all and ok_rm
            actions.append({"action": "conda_remove", "count": len(conda_rm_present), "ok": ok_rm, "packages": conda_rm_present})
            if ok_rm:
                ok_relink = _relink_after_conda_remove(plan, installed_set=initially_installed)
                ok_all = ok_all and ok_relink
                actions.append({"action": "conda_relink", "ok": ok_relink})
    if pip_rm and python_exe:
        print(f"\nRemoving {len(pip_rm)} deprecated wrapper package(s) via pip...")
        ok_rm = pip_uninstall(python_exe, pip_rm)
        ok_all = ok_all and ok_rm
        actions.append({"action": "pip_uninstall", "count": len(pip_rm), "ok": ok_rm, "packages": pip_rm})

    pip_items = [p for p in plan if p["kind"] in ("pip", "unknown") and p["name"]]
    if pip_items:
        no_deps = bool(is_conda)
        why = " (no-deps in conda env)" if no_deps else ""
        only_binary = os.name == "nt"
        ignore_installed = bool(is_conda)
        extra = (why + (", wheels-only" if only_binary else "")) if (why or only_binary) else ""
        print(f"\nReinstalling {len(pip_items)} packages via pip{extra}...")
        print("Pip targets:", ", ".join(sorted(set(p['name'] for p in pip_items if p.get('name')))))
        for item in pip_items:
            pkg = item["name"]
            imp = item.get("import")
            print(f"  - {pkg}")
            ok = pip_reinstall(
                python_exe,
                pkg,
                no_deps=no_deps,
                only_binary=only_binary,
                ignore_installed=ignore_installed,
            )
            ok_all = ok_all and ok
            actions.append(
                {
                    "action": "pip_reinstall",
                    "package": pkg,
                    "ok": ok,
                    "no_deps": no_deps,
                    "only_binary": only_binary,
                    "ignore_installed": ignore_installed,
                }
            )

            # If the reinstall succeeded but the import still fails, remove the package (pip-managed only).
            if ok and imp and imp.isidentifier():
                ok_imp, err_imp = check_import(imp, python_exe)
                actions.append({"action": "pip_recheck_import", "package": pkg, "import": imp, "ok": ok_imp})
                if not ok_imp:
                    if normalize_name(pkg) in {normalize_name(p) for p in CRITICAL_PIP_PACKAGES}:
                        print(f"    ! Import still fails for {imp}, but refusing to uninstall critical pip package: {pkg}")
                        actions.append({"action": "skip_uninstall_critical_pip", "package": pkg, "import": imp})
                        ok_all = False
                        continue

                    ver = pip_get_version(python_exe, pkg)
                    ver_str = ver or "unknown"
                    print(f"    ! Import still fails after reinstall; removing via pip: {pkg}=={ver_str}")
                    if err_imp:
                        first_lines = err_imp.splitlines()[:8]
                        for line in first_lines:
                            print(f"      {line}")
                    ok_rm = pip_uninstall(python_exe, [pkg])
                    ok_all = ok_all and ok_rm
                    actions.append(
                        {
                            "action": "pip_uninstall_after_failed_reinstall",
                            "package": pkg,
                            "version": ver,
                            "import": imp,
                            "import_error": err_imp,
                            "ok": ok_rm,
                        }
                    )

    skipped = [p for p in plan if p["kind"] == "skip"]
    for item in skipped:
        actions.append({"action": "skip", "dist": item["dist"], "reason": item["reason"]})

    if is_conda and not manager and conda_items:
        ok_all = False
        actions.append({"action": "error", "reason": "conda env but no manager found"})

    return {"ok": ok_all, "plan": plan, "actions": actions}


def parse_record_file(record_path):
    """
    Parse a RECORD file to find the top-level importable packages.
    Heuristic: Look for files in top-level directories (excluding .dist-info/ etc).
    """
    try:
        content = record_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()

    top_levels = set()
    for line in content.splitlines():
        # RECORD format: path,sha256=...,size
        parts = line.split(",")
        if not parts:
            continue
        path = parts[0].strip()
        
        # Windows/Linux path normalization
        path = path.replace("\\", "/")
        
        # Filter noise
        if path.startswith("..") or "/site-packages/" in path:
            continue
        if ".dist-info/" in path or ".egg-info/" in path:
            continue
        
        # Start of path is usually the package name
        if "/" in path:
            top = path.split("/")[0]
            # Ignore __pycache__
            if top == "__pycache__":
                continue
            # If it looks like a valid identifier, add it
            if top.isidentifier():
                top_levels.add(top)
        else:
            # File in root (e.g. 'six.py')
            if path.endswith(".py"):
                top_levels.add(path[:-3])
                
    return sorted(top_levels)

def get_toplevel_imports(dist):
    """
    Extract the top-level import names from a distribution.
    Strategie:
    1. Parse RECORD (most accurate for file-based structure).
    2. Fallback to top_level.txt (can contain virtual modules or old metadata).
    3. Fallback to distribution name guessing.
    """
    # 1. Try RECORD (used by wheels, conda, etc.) - PREFERRED
    record = dist / "RECORD"
    if record.exists():
        found = parse_record_file(record)
        if found:
            return found

    # 2. Try top_level.txt (standard but sometimes lying, e.g. for CFFI bindings)
    top_level = dist / "top_level.txt"
    if top_level.exists():
        try:
            return [line.strip() for line in top_level.read_text().splitlines() if line.strip()]
        except Exception:
            pass
            
    # 3. Fallback: derive from distribution name
    name = dist.name.split("-")[0].replace("_", ".")
    return [name] if name else []

def check_import(package_name, python_exe):
    """
    Run `python -c "import <name>"` via subprocess.
    Returns (ok: bool, error_message: str|None).
    """
    cmd = [python_exe, "-c", f"import {package_name}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            return True, None
        return False, proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Import timed out (>30s)"
    except Exception as e:
        return False, str(e)

def verify_imports(args):
    # `--debug` should show command lines, but avoid dumping huge `--json` payloads by default.
    show_json_output = False
    lang = "auto"
    
    all_envs, base_prefix, manager = discover_envs(show_json_output=show_json_output)
    targets = select_envs(all_envs, [args.env] if args.env else [], base_prefix)
    
    if not targets:
        return {"ok": False, "exit_code": 2, "error": "no target env"}
    
    env_path = targets[0]
    python_exe = get_python_exe(env_path)
    if not python_exe:
        return {"ok": False, "exit_code": 2, "error": "missing python executable"}

    # Discovery of packages to check
    # For now, we scan site-packages for .dist-info
    # A full implementation would use standard library importlib.metadata if available in the target python,
    # but we are running from outside. So manual scan of site-packages is robust for broken envs.
    
    from .discovery import get_site_packages
    site_pkgs = get_site_packages(python_exe)
    if not site_pkgs or not Path(site_pkgs[0]).exists():
         return {"ok": False, "exit_code": 2, "error": "missing site-packages"}
    
    to_check = []
    for sp_path in site_pkgs:
        sp = Path(sp_path)
        if not sp.exists():
            continue
        dists = list(sp.glob("*.dist-info"))
        
        for d in dists:
            # Get import names
            imports = get_toplevel_imports(d)
            for imp in imports:
                if not getattr(args, "full", False):
                    if imp.lower() not in CRITICAL_PACKAGES:
                        continue
                
                # Skip invalid identifiers (e.g. ujson-stubs)
                if not imp.isidentifier():
                    continue
                    
                to_check.append((d.name, imp, sp))
    to_check = sorted(list(set(to_check)))
    
    if not to_check:
         # Fallback: if no critical packages found in lazy mode, warn user or check a few random ones?
         # Or just return empty report.
        if not getattr(args, "full", False):
            # Avoid requiring a translation entry for this experimental path.
            print("No import candidates found in lazy mode.")
        return {"ok": True, "exit_code": 0, "report": {"env": env_path, "checks": 0, "failures": []}}

    results = []
    
    # Parallel execution
    max_workers = min(32, (os.cpu_count() or 1) * 4) 
    print(f"Verifying {len(to_check)} imports using {max_workers} threads...")
    
    progress = Progress(total=len(to_check), label="Verifying imports")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_check = {
            executor.submit(check_import, import_name, python_exe): (dist_name, import_name, sp_path)
            for dist_name, import_name, sp_path in to_check
        }
        
        completed_count = 0
        for future in concurrent.futures.as_completed(future_to_check):
            dist_name, import_name, sp_path = future_to_check[future]
            try:
                ok, error = future.result()
            except Exception as exc:
                ok, error = False, str(exc)
            
            results.append(
                {
                    "dist": dist_name,
                    "dist_path": sp_path / dist_name,  # store path for fixer
                    "import": import_name,
                    "ok": ok,
                    "error": error,
                }
            )
            
            completed_count += 1
            progress.update(completed_count)

    progress.finish()
    
    failures = [r for r in results if not r["ok"]]

    if not args.json:
        print("\nImport Verification Report:")
        print(f"Checked {len(to_check)} imports in {env_path}\n")
        if not failures:
            print("All checked imports succeeded!")
        else:
            print(f"Found {len(failures)} broken imports:\n")
            for f in failures:
                print(f"  ❌ {f['import']} (from {f['dist']})")
                if f.get("error"):
                    for line in f["error"].splitlines()[:8]:
                        print(f"      {line}")

    fix_report = None
    if getattr(args, "fix", False) and failures:
        fix_report = attempt_fix(
            failures,
            python_exe,
            env_path,
            manager,
            base_prefix=base_prefix,
            debug=show_json_output,
        )

    post_failures = []
    if fix_report is not None:
        plan_items = fix_report.get("plan") or []
        plan_by_dist = {p.get("dist"): p for p in plan_items if isinstance(p, dict) and p.get("dist")}
        for f in failures:
            dist = f.get("dist")
            imp = f.get("import")
            p = plan_by_dist.get(dist) if dist else None
            if p and p.get("kind") in ("skip", "remove"):
                continue
            if not imp or not isinstance(imp, str) or not imp.isidentifier():
                post_failures.append(
                    {"dist": dist, "import": imp, "error": f.get("error") or "invalid import name"}
                )
                continue
            ok, err = check_import(imp, python_exe)
            if not ok:
                post_failures.append({"dist": dist, "import": imp, "error": err})
        if not args.json:
            if post_failures:
                print(f"\nPost-fix: {len(post_failures)} import(s) still failing.")
                for pf in post_failures[:12]:
                    dist = pf.get("dist") or "?"
                    imp = pf.get("import") or "?"
                    print(f"  ❌ {imp} (from {dist})")
            else:
                print("\nPost-fix: all non-skipped imports OK.")
    else:
        post_failures = [
            {"dist": f.get("dist"), "import": f.get("import"), "error": f.get("error")} for f in failures
        ]

    ok_all = len(post_failures) == 0 and (fix_report is None or bool(fix_report.get("ok")))

    report = {
        "env": env_path,
        "python": python_exe,
        "checks": len(to_check),
        "failures": [
            {
                "dist": f.get("dist"),
                "import": f.get("import"),
                "error": f.get("error"),
            }
            for f in failures
        ],
        "post_failures": post_failures,
        "fix": fix_report,
    }
    return {"ok": ok_all, "exit_code": 0 if ok_all else 1, "report": report}
