import sys
import json
import time
from pathlib import Path
from .conda_ops import (
    conda_install,
    conda_install_capture,
    get_env_package_entries,
)
from .conflicts import find_same_version_case_conflicts
from .discovery import which
from .i18n import t
from .naming import normalize_name, normalize_name_simple
from .pip_ops import pip_reinstall, pip_uninstall
from .progress import Progress
from .scan import (
    remove_dist_info_paths,
    remove_invalid_artifact,
)
from .search_parse import parse_search_output
from .subprocess_utils import run_json_cmd

# Manual override map for PyPI package names to conda(-forge) package names.
# Keys are normalized via `normalize_name()`.
PYPI_TO_CONDA_NAME_MAP = {
    # Concrete examples observed in this repo/workflow
    "msgpack": "msgpack-python",
    "build": "python-build",
    "ccxt": "ccxt-py",

    # Common alias/meta packages on PyPI
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",

    # Import-name used as package-name
    "skimage": "scikit-image",

    # Qt naming mismatch
    "pyqt5": "pyqt",

    # OpenCV wheels vs conda package
    "opencv-python": "opencv",
    "opencv-contrib-python": "opencv",
    "opencv-python-headless": "opencv",

    # Levenshtein naming mismatch (prefer the conda package name commonly used for adoption)
    "python-levenshtein": "levenshtein",
}

# Packages where a conda replacement makes the pip package obsolete even if versions differ.
# Keyed by normalized pip name -> conda replacement name.
FORCE_REMOVE_PIP_IF_CONDA_PRESENT = {
    "pysha3": "safe-pysha3",
}


def _pypi_to_conda_override(pip_name):
    key = normalize_name(pip_name or "")
    if not key:
        return None
    return PYPI_TO_CONDA_NAME_MAP.get(key)


def _adopt_pip_blacklist_path():
    return Path(".env_repair") / "adopt_pip_blacklist.json"


def _load_adopt_pip_blacklist():
    path = _adopt_pip_blacklist_path()
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
    # Normalize shape: blocked[pip_norm][version] = {...}
    out = {}
    for k, v in blocked.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        kn = normalize_name(k)
        if not kn:
            continue
        out.setdefault(kn, {})
        for ver, meta in v.items():
            if not isinstance(ver, str) or not ver:
                continue
            out[kn].setdefault(ver, meta if isinstance(meta, dict) else {})
    return out


