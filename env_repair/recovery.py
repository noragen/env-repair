import argparse
import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import zipfile
from pathlib import Path
from uuid import uuid4

from .i18n import tr
from .naming import normalize_name
from .package_sandbox import _cleanup_workspace, _fingerprint, _inventory
from .subprocess_utils import OperationInterrupted, run_cmd_capture, run_cmd_live
from .unfixable import PROTECTED_PACKAGES


ENTRY = """import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from env_repair.recovery import worker_main
raise SystemExit(worker_main(sys.argv[1], sys.argv[2]))
"""

TOOL_QUERY = """import importlib.metadata as metadata, json
matches = [dist for dist in metadata.distributions() if dist.metadata.get('Name', '').lower().replace('_', '-') == 'env-repair']
if len(matches) > 1:
    raise RuntimeError('ambiguous env-repair distribution metadata')
dist = matches[0] if matches else None
print(json.dumps(None if dist is None else {'version': dist.version, 'metadata': str(dist._path),
    'root': str(dist.locate_file('')), 'files': [str(path) for path in (dist.files or [])]}))
"""


def _tool_identity(plan):
    code, output, error = run_cmd_capture([plan["source_python"], "-I", "-c", TOOL_QUERY],
                                           env=_original_environment(plan), timeout=60)
    if code:
        raise ValueError(error or "cannot inspect original env-repair installation")
    identity = json.loads(output)
    if identity is not None and (not isinstance(identity, dict) or not identity.get("version")):
        raise ValueError("invalid env-repair distribution identity")
    return identity


def _backup_tool(plan, folder):
    identity = _tool_identity(plan)
    if identity is None:
        return None
    records = [record for record in plan["source_inventory"]["records"] if normalize_name(record["name"]) == "env-repair"]
    if records:
        return {"version": identity["version"], "installer": "conda"}
    root = Path(identity["root"]).resolve()
    metadata = Path(identity["metadata"]).resolve()
    source = Path(plan["source"]).resolve()
    if not root.is_relative_to(source) or metadata.parent != root:
        raise ValueError("env-repair backup must belong to the original environment")
    wheel_metadata = (metadata / "WHEEL").read_text(encoding="utf-8")
    if "Root-Is-Purelib: true" not in wheel_metadata or "Tag: py3-none-any" not in wheel_metadata:
        raise ValueError("env-repair preservation requires a pure Python wheel")
    archive = folder / ("env_repair-" + identity["version"] + "-py3-none-any.whl")
    entries = {}
    for filename in identity["files"]:
        path = (root / filename).resolve()
        if not path.is_relative_to(root):
            continue
        relative = path.relative_to(root)
        if relative.parts[0] not in {"env_repair", metadata.name} or "__pycache__" in relative.parts:
            continue
        if path.name in {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}:
            continue
        entries[relative.as_posix()] = path.read_bytes()
    if "env_repair/cli.py" not in entries or metadata.name + "/entry_points.txt" not in entries:
        raise ValueError("incomplete original env-repair installation; cannot preserve CLI")
    import csv
    import io

    record = io.StringIO(newline="")
    writer = csv.writer(record)
    for filename, content in entries.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")
        writer.writerow([filename, "sha256=" + digest, len(content)])
    writer.writerow([metadata.name + "/RECORD", "", ""])
    entries[metadata.name + "/RECORD"] = record.getvalue().encode("utf-8")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for filename, content in entries.items():
            wheel.writestr(filename, content)
    return {"version": identity["version"], "installer": "pip", "wheel": str(archive),
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}


def _preserve_tool(plan, *, restore=True):
    expected = plan.get("original_tool")
    if not expected:
        return
    current = _tool_identity(plan)
    launcher = Path(plan["source"]) / ("Scripts/env-repair.exe" if os.name == "nt" else "bin/env-repair")
    if current is None or current["version"] != expected["version"] or not launcher.is_file():
        if not restore or expected["installer"] != "pip":
            raise ValueError("original env-repair version was not preserved")
        wheel = Path(expected["wheel"])
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != expected["sha256"]:
            raise ValueError("env-repair backup checksum mismatch")
        command = [plan["source_python"], "-I", "-m", "pip", "--isolated", "install", "--no-index",
                   "--no-deps", "--force-reinstall", str(wheel)]
        if run_cmd_live(command, env=_original_environment(plan), wait_on_interrupt=True):
            raise ValueError("original env-repair restoration failed")
        current = _tool_identity(plan)
        if current is None or current["version"] != expected["version"]:
            raise ValueError("original env-repair version was not preserved")
    for command in ([plan["source_python"], "-I", "-m", "env_repair", "--help"], [str(launcher), "--help"]):
        code, _, error = run_cmd_capture(command, env=_original_environment(plan), timeout=60)
        if code:
            raise ValueError(error or "original env-repair CLI cannot start")


