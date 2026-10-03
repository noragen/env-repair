import json
import subprocess
from pathlib import Path

from .subprocess_utils import run_cmd_live
from .discovery import which_path

def _get_pip_cmd(python_exe, use_uv, base_args):
    uv_path = which_path("uv") if use_uv else None
    if uv_path and "--ignore-installed" not in base_args:
        mapped = []
        for arg in base_args:
            if arg == "--force-reinstall":
                mapped.append("--reinstall")
            elif arg == "-y" and base_args and base_args[0] == "uninstall":
                # uv pip uninstall doesn't expect -y
                pass
            else:
                mapped.append(arg)
        return [uv_path, "pip"] + mapped + ["--python", python_exe]
    return [python_exe, "-m", "pip"] + base_args


def pip_list_json(python_exe, *, use_uv=False):
    cmd = _get_pip_cmd(python_exe, use_uv, ["list", "--format=json"])
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


def pip_freeze(python_exe, out_path, *, use_uv=False):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = _get_pip_cmd(python_exe, use_uv, ["freeze"])
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return False
    try:
        out_path.write_text(res.stdout, encoding="utf-8")
        return True
    except OSError:
        return False


def pip_install_requirements(python_exe, req_path, *, use_uv=False):
    cmd = _get_pip_cmd(python_exe, use_uv, ["install", "-r", str(req_path)])
    return run_cmd_live(cmd) == 0


def pip_reinstall(python_exe, package, *, no_deps=False, only_binary=False, ignore_installed=False, use_uv=False):
    no_deps_args = ["--no-deps"] if no_deps else []
    only_bin_args = ["--only-binary=:all:"] if only_binary else []
    ignore_args = ["--ignore-installed"] if ignore_installed else []
    base_args = (
        ["install", "--upgrade", "--force-reinstall"]
        + no_deps_args
        + only_bin_args
        + ignore_args
        + [package]
    )
    cmd = _get_pip_cmd(python_exe, use_uv, base_args)
    return run_cmd_live(cmd) == 0


def pip_get_version(python_exe, package, *, use_uv=False):
    """
    Best-effort query of the installed version via pip show.
    Returns version string or None.
    """
    cmd = _get_pip_cmd(python_exe, use_uv, ["show", package])
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0 or not res.stdout:
        return None
    for line in res.stdout.splitlines():
        if line.lower().startswith("version:"):
            val = line.split(":", 1)[1].strip()
            return val or None
    return None


def pip_uninstall(python_exe, packages, *, use_uv=False):
    if not packages:
        return True
    cmd = _get_pip_cmd(python_exe, use_uv, ["uninstall", "-y"] + list(packages))
    return run_cmd_live(cmd) == 0