def _save_adopt_pip_blacklist(blocked):
    path = _adopt_pip_blacklist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"blocked": blocked, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _blacklist_add(blocked, *, pip_name, pip_version, conda_name, reason):
    pn = normalize_name(pip_name)
    if not pn or not pip_version:
        return False
    blocked.setdefault(pn, {})
    blocked[pn].setdefault(
        pip_version,
        {
            "pip": pip_name,
            "version": pip_version,
            "conda": conda_name,
            "reason": reason,
            "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    return True


def _extract_incompatible_specs(output_text):
    """
    Extract likely conflicting package names from a libmamba solver error output.
    Returns a set of candidate names.
    """
    import re

    if not output_text:
        return set()
    names = set()
    for m in re.finditer(r"[├└]─\s*([A-Za-z0-9_.-]+)", output_text):
        names.add(m.group(1))
    return names


def _debug(enabled, event, payload):
    if not enabled:
        return
    print("[debug]", f"{event}:", payload)

def _fix_conda_meta_issues(env, manager, channels, ignore_pinned, force_reinstall, debug):
    """
    Repair broken conda-meta records by force-reinstalling the owning package.
    """
    if not manager:
        return []
    pkgs = []
    for issue in env.get("issues") or []:
        if issue.get("type") not in ("conda-meta-invalid-json", "conda-meta-missing-depends"):
            continue
        pkg = issue.get("package")
        if isinstance(pkg, str) and pkg:
            pkgs.append(pkg)
    pkgs = sorted(set(pkgs))
    if not pkgs:
        return []
    ok = conda_install(
        env["path"],
        pkgs,
        manager,
        channels,
        ignore_pinned=ignore_pinned,
        force_reinstall=True if force_reinstall else True,
    )
    _debug(debug, "conda_meta_reinstall", {"count": len(pkgs), "ok": ok})
    fixes = [
        {
            "fixed": ok,
            "method": "mamba/conda",
            "package": "conda-meta",
            "count": len(pkgs),
            "reason_key": "reason_conda_meta_reinstall",
        }
    ]
    if ok:
        env["issues"] = [
            i
            for i in env.get("issues") or []
            if i.get("type") not in ("conda-meta-invalid-json", "conda-meta-missing-depends")
        ]
    return fixes


def _remove_invalid_artifacts(env, debug):
    fixes = []
    for issue in list(env.get("issues") or []):
        if issue.get("type") != "invalid-artifact":
            continue
        ok = remove_invalid_artifact(issue.get("path") or "")
        fixes.append(
            {
                "fixed": ok,
                "method": "cleanup",
                "artifact": issue.get("name"),
                "reason_key": "reason_stale_artifact",
            }
        )
        if ok:
            env["issues"].remove(issue)
        _debug(debug, "invalid_artifact_cleanup", {"path": issue.get("path"), "ok": ok})
    return fixes


def _cleanup_duplicate_dist_info(env, debug):
    """
    For each duplicate-dist-info group: remove all dist-info directories (we rely on reinstall afterwards).
    """
    fixes = []
    for issue in list(env.get("issues") or []):
        if issue.get("type") != "duplicate-dist-info":
            continue
        ok = remove_dist_info_paths(issue.get("paths") or [])
        fixes.append(
            {
                "fixed": ok,
                "method": "cleanup",
                "package": issue.get("package"),
                "reason_key": "reason_duplicate_dist_info",
            }
        )
        if ok:
            env["issues"].remove(issue)
        _debug(debug, "duplicate_dist_info_cleanup", {"package": issue.get("package"), "ok": ok})
    return fixes


def _mamba_search_available(terms, channels, debug, *, show_json_output):
    if not terms:
        return set()
    if not which("mamba") and not which("conda"):
        return set()
    manager = "mamba" if which("mamba") else "conda"
    channel_args = []
    for ch in channels:
        channel_args.extend(["-c", ch])
    cmd = [manager, "search"] + list(terms) + channel_args + ["--json"]
    data = run_json_cmd(cmd, show_json_output=show_json_output)
    names = set(parse_search_output(data))
    _debug(debug, "mamba_search", {"terms": len(terms), "results": len(names)})
    return names


def _resolve_adopt_pip_target(*, pip_name, available):
    """
    Resolve a pip package name to the best matching conda package name from `mamba search`.

    Fast-path: exact match (case-sensitive, as returned by search parsing).
    Next: match by alnum-only normalization (ignores [-_.] separators).
    Fallbacks (conservative) for common conda naming:
      - <name>-python  (pip "msgpack" -> conda "msgpack-python")
      - <name>-py      (pip "ccxt" -> conda "ccxt-py")
      - python-<name>  (pip "build" -> conda "python-build")
      - safe-<name>    (pip "pysha3" -> conda "safe-pysha3")
    """
    pip_norm = normalize_name(pip_name)
    if not pip_norm:
        return None

    pip_simple = normalize_name_simple(pip_name)

    # Build a normalized lookup so we can match in a case-insensitive / separator-insensitive way.
    norm_to_name = {}
    simple_to_names = {}
    for name in available:
        if not isinstance(name, str) or not name:
            continue
        nn = normalize_name(name)
        if nn not in norm_to_name or len(name) < len(norm_to_name[nn]):
            norm_to_name[nn] = name
        sn = normalize_name_simple(name)
        if sn:
            simple_to_names.setdefault(sn, []).append(name)

    # Exact match (case-sensitive, as returned by search parsing).
    if pip_name in available:
        return pip_name

    # Exact alnum match (ignores separators like -, _, .).
    if pip_simple and pip_simple in simple_to_names:
        candidates = sorted(set(simple_to_names[pip_simple]), key=len)
        if candidates:
            return candidates[0]

    # Common conda naming alias: <name>-python
    alias_norm = pip_norm + "-python"
    if alias_norm in norm_to_name:
        return norm_to_name[alias_norm]

    # Common conda naming alias: <name>-py
    alias_norm_py = pip_norm + "-py"
    if alias_norm_py in norm_to_name:
        return norm_to_name[alias_norm_py]

    # Common conda naming alias: python-<name>
    alias_norm2 = "python-" + pip_norm
    if alias_norm2 in norm_to_name:
        return norm_to_name[alias_norm2]

    # Common conda naming alias: safe-<name>
    alias_norm3 = "safe-" + pip_norm
    if alias_norm3 in norm_to_name:
        return norm_to_name[alias_norm3]

    return None


def _adopt_pip_core_pattern(name):
    """
    Build a conservative wildcard pattern from a package name by replacing common separators with '*'.

    Examples:
      - "langchain-community" -> "langchain*community"
      - "langchain_community" -> "langchain*community"
      - "foo.bar" -> "foo*bar"
    """
    import re

    core = re.sub(r"[-_.]+", "*", str(name))
    core = re.sub(r"\*+", "*", core)
    core = core.strip("*")
    return core or str(name)


def _adopt_pip_uninstall_plan(*, pip_to_conda, pip_versions, entries):
    """
    Decide which pip packages are safe to uninstall after adopt-pip.

    Policy: only uninstall pip package if the mapped conda package exists and has the same version.
    This avoids removing pip when the mapping is only an alias (e.g. msgpack -> msgpack-python) but
    conda ended up with a different version.
    Returns (uninstallable_pip_names, skipped_details).
    """

    def conda_version_for(name):
        want = normalize_name(name)
        for item in entries or []:
            ch = (item.get("channel") or "").lower()
            if ch and ch != "pypi" and normalize_name(item.get("name") or "") == want:
                v = item.get("version")
                if isinstance(v, str):
                    return v
        return None

    uninstallable = []
    skipped = []
    for pip_name, conda_name in sorted((pip_to_conda or {}).items()):
        pv = pip_versions.get(pip_name)
        cv = conda_version_for(conda_name)
        if pv and cv and pv == cv:
            uninstallable.append(pip_name)
        else:
            skipped.append({"pip": pip_name, "pip_version": pv, "conda": conda_name, "conda_version": cv})
    return uninstallable, skipped


def _adopt_pip_force_uninstall_plan(*, pip_versions, entries):
    """
    Force-uninstall certain pip packages if a known conda replacement is present.

    Returns (pip_names_to_uninstall, conda_names_to_relink, details).
    """

    def conda_present(name):
        want = normalize_name(name)
        for item in entries or []:
            ch = (item.get("channel") or "").lower()
            if ch and ch != "pypi" and normalize_name(item.get("name") or "") == want:
                return True, item.get("version")
        return False, None

    uninstallable = []
    relink = []
    details = []
    for pip_name, pv in sorted((pip_versions or {}).items()):
        pn = normalize_name(pip_name)
        repl = FORCE_REMOVE_PIP_IF_CONDA_PRESENT.get(pn)
        if not repl:
            continue
        present, cv = conda_present(repl)
        if not present:
            continue
        uninstallable.append(pip_name)
        relink.append(repl)
        details.append({"pip": pip_name, "pip_version": pv, "conda": repl, "conda_version": cv})
    return sorted(set(uninstallable)), sorted(set(relink)), details


def _choose_reinstall_method(entries_index, pkg_norm, prefer, pip_fallback):
    """
    Decide installer for a normalized package based on conda list entries.
    Returns: ("conda"|"pip"|None, package_name_for_installer)
    """
    info = entries_index.get(pkg_norm) or {}
    channels = info.get("channels") or set()
    names_by_channel = info.get("names") or {}

    if prefer == "pip":
        if "pypi" in channels:
            pip_names = sorted(list(names_by_channel.get("pypi") or []), key=len)
            return "pip", pip_names[0] if pip_names else None
        if pip_fallback:
            # if unknown, try pip with normalized name
            return "pip", pkg_norm
        return None, None

    # conda preferred (auto/conda)
    non_pypi_channels = [c for c in channels if c and c != "pypi"]
    if non_pypi_channels:
        # take shortest name from any conda channel
        candidates = []
        for ch in non_pypi_channels:
            candidates.extend(list(names_by_channel.get(ch) or []))
        candidates = sorted(set(candidates), key=len)
        return "conda", candidates[0] if candidates else pkg_norm

    if "pypi" in channels:
        pip_names = sorted(list(names_by_channel.get("pypi") or []), key=len)
        return "pip", pip_names[0] if pip_names else pkg_norm

    return "conda", pkg_norm


def _build_channel_index(entries):
    index = {}
    for item in entries or []:
        name = item.get("name")
        channel = (item.get("channel") or "").lower()
        if not isinstance(name, str) or not channel:
            continue
        norm = normalize_name(name)
        info = index.setdefault(norm, {"channels": set(), "names": {}})
        info["channels"].add(channel)
        info["names"].setdefault(channel, set()).add(name)
    return index


def _conda_meta_record_paths(env_path, conda_pkg_name):
    root = Path(env_path) / "conda-meta"
    if not root.exists():
        return []
    prefix = f"{conda_pkg_name}-"
    paths = [p for p in root.glob("*.json") if p.name.startswith(prefix)]
    return sorted(paths, key=lambda p: p.name.lower())


def _conda_meta_owns_distinfo(env_path, *, conda_pkg_name, dist_name, version):
    if not (env_path and conda_pkg_name and dist_name and version):
        return False
    needle = f"Lib/site-packages/{dist_name}-{version}.dist-info/".lower()
    for meta_path in _conda_meta_record_paths(env_path, conda_pkg_name):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        files = data.get("files")
        if not isinstance(files, list):
            continue
        for f in files:
            if not isinstance(f, str):
                continue
            if f.replace("\\", "/").lower().startswith(needle):
                return True
    return False


def _apply_same_version_case_conflicts(env, entries, manager, channels, ignore_pinned, force_reinstall, debug):
    fixes = []
    python_exe = env.get("python")
    pip_items, conda_force = find_same_version_case_conflicts(entries)
    _debug(debug, "case_conflict_plan", {"pip": pip_items, "conda": conda_force})

    # Skip uninstall if the "pip" dist-info is actually owned by the conda package.
    env_path = env.get("path") or ""
    filtered = []
    skipped_owned = []
    for item in pip_items:
        pip_name = item.get("name")
        pip_version = item.get("version")
        conda_name = item.get("conda")
        if (
            isinstance(pip_name, str)
            and isinstance(pip_version, str)
            and isinstance(conda_name, str)
            and _conda_meta_owns_distinfo(env_path, conda_pkg_name=conda_name, dist_name=pip_name, version=pip_version)
        ):
            skipped_owned.append({"pip": pip_name, "version": pip_version, "conda": conda_name})
            continue
        filtered.append(item)
    if skipped_owned:
        _debug(debug, "case_conflict_skip_conda_owned", {"count": len(skipped_owned), "items": skipped_owned})

    pip_names = [i["name"] for i in filtered if isinstance(i.get("name"), str)]
    if pip_names and python_exe:
        ok = pip_uninstall(python_exe, pip_names)
        fixes.append(
            {
                "fixed": ok,
                "method": "pip-uninstall",
                "package": "case-conflicts",
                "reason_key": "reason_case_conflict_pip_uninstall",
            }
        )
        _debug(debug, "case_conflict_pip_uninstall", {"count": len(pip_names), "ok": ok})

    # Only relink conda packages for which we actually removed a pip duplicate.
    relink_conda = sorted({i.get("conda") for i in filtered if isinstance(i.get("conda"), str)})
    if relink_conda and manager:
        ok = conda_install(
            env["path"],
            relink_conda,
            manager,
            channels,
            ignore_pinned=ignore_pinned,
            force_reinstall=True,
        )
        fixes.append(
            {
                "fixed": ok,
                "method": "mamba/conda",
                "package": "case-conflicts",
                "reason_key": "reason_case_conflict_conda_relink",
            }
        )
        _debug(debug, "case_conflict_conda_reinstall", {"count": len(relink_conda), "ok": ok})
    return fixes


def _fix_duplicates(env, entries, manager, channels, ignore_pinned, force_reinstall, prefer, pip_fallback, debug, *, in_conda_env):
    fixes = []
    idx = _build_channel_index(entries)

    dup_pkgs = [i.get("package") for i in env.get("issues") or [] if i.get("type") == "duplicate-dist-info"]
    dup_pkgs = [p for p in dup_pkgs if isinstance(p, str)]
    for pkg_norm in dup_pkgs:
        method, name = _choose_reinstall_method(idx, pkg_norm, prefer, pip_fallback)
        if method == "pip":
            py = env.get("python")
            ok = bool(py) and pip_reinstall(
                py,
                name,
                no_deps=bool(in_conda_env),
                only_binary=os.name == "nt",
                ignore_installed=bool(in_conda_env),
            )
            fixes.append({"fixed": ok, "method": "pip", "package": pkg_norm, "reason_key": "reason_reinstall_duplicates"})
        else:
            ok = bool(manager) and conda_install(
                env["path"],
                [name],
                manager,
                channels,
                ignore_pinned=ignore_pinned,
                force_reinstall=force_reinstall,
            )
            fixes.append(
                {"fixed": ok, "method": "mamba/conda", "package": pkg_norm, "reason_key": "reason_reinstall_duplicates"}
            )
        _debug(debug, "duplicate_fix_attempt", {"package": pkg_norm, "method": method, "name": name})
    return fixes


def _adopt_pip(env, entries, manager, channels, ignore_pinned, force_reinstall, pip_uninstall_flag, debug, *, show_json_output, lang):
    if not manager:
        return []

    fixes = []
    idx = _build_channel_index(entries)
    pip_entries = [
        e
        for e in entries
        if (e.get("channel") or "").lower() == "pypi" and isinstance(e.get("name"), str) and isinstance(e.get("version"), str)
    ]
    if not pip_entries:
        return []

    blacklist = _load_adopt_pip_blacklist()

    # Only consider pip packages that are not already provided by conda under the same normalized name.
    adopt_candidates = []
    skipped_blacklist = []
    for e in pip_entries:
        pip_name = e["name"]
        pip_ver = e["version"]
        if blacklist.get(normalize_name(pip_name), {}).get(pip_ver):
            skipped_blacklist.append({"pip": pip_name, "pip_version": pip_ver})
            continue
        norm = normalize_name(e["name"])
        info = idx.get(norm) or {}
        conda_channels = [c for c in (info.get("channels") or set()) if c and c != "pypi"]
        if conda_channels:
            continue
        adopt_candidates.append(pip_name)

    for item in skipped_blacklist:
        fixes.append(
            {
                "fixed": True,
                "method": "skip",
                "package": "<pip-to-conda>",
                "reason_key": "adopt_pip_blacklisted",
                "reason_args": item,
            }
        )

    if not adopt_candidates:
        return []

    pip_to_core = {pip_name: _adopt_pip_core_pattern(pip_name) for pip_name in adopt_candidates}

    def _dedupe_keep_order(items):
        seen = set()
        out = []
        for x in items:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    # Pass 1: exact name + a single wildcard-core (separators replaced by '*').
    terms1 = []
    for pip_name in adopt_candidates:
        core = pip_to_core[pip_name]
        # If the wildcard-core already covers the original name (e.g. streamlit-chat -> streamlit*chat),
        # don't include the original name to keep the search term list smaller.
        if core and core != pip_name:
            terms1.append(core)
        else:
            terms1.append(pip_name)
        mapped = _pypi_to_conda_override(pip_name)
        if mapped:
            # Ensure mapped names are searchable in pass 1 so the availability set includes them.
            terms1.append(mapped)
    terms1 = _dedupe_keep_order(terms1)

    progress = Progress(total=2, label=t("adopt_search", lang=lang) + " 1/2")
    available1 = _mamba_search_available(terms1, channels, debug, show_json_output=show_json_output)
    progress.update(1)

    to_install = []
    pip_to_conda = {}
    pip_versions = {e["name"]: e["version"] for e in pip_entries}
    unresolved = []
    resolve_progress = Progress(total=len(adopt_candidates), label=t("adopt_resolve", lang=lang))
    done = 0
    for pip_name in adopt_candidates:
        resolved = _pypi_to_conda_override(pip_name) or _resolve_adopt_pip_target(pip_name=pip_name, available=available1)
        if resolved:
            pip_to_conda[pip_name] = resolved
            to_install.append(resolved)
        else:
            unresolved.append(pip_name)
        done += 1
        resolve_progress.update(done)
    resolve_progress.finish()

    # Pass 2: for unresolved only, broaden by wrapping with '*' on both ends.
    if unresolved:
        terms2 = _dedupe_keep_order([f"*{pip_to_core[p]}*" for p in unresolved])
        progress.label = t("adopt_search", lang=lang) + " 2/2"
        available2 = _mamba_search_available(terms2, channels, debug, show_json_output=show_json_output)
        progress.update(2)
        progress.finish()
        for pip_name in unresolved:
            resolved = _pypi_to_conda_override(pip_name) or _resolve_adopt_pip_target(pip_name=pip_name, available=available2)
            if resolved:
                pip_to_conda[pip_name] = resolved
                to_install.append(resolved)
    else:
        progress.update(2)
        progress.finish()

    if not to_install:
        return []

    pkgs = sorted(set(to_install))
    print(f"{t('step_adopt_pip', lang=lang)}: {len(pkgs)} conda target(s) via {manager}")
    print("Conda targets:", ", ".join(pkgs))

    # Large one-shot solves can be slow and produce little visible output in some terminals.
    # Batch installs to keep the SAT problem size manageable and show steady progress.
    batch_size = 30
    ok = True
    if len(pkgs) <= batch_size:
        ok, out, err = conda_install_capture(
            env["path"],
            pkgs,
            manager,
            channels,
            ignore_pinned=ignore_pinned,
            force_reinstall=force_reinstall,
        )
        if not ok:
            conflict_names = _extract_incompatible_specs((out or "") + "\n" + (err or ""))
            offenders = sorted({n for n in conflict_names if n in set(pkgs)})
            if offenders:
                for off in offenders:
                    # Find pip package(s) that mapped to this conda name.
                    for pip_pkg, conda_pkg in (pip_to_conda or {}).items():
                        if normalize_name(conda_pkg) == normalize_name(off):
                            pv = pip_versions.get(pip_pkg)
                            if pv:
                                _blacklist_add(
                                    blacklist,
                                    pip_name=pip_pkg,
                                    pip_version=pv,
                                    conda_name=off,
                                    reason="solver_incompatible",
                                )
                _save_adopt_pip_blacklist(blacklist)
                pkgs = [p for p in pkgs if p not in offenders]
                if pkgs:
                    print(f"{t('step_adopt_pip', lang=lang)}: retry without incompatible package(s): {', '.join(offenders)}")
                    ok, _out2, _err2 = conda_install_capture(
                        env["path"],
                        pkgs,
                        manager,
                        channels,
                        ignore_pinned=ignore_pinned,
                        force_reinstall=force_reinstall,
                    )
    else:
        batches = [pkgs[i : i + batch_size] for i in range(0, len(pkgs), batch_size)]
        for i, batch in enumerate(batches, 1):
            print(f"{t('step_adopt_pip', lang=lang)}: mamba/conda batch {i}/{len(batches)} ({len(batch)} pkgs)")
            batch_ok, out, err = conda_install_capture(
                env["path"],
                batch,
                manager,
                channels,
                ignore_pinned=ignore_pinned,
                force_reinstall=force_reinstall,
            )
            ok_batch = batch_ok
            if not ok_batch:
                conflict_names = _extract_incompatible_specs((out or "") + "\n" + (err or ""))
                offenders = sorted({n for n in conflict_names if n in set(batch)})
                if offenders:
                    for off in offenders:
                        for pip_pkg, conda_pkg in (pip_to_conda or {}).items():
                            if normalize_name(conda_pkg) == normalize_name(off):
                                pv = pip_versions.get(pip_pkg)
                                if pv:
                                    _blacklist_add(
                                        blacklist,
                                        pip_name=pip_pkg,
                                        pip_version=pv,
                                        conda_name=off,
                                        reason="solver_incompatible",
                                    )
                    _save_adopt_pip_blacklist(blacklist)
                    reduced = [p for p in batch if p not in offenders]
                    if reduced:
                        print(
                            f"{t('step_adopt_pip', lang=lang)}: retry batch {i}/{len(batches)} without incompatible package(s): {', '.join(offenders)}"
                        )
                        ok_batch2, _out2, _err2 = conda_install_capture(
                            env["path"],
                            reduced,
                            manager,
                            channels,
                            ignore_pinned=ignore_pinned,
                            force_reinstall=force_reinstall,
                        )
                        ok_batch = ok_batch2
            ok = ok and ok_batch
            if not ok_batch:
                break
    fixes.append(
        {
            "fixed": ok,
            "method": "mamba/conda",
            "package": "<pip-to-conda>",
            "count": len(to_install),
            "reason_key": "reason_adopt_conda_install",
        }
    )
    _debug(debug, "adopt_pip_conda_install", {"count": len(to_install), "ok": ok})

    if ok and pip_uninstall_flag and env.get("python"):
        # Uninstall pip names only if conda install succeeded.
        #
        # Note: If conda installed into paths previously owned by pip, a subsequent pip uninstall
        # could remove those paths (because pip removes files listed in its RECORD).
        # Mitigation: after pip uninstall, force-reinstall the conda package(s) again to relink files.
        # Refresh entries so we can compare versions after the conda install.
        refreshed = get_env_package_entries(env["path"], manager, show_json_output=show_json_output)

        uninstallable, skipped = _adopt_pip_uninstall_plan(
            pip_to_conda=pip_to_conda,
            pip_versions=pip_versions,
            entries=refreshed,
        )

        forced_uninstall, forced_relink, forced_details = _adopt_pip_force_uninstall_plan(
            pip_versions=pip_versions,
            entries=refreshed,
        )
        for item in forced_details:
            fixes.append(
                {
                    "fixed": True,
                    "method": "plan",
                    "package": "<pip-to-conda>",
                    "reason_key": "reason_adopt_pip_force_uninstall",
                    "reason_args": item,
                }
            )
        for item in skipped:
            fixes.append(
                {
                    "fixed": True,
                    "method": "skip",
                    "package": "<pip-to-conda>",
                    "reason_key": "reason_adopt_skip_keep",
                    "reason_args": {
                        "pip": item["pip"],
                        "pip_version": item["pip_version"],
                        "conda": item["conda"],
                        "conda_version": item["conda_version"],
                    },
                }
            )

        to_uninstall = sorted(set(uninstallable) | set(forced_uninstall))
        relink_targets = sorted(set(to_install) | set(forced_relink))

        if to_uninstall:
            ok2 = pip_uninstall(env["python"], to_uninstall)
            fixes.append(
                {
                    "fixed": ok2,
                    "method": "pip-uninstall",
                    "package": "<pip-to-conda>",
                    "count": len(to_uninstall),
                    "reason_key": "reason_adopt_pip_uninstall",
                }
            )
            _debug(debug, "adopt_pip_pip_uninstall", {"count": len(to_uninstall), "ok": ok2})

            # Relink after pip uninstall (same batching logic as above).
            ok3 = True
            # Use relink_targets, not just pkgs installed in this run, because forced-uninstalls
            # can affect already-present conda replacements.
            pkgs_relink = sorted(set(relink_targets))
            if len(pkgs_relink) <= batch_size:
                ok3 = conda_install(
                    env["path"],
                    pkgs_relink,
                    manager,
                    channels,
                    ignore_pinned=ignore_pinned,
                    force_reinstall=True,
                )
            else:
                batches = [pkgs_relink[i : i + batch_size] for i in range(0, len(pkgs_relink), batch_size)]
                for i, batch in enumerate(batches, 1):
                    print(f"{t('step_adopt_pip', lang=lang)}: relink batch {i}/{len(batches)} ({len(batch)} pkgs)")
                    ok_batch = conda_install(
                        env["path"],
                        batch,
                        manager,
                        channels,
                        ignore_pinned=ignore_pinned,
                        force_reinstall=True,
                    )
                    ok3 = ok3 and ok_batch
                    if not ok_batch:
                        break
            fixes.append(
                {
                    "fixed": ok3,
                    "method": "mamba/conda",
                    "package": "<pip-to-conda>",
                    "count": len(to_install),
                    "reason_key": "reason_adopt_conda_relink",
                }
            )
            _debug(debug, "adopt_pip_conda_relink", {"count": len(to_install), "ok": ok3})

    return fixes
