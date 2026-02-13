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

    def test_detects_missing_depends_even_when_record_has_common_keys(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td)
            cm = env / "conda-meta"
            cm.mkdir(parents=True, exist_ok=True)
            bad = cm / "python-3.10.19-hc20f281_3_cpython.json"
            bad.write_text(
                json.dumps(
                    {
                        "name": "python",
                        "version": "3.10.19",
                        "build": "hc20f281_3_cpython",
                        "subdir": "win-64",
                    }
                ),
                encoding="utf-8",
            )

            issues = scan_conda_meta_json(str(env))
            types = {i.get("type") for i in issues}
            self.assertIn("conda-meta-missing-depends", types)

    def test_noarch_record_without_depends_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td)
            cm = env / "conda-meta"
            cm.mkdir(parents=True, exist_ok=True)
            ok = cm / "helper-1.0.0-py_0.json"
            ok.write_text(
                json.dumps(
                    {
                        "name": "helper",
                        "version": "1.0.0",
                        "build": "py_0",
                        "noarch": "python",
                    }
                ),
                encoding="utf-8",
            )

            issues = scan_conda_meta_json(str(env))
            types = {i.get("type") for i in issues}
            self.assertNotIn("conda-meta-missing-depends", types)


if __name__ == "__main__":
    unittest.main()

