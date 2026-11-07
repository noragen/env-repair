import contextlib
import io
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from env_repair import discovery, verify_imports as imports


class TestImportTimeoutRecovery(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.prefix = Path(temporary.name)
        self.dist = self.prefix / "anaconda_client-1.13.1.dist-info"
        self.dist.mkdir()
        (self.dist / "METADATA").write_text("Name: anaconda-client\nVersion: 1.13.1\n", encoding="utf-8")
        self.result = {"dist": self.dist.name, "dist_path": self.dist, "import": "binstar_client",
                       "ok": False, "error": "Import timed out (>30s)"}

    def retry(self, *, fix):
        return imports._retry_import_timeouts([self.result], python_exe="target-python",
                                              env_path=str(self.prefix), manager="mamba",
                                              base_prefix=str(self.prefix), fix=fix, quiet=True)

    def test_isolated_retry_succeeds_without_upgrading(self):
        with patch.object(imports, "check_import", return_value=(True, None)) as check, \
                patch.object(imports, "conda_upgrade_dependencies") as upgrade:
            results, _ = self.retry(fix=True)
        self.assertTrue(results[0]["ok"])
        check.assert_called_once_with("binstar_client", "target-python", timeout=90)
        upgrade.assert_not_called()

    def test_scan_only_timeout_does_not_install_anything(self):
        with patch.object(imports, "check_import", return_value=(False, "Import timed out (>90s)")), \
                patch.object(imports, "conda_upgrade_dependencies") as upgrade:
            results, _ = self.retry(fix=False)
        self.assertFalse(results[0]["ok"])
        upgrade.assert_not_called()

    def test_persistent_managed_timeout_upgrades_distribution_not_import_name(self):
        with patch.object(imports, "check_import", side_effect=[(False, "Import timed out (>90s)"), (True, None)]), \
                patch.object(imports, "is_conda_env", return_value=True), \
                patch.object(imports, "get_env_package_entries", return_value=[{"name": "anaconda-client", "channel": "conda-forge"}]), \
                patch.object(imports, "load_conda_channels", return_value=["conda-forge"]), \
                patch.object(imports, "which", return_value=True), \
                patch.object(imports, "conda_upgrade_dependencies", return_value=True) as upgrade:
            results, actions = self.retry(fix=True)
        upgrade.assert_called_once_with(str(self.prefix), ["anaconda-client"], "mamba", ["conda-forge"])
        self.assertTrue(results[0]["ok"])
        self.assertEqual(actions[-1]["action"], "timeout_upgrade_recheck")

    def test_local_pip_package_is_not_upgraded_for_timeout(self):
        (self.dist / "INSTALLER").write_text("pip", encoding="utf-8")
        (self.dist / "direct_url.json").write_text('{"url":"file:///private/build"}', encoding="utf-8")
        with patch.object(imports, "check_import", return_value=(False, "Import timed out (>90s)")), \
                patch.object(imports, "is_conda_env", return_value=True), \
                patch.object(imports, "get_env_package_entries", return_value=[]), \
                patch.object(imports, "conda_upgrade_dependencies") as upgrade:
            self.retry(fix=True)
        upgrade.assert_not_called()

    def test_retry_revealing_real_error_is_not_hidden_as_timeout(self):
        with patch.object(imports, "check_import", return_value=(False, "ImportError: broken dependency")), \
                patch.object(imports, "conda_upgrade_dependencies") as upgrade:
            results, _ = self.retry(fix=True)
        self.assertFalse(imports._is_import_timeout_error(results[0]["error"]))
        upgrade.assert_not_called()

    def test_remaining_timeout_makes_report_incomplete_and_exit_nonzero(self):
        args = Namespace(env=[str(self.prefix)], full=True, fix=False, json=True)
        with patch.object(imports, "discover_envs", return_value=([str(self.prefix)], str(self.prefix), "mamba")), \
                patch.object(imports, "get_python_exe", return_value="target-python"), \
                patch.object(discovery, "get_site_packages", return_value=[str(self.prefix)]), \
                patch.object(imports, "get_toplevel_imports", return_value=["binstar_client"]), \
                patch.object(imports, "_run_import_checks_parallel", return_value=[self.result]), \
                patch.object(imports, "check_import", return_value=(False, "Import timed out (>90s)")), \
                contextlib.redirect_stdout(io.StringIO()):
            outcome = imports.verify_imports(args)
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["exit_code"], 1)
        self.assertFalse(outcome["report"]["complete"])
        self.assertEqual(len(outcome["report"]["timeouts"]), 1)
        self.assertEqual(outcome["report"]["failures"], [])


if __name__ == "__main__":
    unittest.main()