def _activation_files(source):
    source = Path(source).resolve()
    for directory in ("condabin", "Scripts", "bin", "etc/profile.d", "etc/fish/conf.d", "shell/condabin"):
        folder = source / directory
        if folder.is_symlink():
            raise ValueError("activation directory must not be a symlink")
        for path in folder.glob("*"):
            if path.is_file() and path.suffix.lower() in {".bat", ".cmd", ".ps1", ".psm1", ".sh", ".fish", ".xsh", ""}:
                if path.is_symlink() or not path.resolve().is_relative_to(source):
                    raise ValueError("activation file escapes original environment")
                if path.stat().st_size <= 1024 * 1024:
                    yield path


def _snapshot_activation(source):
    return {str(path.relative_to(source)): path.read_bytes().hex() for path in _activation_files(source)}


def _restore_activation(plan):
    source = Path(plan["source"]).resolve()
    temporary = [plan.get("worker_workspace"), str(Path(plan["entry"]).parent) if plan.get("entry") else None]
    needles = [str(value).replace("\\", "/").lower() for value in temporary if value]
    restored = []
    for path in _activation_files(source):
        content = path.read_bytes()
        text = content.decode("utf-8", errors="replace").replace("\\", "/").lower()
        if not any(needle in text for needle in needles):
            continue
        relative = str(path.relative_to(source))
        previous = plan.get("activation_snapshot", {}).get(relative)
        if previous is None:
            raise ValueError("temporary installer reference in new activation file: " + str(path))
        original = bytes.fromhex(previous)
        if any(needle in original.decode("utf-8", errors="replace").replace("\\", "/").lower() for needle in needles):
            raise ValueError("activation backup already contains temporary installer references")
        replacement = path.with_name(path.name + ".env-repair-restore")
        replacement.write_bytes(original)
        os.replace(replacement, path)
        restored.append(relative)
    return restored


def _activation_variable(key):
    key = key.upper()
    return key in {"CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_SHLVL", "CONDA_PROMPT_MODIFIER",
                   "CONDA_EXE", "CONDA_EXES", "CONDA_PYTHON_EXE", "CONDA_ROOT", "CONDA_TARGET_PREFIX",
                   "_CONDA_EXE", "_CONDA_ROOT", "_CE_M", "_CE_CONDA", "MAMBA_ROOT_PREFIX",
                   "MAMBA_TARGET_PREFIX", "MAMBA_EXE", "MAMBA_BAT"} or key.startswith("CONDA_PREFIX_")


def _without_activation(environment):
    environment = dict(environment)
    for key in list(environment):
        if _activation_variable(key) or key.upper() in {"PYTHONHOME", "PYTHONPATH"}:
            environment.pop(key)
    return environment


def _installer_environment(plan):
    environment = _without_activation(os.environ)
    prefix = Path(plan["worker_workspace"]) / "environment"
    environment["MAMBA_ROOT_PREFIX"] = str(prefix)
    environment["PATH"] = os.pathsep.join([str(prefix / "Library/bin"), str(prefix / "Scripts"), str(prefix / "bin"), plan["original_path"]])
    return environment


def _original_environment(plan):
    environment = _without_activation(os.environ)
    environment.update(plan.get("original_activation_environment", {}))
    environment["PATH"] = plan["original_path"]
    return environment


def successful_sandboxes(actions):
    found = []
    for action in actions or []:
        report = action.get("sandbox")
        if isinstance(report, dict) and report.get("ok") and report not in found:
            found.append(report)
        for attempt in action.get("attempts") or []:
            report = attempt.get("sandbox") if isinstance(attempt, dict) else None
            if isinstance(report, dict) and report.get("ok") and report not in found:
                found.append(report)
    return found


