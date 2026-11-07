# Recovery validation and remaining limitations

## Validation on 2026-11-07

The following Windows/conda-forge scenarios were exercised manually during
development. They describe observed runs, not guarantees about package availability
on every Python version/platform. Recreate intentional damage only in disposable
environments with a known restoration path, not as a routine production test.

| Scenario | Observed result |
| --- | --- |
| Missing `narwhals` package directory with its metadata retained | The user confirmed normal dependency repair restored the dependent import without a sandbox or Python change. The moved original directory was retained separately as a backup. |
| Python 3.15.2 without a Qt binding, choosing PySide6 | The user confirmed imports of `qtpy`, `qtawesome`, `superqt`, `PySide6.QtCore` and `PySide6.QtWidgets` on Python 3.15.2. No downgrade was needed for that combination. |
| Python 3.15.2 without a Qt binding, choosing PyQt6 | After the separately confirmed migration, `verification.json` recorded Python 3.14.10 in the original prefix. The final check passed 647 imports. `application.json` reported `ok: true`, exit code 0, env-repair 0.2.8 preserved/startup checked, and successful installer/runner cleanup. |

The full original-environment migration was run by the user. Subsequent developer
verification used unit/fixture tests, a real streaming child-process test and
read-only interpreter-path queries, not another repair of base.

The development suite passed 297 tests after the multiple-site-packages fix:

```cmd
python -m unittest discover -s tests -p "test_*.py"
```

This is not a claim that the current changes have passed the GitHub OS/Python matrix
or that the separate integration scenarios were rerun at this revision.

## Required evidence for a successful migration

- The command waits through installation, original-interpreter verification and
  cleanup. Output is streamed live; activating the temporary installer manually
  is neither necessary nor correct.
- `verification_runtime` identifies the intended original prefix and the newly
  installed Python version, not the sandbox or installer interpreter.
- `application.json` has a successful final result/exit code, preserved env-repair
  version and successful cleanup. A successful sandbox alone is not enough.
- The original environment can be activated again without references to a removed
  temporary installer in its activation scripts.
- A pip `.deleteme` launcher warning on Windows is not itself proof of failure or
  success: module/console startup and the final application result decide.

Full local reports, package inventories, activation backups and temporary-directory
paths stay outside Git. Keep a redacted summary in documentation instead.

## Historical issue: exact dependency specifications in history

Earlier migration code passed the tested conda dependency closure as exact
`name=version=build` specs. A locally inspected transaction recorded 116 requested
specs, including Python implementation/ABI helpers. A subsequent conda Python
upgrade failed on the historical `python-gil=3.14.10` requirement; no configured
pins or prefix pin file were present. Mamba proposed upgrading the helpers while
removing the incompatible Qt binding. That proposal was aborted before execution.

The history entry and conflict support this diagnosis; different frontend handling
of requested specs is not evidence that one solver checks only direct dependencies.
The current migration separates those concerns: only the requested Python minor,
target distributions and explicit overrides appear in the install command. The
tested dependency lock stays in the application plan. Projecting the dry-run onto
the installed inventory must reproduce that lock, including dependencies that
remain unchanged. A changed version/build or missing tested package blocks the
offer until a new sandbox test validates the different combination. The worker
rechecks both the preview and installed package state. Legacy exact-lock plans
are rejected rather than replayed.

No existing history is rewritten, and user pins remain active. Old requirements
already recorded by previous releases/runs can therefore still constrain upgrades;
their cleanup is a separate, explicitly approved operation, not part of this fix.
The manual Qt migration above preceded this history fix; its success is not an
end-to-end Qt migration test of the new root-only planning strategy.

The opt-in manager regression installs small official packages in owned disposable
prefixes, checks the projected/installed package identities and asserts that only
the requested root, not its transitive dependencies, appears in the history:

```cmd
set ENV_REPAIR_RUN_MANAGER_TESTS=1
python -m unittest discover -s tests -p "test_migration_history.py"
set ENV_REPAIR_RUN_MANAGER_TESTS=
```

This test uses available conda/mamba executables and requires channel access. It
does not run a repair against base or modify its history.

Both environment creation and the tested installation use `--copy`. On Windows,
the test verifies that installed DLLs have different file identities from their
package-cache counterparts before testing removal. Otherwise conda's default
hardlinks can share a DLL with an active base/manager process and leave locked
`.conda_trash` files in the disposable prefix. The test must assert that its owned
workspace is completely removed; cleanup errors are not ignored and no process
outside the test is killed. This isolation is specific to this integration test,
not a change to the user's package-manager configuration or migration solver.

The copy-isolated manager test was rerun successfully with both conda and mamba:
each preserved root-only history and completely removed its owned workspace.
The normal suite then had 309 passing tests and one opt-in test skipped; the
manager integration test was executed separately. The earlier failed hardlink-based
run still has locked leftovers until the processes using the shared DLLs exit;
this does not describe a cleanup failure in the new copy-isolated runs.

## Deferred functionality

Wrapping conda/mamba/pip/uv install/update operations with an automatic read-only
post-check has been discussed but is not implemented by these changes.
