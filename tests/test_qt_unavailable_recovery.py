import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from env_repair import qt_repair
from env_repair import conda_ops
from env_repair import package_sandbox


class TestQtUnavailableRecovery(unittest.TestCase):
    def setUp(self):
        self.failures = [{"import": "qtpy", "error": "qtpy.QtBindingsNotFoundError: No Qt bindings could be found"}]

    def recover(self, prefix="fixture-prefix"):
        with contextlib.redirect_stdout(io.StringIO()):
            return qt_repair._offer_qt_recovery(self.failures, env_path=prefix, python_exe="fixture-python",
                                               manager="mamba", channels=["conda-forge"], package="pyqt6", use_uv=False)

    def test_default_invalid_eof_and_interrupt_keep_environment(self):
        for answer in ("", "0", "y", "invalid", EOFError(), KeyboardInterrupt()):
            with self.subTest(answer=answer), patch("builtins.input", side_effect=answer if isinstance(answer, BaseException) else lambda _: answer), \
                    patch.object(qt_repair, "remove_unfixable") as remove, patch.object(qt_repair, "conda_install") as install:
                failures, action = self.recover()
                self.assertEqual(failures, self.failures)
                self.assertFalse(action["ok"])
                remove.assert_not_called()
                install.assert_not_called()

    def test_qt_removal_always_requires_separate_consent(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary) / "qtpy-2.dist-info"
            dist.mkdir()
            (dist / "METADATA").write_text("Name: qtpy\nVersion: 2\n", encoding="utf-8")
            self.failures[0]["dist_path"] = dist
            with patch("builtins.input", return_value="1"), \
                    patch.object(conda_ops, "get_env_package_entries", return_value=[{"name": "qtpy", "channel": "conda-forge"}]), \
                    patch.object(qt_repair, "remove_unfixable", return_value=({"qtpy"}, [{"ok": True}])) as remove:
                failures, action = self.recover(temporary)
            self.assertTrue(failures[0]["removed"])
            self.assertTrue(action["ok"])
            self.assertFalse(remove.call_args.kwargs["force"])
            self.assertTrue(remove.call_args.kwargs["interactive"])

    def test_unknown_consumer_prevents_removal(self):
        with patch("builtins.input", return_value="1"), \
                patch.object(conda_ops, "get_env_package_entries", return_value=[]), patch.object(qt_repair, "remove_unfixable") as remove:
            failures, action = self.recover()
        self.assertFalse(action["ok"])
        remove.assert_not_called()
        self.assertEqual(failures, self.failures)

    def test_lower_python_only_tests_targets_and_never_installs_in_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            meta = Path(temporary) / "conda-meta"
            meta.mkdir()
            (meta / "python-3.15.2-0.json").write_text(json.dumps({"name": "python", "version": "3.15.2"}), encoding="utf-8")
            dist = Path(temporary) / "qtpy-2.dist-info"
            dist.mkdir()
            (dist / "METADATA").write_text("Name: qtpy\nVersion: 2\n", encoding="utf-8")
            self.failures[0]["dist_path"] = dist
            with patch("builtins.input", side_effect=["2", "yes"]), \
                    patch.object(package_sandbox, "test_packages", return_value={"ok": True}) as solve, \
                    patch.object(qt_repair, "_preview_qt_install") as original_preview, \
                    patch.object(qt_repair, "conda_install") as install:
                failures, action = self.recover(temporary)
            self.assertNotIn("command", action)
            self.assertEqual(solve.call_args.kwargs["python_spec"], "python=3.14")
            self.assertEqual(solve.call_args.kwargs["targets"], ["qtpy"])
            self.assertEqual(solve.call_args.kwargs["conda_specs"], ["pyqt6"])
            original_preview.assert_not_called()
            self.assertFalse(action["ok"])
            self.assertEqual(failures, self.failures)
            install.assert_not_called()

    def test_no_compatible_lower_python_is_reported_without_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            meta = Path(temporary) / "conda-meta"
            meta.mkdir()
            (meta / "python-3.15.2-0.json").write_text(json.dumps({"name": "python", "version": "3.15.2"}), encoding="utf-8")
            dist = Path(temporary) / "qtpy-2.dist-info"
            dist.mkdir()
            (dist / "METADATA").write_text("Name: qtpy\nVersion: 2\n", encoding="utf-8")
            self.failures[0]["dist_path"] = dist
            with patch("builtins.input", side_effect=["2", "yes"]), patch.object(package_sandbox, "test_packages", return_value={"ok": False}) as solve:
                _, action = self.recover(temporary)
            self.assertNotIn("command", action)
            self.assertEqual(solve.call_count, 2)
            self.assertEqual(len(action["attempts"]), 2)

    def test_solver_failure_opens_recovery_menu_and_default_does_not_install(self):
        with patch.object(qt_repair.sys.stdin, "isatty", return_value=True), \
                patch.dict(qt_repair.os.environ, {"QT_API": ""}), patch("builtins.input", side_effect=["2", "0"]) as prompt, \
                patch.object(qt_repair, "_preview_qt_install", side_effect=qt_repair.QtSolveError("no candidates")), \
                patch.object(qt_repair, "conda_install") as install, contextlib.redirect_stdout(io.StringIO()):
            failures, actions = qt_repair.repair_qt_bindings(self.failures, python_exe="fixture-python", env_path="fixture", manager="mamba",
                                                            channels=[], in_conda_env=True, check_import=Mock())
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(actions[-1]["choice"], "keep")
        self.assertEqual(failures, self.failures)
        install.assert_not_called()

    def test_migration_preview_must_match_requested_python(self):
        payload = {"success": True, "actions": {"UNLINK": [{"name": "python", "version": "3.15.2"}], "LINK": [{"name": "python", "version": "3.12.9"}]}}
        with patch.object(qt_repair, "run_cmd_capture", return_value=(0, json.dumps(payload), "")):
            with self.assertRaisesRegex(ValueError, "requested Python"):
                qt_repair._preview_qt_install("fixture", "mamba", [], "pyqt6", python_spec="python=3.14")

    def test_migration_preview_accepts_only_the_explicit_python_change(self):
        payload = {"success": True, "actions": {"UNLINK": [{"name": "python", "version": "3.15.2"}],
                                                  "LINK": [{"name": "python", "version": "3.14.8"}, {"name": "pyqt6", "version": "6.11"}]}}
        with patch.object(qt_repair, "run_cmd_capture", return_value=(0, json.dumps(payload), "")) as command:
            result = qt_repair._preview_qt_install("fixture", "mamba", [], "pyqt6", python_spec="python=3.14")
        self.assertIn("python=3.14", command.call_args.args[0])
        self.assertNotIn("--no-pin", command.call_args.args[0])
        self.assertEqual(result["LINK"][0]["version"], "3.14.8")