def _transaction(manager, source, specs, channels):
    command = [manager, "install", "-y", "-p", str(source)]
    for channel in channels:
        command.extend(["-c", channel])
    command.extend(specs)
    code, output, error = run_cmd_capture(command + ["--dry-run", "--json"], timeout=180)
    try:
        payload = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise ValueError(error or "invalid original-environment transaction preview") from exc
    if code or not isinstance(payload, dict) or payload.get("success") is not True:
        from .qt_repair import _solver_failure

        raise _solver_failure(output, error)
    actions = payload.get("actions")
    if not isinstance(actions, dict):
        raise ValueError("missing original-environment transaction preview")
    normalized = {}
    for kind in ("LINK", "UNLINK"):
        records = actions.get(kind, [])
        if not isinstance(records, list):
            raise ValueError("invalid original-environment transaction preview")
        normalized[kind] = []
        for record in records:
            if not isinstance(record, dict) or not all(isinstance(record.get(key), str) for key in ("name", "version")):
                raise ValueError("incomplete original-environment transaction record")
            normalized[kind].append({"name": record["name"], "version": record["version"],
                                     "build": record.get("build") or record.get("build_string") or ""})
    linked = {normalize_name(record["name"]) for record in normalized["LINK"]}
    unlinked = {normalize_name(record["name"]) for record in normalized["UNLINK"]}
    tool_before = [record for record in normalized["UNLINK"] if normalize_name(record["name"]) == "env-repair"]
    tool_after = [record for record in normalized["LINK"] if normalize_name(record["name"]) == "env-repair"]
    if tool_before != tool_after or (unlinked - linked) & PROTECTED_PACKAGES:
        raise ValueError("transaction would remove protected packages or change env-repair")
    signature = hashlib.sha256(json.dumps({kind: sorted(records, key=lambda record: record["name"])
                                          for kind, records in normalized.items()}, sort_keys=True).encode()).hexdigest()
    return command, normalized, signature


