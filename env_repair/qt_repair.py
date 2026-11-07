from .i18n import tr
import json
import os
import sys
import subprocess
from pathlib import Path

from .unfixable import remove_unfixable
from .naming import normalize_name

from .conda_ops import conda_install
from .pip_ops import _get_pip_cmd
from .subprocess_utils import run_cmd_capture, run_cmd_live


QT_BINDINGS = {
    "pyside6": ("PySide6", "pyside6", "PySide6"),
    "pyqt6": ("PyQt6", "pyqt6", "PyQt6"),
    "pyqt5": ("PyQt5", "pyqt>=5,<6", "PyQt5"),
    "pyside2": ("PySide2", "pyside2", "PySide2"),
}


class QtSolveError(ValueError):
    def __init__(self, message, *, diagnostic=None):
        super().__init__(message)
        self.diagnostic = diagnostic


def _solver_failure(output, error):
    for value in (output, error):
        try:
            payload = json.loads(value)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        history = payload.get("log_history")
        problems = payload.get("solver_problems")
        messages = [item["message"] for item in history if isinstance(item, dict) and isinstance(item.get("message"), str)] if isinstance(history, list) else []
        if not messages:
            messages = [item for item in problems if isinstance(item, str)] if isinstance(problems, list) else []
        if not messages and isinstance(payload.get("message"), str):
            messages = [payload["message"]]
        if messages:
            return QtSolveError("\n".join(dict.fromkeys(messages)), diagnostic=payload)
        return QtSolveError("Qt compatibility preview did not report success", diagnostic=payload)
    return QtSolveError(error or output or "Qt binding is not compatible with the environment")


def _preview_qt_install(env_path, manager, channels, package, *, python_spec=None):
    command = [manager, "install", "--dry-run", "--json", "-y", "-p", str(env_path)]
    for channel in channels:
        command.extend(["-c", channel])
    command.append(package)
    if python_spec:
        command.append(python_spec)
    code, output, error = run_cmd_capture(command, timeout=120)
    if code:
        raise _solver_failure(output, error)
    payload = json.loads(output)
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise _solver_failure(output, error)
    actions = payload.get("actions")
    if not isinstance(actions, dict):
        if payload.get("message") == "All requested packages already installed":
            return {"LINK": [], "UNLINK": []}
        raise ValueError("missing Qt transaction preview")
    linked = actions.get("LINK", [])
    unlinked = actions.get("UNLINK", [])
    if not isinstance(linked, list) or not isinstance(unlinked, list):
        raise ValueError("invalid Qt transaction preview")
    for record in linked + unlinked:
        if not isinstance(record, dict) or not isinstance(record.get("name"), str) or not isinstance(record.get("version"), str):
            raise ValueError("incomplete Qt transaction record")
    replacements = {record["name"]: record["version"] for record in linked}
    for record in unlinked:
        if record["name"] not in replacements:
            raise ValueError(f"Qt install would remove {record['name']}; no automatic removal allowed")
        if record["name"] == "python" and replacements["python"] != record["version"] and not python_spec:
            raise ValueError("Qt install would change the Python version; keeping the environment unchanged")
    if python_spec:
        version = python_spec.split("=", 1)[1]
        if not replacements.get("python", "").startswith(version + "."):
            raise ValueError("preview does not install the requested Python version")
    return {"LINK": [{"name": item["name"], "version": item["version"]} for item in linked],
            "UNLINK": [{"name": item["name"], "version": item["version"]} for item in unlinked]}


