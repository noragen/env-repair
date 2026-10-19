import hashlib
import json
import shutil
from pathlib import Path

from .naming import normalize_name


def scan_conda_pip_overwrites(env_path, site_packages):
    """Find pip metadata that replaced a conda-owned Python distribution.

    A pip INSTALLER alone is normal in many conda builds. Require a changed
    version or a changed installed-file checksum as additional evidence.
    """
    prefix = Path(env_path).resolve()
    installed = {}
    for site_pkg in site_packages:
        for dist in Path(site_pkg).glob("*.dist-info"):
            try:
                dist.resolve().relative_to(prefix)
                if (dist / "INSTALLER").read_text(encoding="utf-8").strip().lower() != "pip":
                    continue
                name, version = dist.name[:-len(".dist-info")].rsplit("-", 1)
                installed.setdefault(normalize_name(name), []).append((dist, name, version))
            except (OSError, ValueError):
                continue

    issues = []
    for record_path in (prefix / "conda-meta").glob("*.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (not isinstance(record, dict) or not isinstance(record.get("name"), str)
                or not isinstance(record.get("version"), str)):
            continue
        paths_data = record.get("paths_data") or {}
        paths = paths_data.get("paths", []) if isinstance(paths_data, dict) else []
        if not isinstance(paths, list):
            paths = []
        files = record.get("files") or []
        if not isinstance(files, list):
            files = []
        owned = files + [p.get("_path") for p in paths if isinstance(p, dict)]
        owned_dists = set()
        for path in owned:
            if isinstance(path, str) and ".dist-info/" in path.replace("\\", "/"):
                parent = path.replace("\\", "/").split(".dist-info/", 1)[0] + ".dist-info"
                owned_dists.add(parent.lower())
        owned_names = set()
        for owned_dist in owned_dists:
            stem = owned_dist.rsplit("/", 1)[-1][:-len(".dist-info")]
            if "-" in stem:
                owned_names.add(normalize_name(stem.rsplit("-", 1)[0]))
        # The conda provider can have a different name than its Python dist,
        # e.g. matplotlib-base owns matplotlib-<version>.dist-info.
        candidates = [item for name in owned_names for item in installed.get(name, [])]
        for dist, pip_name, version in candidates:
            changed = []
            if version != str(record.get("version")):
                changed.append("version")
            for path in paths:
                if not isinstance(path, dict):
                    continue
                rel = path.get("_path")
                if not isinstance(rel, str):
                    continue
                target = prefix / rel.replace("\\", "/")
                try:
                    target.resolve().relative_to(prefix)
                    if target.parent.resolve() != dist.resolve():
                        continue
                    if target.name not in {"INSTALLER", "METADATA", "RECORD", "WHEEL"}:
                        continue
                    checksum = path.get("sha256_in_prefix")
                    if not checksum and not path.get("prefix_placeholder"):
                        checksum = path.get("sha256")
                    if checksum and hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
                        changed.append(rel)
                except (OSError, ValueError):
                    continue
            if changed:
                issues.append({
                    "type": "conda-pip-overwrite", "package": record["name"],
                    "name": pip_name, "version": version,
                    "conda_version": record.get("version"),
                    "dist_path": str(dist), "changed": changed,
                })
    return issues


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
