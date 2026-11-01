# Changelog (env-repair)

## 0.2.8 - 2026-11-01

Historical changes were reconstructed from Git tags and the commits between
releases. Dates through 0.2.7 are the tagged commit dates, not independently
verified PyPI publication dates.

- Pin Linux CI/build runners to Ubuntu 24.04 so Python 3.9 remains testable after the ubuntu-latest migration to 26.04.
- Install conda-build and upload tooling explicitly in base and execute them through `conda run -n base`, avoiding missing conda subcommands caused by environment mismatches.
- Update checkout/setup actions to Node.js 24-compatible majors and remove implicit defaults channels from conda build setup.

### Import and dependency repair
- Repair shared import dependencies before their consumers, including missing distribution metadata and module attribute/import errors; batch conda dependency repairs and defer unresolved consumers without uninstall/reinstall cascades.
- Preserve pip-installed packages when imports still fail after reinstall instead of removing potentially usable packages because of dependency or DLL failures.
- Repair missing `matplotlib` imports through the `matplotlib-base` provider.
- Detect pip overlays even with a single dist-info directory, a masked conda-list entry, or different conda-provider/Python-distribution names. Compare installed metadata with original conda records and checksums, then clean the overlay before relinking its conda provider.
- After conda takeover of a local installation, allow one recorded-file cleanup/relink retry only when the import failure belongs to that package; preserve consumers when another dependency is broken, and retain the final exception in diagnostics.
- Apply legacy-package and dedicated compatibility rules before generic local-install handling. Keep the warning/skip policy for `nose`, including local direct URLs.
- Retry parallel import timeouts sequentially with a 90-second limit. Under `--fix`, persistent timeouts of conda-managed distributions can trigger one strictly-newer provider upgrade and recheck; local/pip-only packages are preserved.
- Mark unresolved timeouts as incomplete verification (`complete: false`, exit code 1), retain retry/upgrade actions and initial timeouts in JSON reports, and do not silently count timed-out imports as successful.

### Metadata and binary-file safety
- Preserve a valid dist-info directory during duplicate cleanup, prefer conda-owned metadata, and retain duplicate findings until force-reinstallation succeeds.
- Recognize conda metadata ownership on Windows and Unix and through both file lists and path records.
- Always repair duplicate PyPI `build` distributions via pip, not an unrelated conda package.
- After a successful charset-normalizer dependency relink, quarantine unowned Windows extensions that shadow existing conda-owned `cd`/`md` modules. Accept Python source or binary-only replacements such as `md.pyd`.
- Preserve owned binaries and unrelated mypyc helpers; retain moved files under the target environment's `.env_repair/quarantine/` with original and recovery paths in repair actions.

### Manager discovery and conda bootstrap
- Make mamba the default duplicate-repair preference and retain it as installer even if discovery needs fallback information from conda.
- Use the selected manager's environment listing and local environment directories instead of additionally invoking conda after successful mamba discovery; prefer mamba for external channel lookup.
- Fix `.condarc` parsing so mirror URLs and lists in unrelated settings do not become explicit channel arguments.
- Distinguish installer selection from separate conda health diagnostics in help and logs.
- Bound conda health probes to 30 seconds, disable automatic error reporting, capture missing-executable/timeout failures, force plugin disabling for the retry, and recognize plugin-load errors even with exit code 0.
- Resolve bootstrap dependency providers directly from local conda-meta records, avoiding an unnecessary package-list call before repairing conda imports.
- Repair diagnostic dependencies before the conda core; do not infer a broken Python runtime merely from a failing dependency import.
- If the same dependency error survives relinking, request a strictly newer provider version (for example `anaconda-auth>0.8.4`) and recheck conda health. Respect pins, retain installed packages on solver failure, and stop instead of repeating the same reinstall or automatically removing dependents.

### Validation
- Expand regression coverage for dependency-first repair, metadata preservation, binary shadowing, manager preference, conda health/upgrade escalation, and timeout recovery.
- 138 unit tests pass locally. No automated repair run against the real base environment was performed for validation.

## 0.2.7 - 2026-10-04

- Complete optional `--use-uv` support for scan/fix, one-shot, import repairs, adoption, and snapshot restoration; retain pip fallback when uv is missing or `--ignore-installed` semantics are required.
- Preserve `--use-uv` before or after the `one-shot` and `verify-imports` subcommands and target the selected environment's Python interpreter.
- Add removal of legacy `nose` when its import requires the removed `imp` module. Version 0.2.8 restores a conservative warning/skip policy before local-install handling.
- Attempt to restore missing `pkg_resources` through pip-installed setuptools when the target Python is available, retaining a conda fallback without a Python executable.
- Migrate the conda-forge recipe template to v1, with an embedded build command, declared console entry point, Python 3.9/current-Python import tests, `pip check`, and a CLI smoke test.
- Explicitly use the build-provided Python executable on Windows and Unix instead of relying on a custom Windows batch launcher in the staged recipe.
- Synchronize v1 recipe context variables and copy the conda-forge template as `staged-recipes/recipes/env-repair/recipe.yaml`; update packaging documentation and sync tests.

## 0.2.6 - 2026-02-28

