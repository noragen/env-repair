# Changelog (env-repair)

## Unreleased

- Isolate DLLs in the opt-in conda/mamba history integration test using `--copy` for creation and installation. Check that Windows DLLs do not share cache/base hardlink identities and assert complete owned-workspace cleanup instead of ignoring locked `.conda_trash` leftovers.
- Keep migration intent separate from tested dependency locks: install only Python/target requirements, check the projected conda state against exact tested versions/builds before approval and execution, and verify the installed state before the original one-shot. Reject differing combinations and legacy full-lock plans without modifying existing history or bypassing pins. Add opt-in isolated conda/mamba history regression coverage.
- Document the manually verified Narwhals/Altair, PySide6 and PyQt6 recovery paths, final-result checks and historical exact-spec issue in `docs/validation.md`; install/update wrapping remains deferred.
- Do not abort official-package sandbox validation when Windows Python exposes multiple site-packages roots. Only resolve a copy destination when local packages actually need copying; use the isolated target interpreter's sysconfig purelib path with prefix/escape checks, not a unique-directory heuristic. Preserve validation, diagnostics and cleanup across legacy/new Python layouts.
- Replace asynchronous Windows process replacement with synchronous, live-streamed supervision through installation, original-interpreter verification and cleanup. Propagate the final exit status, reject stale worker reports, and wait for interrupted workers before cleanup.
- Preserve the installed env-repair version across Python migration: keep conda identity guards, back up pip-installed code/metadata as a checksum-verified offline wheel, restore missing/changed pip installations or launchers, and check both module and console-script startup before reporting success.
- Isolate the independent installer's Mamba root and activation variables from the original environment. Back up activation scripts before bootstrap, restore temporary installer references byte-for-byte before verification/cleanup (including failed transactions), and retain the installer if a new unsafe activation script cannot be restored. Restore original activation variables for the original-interpreter verification, not only PATH.
- Generalize `-f/--remove-unfixable` to unresolved leaf packages beyond pkg_resources, with protected-package and mixed-installer dependency guards; reject unrequested cascading removals in the generic flow.
- After successful sandbox validation, offer a separately solved original-environment transaction with explicit confirmation, snapshots, independent Python/mamba installation and unchanged-plan checks. Rerun the same one-shot options (including `--env base`) with the newly installed original interpreter, never with the sandbox/worker interpreter; guard against wrong-environment discovery and recursive migrations. Preserve final verification/application reports and clean up the installer when the original Python is usable.
- Keep recovery logs across supervised child processes and continue draining installer output if a console pipe closes, preventing output deadlocks. Return only after installation, verification and cleanup have finished, with the final application report retained for diagnostics.

- Use native German umlauts and ß in UTF-8 translation catalogs instead of ASCII transliterations; preserve English messages, placeholders and technical identifiers.

- Accept valid underscore-prefixed conda helper packages such as `_python_abi3_support`; identify malformed metadata by record path and identity instead of aborting with an opaque validation message. Strip native-extension ABI tags from import targets (for example `_brotli.cp313-win_amd64.pyd` -> `_brotli`).
- Complete sandbox dependency/import checks before reporting success; normalize mamba URL-only explicit manifests and treat optional export failures as warnings rather than failed compatibility tests. Stop version retries for inventory/internal/cleanup failures.
- Always finalize owned sandboxes after success, errors or ordinary interruption: try manager removal, safely remove the temporary workspace with bounded Windows-lock/read-only retries, and retain only diagnostic reports/manifests under `.env_repair/reports/sandboxes/`. Report cleanup failures and residual paths explicitly; never sweep unrelated or unmarked environments.

- Distinguish stale metadata in an old site-packages layout from a real pip overlay when the current conda-owned metadata is checksum-verified. Quarantine only the stale dist-info after revalidation, preventing repeated pip-uninstall/relink loops such as PyArrow 23 residue alongside PyArrow 26.
- Show immediate Qt solver progress and readable solver messages instead of raw JSON/internal C++ locations; retain structured diagnostics in JSON reports. Localize remaining CLI/help, repair/import, Qt, sandbox and removal messages in English/German, preserving external diagnostics and machine fields. Use modern locale detection compatible with Python versions without getdefaultlocale.

- Automatically run the minimal package sandbox for unresolved imports after normal `one-shot`/`verify-imports --fix` repairs, without requiring a separate command or flag. Bound Python-version trials, retain diagnostics, avoid repeated tests and implicit Qt-backend choices, and keep original failures/exit status unchanged until genuinely repaired. Never mutate the original through this fallback.

- Add a general `sandbox` command/API for selected conda/PyPI/local targets and their necessary dependencies, optionally with another Python version. Do not clone the original package set or carry over unrelated packages/local builds/prefix pins. Require binary PyPI wheels, guarded local copies, pip dependency checks and isolated imports of installed sandbox dependencies. Preserve stage output and diagnostic error categories. Qt recovery uses this shared API rather than solving a downgrade of the full original first. Target-only success never authorizes original-environment migration or counts original errors as repaired; exclude env-repair and never automatically remove/rebuild local packages.

- Offer explicit recovery choices after an unsolvable Qt-binding installation: keep unchanged, preview and separately confirm removal of the identified conda Qt consumers, or prepare a dry-run-validated lower-Python migration command. General -y/-f never select these choices; Python migration is handed off rather than replacing the running interpreter.

- Correct the Qt6 conda-provider mapping to `pyqt6` instead of requesting the nonexistent conda-forge `pyqt>=6,<7`; keep PyQt5 and PyQt6 provider choices distinct.

- Add a shared interactive Qt-backend selection for unresolved QtPy consumers in fix mode, with keep/skip as default and explicit PySide6/PyQt6 choices. Respect existing QT_API, never select via -y/-f/auto-yes, and do not prompt in JSON/noninteractive runs.
- Preview conda Qt installs with pins active, block Python-version changes and unreplaced package removals, use target-environment binary wheels for venvs, and recheck all affected imports after installation.

- Add dedicated removal consent for unresolved `pkg_resources` consumers after repair: interactive default-no confirmation or explicit `-f`/`--remove-unfixable` authorization in one-shot and verify-imports fix mode. General `-y` and auto-yes do not authorize removal.
- Preview conda cascading removals with package names/versions, block protected runtime/manager removals and invalid previews, refuse pip removals that leave dependents behind, and keep declined/failed removals unresolved. Qt consumers are not candidates for this policy.

- Detect dist-info directories with missing/empty METADATA, which can crash pip inspect on newer Python. Quarantine only the damaged metadata with recovery paths, preserve package code, and batch force-relink known conda owners; do not guess pip reinstalls for orphaned metadata.

- Recognize missing `pkg_resources` as a legacy-consumer compatibility problem: attempt a strictly newer conda consumer instead of blindly reinstalling current setuptools or overlaying it with pip. Defer unresolved consumers without an automatic setuptools downgrade.
- Group QtPy `QtBindingsNotFoundError` failures as a shared backend issue. Relink one unambiguous installed conda Qt binding while respecting `QT_API`; preserve consumers and explain missing/ambiguous backend choices rather than installing a GUI stack automatically.

- Add `tools/sync_versions.py --feedstock` for updates to the existing conda-forge feedstock, preserving its requirements and configuration while updating version/hash and resetting the build number only for a new version. Retain the legacy staged-recipes target.
- Abort on requested PyPI download failure without reusing a stale checksum or updating recipes; document PyPI-first publication and bot/manual feedstock update paths.

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
