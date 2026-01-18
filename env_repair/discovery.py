import os
import shutil
from pathlib import Path

from .subprocess_utils import run_json_cmd


def which(cmd):
    return shutil.which(cmd) is not None


def which_path(cmd):
    return shutil.which(cmd)


def detect_managers():
    return {
        "conda": which_path("conda"),
        "mamba": which_path("mamba"),
        "micromamba": which_path("micromamba"),
    }


def add_env(envs, path):
    if not path:
        return
    p = Path(path)
    if p.exists():
        envs.add(str(p))


def discover_envs(*, show_json_output):
    envs = set()
    base_prefix = None
    manager = None

    add_env(envs, os.environ.get("CONDA_PREFIX"))

    for cmd in ("mamba", "conda"):
        if not which(cmd):
            continue
        info = run_json_cmd([cmd, "info", "--json"], show_json_output=show_json_output)
        if info and "envs" in info:
            for p in info["envs"]:
                add_env(envs, p)
        if info:
            base_prefix = info.get("base_prefix") or info.get("root_prefix") or base_prefix
            if manager is None:
                manager = cmd

    if which("micromamba"):
        info = run_json_cmd(["micromamba", "env", "list", "--json"], show_json_output=show_json_output)
        if info and "envs" in info:
            for p in info["envs"]:
                add_env(envs, p)
        if info:
            base_prefix = info.get("root_prefix") or base_prefix
            if manager is None:
                manager = "micromamba"

    return sorted(envs), base_prefix, manager


def get_python_exe(env_path):
    env = Path(env_path)
    if os.name == "nt":
        # conda: <prefix>\python.exe
        conda_py = env / "python.exe"
        if conda_py.exists():
            return str(conda_py)
        # venv/virtualenv: <prefix>\Scripts\python.exe
        venv_py = env / "Scripts" / "python.exe"
        if venv_py.exists():
            return str(venv_py)
    else:
        # conda/venv: <prefix>/bin/python
        py = env / "bin" / "python"
        if py.exists():
            return str(py)
    return None


def get_site_packages(python_exe):
    import json
    import subprocess

    code = "import json, site; print(json.dumps(site.getsitepackages()))"
    res = subprocess.run([python_exe, "-c", code], capture_output=True, text=True)
    if res.returncode != 0:
        return []
    try:
        data = json.loads(res.stdout.strip())
        return [str(Path(p)) for p in data if isinstance(p, str)]
    except ValueError:
        return []
