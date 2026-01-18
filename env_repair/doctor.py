import json
import os
import time
import sys
from pathlib import Path

from .conda_config import ensure_default_channels, load_conda_channels, load_pinned_specs
from .conda_ops import (
    conda_install,
    env_update_from_yaml,
    env_create_from_yaml,
    dry_run_install,
    clean_index_cache,
    conda_clean,
    conda_info_json,
    extract_pkgs_dirs,
    clean_cache_level,
    export_env_yaml,
    get_env_package_entries,
    is_conda_env,
    list_revisions,
    rollback_to_revision,
)
from .conflicts import find_same_version_case_conflicts
from .discovery import detect_managers, discover_envs, get_python_exe, get_site_packages, which
from .i18n import t
from .naming import build_search_variants, normalize_name
from .pip_ops import pip_freeze, pip_install_requirements, pip_list_json, pip_reinstall, pip_uninstall
from .progress import Progress
from .scan import (
    remove_dist_info_paths,
    remove_invalid_artifact,
    scan_conda_meta_json,
    scan_dist_info,
    scan_invalid_artifacts,
    scan_pyd_duplicates,
)
from .search_parse import parse_search_output
from .subprocess_utils import OperationInterrupted, run_json_cmd
from .subprocess_utils import run_cmd_capture

from .clobber import build_conda_file_owner_map, extract_paths_from_text, to_relpath
from .inconsistent import parse_inconsistent

def _debug(enabled, event, payload):
    if not enabled:
        return
    print("[debug]", f"{event}:", payload)


def env_name_from_path(path):
    p = Path(path)
    if p.name:
        return p.name
    return str(path)


def select_envs(all_envs, targets, base_prefix):
    if not targets:
        return list(all_envs)

    out = []
    by_name = {Path(p).name.lower(): p for p in all_envs}
    for t in targets:
        if not t:
            continue
        # If user passed a path (relative or absolute), use it directly.
        tp = Path(t)
        if tp.exists():
            out.append(str(tp.resolve()))
            continue
        if os.path.isabs(t) or (":" in t and "\\" in t):
            out.append(t)
            continue
        if t.lower() == "base" and base_prefix:
            out.append(base_prefix)
            continue
        if t.lower() in by_name:
            out.append(by_name[t.lower()])
            continue
    # de-dupe while preserving order
    seen = set()
    dedup = []
    for p in out:
        if p in seen:
            continue
        seen.add(p)
        dedup.append(p)
    return dedup


def scan_env(env_path):
    env = {"path": env_path, "python": None, "issues": []}
    python_exe = get_python_exe(env_path)
    if not python_exe:
        env["issues"].append({"type": "missing-python"})
        return env

    env["python"] = python_exe
    site_pkgs = get_site_packages(python_exe)
    if not site_pkgs:
        env["issues"].append({"type": "missing-site-packages"})
        return env

    for site_pkg in site_pkgs:
        if not Path(site_pkg).exists():
            continue
        env["issues"].extend(scan_dist_info(site_pkg))
        env["issues"].extend(scan_pyd_duplicates(site_pkg))
        env["issues"].extend(scan_invalid_artifacts(site_pkg))

    if is_conda_env(env_path):
        env["issues"].extend(scan_conda_meta_json(env_path))

    return env


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