def _migration_specs(report):
    python_spec = report.get("python_spec", "")
    if not isinstance(python_spec, str) or not re.fullmatch(r"python=3\.[0-9]+", python_spec):
        raise ValueError("invalid migration Python requirement")
    overrides = list(report.get("conda_specs") or [])
    names = set()
    for spec in overrides:
        if not isinstance(spec, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*(?:[=<>!~]+[A-Za-z0-9.*+!_-]+(?:,[=<>!~]+[A-Za-z0-9.*+!_-]+)*)?", spec):
            raise ValueError("invalid migration target requirement")
        name = normalize_name(re.split(r"[=<>!~]", spec)[0])
        if name in {"python", "env-repair"}:
            raise ValueError("protected migration target requirement")
        names.add(name)
    roots = []
    for record in (report.get("inventory") or {}).get("records", []):
        name = record["name"]
        if normalize_name(name) in names | {"python", "env-repair"}:
            continue
        roots.append(name + "=" + record["version"])
    return [python_spec] + sorted(set(roots + overrides))


def _assert_tested_combination(tested, inventory, transaction):
    projected = {normalize_name(record["name"]): record for record in inventory["records"]}
    for record in transaction.get("UNLINK", []):
        projected.pop(normalize_name(record["name"]), None)
    for record in transaction.get("LINK", []):
        projected[normalize_name(record["name"])] = record
    differences = []
    for expected in tested:
        name = normalize_name(expected["name"])
        if name == "env-repair":
            continue
        actual = projected.get(name)
        expected_build = expected.get("build") or expected.get("build_string")
        if not expected_build:
            raise ValueError("tested migration lock has no conda build identity")
        if actual is None or actual["version"] != expected["version"] or (actual.get("build") or actual.get("build_string")) != expected_build:
            differences.append(expected["name"])
    if differences:
        raise ValueError("original transaction differs from tested packages; a new sandbox test is required: " + ", ".join(sorted(differences)))


def offer_installation(report, *, options, interactive=True):
    result = {"action": "sandbox_installation_offer", "approved": False, "ok": False}
    if not interactive or not sys.stdin.isatty() or os.environ.get("ENV_REPAIR_REVERIFY") == "1":
        result["reason"] = "installation requires separate interactive confirmation; -y/-f do not approve migration"
        return result
    if not report.get("ok") or not report.get("tested_conda_packages") or report.get("scope") != "target-packages":
        result["reason"] = "no complete successful sandbox report available"
        return result
    source = Path(report["source"]).resolve()
    python = source / ("python.exe" if os.name == "nt" else "bin/python")
    try:
        specs = _migration_specs(report)
        inventory = _inventory(source, str(python))
        print(tr("Preparing installation of the tested combination in the ORIGINAL environment; checking all installed conda constraints..."), flush=True)
        command, transaction, signature = _transaction(report["manager"], source, specs, report["channels"])
        _assert_tested_combination(report["tested_conda_packages"], inventory, transaction)
        print(tr("Original-environment transaction (including Python changes and dependencies):"))
        for kind, records in transaction.items():
            for record in records:
                print(f"  {kind}: {record['name']} {record['version']} {record['build']}")
        print(tr("A Python change can affect unrelated pip/local extensions. They are not rebuilt automatically. A snapshot and a complete one-shot recheck will follow."))
        if input(tr("Apply this transaction to the ORIGINAL environment and rerun one-shot with the same options? [y/N] ")).strip().lower() not in {"y", "yes", "j", "ja"}:
            result["reason"] = "original-environment installation declined"
            print(tr("Original installation declined; the original environment is unchanged."))
            return result
        folder = Path(report["report_path"]).resolve().parent
        plan_path = folder / "application-plan.json"
        plan = {"intent_version": 1, "source": str(source), "source_python": str(python), "specs": specs, "channels": report["channels"],
                "tested_conda_packages": report["tested_conda_packages"],
                "manager": report["manager"], "transaction": transaction, "transaction_signature": signature,
                "source_fingerprint": _fingerprint(inventory), "source_inventory": inventory,
                "options": options, "python_spec": report["python_spec"],
                "pip_specs": [item["name"] + "==" + item["version"] for item in report["tested_packages"] if item["kind"] == "pypi"],
                "original_path": os.environ.get("PATH", ""), "parent_pid": os.getpid()}
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        result.update(approved=True, plan_path=str(plan_path), pending=True)
    except (OSError, ValueError, EOFError, subprocess.TimeoutExpired) as exc:
        result["reason"] = str(exc)
        print(tr("Original installation could not be prepared: {value0}", value0=tr(exc)))
    return result


def launch_installation(plan_path):
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("intent_version") != 1:
        raise ValueError("legacy migration plan must be recreated to avoid exact dependency history entries")
    plan["original_tool"] = _backup_tool(plan, plan_path.parent)
    activation_snapshot = _snapshot_activation(Path(plan["source"]).resolve())
    workspace = Path(tempfile.mkdtemp(prefix="env-repair-apply-")).resolve()
    token = uuid4().hex
    (workspace / ".env-repair-owned").write_text(token, encoding="utf-8")
    plan.update(worker_workspace=str(workspace), worker_token=token,
                activation_snapshot=activation_snapshot,
                original_activation_environment={key: value for key, value in os.environ.items()
                                                 if _activation_variable(key)})
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    worker_started = False
    try:
        command = [plan["manager"], "create", "-y", "-p", str(workspace / "environment"), plan["python_spec"], "mamba"]
        for channel in plan["channels"]:
            command.extend(["-c", channel])
        print(tr("Preparing an independent installer so the original Python can be safely replaced..."), flush=True)
        bootstrap_code = run_cmd_live(command, env=_installer_environment(plan), wait_on_interrupt=True)
        if bootstrap_code == 130:
            raise OperationInterrupted(command)
        if bootstrap_code:
            raise ValueError("independent installer creation failed; original unchanged")
        runner = plan_path.parent / ("application-runner-" + token)
        runner.mkdir()
        (runner / ".env-repair-owned").write_text(token, encoding="utf-8")
        shutil.copytree(Path(__file__).parent, runner / "env_repair", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        entry = runner / "entry.py"
        entry.write_text(ENTRY, encoding="utf-8")
        plan["entry"] = str(entry)
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        python = workspace / "environment" / ("python.exe" if os.name == "nt" else "bin/python")
        print(tr("Waiting for installation, original-environment verification and cleanup. Live log: {value0}", value0=str(plan_path.parent)), flush=True)
        sys.stdout.flush()
        sys.stderr.flush()
        worker_started = True
        code = run_cmd_live([str(python), "-u", "-I", str(entry), "apply", str(plan_path)],
                            env=_installer_environment(plan), wait_on_interrupt=True)
        result_path = plan_path.parent / "application.json"
        if not result_path.is_file():
            failure = {"ok": False, "exit_code": code or 1, "phase": "independent-installer",
                       "error": "installer exited without a final report; workspace retained for recovery"}
            result_path.write_text(json.dumps(failure, indent=2), encoding="utf-8")
            print(tr("Independent installer could not be removed safely: {value0}", value0=workspace), flush=True)
            return failure["exit_code"]
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("worker_token") != token:
            failure = {"ok": False, "exit_code": code or 1, "phase": "independent-installer",
                       "error": "installer returned no matching final report; workspace retained for recovery"}
            result_path.write_text(json.dumps(failure, indent=2), encoding="utf-8")
            print(tr("Independent installer could not be removed safely: {value0}", value0=workspace), flush=True)
            return failure["exit_code"]
        if result.get("cleanup_error"):
            print(tr("Original installation failed: {value0}", value0=tr(result["cleanup_error"])), flush=True)
            print(tr("Independent installer could not be removed safely: {value0}", value0=workspace), flush=True)
            return result.get("exit_code") or 1
        cleanup_code = run_cmd_live([plan["source_python"], "-u", "-I", str(entry), "cleanup", str(plan_path)],
                                    env=_original_environment(plan), wait_on_interrupt=True)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        return code or cleanup_code or (0 if result.get("ok") else result.get("exit_code") or 1)
    except BaseException as exc:
        environment = _original_environment(plan)
        os.environ.clear()
        os.environ.update(environment)
        try:
            _restore_activation(plan)
        except (OSError, ValueError) as activation_error:
            cleanup = {"ok": False, "error": str(activation_error), "retained_for_recovery": True}
        else:
            if worker_started:
                cleanup = {"ok": False, "retained_for_recovery": True}
            else:
                cleanup = _cleanup_workspace(workspace, source=plan["source"], token=token, manager=plan["manager"])
        failure = {"ok": False, "exit_code": 130 if isinstance(exc, (KeyboardInterrupt, OperationInterrupted)) else 1,
                   "phase": "independent-installer" if worker_started else "independent-installer-start", "error": str(exc), "cleanup": cleanup}
        try:
            (plan_path.parent / "application.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
        except OSError:
            pass
        if not cleanup["ok"]:
            print(tr("Independent installer could not be removed safely: {value0}", value0=workspace))
        raise


def _worker_manager(workspace):
    prefix = Path(workspace) / "environment"
    candidates = [prefix / "Library/bin/mamba.exe", prefix / "Scripts/mamba.exe", prefix / "bin/mamba"]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise ValueError("independent mamba executable not found")


def _verify(plan, plan_path):
    from .cli import _normalize_single_env_arg, one_shot
    from .discovery import discover_envs, select_envs

    options = argparse.Namespace(**plan["options"])
    if Path(sys.executable).resolve() != Path(plan["source_python"]).resolve() or Path(sys.prefix).resolve() != Path(plan["source"]).resolve():
        raise ValueError("verification must use the newly installed original Python")
    envs, base, _ = discover_envs(show_json_output=False)
    targets = select_envs(envs, [_normalize_single_env_arg(options)], base)
    if len(targets) != 1 or Path(targets[0]).resolve() != Path(plan["source"]).resolve():
        raise ValueError("original one-shot parameters resolve to a different environment; refusing verification")
    actual_version = ".".join(map(str, sys.version_info[:2]))
    if plan.get("python_spec") and plan["python_spec"] != "python=" + actual_version:
        raise ValueError("original Python version does not match the approved migration")
    os.environ["ENV_REPAIR_REVERIFY"] = "1"
    print(tr("Original verification uses Python {value0}: {value1}", value0=".".join(map(str, sys.version_info[:3])), value1=sys.executable), flush=True)
    result = one_shot(options)
    result["verification_runtime"] = {"executable": sys.executable, "prefix": sys.prefix, "version": list(sys.version_info[:3])}
    (plan_path.parent / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return int(result.get("exit_code", 0 if result.get("ok") else 1))


class _RecoveryOutput:
    def __init__(self, console, logfile, lock=None):
        self.console = console
        self.logfile = logfile
        self.lock = lock or threading.Lock()

    def write(self, value):
        with self.lock:
            self.logfile.write(value)
            self.logfile.flush()
        if self.console is not None:
            try:
                self.console.write(value)
                self.console.flush()
            except (OSError, UnicodeError):
                self.console = None
        return len(value)

    def flush(self):
        with self.lock:
            self.logfile.flush()
        if self.console is not None:
            try:
                self.console.flush()
            except (OSError, UnicodeError):
                self.console = None

    def isatty(self):
        return self.console is not None and self.console.isatty()


def worker_main(mode, filename):
    if mode == "verify":
        return _worker_main(mode, filename)
    log_path = Path(filename).resolve().parent / "application.log"
    with log_path.open("a", encoding="utf-8") as logfile:
        lock = threading.Lock()
        with contextlib.redirect_stdout(_RecoveryOutput(sys.stdout, logfile, lock)), contextlib.redirect_stderr(_RecoveryOutput(sys.stderr, logfile, lock)):
            return _worker_main(mode, filename)


def _worker_main(mode, filename):
    plan_path = Path(filename).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if mode == "verify":
        try:
            return _verify(plan, plan_path)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            result = {"ok": False, "exit_code": 1, "error": str(exc)}
            (plan_path.parent / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(tr("Original verification failed: {value0}", value0=tr(exc)))
            return 1
    if mode == "cleanup":
        result = json.loads((plan_path.parent / "application.json").read_text(encoding="utf-8"))
        try:
            _preserve_tool(plan, restore=False)
            result["activation_restored"] = sorted(set(result.get("activation_restored", []) + _restore_activation(plan)))
        except (OSError, ValueError) as exc:
            result.update(ok=False, exit_code=1, cleanup_error=str(exc))
            (plan_path.parent / "application.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            return 1
        result["cleanup"] = _cleanup_workspace(plan["worker_workspace"], source=plan["source"],
                                                token=plan["worker_token"], manager=plan["manager"])
        try:
            result["activation_restored"] = sorted(set(result["activation_restored"] + _restore_activation(plan)))
        except (OSError, ValueError) as exc:
            result.update(ok=False, exit_code=1, cleanup_error=str(exc))
        result["verification_ok"] = result["ok"]
        if not result["cleanup"]["ok"]:
            result.update(ok=False, exit_code=1)
            print(tr("Independent installer could not be removed safely: {value0}", value0=plan["worker_workspace"]))
        runner = Path(plan["entry"]).resolve().parent
        if runner.parent == plan_path.parent and runner.name == "application-runner-" + plan["worker_token"]:
            result["runner_cleanup"] = _cleanup_workspace(runner, source=plan["source"], token=plan["worker_token"], manager=plan["manager"])
        (plan_path.parent / "application.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(tr("Original-environment result: {value0}", value0=str(plan_path.parent / "application.json")))
        return result["exit_code"] if result["cleanup"]["ok"] else 1
    result = {"ok": False, "exit_code": 1, "worker_token": plan["worker_token"]}
    try:
        if Path(sys.prefix).resolve() != (Path(plan["worker_workspace"]) / "environment").resolve():
            raise ValueError("installer must run outside the original environment")
        manager = _worker_manager(plan["worker_workspace"])
        environment = _installer_environment(plan)
        os.environ.clear()
        os.environ.update(environment)
        _preserve_tool(plan, restore=False)
        inventory = _inventory(plan["source"], plan["source_python"])
        if _fingerprint(inventory) != plan["source_fingerprint"]:
            raise ValueError("source package inventory changed since approval; original unchanged")
        if plan.get("intent_version") != 1:
            raise ValueError("legacy migration plan must be recreated to avoid exact dependency history entries")
        command, transaction, signature = _transaction(manager, plan["source"], plan["specs"], plan["channels"])
        if signature != plan["transaction_signature"]:
            raise ValueError("original transaction changed since approval; new confirmation required")
        _assert_tested_combination(plan["tested_conda_packages"], inventory, transaction)
        for command_line, name in (([manager, "list", "-p", plan["source"], "--explicit"], "before-conda.txt"),
                                   ([plan["source_python"], "-m", "pip", "freeze", "--all"], "before-pip.txt")):
            code, output, error = run_cmd_capture(command_line, timeout=120)
            if code:
                raise ValueError(error or "pre-installation snapshot failed")
            (plan_path.parent / name).write_text(output, encoding="utf-8")
        try:
            installation_code = run_cmd_live(command, wait_on_interrupt=True)
        finally:
            result["activation_restored"] = _restore_activation(plan)
        if installation_code == 130:
            raise OperationInterrupted(command)
        if installation_code:
            raise ValueError("original-environment installation failed; inspect snapshot")
        _assert_tested_combination(plan["tested_conda_packages"], _inventory(plan["source"], plan["source_python"]), {})
        if plan["pip_specs"]:
            command = [plan["source_python"], "-I", "-m", "pip", "--isolated", "install", "--index-url", "https://pypi.org/simple",
                       "--only-binary=:all:", "--no-deps", "--force-reinstall"] + plan["pip_specs"]
            if run_cmd_live(command, wait_on_interrupt=True):
                raise ValueError("original-environment wheel installation failed")
        _preserve_tool(plan)
        print(tr("Installation finished. Rerunning the same one-shot using the newly installed ORIGINAL Python..."), flush=True)
        env = dict(_original_environment(plan), ENV_REPAIR_REVERIFY="1")
        code = run_cmd_live([plan["source_python"], "-u", "-I", plan["entry"], "verify", str(plan_path)], env=env, wait_on_interrupt=True)
        _preserve_tool(plan)
        result.update(ok=code == 0, exit_code=code, verification=str(plan_path.parent / "verification.json"))
        result["original_tool"] = {"ok": True, "version": (plan.get("original_tool") or {}).get("version")}
        if code == 0:
            current = _inventory(plan["source"], plan["source_python"])
            installed = {normalize_name(item["name"]) for item in current["packages"]}
            verification = json.loads((plan_path.parent / "verification.json").read_text(encoding="utf-8"))
            steps = verification.get("report", {}).get("steps", {})
            fix = steps.get("verify_imports", {}).get("report", {}).get("fix") or {}
            removed = {normalize_name(item["name"]) for action in fix.get("actions", [])
                       if action.get("action") == "remove_unfixable" and action.get("ok") for item in action.get("removed", [])}
            missing = [item["name"] for item in plan["source_inventory"]["packages"]
                       if item["kind"] in {"pypi", "local"} and normalize_name(item["name"]) not in installed | removed]
            if missing:
                result.update(ok=False, exit_code=1, missing_original_distributions=missing)
                print(tr("Original pip/local distributions are missing after migration: {value0}", value0=", ".join(missing)))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result["error"] = str(exc)
        print(tr("Original installation failed: {value0}", value0=tr(exc)))
    except (KeyboardInterrupt, OperationInterrupted) as exc:
        result.update(ok=False, exit_code=130, interrupted=True, error=str(exc))
    except Exception as exc:
        result.update(ok=False, error=str(exc), traceback=traceback.format_exc())
    try:
        _preserve_tool(plan)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result.update(ok=False, exit_code=1, cleanup_error=str(exc))
    (plan_path.parent / "application.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    try:
        result["activation_restored"] = sorted(set(result.get("activation_restored", []) + _restore_activation(plan)))
    except (OSError, ValueError) as exc:
        result.update(ok=False, exit_code=1, cleanup_error=str(exc))
        (plan_path.parent / "application.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 1
    try:
        code, _, _ = run_cmd_capture([plan["source_python"], "-I", "-c", "pass"], timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        code = 1
    if code:
        result["cleanup_error"] = "original Python cannot start; independent installer retained for recovery"
        (plan_path.parent / "application.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(tr("Independent installer could not be removed safely: {value0}", value0=plan["worker_workspace"]))
        return 1
    return result["exit_code"]
