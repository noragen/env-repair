import contextlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_repair import package_sandbox as sandbox


class TestSandboxResilience(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "env-repair-packages-fixture"
        self.workspace.mkdir()
        self.source = self.root / "original"
        self.source.mkdir()
        self.sentinel = self.source / "sentinel.txt"
        self.sentinel.write_text("keep original", encoding="utf-8")
        self.reports = self.root / "reports"

    def run_test(self, *, creation=None, checks=None, inventory_error=None, export=(0, "@EXPLICIT\n", ""), records=None):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sandbox.tempfile, "mkdtemp", return_value=str(self.workspace)))
            stack.enter_context(patch.object(sandbox, "_report_directory", return_value=self.reports))
            inventory = stack.enter_context(patch.object(sandbox, "_inventory", return_value={"records": records or [], "packages": []}))
            if inventory_error:
                inventory.side_effect = inventory_error
            stage = stack.enter_context(patch.object(sandbox, "_run_stage", side_effect=creation))
            stack.enter_context(patch.object(sandbox, "_site_directory", return_value=str(self.workspace / "site")))
            capture = stack.enter_context(patch.object(sandbox, "run_cmd_capture", return_value=export))
            validation = stack.enter_context(patch.object(sandbox, "_checks", return_value=[] if checks is None else checks))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            result = sandbox.test_packages(env_path=self.source, python_exe="source-python", manager="mamba",
                                           channels=["conda-forge"], python_spec="python=3.13", conda_specs=["requests"])
        return result, inventory, validation, stage, capture

    def test_success_completes_checks_saves_report_and_removes_all_temporary_files(self):
        def create(*args, **kwargs):
            (self.workspace / "environment/conda-meta").mkdir(parents=True)
            (self.workspace / "download-cache").mkdir()
            (self.workspace / "download-cache/large-download.whl").write_bytes(b"artifact")
        result, _, checks, _, capture = self.run_test(creation=create)
        self.assertTrue(result["ok"])
        self.assertTrue(result["validation_ok"])
        self.assertTrue(result["cleanup"]["ok"])
        self.assertFalse(self.workspace.exists())
        self.assertEqual(checks.call_count, 1)
        self.assertTrue(any(call.args[0][1:3] == ["env", "remove"] for call in capture.call_args_list))
        saved = json.loads(Path(result["report_path"]).read_text())
        self.assertTrue(saved["cleanup"]["ok"])
        self.assertTrue((self.reports / "requirements.txt").is_file())
        self.assertTrue((self.reports / "explicit.txt").is_file())
        self.assertEqual(self.sentinel.read_text(), "keep original")

    def test_dependency_import_failure_is_reported_and_cleaned(self):
        result, _, _, _, _ = self.run_test(checks=[{"import": "requests", "error": "ImportError: dependency failed"}])
        self.assertFalse(result["ok"])
        self.assertEqual(result["failures"][0]["import"], "requests")
        self.assertTrue(result["cleanup"]["ok"])
        self.assertFalse(self.workspace.exists())

    def test_inventory_error_is_not_a_solver_conflict_and_still_cleans_up(self):
        result, _, checks, stage, _ = self.run_test(inventory_error=sandbox.SandboxInventoryError("record.json: invalid package"))
        self.assertEqual(result["failure_category"], "inventory")
        self.assertTrue(result["cleanup"]["ok"])
        stage.assert_not_called()
        checks.assert_not_called()
        self.assertFalse(self.workspace.exists())

    def test_unexpected_internal_error_is_preserved_without_crashing_outer_repair(self):
        result, _, _, _, _ = self.run_test(creation=RuntimeError("unexpected record shape"))
        self.assertEqual(result["failure_category"], "internal-error")
        self.assertIn("RuntimeError", result["traceback"])
        self.assertTrue(result["cleanup"]["ok"])

    def test_interruption_cleans_and_preserves_interrupted_report_before_propagating(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_test(creation=KeyboardInterrupt())
        self.assertFalse(self.workspace.exists())
        report = json.loads((self.reports / "migration.json").read_text())
        self.assertTrue(report["interrupted"])
        self.assertTrue(report["cleanup"]["ok"])

    def test_subprocess_interruption_also_cleans_before_propagating(self):
        with self.assertRaises(sandbox.OperationInterrupted):
            self.run_test(creation=sandbox.OperationInterrupted(["mamba", "create"]))
        self.assertFalse(self.workspace.exists())
        report = json.loads((self.reports / "migration.json").read_text())
        self.assertTrue(report["interrupted"])
        self.assertTrue(report["cleanup"]["ok"])

    def test_report_write_failure_does_not_prevent_cleanup(self):
        self.reports.write_text("not a directory", encoding="utf-8")
        result, _, _, _, _ = self.run_test()
        self.assertTrue(result["cleanup"]["ok"])
        self.assertIn("report_error", result)
        self.assertFalse(self.workspace.exists())

    def test_optional_export_failure_does_not_invalidate_successful_import_checks(self):
        result, _, checks, _, _ = self.run_test(export=(1, "", "export unavailable"))
        self.assertTrue(result["ok"])
        self.assertIn("export unavailable", result["export_warning"])
        self.assertEqual(checks.call_count, 1)

    def test_mamba_url_manifest_without_explicit_marker_is_normalized(self):
        url = "https://conda.anaconda.org/conda-forge/noarch/requests-1-build.conda"
        result, _, _, _, _ = self.run_test(export=(0, "List of packages in environment: fixture\n\n" + url + "\n", ""),
                                          records=[{"name": "requests", "version": "1", "url": url}])
        self.assertTrue(result["ok"])
        self.assertNotIn("export_warning", result)
        self.assertEqual((self.reports / "explicit.txt").read_text(), "@EXPLICIT\n" + url + "\n")

    def test_cleanup_refuses_source_paths_and_incorrect_markers(self):
        marker = self.workspace / ".env-repair-owned"
        marker.write_text("owned-token", encoding="utf-8")
        for source, token in ((self.workspace, "owned-token"), (self.source, "wrong-token")):
            with self.subTest(source=source, token=token), patch.object(sandbox, "run_cmd_capture") as capture:
                result = sandbox._cleanup_workspace(self.workspace, source=source, token=token, manager="mamba")
            self.assertFalse(result["ok"])
            self.assertTrue(self.workspace.exists())
            capture.assert_not_called()
        self.assertEqual(self.sentinel.read_text(), "keep original")

    def test_cleanup_retries_transient_locks_and_reports_permanent_failures(self):
        (self.workspace / ".env-repair-owned").write_text("token", encoding="utf-8")
        remove = sandbox.shutil.rmtree
        attempts = []
        def transient(*args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise PermissionError("file temporarily locked")
            return remove(*args, **kwargs)
        with patch.object(sandbox.shutil, "rmtree", side_effect=transient), patch.object(sandbox.time, "sleep"):
            result = sandbox._cleanup_workspace(self.workspace, source=self.source, token="token", manager="mamba")
        self.assertTrue(result["ok"])
        self.assertEqual(result["attempts"], 2)
        self.workspace.mkdir()
        (self.workspace / ".env-repair-owned").write_text("token", encoding="utf-8")
        with patch.object(sandbox.shutil, "rmtree", side_effect=PermissionError("permanent lock")), patch.object(sandbox.time, "sleep"):
            result = sandbox._cleanup_workspace(self.workspace, source=self.source, token="token", manager="mamba")
        self.assertFalse(result["ok"])
        self.assertEqual(result["attempts"], 3)
        self.assertIn("permanent lock", result["error"])

    def test_readonly_files_are_removed_without_touching_source(self):
        (self.workspace / ".env-repair-owned").write_text("token", encoding="utf-8")
        readonly = self.workspace / "readonly.txt"
        readonly.write_text("temporary", encoding="utf-8")
        readonly.chmod(stat.S_IREAD)
        result = sandbox._cleanup_workspace(self.workspace, source=self.source, token="token", manager="mamba")
        self.assertTrue(result["ok"])
        self.assertFalse(self.workspace.exists())
        self.assertEqual(self.sentinel.read_text(), "keep original")

    def test_failed_manager_removal_falls_back_to_owned_workspace_cleanup(self):
        (self.workspace / ".env-repair-owned").write_text("token", encoding="utf-8")
        (self.workspace / "environment/conda-meta").mkdir(parents=True)
        with patch.object(sandbox, "run_cmd_capture", return_value=(1, "", "manager could not remove prefix")):
            result = sandbox._cleanup_workspace(self.workspace, source=self.source, token="token", manager="mamba")
        self.assertTrue(result["ok"])
        self.assertEqual(result["manager_remove"]["returncode"], 1)
        self.assertFalse(self.workspace.exists())

    def test_real_conda_helper_names_and_epoch_versions_are_accepted(self):
        meta = self.workspace / "conda-meta"
        meta.mkdir()
        names = ["_python_abi3_support", "_openmp_mutex", "_libgcc_mutex", "x264"]
        for name in names:
            version = "1!164.3095" if name == "x264" else "1.0"
            (meta / f"{name}.json").write_text(json.dumps({"name": name, "version": version, "files": []}), encoding="utf-8")
        with patch("env_repair.discovery.get_site_packages", return_value=[]):
            inventory = sandbox._inventory(self.workspace, "fixture-python")
        self.assertEqual({record["name"] for record in inventory["records"]}, set(names))

    def test_malformed_records_include_the_filename_and_identity(self):
        meta = self.workspace / "conda-meta"
        meta.mkdir()
        path = meta / "broken.json"
        for payload in (["not", "a", "record"], {"name": 42, "version": "1.0"}, {"name": "valid", "version": None}):
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.subTest(payload=payload), patch("env_repair.discovery.get_site_packages", return_value=[]):
                with self.assertRaises(sandbox.SandboxInventoryError) as raised:
                    sandbox._inventory(self.workspace, "fixture-python")
            self.assertIn("broken.json", str(raised.exception))

    def test_native_extension_abi_tags_are_not_mistaken_for_import_names(self):
        from env_repair.verify_imports import parse_record_file

        record = self.workspace / "RECORD"
        record.write_text("_brotli.cp313-win_amd64.pyd,,\n_native.cpython-313-x86_64-linux-gnu.so,,\nnormal.py,,\n", encoding="utf-8")
        self.assertEqual(parse_record_file(record), ["_brotli", "_native", "normal"])
