import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PYPROJECT = ROOT / "pyproject.toml"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _get_version(pyproject_text: str) -> str:
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', pyproject_text)
    if not m:
        raise RuntimeError("Could not find version in pyproject.toml")
    return m.group(1).strip()


def _set_version(pyproject_text: str, version: str) -> str:
    pat = re.compile(r'(?m)^(version\s*=\s*")([^"]+)(")\s*$')
    if not pat.search(pyproject_text):
        raise RuntimeError("Could not update version in pyproject.toml")
    return pat.sub(rf'\g<1>{version}\g<3>', pyproject_text, count=1)


def _bump_patch(version: str) -> str:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version)
    if not m:
        raise RuntimeError(f"Unsupported version format: {version!r} (expected X.Y.Z)")
    major, minor, patch = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return f"{major}.{minor}.{patch + 1}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bump patch version in pyproject.toml")
    ap.add_argument("--sync", action="store_true", help="Run tools/sync_versions.py after bump")
    ap.add_argument(
        "--staged-recipes",
        default="staged-recipes",
        help="Path to a conda-forge/staged-recipes checkout (used with --sync)",
    )
    args = ap.parse_args(argv)

    txt = _read_text(PYPROJECT)
    current = _get_version(txt)
    bumped = _bump_patch(current)
    _write_text(PYPROJECT, _set_version(txt, bumped))
    sys.stderr.write(f"Bumped version: {current} -> {bumped}\n")

    if args.sync:
        cmd = [
            sys.executable,
            str(ROOT / "tools" / "sync_versions.py"),
            "--pypi-sdist",
            "--staged-recipes",
            str(Path(args.staged_recipes)),
        ]
        sys.stderr.write("[cmd] " + subprocess.list2cmdline(cmd) + "\n")
        subprocess.check_call(cmd, cwd=str(ROOT))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
