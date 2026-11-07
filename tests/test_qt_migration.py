import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_repair import package_sandbox as qt_migration


class TestQtMigration(unittest.TestCase):
    def test_windows_prefix_is_not_mistaken_for_site_packages(self):
        root = Path("fixture").resolve()
        site = root / "Lib/site-packages"
        with patch.object(qt_migration, "run_cmd_capture", return_value=(0, json.dumps({"prefix": str(root), "purelib": str(site)}), "")):
            self.assertEqual(Path(qt_migration._site_directory("python", expected_prefix=root)), site)

    def local_package(self, root):
        source = root / "source"
        dist = source / "custom-1.dist-info"
        dist.mkdir(parents=True)
        (source / "custom.py").write_text("value = 1\n", encoding="utf-8")
        (dist / "METADATA").write_text("Name: custom\nVersion: 1\n", encoding="utf-8")
        (dist / "RECORD").write_text("custom.py,,\ncustom-1.dist-info/METADATA,,\ncustom-1.dist-info/RECORD,,\n", encoding="utf-8")
        return {"name": "custom", "version": "1", "dist": str(dist), "kind": "local", "imports": ["custom"]}

    def test_local_files_are_copied_without_changing_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = self.local_package(root)
            qt_migration._copy_local(item, root / "sandbox")
            self.assertEqual((root / "sandbox/custom.py").read_bytes(), (root / "source/custom.py").read_bytes())
            self.assertTrue(Path(item["dist"]).exists())
            qt_migration._copy_local(item, root / "sandbox")

    def test_copy_preflight_blocks_escape_hooks_missing_and_conflicting_files(self):
        for filename in ("../outside.py", "startup.pth", "missing.py", "custom.py"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                item = self.local_package(root)
                (root / "source/startup.pth").write_text("import custom", encoding="utf-8")
                target = root / "sandbox"
                target.mkdir()
                (target / "custom.py").write_text("different", encoding="utf-8")
                (Path(item["dist"]) / "RECORD").write_text(filename + ",,\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    qt_migration._copy_local(item, target)
                self.assertEqual((target / "custom.py").read_text(), "different")

    def test_inventory_distinguishes_conda_local_pypi_and_excludes_env_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "site"
            meta = root / "conda-meta"
            meta.mkdir()
            for name in ("managed", "official", "local", "env_repair"):
                dist = site / f"{name}-1.dist-info"
                dist.mkdir(parents=True)
                (dist / "METADATA").write_text(f"Name: {name}\nVersion: 1\n", encoding="utf-8")
                (dist / "INSTALLER").write_text("pip", encoding="utf-8")
            (site / "local-1.dist-info/direct_url.json").write_text('{"url":"file:///local"}', encoding="utf-8")
            (meta / "managed.json").write_text(json.dumps({"name": "managed", "version": "1", "files": ["site/managed-1.dist-info/METADATA"]}), encoding="utf-8")
            with patch("env_repair.discovery.get_site_packages", return_value=[str(site)]):
                result = qt_migration._inventory(root, "python")
            self.assertEqual({item["name"]: item["kind"] for item in result["packages"]},
                             {"managed": "conda", "official": "pypi", "local": "local"})

    def sandbox(self, inventory, *, live_result=0, failures=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        workspace = root / "work"
        workspace.mkdir()
        site = workspace / "site"
        site.mkdir()
        with patch.object(qt_migration.tempfile, "mkdtemp", return_value=str(workspace)), \
                patch.object(qt_migration, "_report_directory", return_value=root / "reports"), \
                patch.object(qt_migration, "_inventory", return_value=inventory), \
                patch.object(qt_migration, "run_cmd_live_capture", return_value=(live_result, "", "")) as live, \
                patch.object(qt_migration, "run_cmd_capture", return_value=(0, "@EXPLICIT\nhttps://example.test/package.conda\n", "")), \
                patch("env_repair.discovery.get_site_packages", return_value=[str(site)]), \
                patch.object(qt_migration, "_checks", return_value=failures or []) as checks, \
                contextlib.redirect_stdout(io.StringIO()):
            result = qt_migration.test_packages(env_path=root / "original", python_exe="python", manager="mamba",
                                               channels=["conda-forge"], python_spec="python=3.14", conda_specs=["pyqt6"])
        return result, live, checks

    def test_official_packages_are_versioned_wheels_and_never_source_builds(self):
        inventory = {"records": [{"name": "python", "version": "3.15"}, {"name": "env-repair", "version": "1"},
                                 {"name": "qtpy", "version": "2"}],
                     "packages": [{"name": "official", "version": "1", "kind": "pypi", "imports": ["official"]}]}
        result, live, checks = self.sandbox(inventory)
        self.assertTrue(result["ok"])
        create = live.call_args_list[0].args[0]
        specs = (Path(result["report_path"]).parent / "requirements.txt").read_text()
        self.assertNotIn("env-repair", specs)
        self.assertIn("qtpy=2", specs)
        download = live.call_args_list[1].args[0]
        self.assertIn("--only-binary=:all:", download)
        self.assertIn("https://pypi.org/simple", download)
        self.assertIn("official==1", download)
        self.assertEqual(checks.call_count, 1)

    def test_no_candidates_fail_closed_and_save_report(self):
        result, live, checks = self.sandbox({"records": [], "packages": []}, live_result=1)
        self.assertFalse(result["ok"])
        checks.assert_not_called()
        self.assertEqual(live.call_count, 1)
        self.assertTrue(Path(result["report_path"]).exists())
        self.assertFalse(Path(result["workspace"]).exists())

    def test_inventory_failure_is_reported_without_installation(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as reports, \
                patch.object(qt_migration.tempfile, "mkdtemp", return_value=directory), \
                patch.object(qt_migration, "_report_directory", return_value=Path(reports)), \
                patch.object(qt_migration, "_inventory", side_effect=ValueError("local conda artifact")), \
                patch.object(qt_migration, "run_cmd_live") as live, contextlib.redirect_stdout(io.StringIO()):
            result = qt_migration.test_packages(env_path="original", python_exe="python", manager="mamba",
                                               channels=[], python_spec="python=3.14", conda_specs=["pyqt6"])
            self.assertFalse(result["ok"])
            self.assertIn("local conda artifact", result["error"])
            self.assertTrue(Path(result["report_path"]).exists())
            self.assertFalse(Path(directory).exists())
            live.assert_not_called()

    def test_local_failure_blocks_approval_without_removing_original(self):
        with tempfile.TemporaryDirectory() as directory:
            item = self.local_package(Path(directory))
            result, _, _ = self.sandbox({"records": [], "packages": [item]}, failures=[{"import": "custom", "error": "ABI mismatch"}])
            self.assertFalse(result["ok"])
            self.assertTrue(Path(item["dist"]).exists())

    def test_base_sandbox_never_authorizes_automatic_application(self):
        result, _, _ = self.sandbox({"records": [{"name": "conda", "version": "1"}], "packages": []})
        self.assertTrue(result["ok"])
        self.assertTrue(result["apply_blocked"])
        with self.assertRaisesRegex(ValueError, "target-only"):
            qt_migration.apply_migration(result["report_path"])

    def test_failed_sandbox_cannot_be_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "migration.json"
            report.write_text('{"ok":false}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "passed validation"):
                qt_migration.apply_migration(report)

    def test_import_timeouts_and_pip_conflicts_are_failures(self):
        inventory = [{"imports": ["custom"]}]
        with patch.object(qt_migration, "run_cmd_capture", side_effect=[(1, "conflict", ""),
                subprocess.TimeoutExpired("python", 90), (1, "", "Qt failed"), (1, "", "local failed")]) as capture, \
                contextlib.redirect_stdout(io.StringIO()):
            failures = qt_migration._checks("python", inventory, ["PyQt6.QtCore", "PyQt6.QtWidgets"])
        self.assertEqual(len(failures), 4)
        self.assertTrue(all("-I" in call.args[0] for call in capture.call_args_list))

    def test_running_target_interpreter_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "migration.json"
            report.write_text(json.dumps({"ok": True, "inventory": {"records": []}, "source": directory}), encoding="utf-8")
            with patch.object(qt_migration.sys, "prefix", directory), self.assertRaisesRegex(ValueError, "independent"):
                qt_migration.apply_migration(report)

    def apply_fixture(self, root):
        inventory = {"records": [], "packages": []}
        report = {"ok": True, "source": str(root / "original"), "inventory": inventory,
                  "fingerprint": qt_migration._fingerprint(inventory), "manager": "mamba",
                  "requirements": [], "binding": "PyQt6"}
        path = root / "migration.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return path, inventory

    def test_changed_source_prevents_application(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = self.apply_fixture(Path(directory))
            with patch.object(qt_migration, "_inventory", return_value={"changed": True}), \
                    patch.object(qt_migration, "run_cmd_live") as live:
                with self.assertRaisesRegex(ValueError, "inventory changed"):
                    qt_migration.apply_migration(path)
            live.assert_not_called()

    def test_application_rejects_package_removals_and_env_repair_updates(self):
        for name, link in (("dependency", []), ("env-repair", [{"name": "env-repair", "version": "2"}])):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path, inventory = self.apply_fixture(Path(directory))
                preview = {"success": True, "actions": {"UNLINK": [{"name": name, "version": "1"}], "LINK": link}}
                with patch.object(qt_migration, "_inventory", return_value=inventory), \
                        patch.object(qt_migration, "run_cmd_capture", return_value=(0, json.dumps(preview), "")), \
                        patch.object(qt_migration, "run_cmd_live") as live:
                    with self.assertRaisesRegex(ValueError, "remove packages or alter env-repair"):
                        qt_migration.apply_migration(path)
                live.assert_not_called()

    def test_application_requires_consent_and_rechecks_after_change(self):
        for answer in ("", "yes"):
            with self.subTest(answer=answer), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path, inventory = self.apply_fixture(root)
                preview = {"success": True, "actions": {"LINK": [{"name": "python", "version": "3.14"}],
                                                         "UNLINK": [{"name": "python", "version": "3.15"}]}}
                with patch.object(qt_migration, "_inventory", return_value=inventory), \
                        patch.object(qt_migration, "run_cmd_capture", return_value=(0, json.dumps(preview), "")), \
                        patch.object(qt_migration, "run_cmd_live", return_value=0) as live, \
                        patch.object(qt_migration, "_checks", return_value=[]) as checks, \
                        patch("env_repair.discovery.get_site_packages", return_value=[str(root / "site")]), \
                        patch("builtins.input", return_value=answer), contextlib.redirect_stdout(io.StringIO()):
                    result = qt_migration.apply_migration(path)
                self.assertEqual(result["ok"], answer == "yes")
                self.assertEqual(live.call_count, int(answer == "yes"))
                self.assertEqual(checks.call_count, int(answer == "yes"))
                if answer:
                    self.assertTrue((root / "before-conda.txt").exists())
                    self.assertTrue((root / "applied.json").exists())
