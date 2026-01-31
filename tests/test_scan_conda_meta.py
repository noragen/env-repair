import json
import tempfile
import unittest
from pathlib import Path

from env_repair.scan import scan_conda_meta_json


class TestScanCondaMetaJson(unittest.TestCase):
    def test_missing_depends_not_flagged_if_record_complete(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td)
            meta = env / "conda-meta"
            meta.mkdir(parents=True, exist_ok=True)
            p = meta / "pybind11-abi-11-hc364b38_1.json"
            p.write_text(json.dumps({"name": "pybind11-abi", "version": "11", "build": "hc364b38_1"}), encoding="utf-8")
            issues = scan_conda_meta_json(str(env))
            self.assertEqual(issues, [])

    def test_missing_depends_flagged_if_record_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td)
            meta = env / "conda-meta"
            meta.mkdir(parents=True, exist_ok=True)
            p = meta / "somepkg-1.0-0.json"
            p.write_text(json.dumps({"version": "1.0"}), encoding="utf-8")
            issues = scan_conda_meta_json(str(env))
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].get("type"), "conda-meta-missing-depends")


if __name__ == "__main__":
    unittest.main()

