import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_repair import cli, recovery, unfixable
from env_repair.subprocess_utils import _stream_reader
from env_repair import subprocess_utils


class Handoff(BaseException):
    pass


class TestRecoveryApplication(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "original"
        self.source.mkdir()
        self.python = self.source / ("python.exe" if os.name == "nt" else "bin/python")
        self.inventory = {"records": [], "packages": []}
        self.options = vars(cli.build_parser().parse_args(["one-shot", "--env", "base", "-y", "-f", "--adopt-pip", "--level", "normal"]))
        self.plan = {"source": str(self.source), "source_python": str(self.python), "options": self.options,
                     "specs": ["python=3.13", "qtpy=2.4.3"], "channels": ["conda-forge"], "manager": "mamba",
                     "transaction_signature": "approved", "source_fingerprint": recovery._fingerprint(self.inventory),
                     "source_inventory": self.inventory, "worker_workspace": str(self.root / "worker"), "worker_token": "token",
                     "pip_specs": [], "original_path": "original-manager-path", "entry": str(self.root / "runner/entry.py")}
        self.plan["python_spec"] = "python=3.13"
        self.plan["intent_version"] = 1
        self.plan["tested_conda_packages"] = []
        self.plan_path = self.root / "application-plan.json"
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        identity = patch.object(recovery, "_tool_identity", return_value=None)
        identity.start()
        self.addCleanup(identity.stop)

    def test_reverification_uses_original_new_python_and_preserves_base_and_all_options(self):
        with patch.object(recovery.sys, "executable", str(self.python)), patch.object(recovery.sys, "prefix", str(self.source)), \
                patch.object(recovery.sys, "version_info", (3, 13, 18, "final", 0)), \
                patch("env_repair.discovery.discover_envs", return_value=([str(self.source)], str(self.source), "mamba")), \
                patch("env_repair.discovery.select_envs", return_value=[str(self.source)]) as select, \
                patch.object(cli, "one_shot", return_value={"ok": True, "exit_code": 0}) as verify, patch.dict(os.environ, {}):
            self.assertEqual(recovery._verify(self.plan, self.plan_path), 0)
            self.assertEqual(os.environ["ENV_REPAIR_REVERIFY"], "1")
        self.assertEqual(vars(verify.call_args.args[0]), self.options)
        self.assertEqual(verify.call_args.args[0].env_single, "base")
        self.assertEqual(select.call_args.args[1], ["base"])

    def test_original_interpreter_version_must_match_the_approved_downgrade(self):
        with patch.object(recovery.sys, "executable", str(self.python)), patch.object(recovery.sys, "prefix", str(self.source)), \
                patch.object(recovery.sys, "version_info", (3, 15, 2, "final", 0)), \
                patch("env_repair.discovery.discover_envs", return_value=([], str(self.source), "mamba")), \
                patch("env_repair.discovery.select_envs", return_value=[str(self.source)]), patch.object(cli, "one_shot") as verify:
            with self.assertRaisesRegex(ValueError, "does not match"):
                recovery._verify(self.plan, self.plan_path)
        verify.assert_not_called()

    def test_worker_python_cannot_be_used_for_original_verification(self):
        with patch.object(recovery.sys, "executable", str(self.root / "worker/python.exe")), patch.object(cli, "one_shot") as verify:
            with self.assertRaisesRegex(ValueError, "newly installed original Python"):
                recovery._verify(self.plan, self.plan_path)
        verify.assert_not_called()

    def test_resolution_to_different_base_is_rejected_before_any_repair(self):
        with patch.object(recovery.sys, "executable", str(self.python)), patch.object(recovery.sys, "prefix", str(self.source)), \
                patch("env_repair.discovery.discover_envs", return_value=([], "worker-base", "mamba")), \
                patch("env_repair.discovery.select_envs", return_value=["worker-base"]), patch.object(cli, "one_shot") as verify:
            with self.assertRaisesRegex(ValueError, "different environment"):
                recovery._verify(self.plan, self.plan_path)
        verify.assert_not_called()

    def test_one_shot_passes_complete_original_options_to_offer(self):
        with patch.object(cli, "fix_inconsistent", return_value={"ok": True}), patch.object(cli, "run", return_value={"ok": True}), \
                patch.object(cli, "verify_imports", return_value={"ok": False}) as verify, contextlib.redirect_stdout(io.StringIO()):
            cli.one_shot(argparse.Namespace(**self.options))
        self.assertEqual(verify.call_args.args[0].recovery_options, self.options)

    def report(self):
        return {"ok": True, "scope": "target-packages", "source": str(self.source), "manager": "mamba", "channels": ["conda-forge"],
                "python_spec": "python=3.13", "tested_conda_packages": [{"name": "python", "version": "3.13.18", "build": "build"}],
                "tested_packages": [], "report_path": str(self.root / "migration.json")}

    def test_offer_requires_separate_confirmation_even_with_yes_and_force(self):
        for answer in ("", "no", "yes"):
            with self.subTest(answer=answer), patch.object(recovery.sys.stdin, "isatty", return_value=True), \
                    patch.object(recovery, "_inventory", return_value=self.inventory), \
                    patch.object(recovery, "_transaction", return_value=(["mamba"], {"LINK": self.report()["tested_conda_packages"], "UNLINK": []}, "approved")), \
                    patch("builtins.input", return_value=answer), contextlib.redirect_stdout(io.StringIO()), patch.dict(os.environ, {"ENV_REPAIR_REVERIFY": ""}):
                result = recovery.offer_installation(self.report(), options=self.options)
            self.assertEqual(result["approved"], answer == "yes")
            if answer == "yes":
                plan = json.loads(Path(result["plan_path"]).read_text())
                self.assertEqual(plan["options"], self.options)
                self.assertEqual(plan["source"], str(self.source))

    def test_json_and_reverification_never_prompt_or_apply(self):
        with patch("builtins.input") as prompt, patch.object(recovery, "_transaction") as solve:
            result = recovery.offer_installation(self.report(), options=self.options, interactive=False)
        self.assertFalse(result["approved"])
        prompt.assert_not_called()
        solve.assert_not_called()

    def test_failed_installer_bootstrap_records_cleanup_without_touching_original(self):
        workspace = self.root / "bootstrap"
        workspace.mkdir()
        with patch.object(recovery.tempfile, "mkdtemp", return_value=str(workspace)), \
                patch.object(recovery, "run_cmd_live", return_value=1), \
                patch.object(recovery, "_cleanup_workspace", return_value={"ok": True}) as cleanup, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "creation failed"):
                recovery.launch_installation(self.plan_path)
        self.assertEqual(cleanup.call_args.args[0], workspace)
        self.assertEqual(cleanup.call_args.kwargs["source"], str(self.source))
        result = json.loads((self.root / "application.json").read_text())
        self.assertFalse(result["ok"])
        self.assertTrue(result["cleanup"]["ok"])
        self.assertTrue(self.source.exists())

    def test_transaction_checks_pins_and_rejects_protected_removals_or_self_update(self):
        for name in ("conda", "python", "env-repair"):
            payload = {"success": True, "actions": {"UNLINK": [{"name": name, "version": "1"}], "LINK": []}}
            with self.subTest(name=name), patch.object(recovery, "run_cmd_capture", return_value=(0, json.dumps(payload), "")) as capture:
                with self.assertRaisesRegex(ValueError, "protected"):
                    recovery._transaction("mamba", self.source, ["python=3.13"], [])
            self.assertIn("--dry-run", capture.call_args.args[0])
            self.assertNotIn("--no-pin", capture.call_args.args[0])
            self.assertNotIn("--force-reinstall", capture.call_args.args[0])

    def test_closed_output_pipe_does_not_stop_draining_subprocess_output(self):
        class ClosedOutput:
            def write(self, value):
                raise BrokenPipeError("parent output pipe closed during handoff")
        buffer = []
        _stream_reader(io.StringIO("first\nsecond\n"), ClosedOutput(), buffer)
        self.assertEqual(buffer, ["first\n", "second\n"])
        logfile = io.StringIO()
        output = recovery._RecoveryOutput(ClosedOutput(), logfile)
        output.write("progress\n")
        output.write("finished\n")
        output.flush()
        self.assertEqual(logfile.getvalue(), "progress\nfinished\n")

    def test_worker_installs_then_uses_source_python_and_restores_original_path(self):
        (self.root / "verification.json").write_text('{"ok":true,"report":{"steps":{}}}', encoding="utf-8")
        with patch.object(recovery.sys, "prefix", str(self.root / "worker/environment")), \
                patch.object(recovery, "_worker_manager", return_value="independent-mamba"), \
                patch.object(recovery, "_inventory", return_value=self.inventory), \
                patch.object(recovery, "_transaction", return_value=(["independent-mamba", "install"], {}, "approved")), \
                patch.object(recovery, "run_cmd_capture", return_value=(0, "snapshot", "")), \
                patch.object(recovery, "run_cmd_live", return_value=0) as live, \
                patch.object(recovery.os, "execv", side_effect=Handoff) as handoff, patch.dict(os.environ, {}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(recovery.worker_main("apply", str(self.plan_path)), 0)
        self.assertEqual(live.call_args_list[0].args[0], ["independent-mamba", "install"])
        verification = live.call_args_list[1]
        self.assertEqual(verification.args[0][:3], [str(self.python), "-u", "-I"])
        self.assertEqual(verification.args[0][4], "verify")
        self.assertEqual(verification.kwargs["env"]["PATH"], self.plan["original_path"])
        handoff.assert_not_called()
        self.assertTrue(json.loads((self.root / "application.json").read_text())["ok"])

    def test_worker_refuses_changed_transaction_before_install(self):
        with patch.object(recovery.sys, "prefix", str(self.root / "worker/environment")), \
                patch.object(recovery, "_worker_manager", return_value="independent-mamba"), \
                patch.object(recovery, "_inventory", return_value=self.inventory), \
                patch.object(recovery, "_transaction", return_value=([], {}, "changed")), \
                patch.object(recovery, "run_cmd_capture", return_value=(0, "", "")), \
                patch.object(recovery, "run_cmd_live") as live, patch.object(recovery.os, "execv", side_effect=Handoff), \
                patch.dict(os.environ, {}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(recovery.worker_main("apply", str(self.plan_path)), 1)
        live.assert_not_called()
        result = json.loads((self.root / "application.json").read_text())
        self.assertFalse(result["ok"])
        self.assertIn("new confirmation", result["error"])

    def test_actual_installed_build_must_match_lock_before_original_one_shot(self):
        tested = [{"name": "runtime", "version": "1", "build": "tested"}]
        inventory = {"records": tested, "packages": []}
        self.plan.update(tested_conda_packages=tested, source_inventory=inventory,
                         source_fingerprint=recovery._fingerprint(inventory))
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        changed = {"records": [dict(tested[0], build="untested")], "packages": []}
        with patch.object(recovery.sys, "prefix", str(self.root / "worker/environment")), \
                patch.object(recovery, "_worker_manager", return_value="independent-mamba"), \
                patch.object(recovery, "_inventory", side_effect=[inventory, changed]), \
                patch.object(recovery, "_transaction", return_value=(["independent-mamba", "install"], {}, "approved")), \
                patch.object(recovery, "run_cmd_capture", return_value=(0, "snapshot", "")), \
                patch.object(recovery, "run_cmd_live", return_value=0) as live, \
                patch.dict(os.environ, {}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(recovery.worker_main("apply", str(self.plan_path)), 1)
        self.assertEqual(live.call_count, 1)
        result = json.loads((self.root / "application.json").read_text())
        self.assertFalse(result["ok"])
        self.assertIn("new sandbox test", result["error"])

    def test_final_result_uses_original_verification_failure_not_sandbox_success(self):
        with patch.object(recovery.sys, "prefix", str(self.root / "worker/environment")), \
                patch.object(recovery, "_worker_manager", return_value="independent-mamba"), \
                patch.object(recovery, "_inventory", return_value=self.inventory), \
                patch.object(recovery, "_transaction", return_value=(["independent-mamba", "install"], {}, "approved")), \
                patch.object(recovery, "run_cmd_capture", return_value=(0, "snapshot", "")), \
                patch.object(recovery, "run_cmd_live", side_effect=[0, 7]), \
                patch.object(recovery.os, "execv", side_effect=Handoff), patch.dict(os.environ, {}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(recovery.worker_main("apply", str(self.plan_path)), 7)
        result = json.loads((self.root / "application.json").read_text())
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 7)

    def test_final_cleanup_only_targets_owned_installer_and_runner(self):
        self.plan["entry"] = str(self.root / "application-runner-token/entry.py")
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        (self.root / "application.json").write_text('{"ok":true,"exit_code":0}', encoding="utf-8")
        with patch.object(recovery, "_cleanup_workspace", return_value={"ok": True}) as cleanup, contextlib.redirect_stdout(io.StringIO()):
            code = recovery.worker_main("cleanup", str(self.plan_path))
        self.assertEqual(code, 0)
        self.assertEqual([Path(call.args[0]) for call in cleanup.call_args_list], [self.root / "worker", self.root / "application-runner-token"])
        self.assertTrue(self.source.exists())

    def test_installer_has_its_own_root_and_no_inherited_activation(self):
        with patch.dict(os.environ, {"MAMBA_ROOT_PREFIX": str(self.source), "MAMBA_EXE": "old-mamba",
                                    "CONDA_PREFIX": str(self.source), "CONDA_SHLVL": "2",
                                    "PYTHONPATH": "unsafe", "SYSTEMROOT": "system", "UNRELATED": "preserved",
                                    "CONDA_PINNED_PACKAGES": "python=3.14", "MAMBA_SSL_VERIFY": "true"}, clear=True):
            environment = recovery._installer_environment(self.plan)
        self.assertEqual(environment["MAMBA_ROOT_PREFIX"], str(self.root / "worker/environment"))
        self.assertEqual(environment["SYSTEMROOT"], "system")
        self.assertEqual(environment["UNRELATED"], "preserved")
        self.assertEqual(environment["CONDA_PINNED_PACKAGES"], "python=3.14")
        self.assertEqual(environment["MAMBA_SSL_VERIFY"], "true")
        for name in ("MAMBA_EXE", "CONDA_PREFIX", "CONDA_SHLVL", "PYTHONPATH"):
            self.assertNotIn(name, environment)

    def test_original_environment_restores_activation_not_only_path(self):
        self.plan["original_activation_environment"] = {"MAMBA_ROOT_PREFIX": str(self.source), "MAMBA_EXE": "original-mamba",
                                                        "CONDA_PREFIX": str(self.source), "CONDA_SHLVL": "1"}
        with patch.dict(os.environ, {"MAMBA_ROOT_PREFIX": "worker", "MAMBA_EXE": "worker-mamba", "SYSTEMROOT": "system"}, clear=True):
            environment = recovery._original_environment(self.plan)
        self.assertEqual(environment["MAMBA_EXE"], "original-mamba")
        self.assertEqual(environment["CONDA_PREFIX"], str(self.source))
        self.assertEqual(environment["PATH"], self.plan["original_path"])
        self.assertEqual(environment["SYSTEMROOT"], "system")

    def activation_fixture(self):
        folder = self.source / "condabin"
        folder.mkdir()
        wrapper = folder / "mamba.bat"
        original = b'@SET "MAMBA_EXE=original-mamba.exe"\r\n'
        wrapper.write_bytes(original)
        self.plan["activation_snapshot"] = recovery._snapshot_activation(self.source)
        return wrapper, original

    def test_temporary_manager_reference_is_restored_byte_for_byte(self):
        wrapper, original = self.activation_fixture()
        unrelated = self.source / "condabin/conda.bat"
        unrelated.write_bytes(b"updated official wrapper")
        temporary = str(self.root / "worker/environment/Library/bin/mamba.exe")
        for reference in (temporary, temporary.replace("\\", "/").upper()):
            wrapper.write_text(reference, encoding="utf-8")
            self.assertEqual(recovery._restore_activation(self.plan), [str(wrapper.relative_to(self.source))])
            self.assertEqual(wrapper.read_bytes(), original)
            self.assertEqual(unrelated.read_bytes(), b"updated official wrapper")

    def test_new_contaminated_file_retains_installer_instead_of_unsafe_cleanup(self):
        wrapper, _ = self.activation_fixture()
        (wrapper.parent / "mamba_hook.bat").write_text(self.plan["worker_workspace"], encoding="utf-8")
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        (self.root / "application.json").write_text('{"ok":true,"exit_code":0}', encoding="utf-8")
        with patch.object(recovery, "_cleanup_workspace") as cleanup:
            self.assertEqual(recovery.worker_main("cleanup", str(self.plan_path)), 1)
        cleanup.assert_not_called()
        result = json.loads((self.root / "application.json").read_text())
        self.assertFalse(result["ok"])
        self.assertIn("new activation file", result["cleanup_error"])

    def test_failed_transaction_restores_activation_before_worker_handoff(self):
        wrapper, original = self.activation_fixture()
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")

        def failed_install(command, **kwargs):
            wrapper.write_text(self.plan["worker_workspace"], encoding="utf-8")
            return 1

        with patch.object(recovery.sys, "prefix", str(self.root / "worker/environment")), \
                patch.object(recovery, "_worker_manager", return_value="independent-mamba"), \
                patch.object(recovery, "_inventory", return_value=self.inventory), \
                patch.object(recovery, "_transaction", return_value=(["independent-mamba", "install"], {}, "approved")), \
                patch.object(recovery, "run_cmd_capture", return_value=(0, "snapshot", "")), \
                patch.object(recovery, "run_cmd_live", side_effect=failed_install), \
                patch.object(recovery.os, "execv", side_effect=Handoff), patch.dict(os.environ, {}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(recovery.worker_main("apply", str(self.plan_path)), 1)
        self.assertEqual(wrapper.read_bytes(), original)
        self.assertFalse(json.loads((self.root / "application.json").read_text())["ok"])

    def test_bootstrap_isolated_and_restores_activation_even_on_failure(self):
        wrapper, original = self.activation_fixture()
        workspace = self.root / "bootstrap"
        workspace.mkdir()

        def failed_create(command, *, env, **kwargs):
            self.assertEqual(env["MAMBA_ROOT_PREFIX"], str(workspace / "environment"))
            self.assertNotIn("MAMBA_EXE", env)
            wrapper.write_text(str(workspace), encoding="utf-8")
            return 1

        with patch.object(recovery.tempfile, "mkdtemp", return_value=str(workspace)), \
                patch.object(recovery, "run_cmd_live", side_effect=failed_create), \
                patch.object(recovery, "_cleanup_workspace", return_value={"ok": True}), \
                patch.dict(os.environ, {"MAMBA_EXE": "original-mamba"}), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "creation failed"):
                recovery.launch_installation(self.plan_path)
            self.assertEqual(os.environ["MAMBA_EXE"], "original-mamba")
        self.assertEqual(wrapper.read_bytes(), original)

    def test_launcher_waits_for_worker_exit_before_cleanup_and_returns_final_status(self):
        workspace = self.root / "bootstrap"
        workspace.mkdir()
        phases = []

        def live(command, **kwargs):
            if "create" in command:
                phases.append("create")
                return 0
            self.assertTrue(kwargs["wait_on_interrupt"])
            self.assertIn("-u", command)
            if "apply" in command:
                phases.append("apply-finished")
                self.assertNotIn("cleanup", phases)
                token = json.loads(self.plan_path.read_text())["worker_token"]
                (self.root / "application.json").write_text(json.dumps({"ok": False, "exit_code": 7, "worker_token": token}), encoding="utf-8")
                return 7
            self.assertIn("cleanup", command)
            phases.append("cleanup")
            self.assertEqual(command[0], str(self.python))
            self.assertEqual(kwargs["env"]["PATH"], self.plan["original_path"])
            return 7

        before = dict(os.environ)
        with patch.object(recovery.tempfile, "mkdtemp", return_value=str(workspace)), \
                patch.object(recovery, "run_cmd_live", side_effect=live), \
                patch.object(recovery.os, "execv") as execute, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(recovery.launch_installation(self.plan_path), 7)
        execute.assert_not_called()
        self.assertEqual(phases, ["create", "apply-finished", "cleanup"])
        self.assertEqual(dict(os.environ), before)

    def test_worker_crash_without_report_keeps_workspace_and_is_not_success(self):
        workspace = self.root / "bootstrap"
        workspace.mkdir()
        with patch.object(recovery.tempfile, "mkdtemp", return_value=str(workspace)), \
                patch.object(recovery, "run_cmd_live", side_effect=[0, 9]), \
                patch.object(recovery, "_cleanup_workspace") as cleanup, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(recovery.launch_installation(self.plan_path), 9)
        cleanup.assert_not_called()
        self.assertTrue(workspace.exists())
        self.assertFalse(json.loads((self.root / "application.json").read_text())["ok"])

    def test_stale_success_report_cannot_trigger_cleanup_or_success(self):
        workspace = self.root / "bootstrap"
        workspace.mkdir()
        (self.root / "application.json").write_text('{"ok":true,"exit_code":0,"worker_token":"old"}', encoding="utf-8")
        with patch.object(recovery.tempfile, "mkdtemp", return_value=str(workspace)), \
                patch.object(recovery, "run_cmd_live", side_effect=[0, 0]) as live, \
                patch.object(recovery, "_cleanup_workspace") as cleanup, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(recovery.launch_installation(self.plan_path), 1)
        self.assertEqual(live.call_count, 2)
        cleanup.assert_not_called()

    def test_cli_returns_installer_status_instead_of_stale_preinstallation_status(self):
        with patch.object(cli, "one_shot", return_value={"pending_installation": str(self.plan_path), "exit_code": 1}), \
                patch.object(recovery, "launch_installation", return_value=0):
            self.assertEqual(cli.main(["one-shot", "--env", "base"]), 0)

    def test_preserves_pip_version_offline_and_checks_module_and_launcher(self):
        wheel = self.root / "env_repair-0.2.8-py3-none-any.whl"
        wheel.write_bytes(b"original-package")
        launcher = self.source / ("Scripts/env-repair.exe" if os.name == "nt" else "bin/env-repair")
        launcher.parent.mkdir()
        launcher.touch()
        self.plan["original_tool"] = {"version": "0.2.8", "installer": "pip", "wheel": str(wheel),
                                       "sha256": recovery.hashlib.sha256(wheel.read_bytes()).hexdigest()}
        with patch.object(recovery, "_tool_identity", side_effect=[None, {"version": "0.2.8"}]), \
                patch.object(recovery, "run_cmd_live", return_value=0) as install, \
                patch.object(recovery, "run_cmd_capture", return_value=(0, "help", "")) as check:
            recovery._preserve_tool(self.plan)
        command = install.call_args.args[0]
        self.assertIn("--no-index", command)
        self.assertIn("--no-deps", command)
        self.assertEqual(command[-1], str(wheel))
        self.assertEqual(len(check.call_args_list), 2)
        self.assertIn("-m", check.call_args_list[0].args[0])
        self.assertEqual(check.call_args_list[1].args[0], [str(launcher), "--help"])

    def test_changed_conda_version_and_broken_cli_cannot_report_success(self):
        launcher = self.source / ("Scripts/env-repair.exe" if os.name == "nt" else "bin/env-repair")
        launcher.parent.mkdir()
        launcher.touch()
        self.plan["original_tool"] = {"version": "0.2.8", "installer": "conda"}
        with patch.object(recovery, "_tool_identity", return_value={"version": "0.2.9"}), \
                patch.object(recovery, "run_cmd_live") as install:
            with self.assertRaisesRegex(ValueError, "not preserved"):
                recovery._preserve_tool(self.plan)
        install.assert_not_called()
        with patch.object(recovery, "_tool_identity", return_value={"version": "0.2.8"}), \
                patch.object(recovery, "run_cmd_capture", return_value=(1, "", "broken CLI")):
            with self.assertRaisesRegex(ValueError, "broken CLI"):
                recovery._preserve_tool(self.plan)

    def test_unchanged_pip_tool_is_checked_but_never_reinstalled(self):
        launcher = self.source / ("Scripts/env-repair.exe" if os.name == "nt" else "bin/env-repair")
        launcher.parent.mkdir()
        launcher.touch()
        self.plan["original_tool"] = {"version": "0.2.8", "installer": "pip"}
        with patch.object(recovery, "_tool_identity", return_value={"version": "0.2.8"}), \
                patch.object(recovery, "run_cmd_capture", return_value=(0, "help", "")), \
                patch.object(recovery, "run_cmd_live") as install:
            recovery._preserve_tool(self.plan)
        install.assert_not_called()

    def test_tampered_backup_is_never_installed(self):
        wheel = self.root / "backup.whl"
        wheel.write_bytes(b"tampered")
        self.plan["original_tool"] = {"version": "0.2.8", "installer": "pip", "wheel": str(wheel), "sha256": "wrong"}
        with patch.object(recovery, "run_cmd_live") as install:
            with self.assertRaisesRegex(ValueError, "checksum"):
                recovery._preserve_tool(self.plan)
        install.assert_not_called()

    def test_missing_tool_after_verification_blocks_success_and_cleanup(self):
        with patch.object(recovery.sys, "prefix", str(self.root / "worker/environment")), \
                patch.object(recovery, "_worker_manager", return_value="independent-mamba"), \
                patch.object(recovery, "_inventory", return_value=self.inventory), \
                patch.object(recovery, "_transaction", return_value=(["independent-mamba", "install"], {}, "approved")), \
                patch.object(recovery, "run_cmd_capture", return_value=(0, "snapshot", "")), \
                patch.object(recovery, "run_cmd_live", return_value=0), \
                patch.object(recovery, "_preserve_tool", side_effect=[None, None, ValueError("tool missing"), ValueError("tool missing")]), \
                patch.dict(os.environ, {}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(recovery.worker_main("apply", str(self.plan_path)), 1)
        result = json.loads((self.root / "application.json").read_text())
        self.assertFalse(result["ok"])
        self.assertEqual(result["cleanup_error"], "tool missing")

    def test_pip_backup_keeps_original_code_and_metadata_not_windows_launcher(self):
        site = self.source / "site-packages"
        package = site / "env_repair"
        package.mkdir(parents=True)
        metadata = site / "env_repair-0.2.8.dist-info"
        metadata.mkdir()
        (package / "cli.py").write_bytes(b"original local changes")
        (metadata / "WHEEL").write_text("Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n", encoding="utf-8")
        (metadata / "METADATA").write_text("Name: env-repair\nVersion: 0.2.8\n", encoding="utf-8")
        (metadata / "entry_points.txt").write_text("[console_scripts]\nenv-repair = env_repair.cli:main\n", encoding="utf-8")
        identity = {"version": "0.2.8", "root": str(site), "metadata": str(metadata),
                    "files": ["env_repair/cli.py", metadata.name + "/WHEEL", metadata.name + "/METADATA",
                              metadata.name + "/entry_points.txt", "../../Scripts/env-repair.exe"]}
        with patch.object(recovery, "_tool_identity", return_value=identity):
            backup = recovery._backup_tool(self.plan, self.root)
        with recovery.zipfile.ZipFile(backup["wheel"]) as wheel:
            self.assertEqual(wheel.read("env_repair/cli.py"), b"original local changes")
            self.assertIn(metadata.name + "/RECORD", wheel.namelist())
            self.assertFalse(any("Scripts" in name for name in wheel.namelist()))


class TestSynchronousStreaming(unittest.TestCase):
    def test_real_child_streams_before_exit_and_caller_waits_until_final_output(self):
        output = io.StringIO()
        observed = []

        class Sink:
            def write(self, value):
                observed.append((value.strip(), marker.exists()))
                output.write(value)

            def flush(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "finished"
            script = ("import sys,time; from pathlib import Path; print('early',flush=True); "
                      "time.sleep(0.2); Path(sys.argv[1]).touch(); print('late',flush=True); sys.exit(7)")
            with contextlib.redirect_stdout(Sink()):
                code = subprocess_utils.run_cmd_live([sys.executable, "-u", "-c", script, str(marker)], wait_on_interrupt=True)
            self.assertTrue(marker.exists())
        self.assertEqual(code, 7)
        self.assertIn(("early", False), observed)
        self.assertIn("late", output.getvalue())

    def test_interrupt_waits_for_child_instead_of_terminating_and_returns_130(self):
        from unittest.mock import Mock

        child = Mock(stdout=io.StringIO("done\n"), stderr=io.StringIO(""))
        child.wait.side_effect = [KeyboardInterrupt(), KeyboardInterrupt(), 0]
        with patch.object(subprocess_utils.subprocess, "Popen", return_value=child), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(subprocess_utils.run_cmd_live(["worker"], wait_on_interrupt=True), 130)
        child.terminate.assert_not_called()
        self.assertEqual(child.wait.call_count, 3)


class TestGeneralLeafRemoval(unittest.TestCase):
    def test_non_pkg_resources_failure_is_removed_by_general_force_flow(self):
        from env_repair import discovery, package_sandbox, verify_imports

        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            dist = root / "broken-1.dist-info"
            dist.mkdir()
            (dist / "METADATA").write_text("Name: broken\nVersion: 1\n", encoding="utf-8")
            (dist / "INSTALLER").write_text("pip", encoding="utf-8")
            failure = {"dist": dist.name, "dist_path": dist, "import": "broken", "ok": False, "error": "ImportError: unsupported native ABI"}
            for name, value in {"discover_envs": ([directory], directory, "mamba"), "get_python_exe": "fixture-python",
                                "get_toplevel_imports": ["broken"], "_run_import_checks_parallel": [failure],
                                "check_import": (False, failure["error"]), "load_conda_channels": [], "which": None,
                                "_python_major_minor": "3.15"}.items():
                stack.enter_context(patch.object(verify_imports, name, return_value=value))
            stack.enter_context(patch.object(discovery, "get_site_packages", return_value=[directory]))
            stack.enter_context(patch.object(verify_imports, "attempt_fix", return_value={"ok": False, "plan": [], "actions": []}))
            stack.enter_context(patch.object(package_sandbox, "recover_import_failures", return_value={"ok": False}))
            remove = stack.enter_context(patch.object(verify_imports, "remove_unfixable_consumers", return_value=({"broken"}, [{"action": "remove_unfixable", "ok": True}])))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            args = cli.build_parser().parse_args(["verify-imports", "--env", directory, "--fix", "--full", "--json", "-f"])
            result = verify_imports.verify_imports(args)
        self.assertTrue(result["ok"])
        self.assertEqual(remove.call_args.args[0][0]["package"], "broken")
        self.assertTrue(remove.call_args.kwargs["force"])
        self.assertFalse(remove.call_args.kwargs["allow_dependents"])
        self.assertEqual(result["report"]["post_failures"], [])

    def test_force_does_not_allow_unrequested_conda_dependents(self):
        candidates = [{"package": "broken", "installer": "conda", "error": "binary import failure"}]
        with patch.object(unfixable, "_conda_plan", return_value=[{"name": "broken", "version": "1"}, {"name": "consumer", "version": "2"}]), \
                patch.object(unfixable, "conda_remove") as remove, contextlib.redirect_stdout(io.StringIO()):
            removed, _ = unfixable.remove_unfixable(candidates, env_path="fixture", python_exe="python", manager="mamba", force=True, allow_dependents=False)
        self.assertFalse(removed)
        remove.assert_not_called()

    def test_conda_leaf_still_checks_mixed_pip_dependents(self):
        candidates = [{"package": "provider", "distribution": "broken", "installer": "conda", "error": "binary import failure"}]
        with patch.object(unfixable, "_conda_plan", return_value=[{"name": "provider", "version": "1"}]), \
                patch.object(unfixable, "_pip_plan", side_effect=ValueError("pip consumer still depends on broken")) as check, \
                patch.object(unfixable, "conda_remove") as remove, contextlib.redirect_stdout(io.StringIO()):
            removed, _ = unfixable.remove_unfixable(candidates, env_path="fixture", python_exe="python", manager="mamba", force=True, allow_dependents=False)
        self.assertFalse(removed)
        check.assert_called_once_with("python", ["broken"])
        remove.assert_not_called()

    def test_help_no_longer_restricts_force_to_pkg_resources(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"LC_ALL": "de_DE"}), contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["one-shot", "--help"])
        force_help = output.getvalue().split("--remove-unfixable", 1)[1]
        self.assertIn("Abhängigkeits- und", force_help)
        self.assertNotIn("pkg_resources", force_help)
