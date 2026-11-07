# 🩺 EnvRepair [![CI](https://github.com/noragen/env-repair/actions/workflows/ci.yml/badge.svg)](https://github.com/noragen/env-repair/actions/workflows/ci.yml)
### Fixing broken Python environments – safely, transparently, reproducibly

<p align="center">
  <img alt="EnvRepair icon" src="https://raw.githubusercontent.com/noragen/env-repair/83906131f0ecb478cfc4dd3a6eb724e8b2517c45/icons/env-repair-256x256.png" width="128" />
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
- `mamba` in PATH for conda-style envs (preferred). `conda`/`micromamba` are supported when available.
- `conda` is optional: mamba-only installs (e.g. Mambaforge/Miniforge variants) are supported.
- Python available in the target environment.

### Planning without repair

For commands exposing `--plan`, planning returns before approval or mutation,
including when combined with `--json`, `-y` or auto-yes. The JSON report contains
`planned: true`; it is a static description, not a solver-validated transaction.
Environment/revision discovery can still invoke metadata queries. No rescue
snapshots, cache cleanup, installations or rebuild destinations are created.

```cmd
env-repair one-shot --env myenv --plan --json
env-repair fix-inconsistent --env myenv --plan --json
env-repair rebuild --env myenv --to newenv --plan --json
env-repair rollback --env myenv --to prev --plan --json
env-repair cache-fix --plan --json
```

This guarantee applies to the corrected code; older releases may not contain it.
The separate rollback `--dry-run` option is not equivalent to `--plan`: its flow
can still create snapshot files after approval.

### Output language

env-repair's own prompts, help, progress and repair messages support English and
German. Selection follows `LC_ALL`, `LC_MESSAGES`, `LANG`, then the system locale;
other locales fall back to English. For example, use `set LC_ALL=de_DE` or
`set LC_ALL=en_US` in CMD before starting env-repair. Package names, command-line
options, JSON field names and diagnostic codes are not translated. Original
solver messages, installer output and Python tracebacks remain available in
their original language rather than being rewritten.

---

## 🚀 Quick Start

> All examples below use Windows `cmd.exe` syntax (`.bat` blocks).  
> Adjust paths/shells as needed for Linux/macOS.

Recommended first run (one-shot):
```bat
env-repair one-shot --env base -y
```

`--prefer mamba` is the default, both for one-shot and the regular scan/fix.
Conda packages use `mamba` whenever it is available in PATH; otherwise `conda`
is the fallback. A failed `mamba info` call does not switch repairs to conda.
Successful mamba discovery uses `mamba env list` and does not additionally invoke
`conda info`; channel discovery also prefers mamba when external tooling is needed.
The bootstrap health check still diagnoses an installed **conda** separately,
even when **mamba** is the installer. These diagnostic calls do not change the
selected installer. Failures are captured (health probes time out after 30 seconds),
and known dependency errors are repaired using mamba and local `conda-meta` records
before retrying conda. Conda error-report uploads are disabled for health probes.
If the same dependency error survives relinking, bootstrap repair requests a
strictly newer version of the affected provider and checks conda health again.
It respects pins and does not uninstall the package or its dependents first.
`--prefer auto` and `--prefer conda` retain this executable selection;
`--prefer pip` prefers pip packages when fixing duplicates.

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

Use `--use-uv` to run pip operations through an available `uv` executable:
```bat
env-repair one-shot --env base -y --use-uv
env-repair verify-imports --env base --full --fix --use-uv
```
The selected environment's Python is passed explicitly to uv. If uv is unavailable,
or a repair requires pip's `--ignore-installed` recovery behavior, pip is used instead.

One-shot repair flow (recommended):
```bat
env-repair one-shot --env base -y
```

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

Both `--env` placements are supported:
```bat
env-repair --env base verify-imports --full --fix
env-repair verify-imports --env base --full --fix
```

