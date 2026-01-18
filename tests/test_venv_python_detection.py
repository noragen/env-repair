import os
import tempfile
import unittest
from pathlib import Path

from env_repair.discovery import get_python_exe


class TestVenvPythonDetection(unittest.TestCase):
    def test_detects_venv_scripts_python_on_windows(self):
        if os.name != "nt":
            self.skipTest("Windows-only path layout test")
        with tempfile.TemporaryDirectory() as td:
            env = Path(td)
            scripts = env / "Scripts"
            scripts.mkdir(parents=True, exist_ok=True)
            py = scripts / "python.exe"
            py.write_bytes(b"")
            self.assertEqual(get_python_exe(str(env)), str(py))


if __name__ == "__main__":
    unittest.main()

