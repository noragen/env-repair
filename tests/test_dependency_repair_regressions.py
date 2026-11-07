import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_repair import repair, verify_imports as imports


class TestDependencyRepairRegressions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.prefix = Path(temporary.name)
        self.site = self.prefix / "Lib" / "site-packages"
        self.site.mkdir(parents=True)
        (self.prefix / "conda-meta").mkdir()
        self.entries = []
        for name, value in {
            "get_env_package_entries": self.entries,
            "load_conda_channels": ["conda-forge"],
            "which": "mamba",
            "_python_major_minor": "3.13",
            "_load_verify_imports_blacklist": {},
            "conda_install": True,
            "conda_install_capture": (True, "", ""),
            "conda_remove": True,
            "pip_uninstall": True,
            "pip_reinstall": True,
            "check_import": (True, None),
        }.items():
            patcher = patch.object(imports, name, return_value=value)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            setattr(self, name, mock)

    def distribution(self, name, module=None, local=False):
        dist = self.site / f"{name.replace('-', '_')}-1.0.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(f"Name: {name}\nVersion: 1.0\n", encoding="utf-8")
        (dist / "top_level.txt").write_text(module or name.replace("-", "_"), encoding="utf-8")
        if local:
            (dist / "direct_url.json").write_text('{"url": "file:///local/build"}', encoding="utf-8")
        return dist

    def failure(self, name, error, module=None, local=False):
        dist = self.distribution(name, module, local)
        return {"dist": dist.name, "dist_path": dist,
                "import": module or name.replace("-", "_"), "error": error}

    def run_fix(self, failures, *, interactive_qt=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return imports.attempt_fix(failures, "target-python", str(self.prefix), "mamba",
                                       base_prefix=str(self.prefix), debug=False, interactive_qt=interactive_qt)

    def test_interactive_qt_selection_runs_once_for_all_consumers(self):
        from env_repair import qt_repair

        error = "qtpy.QtBindingsNotFoundError: No Qt bindings could be found"
        failures = [self.failure(name, error) for name in ("qtpy", "qtawesome", "superqt")]
        with patch.dict(imports.os.environ, {"QT_API": ""}), \
                patch.object(qt_repair.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="1") as prompt, \
                patch.object(qt_repair, "_preview_qt_install", return_value={"LINK": [], "UNLINK": []}), \
                patch.object(qt_repair, "conda_install", return_value=True) as install:
            report = self.run_fix(failures, interactive_qt=True)
        self.assertTrue(report["ok"])
        self.assertEqual(report["plan"], [])
        self.assertEqual(report["actions"][0]["action"], "qt_binding_selection")
        prompt.assert_called_once()
        install.assert_called_once()

    def test_confirmed_qt_removal_is_reported_as_removed_not_failed_import(self):
        failure = self.failure("qtpy", "qtpy.QtBindingsNotFoundError: No Qt bindings could be found")
        with patch.object(imports, "repair_qt_bindings", return_value=([dict(failure, removed=True)],
                                                                     [{"action": "qt_unavailable_recovery", "choice": "remove", "ok": True}])):
            report = self.run_fix([failure], interactive_qt=True)
        self.assertTrue(report["ok"])
        self.assertEqual(report["plan"][0]["kind"], "removed")

    def test_shared_attribute_and_metadata_failures_repair_providers_in_one_batch(self):
        self.entries.extend({"name": name, "channel": "conda-forge"}
                            for name in ("charset-normalizer", "eth-hash"))
        self.distribution("charset-normalizer")
        failures = [
            self.failure("requests", "AttributeError: module 'charset_normalizer.md' has no attribute 'CharInfo'"),
            self.failure("jupyterlab", "AttributeError: module 'charset_normalizer.md' has no attribute 'CharInfo'"),
            self.failure("eth-account", "importlib.metadata.PackageNotFoundError: No package metadata was found for eth-hash"),
        ]
        report = self.run_fix(failures)
        self.assertTrue(report["ok"])
        self.assertEqual(self.conda_install.call_args.args[1], ["charset-normalizer", "eth-hash"])
        self.conda_install.assert_called_once()
        self.pip_uninstall.assert_not_called()
        self.conda_install_capture.assert_not_called()

    def test_dependency_failure_preserves_dependents_and_is_not_success(self):
        self.entries.append({"name": "charset-normalizer", "channel": "conda-forge"})
        error = "AttributeError: module 'charset_normalizer.md' has no attribute 'CharInfo'"
        self.check_import.return_value = (False, error)
        self.conda_install.return_value = False
        failure = self.failure("jupyterlab", error, local=True)
        report = self.run_fix([failure])
        self.assertFalse(report["ok"])
        self.assertEqual(report["plan"][0]["kind"], "defer")
        self.pip_uninstall.assert_not_called()
        self.pip_reinstall.assert_not_called()
        self.conda_install_capture.assert_not_called()

    def test_unknown_external_dependency_does_not_trigger_dependent_reinstall(self):
        report = self.run_fix([self.failure("consumer", "ModuleNotFoundError: No module named 'private_dependency'")])
        self.assertFalse(report["ok"])
        self.pip_reinstall.assert_not_called()
        self.pip_uninstall.assert_not_called()
        self.conda_install_capture.assert_not_called()

    def test_defaults_disabled_does_not_override_dependency_deferral(self):
        self.entries.append({"name": "consumer", "channel": "pkgs/main"})
        self.load_conda_channels.return_value = ["conda-forge", "nodefaults"]
        report = self.run_fix([self.failure("consumer", "AttributeError: module 'private_helper' has no attribute 'thing'")])
        self.assertEqual(report["plan"][0]["kind"], "defer")
        self.conda_install_capture.assert_not_called()

    def test_local_nose_uses_legacy_policy_before_search(self):
        with patch.object(imports, "_pick_conda_candidate_for_local_direct_url") as search:
            report = self.run_fix([self.failure("nose", "ModuleNotFoundError: No module named 'imp'", local=True)])
        self.assertEqual(report["plan"][0]["kind"], "skip")
        self.assertIn("unmaintained", report["plan"][0]["reason"])
        search.assert_not_called()
        self.conda_install.assert_not_called()

    def test_local_attrdict_special_case_precedes_external_and_local_policies(self):
        error = "ImportError: cannot import name 'Mapping' from 'collections'"
        with patch.object(imports, "_pick_conda_candidate_for_local_direct_url") as search:
            report = self.run_fix([self.failure("attrdict", error, local=True)])
        self.assertEqual(report["plan"][0]["name"], "attrdict3")
        search.assert_not_called()

    def test_readline_attribute_failure_targets_pyreadline3_not_legacy_pyreadline(self):
        self.entries.append({"name": "pyreadline3", "channel": "conda-forge"})
        self.distribution("pyreadline3")
        report = self.run_fix([self.failure("pyreadline", "AttributeError: module 'pyreadline3' has no attribute '__version__'", module="readline")])
        self.assertTrue(report["ok"])
        self.assertEqual(self.conda_install.call_args.args[1], ["pyreadline3"])

    def test_pypi_dependency_stays_pip_managed(self):
        self.entries.append({"name": "helper", "channel": "pypi"})
        self.distribution("helper")
        self.run_fix([self.failure("consumer", "ModuleNotFoundError: No module named 'helper'")])
        self.pip_reinstall.assert_called_once()
        self.conda_install.assert_not_called()

    def test_pkg_resources_upgrades_consumer_not_setuptools(self):
        self.entries.append({"name": "clyent", "channel": "conda-forge"})
        with patch.object(imports, "conda_upgrade_dependencies", return_value=True) as upgrade:
            report = self.run_fix([self.failure("clyent", "ModuleNotFoundError: No module named 'pkg_resources'")])
        self.assertTrue(report["ok"])
        self.assertEqual(upgrade.call_args.args[1], ["clyent"])
        self.pip_reinstall.assert_not_called()
        self.conda_install.assert_not_called()

    def test_setuptools_missing_legacy_api_is_not_blindly_reinstalled(self):
        self.entries.append({"name": "setuptools", "channel": "conda-forge"})
        report = self.run_fix([self.failure("setuptools", "ModuleNotFoundError: No module named 'pkg_resources'", module="pkg_resources")])
        self.assertFalse(report["ok"])
        self.assertEqual(report["plan"][0]["kind"], "defer")
        self.pip_reinstall.assert_not_called()
        self.conda_install_capture.assert_not_called()

    def test_qt_failures_share_one_installed_binding_repair(self):
        self.entries.append({"name": "pyside6", "channel": "conda-forge", "version": "6.10"})
        error = "qtpy.QtBindingsNotFoundError: No Qt bindings could be found"
        with patch.dict(imports.os.environ, {"QT_API": ""}):
            report = self.run_fix([self.failure(name, error) for name in ("qtpy", "qtawesome", "superqt")])
        self.assertTrue(report["ok"])
        self.assertEqual(self.conda_install.call_args.args[1], ["pyside6"])
        self.conda_install.assert_called_once()
        self.pip_uninstall.assert_not_called()

    def test_missing_qt_binding_is_reported_without_choosing_one(self):
        report = self.run_fix([self.failure("qtpy", "qtpy.QtBindingsNotFoundError: No Qt bindings could be found")])
        self.assertFalse(report["ok"])
        self.assertIn("Qt backend unavailable", report["plan"][0]["reason"])
        self.conda_install.assert_not_called()
        self.conda_install_capture.assert_not_called()

    def test_qt_api_selection_is_not_silently_overridden(self):
        self.entries.append({"name": "pyqt", "version": "5.15", "channel": "conda-forge"})
        with patch.dict(imports.os.environ, {"QT_API": "pyside6"}):
            report = self.run_fix([self.failure("superqt", "qtpy.QtBindingsNotFoundError: No Qt bindings could be found")])
        self.assertFalse(report["ok"])
        self.assertIn("QT_API=pyside6", report["plan"][0]["reason"])
        self.conda_install.assert_not_called()

    def test_persistent_qt_failure_does_not_reinstall_consumers(self):
        self.entries.append({"name": "pyside6", "channel": "conda-forge"})
        error = "qtpy.QtBindingsNotFoundError: No Qt bindings could be found"
        self.check_import.return_value = (False, error)
        with patch.dict(imports.os.environ, {"QT_API": ""}):
            report = self.run_fix([self.failure("qtawesome", error)])
        self.assertFalse(report["ok"])
        self.conda_install_capture.assert_not_called()
        self.pip_uninstall.assert_not_called()

    def test_conda_record_repairs_missing_metadata_when_list_omits_distribution(self):
        record = {"name": "eth-hash", "version": "1.0",
                  "files": ["Lib/site-packages/eth_hash-1.0.dist-info/METADATA"]}
        (self.prefix / "conda-meta" / "eth-hash-1.0-0.json").write_text(json.dumps(record), encoding="utf-8")
        self.run_fix([self.failure("eth-account", "importlib.metadata.PackageNotFoundError: No package metadata was found for eth-hash")])
        self.assertEqual(self.conda_install.call_args.args[1], ["eth-hash"])
        self.pip_reinstall.assert_not_called()

    def test_failed_root_dependency_is_not_reinstalled_a_second_time(self):
        self.entries.append({"name": "charset-normalizer", "channel": "conda-forge"})
        error = "AttributeError: module 'charset_normalizer.md' has no attribute 'CharInfo'"
        self.check_import.return_value = (False, error)
        report = self.run_fix([self.failure("charset-normalizer", error)])
        self.assertFalse(report["ok"])
        self.conda_install.assert_called_once()
        self.conda_install_capture.assert_not_called()

    def test_binary_provider_shadow_is_removed_before_rechecking_consumers(self):
        root = self.site / "charset_normalizer"
        root.mkdir()
        (root / "md.pyd").write_bytes(b"conda-module")
        shadow = root / "md.cp313-win_amd64.pyd"
        shadow.write_bytes(b"stale-module")
        record = {"name": "charset-normalizer", "version": "1.0", "files": [
            "Lib/site-packages/charset_normalizer/md.pyd"]}
        (self.prefix / "conda-meta" / "charset-normalizer-1.0-0.json").write_text(json.dumps(record), encoding="utf-8")
        error = "AttributeError: module 'charset_normalizer.md' has no attribute 'CharInfo'"
        self.check_import.side_effect = lambda *_: (False, error) if shadow.exists() else (True, None)
        report = self.run_fix([self.failure("requests", error)])
        self.assertTrue(report["ok"])
        self.assertTrue((root / "md.pyd").exists())
        self.assertFalse(shadow.exists())
        self.pip_uninstall.assert_not_called()
        self.conda_install.assert_called_once()

    def test_unmanaged_local_dependency_is_not_replaced(self):
        self.distribution("private-helper", module="private_helper", local=True)
        self.run_fix([self.failure("consumer", "ModuleNotFoundError: No module named 'private_helper'")])
        self.pip_reinstall.assert_not_called()
        self.conda_install.assert_not_called()

    def test_terminal_charset_error_wins_over_plugin_warning(self):
        error = "anaconda-auth: module 'conda.plugins' has no attribute 'CondaAuthHandler'\nAttributeError: module 'charset_normalizer.md' has no attribute 'CharInfo'"
        self.assertEqual(imports._import_failure_cause(error), ("charset_normalizer", False))

    def test_auth_plugin_error_resolves_plugin_provider(self):
        error = "Error while loading conda entry point: anaconda-auth (module 'conda.plugins' has no attribute 'CondaAuthHandler')"
        self.assertEqual(imports._import_failure_cause(error), ("anaconda_auth", False))

    def test_external_attribute_error_after_relink_does_not_uninstall_consumer(self):
        failure = self.failure("consumer", "ImportError: DLL load failed", local=True)
        self.check_import.return_value = (False, "AttributeError: module 'dependency.core' has no attribute 'thing'")
        with patch.object(imports, "_pick_conda_candidate_for_local_direct_url", return_value="consumer"):
            self.run_fix([failure])
        self.conda_install_capture.assert_called_once()
        self.pip_uninstall.assert_not_called()

    def test_metadata_error_after_relink_does_not_uninstall_consumer(self):
        failure = self.failure("consumer", "ImportError: DLL load failed", local=True)
        self.check_import.return_value = (False, "importlib.metadata.PackageNotFoundError: No package metadata was found for helper")
        with patch.object(imports, "_pick_conda_candidate_for_local_direct_url", return_value="consumer"):
            self.run_fix([failure])
        self.pip_uninstall.assert_not_called()


class TestDuplicateMetadataSafety(unittest.TestCase):
    def test_conda_owned_metadata_wins_even_if_stale_copy_is_newer(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            module = prefix / "eth_hash" / "__init__.py"
            module.parent.mkdir()
            module.write_text("sentinel = True", encoding="utf-8")
            meta = prefix / "conda-meta"
            meta.mkdir()
            paths = []
            for version in ("1.0", "2.0"):
                dist = prefix / "lib" / "python3.13" / "site-packages" / f"eth_hash-{version}.dist-info"
                dist.mkdir(parents=True)
                (dist / "METADATA").write_text(f"Name: eth-hash\nVersion: {version}\n", encoding="utf-8")
                paths.append(str(dist))
            (meta / "eth-hash-1.0-0.json").write_text(json.dumps({
                "files": ["lib/python3.13/site-packages/eth_hash-1.0.dist-info/METADATA"]}), encoding="utf-8")
            env = {"path": temporary, "issues": [{"type": "duplicate-dist-info", "package": "eth-hash", "paths": paths}]}
            repair._cleanup_duplicate_dist_info(env, False)
            self.assertTrue(Path(paths[0]).exists())
            self.assertFalse(Path(paths[1]).exists())

    def test_cleanup_keeps_metadata_and_reinstall_failure_keeps_issue(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            module = prefix / "eth_hash" / "__init__.py"
            module.parent.mkdir()
            module.write_text("sentinel = True", encoding="utf-8")
            paths = []
            for version in ("1.0", "2.0"):
                dist = prefix / f"eth_hash-{version}.dist-info"
                dist.mkdir()
                (dist / "METADATA").write_text(f"Name: eth-hash\nVersion: {version}\n", encoding="utf-8")
                paths.append(str(dist))
            issue = {"type": "duplicate-dist-info", "package": "eth-hash", "paths": paths}
            env = {"path": str(prefix), "python": "target-python", "issues": [issue]}
            repair._cleanup_duplicate_dist_info(env, False)
            self.assertEqual(len(list(prefix.glob("*.dist-info/METADATA"))), 1)
            self.assertEqual(module.read_text(encoding="utf-8"), "sentinel = True")
            self.assertIn(issue, env["issues"])
            with patch.object(repair, "conda_install", return_value=False) as install:
                repair._fix_duplicates(env, [], "mamba", [], False, False, "auto", False, False, in_conda_env=True)
            self.assertTrue(install.call_args.kwargs["force_reinstall"])
            self.assertIn(issue, env["issues"])
            self.assertEqual(len(list(prefix.glob("*.dist-info/METADATA"))), 1)

    def test_invalid_metadata_is_not_blindly_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary) / "broken-1.dist-info"
            dist.mkdir()
            env = {"path": temporary, "issues": [{"type": "duplicate-dist-info", "package": "broken", "paths": [str(dist)]}]}
            result = repair._cleanup_duplicate_dist_info(env, False)
            self.assertFalse(result[0]["fixed"])
            self.assertTrue(dist.exists())

    def test_build_duplicate_uses_pip_even_when_conda_lists_build(self):
        index = repair._build_channel_index([{"name": "build", "channel": "conda-forge"}])
        self.assertEqual(repair._choose_reinstall_method(index, "build", "auto", False), ("pip", "build"))

    def test_successful_duplicate_reinstall_clears_pending_issue(self):
        issue = {"type": "duplicate-dist-info", "package": "build"}
        env = {"path": "temporary-prefix", "python": "target-python", "issues": [issue]}
        with patch.object(repair, "pip_reinstall", return_value=True) as install:
            repair._fix_duplicates(env, [], "mamba", [], False, False, "auto", False, False, in_conda_env=True)
        self.assertEqual(env["issues"], [])
        self.assertEqual(install.call_args.args, ("target-python", "build"))

    def test_cleanup_does_not_touch_paths_outside_target_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "target"
            prefix.mkdir()
            dist = Path(temporary) / "outside-1.dist-info"
            dist.mkdir()
            (dist / "METADATA").write_text("Name: outside\nVersion: 1\n", encoding="utf-8")
            env = {"path": str(prefix), "issues": [{"type": "duplicate-dist-info", "package": "outside", "paths": [str(dist)]}]}
            result = repair._cleanup_duplicate_dist_info(env, False)
            self.assertFalse(result[0]["fixed"])
            self.assertTrue(dist.exists())


class TestCharsetExtensionQuarantine(unittest.TestCase):
    def test_abi_tagged_binary_shadowing_owned_binary_is_quarantined_without_python_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            root = prefix / "Lib" / "site-packages" / "charset_normalizer"
            root.mkdir(parents=True)
            (prefix / "conda-meta").mkdir()
            for name in ("md.pyd", "cd.pyd", "md.cp313-win_amd64.pyd", "md__mypyc.cp313-win_amd64.pyd"):
                (root / name).write_bytes(b"fixture")
            record = {"files": ["Lib/site-packages/charset_normalizer/md.pyd",
                                 "Lib/site-packages/charset_normalizer/cd.pyd"]}
            (prefix / "conda-meta" / "charset-normalizer.json").write_text(json.dumps(record), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                result = imports._quarantine_charset_extensions(prefix, [root.parent])
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["moved"]), 1)
            self.assertEqual(Path(result["moved"][0]["source"]).name, "md.cp313-win_amd64.pyd")
            self.assertTrue((root / "md.pyd").exists())
            self.assertTrue((root / "cd.pyd").exists())
            self.assertTrue((root / "md__mypyc.cp313-win_amd64.pyd").exists())
            self.assertFalse((root / "md.py").exists())

    def test_unowned_extension_is_preserved_without_existing_owned_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            root = prefix / "Lib" / "site-packages" / "charset_normalizer"
            root.mkdir(parents=True)
            (prefix / "conda-meta").mkdir()
            binary = root / "md.cp313-win_amd64.pyd"
            binary.write_bytes(b"fixture")
            (prefix / "conda-meta" / "charset-normalizer.json").write_text(json.dumps({
                "files": ["Lib/site-packages/charset_normalizer/md.pyd"]}), encoding="utf-8")
            result = imports._quarantine_charset_extensions(prefix, [root.parent])
            self.assertEqual(result["moved"], [])
            self.assertTrue(binary.exists())

    def test_only_unowned_shadowing_binary_is_moved_and_can_be_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            root = prefix / "Lib" / "site-packages" / "charset_normalizer"
            root.mkdir(parents=True)
            (prefix / "conda-meta").mkdir()
            for name in ("md.py", "md.cp313-win_amd64.pyd", "cd.cp313-win_amd64.pyd"):
                (root / name).write_bytes(b"fixture")
            record = {"files": ["Lib/site-packages/charset_normalizer/md.py",
                                 "Lib/site-packages/charset_normalizer/cd.cp313-win_amd64.pyd"]}
            (prefix / "conda-meta" / "charset-normalizer.json").write_text(json.dumps(record), encoding="utf-8")
            result = imports._quarantine_charset_extensions(prefix, [root.parent])
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["moved"]), 1)
            self.assertTrue((root / "md.py").exists())
            self.assertTrue((root / "cd.cp313-win_amd64.pyd").exists())
            self.assertEqual(Path(result["moved"][0]["destination"]).read_bytes(), b"fixture")


