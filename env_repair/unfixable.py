from .i18n import tr
import json
import re
import subprocess
import sys
from pathlib import Path

from .conda_ops import conda_remove
from .naming import normalize_name
from .pip_ops import pip_uninstall
from .subprocess_utils import run_cmd_capture


PROTECTED_PACKAGES = {
    "python", "python-abi", "conda", "mamba", "micromamba", "libmamba", "libmambapy",
    "conda-libmamba-solver", "pip", "setuptools", "wheel", "env-repair",
}


def _conda_plan(env_path, packages, manager):
    if manager not in {"conda", "mamba", "micromamba"}:
        raise ValueError("no supported conda manager for removal preview")
    code, output, error = run_cmd_capture(
        [manager, "remove", "--dry-run", "--json", "-y", "-p", str(env_path)] + packages,
        timeout=120,
    )
    if code:
        raise ValueError(error or output or "removal preview failed")
    data = json.loads(output)
    if not isinstance(data, dict) or data.get("success") is not True:
        raise ValueError("removal preview did not report success")
    actions = data.get("actions")
    if not isinstance(actions, dict) or actions.get("LINK"):
        raise ValueError("unsupported removal transaction")
    removed = actions.get("UNLINK")
    if not isinstance(removed, list) or not removed:
        raise ValueError("removal preview contains no package records")
    records = []
    for record in removed:
        if not isinstance(record, dict) or not isinstance(record.get("name"), str) or not isinstance(record.get("version"), str):
            raise ValueError("incomplete removal package record")
        records.append({"name": record["name"], "version": record["version"]})
    return records


def _pip_plan(python_exe, packages):
    script = (
        "import importlib.metadata as metadata,json; "
        "print(json.dumps([{'name':dist.metadata['Name'],'version':dist.version,"
        "'requires':list(dist.requires or [])} for dist in metadata.distributions()]))"
    )
    result = subprocess.run([python_exe, "-c", script], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(result.stderr or "cannot inspect pip dependents")
    records = json.loads(result.stdout)
    if not isinstance(records, list):
        raise ValueError("invalid pip dependency inventory")
    requested = {normalize_name(name) for name in packages}
    removed = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("name"), str) or not isinstance(record.get("version"), str):
            raise ValueError("incomplete pip dependency inventory")
        if normalize_name(record["name"]) in requested:
            removed.append({"name": record["name"], "version": record["version"]})
            continue
        requirements = record.get("requires")
        if not isinstance(requirements, list):
            raise ValueError("missing pip dependency requirements")
        for requirement in requirements:
            if not isinstance(requirement, str):
                raise ValueError("invalid pip dependency requirement")
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
            if not match:
                raise ValueError("unreadable pip dependency requirement")
            if normalize_name(match.group(1)) in requested:
                raise ValueError(f"pip removal would leave a dependent installed: {record['name']}; keeping packages")
    return removed


def remove_unfixable(candidates, *, env_path, python_exe, manager, force=False, interactive=True, use_uv=False, label="Unresolved packages", allow_dependents=True):
    actions = []
    removed_names = set()
    for kind in ("conda", "pip"):
        items = [item for item in candidates if item.get("installer") == kind]
        if not items:
            continue
        packages = sorted({item["package"] for item in items})
        action = {"action": "remove_unfixable", "installer": kind, "requested": packages,
                  "approved": False, "ok": False, "removed": []}
        actions.append(action)
        try:
            if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) for name in packages):
                raise ValueError("invalid removal package name")
            records = _conda_plan(env_path, packages, manager) if kind == "conda" else _pip_plan(python_exe, packages)
            names = {normalize_name(record["name"]) for record in records}
            if not {normalize_name(name) for name in packages}.issubset(names):
                raise ValueError("requested packages missing from removal preview")
            if names & PROTECTED_PACKAGES:
                raise ValueError("removal would affect protected runtime/manager packages; keeping packages")
            if not allow_dependents:
                if names - {normalize_name(name) for name in packages}:
                    raise ValueError("removal would affect additional dependent packages; keeping packages")
                if kind == "conda":
                    _pip_plan(python_exe, [item.get("distribution") or item["package"] for item in items])
            action["preview"] = records
            print(f"\n{tr(label)}:")
            for item in items:
                print(f"  {item['package']}: {item['error']}")
            print(tr('Removal plan (including any conda dependents):'))
            for record in records:
                print(f"  - {record['name']}=={record['version']}")
            approved = force
            if not approved and interactive and sys.stdin.isatty():
                try:
                    approved = input(tr('Remove ALL packages listed above? [y/N] ')).strip().lower() in {"y", "yes", "j", "ja"}
                except (EOFError, KeyboardInterrupt):
                    approved = False
            action["approved"] = approved
            if not approved:
                action["reason"] = "removal declined or no explicit permission; -y and auto-yes do not authorize removal"
                continue
            ok = conda_remove(env_path, packages, manager) if kind == "conda" else pip_uninstall(python_exe, packages, use_uv=use_uv)
            if ok and any(item.get("dist_path") and Path(item["dist_path"]).exists() for item in items):
                ok = False
                action["reason"] = "installer returned success but consumer metadata still exists; removal not verified"
            action["ok"] = bool(ok)
            if ok:
                action["removed"] = records
                removed_names.update(names)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            action["reason"] = str(exc)
            print(tr('Cannot safely remove {value0}: {value1}', value0=', '.join(packages), value1=tr(exc)))
    return removed_names, actions
