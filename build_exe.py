import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _which(cmd):
    return shutil.which(cmd) is not None


def _repo_root():
    return Path(__file__).resolve().parent


def _run(cmd):
    print("+ " + " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=False).returncode


def _pick_conda_runner():
    if _which("mamba"):
        return "mamba"
    if _which("conda"):
        return "conda"
    return None


def _pick_icon(root):
    icons_dir = root / "icons"
    if not icons_dir.exists():
        return None
    # Preferred: explicit .ico for Windows (also acceptable for many other targets).
    ico = icons_dir / "env-repair.ico"
    if ico.exists():
        return ico
    # Fallback: a large PNG (best effort; PyInstaller support varies by platform).
    for size in ("512x512", "256x256", "128x128"):
        png = icons_dir / f"env-repair-{size}.png"
        if png.exists():
            return png
    return None


def build(*, conda_env, name):
    root = _repo_root()
    entry = root / "env_repair.py"
    if not entry.exists():
        raise SystemExit(f"Entry script not found: {entry}")

    dist = root / "dist"
    work = root / "build" / "pyinstaller"
    dist.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    icon = _pick_icon(root)

    pyinstaller_args = [
        "--onefile",
        "--noconfirm",
        "--clean",
        "--name",
        name,
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
    ]
    if icon:
        pyinstaller_args += ["--icon", str(icon)]
    pyinstaller_args += [
        str(entry),
    ]

    if conda_env:
        runner = _pick_conda_runner()
        if not runner:
            raise SystemExit("conda_env requested but neither `conda` nor `mamba` found in PATH.")
        cmd = [runner, "run", "-n", conda_env, "python", "-m", "PyInstaller"] + pyinstaller_args
    else:
        cmd = [sys.executable, "-m", "PyInstaller"] + pyinstaller_args

    # Ensure predictable cwd for generated files.
    old_cwd = os.getcwd()
    os.chdir(str(root))
    try:
        return _run(cmd)
    finally:
        os.chdir(old_cwd)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="build_exe.py")
    ap.add_argument("--conda-env", default=None, help="Build inside this conda env via `conda/mamba run -n ...`.")
    ap.add_argument("--name", default="env-repair", help="Executable name (default: env-repair).")
    args = ap.parse_args(argv)
    return build(conda_env=args.conda_env, name=args.name)


if __name__ == "__main__":
    raise SystemExit(main())
