import json
import tempfile
import unittest
from pathlib import Path

from env_repair.repair import _conda_meta_owns_distinfo


class TestCaseConflictCondaOwned(unittest.TestCase):
    def test_conda_meta_owns_distinfo_true(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td)
            meta = env / "conda-meta"
            meta.mkdir(parents=True, exist_ok=True)
            record = {
                "name": "pydrive",
                "version": "1.3.1",
                "build": "py_1",
                "files": [
                    "Lib/site-packages/PyDrive-1.3.1.dist-info/METADATA",
                    "Lib/site-packages/pydrive/__init__.py",
                ],
            }
            (meta / "pydrive-1.3.1-py_1.json").write_text(json.dumps(record), encoding="utf-8")
            self.assertTrue(
                _conda_meta_owns_distinfo(
                    str(env),
                    conda_pkg_name="pydrive",
                    dist_name="PyDrive",
                    version="1.3.1",
                )
            )

    def test_conda_meta_owns_distinfo_false(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td)
            meta = env / "conda-meta"
            meta.mkdir(parents=True, exist_ok=True)
            record = {"name": "pydrive", "version": "1.3.1", "build": "py_1", "files": []}
            (meta / "pydrive-1.3.1-py_1.json").write_text(json.dumps(record), encoding="utf-8")
            self.assertFalse(
                _conda_meta_owns_distinfo(
                    str(env),
                    conda_pkg_name="pydrive",
                    dist_name="PyDrive",
                    version="1.3.1",
                )
            )


if __name__ == "__main__":
    unittest.main()

