import shutil
import subprocess
import sys
from pathlib import Path

from .subprocess_utils import run_cmd_capture, run_cmd_live, run_cmd_live_capture, run_json_cmd


def is_conda_env(env_path):
    return Path(env_path, "conda-meta").exists()


def ensure_mamba(*, base_prefix):
    import shutil

    if shutil.which("mamba"):
        return True
    if not shutil.which("conda") or not base_prefix:
        return False
    cmd = ["conda", "install", "-y", "-p", base_prefix, "mamba"]
    return subprocess.run(cmd, check=False).returncode == 0 and shutil.which("mamba")


def get_env_package_entries(env_path, manager, *, show_json_output):
    if manager == "micromamba":
        cmd = ["micromamba", "list", "-p", env_path, "--json"]
    elif manager == "mamba":
        cmd = ["mamba", "list", "-p", env_path, "--json"]
    else:
        cmd = ["conda", "list", "-p", env_path, "--json"]
    data = run_json_cmd(cmd, show_json_output=show_json_output)
    if not data or not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, dict)]


def conda_install(env_path, packages, manager, channels, *, ignore_pinned, force_reinstall):
    if not packages:
        return True
    channel_args = []
    for ch in channels or []:
        channel_args.extend(["-c", ch])
    pin_args = ["--no-pin"] if ignore_pinned else []
    force_args = ["--force-reinstall"] if force_reinstall else []
    if manager == "micromamba":
        cmd = ["micromamba", "install", "-y", "-p", env_path] + force_args + pin_args + channel_args + list(packages)
    elif manager == "mamba":
        cmd = ["mamba", "install", "-y", "-p", env_path] + force_args + pin_args + channel_args + list(packages)
    else:
        # Some conda versions do not support `--update-deps` (mamba does).
        cmd = ["conda", "install", "-y", "-p", env_path] + force_args + pin_args + channel_args + list(packages)
    return run_cmd_live(cmd) == 0


def conda_install_capture(env_path, packages, manager, channels, *, ignore_pinned, force_reinstall):
    """
    Like `conda_install` but returns (ok, stdout, stderr) while still streaming output live.
    """
    if not packages:
        return True, "", ""
    channel_args = []
    for ch in channels or []:
        channel_args.extend(["-c", ch])
    pin_args = ["--no-pin"] if ignore_pinned else []
    force_args = ["--force-reinstall"] if force_reinstall else []
    if manager == "micromamba":
        cmd = ["micromamba", "install", "-y", "-p", env_path] + force_args + pin_args + channel_args + list(packages)
    elif manager == "mamba":
        cmd = ["mamba", "install", "-y", "-p", env_path] + force_args + pin_args + channel_args + list(packages)
    else:
        cmd = ["conda", "install", "-y", "-p", env_path] + force_args + pin_args + channel_args + list(packages)
    rc, out, err = run_cmd_live_capture(cmd)
    return rc == 0, out, err


def conda_remove(env_path, packages, manager):
    if not packages:
        return True
    if manager == "micromamba":
        cmd = ["micromamba", "remove", "-y", "-p", env_path] + list(packages)
    elif manager == "mamba":
        cmd = ["mamba", "remove", "-y", "-p", env_path] + list(packages)
    else:
        cmd = ["conda", "remove", "-y", "-p", env_path] + list(packages)
    return run_cmd_live(cmd) == 0


