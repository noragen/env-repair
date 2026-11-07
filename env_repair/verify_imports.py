from .i18n import tr
import concurrent.futures
import contextlib
import os
import json
import re
import subprocess
import shutil
import signal
import sys
from urllib.parse import urlparse
from pathlib import Path

from .conda_ops import conda_install, conda_install_capture, conda_remove, conda_upgrade_dependencies, get_env_package_entries, is_conda_env
from .conda_config import load_conda_channels
from .discovery import discover_envs, get_python_exe, select_envs, which
from .naming import normalize_name
from .pip_ops import pip_reinstall, pip_uninstall
from .progress import Progress
from .subprocess_utils import run_json_cmd
from .unfixable import remove_unfixable as remove_unfixable_consumers
from .qt_repair import repair_qt_bindings

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

LEGACY_UNMAINTAINED_PACKAGES = {
    "nose": {
        "reason": "legacy/unmaintained package",
        "hint": "nose is unmaintained; prefer migrating test runs to pytest/unittest",
    }
}


def _normalize_env_filters(env_arg):
    """
    `argparse` in this project defines `--env` both at the top-level (multi env)
    and inside subcommands like `verify-imports` (single env). Depending on where
    the user places the flag, `args.env` may be a string, a list, or None.
    """
    if env_arg is None:
        return []
    if isinstance(env_arg, str):
        return [env_arg] if env_arg else []
    if isinstance(env_arg, (list, tuple)):
        out = []
        for item in env_arg:
            if isinstance(item, str) and item:
                out.append(item)
        return out
    return []


@contextlib.contextmanager
def _ignore_sigint_windows():
    """
    On Windows users often press Ctrl+C multiple times. If the 2nd Ctrl+C hits while
    the ThreadPoolExecutor is shutting down, Python may emit noisy shutdown traces.
    Temporarily ignore SIGINT while we cleanly drain worker threads.
    """
    if os.name != "nt":
        yield
        return
    old = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        yield
    finally:
        try:
            signal.signal(signal.SIGINT, old)
        except Exception:
            pass


def _run_import_checks_parallel(*, to_check, python_exe, max_workers, progress):
    results = []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    future_to_check = {}
    try:
        for dist_name, import_name, sp_path in to_check:
            future_to_check[
                executor.submit(check_import, import_name, python_exe)
            ] = (dist_name, import_name, sp_path)

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
        return results
    except KeyboardInterrupt:
        # Cancel as much as we can, then perform a clean shutdown.
        for fut in list(future_to_check.keys()):
            try:
                fut.cancel()
            except Exception:
                pass
        with _ignore_sigint_windows():
            executor.shutdown(wait=True, cancel_futures=True)
        raise
    finally:
        try:
            progress.finish()
        except Exception:
            pass
        # Normal path shutdown (in case we returned early without draining).
        try:
            executor.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass


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


def _dist_has_local_direct_url(dist_info):
    path = dist_info / "direct_url.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    url = data.get("url")
    if not isinstance(url, str) or not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme.lower() == "file"


def _print_import_error(error):
    # Keep the exception at the end of a long traceback visible.
    lines = str(error or "").splitlines()
    if len(lines) > 16:
        lines = lines[:4] + ["... (traceback shortened) ..."] + lines[-12:]
    for line in lines:
        print(f"      {line}")


def _should_skip_local_unmanaged_dist(dist_info, *, kind):
    return kind == "unknown" and _dist_has_local_direct_url(dist_info)


def _is_import_timeout_error(error):
    if not error:
        return False
    e = str(error).lower()
    return "import timed out" in e


def _search_json_has_package(search_json, package_name):
    """
    Best-effort detection whether `package_name` exists in a conda/mamba search --json payload.
    Supports both solver payload styles used by conda/mamba.
    """
    if not isinstance(search_json, dict) or not package_name:
        return False
    target = normalize_name(package_name)

    # conda/mamba often use {"pkgname": [builds...]}
    for key in search_json.keys():
        if normalize_name(key) == target:
            return True

    # Some payloads use {"result": {"pkgs": [{"name": "..."}]}}
    result = search_json.get("result")
    if isinstance(result, dict):
        pkgs = result.get("pkgs")
        if isinstance(pkgs, list):
            for pkg in pkgs:
                if isinstance(pkg, dict) and normalize_name(pkg.get("name") or "") == target:
                    return True
    return False


def _conda_search_has_package(manager, package_name, *, debug):
    if manager not in ("conda", "mamba") or not package_name:
        return False
    try:
        data = run_json_cmd([manager, "search", package_name, "--json"], show_json_output=debug)
    except Exception:
        return False
    return _search_json_has_package(data, package_name)


def _pick_conda_candidate_for_local_direct_url(*, manager, dist_name, failed_import, debug):
    """
    For local file/path installs (`direct_url=file://`), probe conda search before skipping.
    Returns a candidate conda package name or None.
    """
    candidates = []
    if dist_name:
        candidates.append(dist_name)
    if failed_import:
        candidates.append(failed_import)
        candidates.append(failed_import.replace("_", "-"))
        candidates.append(failed_import.replace(".", "-"))

    seen = set()
    deduped = []
    for cand in candidates:
        n = normalize_name(cand or "")
        if not n or n in seen:
            continue
        seen.add(n)
        deduped.append(cand)

    for cand in deduped:
        if _conda_search_has_package(manager, cand, debug=debug):
            return cand
    return None


def _is_numba_llvmlite_version_error(error):
    if not error:
        return False
    e = str(error).lower()
    return "numba requires at least version" in e and "of llvmlite" in e


def _is_numba_numpy_upper_bound_error(error):
    if not error:
        return False
    e = str(error).lower()
    return "numba needs numpy" in e and ("or less" in e or "<" in e)


def _is_blacklist_skip_active(*, kind, name, blocked_names, initially_installed):
    if kind != "conda" or not name:
        return False
    key = normalize_name(name)
    if key not in blocked_names:
        return False
    # If the package is already conda-installed in this env, allow one more reinstall attempt.
    # This avoids stale blacklist entries permanently suppressing repair of installable packages.
    return key not in initially_installed


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


def _missing_module_conda_fallback(mod_norm):
    """
    Map missing modules to conda providers with a different package name.
    """
    if not mod_norm:
        return None
    mapping = {
        normalize_name("_brotli"): "brotli-python",
        normalize_name("_argon2_cffi_bindings"): "argon2-cffi-bindings",
        normalize_name("matplotlib"): "matplotlib-base",
    }
    return mapping.get(normalize_name(mod_norm))


def _import_failure_cause(error):
    text = str(error or "")
    if "qtpy.QtBindingsNotFoundError" in text and "No Qt bindings could be found" in text:
        return "qt-bindings", False
    metadata = re.findall(r"PackageNotFoundError: No package metadata was found for ([\w.-]+)", text)
    if metadata:
        return metadata[-1], True
    modules = re.findall(r"(?:AttributeError: (?:partially initialized )?module|ImportError: cannot import name [^\n]+ from) ['\"]([\w.]+)['\"]", text)
    if modules:
        if modules[-1] == "conda.plugins" and "CondaAuthHandler" in text and "anaconda-auth" in text:
            return "anaconda_auth", False
        return modules[-1].split(".", 1)[0], False
    if "anaconda-auth" in text and "CondaAuthHandler" in text:
        return "anaconda_auth", False
    missing = _extract_missing_module_name(error)
    return (missing, False) if missing else (None, False)


def _failure_is_external(error, import_name):
    cause, metadata = _import_failure_cause(error)
    return bool(cause and (metadata or normalize_name(cause) != normalize_name(import_name or "")))


