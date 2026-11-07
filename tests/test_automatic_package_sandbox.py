import argparse
import contextlib
import importlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_repair import package_sandbox


class TestAutomaticPackageSandbox(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        dist = self.root / "scipy-1.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text("Name: scipy\nVersion: 1\n", encoding="utf-8")
        self.failure = {"dist": dist.name, "dist_path": dist, "import": "scipy", "ok": False,
                        "error": "ImportError: DLL load failed"}

    def recover(self, failures=None, actions=()):
        with contextlib.redirect_stdout(io.StringIO()):
            return package_sandbox.recover_import_failures(
                [self.failure] if failures is None else failures, env_path=self.root, python_exe="fixture-python",
                manager="mamba", channels=["conda-forge"], python_version="3.15", actions=actions)

    def test_automatic_current_python_then_lower_without_prompt_or_original_install(self):
        with patch.object(package_sandbox, "test_packages", side_effect=[
                {"ok": False, "failure_category": "candidate-unavailable"}, {"ok": True, "scope": "target-packages"}]) as test, \
                patch("builtins.input", side_effect=AssertionError("automatic fallback must not prompt")):
            result = self.recover()
        self.assertTrue(result["ok"])
        self.assertFalse(result["original_repaired"])
        self.assertEqual([call.kwargs["python_spec"] for call in test.call_args_list], ["python=3.15", "python=3.14"])
        self.assertEqual(test.call_args.kwargs["targets"], ["scipy"])

    def test_network_unknown_and_permission_errors_do_not_trigger_downgrades(self):
        for category in ("network", "permission", "unknown", "timeout"):
            report = {"ok": False, "failure_category": category}
            if category != "unknown":
                report["failures"] = [{"import": "scipy", "error": category}]
            with self.subTest(category=category), patch.object(package_sandbox, "test_packages", return_value=report) as test:
                result = self.recover()
            self.assertFalse(result["ok"])
            self.assertEqual(test.call_count, 1)

    def test_attempts_are_bounded_and_duplicates_are_grouped(self):
        with patch.object(package_sandbox, "test_packages", return_value={"ok": False, "failure_category": "dependency-conflict"}) as test:
            result = self.recover([self.failure, self.failure])
        self.assertEqual(len(result["attempts"]), 3)
        self.assertEqual(test.call_args.kwargs["targets"], ["scipy"])

    def test_already_tested_targets_are_not_retested(self):
        with patch.object(package_sandbox, "test_packages") as test:
            result = self.recover(actions=[{"sandbox": {"targets": ["scipy"], "ok": False}}])
        test.assert_not_called()
        self.assertIn("already tested", result["skipped"][0]["reason"])

    def test_qt_backend_is_never_chosen_implicitly(self):
        failure = dict(self.failure, error="qtpy.QtBindingsNotFoundError")
        with patch.object(package_sandbox, "test_packages") as test:
            result = self.recover([failure])
        test.assert_not_called()
        self.assertIn("explicit user selection", result["skipped"][0]["reason"])

    def test_verify_fix_invokes_fallback_but_scan_only_does_not(self):
        verify = importlib.import_module("env_repair.verify_imports")
        for fix in (False, True):
            with self.subTest(fix=fix), contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(verify, "discover_envs", return_value=([str(self.root)], "base", "mamba")))
                stack.enter_context(patch.object(verify, "select_envs", return_value=[str(self.root)]))
                stack.enter_context(patch.object(verify, "get_python_exe", return_value="fixture-python"))
                stack.enter_context(patch("env_repair.discovery.get_site_packages", return_value=[str(self.root)]))
                stack.enter_context(patch.object(verify, "_run_import_checks_parallel", return_value=[self.failure]))
                stack.enter_context(patch.object(verify, "attempt_fix", return_value={"ok": False, "plan": [], "actions": []}))
                stack.enter_context(patch.object(verify, "check_import", return_value=(False, "DLL load failed")))
                stack.enter_context(patch.object(verify, "_python_major_minor", return_value="3.15"))
                stack.enter_context(patch.object(verify, "load_conda_channels", return_value=["conda-forge"]))
                stack.enter_context(patch.object(verify, "which", return_value=None))
                recovery = stack.enter_context(patch.object(package_sandbox, "recover_import_failures", return_value={"ok": True, "original_repaired": False}))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                result = verify.verify_imports(argparse.Namespace(env=[], env_single="fixture", full=True, fix=fix, json=True))
                self.assertEqual(recovery.call_count, int(fix))
                self.assertFalse(result["ok"])
                self.assertEqual(result["exit_code"], 1)
                if fix:
                    self.assertEqual(recovery.call_args.args[0][0]["dist_path"], self.failure["dist_path"])
                    self.assertTrue(result["report"]["fix"]["actions"][-1]["ok"])