def export_env_yaml(env_path, manager, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if manager == "micromamba":
        cmd = ["micromamba", "env", "export", "-p", env_path]
    elif manager == "mamba":
        cmd = ["mamba", "env", "export", "-p", env_path]
    elif manager == "conda":
        cmd = ["conda", "env", "export", "-p", env_path]
    else:
        return False
    with out_path.open("w", encoding="utf-8") as f:
        proc = subprocess.Popen(cmd, stdout=f, stderr=sys.stderr)
        return proc.wait() == 0


def env_update_from_yaml(env_path, manager, yaml_path):
    if manager == "micromamba":
        cmd = ["micromamba", "env", "update", "-p", env_path, "-f", str(yaml_path)]
    elif manager == "mamba":
        cmd = ["mamba", "env", "update", "-p", env_path, "-f", str(yaml_path)]
    elif manager == "conda":
        cmd = ["conda", "env", "update", "-p", env_path, "-f", str(yaml_path)]
    else:
        return False
    return run_cmd_live(cmd) == 0


def list_revisions(env_path):
    """
    Returns a sorted list of available revision numbers for the env.
    Uses `conda list --revisions` (text output) and parses the leading revision column.
    """
    import re

    if shutil.which("conda"):
        cmd = ["conda", "list", "--revisions", "-p", env_path]
    elif shutil.which("mamba"):
        cmd = ["mamba", "list", "--revisions", "-p", env_path]
    else:
        return []

    rc, out, _err = run_cmd_capture(cmd)
    if rc != 0 or not out:
        return []
    revs = []
    for line in out.splitlines():
        m = re.match(r"^\\s*(\\d+)\\s+", line)
        if m:
            try:
                revs.append(int(m.group(1)))
            except ValueError:
                pass
    return sorted(set(revs))


def rollback_to_revision(env_path, revision, *, dry_run):
    """
    Rollback an environment to a specific conda revision number.
    Returns bool.
    """
    runner = "conda" if shutil.which("conda") else ("mamba" if shutil.which("mamba") else None)
    if not runner:
        return False
    cmd = [runner, "install", "-y", "-p", env_path, "--revision", str(int(revision))]
    if dry_run:
        cmd.insert(2, "--dry-run")
    return run_cmd_live(cmd) == 0


def env_create_from_yaml(*, manager, src_yaml, target, target_is_path):
    if manager == "micromamba":
        # micromamba supports env create -f and -p
        if target_is_path:
            cmd = ["micromamba", "env", "create", "-f", str(src_yaml), "-p", str(target)]
        else:
            cmd = ["micromamba", "env", "create", "-f", str(src_yaml), "-n", str(target)]
    elif manager == "mamba":
        if target_is_path:
            cmd = ["mamba", "env", "create", "-f", str(src_yaml), "-p", str(target)]
        else:
            cmd = ["mamba", "env", "create", "-f", str(src_yaml), "-n", str(target)]
    elif manager == "conda":
        if target_is_path:
            cmd = ["conda", "env", "create", "-f", str(src_yaml), "-p", str(target)]
        else:
            cmd = ["conda", "env", "create", "-f", str(src_yaml), "-n", str(target)]
    else:
        return False
    return run_cmd_live(cmd) == 0


def dry_run_install(env_path, packages):
    runner = "conda" if shutil.which("conda") else ("mamba" if shutil.which("mamba") else None)
    if not runner:
        return 1, "", "no conda/mamba"
    cmd = [runner, "install", "-y", "-p", env_path, "--dry-run"] + list(packages)
    return run_cmd_capture(cmd)


def clean_index_cache(*, yes):
    runner = "conda" if shutil.which("conda") else ("mamba" if shutil.which("mamba") else None)
    if not runner:
        return False
    cmd = [runner, "clean", "--index-cache"]
    if yes:
        cmd.append("-y")
    return run_cmd_live(cmd) == 0


def conda_info_json(*, show_json_output):
    runner = "conda" if shutil.which("conda") else ("mamba" if shutil.which("mamba") else None)
    if not runner:
        return None
    return run_json_cmd([runner, "info", "--json"], show_json_output=show_json_output)


def extract_pkgs_dirs(info):
    if not isinstance(info, dict):
        return []
    pkgs_dirs = info.get("pkgs_dirs")
    if isinstance(pkgs_dirs, list):
        return [p for p in pkgs_dirs if isinstance(p, str)]
    return []


def clean_cache_level(level):
    """
    Returns list of conda/mamba clean args for a given level.
    """
    if level == "safe":
        return [["--index-cache"], ["--tempfiles"]]
    if level == "targeted":
        return [["--tarballs"]]
    if level == "aggressive":
        return [["--all"]]
    return []


def conda_clean(args, *, yes):
    runner = "conda" if shutil.which("conda") else ("mamba" if shutil.which("mamba") else None)
    if not runner:
        return False
    cmd = [runner, "clean"] + list(args)
    if yes:
        cmd.append("-y")
    return run_cmd_live(cmd) == 0