def _quarantine_charset_extensions(env_path, sites):
    prefix = Path(env_path).resolve()
    owned = set()
    records = list((prefix / "conda-meta").glob("*.json"))
    for record_path in records:
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"ok": False, "moved": [], "reason": "unreadable conda ownership record"}
        if not isinstance(record, dict):
            return {"ok": False, "moved": [], "reason": "invalid conda ownership record"}
        files = record.get("files") or []
        paths_data = record.get("paths_data") or {}
        if not isinstance(paths_data, dict) or not isinstance(files, list):
            return {"ok": False, "moved": [], "reason": "invalid conda ownership paths"}
        paths = paths_data.get("paths") or []
        if not isinstance(paths, list):
            return {"ok": False, "moved": [], "reason": "invalid conda ownership paths"}
        owned.update(str(path).replace("\\", "/").lower() for path in files)
        owned.update(str(path.get("_path", "")).replace("\\", "/").lower()
                     for path in paths if isinstance(path, dict))
    moved = []
    for site in sites:
        root = Path(site) / "charset_normalizer"
        for stem in ("cd", "md"):
            replacements = []
            for candidate in root.glob(f"{stem}.*"):
                if not candidate.is_file() or candidate.suffix not in {".py", ".pyd"}:
                    continue
                try:
                    relative = candidate.resolve().relative_to(prefix)
                except ValueError:
                    continue
                if relative.as_posix().lower() in owned:
                    replacements.append(candidate)
            if not replacements:
                continue
            for binary in root.glob(f"{stem}*.pyd"):
                if binary.name.split(".", 1)[0] != stem:
                    continue
                try:
                    relative = binary.resolve().relative_to(prefix)
                except ValueError:
                    continue
                if relative.as_posix().lower() in owned:
                    continue
                from uuid import uuid4
                destination = prefix / ".env_repair" / "quarantine" / uuid4().hex / relative
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(binary), str(destination))
                except OSError as exc:
                    return {"ok": False, "moved": moved, "error": str(exc)}
                moved.append({"source": str(binary), "destination": str(destination)})
                print(tr('Quarantined unowned extension: {value0} -> {value1}', value0=binary, value1=destination))
    return {"ok": True, "moved": moved}


