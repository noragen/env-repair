# Changelog (env-repair)

## Unreleased

## 0.2.7 (release preparation)
- Completed optional `--use-uv` support for scan/fix, one-shot, import repairs, adoption and snapshot restoration; retain pip fallback when uv is missing or `--ignore-installed` is needed.
- CLI: preserve `--use-uv` when supplied before or after the `one-shot` and `verify-imports` subcommands, and target the selected environment's Python interpreter.
- Verify-imports: remove legacy `nose` installations when imports fail because the removed `imp` module is required, rather than repeatedly reinstalling the broken package.
- Verify-imports: attempt to restore missing `pkg_resources` by installing setuptools via pip when the target Python is available; retain a conda fallback when no Python executable is available.
- Conda-forge: migrate the recipe template to the v1 format with an embedded build command, declared console entry point, Python 3.9/current-Python import tests, `pip check`, and a CLI smoke test.
- Conda-forge: use the build-provided Python executable explicitly (`%PYTHON%` on Windows, `${PYTHON}` on Unix); replace the staged recipe's custom build scripts and Windows batch launcher with generated entry points.
- Release tooling: synchronize v1 context variables and copy the conda-forge template as `staged-recipes/recipes/env-repair/recipe.yaml`; update regression tests and packaging documentation.
- Validation: 89 unit tests passed. The Windows package build and CLI smoke test passed; Python 3.9/3.14 imports passed. The local Python 3.14 `pip check` needs user-site isolation (`python -s -m pip check`) because unrelated user-installed packages contaminate the test environment.

## Earlier development history
- Verify-imports: import timeouts are now reported separately (`[TIMEOUT]`) and no longer treated as hard failures for fix planning/retry loops.
- Verify-imports: local `direct_url=file://...` distributions are no longer auto-skipped by default; env-repair now probes `mamba/conda search --json` and force-reinstalls via conda when a managed candidate exists.
- Verify-imports: report JSON now includes a dedicated `timeouts` field.
- Scan UX: fixed progress/header formatting so `Scan:` starts on a new line after `Envs ... ETA ...`.
- Initial project extraction and baseline docs.
- Docs: prefer `mamba` in command examples (keep `conda` as fallback where needed).
- Core env scan and repair workflow.
- Adopt-pip flow with conda mapping and fallback checks.
- Adopt-pip: reduced `mamba search` combinatorics (2-pass search) and added explicit PyPI→conda name overrides (e.g. `msgpack`→`msgpack-python`, `ccxt`→`ccxt-py`).
- Adopt-pip: `build` is now explicitly ignored for pip→conda adoption (`build` != `python-build`).
- Adopt-pip: solver-fail handling now skips offending packages, retries the batch, and remembers failures in `.env_repair/adopt_pip_blacklist.json`.
- Verify-imports: `verify-imports --full --fix` with dependency-first repair stage, batched conda reinstalls, and solver-offender skipping + persistent blacklist in `.env_repair/verify_imports_blacklist.json`.
- Verify-imports: better handling of “unfixable” cases (platform-only imports like `sh`/`ptyprocess`, crashing imports like `PySimpleGUI`, obsolete packages like `idna_ssl`).
- Verify-imports: fixed env selection for `--env` before/after subcommand (`env-repair --env X verify-imports ...` and `env-repair verify-imports --env X ...`).
- Verify-imports: cleaner Ctrl+C handling during parallel import checks.
- Fix loops: avoid pointless pip-uninstall+conda-reinstall for conda-owned dist-info (e.g. `PyDrive`/`pydrive` case-conflicts).
- Debug output and progress indicators.
- Debug: show exact `mamba/conda/...` command lines (`[cmd] ...`) and stream stdout/stderr live for transparency.
- Support plain `venv`/`virtualenv` envs via `--env <path>` (pip-only scan/fix).
- Mamba-only support: channel loading now falls back to `mamba config list --json` when `conda` is not installed.
- Mamba-only support: base env detection now also reads `mamba info --json` key `base environment`.
- Ctrl+C handling during installs: no traceback, rescue snapshot + interactive restore/continue/abort prompt, state saved to `.env_repair/state.json`.
- Localized CLI output (auto-detected from system locale).
- Localized `--help` / subcommand help text (auto-detected from system locale).
- Adopt-pip removes the pip version by default (use `--keep-pip` to skip) and force-reinstalls the conda package after uninstall to avoid missing files.
- Added `rollback` subcommand (conda revisions) with optional confirmation (`-y`).
- Added `rebuild` subcommand (export/import into new env) with optional verification (`--verify`) and confirmation (`-y`).
- Added `diagnose-clobber` subcommand (parse ClobberError logs + conda owner lookup).
- Added `diagnose-inconsistent` and `fix-inconsistent` (levels: safe/normal/rebuild).
- Added `one-shot` subcommand to run `fix-inconsistent` + scan/fix + `verify-imports --fix` in one command.
- Added `cache-check` and `cache-fix` (levels: safe/targeted/aggressive, confirmation via `-y`).
- Added `diagnose-ssl` advisor (checks `import ssl` in env/base and prints guidance).
- CI: GitHub Actions workflow for unit tests and sdist/wheel build checks.
- Release: GitHub Actions workflows for PyPI (Trusted Publishing) and optional anaconda.org upload; local conda-build recipe for testing feedstock packaging.
- Release tooling: added `release.py` (patch bump + optional sync) and `tools/sync_versions.py` staged-recipes copy support.
- Release tooling: `tools/sync_versions.py --pypi-sdist` hashes the exact PyPI sdist to avoid local-vs-PyPI `sha256` mismatches.
