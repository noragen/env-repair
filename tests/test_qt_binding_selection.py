import contextlib
import io
import json
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

from env_repair import qt_repair


ERROR = "qtpy.QtBindingsNotFoundError: No Qt bindings could be found"


class TestQtBindingSelection(unittest.TestCase):
    def setUp(self):
        self.failures = [{"import": name, "error": ERROR} for name in ("qtpy", "qtawesome", "superqt")]
        self.check = Mock(return_value=(True, None))
        self.preview = {"LINK": [{"name": "pyside6", "version": "6.10"}], "UNLINK": []}

    def run_selection(self, *, interactive=True, conda=True, manager="mamba", use_uv=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return qt_repair.repair_qt_bindings(self.failures, python_exe="target-python", env_path="fixture-prefix",
                                               manager=manager, channels=["conda-forge"], in_conda_env=conda,
                                               check_import=self.check, interactive=interactive, use_uv=use_uv)

    def test_one_explicit_selection_installs_one_binding_and_rechecks_all_consumers(self):
        with patch.dict(os.environ, {"QT_API": ""}), patch.object(qt_repair.sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="1") as prompt, \
                patch.object(qt_repair, "_preview_qt_install", return_value=self.preview), \
                patch.object(qt_repair, "conda_install", return_value=True) as install:
            failures, actions = self.run_selection()
        self.assertEqual(failures, [])
        self.assertTrue(actions[0]["ok"])
        self.assertEqual(actions[0]["selected"], "pyside6")
        prompt.assert_called_once()
        install.assert_called_once_with("fixture-prefix", ["pyside6"], "mamba", ["conda-forge"], ignore_pinned=False, force_reinstall=False)
        self.assertEqual(self.check.call_count, 3)

    def test_empty_skip_invalid_yes_and_cancel_never_install(self):
        for answer in ("", "0", "y", "invalid", EOFError(), KeyboardInterrupt()):
            with self.subTest(answer=answer), patch.dict(os.environ, {"QT_API": "", "ENV_REPAIR_AUTO_YES": "1"}), \
                    patch.object(qt_repair.sys.stdin, "isatty", return_value=True), \
                    patch("builtins.input", side_effect=answer if isinstance(answer, BaseException) else lambda _: answer), \
                    patch.object(qt_repair, "conda_install") as install:
                failures, _ = self.run_selection()
                self.assertEqual(failures, self.failures)
                install.assert_not_called()

    def test_json_or_noninteractive_mode_keeps_packages_without_prompt(self):
        for interactive, tty in ((False, True), (True, False)):
            with self.subTest(interactive=interactive, tty=tty), patch.object(qt_repair.sys.stdin, "isatty", return_value=tty), \
                    patch("builtins.input") as prompt, patch.object(qt_repair, "conda_install") as install:
                failures, actions = self.run_selection(interactive=interactive)
                self.assertEqual(failures, self.failures)
                self.assertIn("explicit interactive choice", actions[0]["reason"])
                prompt.assert_not_called()
                install.assert_not_called()

    def test_selected_qt_api_is_preserved_and_limits_choices(self):
        with patch.dict(os.environ, {"QT_API": "pyqt6"}), patch.object(qt_repair.sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="1"), patch.object(qt_repair, "_preview_qt_install", return_value=self.preview), \
                patch.object(qt_repair, "conda_install", return_value=True) as install:
            _, actions = self.run_selection()
            self.assertEqual(os.environ["QT_API"], "pyqt6")
        self.assertEqual(actions[0]["selected"], "pyqt6")
        self.assertEqual(install.call_args.args[1], ["pyqt6"])

    def test_conflicting_choice_does_not_override_qt_api(self):
        with patch.dict(os.environ, {"QT_API": "pyqt6"}), patch.object(qt_repair.sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="pyside6"), patch.object(qt_repair, "conda_install") as install:
            self.run_selection()
            self.assertEqual(os.environ["QT_API"], "pyqt6")
        install.assert_not_called()

    def test_bad_qt_api_does_not_prompt_or_install(self):
        with patch.dict(os.environ, {"QT_API": "invalid"}), patch.object(qt_repair.sys.stdin, "isatty", return_value=True), \
                patch("builtins.input") as prompt, patch.object(qt_repair, "conda_install") as install:
            self.run_selection()
        prompt.assert_not_called()
        install.assert_not_called()

    def test_solver_failure_or_timeout_preserves_consumers(self):
        for error in (ValueError("no compatible package"), subprocess.TimeoutExpired("mamba", 120)):
            with self.subTest(error=error), patch.dict(os.environ, {"QT_API": ""}), \
                    patch.object(qt_repair.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="1"), \
                    patch.object(qt_repair, "_preview_qt_install", side_effect=error), patch.object(qt_repair, "conda_install") as install:
                failures, actions = self.run_selection()
                self.assertEqual(failures, self.failures)
                self.assertFalse(actions[0]["ok"])
                install.assert_not_called()

    def test_successful_installer_does_not_hide_failed_import(self):
        self.check.return_value = (False, ERROR)
        with patch.dict(os.environ, {"QT_API": ""}), patch.object(qt_repair.sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="1"), patch.object(qt_repair, "_preview_qt_install", return_value=self.preview), \
                patch.object(qt_repair, "conda_install", return_value=True):
            failures, actions = self.run_selection()
        self.assertEqual(len(failures), 3)
        self.assertFalse(actions[0]["ok"])

    def test_venv_install_targets_its_python_and_only_binary_wheels(self):
        with patch.dict(os.environ, {"QT_API": ""}), patch.object(qt_repair.sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="2"), patch.object(qt_repair, "run_cmd_live", return_value=0) as run, \
                patch.object(qt_repair, "conda_install") as install:
            self.run_selection(conda=False, manager=None)
        self.assertEqual(run.call_args.args[0], ["target-python", "-m", "pip", "install", "--only-binary=:all:", "PyQt6"])
        install.assert_not_called()

    def test_conda_environment_without_manager_does_not_fall_back_to_pip(self):
        with patch.object(qt_repair.sys.stdin, "isatty", return_value=True), patch.object(qt_repair, "run_cmd_live") as run:
            failures, _ = self.run_selection(manager=None)
        self.assertEqual(failures, self.failures)
        run.assert_not_called()


class TestQtTransactionPreview(unittest.TestCase):
    def test_pyqt6_uses_its_dedicated_conda_package(self):
        package = qt_repair.QT_BINDINGS["pyqt6"][1]
        payload = {"success": True, "actions": {"LINK": [{"name": "pyqt6", "version": "6.11.0"}]}}
        with patch.object(qt_repair, "run_cmd_capture", return_value=(0, json.dumps(payload), "")) as run:
            qt_repair._preview_qt_install("fixture-prefix", "mamba", ["conda-forge"], package)
        self.assertEqual(run.call_args.args[0][-1], "pyqt6")
        self.assertNotIn("pyqt>=6,<7", run.call_args.args[0])

    def preview(self, actions):
        with patch.object(qt_repair, "run_cmd_capture", return_value=(0, json.dumps({"success": True, "actions": actions}), "")) as run:
            result = qt_repair._preview_qt_install("fixture-prefix", "mamba", ["conda-forge"], "pyside6")
        self.assertIn("--dry-run", run.call_args.args[0])
        self.assertNotIn("--no-pin", run.call_args.args[0])
        return result

    def test_compatible_transaction_is_accepted(self):
        self.assertEqual(self.preview({"LINK": [{"name": "pyside6", "version": "6"}]}), {"LINK": [{"name": "pyside6", "version": "6"}], "UNLINK": []})

    def test_python_version_change_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "Python version"):
            self.preview({"LINK": [{"name": "python", "version": "3.13"}], "UNLINK": [{"name": "python", "version": "3.15"}]})

    def test_removing_existing_packages_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "remove"):
            self.preview({"LINK": [], "UNLINK": [{"name": "pyqt", "version": "5"}]})

    def test_malformed_preview_is_blocked(self):
        with self.assertRaises(ValueError):
            self.preview({"LINK": ["unknown"]})
