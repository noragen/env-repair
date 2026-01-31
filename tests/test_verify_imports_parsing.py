import os
import tempfile
import unittest
from pathlib import Path

from env_repair.verify_imports import (
    _extract_missing_module_name,
    _extract_solver_offenders,
    _load_verify_imports_blacklist,
    _save_verify_imports_blacklist,
)


class TestVerifyImportsParsing(unittest.TestCase):
    def test_extract_missing_module_name(self):
        err = "ModuleNotFoundError: No module named 'numpy'"
        self.assertEqual(_extract_missing_module_name(err), "numpy")
        err2 = "ModuleNotFoundError: No module named pyproject_api"
        self.assertEqual(_extract_missing_module_name(err2), "pyproject_api")
        err3 = "No module named 'numpy.linalg'"
        self.assertEqual(_extract_missing_module_name(err3), "numpy")

    def test_extract_solver_offenders(self):
        text = """error    libmamba Could not solve for environment specs
    The following packages are incompatible
    ├─ pysimplegui ==5.0.4 pyhd8ed1ab_0 does not exist (perhaps a typo or a missing channel);
    └─ tables =* * does not exist (perhaps a typo or a missing channel).
"""
        offenders = _extract_solver_offenders(text)
        self.assertIn("pysimplegui", offenders)
        self.assertIn("tables", offenders)

    def test_blacklist_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            prev = Path.cwd()
            os.chdir(td)
            try:
                data = {"3.13": {"tables": {"conda": "tables", "reason": "solver_incompatible"}}}
                _save_verify_imports_blacklist(data)
                loaded = _load_verify_imports_blacklist()
                self.assertIn("3.13", loaded)
                self.assertIn("tables", loaded["3.13"])
            finally:
                os.chdir(prev)


if __name__ == "__main__":
    unittest.main()