- Detect and repair a broken conda core before normal fix operations, retry conda commands with plugins disabled, and cache bootstrap health checks within a run.
- Detect mixed Python ABI residue in base; add a second repair stage for Python/menuinst and improve duplicate-pyd cleanup.
- Support `ENV_REPAIR_AUTO_YES` for noninteractive approval and use it in integration-test runs.
- Warn and skip legacy, unmaintained `nose` rather than repeatedly reinstalling it.
- Improve defaults-disabled fallback, solver-offender handling, and adoption target resolution.
- Add targeted repairs for NumPy/Numba compatibility, the NumPy/SciPy/scikit-learn/imbalanced-learn stack, PyArrow runtime mismatches, PyGithub/PyNaCl dependencies, and Streamlit/Protobuf mismatches.
- Clean known stale/shadowing package artifacts and add narrow pip-wheel fallbacks for stubborn imports.
- Probe conda search for local `direct_url=file://` installations before skipping them, allowing a managed candidate to be relinked.
- Report import timeouts separately as `[TIMEOUT]`, exclude them from hard-failure repair loops, and add a dedicated JSON `timeouts` field. Version 0.2.8 adds retries and incomplete-verification status.
- Fix scan progress/header formatting so `Scan:` starts on a new line after the environment progress indicator.
- Expand import-repair regression tests and update repair-behavior documentation.

## 0.2.5 - 2026-02-14

- Add `one-shot` to run `fix-inconsistent`, scan/fix, and `verify-imports --fix` in sequence, with shared environment selection, confirmation, and planning support.
- Add integration scenarios, an isolated environment runner, and Markdown/JSON result summaries for duplicate metadata, import repair, corrupt conda records, broken imports, and inconsistent environments.
- Tighten corrupt conda-record detection while allowing legitimate minimal/helper records without `depends`.
- Protect local/manual installations without a conda-managed equivalent from automatic reinstall.
- Repair Numba/llvmlite version mismatches by targeting llvmlite before relinking Numba.
- Refine blacklist behavior so stale entries do not permanently suppress repairs for already installed conda packages.
- Retry pinned-Python solver conflicts without force-reinstall to allow compatible upgrades.
- Add regression tests and document the combined repair workflow.

## 0.2.4 - 2026-02-09

- Support mamba-only installations: load channels using `mamba config list --json` when conda is unavailable and recognize the `base environment` key from mamba info.
- Add `release.py` for patch-version bumps and optional recipe synchronization.
- Add `tools/sync_versions.py` to synchronize recipe versions, hash an sdist, and copy the conda-forge recipe into staged-recipes.
- Add `--pypi-sdist` to hash the exact published source archive instead of a potentially different local rebuild.
- Add regression coverage for environment arguments, interrupted import checks, mamba-only discovery/channel loading, Windows command fallback, and recipe synchronization.
- Update release documentation and conda-forge source metadata.

## 0.2.3 - 2026-02-01

- Fix `verify-imports` environment selection for `--env` before or after the subcommand; prevent subparser defaults from replacing the requested environment.
- Print the selected environment, Python executable, distribution count, and import-target count.
- Handle Ctrl+C more cleanly while draining parallel import checks.
- Route streamed export commands through Windows-aware subprocess handling so conda/mamba batch shims work outside base.
- Add Windows-friendly noarch recipe build scripts and a dedicated PyPI-source conda-forge recipe template.
- Correct source archive naming and document source hashes and staged-recipes packaging.
- Exclude PyPI `build` from pip-to-conda adoption because it is not the conda `python-build` package.
- Add `build` to the development dependencies and refine Anaconda publishing workflow configuration.

## 0.2.2 - 2026-01-31

- Bump package metadata from 0.2.0 to 0.2.2 and make minor README updates.
- No runtime code changes relative to the v0.2.0 tag.

## 0.2.0 - 2026-01-31

- Initial release of the extracted project with baseline documentation, scanning, and repair for mixed conda/mamba/micromamba/pip installations and plain venv/virtualenv environments.
- Detect duplicate distribution metadata, invalid artifacts, duplicate extension modules, and damaged conda metadata; select pip or conda repair paths.
- Add pip-to-conda adoption with reduced two-pass search, normalized-name matching, explicit package-name overrides, solver-offender retries, and a persistent adoption blacklist.
- Remove adopted pip versions by default (`--keep-pip` opts out) and force-relink conda packages after uninstall to restore shared files.
- Add critical/full import verification with dependency-first repairs, batched conda operations, solver-offender skipping, and a persistent import-repair blacklist.
- Handle known obsolete wrappers, incompatible packages, and platform-only imports; avoid pointless pip-uninstall/conda-reinstall loops for conda-owned case variants such as PyDrive/pydrive.
- Add transparent target lists, debug command-line logging, live subprocess output, and progress indicators.
- Add localized CLI output and help.
- Add rescue snapshots, interruption handling with restore/continue/abort, and persisted interruption state.
- Add `rollback`, `rebuild`, `diagnose-clobber`, `diagnose-inconsistent`, `fix-inconsistent`, `cache-check`, `cache-fix`, and the `diagnose-ssl` advisor.
- Add GitHub Actions for unit tests, source/wheel build checks, PyPI Trusted Publishing, and optional anaconda.org publishing.
- Fix cross-platform ClobberError path parsing; adopt SPDX license metadata and harden distribution checks.

## History notes

- No v0.2.1 tag or 0.2.1 version bump is present in the local Git history; no release entry or date has been invented.
- Pre-release development from 2026-01-18 onward is included in the first tagged release, 0.2.0.
