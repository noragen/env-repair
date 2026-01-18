import json
import tempfile
import unittest
from pathlib import Path

from env_repair.clobber import build_conda_file_owner_map, extract_paths_from_text, to_relpath


class TestClobberParse(unittest.TestCase):
    def test_extracts_paths_inside_prefix(self):
        prefix = r"C:\Anaconda3"
        log = r"ClobberError: The package cannot be installed due to a path conflict.\n  path: 'C:\\Anaconda3\\Lib\\site-packages\\x.py'\n"
        paths = extract_paths_from_text(log, env_prefix=prefix)
        self.assertEqual(paths, [r"C:\Anaconda3\Lib\site-packages\x.py"])

    def test_owner_map_from_conda_meta_files(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td)
            cm = env / "conda-meta"
            cm.mkdir(parents=True, exist_ok=True)
            record = cm / "demo-1.0-0.json"
            record.write_text(
                json.dumps({"name": "demo", "version": "1.0", "build": "0", "files": ["Lib/site-packages/x.py"]}),
                encoding="utf-8",
            )
            owners = build_conda_file_owner_map(str(env))
            self.assertIn("Lib/site-packages/x.py", owners)
            self.assertEqual(owners["Lib/site-packages/x.py"]["name"], "demo")

    def test_relpath(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td)
            p = env / "Lib" / "site-packages" / "x.py"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
            rel = to_relpath(str(env), str(p))
            self.assertEqual(rel, "Lib/site-packages/x.py")


if __name__ == "__main__":
    unittest.main()

