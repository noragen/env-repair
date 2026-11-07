import argparse
import contextlib
import io
import itertools
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_repair import cli, doctor


class TestPlanJSON(unittest.TestCase):
    commands = {
        "one-shot": [],
        "rollback": ["--to", "prev"],
        "rebuild": ["--to", "new-plan-target"],
        "fix-inconsistent": ["--level", "normal"],
        "cache-fix": ["--level", "aggressive"],
    }

    def run_plan(self, root, command, extra, *, json_output, yes, auto_yes):
        arguments = [command] + ([] if command == "cache-fix" else ["--env", str(root)]) + extra + ["--plan"]
        if json_output:
            arguments.append("--json")
        if yes:
            arguments.append("-y")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"ENV_REPAIR_AUTO_YES": auto_yes}))
            stack.enter_context(patch.object(doctor, "detect_managers", return_value={"mamba": "fixture"}))
            stack.enter_context(patch.object(doctor, "discover_envs", return_value=([str(root)], str(root), "mamba")))
            stack.enter_context(patch.object(doctor, "select_envs", return_value=[str(root)]))
            stack.enter_context(patch.object(doctor, "list_revisions", return_value=[0, 1]))
            for name in ("_approve", "_check_and_repair_conda_core", "export_env_yaml", "rollback_to_revision",
                         "env_create_from_yaml", "conda_install", "clean_index_cache", "conda_clean", "scan_env"):
                stack.enter_context(patch.object(doctor, name, side_effect=AssertionError("plan reached " + name)))
            stack.enter_context(patch.object(cli, "run", side_effect=AssertionError("plan reached scan/fix")))
            stack.enter_context(patch.object(cli, "verify_imports", side_effect=AssertionError("plan reached import repair")))
            stack.enter_context(patch.object(subprocess, "run", side_effect=AssertionError("unexpected external command")))
            stack.enter_context(patch.object(subprocess, "Popen", side_effect=AssertionError("unexpected external process")))
            stack.enter_context(patch.object(Path, "mkdir", side_effect=AssertionError("plan created snapshot directory")))
            stack.enter_context(patch("builtins.input", side_effect=AssertionError("plan requested approval")))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            status = cli.main(arguments)
        self.assertEqual(status, 0)
        if json_output:
            report = json.loads(output.getvalue())
            self.assertIsInstance(report, dict)
            self.assertIs(report["planned"], True)
            if command != "one-shot":
                self.assertEqual(report["action"], command)
            if command == "rebuild":
                self.assertEqual(report["source"], str(root))
                self.assertEqual(report["to"], "new-plan-target")
            elif command in {"one-shot", "rollback", "fix-inconsistent"}:
                self.assertEqual(report["env"], str(root))
            if command == "rollback":
                self.assertEqual((report["from"], report["to"]), (1, 0))
            if command == "cache-fix":
                self.assertEqual(report["level"], extra[extra.index("--level") + 1])
                self.assertTrue(report["steps"])

    def test_every_plan_subcommand_is_covered(self):
        parser = cli.build_parser()
        subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
        available = {name for name, child in subparsers.choices.items()
                     if any("--plan" in action.option_strings for action in child._actions)}
        self.assertEqual(available, set(self.commands))

    def test_plan_is_non_executing_with_json_yes_and_auto_yes_combinations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for command, (json_output, yes, auto_yes) in itertools.product(self.commands, itertools.product((False, True), (False, True), ("0", "1"))):
                with self.subTest(command=command, json=json_output, yes=yes, auto_yes=auto_yes):
                    self.run_plan(root, command, self.commands[command], json_output=json_output, yes=yes, auto_yes=auto_yes)
            self.assertEqual(list(root.iterdir()), [])

    def test_each_fix_level_remains_a_plan_with_json_and_yes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for command, levels in (("fix-inconsistent", ("safe", "normal", "rebuild")),
                                    ("cache-fix", ("safe", "targeted", "aggressive"))):
                for level in levels:
                    with self.subTest(command=command, level=level):
                        extra = ["--level", level]
                        self.run_plan(root, command, extra, json_output=True, yes=True, auto_yes="1")

    def test_rebuild_plan_does_not_create_path_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = root / "must-not-exist"
            with patch.object(doctor, "detect_managers", return_value={"mamba": "fixture"}), \
                    patch.object(doctor, "discover_envs", return_value=([str(root)], str(root), "mamba")), \
                    patch.object(doctor, "_approve", side_effect=AssertionError("approval reached")), \
                    patch.object(doctor, "env_create_from_yaml", side_effect=AssertionError("creation reached")), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(["rebuild", "--env", str(root), "--to", str(destination), "--plan", "--json", "-y"]), 0)
            self.assertEqual(json.loads(output.getvalue())["to"], str(destination))
            self.assertFalse(destination.exists())

    def test_json_without_plan_still_runs_explicitly_approved_cache_cleanup(self):
        with patch.object(doctor, "conda_clean", return_value=True) as clean, \
                patch.object(doctor, "conda_info_json", return_value={"pkgs_dirs": []}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["cache-fix", "--level", "safe", "--json", "-y"]), 0)
        self.assertEqual(clean.call_count, 2)
        self.assertNotIn("planned", json.loads(output.getvalue())[0])


if __name__ == "__main__":
    unittest.main()
