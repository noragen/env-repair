# Changelog (env-repair)

## Unreleased
- Initial project extraction and baseline docs.
- Docs: prefer `mamba` in command examples (keep `conda` as fallback where needed).
- Core env scan and repair workflow.
- Adopt-pip flow with conda mapping and fallback checks.
- Debug output and progress indicators.
- Support plain `venv`/`virtualenv` envs via `--env <path>` (pip-only scan/fix).
- Ctrl+C handling during installs: no traceback, rescue snapshot + interactive restore/continue/abort prompt, state saved to `.env_repair/state.json`.
- Localized CLI output (auto-detected from system locale).
- Localized `--help` / subcommand help text (auto-detected from system locale).
- Adopt-pip removes the pip version by default (use `--keep-pip` to skip) and force-reinstalls the conda package after uninstall to avoid missing files.
- Added `rollback` subcommand (conda revisions) with optional confirmation (`-y`).
- Added `rebuild` subcommand (export/import into new env) with optional verification (`--verify`) and confirmation (`-y`).
- Added `diagnose-clobber` subcommand (parse ClobberError logs + conda owner lookup).
- Added `diagnose-inconsistent` and `fix-inconsistent` (levels: safe/normal/rebuild).
- Added `cache-check` and `cache-fix` (levels: safe/targeted/aggressive, confirmation via `-y`).
- Added `diagnose-ssl` advisor (checks `import ssl` in env/base and prints guidance).
