import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _pypi_sdist_url(*, name: str, version: str) -> str:
    sdist_name = name.replace("-", "_")
    first = name[0]
    return f"https://files.pythonhosted.org/packages/source/{first}/{name}/{sdist_name}-{version}.tar.gz"


def _download_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:
        return resp.read()


def _sha256_pypi_sdist(*, name: str, version: str) -> tuple[str, str]:
    url = _pypi_sdist_url(name=name, version=version)
    data = _download_bytes(url)
    return url, hashlib.sha256(data).hexdigest()


def _project_version_from_pyproject(pyproject_text: str) -> str:
    # Minimal TOML parsing for this repo's pyproject shape.
    m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"\s*$', pyproject_text)
    if not m:
        raise RuntimeError("Could not find [project].version in pyproject.toml")
    return m.group(1).strip()


def _project_name_from_pyproject(pyproject_text: str) -> str:
    m = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"\s*$', pyproject_text)
    if not m:
        raise RuntimeError("Could not find [project].name in pyproject.toml")
    return m.group(1).strip()


def _replace_set_var(text: str, *, var: str, value: str) -> str:
    # Updates lines like: {% set version = "0.2.2" %}
    # Allow trailing comments/whitespace after the closing %}.
    pat = re.compile(
        rf'(?m)^(\s*\{{%\s*set\s+{re.escape(var)}\s*=\s*")([^"]*)("\s*%\}}\s*)(.*)$'
    )
    if not pat.search(text):
        raise RuntimeError(f'Could not find Jinja set for "{var}"')
    return pat.sub(rf"\g<1>{value}\g<3>\g<4>", text, count=1)


def _build_sdist(*, python_exe: str) -> Optional[Path]:
    # Uses `python -m build --sdist` if available. No network needed.
    dist_dir = ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    cmd = [python_exe, "-m", "build", "--sdist", "--outdir", str(dist_dir)]
    try:
        subprocess.check_call(cmd, cwd=str(ROOT))
    except Exception:
        return None
    # Pick newest .tar.gz in dist/
    candidates = sorted(dist_dir.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sync conda.recipe versions from pyproject.toml")
    ap.add_argument("--python", default=sys.executable, help="Python executable to use for optional sdist build")
    ap.add_argument("--build-sdist", action="store_true", help="Build an sdist in ./dist/ using python -m build")
    ap.add_argument("--sdist", help="Path to an existing sdist to hash (overrides --build-sdist)")
    ap.add_argument(
        "--pypi-sdist",
        action="store_true",
        help="Download the exact PyPI sdist for the current version and hash it (recommended for conda-forge).",
    )
    ap.add_argument(
        "--staged-recipes",
        help=(
            "Path to a conda-forge/staged-recipes checkout. "
            "If set, copy conda.recipe/meta-forge.yaml to "
            "staged-recipes/recipes/env-repair/meta.yaml after syncing."
        ),
    )
    args = ap.parse_args(argv)

    pyproject_path = ROOT / "pyproject.toml"
    pyproject_text = _read_text(pyproject_path)
    version = _project_version_from_pyproject(pyproject_text)
    name = _project_name_from_pyproject(pyproject_text)

    sdist_path = Path(args.sdist).resolve() if args.sdist else None
    sha256 = None
    sdist_desc = None
    if args.pypi_sdist:
        try:
            sdist_desc, sha256 = _sha256_pypi_sdist(name=name, version=version)
        except Exception:
            sha256 = None
            sdist_desc = None

    if not sha256 and not sdist_path and args.build_sdist:
        sdist_path = _build_sdist(python_exe=args.python)

    if not sha256 and sdist_path and sdist_path.exists():
        sha256 = _sha256_file(sdist_path)
        sdist_desc = str(sdist_path)

    meta_local = ROOT / "conda.recipe" / "meta.yaml"
    meta_forge = ROOT / "conda.recipe" / "meta-forge.yaml"

    meta_local_text = _read_text(meta_local)
    meta_forge_text = _read_text(meta_forge)

    meta_local_text = _replace_set_var(meta_local_text, var="version", value=version)
    meta_forge_text = _replace_set_var(meta_forge_text, var="version", value=version)
    meta_local_text = _replace_set_var(meta_local_text, var="name", value=name)
    meta_forge_text = _replace_set_var(meta_forge_text, var="name", value=name)

    if sha256:
        # Update `sha256: ...` (first occurrence only).
        meta_forge_text = re.sub(r"(?m)^(\s*sha256:\s*)([0-9a-f]{64})(\s*)$",
                                 rf"\g<1>{sha256}\g<3>",
                                 meta_forge_text,
                                 count=1)

    _write_text(meta_local, meta_local_text)
    _write_text(meta_forge, meta_forge_text)

    copied_to = None
    if args.staged_recipes:
        sr_root = Path(args.staged_recipes).resolve()
        target = sr_root / "recipes" / name / "meta.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(meta_forge), str(target))
        copied_to = target

    sys.stderr.write(f"Synced conda.recipe version to {version}\n")
    if sha256:
        sys.stderr.write(f"Updated meta-forge.yaml sha256 from {sdist_desc}\n")
    else:
        sys.stderr.write("Note: meta-forge.yaml sha256 not updated (no sdist provided/built)\n")
    if copied_to:
        sys.stderr.write(f"Copied meta-forge.yaml to {copied_to}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
