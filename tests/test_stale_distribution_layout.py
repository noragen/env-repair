import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_repair import repair, scan


class TestStaleDistributionLayout(unittest.TestCase):
    def fixture(self, root):
        old = root / "Lib/site-packages/pyarrow-23.0.0.dist-info"
        current = root / "lib/python/site-packages/pyarrow-26.0.0.dist-info"
        for dist, version in ((old, "23.0.0"), (current, "26.0.0")):
            dist.mkdir(parents=True)
            (dist / "METADATA").write_text(f"Name: pyarrow\nVersion: {version}\n", encoding="utf-8")
            (dist / "INSTALLER").write_text("pip\n", encoding="utf-8")
        code = current.parent / "pyarrow/__init__.py"
        code.parent.mkdir()
        code.write_text("sentinel = True", encoding="utf-8")
        meta = root / "conda-meta"
        meta.mkdir()
        relative = (current / "METADATA").relative_to(root).as_posix()
        record = {"name": "pyarrow-core", "version": "26.0.0", "files": [relative],
                  "paths_data": {"paths": [{"_path": relative, "sha256": hashlib.sha256((current / "METADATA").read_bytes()).hexdigest()}]}}
        record_path = meta / "pyarrow-core-26.0.0-build.json"
        record_path.write_text(json.dumps(record), encoding="utf-8")
        return old, current, code, record_path

    def test_old_layout_metadata_is_quarantined_once_without_pip_or_reinstall(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, current, code, record = self.fixture(root)
            original_record = record.read_bytes()
            issues = scan.scan_conda_pip_overwrites(root, [old.parent, current.parent])
            self.assertEqual([issue["type"] for issue in issues], ["stale-dist-info"])
            env = {"path": str(root), "python": "fixture-python", "issues": issues}
            with patch.object(repair, "pip_uninstall") as uninstall, patch.object(repair, "conda_install") as install:
                fixes = repair._cleanup_duplicate_dist_info(env, False)
                repair._apply_same_version_case_conflicts(env, [], "mamba", [], False, False, False)
            uninstall.assert_not_called()
            install.assert_not_called()
            self.assertTrue(fixes[0]["fixed"])
            self.assertTrue(Path(fixes[0]["destination"]).is_dir())
            self.assertFalse(old.exists())
            self.assertTrue(current.is_dir())
            self.assertEqual(code.read_text(), "sentinel = True")
            self.assertEqual(record.read_bytes(), original_record)
            self.assertEqual(scan.scan_conda_pip_overwrites(root, [old.parent, current.parent]), [])

    def test_missing_or_changed_current_owner_is_not_treated_as_healthy(self):
        for missing in (False, True):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                old, current, _, _ = self.fixture(root)
                metadata = current / "METADATA"
                if missing:
                    metadata.unlink()
                else:
                    metadata.write_text("modified", encoding="utf-8")
                issues = scan.scan_conda_pip_overwrites(root, [old.parent, current.parent])
                self.assertTrue(any(issue["type"] == "conda-pip-overwrite" for issue in issues))
                self.assertFalse(any(issue["type"] == "stale-dist-info" for issue in issues))

    def test_cleanup_revalidates_the_owner_before_touching_stale_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, current, _, _ = self.fixture(root)
            issues = scan.scan_conda_pip_overwrites(root, [old.parent, current.parent])
            (current / "METADATA").write_text("modified after scan", encoding="utf-8")
            result = repair._cleanup_duplicate_dist_info({"path": str(root), "issues": issues}, False)
            self.assertFalse(result[0]["fixed"])
            self.assertTrue(old.exists())

    def test_genuine_in_place_overlay_is_still_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, current, _, _ = self.fixture(root)
            self.assertEqual(scan.scan_conda_pip_overwrites(root, [current.parent]), [])
            (current / "METADATA").write_text("Name: pyarrow\nVersion: 26.0.0\nModified: pip\n", encoding="utf-8")
            issues = scan.scan_conda_pip_overwrites(root, [current.parent])
            self.assertEqual([issue["type"] for issue in issues], ["conda-pip-overwrite"])
            self.assertEqual(issues[0]["package"], "pyarrow-core")
