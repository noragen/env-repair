from .naming import normalize_name


def find_same_version_case_conflicts(entries):
    """
    Detect packages that exist in both conda and pypi channels with the same normalized name and same version.
    Returns (pip_names_to_uninstall, conda_names_to_force_reinstall).
    """
    by_norm = {}
    for item in entries:
        name = item.get("name")
        version = item.get("version")
        channel = (item.get("channel") or "").lower()
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        norm = normalize_name(name)
        by_norm.setdefault(norm, []).append((channel, name, version))

    pip_uninstall = set()
    conda_force = set()
    for _, items in by_norm.items():
        pip_items = [(n, v) for ch, n, v in items if ch == "pypi"]
        conda_items = [(n, v) for ch, n, v in items if ch != "pypi" and ch]
        if not pip_items or not conda_items:
            continue
        conda_versions = {v for _, v in conda_items}
        for pip_name, pip_version in pip_items:
            if pip_version in conda_versions:
                pip_uninstall.add(pip_name)
                conda_candidates = sorted([n for n, v in conda_items if v == pip_version], key=len)
                if conda_candidates:
                    conda_force.add(conda_candidates[0])
    return sorted(pip_uninstall), sorted(conda_force)

