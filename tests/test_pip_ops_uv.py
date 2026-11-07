import unittest
from unittest import mock

from env_repair.pip_ops import _get_pip_cmd
from env_repair.cli import build_parser


class TestUvCommands(unittest.TestCase):
    def test_uv_targets_requested_python_and_maps_reinstall(self):
        with mock.patch("env_repair.pip_ops.which_path", return_value="uv.exe"):
            command = _get_pip_cmd("target/python.exe", True, ["install", "--force-reinstall", "example"])
        self.assertEqual(command, ["uv.exe", "pip", "install", "--reinstall", "example", "--python", "target/python.exe"])

    def test_uninstall_without_confirmation_flag(self):
        with mock.patch("env_repair.pip_ops.which_path", return_value="uv.exe"):
            command = _get_pip_cmd("python", True, ["uninstall", "-y", "example"])
        self.assertNotIn("-y", command)

    def test_missing_uv_falls_back_to_pip(self):
        with mock.patch("env_repair.pip_ops.which_path", return_value=None):
            self.assertEqual(_get_pip_cmd("python", True, ["freeze"]), ["python", "-m", "pip", "freeze"])

    def test_ignore_installed_preserves_pip_recovery(self):
        with mock.patch("env_repair.pip_ops.which_path", return_value="uv.exe"):
            command = _get_pip_cmd("python", True, ["install", "--ignore-installed", "example"])
        self.assertEqual(command[:3], ["python", "-m", "pip"])

    def test_uv_flag_before_and_after_subcommands(self):
        parser = build_parser()
        for subcommand in ["one-shot", "verify-imports"]:
            for arguments in [["--use-uv", subcommand], [subcommand, "--use-uv"]]:
                self.assertTrue(parser.parse_args(arguments).use_uv)