If you want the full sequence in one command (`fix-inconsistent` + scan/fix + `verify-imports --fix`):
```bat
env-repair one-shot --env base -y
```

Notes:
- Repairs use **batched** conda/mamba operations (no slow one-by-one reinstalls).
- If the solver fails for a specific package, EnvRepair retries the batch without the offending spec and remembers it in:
  - `.env_repair\verify_imports_blacklist.json`
- Platform-only modules are skipped (e.g. `sh` on Windows, `ptyprocess` missing `fcntl` on Windows).
- Import timeouts are reported separately (`[TIMEOUT]`) and are not treated as hard failures in fix planning.
- For local/manual installs from `direct_url=file://...`, EnvRepair now probes conda/mamba search first; only packages without a conda candidate are skipped.

---

## 🧑‍💻 Development

Run tests:
```bat
python -m unittest discover -s tests -p "test_*.py"
```

Run integration tests (non-interactive):
```bat
python itest\scripts\run_itest.py --list
python itest\scripts\run_itest.py --scenario S01_DUP_DIST_INFO --summarize
```

JSON output:
```bat
python env_repair.py --env base --json
```

Release helper (patch bump in `pyproject.toml`):
```bat
python release.py
python release.py --sync
```

Sync conda recipe metadata from project version:
```bat
python tools\sync_versions.py
python tools\sync_versions.py --pypi-sdist --feedstock env-repair-feedstock
```

---

The feedstock command is for use **after PyPI publication** and preserves
feedstock-specific recipe settings. Check for an existing bot update PR first.
The legacy `--staged-recipes` option remains available for initial admissions;
env-repair updates now belong in `conda-forge/env-repair-feedstock`.
See [the release guide](docs/releasing.md) for the complete sequence.

## 📝 Notes

- `--adopt-pip` installs only mapped PyPI packages.
- `--adopt-pip` is conda-only; for plain venvs it is ignored.
- After successful adoption, env-repair uninstalls the pip version by default; use `--keep-pip` to skip.
- For alias-like mappings (e.g. pip `msgpack` → conda `msgpack-python`), pip is only removed if both versions match.
- Channels are loaded from `.condarc` first, then `defaults` and `anaconda` unless disabled.
- `--debug` prints the exact external command lines as `[cmd] ...` (mamba/conda/pip), and streams live output to keep long operations transparent.
- If `conda` core is broken after updates, env-repair can auto-repair it in two stages: core packages first, then `python`/`menuinst` when health remains degraded or mixed ABI `.pyd` residue is detected.
- For automated runs (CI/itest), set `ENV_REPAIR_AUTO_YES=1` to bypass interactive confirmation prompts.
- On Windows, `duplicate-pyd` issues from mixed Python ABI residues are now cleaned directly by removing stale `.pyd` files that do not match the active ABI tag.

### Mini Troubleshooting

| Signal in output | Meaning | Recommended next step |
|---|---|---|
| `skip [installed from local file/path (direct_url=file://...)]` | Package was installed from a local/custom artifact and no conda candidate was found via search. | Keep as-is or reinstall manually from your local source if needed. |
| `[TIMEOUT] ...` | Import check exceeded the timeout window for this run. | Re-run verify-imports; timeout entries are informational and not auto-repair targets. |
| `skip [blacklisted for python X.Y ...]` | Previous solver run marked this package as incompatible for this Python version/channel set. | Re-run after channel/version changes, or remove the entry from `.env_repair\verify_imports_blacklist.json` to retry once. |
| `Solver hit pinned-python conflict; retrying without --force-reinstall...` | `--force-reinstall` could not satisfy pinned Python constraints. | Usually safe to continue; env-repair already retries with upgrade-friendly solver behavior. |
| `Post-fix: all non-skipped imports OK.` | Automatic repair succeeded for everything that is auto-fixable. | Review skipped imports; handle only those manually if they matter for your workload. |

### verify-imports auto-repair hints

#### Minimal package sandboxes (not specific to Qt)

