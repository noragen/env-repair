import os
import re
from pathlib import Path

from .subprocess_utils import run_json_cmd


def load_conda_channels_from_condarc(*, base_prefix=None):
    paths = []
    env_condarc = os.environ.get("CONDARC")
    if env_condarc:
        paths.append(Path(env_condarc))
    paths.append(Path.home() / ".condarc")
    if base_prefix:
        paths.append(Path(base_prefix) / ".condarc")

    for p in paths:
        if not p.exists():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        in_channels = False
        channels = []
        for line in lines:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            if raw.startswith("channels:"):
                in_channels = True
                continue
            if in_channels:
                if raw.startswith("- "):
                    channels.append(raw[2:].strip())
                elif re.match(r"^[A-Za-z0-9_-]+\\s*:", raw):
                    break
        if channels:
            return channels
    return []


def load_conda_channels(*, base_prefix=None, has_conda, show_json_output):
    channels = load_conda_channels_from_condarc(base_prefix=base_prefix)
    if channels:
        return channels
    if has_conda:
        data = run_json_cmd(["conda", "config", "--show", "channels", "--json"], show_json_output=show_json_output)
        if data:
            channels = data.get("channels") or []
            return [c for c in channels if isinstance(c, str)]
    return []


def ensure_default_channels(channels):
    if "defaults" not in channels:
        channels.append("defaults")
    if "anaconda" not in channels:
        channels.append("anaconda")
    return channels


def load_pinned_specs(env_path):
    pinned = []
    pinned_file = Path(env_path, "conda-meta", "pinned")
    if pinned_file.exists():
        for line in pinned_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pinned.append(line)
    return sorted(set(pinned))