def _offer_qt_recovery(affected, *, env_path, python_exe, manager, channels, package, use_uv):
    action = {"action": "qt_unavailable_recovery", "ok": False, "choice": "keep"}
    print(tr('\nNo compatible Qt installation was found for the current environment.'))
    print(tr('  0: Keep everything unchanged [default]'))
    print(tr('  1: Preview removal of the failing Qt consumers (including dependents)'))
    print(tr('  2: Test the selected Qt binding and affected packages with a lower Python in a minimal sandbox'))
    print(tr('Neither -y nor -f selects one of these actions.'))
    try:
        choice = input(tr('Choose recovery [0]: ')).strip()
    except (EOFError, KeyboardInterrupt):
        return affected, action
    if choice == "1":
        candidates = []
        from .verify_imports import _classify_dist
        from .conda_ops import get_env_package_entries

        entries = get_env_package_entries(env_path, manager, show_json_output=False)
        by_name = {normalize_name(entry.get("name") or ""): entry for entry in entries if isinstance(entry, dict)}
        for failure in affected:
            dist = failure.get("dist_path")
            if not isinstance(dist, Path):
                continue
            kind, name = _classify_dist(dist, conda_entries_by_name=by_name)
            if kind == "conda" and name:
                candidates.append({"package": name, "installer": "conda", "error": failure.get("error"), "dist_path": str(dist)})
        if len(candidates) != len(affected):
            action["reason"] = "cannot safely identify all conda Qt consumers; keeping packages"
            return affected, action
        removed, actions = remove_unfixable(candidates, env_path=env_path, python_exe=python_exe,
                                            manager=manager, force=False, interactive=True, use_uv=use_uv,
                                            label="Qt consumers without a compatible backend")
        action.update(choice="remove", removal_actions=actions, ok=bool(removed))
        outcomes = []
        for failure, candidate in zip(affected, candidates):
            outcomes.append(dict(failure, removed=True) if normalize_name(candidate["package"]) in removed else failure)
        return outcomes, action
    if choice == "2":
        action["choice"] = "python-plan"
        versions = set()
        for path in (Path(env_path) / "conda-meta").glob("python-*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("name") == "python":
                    major, minor, *_ = record["version"].split(".")
                    versions.add((int(major), int(minor)))
            except (OSError, ValueError, KeyError, AttributeError):
                continue
        if len(versions) != 1:
            action["reason"] = "cannot determine installed Python version safely"
            return affected, action
        major, minor = next(iter(versions))
        from .verify_imports import _read_metadata_name

        targets = set()
        for failure in affected:
            dist = failure.get("dist_path")
            name = _read_metadata_name(dist) if isinstance(dist, Path) else None
            if not name:
                action["reason"] = "cannot identify target distribution safely; no sandbox created"
                return affected, action
            targets.add(name)
        try:
            consent = input(tr('Create a minimal sandbox for the selected targets and their dependencies? [y/N] ')).strip().lower()
        except (EOFError, KeyboardInterrupt):
            consent = ""
        if consent not in {"y", "yes", "j", "ja"}:
            action["reason"] = "sandbox declined; original environment unchanged"
            return affected, action
        from .package_sandbox import test_packages

        binding = next(value[2] for value in QT_BINDINGS.values() if value[1] == package)

        attempts = []
        for candidate in range(minor - 1, max(8, minor - 3), -1):
            spec = f"python={major}.{candidate}"
            try:
                sandbox = test_packages(env_path=env_path, python_exe=python_exe, manager=manager,
                                        channels=channels, python_spec=spec, conda_specs=[package], targets=sorted(targets),
                                        extra_imports=[binding + ".QtCore", binding + ".QtWidgets"])
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                attempts.append({"python": spec, "error": str(exc)})
                continue
            attempts.append({"python": spec, "sandbox": sandbox})
            action.update(sandbox=sandbox, attempts=attempts)
            if not sandbox["ok"]:
                if sandbox.get("failure_category") in {"inventory", "internal-error", "cleanup", "network", "permission", "timeout"}:
                    action["reason"] = "sandbox could not complete; see the diagnostic report; original unchanged"
                    print(tr(action["reason"]))
                    return affected, action
                continue
            print(tr("Target-package sandbox passed. The original transaction will now be checked and offered for separate confirmation."))
            return affected, action
        action.update(reason="target-package tests failed for the two nearest supported lower Python minors; original unchanged", attempts=attempts)
        print(tr(action["reason"]))
    return affected, action


def repair_qt_bindings(failures, *, python_exe, env_path, manager, channels, in_conda_env,
                       check_import, interactive=True, use_uv=False):
    affected = [failure for failure in failures if "qtpy.QtBindingsNotFoundError" in str(failure.get("error") or "")]
    if not affected:
        return failures, []
    if not interactive or not sys.stdin.isatty():
        action = {"action": "qt_binding_selection", "ok": False, "selected": None,
                  "reason": "Qt requires an explicit interactive choice; -y/-f do not approve Qt removal or Python migration",
                  "recovery_choices": ["remove Qt consumers after preview/confirmation", "prepare a compatible lower-Python plan", "keep unchanged"]}
        if interactive:
            print(tr(action["reason"]))
            print(tr('Options: remove failing Qt consumers, plan a compatible lower Python, or keep unchanged.'))
        return failures, [action]
    selected_api = os.environ.get("QT_API", "").strip().lower()
    options = [selected_api] if selected_api in QT_BINDINGS else ["pyside6", "pyqt6"]
    action = {"action": "qt_binding_selection", "ok": False, "selected": None}
    if selected_api and selected_api not in QT_BINDINGS:
        action["reason"] = f"unsupported QT_API={selected_api}; environment setting was not overridden"
        return failures, [action]
    if in_conda_env and manager not in {"mamba", "conda", "micromamba"}:
        action["reason"] = "conda environment without a supported manager; no pip fallback"
        return failures, [action]
    print(tr('\nQt backend selection (independent of -y and -f):'))
    print(tr('  0: Keep environment unchanged [default]'))
    for index, api in enumerate(options, 1):
        label, conda_package, pip_package = QT_BINDINGS[api]
        print(f"  {index}: {label} ({conda_package if in_conda_env else pip_package})")
    if selected_api:
        print(tr('QT_API={value0}: only the explicitly selected backend can be installed.', value0=selected_api))
    print(tr('Compatibility depends on Python/platform; solver checks and pins remain active.'))
    try:
        answer = input(tr('Choose Qt backend [0]: ')).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    choices = {str(index): api for index, api in enumerate(options, 1)}
    choices.update({api: api for api in options})
    selected = choices.get(answer)
    if selected is None:
        action["reason"] = "no valid explicit Qt selection; packages kept"
        return failures, [action]
    _, conda_package, pip_package = QT_BINDINGS[selected]
    action["selected"] = selected
    action["package"] = conda_package if in_conda_env else pip_package
    try:
        if in_conda_env:
            print(tr('Checking Qt backend compatibility via {value0}; waiting for the solver...', value0=manager), flush=True)
            preview = _preview_qt_install(env_path, manager, channels, conda_package)
            action["preview"] = preview
            print(tr('Qt installation plan:'))
            for record in preview["LINK"]:
                print(f"  + {record['name']}=={record['version']}")
            installed = conda_install(env_path, [conda_package], manager, channels,
                                      ignore_pinned=False, force_reinstall=False)
        else:
            command = _get_pip_cmd(python_exe, use_uv, ["install", "--only-binary=:all:", pip_package])
            installed = run_cmd_live(command) == 0
        action["install_ok"] = bool(installed)
    except QtSolveError as exc:
        action["reason"] = str(exc)
        if exc.diagnostic is not None:
            action["diagnostic"] = exc.diagnostic
        print(tr('Qt backend installation not performed: {value0}', value0=tr(exc)))
        if in_conda_env:
            outcomes, recovery = _offer_qt_recovery(
                affected, env_path=env_path, python_exe=python_exe, manager=manager,
                channels=channels, package=conda_package, use_uv=use_uv,
            )
            if outcomes and all(outcome.get("removed") for outcome in outcomes):
                action.update(ok=True, resolution="consumers_removed")
            replacements = {id(original): outcome for original, outcome in zip(affected, outcomes)}
            return [replacements.get(id(failure), failure) for failure in failures], [action, recovery]
        return failures, [action]
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        action["reason"] = str(exc)
        print(tr('Qt backend installation not performed: {value0}', value0=tr(exc)))
        return failures, [action]
    if not installed:
        action["reason"] = "Qt backend install failed; consumers kept"
        return failures, [action]
    remaining = []
    checks = []
    for failure in failures:
        if failure not in affected:
            remaining.append(failure)
            continue
        ok, error = check_import(failure["import"], python_exe)
        checks.append({"import": failure["import"], "ok": ok, "error": error})
        if not ok:
            remaining.append(dict(failure, error=error))
    action["checks"] = checks
    action["ok"] = all(check["ok"] for check in checks)
    return remaining, [action]
