import json
import subprocess
from pathlib import Path

from .subprocess_utils import run_cmd_live


def pip_list_json(python_exe):
    cmd = [python_exe, "-m", "pip", "list", "--format=json"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return []
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        version = item.get("version")
        if isinstance(name, str) and isinstance(version, str):
            out.append({"name": name, "version": version, "channel": "pypi"})
    return out


def pip_freeze(python_exe, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run([python_exe, "-m", "pip", "freeze"], capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return False
    try:
        out_path.write_text(res.stdout, encoding="utf-8")
        return True
    except OSError:
        return False


def pip_install_requirements(python_exe, req_path):
    cmd = [python_exe, "-m", "pip", "install", "-r", str(req_path)]
    return run_cmd_live(cmd) == 0


def pip_reinstall(python_exe, package, *, no_deps=False, only_binary=False, ignore_installed=False):
    no_deps_args = ["--no-deps"] if no_deps else []
    only_bin_args = ["--only-binary=:all:"] if only_binary else []
    ignore_args = ["--ignore-installed"] if ignore_installed else []
    cmd = (
        [python_exe, "-m", "pip", "install", "--upgrade", "--force-reinstall"]
        + no_deps_args
        + only_bin_args
        + ignore_args
        + [package]
    )
    return run_cmd_live(cmd) == 0


def pip_get_version(python_exe, package):
    """
    Best-effort query of the installed version via `pip show`.
    Returns version string or None.
    """
    cmd = [python_exe, "-m", "pip", "show", package]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0 or not res.stdout:
        return None
    for line in res.stdout.splitlines():
        if line.lower().startswith("version:"):
            val = line.split(":", 1)[1].strip()
            return val or None
    return None


def pip_uninstall(python_exe, packages):
    if not packages:
        return True
    cmd = [python_exe, "-m", "pip", "uninstall", "-y"] + list(packages)
    return run_cmd_live(cmd) == 0