def _apply_same_version_case_conflicts(env, entries, manager, channels, ignore_pinned, force_reinstall, debug):
    fixes = []
    python_exe = env.get("python")
    pip_names, conda_force = find_same_version_case_conflicts(entries)
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
    if conda_force and manager:
        ok = conda_install(
            env["path"],
            conda_force,
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
        _debug(debug, "case_conflict_conda_reinstall", {"count": len(conda_force), "ok": ok})
    return fixes


def _fix_duplicates(env, entries, manager, channels, ignore_pinned, force_reinstall, prefer, pip_fallback, debug):
    fixes = []
    idx = _build_channel_index(entries)

    dup_pkgs = [i.get("package") for i in env.get("issues") or [] if i.get("type") == "duplicate-dist-info"]
    dup_pkgs = [p for p in dup_pkgs if isinstance(p, str)]
    for pkg_norm in dup_pkgs:
        method, name = _choose_reinstall_method(idx, pkg_norm, prefer, pip_fallback)
        if method == "pip":
            py = env.get("python")
            ok = bool(py) and pip_reinstall(py, name)
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

    # Only consider pip packages that are not already provided by conda under the same normalized name.
    adopt_candidates = []
    for e in pip_entries:
        norm = normalize_name(e["name"])
        info = idx.get(norm) or {}
        conda_channels = [c for c in (info.get("channels") or set()) if c and c != "pypi"]
        if conda_channels:
            continue
        adopt_candidates.append(e["name"])

    if not adopt_candidates:
        return []

    # Build search terms (exact + wildcard) for all variants.
    terms = []
    pip_to_variants = {}
    for pip_name in adopt_candidates:
        variants = build_search_variants(pip_name)
        pip_to_variants[pip_name] = variants
        for v in variants:
            terms.append(v)
            terms.append(v + "*")
    # De-dupe while keeping order.
    seen = set()
    terms = [t for t in terms if not (t in seen or seen.add(t))]

    progress = Progress(total=1, label=t("adopt_search", lang=lang) + " 1/1")
    available = _mamba_search_available(terms, channels, debug, show_json_output=show_json_output)
    progress.update(1)
    progress.finish()

    to_install = []
    pip_to_conda = {}
    pip_versions = {e["name"]: e["version"] for e in pip_entries}
    resolve_progress = Progress(total=len(adopt_candidates), label=t("adopt_resolve", lang=lang))
    done = 0
    for pip_name in adopt_candidates:
        resolved = None
        for v in pip_to_variants[pip_name]:
            if v in available:
                resolved = v
                break
        if resolved:
            pip_to_conda[pip_name] = resolved
            to_install.append(resolved)
        done += 1
        resolve_progress.update(done)
    resolve_progress.finish()

    if not to_install:
        return []

    ok = conda_install(
        env["path"],
        sorted(set(to_install)),
        manager,
        channels,
        ignore_pinned=ignore_pinned,
        force_reinstall=force_reinstall,
    )
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

        if uninstallable:
            ok2 = pip_uninstall(env["python"], uninstallable)
            fixes.append(
                {
                    "fixed": ok2,
                    "method": "pip-uninstall",
                    "package": "<pip-to-conda>",
                    "count": len(uninstallable),
                    "reason_key": "reason_adopt_pip_uninstall",
                }
            )
            _debug(debug, "adopt_pip_pip_uninstall", {"count": len(uninstallable), "ok": ok2})

            ok3 = conda_install(
                env["path"],
                sorted(set(to_install)),
                manager,
                channels,
                ignore_pinned=ignore_pinned,
                force_reinstall=True,
            )
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


def _print_fix_report(fixes, *, lang):
    if not fixes:
        print(t("fix_report", lang=lang))
        print(t("fix_report_none", lang=lang))
        return

    rows = []
    for f in fixes:
        action = f.get("method", "")
        item = f.get("package") or f.get("artifact") or ""
        if item == "<pip-to-conda>":
            item = "pip-to-conda({})".format(f.get("count", 0))
        result = t("fix_ok", lang=lang) if f.get("fixed") else t("fix_failed", lang=lang)
        reason_key = f.get("reason_key")
        if reason_key:
            reason = t(reason_key, lang=lang, **(f.get("reason_args") or {}))
        else:
            reason = ""
        rows.append((str(action), str(item), str(result), str(reason)))

    headers = (
        t("col_action", lang=lang),
        t("col_item", lang=lang),
        t("col_result", lang=lang),
        t("col_reason", lang=lang),
    )
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(r):
        return "  ".join(str(r[i]).ljust(widths[i]) for i in range(4))

    print(t("fix_report", lang=lang))
    print(fmt_row(headers))
    print(fmt_row(("-" * widths[0], "-" * widths[1], "-" * widths[2], "-" * widths[3])))
    for r in rows:
        print(fmt_row(r))


def _approve(*, yes, plan, prompt, lang):
    if plan:
        print(t("plan_only", lang=lang))
        return False
    if yes:
        return True
    if not sys.stdin.isatty():
        return False
    try:
        ans = input(prompt).strip().lower()
    except KeyboardInterrupt:
        ans = ""
    return ans in ("y", "yes", "j", "ja")


def rollback(args):
    """
    Rollback command handler: snapshot -> conda revision rollback -> rescan.
    """
    show_json_output = bool(getattr(args, "debug", False))
    lang = "auto"

    managers = detect_managers()
    has_any = any(managers.values())
    if not has_any:
        report = {"ok": False, "exit_code": 2, "error": t("manager_missing", lang=lang)}
        if args.json:
            return report
        print(t("manager_missing", lang=lang))
        return report

    all_envs, base_prefix, manager = discover_envs(show_json_output=show_json_output)
    targets = select_envs(all_envs, [args.env] if args.env else [], base_prefix)
    if not targets:
        report = {"ok": False, "exit_code": 2, "error": "no target env"}
        return report
    env_path = targets[0]

    revs = list_revisions(env_path)
    if not revs:
        report = {"ok": False, "exit_code": 2, "error": t("rollback_no_revisions", lang=lang)}
        if not args.json:
            print(t("rollback_no_revisions", lang=lang))
        return report

    current = max(revs)
    to = args.to
    if to in (None, "", "prev"):
        target = current - 1
    elif to == "latest":
        target = current
    else:
        try:
            target = int(to)
        except Exception:
            target = None
    if target is None or target not in revs:
        report = {"ok": False, "exit_code": 2, "error": t("rollback_invalid_target", lang=lang, to=to)}
        if not args.json:
            print(t("rollback_invalid_target", lang=lang, to=to))
        return report

    if not args.json:
        print(t("rollback_plan", lang=lang, from_rev=current, to_rev=target))
    if getattr(args, "plan", False):
        if not args.json:
            print(t("plan_only", lang=lang))
        return {"ok": True, "exit_code": 0, "planned": True}

    if not _approve(yes=bool(args.yes), plan=False, prompt=t("prompt_approve", lang=lang), lang=lang):
        if not args.json:
            print(t("abort", lang=lang))
        return {"ok": False, "exit_code": 2, "aborted": True}

    # Snapshot (reuse existing mechanism by forcing a snapshot path)
    ts = time.strftime("%Y%m%d-%H%M%S")
    snap = Path(".env_repair") / "snapshots" / f"{env_name_from_path(env_path)}-{ts}" / "env.yml"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap_ok = False
    if is_conda_env(env_path) and manager:
        snap_ok = export_env_yaml(env_path, manager, snap)

    ok = rollback_to_revision(env_path, target, dry_run=bool(args.dry_run))
    if not args.json:
        if ok:
            print(t("rollback_done", lang=lang, to_rev=target))
    post = scan_env(env_path)
    post["snapshot"] = {"path": str(snap), "ok": snap_ok, "type": "conda-yaml"}
    post["action"] = {"type": "rollback", "from": current, "to": target, "ok": ok}
    exit_code = 0 if ok else 2
    return {"ok": ok, "exit_code": exit_code, "report": [post]}


def rebuild(args):
    """
    Rebuild command handler: export -> create new env -> optional verify scan.
    """
    show_json_output = bool(getattr(args, "debug", False))
    lang = "auto"

    managers = detect_managers()
    has_any = any(managers.values())
    if not has_any:
        report = {"ok": False, "exit_code": 2, "error": t("manager_missing", lang=lang)}
        if not args.json:
            print(t("manager_missing", lang=lang))
        return report

    all_envs, base_prefix, manager = discover_envs(show_json_output=show_json_output)
    src_targets = select_envs(all_envs, [args.env] if args.env else [], base_prefix)
    if not src_targets:
        return {"ok": False, "exit_code": 2, "error": "no source env"}
    src = src_targets[0]

    to = args.to
    target_is_path = (":" in to) or ("\\" in to) or ("/" in to)
    dst = Path(to).resolve() if target_is_path else to

    # Existence check
    if target_is_path:
        if Path(dst).exists():
            msg = t("rebuild_target_exists", lang=lang, dst=str(dst))
            if not args.json:
                print(msg)
            return {"ok": False, "exit_code": 2, "error": msg}
    else:
        # name: avoid clobbering an existing env with same name
        existing = {Path(p).name.lower() for p in all_envs}
        if str(dst).lower() in existing:
            msg = t("rebuild_target_exists", lang=lang, dst=str(dst))
            if not args.json:
                print(msg)
            return {"ok": False, "exit_code": 2, "error": msg}

    if not args.json:
        print(t("rebuild_plan", lang=lang, src=src, dst=str(dst)))
        if getattr(args, "plan", False):
            print(t("plan_only", lang=lang))
            return {"ok": True, "exit_code": 0, "planned": True}

    if not _approve(yes=bool(args.yes), plan=False, prompt=t("prompt_approve", lang=lang), lang=lang):
        if not args.json:
            print(t("abort", lang=lang))
        return {"ok": False, "exit_code": 2, "aborted": True}

    ts = time.strftime("%Y%m%d-%H%M%S")
    snap = Path(".env_repair") / "snapshots" / f"{env_name_from_path(src)}-{ts}" / "env.yml"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap_ok = False
    if is_conda_env(src) and manager:
        snap_ok = export_env_yaml(src, manager, snap)

    created = env_create_from_yaml(manager=manager, src_yaml=snap, target=dst, target_is_path=target_is_path)

    result_report = {"path": str(dst), "python": None, "issues": []}
    if args.verify and created:
        # If name-based, discover target path and scan it.
        if not target_is_path:
            all2, base2, _mgr2 = discover_envs(show_json_output=show_json_output)
            tgt = select_envs(all2, [str(dst)], base2)
            if tgt:
                result_report = scan_env(tgt[0])
        else:
            result_report = scan_env(str(dst))

    out = {
        "ok": bool(created),
        "exit_code": 0 if created else 2,
        "report": [
            {
                "source": src,
                "snapshot": {"path": str(snap), "ok": snap_ok, "type": "conda-yaml"},
                "action": {"type": "rebuild", "to": str(dst), "ok": bool(created), "verify": bool(args.verify)},
                "result": result_report,
            }
        ],
    }
    if not args.json and created:
        print(t("rebuild_done", lang=lang, dst=str(dst)))
    return out


def diagnose_clobber(args):
    show_json_output = bool(getattr(args, "debug", False))
    lang = "auto"

    all_envs, base_prefix, _manager = discover_envs(show_json_output=show_json_output)
    targets = select_envs(all_envs, [args.env] if args.env else [], base_prefix)
    if not targets:
        return {"ok": False, "exit_code": 2, "error": "no target env"}
    env_path = targets[0]

    if not args.logfile:
        msg = t("clobber_no_log", lang=lang)
        if not args.json:
            print(t("clobber_header", lang=lang))
            print(msg)
        return {"ok": False, "exit_code": 2, "error": msg, "report": []}

    log_path = Path(args.logfile)
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        msg = t("clobber_log_read_failed", lang=lang, path=str(log_path))
        if not args.json:
            print(t("clobber_header", lang=lang))
            print(msg)
        return {"ok": False, "exit_code": 2, "error": msg, "report": []}

    paths = extract_paths_from_text(text, env_prefix=env_path)
    owners = build_conda_file_owner_map(env_path)

    conflicts = []
    for p in paths:
        rel = to_relpath(env_path, p)
        owner = owners.get(rel) if rel else None
        conflicts.append({"path": p, "relpath": rel, "conda_owner": owner})

    ok = bool(conflicts)
    if not args.json:
        print(t("clobber_header", lang=lang))
        if not conflicts:
            print(t("clobber_no_paths", lang=lang))

    return {"ok": ok, "exit_code": 0 if ok else 1, "report": [{"env": env_path, "conflicts": conflicts}]}


def diagnose_inconsistent(args):
    show_json_output = bool(getattr(args, "debug", False))
    lang = "auto"

    all_envs, base_prefix, _manager = discover_envs(show_json_output=show_json_output)
    targets = select_envs(all_envs, [args.env] if args.env else [], base_prefix)
    if not targets:
        return {"ok": False, "exit_code": 2, "error": "no target env"}
    env_path = targets[0]

    rc, out, err = dry_run_install(env_path, ["python"])
    inconsistent, pkgs = parse_inconsistent(out + "\n" + err)
    if not args.json:
        print(t("inconsistent_header", lang=lang))
        print(t("inconsistent_found", lang=lang) if inconsistent else t("inconsistent_not_found", lang=lang))
    return {
        "ok": True,
        "exit_code": 0,
        "report": [{"env": env_path, "dry_run_rc": rc, "inconsistent": inconsistent, "packages": pkgs}],
    }


def fix_inconsistent(args):
    show_json_output = bool(getattr(args, "debug", False))
    lang = "auto"

    all_envs, base_prefix, manager = discover_envs(show_json_output=show_json_output)
    targets = select_envs(all_envs, [args.env] if args.env else [], base_prefix)
    if not targets:
        return {"ok": False, "exit_code": 2, "error": "no target env"}
    env_path = targets[0]

    level = args.level
    if not args.json:
        print(t("inconsistent_fix_plan", lang=lang, level=level, env=env_path))
        if getattr(args, "plan", False):
            print(t("plan_only", lang=lang))
            return {"ok": True, "exit_code": 0, "planned": True}

    if not _approve(yes=bool(args.yes), plan=False, prompt=t("prompt_approve", lang=lang), lang=lang):
        if not args.json:
            print(t("abort", lang=lang))
        return {"ok": False, "exit_code": 2, "aborted": True}

    actions = []
    ok = True

    if level in ("safe", "normal"):
        ok = clean_index_cache(yes=True)
        actions.append({"type": "clean_index_cache", "ok": ok})
        if ok and level == "normal":
            # Best-effort: reinstall packages listed in warning, if any.
            rc, out, err = dry_run_install(env_path, ["python"])
            inconsistent, pkgs = parse_inconsistent(out + "\n" + err)
            if inconsistent and pkgs:
                ok2 = conda_install(
                    env_path,
                    pkgs,
                    manager,
                    [],
                    ignore_pinned=False,
                    force_reinstall=True,
                )
                actions.append({"type": "force_reinstall", "packages": pkgs, "ok": ok2})
                ok = ok and ok2
    elif level == "rebuild":
        # Non-invasive: suggest using rebuild.
        actions.append({"type": "suggest_rebuild", "ok": True})
        ok = True
    else:
        return {"ok": False, "exit_code": 2, "error": "invalid level"}

    post = scan_env(env_path)
    if not args.json:
        print(t("inconsistent_fix_done", lang=lang, level=level))
    return {"ok": ok, "exit_code": 0 if ok else 1, "report": [{"env": env_path, "actions": actions, "post": post}]}


def cache_check(args):
    show_json_output = bool(getattr(args, "debug", False))
    lang = "auto"
    info = conda_info_json(show_json_output=show_json_output)
    pkgs_dirs = extract_pkgs_dirs(info)
    if not args.json:
        print(t("cache_header", lang=lang))
        print(t("cache_pkgs_dirs", lang=lang, value=", ".join(pkgs_dirs) if pkgs_dirs else "-"))
    return {"ok": True, "exit_code": 0, "report": [{"pkgs_dirs": pkgs_dirs}]}


def cache_fix(args):
    show_json_output = bool(getattr(args, "debug", False))
    lang = "auto"
    level = args.level
    steps = clean_cache_level(level)
    if not steps:
        return {"ok": False, "exit_code": 2, "error": "invalid level"}

    if not args.json:
        print(t("cache_fix_plan", lang=lang, level=level))
        if getattr(args, "plan", False):
            print(t("plan_only", lang=lang))
            return {"ok": True, "exit_code": 0, "planned": True}

    if not _approve(yes=bool(args.yes), plan=False, prompt=t("prompt_approve", lang=lang), lang=lang):
        if not args.json:
            print(t("abort", lang=lang))
        return {"ok": False, "exit_code": 2, "aborted": True}

    actions = []
    ok = True
    for step in steps:
        step_ok = conda_clean(step, yes=True)
        actions.append({"type": "conda_clean", "args": step, "ok": step_ok})
        ok = ok and step_ok

    info = conda_info_json(show_json_output=show_json_output)
    pkgs_dirs = extract_pkgs_dirs(info)
    if not args.json:
        print(t("cache_fix_done", lang=lang, level=level))
    return {"ok": ok, "exit_code": 0 if ok else 1, "report": [{"level": level, "actions": actions, "pkgs_dirs": pkgs_dirs}]}


def diagnose_ssl(args):
    show_json_output = bool(getattr(args, "debug", False))
    lang = "auto"

    all_envs, base_prefix, manager = discover_envs(show_json_output=show_json_output)
    if getattr(args, "base", False):
        env_path = base_prefix
    else:
        targets = select_envs(all_envs, [args.env] if args.env else [], base_prefix)
        env_path = targets[0] if targets else None

    if not env_path:
        return {"ok": False, "exit_code": 2, "error": "no target env"}

    python_exe = get_python_exe(env_path)
    info = conda_info_json(show_json_output=show_json_output)

    ssl_out = ""
    ssl_err = ""
    ssl_rc = 1
    if python_exe:
        ssl_rc, ssl_out, ssl_err = run_cmd_capture([python_exe, "-c", "import ssl; print(ssl.OPENSSL_VERSION)"])
    ok = ssl_rc == 0
    if not args.json:
        print(t("ssl_header", lang=lang))
        if ok:
            print(t("ssl_ok", lang=lang, value=ssl_out.strip()))
        else:
            msg = ssl_err.strip() or ssl_out.strip() or str(ssl_rc)
            print(t("ssl_fail", lang=lang, value=msg))
            print(t("ssl_hint", lang=lang))

    return {
        "ok": True,
        "exit_code": 0,
        "report": [
            {
                "env": env_path,
                "python": python_exe,
                "ssl": {"ok": ok, "rc": ssl_rc, "stdout": ssl_out, "stderr": ssl_err},
                "conda_info": {"pkgs_dirs": extract_pkgs_dirs(info)},
                "manager": manager,
            }
        ],
    }


def run(args):
    show_json_output = bool(getattr(args, "debug", False))
    lang = "auto"

    managers = detect_managers()
    all_envs, base_prefix, manager = discover_envs(show_json_output=show_json_output)
    targets = select_envs(all_envs, args.env, base_prefix)

    pip_fallback = bool(args.pip_fallback)
    if args.no_pip_fallback:
        pip_fallback = False

    channels = []
    if not args.no_channels_from_condarc:
        channels.extend(
            load_conda_channels(base_prefix=base_prefix, has_conda=which("conda") or which("mamba"), show_json_output=show_json_output)
        )
    if args.channel:
        channels.extend(args.channel)
    if not args.no_default_channels:
        channels = ensure_default_channels(channels)

    report = []
    ok_all = True
    exit_code = 0

    env_progress = None
    if not args.json and targets:
        env_progress = Progress(total=len(targets), label=t("progress_envs", lang=lang))

    for env_idx, env_path in enumerate(targets, start=1):
        if env_progress:
            env_progress.update(env_idx)
        if not args.json:
            print(t("step_scan", lang=lang) + ": " + env_path)
        env_report = scan_env(env_path)
        env_report["managers"] = {
            "conda": {"found": bool(managers.get("conda")), "path": managers.get("conda")},
            "mamba": {"found": bool(managers.get("mamba")), "path": managers.get("mamba")},
            "micromamba": {"found": bool(managers.get("micromamba")), "path": managers.get("micromamba")},
        }
        env_report["channels"] = list(channels)
        env_report["pinned"] = load_pinned_specs(env_path)

        python_exe = env_report.get("python")
        conda_here = bool(manager) and is_conda_env(env_path)
        if conda_here:
            entries = get_env_package_entries(env_path, manager, show_json_output=show_json_output)
        else:
            entries = pip_list_json(python_exe) if python_exe else []
        env_report["initial_entries"] = entries

        snapshot = None
        if args.snapshot:
            snapshot = Path(args.snapshot)
        elif args.fix:
            # Always create a rescue snapshot before modifications.
            ts = time.strftime("%Y%m%d-%H%M%S")
            base = Path(".env_repair") / "snapshots"
            name = env_name_from_path(env_path)
            snapshot = base / f"{name}-{ts}" / ("env.yml" if conda_here else "requirements.txt")

        if snapshot:
            if conda_here and manager:
                if not args.json:
                    print(t("step_snapshot", lang=lang) + ": " + env_path)
                snap_ok = export_env_yaml(env_path, manager, snapshot)
                env_report["snapshot"] = {"path": str(snapshot), "ok": snap_ok, "type": "conda-yaml"}
            elif python_exe:
                if not args.json:
                    print(t("step_snapshot", lang=lang) + ": " + env_path)
                snap_ok = pip_freeze(python_exe, snapshot)
                env_report["snapshot"] = {"path": str(snapshot), "ok": snap_ok, "type": "pip-freeze"}
            else:
                env_report["snapshot"] = {"path": str(snapshot), "ok": False, "reason": "no-python"}

        if args.fix:
            fixes = []
            try:
                fixes.extend(_remove_invalid_artifacts(env_report, args.debug))
                if conda_here:
                    fixes.extend(
                        _fix_conda_meta_issues(
                            env_report,
                            manager,
                            channels,
                            args.ignore_pinned,
                            args.force_reinstall,
                            args.debug,
                        )
                    )
                fixes.extend(_cleanup_duplicate_dist_info(env_report, args.debug))
                fixes.extend(
                    _apply_same_version_case_conflicts(
                        env_report,
                        entries,
                        manager,
                        channels,
                        args.ignore_pinned,
                        args.force_reinstall,
                        args.debug,
                    )
                    )
                fixes.extend(
                    _fix_duplicates(
                        env_report,
                        entries,
                        manager if conda_here else None,
                        channels,
                        args.ignore_pinned,
                        args.force_reinstall,
                        args.prefer,
                        pip_fallback,
                        args.debug,
                    )
                )

                if args.adopt_pip and conda_here:
                    if not args.json:
                        print(t("step_adopt_pip", lang=lang) + ": " + env_path)
                    fixes.extend(
                        _adopt_pip(
                            env_report,
                            entries,
                            manager,
                            channels,
                            args.ignore_pinned,
                            args.force_reinstall,
                            not args.keep_pip,
                            args.debug,
                            show_json_output=show_json_output,
                            lang=lang,
                        )
                    )
            except OperationInterrupted as e:
                env_report["interrupted"] = {
                    "cmd": e.cmd,
                    "returncode": e.returncode,
                    "snapshot": env_report.get("snapshot"),
                }
                exit_code = max(exit_code, e.returncode)
                ok_all = False
                fixes.append({"fixed": False, "method": "interrupted", "package": "<operation>"})

                state_path = Path(".env_repair") / "state.json"
                state_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    state_path.write_text(
                        json.dumps(
                            {
                                "env_path": env_path,
                                "snapshot": env_report.get("snapshot"),
                                "cmd": e.cmd,
                                "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                except OSError:
                    pass

                if args.json or not sys.stdin.isatty():
                    env_report["fixes"] = fixes
                    report.append(env_report)
                    break
                try:
                    choice = input(t("prompt_interrupted", lang=lang)).strip().lower()
                except KeyboardInterrupt:
                    choice = "a"
                if choice == "r":
                    snap = env_report.get("snapshot") or {}
                    snap_path = snap.get("path")
                    restored = False
                    if snap_path and snap.get("ok"):
                        if conda_here and manager and snap.get("type") == "conda-yaml":
                            restored = env_update_from_yaml(env_path, manager, snap_path)
                        elif python_exe and snap.get("type") == "pip-freeze":
                            restored = pip_install_requirements(python_exe, snap_path)
                    fixes.append({"fixed": restored, "method": "restore", "package": "<snapshot>"})
                    if not restored:
                        exit_code = max(exit_code, 1)
                        break
                elif choice == "c":
                    pass
                else:
                    break

            env_report["fixes"] = fixes
            ok_all = ok_all and all(f.get("fixed") for f in fixes)

        report.append(env_report)

    if not args.json:
        if env_progress:
            env_progress.finish()
        for env in report:
            name = env_name_from_path(env["path"])
            print(t("env_header", lang=lang, name=name))
            print(t("path", lang=lang, value=env["path"]))
            print(t("python", lang=lang, value=env.get("python") or "missing"))
            mgrs = env.get("managers") or {}
            if mgrs:
                # Keep this short; details are in JSON output.
                missing = [k for k, v in mgrs.items() if not (v or {}).get("found")]
                if len(missing) == 3:
                    print(t("manager_missing", lang=lang))
            if env.get("channels"):
                print(t("channels", lang=lang, value=", ".join(env["channels"])))
            if env.get("snapshot"):
                s = env["snapshot"]
                status = t("snapshot_ok", lang=lang) if s.get("ok") else t("snapshot_failed", lang=lang)
                print(t("snapshot", lang=lang, path=s.get("path"), status=status))
            if not env.get("issues"):
                print(t("issues_none", lang=lang))
            else:
                print(t("issues", lang=lang))
                for issue in env["issues"]:
                    issue_type = issue.get("type")
                    if issue_type == "duplicate-dist-info":
                        print(
                            t(
                                "issue_duplicate_dist_info",
                                lang=lang,
                                package=issue.get("package"),
                                versions=issue.get("versions"),
                            )
                        )
                    elif issue_type == "duplicate-pyd":
                        print(t("issue_duplicate_pyd", lang=lang, base=issue.get("base"), files=issue.get("files")))
                    elif issue_type == "invalid-artifact":
                        print(t("issue_invalid_artifact", lang=lang, name=issue.get("name")))
                    else:
                        print(t("issue_generic", lang=lang, type=issue_type))
            if env.get("pinned"):
                print(t("pinned", lang=lang))
                for p in env["pinned"]:
                    print(" -", p)
            if args.fix:
                fixes = env.get("fixes", [])
                if not fixes:
                    print(t("fixes_none", lang=lang))
                else:
                    print(t("fixes", lang=lang))
                    for f in fixes:
                        method = f.get("method", "unknown")
                        label = f.get("package") or f.get("artifact") or "item"
                        if label == "<pip-to-conda>":
                            label = "pip-to-conda({})".format(f.get("count", 0))
                        status = t("fix_ok", lang=lang) if f.get("fixed") else t("fix_failed", lang=lang)
                        print(t("fix_line", lang=lang, label=label, method=method, status=status))
                    _print_fix_report(fixes, lang=lang)
            print("")

    if exit_code == 0 and not ok_all:
        exit_code = 1
    return {"ok": ok_all, "report": report, "exit_code": exit_code}
