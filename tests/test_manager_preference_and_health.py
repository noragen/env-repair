import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_repair import conda_config, conda_ops, discovery, doctor


class TestMambaFirstDiscovery(unittest.TestCase):
    def test_successful_mamba_discovery_never_invokes_conda(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            named = base / "envs" / "worker"
            (named / "conda-meta").mkdir(parents=True)
            info = {"base environment": str(base), "envs directories": [str(base / "envs")]}
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(discovery, "which", return_value=True), \
                    patch.object(discovery, "run_json_cmd", side_effect=[info, {"envs": []}]) as run:
                envs, prefix, manager = discovery.discover_envs(show_json_output=False)
            self.assertEqual(manager, "mamba")
            self.assertEqual(prefix, str(base))
            self.assertIn(str(named), envs)
            self.assertEqual([call.args[0][0] for call in run.call_args_list], ["mamba", "mamba"])

    def test_conda_is_discovery_fallback_not_installer_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(discovery, "which", side_effect=lambda name: name in {"mamba", "conda"}), \
                    patch.object(discovery, "run_json_cmd", side_effect=[None, {"root_prefix": temporary}, {"envs": [temporary]}]):
                envs, prefix, manager = discovery.discover_envs(show_json_output=False)
            self.assertEqual(manager, "mamba")
            self.assertEqual(prefix, temporary)
            self.assertIn(temporary, envs)

    def test_micromamba_only_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(discovery, "which", side_effect=lambda name: name == "micromamba"), \
                    patch.object(discovery, "run_json_cmd", side_effect=[{"root_prefix": temporary}, {"envs": [temporary]}]):
                envs, prefix, manager = discovery.discover_envs(show_json_output=False)
            self.assertEqual(manager, "micromamba")
            self.assertEqual(prefix, temporary)
            self.assertEqual(envs, [temporary])

    def test_mamba_channels_precede_conda_when_both_installed(self):
        with patch.object(conda_config, "load_conda_channels_from_condarc", return_value=[]), \
                patch.object(conda_config, "run_json_cmd", return_value={"channels": ["conda-forge"]}) as run:
            channels = conda_config.load_conda_channels(has_conda=True, has_mamba=True)
        self.assertEqual(channels, ["conda-forge"])
        run.assert_called_once_with(["mamba", "config", "list", "--json"], show_json_output=False)


class TestCondaHealthFailures(unittest.TestCase):
    def test_crashing_conda_is_captured_and_retried_without_plugins(self):
        with patch.object(conda_ops.shutil, "which", return_value="conda"), \
                patch.object(conda_ops, "run_cmd_capture", return_value=(1, "", "AttributeError: broken dependency")) as run:
            ok, degraded, detail = conda_ops.conda_health_check(show_json_output=False)
        self.assertFalse(ok)
        self.assertFalse(degraded)
        self.assertIn("broken dependency", detail["stderr"])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.kwargs["env"]["CONDA_NO_PLUGINS"], "true")
        self.assertEqual(run.call_args.kwargs["env"]["CONDA_REPORT_ERRORS"], "false")
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_plugin_warning_is_not_healthy_even_with_success_exit_code(self):
        error = "Error while loading conda entry point: anaconda-auth (module 'conda.plugins' has no attribute 'CondaAuthHandler')"
        with patch.object(conda_ops.shutil, "which", return_value="conda"), \
                patch.object(conda_ops, "run_cmd_capture", return_value=(0, '{"root_prefix": "test-prefix"}', error)):
            ok, degraded, detail = conda_ops.conda_health_check(show_json_output=False)
        self.assertFalse(ok)
        self.assertTrue(degraded)
        self.assertEqual(detail["status"], "plugin-error")

    def test_hanging_health_probe_returns_diagnostic_failure(self):
        with patch.object(conda_ops, "run_cmd_capture", side_effect=subprocess.TimeoutExpired("conda", 30)):
            data, _, error, code = conda_ops._conda_info_json_capture(show_json_output=False)
        self.assertIsNone(data)
        self.assertEqual(code, 124)
        self.assertIn("timed out", error)

    def test_missing_executable_returns_diagnostic_failure(self):
        with patch.object(conda_ops, "run_cmd_capture", side_effect=FileNotFoundError("gone")):
            data, _, error, code = conda_ops._conda_info_json_capture(show_json_output=False)
        self.assertIsNone(data)
        self.assertEqual(code, 127)
        self.assertIn("gone", error)

    def test_plugin_disable_overrides_existing_false_setting(self):
        with patch.dict(os.environ, {"CONDA_NO_PLUGINS": "false"}):
            self.assertEqual(conda_ops._env_no_plugins()["CONDA_NO_PLUGINS"], "true")
            self.assertEqual(os.environ["CONDA_NO_PLUGINS"], "false")

    def test_bootstrap_repairs_charset_from_records_without_running_package_list(self):
        from argparse import Namespace
        from env_repair import verify_imports

        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            prefix = Path(temporary)
            site = prefix / "Lib" / "site-packages"
            site.mkdir(parents=True)
            (prefix / "conda-meta").mkdir()
            record = {"name": "charset-normalizer", "version": "1", "files": []}
            (prefix / "conda-meta" / "charset-normalizer-1-0.json").write_text(json.dumps(record), encoding="utf-8")
            broken = (False, False, {"status": "broken", "stderr": "AttributeError: module 'charset_normalizer.md' has no attribute 'CharInfo'"})
            stack.enter_context(patch.object(doctor, "conda_health_check", side_effect=[broken] + [(True, False, {})] * 3))
            for name, value in {"which": "mamba", "get_python_exe": "target-python",
                                "get_site_packages": [str(site)], "scan_env": {"issues": []}, "_approve": True}.items():
                stack.enter_context(patch.object(doctor, name, return_value=value))
            listing = stack.enter_context(patch.object(doctor, "get_env_package_entries"))
            core = stack.enter_context(patch.object(doctor, "conda_repair_core"))
            install = stack.enter_context(patch.object(verify_imports, "conda_install", return_value=True))
            args = Namespace(json=False, yes=True, debug=False)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertTrue(doctor._check_and_repair_conda_core(args=args, base_prefix=str(prefix), manager="mamba", channels=["conda-forge"]))
            self.assertIn("Installer: mamba", output.getvalue())
            listing.assert_not_called()
            core.assert_not_called()
            self.assertEqual(install.call_args.args[1:3], (["charset-normalizer"], "mamba"))


if __name__ == "__main__":
    unittest.main()
