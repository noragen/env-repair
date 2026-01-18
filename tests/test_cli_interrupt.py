import unittest
from unittest import mock
import io
import contextlib

from env_repair.subprocess_utils import OperationInterrupted


class TestCliInterrupt(unittest.TestCase):
    def test_cli_catches_operation_interrupted(self):
        import env_repair.cli as cli

        with (
            mock.patch.object(cli, "run", side_effect=OperationInterrupted(["mamba", "search"], returncode=130)),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            rc = cli.main(["--env", "base"])
        self.assertEqual(rc, 130)


if __name__ == "__main__":
    unittest.main()
