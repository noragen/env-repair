import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_repair import cli, unfixable, verify_imports


class TestRemovalConsent(unittest.TestCase):
    def setUp(self):
        self.candidates = [{"package": "clyent", "installer": "conda", "error": "No module named 'pkg_resources'"}]
        self.records = [{"name": "clyent", "version": "1.2.2"}, {"name": "anaconda-client", "version": "1.13.1"}]

    def run_removal(self, *, force=False, interactive=True):
        with contextlib.redirect_stdout(io.StringIO()):
            return unfixable.remove_unfixable(self.candidates, env_path="fixture", python_exe="fixture-python",
                                               manager="mamba", force=force, interactive=interactive)

    def test_noninteractive_default_and_auto_yes_do_not_authorize_removal(self):
        with patch.object(unfixable, "_conda_plan", return_value=self.records), \
                patch.object(unfixable.sys.stdin, "isatty", return_value=False), \
                patch.dict(os.environ, {"ENV_REPAIR_AUTO_YES": "1"}), \
                patch.object(unfixable, "conda_remove") as remove:
            removed, actions = self.run_removal()
        self.assertFalse(removed)
        self.assertFalse(actions[0]["approved"])
        remove.assert_not_called()

    def test_prompt_displays_all_cascading_removals_before_consent(self):
        with patch.object(unfixable, "_conda_plan", return_value=self.records), \
                patch.object(unfixable.sys.stdin, "isatty", return_value=True), \
                patch.object(unfixable, "conda_remove", return_value=True) as remove, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            def approve(_prompt):
                self.assertIn("anaconda-client==1.13.1", output.getvalue())
                self.assertIn("pkg_resources", output.getvalue())
                return "ja"
            with patch("builtins.input", side_effect=approve):
                removed, actions = unfixable.remove_unfixable(self.candidates, env_path="fixture", python_exe="fixture-python", manager="mamba")
        self.assertEqual(removed, {"clyent", "anaconda-client"})
        self.assertTrue(actions[0]["approved"])
        remove.assert_called_once_with("fixture", ["clyent"], "mamba")

    def test_negative_empty_eof_and_interrupted_prompt_preserve_packages(self):
        for answer in ("n", "", EOFError(), KeyboardInterrupt()):
            with self.subTest(answer=answer), patch.object(unfixable, "_conda_plan", return_value=self.records), \
                    patch.object(unfixable.sys.stdin, "isatty", return_value=True), \
                    patch("builtins.input", side_effect=answer if isinstance(answer, BaseException) else lambda _: answer), \
                    patch.object(unfixable, "conda_remove") as remove:
                self.assertFalse(self.run_removal()[0])
                remove.assert_not_called()

    def test_explicit_force_authorizes_noninteractive_removal_without_prompt(self):
        with patch.object(unfixable, "_conda_plan", return_value=self.records), \
                patch.object(unfixable, "conda_remove", return_value=True), patch("builtins.input") as prompt:
            removed, _ = self.run_removal(force=True, interactive=False)
        self.assertIn("clyent", removed)
        prompt.assert_not_called()

    def test_force_does_not_override_protected_package_guard(self):
        for name in ("python", "conda", "mamba", "pip", "env-repair"):
            with self.subTest(name=name), patch.object(unfixable, "_conda_plan", return_value=self.records + [{"name": name, "version": "1"}]), \
                    patch.object(unfixable, "conda_remove") as remove:
                self.assertFalse(self.run_removal(force=True)[0])
                remove.assert_not_called()

    def test_preview_failure_never_removes_even_with_force(self):
        with patch.object(unfixable, "_conda_plan", side_effect=ValueError("solver failed")), \
                patch.object(unfixable, "conda_remove") as remove:
            self.assertFalse(self.run_removal(force=True)[0])
            remove.assert_not_called()

    def test_failed_uninstall_is_not_reported_as_removed(self):
        with patch.object(unfixable, "_conda_plan", return_value=self.records), \
                patch.object(unfixable, "conda_remove", return_value=False):
            removed, actions = self.run_removal(force=True)
        self.assertFalse(removed)
        self.assertFalse(actions[0]["ok"])
        self.assertEqual(actions[0]["removed"], [])

    def test_success_code_with_remaining_metadata_is_not_removal_success(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(unfixable, "_conda_plan", return_value=self.records), \
                patch.object(unfixable, "conda_remove", return_value=True):
            self.candidates[0]["dist_path"] = temporary
            removed, actions = self.run_removal(force=True)
        self.assertFalse(removed)
        self.assertFalse(actions[0]["ok"])
        self.assertIn("metadata still exists", actions[0]["reason"])

    def test_json_mode_does_not_prompt_without_explicit_force(self):
        with patch.object(unfixable, "_conda_plan", return_value=self.records), \
                patch.object(unfixable.sys.stdin, "isatty", return_value=True), \
                patch("builtins.input") as prompt, patch.object(unfixable, "conda_remove") as remove:
            self.run_removal(interactive=False)
        prompt.assert_not_called()
        remove.assert_not_called()


class TestRemovalPreviews(unittest.TestCase):
    def test_conda_preview_is_dry_run_and_parses_dependents(self):
        payload = {"success": True, "actions": {"UNLINK": [{"name": "clyent", "version": "1.2.2"}]}}
        with patch.object(unfixable, "run_cmd_capture", return_value=(0, json.dumps(payload), "")) as capture:
            records = unfixable._conda_plan("fixture", ["clyent"], "mamba")
        self.assertEqual(records, payload["actions"]["UNLINK"])
        command = capture.call_args.args[0]
        self.assertIn("--dry-run", command)
        self.assertNotIn("--force", command)

    def test_conda_preview_rejects_incomplete_or_unexpected_json(self):
        for payload in ({}, {"success": True}, {"success": True, "actions": {"UNLINK": ["clyent"]}},
                        {"success": True, "actions": {"UNLINK": [], "LINK": [{"name": "python"}]}}):
            with self.subTest(payload=payload), patch.object(unfixable, "run_cmd_capture", return_value=(0, json.dumps(payload), "")):
                with self.assertRaises(ValueError):
                    unfixable._conda_plan("fixture", ["clyent"], "mamba")

    def test_pip_refuses_to_leave_dependents_broken(self):
        payload = [{"name": "clyent", "version": "1.2.2", "requires": []},
                   {"name": "consumer", "version": "1", "requires": ["clyent>=1"]}]
        process = type("Process", (), {"returncode": 0, "stdout": json.dumps(payload)})()
        with patch.object(unfixable.subprocess, "run", return_value=process):
            with self.assertRaisesRegex(ValueError, "consumer"):
                unfixable._pip_plan("fixture-python", ["clyent"])

    def test_pip_removal_uses_pip_and_selected_python(self):
        items = [{"package": "clyent", "installer": "pip", "error": "pkg_resources missing"}]
        with patch.object(unfixable, "_pip_plan", return_value=[{"name": "clyent", "version": "1.2.2"}]), \
                patch.object(unfixable, "pip_uninstall", return_value=True) as remove, \
                patch.object(unfixable, "conda_remove") as conda, contextlib.redirect_stdout(io.StringIO()):
            unfixable.remove_unfixable(items, env_path="fixture", python_exe="target-python", manager=None, force=True, use_uv=True)
        remove.assert_called_once_with("target-python", ["clyent"], use_uv=True)
        conda.assert_not_called()


class TestRemovalCli(unittest.TestCase):
    def test_force_before_and_after_subcommands_and_long_alias(self):
        parser = cli.build_parser()
        for command in ("one-shot", "verify-imports"):
            for arguments in (["-f", command], [command, "-f"], [command, "--remove-unfixable"]):
                self.assertTrue(parser.parse_args(arguments).remove_unfixable)
            self.assertFalse(parser.parse_args([command, "-y"]).remove_unfixable)

    def test_one_shot_passes_force_independently_of_yes(self):
        for flags, expected in ((["-y"], False), (["-f"], True), (["-y", "-f"], True)):
            with self.subTest(flags=flags), patch.object(cli, "fix_inconsistent", return_value={"ok": True}), \
                    patch.object(cli, "run", return_value={"ok": True}), \
                    patch.object(cli, "verify_imports", return_value={"ok": True}) as verify, contextlib.redirect_stdout(io.StringIO()):
                cli.one_shot(cli.build_parser().parse_args(["one-shot", "--env", "fixture"] + flags))
            self.assertEqual(verify.call_args.args[0].remove_unfixable, expected)

    def test_plan_mode_never_runs_removal_or_repairs(self):
        with patch.object(cli, "verify_imports") as verify:
            result = cli.one_shot(cli.build_parser().parse_args(["one-shot", "--env", "fixture", "--plan", "-f"]))
        self.assertTrue(result["ok"])
        verify.assert_not_called()


class TestRemovalFixIntegration(unittest.TestCase):
    def test_verify_imports_passes_force_and_only_skips_successfully_removed_packages(self):
        from env_repair import discovery

        for force, kind, expected_ok in ((False, "defer", False), (True, "removed", True)):
            with self.subTest(force=force), tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
                dist = Path(temporary) / "clyent-1.dist-info"
                dist.mkdir()
                failure = {"dist": dist.name, "dist_path": dist, "import": "clyent", "ok": False, "error": "No module named 'pkg_resources'"}
                for name, value in {"discover_envs": ([temporary], temporary, "mamba"), "get_python_exe": "fixture-python",
                                    "get_toplevel_imports": ["clyent"], "_run_import_checks_parallel": [failure],
                                    "check_import": (False, failure["error"])}.items():
                    stack.enter_context(patch.object(verify_imports, name, return_value=value))
                stack.enter_context(patch.object(discovery, "get_site_packages", return_value=[temporary]))
                fixer = stack.enter_context(patch.object(verify_imports, "attempt_fix", return_value={
                    "ok": expected_ok, "plan": [{"dist": dist.name, "kind": kind}], "actions": []}))
                args = cli.build_parser().parse_args(["verify-imports", "--env", temporary, "--fix", "--full", "--json", "-y"] + (["-f"] if force else []))
                with contextlib.redirect_stdout(io.StringIO()):
                    result = verify_imports.verify_imports(args)
                self.assertEqual(fixer.call_args.kwargs["remove_unfixable"], force)
                self.assertFalse(fixer.call_args.kwargs["interactive_removal"])
                self.assertEqual(result["ok"], expected_ok)

    def test_only_unresolved_pkg_resources_consumers_are_candidates(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            prefix = Path(temporary)
            failures = []
            entries = []
            for name, error in (("clyent", "No module named 'pkg_resources'"),
                                ("qtpy", "qtpy.QtBindingsNotFoundError: No Qt bindings could be found")):
                dist = prefix / f"{name}-1.dist-info"
                dist.mkdir()
                (dist / "METADATA").write_text(f"Name: {name}\nVersion: 1\n", encoding="utf-8")
                failures.append({"dist": dist.name, "dist_path": dist, "import": name, "error": error})
                entries.append({"name": name, "channel": "conda-forge"})
            for name, value in {"is_conda_env": True, "get_env_package_entries": entries,
                                "_repair_import_dependencies": (set(), []), "_python_major_minor": "3.15",
                                "_load_verify_imports_blacklist": {}, "load_conda_channels": [], "which": "mamba"}.items():
                stack.enter_context(patch.object(verify_imports, name, return_value=value))
            removal = stack.enter_context(patch.object(verify_imports, "remove_unfixable_consumers", return_value=({"clyent"}, [{"action": "remove_unfixable", "ok": True}])))
            with contextlib.redirect_stdout(io.StringIO()):
                report = verify_imports.attempt_fix(failures, "fixture-python", temporary, "mamba", base_prefix=temporary,
                                                    debug=False, remove_unfixable=True, interactive_removal=False, interactive_qt=False)
            candidates = removal.call_args.args[0]
            self.assertEqual([item["package"] for item in candidates], ["clyent"])
            self.assertEqual([item["kind"] for item in report["plan"]], ["removed", "defer"])
            self.assertFalse(report["ok"])
            json.dumps(report)
