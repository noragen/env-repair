# env-repair

Scan and repair conda/mamba/micromamba environments with mixed conda/pip installs.

## What it does
- Detects duplicate `.dist-info` entries and stale artifacts.
- Reinstalls duplicates via mamba/conda or pip based on original source.
- Optional `--adopt-pip` to move pypi-only packages to conda packages.
- Can also scan/fix plain `venv`/`virtualenv` environments via pip-only mode (pass the env path).

## Requirements
- Windows, Linux, or macOS.
- `conda` or `mamba` or `micromamba` in PATH.
- Python available in the target environment.

## Usage
Basic scan:
```bat
python env_repair.py
```

Install as a CLI (editable):
```bat
pip install -e .
env-repair --help
```

Install from local checkout (non-editable):
```bat
pip install .
```

Fix base env:
```bat
python env_repair.py --env base --fix
```

Fix a plain venv (pip-only):
```bat
python env_repair.py --env .venv --fix
```

Same via installed CLI:
```bat
env-repair --env base --fix
```

Adopt pypi packages:
```bat
python env_repair.py --env base --fix --adopt-pip
```

Rollback to previous conda revision:
```bat
env-repair rollback --env base --to prev
```

Rollback without prompt:
```bat
env-repair rollback --env base --to prev -y
```

Rebuild into a new env (name):
```bat
env-repair rebuild --env base --to base-rebuilt --verify
```

Rebuild into a new env (path):
```bat
env-repair rebuild --env base --to C:\\temp\\base-rebuilt --verify
```

Diagnose a ClobberError from a logfile:
```bat
env-repair diagnose-clobber --env base --logfile clobber.txt
```

Diagnose / fix "inconsistent" env:
```bat
env-repair diagnose-inconsistent --env base
env-repair fix-inconsistent --env base --level safe
```

Cache check / fix:
```bat
env-repair cache-check
env-repair cache-fix --level safe
```

SSL diagnosis:
```bat
env-repair diagnose-ssl --base
env-repair diagnose-ssl --env base
```

Create a conda-style snapshot (YAML):
```bat
python env_repair.py --env base --snapshot snapshots\\base.yaml
```

Debug output:
```bat
python env_repair.py --env base --fix --adopt-pip --debug
```

Interrupted installs (Ctrl+C):
- When you run with `--fix`, env-repair creates a rescue snapshot under `.env_repair\\snapshots\\...` before changing anything.
- If you abort during a pip/conda/mamba step, env-repair will prompt:
  - `r` restore from snapshot
  - `c` continue (skip)
  - `a` abort (default)
- It also writes `.env_repair\\state.json` so you can inspect what happened and re-run env-repair afterwards.

Run tests:
```bat
python -m unittest discover -s tests -p "test_*.py"
```

Quiet JSON output from mamba/conda:
```bat
python env_repair.py --env base --fix --adopt-pip
```

## Notes
- `--adopt-pip` installs only mapped pypi packages (no full re-install).
- `--adopt-pip` is conda-only; for plain venvs it is ignored.
- After successful `--adopt-pip`, env-repair uninstalls the pip version by default; use `--keep-pip` to skip.
- For alias-like mappings (e.g. pip `msgpack` → conda `msgpack-python`), pip is only removed if both versions match.
- Channels are loaded from `.condarc` first, then `defaults` and `anaconda` are added unless disabled.

## Files
- `env_repair.py` CLI shim (kept for convenience)
- `latest.txt` scratch notes (not required by the tool)
