import json
import re
from pathlib import Path


def extract_paths_from_text(text, *, env_prefix):
    """
    Best-effort extraction of conflicting paths from conda/mamba error output.
    We only return paths that appear to be inside the given env prefix.
    """
    if not text:
        return []
    prefix = str(Path(env_prefix).resolve())
    # Normalize for case-insensitive comparison on Windows.
    prefix_cmp = prefix.lower()

    # Common patterns include quoted paths, or lines containing "path:".
    candidates = set()

    # Quoted windows paths: 'C:\\...'
    for m in re.finditer(r"['\"]([A-Za-z]:\\\\[^'\"]+)['\"]", text):
        candidates.add(m.group(1))
    # Unquoted windows paths: C:\...\something
    for m in re.finditer(r"([A-Za-z]:\\\\[^\\s\\r\\n]+)", text):
        candidates.add(m.group(1))
    # POSIX paths (for completeness)
    for m in re.finditer(r"(/[^\\s\\r\\n]+)", text):
        candidates.add(m.group(1))

    inside = []
    for p in candidates:
        try:
            rp = str(Path(p).resolve())
        except Exception:
            continue
        if rp.lower().startswith(prefix_cmp):
            inside.append(rp)
    return sorted(set(inside))


def build_conda_file_owner_map(env_prefix):
    """
    Map relative file paths (as stored in conda-meta JSON 'files') to package records.
    Returns dict[relpath] = {"name": ..., "version": ..., "build": ..., "record": ...}
    """
    owners = {}
    root = Path(env_prefix) / "conda-meta"
    if not root.exists():
        return owners

    for p in root.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        files = data.get("files")
        if not isinstance(files, list):
            continue
        name = data.get("name")
        version = data.get("version")
        build = data.get("build") or data.get("build_string")
        for f in files:
            if not isinstance(f, str) or not f:
                continue
            rel = f.replace("\\", "/").lstrip("/")
            owners.setdefault(
                rel,
                {
                    "name": name,
                    "version": version,
                    "build": build,
                    "record": p.name,
                },
            )
    return owners


def to_relpath(env_prefix, abs_path):
    try:
        rel = str(Path(abs_path).resolve().relative_to(Path(env_prefix).resolve()))
    except Exception:
        return None
    return rel.replace("\\", "/")

