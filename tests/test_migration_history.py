import ast
import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from env_repair import recovery
from env_repair.package_sandbox import _cleanup_workspace
from env_repair.subprocess_utils import run_cmd_live


class TestMigrationIntent(unittest.TestCase):
    def report(self):
        return {"ok": True, "scope": "target-packages", "source": "original", "python_spec": "python=3.14", "manager": "mamba",
                "channels": ["conda-forge"], "conda_specs": ["pyqt6"],
                "inventory": {"records": [{"name": "qtpy", "version": "2.4.3", "build": "noarch"}]},
                "tested_packages": [], "tested_conda_packages": [
                    {"name": "python", "version": "3.14.10", "build": "cp314"},
                    {"name": "python-gil", "version": "3.14.10", "build": "gil314"},
                    {"name": "python_abi", "version": "3.14", "build": "abi314"},
                    {"name": "pyqt6", "version": "6.11", "build": "qt314"},
                    {"name": "qtpy", "version": "2.4.3", "build": "noarch"}]}

    def test_only_target_requirements_are_explicit_not_tested_transitive_builds(self):
        specs = recovery._migration_specs(self.report())
        self.assertEqual(specs, ["python=3.14", "pyqt6", "qtpy=2.4.3"])
        self.assertFalse(any("python-gil" in spec or "python_abi" in spec or "cp314" in spec for spec in specs))

    def test_explicit_target_override_is_preserved_and_env_repair_excluded(self):
        report = self.report()
        report["conda_specs"] = ["qtpy>=2.5", "pyqt6>=6,<7"]
        report["inventory"]["records"].append({"name": "env-repair", "version": "0.2.8"})
        self.assertEqual(recovery._migration_specs(report), ["python=3.14", "pyqt6>=6,<7", "qtpy>=2.5"])

    def test_unsafe_specs_are_rejected_not_forwarded_to_manager(self):
        for spec in ("--no-pin", "https://example.test/qt.conda", "python=3.15", "env-repair"):
            report = self.report()
            report["conda_specs"] = [spec]
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                recovery._migration_specs(report)

    def test_projected_final_state_includes_unchanged_dependencies(self):
        tested = self.report()["tested_conda_packages"]
        inventory = {"records": tested[:3]}
        recovery._assert_tested_combination(tested, inventory, {"LINK": tested[3:]})
        recovery._assert_tested_combination(tested, {"records": tested}, {})

    def test_missing_changed_versions_and_changed_builds_require_a_new_test(self):
        tested = self.report()["tested_conda_packages"]
        for changes in ({"UNLINK": [tested[0]]},
                        {"LINK": [dict(tested[0], version="3.14.11")]},
                        {"LINK": [dict(tested[1], build="untested-build")]}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "new sandbox test"):
                recovery._assert_tested_combination(tested, {"records": tested}, changes)

    def test_mismatch_blocks_offer_before_confirmation_and_original_mutation(self):
        with patch.object(recovery.sys.stdin, "isatty", return_value=True), \
                patch.dict(os.environ, {"ENV_REPAIR_REVERIFY": ""}), \
                patch.object(recovery, "_inventory", return_value={"records": []}), \
                patch.object(recovery, "_transaction", return_value=(["mamba"], {}, "signature")), \
                patch("builtins.input") as prompt, patch.object(recovery, "run_cmd_live") as install, \
                contextlib.redirect_stdout(io.StringIO()):
            result = recovery.offer_installation(self.report(), options={})
        self.assertFalse(result["approved"])
        self.assertIn("new sandbox test", result["reason"])
        prompt.assert_not_called()
        install.assert_not_called()

    def test_offer_saves_intent_separately_from_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report()
            report["source"] = directory
            report["report_path"] = str(Path(directory) / "migration.json")
            tested = report["tested_conda_packages"]
            with patch.object(recovery.sys.stdin, "isatty", return_value=True), \
                    patch.dict(os.environ, {"ENV_REPAIR_REVERIFY": ""}), \
                    patch.object(recovery, "_inventory", return_value={"records": tested}), \
                    patch.object(recovery, "_transaction", return_value=(["mamba"], {}, "signature")) as solve, \
                    patch("builtins.input", return_value="yes"), contextlib.redirect_stdout(io.StringIO()):
                result = recovery.offer_installation(report, options={})
            self.assertTrue(result["approved"])
            plan = json.loads(Path(result["plan_path"]).read_text())
            self.assertEqual(plan["specs"], ["python=3.14", "pyqt6", "qtpy=2.4.3"])
            self.assertEqual(plan["tested_conda_packages"], tested)
            self.assertEqual(solve.call_args.args[2], plan["specs"])

    def test_old_exact_dependency_plans_cannot_be_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text('{"specs":["python-gil=3.14.10=build"]}', encoding="utf-8")
            with patch.object(recovery, "run_cmd_live") as install, self.assertRaisesRegex(ValueError, "legacy migration plan"):
                recovery.launch_installation(path)
            install.assert_not_called()

    def test_legacy_full_environment_report_cannot_promote_all_records_to_roots(self):
        report = self.report()
        report["scope"] = "full-environment"
        with patch.object(recovery.sys.stdin, "isatty", return_value=True), \
                patch.dict(os.environ, {"ENV_REPAIR_REVERIFY": ""}), \
                patch.object(recovery, "_inventory") as inventory:
            self.assertFalse(recovery.offer_installation(report, options={})["approved"])
        inventory.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows DLL hardlink isolation")