`one-shot` and `verify-imports --fix` automatically invoke this fallback when
normal repairs still leave import failures. No new flag, separate command or
sandbox confirmation is required. Only unresolved, identified distributions are
tested: first with the current Python minor, then (for compatibility/validation
failures) up to two lower supported minors. Network, permission, timeout and
unclassified installation failures do not cause speculative Python retries.
Previously sandbox-tested targets in the same run are not tested again.
Qt still requires an explicitly selected backend; no binding is guessed.

The fallback creates temporary environments, may download packages, and records
its attempts in the repair report. It never removes original packages or changes
the original Python. Sandbox success does not clear original failures or turn a
failed repair into exit code 0. Scan-only runs never invoke it.

Use `sandbox` to test problematic packages or an alternative Python/package
combination **without repairing, cloning or downgrading the original environment**:

```text
env-repair sandbox --env base --target scipy --python-version 3.13
env-repair sandbox --env base --conda-package "numpy>=2" --check-import numpy
env-repair sandbox --env base --pypi-package "requests==2.32.5" --check-import requests
```

- `--target` selects an installed distribution at its current version and installer.
- `--conda-package` / `--pypi-package` specify packages or version constraints to
  test, including packages not installed in the original. Explicit constraints
  override the installed-version constraint for the same selected provider.
- Repeat these options for multiple roots. Only those roots, the chosen Python,
  pip and their solver-resolved dependencies are installed. Unrelated original
  packages, local builds and prefix-specific pins are **not** carried over.
- `--python-version` accepts a major.minor version and defaults to the source
  interpreter's version. `--manager` selects mamba/conda/micromamba;
  `--channel` adds channels. A conda-compatible manager is needed for creation;
  its normal configuration still applies.
- PyPI targets and dependencies require binary wheels from PyPI; no source
  builds are attempted. Relevant local targets are copied using guarded `RECORD`
  entries, without rebuilding or changing the originals. Unsafe paths, startup
  hooks, missing files, overlaps and relevant local-only conda artifacts block
  the test. Unrelated local packages are neither copied nor questioned.
- `pip check` and isolated import checks cover the distributions actually
  installed in the sandbox, including dependencies. `--check-import` adds
  specific modules, such as `scipy.linalg`. `env-repair` itself is excluded.

After success, failure or an ordinary interruption, the owned temporary
environment is removed through the package manager where possible; its complete
workspace (including downloaded wheels and local copies) is then deleted.
Read-only files and transient Windows locks are handled with bounded retries.
Ownership/path checks prevent cleanup from touching the original or unrelated
directories. If cleanup cannot finish, the exact residual path and error are
reported; it is never silently claimed to be clean. Previously existing,
unmarked test environments are not swept up automatically.

