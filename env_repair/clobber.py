import json
import re
from pathlib import Path
from pathlib import PureWindowsPath


def extract_paths_from_text(text, *, env_prefix):
    """
    Best-effort extraction of conflicting paths from conda/mamba error output.
    We only return paths that appear to be inside the given env prefix.
    """
    if not text:
        return []

    def _looks_like_windows_abs(p):
        # Accept a normal Windows path (C:\...) and also log-escaped variants (C:\\...),
        # since conda/mamba errors often include doubled backslashes.
        return bool(re.match(r"^[A-Za-z]:\\", p or ""))

    def _normalize_windows_abs(p):
        # Logs often contain escaped backslashes (C:\\Anaconda3\\...) – unescape them.
        p = (p or "").replace("\\\\", "\\")
        # Normalize separators and case-insensitive comparison via a forward-slash form.
        return str(PureWindowsPath(p).as_posix()).lower()

    prefix_is_win = _looks_like_windows_abs(env_prefix)
    prefix_cmp = _normalize_windows_abs(env_prefix) if prefix_is_win else str(Path(env_prefix).resolve())
    if prefix_is_win:
        # Ensure consistent prefix matching (avoid C:/A matching C:/AB).
        prefix_cmp = prefix_cmp.rstrip("/")

    # Common patterns include quoted paths, or lines containing "path:".
    candidates = set()

    # Quoted windows paths: 'C:\\...'
    for m in re.finditer(r"['\"]([A-Za-z]:\\[^'\"]+)['\"]", text):
        candidates.add(m.group(1))
    # Unquoted windows paths: C:\...\something
    for m in re.finditer(r"([A-Za-z]:\\[^\\s\\r\\n]+)", text):
        candidates.add(m.group(1))
    # POSIX paths (for completeness)
    for m in re.finditer(r"(/[^\\s\\r\\n]+)", text):
        candidates.add(m.group(1))

    inside = []
    for p in candidates:
        if prefix_is_win and _looks_like_windows_abs(p):
            cand_norm = _normalize_windows_abs(p)
            if cand_norm == prefix_cmp or cand_norm.startswith(prefix_cmp + "/"):
                inside.append(str(PureWindowsPath(p.replace("\\\\", "\\"))))
            continue

        try:
            rp = str(Path(p).resolve())
        except Exception:
            continue
        # Normalize for case-insensitive comparison on Windows prefixes that are not Windows-abs.
        if rp.lower().startswith(str(prefix_cmp).lower()):
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

