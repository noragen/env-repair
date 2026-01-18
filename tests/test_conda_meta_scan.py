import json
import tempfile
import unittest
from pathlib import Path

from env_repair.scan import parse_conda_meta_filename, scan_conda_meta_json


class TestCondaMetaScan(unittest.TestCase):
    def test_parse_conda_meta_filename(self):
        name, ver, build = parse_conda_meta_filename("nodejs-25.2.1-he453025_1.json")
        self.assertEqual(name, "nodejs")
        self.assertEqual(ver, "25.2.1")
        self.assertEqual(build, "he453025_1")

    def test_detects_missing_depends(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td)
            cm = env / "conda-meta"
            cm.mkdir(parents=True, exist_ok=True)
            bad = cm / "nodejs-25.2.1-he453025_1.json"
            bad.write_text(json.dumps({"name": "nodejs", "version": "25.2.1"}), encoding="utf-8")

            issues = scan_conda_meta_json(str(env))
            types = {i.get("type") for i in issues}
            self.assertIn("conda-meta-missing-depends", types)
            self.assertEqual(issues[0].get("package"), "nodejs")


if __name__ == "__main__":
    unittest.main()

