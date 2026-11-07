import contextlib
import io
import json
import os
import string
import unittest
from unittest.mock import patch

from env_repair import cli, i18n, qt_repair
from env_repair.i18n_messages import TEXTS


class TestLocalizedSolverOutput(unittest.TestCase):
    def test_all_translations_have_matching_parameters_in_supported_languages(self):
        formatter = string.Formatter()
        self.assertEqual(set(i18n.MESSAGES), {"en", "de"})
        self.assertEqual(set(i18n.MESSAGES["en"]), set(i18n.MESSAGES["de"]))
        for source, translated in TEXTS.items():
            with self.subTest(source=source):
                fields = lambda value: {field for _, field, _, _ in formatter.parse(value) if field is not None}
                self.assertEqual(fields(source), fields(translated))
                self.assertTrue(translated)

    def test_solver_json_becomes_readable_reason_and_remains_structured_in_report(self):
        payload = {"success": False, "log_history": [{"message": "Python 3.15 conflicts with PyQt6", "location": "internal.cpp:123"}],
                   "solver_problems": ["unsupported request"]}
        with patch.object(qt_repair, "run_cmd_capture", return_value=(1, json.dumps(payload), "warning")):
            with self.assertRaises(qt_repair.QtSolveError) as raised:
                qt_repair._preview_qt_install("fixture", "mamba", [], "pyqt6")
        self.assertEqual(str(raised.exception), "Python 3.15 conflicts with PyQt6")
        self.assertEqual(raised.exception.diagnostic, payload)
        self.assertNotIn("log_history", str(raised.exception))
        self.assertNotIn("internal.cpp", str(raised.exception))

    def test_incomplete_solver_json_never_leaks_to_console_reason(self):
        error = qt_repair._solver_failure('{"success": false, "unexpected": "internal"}', "")
        self.assertNotIn("{", str(error))
        self.assertEqual(error.diagnostic["unexpected"], "internal")

    def test_windows_locale_detection_does_not_need_removed_getdefaultlocale(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(i18n.locale, "getlocale", return_value=("de_DE", "UTF-8")):
            self.assertEqual(i18n.detect_lang(), "de")

    def test_formatted_reason_uses_catalog_without_translating_machine_data(self):
        source = "blacklisted for python 3.15 (previous solver failure)"
        with patch.dict(os.environ, {"LC_ALL": "de_DE"}):
            self.assertIn("Für Python 3.15 gesperrt", i18n.tr(source))
        self.assertEqual(source, "blacklisted for python 3.15 (previous solver failure)")

    def test_progress_is_printed_before_solver_and_json_does_not_leak_to_console(self):
        payload = {"success": False, "solver_problems": ["Python pin conflicts with selected binding"]}
        output = io.StringIO()
        def capture(*args, **kwargs):
            self.assertIn("bitte auf den Solver warten", output.getvalue())
            return 1, json.dumps(payload), ""
        with patch.dict(os.environ, {"LC_ALL": "de_DE", "QT_API": ""}), \
                patch.object(qt_repair.sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", side_effect=["2", "0"]), \
                patch.object(qt_repair, "run_cmd_capture", side_effect=capture), contextlib.redirect_stdout(output):
            _, actions = qt_repair.repair_qt_bindings(
                [{"import": "qtpy", "error": "qtpy.QtBindingsNotFoundError"}], python_exe="fixture-python",
                env_path="fixture", manager="mamba", channels=[], in_conda_env=True, check_import=lambda *args: (False, ""))
        self.assertIn("Qt-Backend wurde nicht installiert", output.getvalue())
        self.assertNotIn('"success"', output.getvalue())
        self.assertEqual(actions[0]["diagnostic"], payload)

    def test_cli_help_uses_detected_language_and_english_remains_available(self):
        for language, expected in (("de_DE", "Nur kritische Imports prüfen"), ("en_US", "Verify imports in critical mode only")):
            output = io.StringIO()
            with patch.dict(os.environ, {"LC_ALL": language}), contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                cli.build_parser().parse_args(["one-shot", "--help"])
            self.assertIn(expected, output.getvalue())

    def test_german_messages_use_utf8_umlauts_without_corrupting_english_or_placeholders(self):
        with patch.dict(os.environ, {"LC_ALL": "de_DE"}):
            message = i18n.tr("Cleaning up temporary sandbox: {value0}", value0="C:/für/test")
            self.assertEqual(message, "Temporäres Testenvironment wird aufgeräumt: C:/für/test")
            self.assertEqual(message.encode("utf-8").decode("utf-8"), message)
            self.assertIn("außerhalb", i18n.tr("sandbox report must be stored outside the temporary workspace"))
            self.assertIn("neueres", i18n.tr("numba import requires newer llvmlite; reinstalling llvmlite"))
        with patch.dict(os.environ, {"LC_ALL": "en_US"}):
            self.assertEqual(i18n.tr("Choose Qt backend [0]: "), "Choose Qt backend [0]: ")