class TestCondaCoreDependencyRepair(unittest.TestCase):
    def test_dependency_failure_does_not_reinstall_python(self):
        from argparse import Namespace
        from env_repair import doctor

        args = Namespace(json=True, yes=True, debug=False)
        detail = {"status": "broken", "stderr": "AttributeError: module 'charset_normalizer.md' has no attribute 'CharInfo'"}
        with contextlib.ExitStack() as stack:
            for name, value in {
                "which": "mamba", "scan_env": {"issues": []},
                "conda_health_check": (False, False, detail),
                "get_python_exe": "target-python", "get_site_packages": ["temporary-site"],
                "get_env_package_entries": [], "_approve": True, "conda_repair_core": True,
            }.items():
                stack.enter_context(patch.object(doctor, name, return_value=value))
            runtime = stack.enter_context(patch.object(doctor, "conda_repair_python_runtime"))
            dependency = stack.enter_context(patch.object(imports, "_repair_import_dependencies", return_value=({"charset-normalizer"}, [{"ok": False}])))
            self.assertFalse(doctor._check_and_repair_conda_core(args=args, base_prefix="temporary-prefix", manager="mamba", channels=[]))
            dependency.assert_called_once()
            runtime.assert_not_called()

    def test_repaired_dependency_avoids_core_reinstall(self):
        from argparse import Namespace
        from env_repair import doctor

        args = Namespace(json=True, yes=True, debug=False)
        broken = (False, False, {"stderr": "AttributeError: module 'charset_normalizer.md' has no attribute 'CharInfo'"})
        with contextlib.ExitStack() as stack:
            for name, value in {
                "which": "mamba", "scan_env": {"issues": []},
                "get_python_exe": "target-python", "get_site_packages": ["temporary-site"],
                "get_env_package_entries": [], "_approve": True,
            }.items():
                stack.enter_context(patch.object(doctor, name, return_value=value))
            stack.enter_context(patch.object(doctor, "conda_health_check", side_effect=[broken] + [(True, False, {})] * 3))
            core = stack.enter_context(patch.object(doctor, "conda_repair_core"))
            runtime = stack.enter_context(patch.object(doctor, "conda_repair_python_runtime"))
            stack.enter_context(patch.object(imports, "_repair_import_dependencies", return_value=({"charset-normalizer"}, [{"ok": True}])))
            self.assertTrue(doctor._check_and_repair_conda_core(args=args, base_prefix="temporary-prefix", manager="mamba", channels=[]))
            core.assert_not_called()
            runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
