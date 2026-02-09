import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class _Proc:
    def __init__(self, rc=0):
        self._rc = rc

    def wait(self, timeout=None):
        return self._rc

    def terminate(self):
        return None


class TestSubprocessUtilsWindowsFallback(unittest.TestCase):
    def test_run_cmd_stdout_to_file_falls_back_to_cmd_exe_on_nt(self):
        # This test is platform-independent by mocking `os.name` and `Popen`.
        from env_repair.subprocess_utils import run_cmd_stdout_to_file
        import env_repair.subprocess_utils as su

        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "out.txt"

            calls = []

            def fake_popen(args, stdout=None, stderr=None):
                calls.append(args)
                # First attempt: pretend the executable is missing (e.g. conda.bat in PATH).
                if len(calls) == 1:
                    raise FileNotFoundError
                # Fallback: write something to the provided stdout file.
                if stdout is not None:
                    stdout.write("ok\n")
                    stdout.flush()
                return _Proc(0)

            with patch.object(su.os, "name", "nt"):
                with patch.object(su.subprocess, "Popen", side_effect=fake_popen):
                    with out_path.open("w", encoding="utf-8") as f:
                        rc = run_cmd_stdout_to_file(["conda", "env", "export", "-p", "X"], stdout_file=f)

            self.assertEqual(rc, 0)
            self.assertGreaterEqual(len(calls), 2)
            # Second call should be via cmd.exe wrapper.
            self.assertEqual(calls[1][0].lower(), "cmd")
            self.assertEqual(out_path.read_text(encoding="utf-8"), "ok\n")