def _repair_import_dependencies(failures, *, python_exe, env_path, manager, entries, channels, use_uv=False):
    by_name = {normalize_name(e.get("name") or ""): e for e in entries if isinstance(e, dict)}
    providers = {}
    for record_path in (Path(env_path) / "conda-meta").glob("*.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or not isinstance(record.get("name"), str):
            continue
        entry = dict(record, channel=record.get("channel") or "conda-meta")
        by_name[normalize_name(record["name"])] = entry
        for owned_path in record.get("files") or []:
            if not isinstance(owned_path, str):
                continue
            relative = owned_path.replace("\\", "/").split("site-packages/", 1)
            if len(relative) != 2:
                continue
            top = relative[1].split("/", 1)[0]
            if top.endswith(".dist-info") and "-" in top:
                dist_name = top[:-len(".dist-info")].rsplit("-", 1)[0]
                by_name[normalize_name(dist_name)] = entry
            else:
                module = top[:-3] if top.endswith(".py") else top
                if module.isidentifier():
                    providers.setdefault(normalize_name(module), set()).add(record["name"])
    sites = {f["dist_path"].parent for f in failures if isinstance(f.get("dist_path"), Path)}
    for site in sites:
        for dist in site.glob("*.dist-info"):
            name = _read_metadata_name(dist)
            if name:
                provider_name = (by_name.get(normalize_name(name)) or {}).get("name") or name
                for module in get_toplevel_imports(dist):
                    providers.setdefault(normalize_name(module), set()).add(provider_name)
    targets = {"conda": set(), "pip": set()}
    upgrade_consumers = set()
    causes = set()
    for failure in failures:
        if _should_skip_failure(failure):
            continue
        cause, metadata = _import_failure_cause(failure.get("error"))
        if cause == "pkg_resources":
            dist = failure.get("dist_path")
            name = _read_metadata_name(dist) if isinstance(dist, Path) else None
            entry = by_name.get(normalize_name(name or "")) or {}
            if name and normalize_name(name) != "setuptools" and manager and entry and "pypi" not in (entry.get("channel") or "").lower():
                upgrade_consumers.add(entry.get("name") or name)
                causes.add(normalize_name(cause))
            continue
        if cause == "qt-bindings":
            selected = os.environ.get("QT_API", "").lower()
            bindings = []
            for entry in by_name.values():
                name = entry.get("name") or ""
                key = normalize_name(name)
                api = {"pyside2": "pyside2", "pyside6": "pyside6", "pyqt6": "pyqt6", "pyqt5": "pyqt5"}.get(key)
                if key == "pyqt":
                    api = "pyqt6" if str(entry.get("version") or "").startswith("6.") else "pyqt5"
                if api and (not selected or selected == api) and "pypi" not in (entry.get("channel") or "").lower():
                    bindings.append(name)
            bindings = sorted(set(bindings))
            if manager and len(bindings) == 1:
                targets["conda"].add(bindings[0])
                causes.add(cause)
            continue
        if not cause or cause in {"imp", "fcntl"}:
            continue
        cause_key = normalize_name(cause)
        names = {cause} if metadata else providers.get(cause_key, {cause})
        if len(names) != 1:
            continue
        name = next(iter(names))
        entry = by_name.get(normalize_name(name))
        if entry and "pypi" not in (entry.get("channel") or "").lower() and manager:
            targets["conda"].add(entry.get("name") or name)
        elif entry or cause_key in providers:
            local_dists = [dist for site in sites for dist in site.glob("*.dist-info")
                           if normalize_name(_read_metadata_name(dist) or "") == normalize_name(name)]
            if any(_dist_has_local_direct_url(dist) for dist in local_dists):
                continue
            targets["pip"].add(name)
        else:
            fallback = _missing_module_conda_fallback(cause_key)
            if not fallback or not manager:
                continue
            targets["conda"].add(fallback)
        causes.add(cause_key)
    actions = []
    if upgrade_consumers:
        packages = sorted(upgrade_consumers)
        ok = conda_upgrade_dependencies(env_path, packages, manager, channels)
        actions.append({"action": "legacy_consumer_upgrade", "packages": packages, "ok": ok})
    for kind, packages in targets.items():
        if not packages:
            continue
        packages = sorted(packages)
        print(tr('\nPriority: repairing {value0} dependency targets: {value1}', value0=kind, value1=', '.join(packages)))
        if kind == "conda":
            ok = conda_install(env_path, packages, manager, channels, ignore_pinned=False, force_reinstall=True)
            if ok and any(normalize_name(name) == "charset-normalizer" for name in packages):
                quarantine = _quarantine_charset_extensions(env_path, sites)
                actions.append(dict(quarantine, action="quarantine_charset_extensions"))
        else:
            results = [pip_reinstall(python_exe, name, no_deps=is_conda_env(env_path),
                                    only_binary=os.name == "nt", ignore_installed=is_conda_env(env_path),
                                    use_uv=use_uv) for name in packages]
            ok = all(results)
        actions.append({"action": f"{kind}_dependency_reinstall", "packages": packages, "ok": ok})
    return causes, actions


def _is_protobuf_generatedfile_mismatch_error(err):
    if not err:
        return False
    e = str(err)
    return (
        "_CheckCalledFromGeneratedFile" in e
        and ("google\\protobuf\\descriptor.py" in e or "google/protobuf/descriptor.py" in e)
    )


def _is_dll_procedure_not_found_error(err):
    if not err:
        return False
    e = str(err).lower()
    return ("die angegebene prozedur wurde nicht gefunden" in e) or ("specified procedure could not be found" in e)


def _preclean_module_roots_for_conda_package(conda_name):
    mapping = {
        "pyarrow": ["pyarrow"],
        "scikit-learn": ["sklearn"],
        "imbalanced-learn": ["imblearn"],
        "pygithub": ["github"],
        "pynacl": ["nacl"],
        "streamlit": ["streamlit"],
    }
    return mapping.get(normalize_name(conda_name), [])


def _stubborn_import_pip_target(import_name):
    mapping = {
        "pyarrow": "pyarrow",
        "github": "pygithub",
        "streamlit": "streamlit",
    }
    return mapping.get(normalize_name(import_name or ""))


def _requires_hard_remove_before_reinstall(conda_name):
    # Some packages can stay broken even after force-reinstall when file ownership
    # is mixed. Hard-remove first, then reinstall.
    return normalize_name(conda_name or "") in {"pygithub"}


def _is_nacl_sodium_dll_error(err):
    if not err:
        return False
    e = str(err).lower()
    return ("_sodium" in e) and ("dll load failed" in e) and ("nacl" in e or "sodium" in e)


def _preclean_stubborn_conda_artifacts(*, python_exe, conda_packages):
    from .discovery import get_site_packages

    roots_by_pkg = {
        normalize_name(pkg): _preclean_module_roots_for_conda_package(pkg)
        for pkg in (conda_packages or [])
    }
    roots = sorted({r for vals in roots_by_pkg.values() for r in vals})
    if not roots:
        return {"ok": True, "removed": 0}

    removed = 0
    ok = True
    for sp in get_site_packages(python_exe) or []:
        sp_path = Path(sp)
        if not sp_path.exists():
            continue
        for root in roots:
            p_dir = sp_path / root
            p_file = sp_path / f"{root}.py"
            if p_dir.exists() and p_dir.is_dir():
                try:
                    shutil.rmtree(p_dir, ignore_errors=True)
                    removed += 1
                except Exception:
                    ok = False
            if p_file.exists() and p_file.is_file():
                try:
                    p_file.unlink()
                    removed += 1
                except Exception:
                    ok = False

            # Remove matching dist-info / egg-info stubs for the same root.
            for di in sp_path.glob("*.dist-info"):
                base = normalize_name(di.name.split("-", 1)[0])
                if base in (normalize_name(root), normalize_name(root.replace("_", "-"))):
                    try:
                        shutil.rmtree(di, ignore_errors=True)
                        removed += 1
                    except Exception:
                        ok = False
            for ei in sp_path.glob("*.egg-info"):
                base = normalize_name(ei.name.split("-", 1)[0])
                if base in (normalize_name(root), normalize_name(root.replace("_", "-"))):
                    try:
                        if ei.is_dir():
                            shutil.rmtree(ei, ignore_errors=True)
                        else:
                            ei.unlink()
                        removed += 1
                    except Exception:
                        ok = False
    return {"ok": ok, "removed": removed}


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
    for m in re.finditer(r"(?:^|\n)[^\n]*\b([A-Za-z0-9_.-]+)\s*=\*\s*\*\s*does not exist", output_text):
        offenders.add(m.group(1))
    for m in re.finditer(r"(?:^|\n)[^\n]*\b([A-Za-z0-9_.-]+)\s*==?[^\n]*does not exist", output_text):
        offenders.add(m.group(1))
    return offenders


def _is_python_pin_conflict(output_text):
    if not output_text:
        return False
    t = output_text.lower()
    return ("pin on python" in t) and ("conflicts with any installable versions" in t)


def _legacy_unmaintained_meta(name):
    if not name:
        return None
    return LEGACY_UNMAINTAINED_PACKAGES.get(normalize_name(name))


def _collect_defaults_disabled_pip_fallback_candidates(*, conda_items, failed_pkgs):
    failed = {normalize_name(p) for p in (failed_pkgs or []) if p}
    out = []
    for item in conda_items or []:
        name = item.get("name")
        reason = (item.get("reason") or "").lower()
        if not name:
            continue
        if normalize_name(name) not in failed:
            continue
        if "defaults-disabled" not in reason:
            continue
        if _legacy_unmaintained_meta(name):
            continue
        out.append(
            {
                "dist": item.get("dist"),
                "kind": "pip",
                "name": name,
                "import": item.get("import"),
                "reason": "conda solver failed in defaults-disabled mode; trying pip fallback",
            }
        )
    return out


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


def attempt_fix(failures, python_exe, env_path, manager, *, base_prefix, debug, use_uv=False, remove_unfixable=False, interactive_removal=True, interactive_qt=True):
    if not failures:
        return {"ok": True, "actions": []}

    is_conda = is_conda_env(env_path)
    entries = get_env_package_entries(env_path, manager, show_json_output=debug) if (is_conda and manager) else []
    conda_entries_by_name = {normalize_name(e.get("name") or ""): e for e in entries if isinstance(e, dict)}
    from .scan import scan_conda_pip_overwrites

    failure_sites = {f["dist_path"].parent for f in failures if isinstance(f.get("dist_path"), Path)}
    overwritten = {
        normalize_name(i["name"]): i
        for i in scan_conda_pip_overwrites(env_path, failure_sites)
    } if is_conda else {}
    initially_installed = set(conda_entries_by_name.keys())
    pyver = _python_major_minor(python_exe) or "unknown"
    blacklist = _load_verify_imports_blacklist()
    blocked_for_py = blacklist.get(pyver) or {}
    blocked_names = set(blocked_for_py.keys())
    configured_channels = load_conda_channels(
        base_prefix=base_prefix,
        has_conda=which("conda") is not None,
        has_mamba=which("mamba") is not None,
        show_json_output=debug,
    )
    nodefaults = "nodefaults" in configured_channels
    has_defaults = ("defaults" in configured_channels) or ("anaconda" in configured_channels)

    dependency_causes, dependency_actions = _repair_import_dependencies(
        failures, python_exe=python_exe, env_path=env_path, manager=manager,
        entries=entries, channels=configured_channels, use_uv=use_uv,
    )
    if dependency_actions:
        remaining = []
        for failure in failures:
            imp = failure.get("import")
            if not isinstance(imp, str) or not imp.isidentifier():
                remaining.append(failure)
                continue
            ok, error = check_import(imp, python_exe)
            if not ok:
                remaining.append(dict(failure, error=error))
        failures = remaining
        if not failures:
            return {"ok": True, "plan": [], "actions": dependency_actions}


    failures, qt_actions = repair_qt_bindings(
        failures, python_exe=python_exe, env_path=env_path, manager=manager,
        channels=configured_channels, in_conda_env=is_conda, check_import=check_import,
        interactive=interactive_qt and os.environ.get("ENV_REPAIR_REVERIFY") != "1", use_uv=use_uv,
    )
    dependency_actions.extend(qt_actions)
    if not failures:
        return {"ok": True, "plan": [], "actions": dependency_actions}

    by_dist = {}
    for f in failures:
        dist_info = f.get("dist_path")
        if isinstance(dist_info, Path):
            by_dist.setdefault(dist_info, f)

    plan = []
    actions = list(dependency_actions)
    for dist_info, f in sorted(by_dist.items(), key=lambda kv: str(kv[0]).lower()):
        failed_import = f.get("import")
        if f.get("removed"):
            plan.append({"dist": dist_info.name, "kind": "removed", "name": None,
                         "import": failed_import, "reason": "Qt consumer removal explicitly confirmed and completed"})
            continue
        missing_mod = _extract_missing_module_name(f.get("error"))
        if _should_skip_failure(f):
            plan.append({"dist": dist_info.name, "kind": "skip", "name": None, "import": failed_import, "reason": "platform/expected"})
            continue
        kind, name = _classify_dist(dist_info, conda_entries_by_name=conda_entries_by_name)
        cause, _ = _import_failure_cause(f.get("error"))
        if cause in {"pkg_resources", "qt-bindings"}:
            reason = (
                "legacy consumer requires pkg_resources (removed from setuptools 82+); "
                "upgrade/migrate the consumer; no automatic setuptools downgrade or pip overlay"
                if cause == "pkg_resources" else
                "Qt backend unavailable; repair the selected installed PyQt/PySide binding or choose a compatible binding; "
                f"QT_API={os.environ.get('QT_API') or '<auto>'}; do not reinstall Qt consumers"
            )
            plan.append({"dist": dist_info.name, "kind": "defer", "name": None,
                         "import": failed_import, "reason": reason,
                         "unfixable": cause, "consumer": name, "installer": kind,
                         "import_error": f.get("error"), "dist_path": str(dist_info)})
            continue
        legacy = _legacy_unmaintained_meta(name)
        if legacy:
            plan.append({"dist": dist_info.name, "kind": "skip", "name": None,
                         "import": failed_import,
                         "reason": f"{legacy['reason']}: {name}; {legacy['hint']}"})
            continue


        # The verify-imports blacklist is about conda solver failures. Do not block pip repairs.
        if _is_blacklist_skip_active(
            kind=kind,
            name=name,
            blocked_names=blocked_names,
            initially_installed=initially_installed,
        ):
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

        # Numba import may fail due to stale llvmlite, even when `numba` itself is present.
        # Reinstall llvmlite explicitly from conda to satisfy numba runtime requirement.
        if kind == "conda" and name and normalize_name(name) == "numba" and _is_numba_llvmlite_version_error(f.get("error")):
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "conda",
                    "name": "llvmlite",
                    "import": failed_import,
                    "reason": "numba import requires newer llvmlite; reinstalling llvmlite",
                }
            )
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "conda",
                    "name": name,
                    "import": failed_import,
                    "reason": "relink numba after llvmlite update",
                }
            )
            continue

        # Numba may also fail when numpy is too new for this numba build.
        if kind == "conda" and name and normalize_name(name) == "numba" and _is_numba_numpy_upper_bound_error(f.get("error")):
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "conda",
                    "name": "numpy<2.4",
                    "import": failed_import,
                    "reason": "numba requires numpy<2.4 for this build",
                }
            )
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "conda",
                    "name": "numba",
                    "import": failed_import,
                    "reason": "relink numba after numpy downgrade",
                }
            )
            continue

        # imbalanced-learn often fails as a follow-up when sklearn/scipy/numpy stack is inconsistent.
        if kind == "conda" and name and normalize_name(name) == "imbalanced-learn":
            err_lower = (f.get("error") or "").lower()
            if "from sklearn.base import clone" in err_lower:
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": "numpy",
                        "import": failed_import,
                        "reason": "imblearn depends on sklearn stack; relink numpy",
                    }
                )
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": "scipy",
                        "import": failed_import,
                        "reason": "imblearn depends on sklearn stack; relink scipy",
                    }
                )
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": "scikit-learn",
                        "import": failed_import,
                        "reason": "imblearn depends on sklearn stack; relink scikit-learn",
                    }
                )
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": "imbalanced-learn",
                        "import": failed_import,
                        "reason": "relink imbalanced-learn after sklearn stack relink",
                    }
                )
                continue

        # sklearn import traces often indicate underlying scipy/numpy ABI drift.
        if kind == "conda" and name and normalize_name(name) == "scikit-learn":
            err_lower = (f.get("error") or "").lower()
            if "sklearn.utils" in err_lower or "from sklearn.base import clone" in err_lower:
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": "numpy",
                        "import": failed_import,
                        "reason": "sklearn stack repair; relink numpy",
                    }
                )
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": "scipy",
                        "import": failed_import,
                        "reason": "sklearn stack repair; relink scipy",
                    }
                )
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": "scikit-learn",
                        "import": failed_import,
                        "reason": "sklearn stack repair; relink scikit-learn",
                    }
                )
                continue

        # pyarrow DLL/procedure issues are frequently due to mixed arrow runtime artifacts.
        if kind == "conda" and name and normalize_name(name) == "pyarrow":
            if _is_dll_procedure_not_found_error(f.get("error")):
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": "libarrow",
                        "import": failed_import,
                        "reason": "pyarrow DLL mismatch; relink libarrow runtime",
                    }
                )
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": "pyarrow",
                        "import": failed_import,
                        "reason": "pyarrow DLL mismatch; relink pyarrow",
                    }
                )
                continue

        # pygithub imports can fail when its dependency chain drifted.
        if kind == "conda" and name and normalize_name(name) == "pygithub":
            if _is_nacl_sodium_dll_error(f.get("error")):
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": "libsodium",
                        "import": failed_import,
                        "reason": "pygithub->pynacl _sodium DLL mismatch; relink libsodium",
                    }
                )
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "conda",
                    "name": "requests",
                    "import": failed_import,
                    "reason": "pygithub dependency chain relink (requests)",
                }
            )
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "conda",
                    "name": "pynacl",
                    "import": failed_import,
                    "reason": "pygithub dependency chain relink (pynacl)",
                }
            )
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "conda",
                    "name": "pyjwt",
                    "import": failed_import,
                    "reason": "pygithub dependency chain relink (pyjwt)",
                }
            )
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "conda",
                    "name": "cryptography",
                    "import": failed_import,
                    "reason": "pygithub dependency chain relink (cryptography)",
                }
            )
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "conda",
                    "name": "pygithub",
                    "import": failed_import,
                    "reason": "pygithub dependency chain relink (pygithub)",
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

        # Streamlit protobuf codegen/runtime mismatch:
        # force-reinstall protobuf first, then relink streamlit.
        if (
            kind == "conda"
            and name
            and normalize_name(name) == "streamlit"
            and _is_protobuf_generatedfile_mismatch_error(f.get("error"))
        ):
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "conda",
                    "name": "protobuf>=4,<7",
                    "import": failed_import,
                    "reason": "streamlit protobuf mismatch; align protobuf runtime with newer streamlit",
                }
            )
            plan.append(
                {
                    "dist": dist_info.name,
                    "kind": "conda",
                    "name": "streamlit>=1.30",
                    "import": failed_import,
                    "reason": "upgrade/relink streamlit to protobuf-compatible build",
                }
            )
            continue

        cause, _metadata_missing = _import_failure_cause(f.get("error"))
        if _failure_is_external(f.get("error"), failed_import) or normalize_name(cause or "") in dependency_causes:
            plan.append({"dist": dist_info.name, "kind": "defer", "name": None,
                         "import": failed_import,
                         "reason": f"unresolved failure in {cause}; preserving installation after dependency check"})
            continue
        overwrite = overwritten.get(normalize_name(name or ""))
        if overwrite and manager:
            plan.append({
                "dist": dist_info.name, "kind": "conda", "name": overwrite["package"],
                "import": failed_import, "pip_cleanup_before_relink": overwrite["name"],
                "reason": "pip metadata replaced conda-owned files; uninstall pip overlay before conda relink",
            })
            continue
        if _dist_has_local_direct_url(dist_info) and normalize_name(name or "") not in conda_entries_by_name:
            candidate = _pick_conda_candidate_for_local_direct_url(
                manager=manager,
                dist_name=name,
                failed_import=failed_import,
                debug=debug,
            )
            if candidate and manager:
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "conda",
                        "name": candidate,
                        "import": failed_import,
                        "local_dist_name": name,
                        "reason": "installed from local file/path (direct_url=file://); conda candidate found via search, forcing relink",
                    }
                )
            else:
                plan.append(
                    {
                        "dist": dist_info.name,
                        "kind": "skip",
                        "name": None,
                        "import": failed_import,
                        "reason": "installed from local file/path (direct_url=file://) and no conda candidate found via search; skip auto-reinstall",
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
        from .recovery import successful_sandboxes

        passed = {normalize_name(name) for sandbox in successful_sandboxes(actions) for name in sandbox.get("targets", [])}
        items = [item for item in items if not (item.get("kind") == "defer" and
                 normalize_name(item.get("dist", "").removesuffix(".dist-info").rsplit("-", 1)[0]) in passed)]
        if not items:
            return
        print(tr('\nFix plan:'))
        for item in items:
            kind = item.get("kind") or "?"
            name = item.get("name")
            dist = item.get("dist") or "?"
            reason = tr(item["reason"]) if item.get("reason") else None
            uninstall = item.get("uninstall") or {}
            remove_name = uninstall.get("name")
            remove_kind = uninstall.get("kind")
            if kind == "remove" and remove_name and remove_kind:
                line = tr("  - {value0}: remove via {value1}: {value2}", value0=dist, value1=remove_kind, value2=remove_name)
                if reason:
                    line += f" [{reason}]"
            else:
                line = f"  - {dist}: {tr(kind)}"
                if name:
                    line += f" -> {name}"
                if reason:
                    line += f" [{reason}]"
                if remove_name and remove_kind:
                    line += tr(" (then remove via {value0}: {value1})", value0=remove_kind, value1=remove_name)
            print(line)

    _print_fix_plan(plan)

    ok_all = not any(p.get("kind") == "defer" for p in plan)

    # Explicit removals (no install dependency).
    explicit_conda_rm, explicit_pip_rm = _collect_explicit_removals(plan)
    if explicit_conda_rm:
        if is_conda and manager:
            refreshed = get_env_package_entries(env_path, manager, show_json_output=debug)
            present = {normalize_name((e or {}).get("name") or "") for e in refreshed if isinstance(e, dict)}
            conda_rm_present = [p for p in explicit_conda_rm if normalize_name(p) in present]
            if conda_rm_present:
                print(tr('\nRemoving {value0} package(s) via {value1} (remove-only)...', value0=len(conda_rm_present), value1=manager))
                print(tr('Conda remove targets:'), ", ".join(conda_rm_present))
                ok_rm = conda_remove(env_path, conda_rm_present, manager)
                ok_all = ok_all and ok_rm
                actions.append({"action": "conda_remove", "count": len(conda_rm_present), "ok": ok_rm, "packages": conda_rm_present})
        else:
            ok_all = False
            actions.append({"action": "error", "reason": "remove-only requested but no conda/mamba manager available"})
    if explicit_pip_rm and python_exe:
        print(tr('\nRemoving {value0} package(s) via pip (remove-only)...', value0=len(explicit_pip_rm)))
        print(tr('Pip remove targets:'), ", ".join(explicit_pip_rm))
        ok_rm = pip_uninstall(python_exe, explicit_pip_rm, use_uv=use_uv)
        ok_all = ok_all and ok_rm
        actions.append({"action": "pip_uninstall", "count": len(explicit_pip_rm), "ok": ok_rm, "packages": explicit_pip_rm})

    cleanup_items = [p for p in plan if p.get("kind") == "cleanup" and p.get("name")]
    for item in cleanup_items:
        if normalize_name(item.get("name") or "") != "jedi-common-dir":
            continue
        print(tr('\nCleaning up jedi/common shadowing (site-packages)...'))
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

            merged = (out or "") + "\n" + (err or "")
            if _is_python_pin_conflict(merged):
                print(tr('Solver hit pinned-python conflict; retrying without --force-reinstall to allow compatible upgrades...'))
                ok_upgrade, out_up, err_up = conda_install_capture(
                    env_path,
                    group,
                    manager,
                    configured_channels,
                    ignore_pinned=False,
                    force_reinstall=False,
                )
                if ok_upgrade:
                    return group, []
                merged = (out_up or "") + "\n" + (err_up or "")

            # If libmamba explicitly names offender specs, blacklist and retry without them.
            offenders = sorted({p for p in _extract_solver_offenders(merged) if p in set(group)})
            if offenders:
                for off in offenders:
                    _blacklist_add(blacklist, pyver=pyver, conda_name=off, reason="solver_incompatible")
                _save_verify_imports_blacklist(blacklist)
                remaining = [p for p in group if p not in set(offenders)]
                if len(remaining) >= 2:
                    print(tr('Solver failed; skipping {value0} incompatible package(s): {value1}', value0=len(offenders), value1=', '.join(offenders)))
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
        print(tr('\nRelinking {value0} replacement package(s) via {value1} (--force-reinstall)...', value0=len(pkgs), value1=manager))
        print(tr('Conda relink targets:'), ", ".join(pkgs))
        return conda_install(env_path, pkgs, manager, configured_channels, ignore_pinned=False, force_reinstall=True)

    conda_items = [p for p in plan if p["kind"] == "conda" and p["name"]]
    conda_failed_for_pip = []
    if conda_items and manager:
        pkgs = [p["name"] for p in conda_items]
        cleanup_names = sorted({p["pip_cleanup_before_relink"] for p in conda_items if p.get("pip_cleanup_before_relink")})
        if cleanup_names:
            print(tr('\nRemoving pip overlays before conda relink:'), ", ".join(cleanup_names))
            ok_clean = pip_uninstall(python_exe, cleanup_names, use_uv=use_uv)
            actions.append({"action": "pip_uninstall_conda_overlay", "packages": cleanup_names, "ok": ok_clean})
            # Always continue to the force-reinstall, including partial cleanup.
        stubborn = []
        for p in pkgs:
            if _preclean_module_roots_for_conda_package(p):
                stubborn.append(p)
        if stubborn:
            print(tr('\nPre-cleaning stale artifacts for {value0} stubborn package(s)...', value0=len(sorted(set(stubborn)))))
            print(tr('Pre-clean targets:'), ", ".join(sorted(set(stubborn))))
            pre = _preclean_stubborn_conda_artifacts(python_exe=python_exe, conda_packages=stubborn)
            actions.append({"action": "preclean_stubborn_artifacts", "ok": bool(pre.get("ok")), "removed": pre.get("removed", 0), "packages": sorted(set(stubborn))})

        hard_remove = sorted({p for p in pkgs if _requires_hard_remove_before_reinstall(p)})
        if hard_remove:
            refreshed = get_env_package_entries(env_path, manager, show_json_output=debug) if is_conda else []
            present = {normalize_name((e or {}).get("name") or "") for e in refreshed if isinstance(e, dict)}
            to_remove = [p for p in hard_remove if normalize_name(p) in present]
            if to_remove:
                print(tr('\nHard-remove before reinstall for {value0} package(s)...', value0=len(to_remove)))
                print(tr('Conda hard-remove targets:'), ", ".join(to_remove))
                ok_rm_hard = conda_remove(env_path, to_remove, manager)
                actions.append({"action": "conda_hard_remove_before_reinstall", "ok": ok_rm_hard, "packages": to_remove})
                ok_all = ok_all and ok_rm_hard

        print(tr('\nReinstalling {value0} packages via {value1} (batched)...', value0=len(pkgs), value1=manager))
        print(tr('Conda targets:'), ", ".join(sorted(set(pkgs))))
        ok, installed, failed = _install_conda_batched(pkgs)
        ok_all = ok_all and ok
        actions.append({"action": "conda_reinstall_batch", "count": len(pkgs), "ok": ok, "installed": installed, "failed": failed})
        # Relinking a conda package can leave files from an earlier local pip
        # installation behind. Retry once with pip's recorded-file cleanup,
        # then force-reinstall through conda to keep its package records valid.
        for item in conda_items:
            pkg = item["name"]
            local_name = item.get("local_dist_name")
            imp = item.get("import")
            if (
                not local_name
                or normalize_name(local_name) != normalize_name(pkg)
                or pkg not in installed
                or not imp
                or not imp.isidentifier()
            ):
                continue
            ok_imp, err_imp = check_import(imp, python_exe)
            actions.append({"action": "conda_local_recheck_import", "package": pkg, "import": imp, "ok": ok_imp, "error": err_imp})
            if ok_imp or _is_import_timeout_error(err_imp):
                continue
            if _failure_is_external(err_imp, imp):
                continue
            print(tr('\nImport still fails after relinking {value0}; cleaning local installation and reinstalling via {value1}...', value0=pkg, value1=manager))
            _print_import_error(err_imp)
            ok_clean = pip_uninstall(python_exe, [local_name], use_uv=use_uv)
            actions.append({"action": "pip_uninstall_before_conda_reinstall", "package": local_name, "ok": ok_clean})
            # Even a failed uninstall may have removed files: always restore.
            ok_restore = conda_install(
                env_path, [pkg], manager, configured_channels,
                ignore_pinned=False, force_reinstall=True,
            )
            actions.append({"action": "conda_clean_reinstall", "package": pkg, "ok": ok_restore})
            ok_final, err_final = check_import(imp, python_exe)
            actions.append({"action": "conda_clean_recheck_import", "package": pkg, "import": imp, "ok": ok_final, "error": err_final})
            ok_all = ok_all and ok_restore and ok_final
        conda_failed_for_pip = _collect_defaults_disabled_pip_fallback_candidates(
            conda_items=conda_items,
            failed_pkgs=failed,
        )

        # If the replacement was installed successfully, remove deprecated wrappers.
        conda_rm, pip_rm = _collect_wrapper_removals(plan, conda_installed=set(installed) | initially_installed)
        if conda_rm:
            refreshed = get_env_package_entries(env_path, manager, show_json_output=debug) if is_conda else []
            present = {normalize_name((e or {}).get("name") or "") for e in refreshed if isinstance(e, dict)}
            conda_rm_present = [p for p in conda_rm if normalize_name(p) in present]
            if conda_rm_present:
                print(tr('\nRemoving {value0} deprecated wrapper package(s) via {value1}...', value0=len(conda_rm_present), value1=manager))
                ok_rm = conda_remove(env_path, conda_rm_present, manager)
                ok_all = ok_all and ok_rm
                actions.append({"action": "conda_remove", "count": len(conda_rm_present), "ok": ok_rm, "packages": conda_rm_present})
                if ok_rm:
                    ok_relink = _relink_after_conda_remove(plan, installed_set=set(installed) | initially_installed)
                    ok_all = ok_all and ok_relink
                    actions.append({"action": "conda_relink", "ok": ok_relink})
        if pip_rm and python_exe:
            print(tr('\nRemoving {value0} deprecated wrapper package(s) via pip...', value0=len(pip_rm)))
            ok_rm = pip_uninstall(python_exe, pip_rm, use_uv=use_uv)
            ok_all = ok_all and ok_rm
            actions.append({"action": "pip_uninstall", "count": len(pip_rm), "ok": ok_rm, "packages": pip_rm})

    # Wrapper removals that don't require installing anything (replacement already present).
    conda_rm, pip_rm = _collect_wrapper_removals(plan, conda_installed=initially_installed)
    if conda_rm and manager:
        refreshed = get_env_package_entries(env_path, manager, show_json_output=debug) if is_conda else []
        present = {normalize_name((e or {}).get("name") or "") for e in refreshed if isinstance(e, dict)}
        conda_rm_present = [p for p in conda_rm if normalize_name(p) in present]
        if conda_rm_present:
            print(tr('\nRemoving {value0} deprecated wrapper package(s) via {value1}...', value0=len(conda_rm_present), value1=manager))
            ok_rm = conda_remove(env_path, conda_rm_present, manager)
            ok_all = ok_all and ok_rm
            actions.append({"action": "conda_remove", "count": len(conda_rm_present), "ok": ok_rm, "packages": conda_rm_present})
            if ok_rm:
                ok_relink = _relink_after_conda_remove(plan, installed_set=initially_installed)
                ok_all = ok_all and ok_relink
                actions.append({"action": "conda_relink", "ok": ok_relink})
    if pip_rm and python_exe:
        print(tr('\nRemoving {value0} deprecated wrapper package(s) via pip...', value0=len(pip_rm)))
        ok_rm = pip_uninstall(python_exe, pip_rm, use_uv=use_uv)
        ok_all = ok_all and ok_rm
        actions.append({"action": "pip_uninstall", "count": len(pip_rm), "ok": ok_rm, "packages": pip_rm})

    pip_items = [p for p in plan if p["kind"] in ("pip", "unknown") and p["name"]]
    if conda_failed_for_pip:
        existing = {normalize_name(p.get("name") or "") for p in pip_items}
        add = [p for p in conda_failed_for_pip if normalize_name(p.get("name") or "") not in existing]
        if add:
            print(
                tr('\nConda fallback: trying pip for {value0} package(s) that failed in defaults-disabled mode...', value0=len(add))
            )
            print(tr('Pip fallback targets:'), ", ".join(sorted(set(p["name"] for p in add if p.get("name")))))
            pip_items.extend(add)
    if pip_items:
        no_deps = bool(is_conda)
        why = tr(" (no-deps in conda env)") if no_deps else ""
        only_binary = os.name == "nt"
        ignore_installed = bool(is_conda)
        extra = (why + (tr(", wheels-only") if only_binary else "")) if (why or only_binary) else ""
        print(tr('\nReinstalling {value0} packages via pip{value1}...', value0=len(pip_items), value1=extra))
        print(tr('Pip targets:'), ", ".join(sorted(set(p['name'] for p in pip_items if p.get('name')))))
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
                use_uv=use_uv,
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

            # A failed import can come from a dependency or DLL. Preserve the
            # installed package and report failure rather than uninstalling it.
            if ok and imp and imp.isidentifier():
                ok_imp, err_imp = check_import(imp, python_exe)
                actions.append({"action": "pip_recheck_import", "package": pkg, "import": imp, "ok": ok_imp})
                if not ok_imp:
                    print(tr('    ! Import still fails after reinstall; keeping installed package: {value0}', value0=pkg))
                    _print_import_error(err_imp)
                    ok_all = False
                    actions.append(
                        {
                            "action": "pip_import_still_failing",
                            "package": pkg,
                            "import": imp,
                            "import_error": err_imp,
                            "ok": False,
                        }
                    )

    candidates = []
    for item in plan:
        if item.get("unfixable") != "pkg_resources" or item.get("installer") not in {"conda", "pip"}:
            continue
        if normalize_name(item.get("consumer") or "") in CRITICAL_PIP_PACKAGES:
            continue
        traceback_files = re.findall(r'File ["\']([^"\']+)["\']', str(item.get("import_error") or ""))
        if traceback_files:
            last_file = traceback_files[-1].replace("\\", "/")
            root = item.get("import") or ""
            if f"/{root}/" not in last_file and not last_file.endswith(f"/{root}.py"):
                continue
        candidates.append({"package": item["consumer"], "installer": item["installer"],
                           "error": item["import_error"], "dist_path": item["dist_path"]})
    if candidates:
        removed, removal_actions = remove_unfixable_consumers(
            candidates, env_path=env_path, python_exe=python_exe, manager=manager,
            force=remove_unfixable, interactive=interactive_removal, use_uv=use_uv,
            allow_dependents=False,
        )
        actions.extend(removal_actions)
        for item in plan:
            consumer = item.get("consumer")
            if consumer and normalize_name(consumer) in removed:
                item["kind"] = "removed"
        for failure in failures:
            dist_info = failure.get("dist_path")
            if not isinstance(dist_info, Path):
                continue
            _kind, name = _classify_dist(dist_info, conda_entries_by_name=conda_entries_by_name)
            if normalize_name(name or "") in removed:
                for item in plan:
                    if item.get("dist") == dist_info.name:
                        item["kind"] = "removed"
        if removed:
            ok_all = not any(item.get("kind") == "defer" for item in plan) and all(
                action.get("ok", True) for action in actions if action.get("action") not in {"legacy_consumer_upgrade"}
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
    import_like_suffixes = (".py", ".pyd", ".so", ".dylib")
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
            lowered = path.lower()
            if not (lowered.endswith(import_like_suffixes) or lowered.endswith("/__init__.py")):
                continue
            top = path.split("/")[0]
            # Ignore __pycache__
            if top == "__pycache__":
                continue
            # If it looks like a valid identifier, add it
            if top.isidentifier():
                top_levels.add(top)
        else:
            # File in root (e.g. 'six.py')
            lower = path.lower()
            if lower.endswith(import_like_suffixes):
                stem = path.split(".", 1)[0] if lower.endswith((".pyd", ".so", ".dylib")) else Path(path).stem
                if stem.isidentifier():
                    top_levels.add(stem)
                
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

def check_import(package_name, python_exe, *, timeout=30):
    """
    Run `python -c "import <name>"` via subprocess.
    Returns (ok: bool, error_message: str|None).
    """
    cmd = [python_exe, "-c", f"import {package_name}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode == 0:
            return True, None
        return False, proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, f"Import timed out (>{timeout}s)"
    except Exception as e:
        return False, str(e)

def _retry_import_timeouts(results, *, python_exe, env_path, manager, base_prefix, fix, quiet=False):
    retried = []
    actions = []
    for result in results:
        if not result.get("ok") and _is_import_timeout_error(result.get("error")):
            if not quiet:
                print(tr('Retrying timed-out import sequentially (90s): {value0}', value0=result['import']))
            ok, error = check_import(result["import"], python_exe, timeout=90)
            result = dict(result, ok=ok, error=error)
            actions.append({"action": "timeout_retry", "import": result["import"], "ok": ok, "error": error})
        retried.append(result)
    persistent = [result for result in retried if not result.get("ok") and _is_import_timeout_error(result.get("error"))]
    if not (persistent and fix and manager and is_conda_env(env_path)):
        return retried, actions
    entries = get_env_package_entries(env_path, manager, show_json_output=False)
    by_name = {normalize_name(entry.get("name") or ""): entry for entry in entries if isinstance(entry, dict)}
    packages = set()
    for result in persistent:
        dist = result.get("dist_path")
        if not isinstance(dist, Path):
            continue
        kind, name = _classify_dist(dist, conda_entries_by_name=by_name)
        if kind == "conda" and name and not _legacy_unmaintained_meta(name):
            packages.add(name)
    if not packages:
        return retried, actions
    channels = load_conda_channels(base_prefix=base_prefix, has_conda=which("conda"),
                                   has_mamba=which("mamba"), show_json_output=False)
    ok = conda_upgrade_dependencies(env_path, sorted(packages), manager, channels)
    actions.append({"action": "timeout_dependency_upgrade", "packages": sorted(packages), "ok": ok})
    if ok:
        for index, result in enumerate(retried):
            if not result.get("ok") and _is_import_timeout_error(result.get("error")):
                ok_import, error = check_import(result["import"], python_exe, timeout=90)
                retried[index] = dict(result, ok=ok_import, error=error)
                actions.append({"action": "timeout_upgrade_recheck", "import": result["import"], "ok": ok_import, "error": error})
    return retried, actions


def verify_imports(args):
    # `--debug` should show command lines, but avoid dumping huge `--json` payloads by default.
    show_json_output = False
    lang = "auto"
    
    all_envs, base_prefix, manager = discover_envs(show_json_output=show_json_output)
    # Support both styles:
    #   env-repair --env NAME verify-imports ...
    #   env-repair verify-imports --env NAME ...
    env_filters = _normalize_env_filters(getattr(args, "env_single", None)) or _normalize_env_filters(
        getattr(args, "env", None)
    )
    targets = select_envs(all_envs, env_filters, base_prefix)
    
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
    dist_info_dirs = 0
    for sp_path in site_pkgs:
        sp = Path(sp_path)
        if not sp.exists():
            continue
        dists = list(sp.glob("*.dist-info"))
        dist_info_dirs += len(dists)
        
        for d in dists:
            # Get import names
            imports = get_toplevel_imports(d)
            for imp in imports:
                if not getattr(args, "full", False):
                    if imp.lower() not in CRITICAL_PACKAGES:
                        continue
                if imp.startswith("_"):
                    continue
                
                # Skip invalid identifiers (e.g. ujson-stubs)
                if not imp.isidentifier():
                    continue
                    
                to_check.append((d.name, imp, sp))
    to_check = sorted(list(set(to_check)))

    if not args.json:
        # Make it explicit which env we are checking (helps diagnose user confusion).
        print(tr('Env: {value0}', value0=env_path))
        print(tr('Python: {value0}', value0=python_exe))
        if getattr(args, "debug", False):
            print("Site-packages:")
            for p in site_pkgs:
                print(f"  - {p}")
        print(tr('Distributions: {value0} (*.dist-info)', value0=dist_info_dirs))
        print(tr('Import targets: {value0}', value0=len(to_check)))
    
    if not to_check:
         # Fallback: if no critical packages found in lazy mode, warn user or check a few random ones?
         # Or just return empty report.
        if not getattr(args, "full", False):
            # Avoid requiring a translation entry for this experimental path.
            print(tr('No import candidates found in lazy mode.'))
        return {"ok": True, "exit_code": 0, "report": {"env": env_path, "checks": 0, "failures": []}}

    # Parallel execution
    max_workers = min(32, (os.cpu_count() or 1) * 4) 
    print(tr('Verifying {value0} imports using {value1} threads...', value0=len(to_check), value1=max_workers))
    
    progress = Progress(total=len(to_check), label=tr("Verifying imports"))
    try:
        results = _run_import_checks_parallel(
            to_check=to_check,
            python_exe=python_exe,
            max_workers=max_workers,
            progress=progress,
        )
    except KeyboardInterrupt:
        # Avoid ugly interpreter shutdown noise if user interrupts mid-flight.
        print(tr('\nInterrupted verify-imports; waiting for running checks to stop...'), file=sys.stderr)
        raise

    initial_timeouts = [r for r in results if (not r["ok"]) and _is_import_timeout_error(r.get("error"))]
    timeout_actions = []
    if initial_timeouts:
        results, timeout_actions = _retry_import_timeouts(
            results, python_exe=python_exe, env_path=env_path, manager=manager,
            base_prefix=base_prefix, fix=bool(getattr(args, "fix", False)), quiet=bool(args.json),
        )
    timeout_results = [r for r in results if (not r["ok"]) and _is_import_timeout_error(r.get("error"))]
    failures = [r for r in results if (not r["ok"]) and not _is_import_timeout_error(r.get("error"))]

    if not args.json:
        print(tr('\nImport Verification Report:'))
        print(tr('Checked {value0} imports in {value1}\n', value0=len(to_check), value1=env_path))
        if not failures and not timeout_results:
            print(tr('All checked imports succeeded!'))
        else:
            if failures:
                print(tr('Found {value0} broken imports:\n', value0=len(failures)))
                for f in failures:
                    print(tr('  [FAIL] {value0} (from {value1})', value0=f['import'], value1=f['dist']))
                    if f.get("error"):
                        _print_import_error(f["error"])
            if timeout_results:
                print(tr('\nIncomplete verification: {value0} import timeout(s) remain unresolved:', value0=len(timeout_results)))
                for t in timeout_results[:12]:
                    print(tr('  [TIMEOUT] {value0} (from {value1})', value0=t['import'], value1=t['dist']))

    fix_report = None
    if getattr(args, "fix", False) and failures:
        fix_report = attempt_fix(
            failures,
            python_exe,
            env_path,
            manager,
            base_prefix=base_prefix,
            debug=show_json_output,
            use_uv=getattr(args, "use_uv", False),
            remove_unfixable=bool(getattr(args, "remove_unfixable", False)),
            interactive_removal=not bool(args.json),
            interactive_qt=not bool(args.json),
        )

    post_failures = []
    if fix_report is not None:
        plan_items = fix_report.get("plan") or []
        plan_by_dist = {p.get("dist"): p for p in plan_items if isinstance(p, dict) and p.get("dist")}
        for f in failures:
            dist = f.get("dist")
            imp = f.get("import")
            p = plan_by_dist.get(dist) if dist else None
            if p and p.get("kind") in ("skip", "remove", "removed"):
                continue
            if not imp or not isinstance(imp, str) or not imp.isidentifier():
                post_failures.append(
                    {"dist": dist, "import": imp, "error": f.get("error") or "invalid import name"}
                )
                continue
            ok, err = check_import(imp, python_exe)
            if not ok:
                post_failures.append({"dist": dist, "import": imp, "error": err})
        from .recovery import successful_sandboxes

        if not args.json and not successful_sandboxes(fix_report.get("actions")):
            if post_failures:
                print(tr('\nPost-fix: {value0} import(s) still failing.', value0=len(post_failures)))
                for pf in post_failures[:12]:
                    dist = pf.get("dist") or "?"
                    imp = pf.get("import") or "?"
                    print(tr('  [FAIL] {value0} (from {value1})', value0=imp, value1=dist))
                    _print_import_error(pf.get("error"))
                # Last-chance fallback for stubborn imports in mixed envs:
                # try pip wheel reinstall for known problematic packages.
                fallback_targets = sorted(
                    {
                        _stubborn_import_pip_target(pf.get("import"))
                        for pf in post_failures
                        if _stubborn_import_pip_target(pf.get("import"))
                        and not _failure_is_external(pf.get("error"), pf.get("import"))
                    }
                )
                if fallback_targets:
                    print(tr('\nFallback: repairing stubborn imports via pip wheel reinstall...'))
                    print(tr('Pip fallback targets:'), ", ".join(fallback_targets))
                    fallback_actions = []
                    for pkg in fallback_targets:
                        ok_fb = pip_reinstall(
                            python_exe,
                            pkg,
                            # Keep fallback narrow: only relink the target wheel itself.
                            # Pulling full dependency trees in a mixed conda base often causes
                            # clobber/permission conflicts (e.g. charset_normalizer *.pyd).
                            no_deps=True,
                            only_binary=(os.name == "nt"),
                            ignore_installed=True,
                            use_uv=getattr(args, "use_uv", False),
                        )
                        fallback_actions.append({"action": "pip_fallback_reinstall", "package": pkg, "ok": ok_fb})
                    if isinstance(fix_report, dict):
                        fix_report.setdefault("actions", []).extend(fallback_actions)

                    # Re-check only currently failing imports.
                    still = []
                    for pf in post_failures:
                        imp = pf.get("import")
                        if not imp or not isinstance(imp, str) or not imp.isidentifier():
                            still.append(pf)
                            continue
                        ok_imp, err_imp = check_import(imp, python_exe)
                        if not ok_imp:
                            still.append({"dist": pf.get("dist"), "import": imp, "error": err_imp})
                    post_failures = still
                    if post_failures:
                        print(tr('\nPost-fallback: {value0} import(s) still failing.', value0=len(post_failures)))
                        for pf in post_failures[:12]:
                            dist = pf.get("dist") or "?"
                            imp = pf.get("import") or "?"
                            print(tr('  [FAIL] {value0} (from {value1})', value0=imp, value1=dist))
                            _print_import_error(pf.get("error"))
                    else:
                        print(tr('\nPost-fallback: all remaining imports OK.'))
            else:
                if timeout_results:
                    print(tr('\nPost-fix: no remaining non-skipped import errors; verification remains incomplete due to timeouts.'))
                else:
                    print(tr('\nPost-fix: all non-skipped imports OK.'))
    else:
        post_failures = [
            {"dist": f.get("dist"), "import": f.get("import"), "error": f.get("error")} for f in failures
        ]

    if fix_report is not None and post_failures:
        from .package_sandbox import recover_import_failures

        original_by_dist = {failure.get("dist"): failure for failure in failures}
        unresolved = [dict(failure, dist_path=original_by_dist.get(failure.get("dist"), {}).get("dist_path"))
                      for failure in post_failures]
        reverify = os.environ.get("ENV_REPAIR_REVERIFY") == "1"
        recovery = {"action": "automatic_package_sandbox", "reason": "no recursive migration during final verification", "ok": False}
        if not reverify:
            recovery = recover_import_failures(
                unresolved, env_path=env_path, python_exe=python_exe, manager=manager,
                channels=load_conda_channels(base_prefix=base_prefix, has_conda=which("conda"),
                                             has_mamba=which("mamba"), show_json_output=False),
                python_version=_python_major_minor(python_exe), actions=fix_report.get("actions") or [], quiet=bool(args.json),
            )
        fix_report.setdefault("actions", []).append(recovery)

        from .recovery import offer_installation, successful_sandboxes

        successes = [] if reverify else successful_sandboxes(fix_report["actions"])
        options = getattr(args, "recovery_options", None)
        if options is None:
            options = dict(vars(args), cmd="one-shot", critical_only=not bool(getattr(args, "full", False)))
        for sandbox in successes:
            offer = offer_installation(sandbox, options=options, interactive=not bool(args.json))
            fix_report["actions"].append(offer)
            if offer.get("approved"):
                return {"ok": False, "exit_code": 1, "pending_installation": offer["plan_path"],
                        "report": {"env": env_path, "python": python_exe, "fix": fix_report, "post_failures": post_failures}}

        if getattr(args, "remove_unfixable", False) or (not args.json and sys.stdin.isatty()):
            candidates = []
            for failure in unresolved:
                dist = failure.get("dist_path")
                if not isinstance(dist, Path) or not dist.exists():
                    continue
                name = _read_metadata_name(dist)
                if not name:
                    continue
                try:
                    from .package_sandbox import _inventory

                    inventory = _inventory(env_path, python_exe, targets=[name])
                    package = inventory["packages"][0]
                    kind = "conda" if package["kind"] == "conda" else "pip"
                    provider = name
                    if kind == "conda":
                        metadata_path = (dist / "METADATA").resolve()
                        provider = next(record["name"] for record in inventory["records"]
                                        if any((Path(env_path) / filename).resolve() == metadata_path for filename in record.get("files", [])))
                    candidates.append({"package": provider, "distribution": name, "installer": kind,
                                       "dist_path": str(dist), "error": failure.get("error")})
                except (OSError, ValueError, StopIteration) as exc:
                    fix_report["actions"].append({"action": "unfixable_removal_skipped", "dist": dist.name, "reason": str(exc)})
                    continue
            if candidates:
                removed, removal_actions = remove_unfixable_consumers(
                    candidates, env_path=env_path, python_exe=python_exe, manager=manager,
                    force=bool(getattr(args, "remove_unfixable", False)), interactive=not bool(args.json),
                    use_uv=bool(getattr(args, "use_uv", False)), allow_dependents=False,
                )
                fix_report["actions"].extend(removal_actions)
                removed_dists = {Path(item["dist_path"]).name for item in candidates if normalize_name(item["package"]) in removed}
                for dist in removed_dists:
                    fix_report.setdefault("plan", []).append({"dist": dist, "kind": "removed", "reason": "unfixable package safely removed with explicit permission"})
                post_failures = [failure for failure in post_failures if failure.get("dist") not in removed_dists]
                if removed_dists and not post_failures:
                    fix_report["ok"] = True

    ok_all = len(post_failures) == 0 and not timeout_results and (fix_report is None or bool(fix_report.get("ok")))

    report = {
        "complete": not timeout_results,
        "timeout_actions": timeout_actions,
        "initial_timeouts": [{"dist": result.get("dist"), "import": result.get("import"), "error": result.get("error")} for result in initial_timeouts],
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
        "timeouts": [
            {
                "dist": t.get("dist"),
                "import": t.get("import"),
                "error": t.get("error"),
            }
            for t in timeout_results
        ],
        "post_failures": post_failures,
        "fix": fix_report,
    }
    return {"ok": ok_all, "exit_code": 0 if ok_all else 1, "report": report}