class TestHistoryDLLIsolation(unittest.TestCase):
    def test_shared_dll_is_rejected_but_private_copy_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            prefix = root / "environment"
            cache.mkdir()
            prefix.mkdir()
            cached = cache / "library.dll"
            installed = prefix / "library.dll"
            cached.write_bytes(b"DLL identity fixture")
            os.link(cached, installed)
            records = [{"link": {"source": str(cache)}, "files": ["library.dll"]}]
            checker = TestRealManagerHistory()
            with self.assertRaisesRegex(AssertionError, "shares a locked cache/base file"):
                checker.assert_private_dlls(prefix, records)
            installed.unlink()
            shutil.copy2(cached, installed)
            checker.assert_private_dlls(prefix, records)
            self.assertEqual(installed.read_bytes(), cached.read_bytes())

    def test_missing_dll_pairs_do_not_claim_successful_isolation(self):
        checker = TestRealManagerHistory()
        with self.assertRaisesRegex(AssertionError, "no installed DLL/cache pairs"):
            checker.assert_private_dlls(Path("unused"), [])


@unittest.skipUnless(os.environ.get("ENV_REPAIR_RUN_MANAGER_TESTS") == "1", "opt-in isolated manager integration test")
class TestRealManagerHistory(unittest.TestCase):
    def assert_private_dlls(self, prefix, records):
        if os.name != "nt":
            return
        checked = 0
        for record in records:
            cache = (record.get("link") or {}).get("source")
            if not cache:
                continue
            for filename in record.get("files", []):
                if not filename.lower().endswith(".dll"):
                    continue
                installed = prefix / filename
                cached = Path(cache) / filename
                if installed.is_file() and cached.is_file():
                    self.assertFalse(os.path.samefile(installed, cached), "test DLL shares a locked cache/base file: " + str(installed))
                    checked += 1
        self.assertGreater(checked, 0, "no installed DLL/cache pairs checked")

    def test_transitive_packages_are_not_recorded_as_user_requirements(self):
        tested_managers = []
        for name in ("conda", "mamba"):
            manager = shutil.which(name)
            if not manager:
                continue
            path = Path(manager)
            if path.suffix.lower() in {".bat", ".cmd"}:
                executable = path.parent.parent / ("Scripts/conda.exe" if name == "conda" else "Library/bin/mamba.exe")
                if not executable.is_file():
                    continue
                manager = str(executable)
            with self.subTest(manager=name), tempfile.TemporaryDirectory(prefix="env-repair-history-test-") as directory:
                root = Path(directory).resolve()
                workspace = root / "workspace"
                workspace.mkdir()
                token = uuid4().hex
                (workspace / ".env-repair-owned").write_text(token, encoding="utf-8")
                prefix = workspace / "environment"
                with patch.dict(os.environ, {"MAMBA_ROOT_PREFIX": str(workspace / "manager-root"), "MAMBA_EXE": manager}):
                    try:
                        self.assertEqual(run_cmd_live([manager, "create", "-y", "--copy", "-p", str(prefix), "-c", "conda-forge", "zlib"]), 0)
                        inventory = {"records": [json.loads(record.read_text(encoding="utf-8")) for record in (prefix / "conda-meta").glob("*.json")]}
                        self.assert_private_dlls(prefix, inventory["records"])
                        command, transaction, _ = recovery._transaction(manager, prefix, ["xz"], ["conda-forge"])
                        self.assertTrue(transaction["LINK"])
                        tested = transaction["LINK"]
                        recovery._assert_tested_combination(tested, inventory, transaction)
                        self.assertEqual(run_cmd_live(command + ["--copy"]), 0)
                        specs = [ast.literal_eval(line.split("specs:", 1)[1].strip())
                                 for line in (prefix / "conda-meta/history").read_text(encoding="utf-8").splitlines()
                                 if line.startswith("# update specs:")]
                        self.assertTrue(specs)
                        requested = {re.split(r"[=<>!~ ]", spec)[0].split("::")[-1] for spec in specs[-1]}
                        self.assertEqual(requested, {"xz"})
                        after = {"records": [json.loads(record.read_text(encoding="utf-8")) for record in (prefix / "conda-meta").glob("*.json")]}
                        self.assert_private_dlls(prefix, after["records"])
                        recovery._assert_tested_combination(tested, after, {})
                        print(name + ": history records xz only, not its transitive dependencies", flush=True)
                    finally:
                        cleanup = _cleanup_workspace(workspace, source=root / "original-sentinel", token=token, manager=manager)
                        self.assertTrue(cleanup["ok"], cleanup)
                        self.assertFalse(workspace.exists())
                        print(name + ": owned test workspace completely removed", flush=True)
                tested_managers.append(name)
        if not tested_managers:
            self.skipTest("no supported manager executable available")
