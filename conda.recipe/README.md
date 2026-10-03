# conda recipe (local build)

This folder contains a `conda-build` recipe to build `env-repair` locally.

Note: publishing to **conda-forge** happens via a separate **feedstock** repository
created from a PR to `conda-forge/staged-recipes` (or an existing feedstock).

For conda-forge PRs, use `conda.recipe/meta-forge.yaml` (v1 recipe, PyPI URL + sha256) as the starting point.
`tools/sync_versions.py --pypi-sdist --staged-recipes staged-recipes` copies it as
`staged-recipes/recipes/env-repair/recipe.yaml`. The legacy `meta.yaml`, `build.sh`
and `bld.bat` must not be included in that staged recipe directory.

The conda-forge recipe embeds the pip build command and declares the console
entry point under `build.python.entry_points`. Installation generates the
appropriate launcher on each platform, including Windows; no custom batch
launcher is needed. Python import tests cover the minimum supported Python
(3.9) and the current Python, and include `pip check` and a CLI smoke test.

The local `conda-build` recipe (`meta.yaml`) and its build scripts remain
separate from the v1 conda-forge recipe.
