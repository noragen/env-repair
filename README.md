# 🩺 EnvRepair [![CI](https://github.com/noragen/env-repair/actions/workflows/ci.yml/badge.svg)](https://github.com/noragen/env-repair/actions/workflows/ci.yml)
### Fixing broken Python environments – safely, transparently, reproducibly

<p align="center">
  <img alt="EnvRepair icon" src="icons/env-repair-256x256.png" width="128" />
</p>

**EnvRepair** is a practical repair tool for the *messy reality* of Python environments:  
Conda / Mamba / Micromamba mixed with `pip`, plus plain `venv` / `virtualenv`.

Instead of starting over (again), EnvRepair helps you **understand what’s broken**, **why it’s broken**, and **fix it safely** – with snapshots and rollback support, so you’re never locked in.

---

## 🤕 Why EnvRepair Exists

If you’ve ever seen things like:

- duplicate `.dist-info` folders  
- mysterious `.pyd` conflicts on Windows  
- `conda-meta` JSON files that suddenly break tools like PyInstaller  
- environments that are *“inconsistent”* but still half-working  
- pip + conda silently stepping on each other’s toes  

…then EnvRepair is for you.

It doesn’t try to replace conda or pip.  
It steps in **after things already went wrong**.

---

## ✨ What EnvRepair Can Do

### 🔍 Diagnose
- Detect duplicates and leftovers (`.dist-info`, stale artifacts, some Windows `.pyd` duplicates).
- Find corrupted or incomplete `conda-meta` entries.
- Detect pip/conda case-sensitivity conflicts.

### 🛠️ Repair (carefully!)
- Repair mixed **conda + pip** installs using the *right* tool.
- Reinstall broken packages with source awareness.
- “Adopt” pip packages into conda where possible (`--adopt-pip`).
- Handle **plain venv / virtualenv** setups (pip-only mode).

### 🛟 Safety First
- Automatic **rescue snapshots** before any destructive action.
- Graceful recovery after `Ctrl+C`.
- Clear prompts instead of silent force-fixes.

---

## 🧰 Requirements
- Windows, Linux, or macOS.
- `mamba` in PATH for conda-style envs (preferred). `conda`/`micromamba` also work in many cases.
- Python available in the target environment.

---

## 🚀 Quick Start

> All examples below use Windows `cmd.exe` syntax (`.bat` blocks).  
> Adjust paths/shells as needed for Linux/macOS.

Basic scan (auto-discovers conda envs):
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

---

## 🔁 Common Workflows

Fix `base`:
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

Adopt pip packages into conda (where possible):
```bat
python env_repair.py --env base --fix --adopt-pip
```

Verify imports (and fix what can be fixed):
```bat
python env_repair.py verify-imports --env base --full --fix
```

---

## ⏪ Rollback & Rebuild

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
env-repair rebuild --env base --to C:\temp\base-rebuilt --verify
```

---

## 🧪 Advanced Diagnostics

Diagnose a `ClobberError` from a logfile:
```bat
env-repair diagnose-clobber --env base --logfile clobber.txt
```

Diagnose / fix “inconsistent” env:
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
python env_repair.py --env base --snapshot snapshots\base.yaml
```

Debug output:
```bat
python env_repair.py --env base --fix --adopt-pip --debug
```

---

## 🛟 Safety Net (Ctrl+C / Rescue)

- When you run with `--fix`, EnvRepair creates a rescue snapshot under:
  ```
  .env_repair\snapshots\...
  ```
- If you abort during a pip/mamba/conda step, EnvRepair will prompt:
  - `r` restore from snapshot
  - `c` continue (skip)
  - `a` abort (default)
- A `.env_repair\state.json` file records progress so you can inspect what happened and re-run later.

---

## 🔎 Import Verification (`verify-imports`)

EnvRepair can scan installed distributions and run `python -c "import <name>"` for their top-level modules.

Full scan + auto-fix (recommended when an env is “mostly working but randomly broken”):
```bat
python env_repair.py verify-imports --env base --full --fix --debug
```

Notes:
- Repairs use **batched** conda/mamba operations (no slow one-by-one reinstalls).
- If the solver fails for a specific package, EnvRepair retries the batch without the offending spec and remembers it in:
  - `.env_repair\verify_imports_blacklist.json`
- Platform-only modules are skipped (e.g. `sh` on Windows, `ptyprocess` missing `fcntl` on Windows).

---

## 🧑‍💻 Development

Run tests:
```bat
python -m unittest discover -s tests -p "test_*.py"
```

JSON output:
```bat
python env_repair.py --env base --json
```

---

## 📝 Notes

- `--adopt-pip` installs only mapped PyPI packages.
- `--adopt-pip` is conda-only; for plain venvs it is ignored.
- After successful adoption, env-repair uninstalls the pip version by default; use `--keep-pip` to skip.
- For alias-like mappings (e.g. pip `msgpack` → conda `msgpack-python`), pip is only removed if both versions match.
- Channels are loaded from `.condarc` first, then `defaults` and `anaconda` unless disabled.
- `--debug` prints the exact external command lines as `[cmd] ...` (mamba/conda/pip), and streams live output to keep long operations transparent.

---

## 📁 Files

- `env_repair.py` – CLI shim (kept for convenience).
- `env_repair/` – actual implementation.
- `docs/` – design notes, feature specs, and roadmaps.

---

## 📦 Releases

PyPI releases are automated via GitHub Actions (Trusted Publishing) on tags:
- `vX.Y.Z` → `.github/workflows/release-pypi.yml`

conda-forge publishing is done via a feedstock (standard conda-forge process). For details see:
- `docs/releasing.md`

Local conda recipe (for testing) lives in:
- `conda.recipe/`

---

## ❤️ Philosophy

EnvRepair is opinionated, but cautious.  
It prefers **understanding and repair** over brute-force reinstallation.

If you’ve ever said *“I’ll just recreate the environment…”*  
EnvRepair is here to save you that hour.
