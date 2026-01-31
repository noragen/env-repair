import json
import tempfile
import unittest
from pathlib import Path

from env_repair.repair import _blacklist_add, _extract_incompatible_specs, _load_adopt_pip_blacklist, _save_adopt_pip_blacklist


class TestAdoptPipBlacklist(unittest.TestCase):
    def test_extracts_incompatible_specs(self):
        text = """
error    libmamba Could not solve for environment specs
    The following packages are incompatible
    ├─ langchain-chroma =* * is installable with the potential options
    └─ pin on python =3.13 * is not installable because it requires
"""
        names = _extract_incompatible_specs(text)
        self.assertIn("langchain-chroma", names)
        self.assertIn("pin", names)

    def test_blacklist_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            (cwd / ".env_repair").mkdir(parents=True, exist_ok=True)
            prev = Path.cwd()
            try:
                # Change CWD so the helper path writes into temp folder.
                import os

                os.chdir(cwd)
                blocked = {}
                self.assertTrue(
                    _blacklist_add(
                        blocked,
                        pip_name="langchain-chroma",
                        pip_version="9.9.9",
                        conda_name="langchain-chroma",
                        reason="solver_incompatible",
                    )
                )
                _save_adopt_pip_blacklist(blocked)
                loaded = _load_adopt_pip_blacklist()
                self.assertIn("langchain-chroma", loaded)
                self.assertIn("9.9.9", loaded["langchain-chroma"])
            finally:
                os.chdir(prev)


if __name__ == "__main__":
    unittest.main()
