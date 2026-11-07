import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_repair import repair, scan


class TestInvalidDistributionMetadata(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.prefix = Path(temporary.name)
        self.site = self.prefix / "lib" / "python" / "site-packages"
        self.site.mkdir(parents=True)
        (self.prefix / "conda-meta").mkdir()

    def directory(self, name, metadata=None):
        path = self.site / name
        path.mkdir()
        if metadata is not None:
            (path / "METADATA").write_text(metadata, encoding="utf-8")
        return path

    def test_scan_detects_missing_and_empty_metadata(self):
        self.directory("attrdict-2.0.1.dist-info")
        self.directory("bcrypt-5.0.0.dist-info", "")
        self.directory("watchdog-6.0.0.dist-info", "Name: watchdog\nVersion: 6.0.0\n")
        invalid = [issue for issue in scan.scan_dist_info(self.site) if issue["type"] == "invalid-dist-info"]
        self.assertEqual({issue["name"] for issue in invalid}, {"attrdict-2.0.1.dist-info", "bcrypt-5.0.0.dist-info"})

    def test_quarantines_metadata_not_code_and_relinks_provider(self):
        path = self.directory("bcrypt-5.0.0.dist-info")
        (path / "RECORD").write_text("record backup", encoding="utf-8")
        module = self.site / "bcrypt.py"
        module.write_text("sentinel = True", encoding="utf-8")
        record = {"name": "bcrypt", "files": [str(path.relative_to(self.prefix)).replace("\\", "/") + "/METADATA"]}
        (self.prefix / "conda-meta" / "bcrypt.json").write_text(json.dumps(record), encoding="utf-8")
        env = {"path": str(self.prefix), "issues": scan.scan_dist_info(self.site)}
        with patch.object(repair, "conda_install", return_value=True) as install, contextlib.redirect_stdout(io.StringIO()):
            fixes = repair._repair_invalid_dist_info(env, "mamba", ["conda-forge"])
        self.assertFalse(path.exists())
        self.assertTrue(module.exists())
        self.assertEqual((Path(fixes[0]["destination"]) / "RECORD").read_text(encoding="utf-8"), "record backup")
        self.assertEqual(install.call_args.args[1], ["bcrypt"])
        self.assertTrue(install.call_args.kwargs["force_reinstall"])

    def test_no_owner_does_not_guess_pip_package_or_delete_code(self):
        self.directory("attrdict-2.0.1.dist-info")
        env = {"path": str(self.prefix), "issues": scan.scan_dist_info(self.site)}
        with patch.object(repair, "conda_install") as install, contextlib.redirect_stdout(io.StringIO()):
            fixes = repair._repair_invalid_dist_info(env, "mamba", [])
        self.assertTrue(fixes[0]["fixed"])
        install.assert_not_called()

    def test_scan_results_do_not_delete_metadata_recreated_before_fix(self):
        path = self.directory("watchdog-6.0.0.dist-info")
        env = {"path": str(self.prefix), "issues": scan.scan_dist_info(self.site)}
        (path / "METADATA").write_text("Name: watchdog\nVersion: 6.0.0\n", encoding="utf-8")
        self.assertEqual(repair._repair_invalid_dist_info(env, "mamba", []), [])
        self.assertTrue(path.exists())

    def test_paths_outside_prefix_are_not_moved(self):
        env = {"path": str(self.prefix / "other"), "issues": scan.scan_dist_info(self.site)}
        path = self.directory("bcrypt-5.0.0.dist-info")
        env["issues"] = scan.scan_dist_info(self.site)
        fixes = repair._repair_invalid_dist_info(env, None, [])
        self.assertFalse(fixes[0]["fixed"])
        self.assertTrue(path.exists())
