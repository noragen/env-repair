# conda recipe (local build)

This folder contains a `conda-build` recipe to build `env-repair` locally.

Note: publishing to **conda-forge** happens via a separate **feedstock** repository
created from a PR to `conda-forge/staged-recipes` (or an existing feedstock).

For conda-forge PRs, use `conda.recipe/meta-forge.yaml` (PyPI URL + sha256) as the starting point.

Windows note (conda-forge CI builds on win-64 too):
- pip installs console_scripts as an `.exe` launcher on Windows.
- `noarch: python` packages must not ship that platform-specific `.exe`.
- The included `conda.recipe/bld.bat` deletes `Scripts/env-repair.exe` after pip install so conda-build can package it as noarch.