Only small diagnostics are retained in
`.env_repair/reports/sandboxes/<run-id>/migration.json` relative to the working
directory, with requirements and an optional explicit package manifest. Reports
record target parameters, tested packages, stage output, failures,
`validation_ok`, `cleanup` and `scope: target-packages`. An optional package-export
failure does not invalidate successful import checks. Conda helper names such
as `_python_abi3_support` are accepted, and native-extension ABI filenames are
converted to their real import names. Malformed metadata reports identify the
offending record and stop pointless retries of other Python versions. `--json`
prints the structured result while progress stays on stderr. Error diagnostics
distinguish candidate availability, Python constraints, dependency conflicts,
network/TLS, permissions, timeouts, unsupported wheels and binary/import errors;
unknown messages stay unknown. These are diagnostic hints, not permission to
uninstall packages. A generic "no matching distribution" does **not** establish
whether the cause is Python, platform, missing wheels or an unavailable release.
See [pip dependency resolution](https://pip.pypa.io/en/stable/topics/dependency-resolution/),
[PyPI troubleshooting](https://docs.pypi.org/using-pypi/troubleshooting/) and
[conda exception types](https://docs.conda.io/projects/conda/en/stable/dev-guide/api/conda/exceptions/index.html).

**Sandbox success validates the target combination, not the complete original.**
In repair flows, env-repair then solves a separate transaction against the
original environment's complete conda constraints and pins. The full plan,
including Python changes and dependency removals, is shown and requires its own
default-no confirmation. Neither `-y` nor `-f` approves this migration. Protected
runtime/manager packages cannot be removed, and env-repair itself cannot be
upgraded/downgraded through it (an identical-version relink is allowed).

After approval, an independent temporary Python/mamba installer runs as a
supervised child process before touching the original interpreter. It rechecks the
inventory and approved transaction, saves conda/pip snapshots and installs the
tested packages. A changed plan requires new confirmation; no automatic rollback
is claimed after a partially failed transaction. Local extensions are not rebuilt.

**Verification runs with the newly installed Python of the original environment**,
not with the sandbox/installer Python. A prior `one-shot --env base -y -f`, for
example, runs again as `one-shot --env base -y -f`; its other parsed options are
preserved too. Environment discovery is checked before rerunning so `base` cannot
accidentally resolve to the temporary installer. Internal recursion protection
prevents repeated migration offers during this final run without changing the
user's options. Missing original pip/local distributions are reported, not silently
ignored. Only the final original-environment result determines repair success.

The independent installer uses its own Mamba root and does not inherit the
original shell's conda/mamba activation variables. Activation scripts are backed
up before bootstrap and checked before verification and cleanup: references to
the temporary installer are restored from the byte-for-byte backup. If a new
unsafe activation script cannot be restored, cleanup is blocked and the installer
is retained for recovery rather than leaving a dangling activation path.

The independent installer is cleaned up after verification using the original
Python. If that interpreter cannot even start after an installation failure, the
installer is retained explicitly for recovery and its path is reported. Snapshots,
application and verification results remain beside the sandbox report. The caller
waits for the independent installer to exit, then starts and waits for cleanup
using the original Python. Output from every phase is streamed live; no Windows
`execv` handoff returns the prompt early. The command returns the final status,
including verification and cleanup failures. Ctrl+C during this supervised phase
waits for the child to stop before proceeding; it never deletes a running
installer's workspace. No manual activation of the temporary environment is needed.
`application.json` and `application.log` retain the final result and diagnostics.

An installed env-repair must remain at its original version and both its module
and console command must still start after the Python change and final one-shot.
Conda transactions cannot change its version/build. For pip installations, a
checksum-protected local wheel of the original code/metadata is saved beforehand;
if the distribution or launcher disappears or its version changes, that exact
version is restored offline without changing dependencies. Failed preservation
is an error and retains the installer for recovery, never a successful repair.

Sandbox validation supports multiple package-search directories, including Windows
Python installations exposing both `Lib/site-packages` and
`lib/python/site-packages`. Official conda/PyPI targets do not require choosing a
single copy destination. When a permitted local package must be copied, the
isolated target Python supplies its `sysconfig` `purelib` destination; env-repair
verifies that it belongs to the intended environment before writing files.

**Requested requirements and the tested lock are separate.** The original install
command contains only the selected Python minor and target-package requirements,
including explicit package overrides. Transitive helper packages such as
`python-gil`, `cpython` and `python_abi` are not promoted to explicit user requests.
Exact tested versions/builds remain in the diagnostic plan, not the install command.
The projected final conda state, including unchanged packages, must match that
tested combination before confirmation and again in the installer. A mismatch
requires a new sandbox test; it never silently installs an untested combination.
The installed state is checked again before original-environment verification.

Earlier migration plans that explicitly installed the complete dependency lock
cannot be replayed; create a fresh plan. Existing historical requirements from
previous runs are not rewritten or removed automatically. Do not blindly edit
history or bypass pins to resolve those older conflicts. See
[validation notes](docs/validation.md) for the observed runs and regression scope.

#### Choosing a Qt backend

Qt compatibility checks print an immediate status line while waiting for the
solver. Failures display readable solver messages, not the raw JSON response or
internal C++ locations; structured diagnostics remain in the JSON repair report.

If the chosen conda Qt binding has no solvable installation, a second menu offers:

- Keep everything unchanged (default).
- Preview removal of the failing conda Qt consumers and their dependents, followed
  by a separate explicit confirmation. `-y` and `-f` do not authorize this Qt removal.
- With separate consent, test the two nearest supported lower Python minors in
  the shared package sandbox. Only the selected binding, affected consumers and
  their necessary dependencies are installed. QtCore/QtWidgets are additional
  import checks. No full-original-environment solve is required to run this test,
  and its success does not change Python or mark original imports repaired.

Noninteractive/JSON reports retain the unresolved status and available recovery
choices without taking an automatic removal or Python downgrade decision.

In fix mode (`one-shot` or `verify-imports --fix`), unresolved QtPy binding errors
offer one shared interactive selection for all affected consumers:

- `0` or Enter: keep the environment unchanged (default).
- `1`: PySide6.
- `2`: PyQt6.

If `QT_API` is already set, only that backend is offered; it is never silently
overridden. Legacy PyQt5/PySide2 are offered only when explicitly selected by
`QT_API`. Neither `-y`, auto-yes nor `-f` chooses a binding. JSON mode and runs
without a terminal never prompt or install a new backend through this selection.

Conda environments use the selected conda manager, configured channels and pins.
A dry-run rejects transactions that change Python's version or remove existing
packages without replacement. A missing compatible build leaves imports unresolved;
ordinary backend repair never downgrades Python or falls back to pip in a conda
environment. Python changes require the separately confirmed migration above.
Plain venvs use their own Python/pip (or opted-in uv), with binary wheels only.
After installation, every affected import is checked again under the unchanged
`QT_API`; installer success alone does not count as a repaired import. The choice,
transaction preview and recheck results are recorded in repair actions.

#### Removing unfixable packages

If packages remain unfixable after normal repair and compatibility tests,
env-repair can offer removal; this is not restricted to `pkg_resources`. A conda
dry-run shows package names and versions. General unfixable removal refuses
unrequested dependent removals and checks mixed pip dependencies too. Confirmation is
separate: `-y` and `ENV_REPAIR_AUTO_YES` do **not** approve this removal.
In an interactive terminal, answer the dedicated `[y/N]` question (default: keep).
Without a terminal or in JSON mode, packages are kept unless explicitly authorized.

Use `-f` / `--remove-unfixable` to explicitly authorize these removals without
the extra question, after the same safety preview:

```cmd
env-repair one-shot --env myenv -y -f
env-repair verify-imports --env myenv --full --fix --remove-unfixable
```

The flag does not enable fix mode by itself. Eligible leaf packages may include
Qt consumers or other unresolved imports, but never healthy unrelated packages.
Protected runtime/manager packages cannot be removed
through this path, even with `-f`; a failed/incomplete preview blocks removal.
Pip-installed consumers use pip/uv, but removal is blocked when it would leave
installed dependents broken (requirements are checked conservatively, including
conditional requirements). Declined or failed removals remain unresolved in the
report. Successful removals and the full preview are recorded in repair actions.

- Old site-packages layouts can retain stale metadata after a Python upgrade
  (for example, PyArrow 23 metadata in `Lib/site-packages` while the verified
  PyArrow 26 provider lives in `lib/python/site-packages`). If the current
  conda-owned metadata passes its stored checksum, only the obsolete metadata
  directory is quarantined after revalidation. The current package is not
  repeatedly uninstalled with pip or relinked. Package code and conda records
  are preserved; without intact owner evidence, normal overlay diagnostics remain.
- The normal scan also detects missing/empty `METADATA` in `.dist-info`
  directories, a cause of `pip inspect` crashes on newer Python. Fix mode moves
  damaged metadata into the target environment's `.env_repair/quarantine/`,
  preserves package code, and relinks known conda owners. Unknown owners are
  not guessed as PyPI packages. Initial discovery/snapshot commands can still
  encounter the metadata error before the cleanup phase runs.

- Missing `pkg_resources` can be an obsolete consumer API, not a broken setuptools
  installation: setuptools 82+ no longer includes it. Repair tries a strictly
  newer conda-managed consumer; it does not automatically downgrade setuptools
  or overlay it with pip. Unresolved consumers remain reported as `defer`.
- QtPy does not install a Qt binding itself. Shared `QtBindingsNotFoundError`
  failures target an unambiguous installed conda PyQt/PySide binding and respect
  `QT_API`. If none is installed or the choice is ambiguous, choose a compatible
  backend explicitly; env-repair does not select a GUI stack or reinstall its
  consumers automatically.

- A parallel import timeout is retried sequentially with a 90-second limit.
  If it still times out, `--fix` can request a strictly newer version of an
  identified conda-managed distribution, respecting pins and checking the import
  again. Local/pip-only packages are not upgraded for timeouts. Remaining timeouts
  produce `complete: false` in the report and exit code 1; they are not silently
  counted as successful checks.

- Shared dependency failures (including missing distribution metadata and module
  attribute errors) are repaired before dependent packages, with conda targets
  batched together. Unresolved consumers are reported as `defer`, not silently
  counted as repaired or repeatedly uninstalled.
- Duplicate cleanup preserves a valid `.dist-info` directory and keeps the
  repair pending until reinstallation succeeds. Conda-owned metadata is preferred.
- After repairing `charset-normalizer`, unowned Windows `.pyd` files shadowing
  existing conda-owned `cd`/`md` modules (Python source or binary extensions)
  are moved into the target environment's
  `.env_repair/quarantine/` directory rather than deleted. Repair actions include
  original and quarantine paths; conda-owned extensions are left untouched.

- Missing private runtime modules are now mapped to the correct conda providers before regular fix planning:
  - `_brotli` -> `brotli-python`
  - `_argon2_cffi_bindings` -> `argon2-cffi-bindings`
  - `pkg_resources` -> `setuptools`
- For Streamlit protobuf runtime/codegen mismatch (`_CheckCalledFromGeneratedFile`), EnvRepair now schedules:
  - `protobuf>=4,<7` alignment
  - `streamlit>=1.30` upgrade/relink
- If `imblearn` fails with `from sklearn.base import clone`, EnvRepair relinks the sklearn stack (`numpy`, `scipy`, `scikit-learn`) before relinking `imbalanced-learn`.
- If `sklearn` still fails in the same chain, EnvRepair also relinks `numpy` + `scipy` + `scikit-learn`.
- If `pyarrow` reports DLL/procedure mismatch, EnvRepair relinks `libarrow` + `pyarrow`.
- If `pygithub` fails in its import chain, EnvRepair relinks `requests` + `pynacl` + `pygithub`.
- `pkg_resources` is treated as legacy and skipped to avoid pip/conda thrash loops.
- For stubborn compiled/import-chain packages (`pyarrow`, `scikit-learn`, `imbalanced-learn`, `pygithub`, `streamlit`), EnvRepair now performs a targeted pre-clean of stale site-packages artifacts before conda relink.
- If `pyarrow`, `github`, or `streamlit` still fail after conda repair, EnvRepair runs a last-chance pip wheel reinstall for exactly those imports and rechecks immediately.
- For `pygithub`, EnvRepair now performs a hard conda remove before reinstall when needed, to recover from partial module-tree loss (`No module named 'github'`).
- For `pygithub` failures involving `nacl._sodium` DLL load issues, EnvRepair additionally relinks `libsodium` + `pynacl` in the same repair batch.
- Solver offender parsing accepts more libmamba output variants (`name =* * does not exist` and `name ==x ... does not exist`) to avoid false negatives in blacklist/retry logic.

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
