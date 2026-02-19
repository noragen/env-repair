import json
import shutil
from pathlib import Path

from .naming import normalize_name


def scan_dist_info(site_pkg):
    issues = []
    dist_infos = []
    for p in Path(site_pkg).iterdir():
        if p.is_dir() and p.name.endswith(".dist-info"):
            dist_infos.append(p.name)

    versions = {}
    paths = {}
    for d in dist_infos:
        base = d[:-len(".dist-info")]
        if "-" not in base:
            continue
        name, version = base.rsplit("-", 1)
        key = normalize_name(name)
        versions.setdefault(key, set()).add(version)
        paths.setdefault(key, []).append(str(Path(site_pkg) / d))

    for pkg, vers in sorted(versions.items()):
        if len(vers) > 1:
            issues.append(
                {
                    "type": "duplicate-dist-info",
                    "package": pkg,
                    "versions": sorted(vers),
                    "paths": paths.get(pkg, []),
                }
            )
    return issues


def scan_pyd_duplicates(site_pkg):
    issues = []
    pyd_files = [p.name for p in Path(site_pkg).glob("*.pyd")]
    base_map = {}
    for name in pyd_files:
        stem = name[:-4]
        base = stem.split(".cp")[0]
        base_map.setdefault(base, []).append(name)
    for base, files in sorted(base_map.items()):
        if len(files) > 1:
            issues.append(
                {
                    "type": "duplicate-pyd",
                    "base": base,
                    "files": sorted(files),
                    "site_pkg": str(site_pkg),
                }
            )
    return issues


def scan_invalid_artifacts(site_pkg):
    issues = []
    root = Path(site_pkg)
    if not root.exists():
        return issues
    for p in root.iterdir():
        name = p.name
        if name.startswith("~") or name.endswith(".conda_trash"):
            issues.append({"type": "invalid-artifact", "path": str(p), "name": name})
    return issues


def remove_invalid_artifact(path):
    p = Path(path)
    if not p.exists():
        return True
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return True
    except OSError:
        return False


def remove_dist_info_paths(paths):
    ok = True
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError:
            ok = False
    return ok


def parse_conda_meta_filename(filename):
    """
    Parse conda-meta json filename like: <name>-<version>-<build>.json
    Returns (name, version, build) or (None, None, None) if not parseable.
    """
    name = Path(filename).name
    if not name.endswith(".json"):
        return None, None, None
    base = name[:-5]
    parts = base.rsplit("-", 2)
    if len(parts) != 3:
        return None, None, None
    pkg_name, version, build = parts
    if not pkg_name or not version or not build:
        return None, None, None
    return pkg_name, version, build


def scan_conda_meta_json(env_path):
    """
    Scan <env>/conda-meta/*.json for broken records.
    Current checks:
      - invalid JSON
      - missing required key: depends
    """
    issues = []
    root = Path(env_path) / "conda-meta"
    if not root.exists():
        return issues
    for p in root.glob("*.json"):
        pkg_name, version, build = parse_conda_meta_filename(p.name)
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="strict"))
        except Exception:
            issues.append(
                {
                    "type": "conda-meta-invalid-json",
                    "path": str(p),
                    "package": pkg_name,
                    "version": version,
                    "build": build,
                }
            )
            continue
        if not isinstance(data, dict):
            issues.append(
                {
                    "type": "conda-meta-invalid-json",
                    "path": str(p),
                    "package": pkg_name,
                    "version": version,
                    "build": build,
                }
            )
            continue
        if "depends" not in data:
            # Some helper/ABI records can legitimately omit `depends`.
            # Treat missing `depends` as broken when the record otherwise looks like a
            # real package metadata record (not just a minimal helper marker).
            noarch = data.get("noarch")
            is_platform_record = not bool(noarch)
            looks_real_record = any(
                k in data for k in ("subdir", "files", "url", "channel", "md5", "sha256", "build_number")
            )
            required = ("name", "version", "build")
            is_incomplete = not all(k in data for k in required)
            if is_incomplete or (is_platform_record and looks_real_record):
                issues.append(
                    {
                        "type": "conda-meta-missing-depends",
                        "path": str(p),
                        "package": pkg_name,
                        "version": version,
                        "build": build,
                    }
                )
    return issues
