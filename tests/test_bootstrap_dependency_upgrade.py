import contextlib
import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from env_repair import conda_ops, doctor, verify_imports


class TestBootstrapDependencyUpgrade(unittest.TestCase):
    def test_upgrade_excludes_installed_version_and_respects_pins(self):
        with tempfile.TemporaryDirectory() as temporary:
            meta = Path(temporary) / "conda-meta"
            meta.mkdir()
            (meta / "anaconda-auth-0.8.4-0.json").write_text(json.dumps({
                "name": "anaconda-auth", "version": "0.8.4"}), encoding="utf-8")
            with patch.object(conda_ops, "conda_install", return_value=True) as install, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(conda_ops.conda_upgrade_dependencies(temporary, ["anaconda-auth"], "mamba", ["conda-forge"]))
            install.assert_called_once_with(temporary, ["anaconda-auth>0.8.4"], "mamba", ["conda-forge"],
                                            ignore_pinned=False, force_reinstall=False)

    def test_missing_version_does_not_fall_back_to_identical_reinstall(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(conda_ops, "conda_install") as install:
            self.assertFalse(conda_ops.conda_upgrade_dependencies(temporary, ["anaconda-auth"], "mamba", []))
            install.assert_not_called()

    def bootstrap(self, upgrade_succeeds, healthy_after_upgrade):
        error = "Error while loading conda entry point: anaconda-auth (module 'conda.plugins' has no attribute 'CondaAuthHandler')"
        broken = (False, True, {"status": "plugin-error", "stderr": error})
        healthy = (True, False, {})
        with contextlib.ExitStack() as stack:
            for name, value in {"which": "mamba", "get_python_exe": "target-python",
                                "get_site_packages": ["fixture-site"], "scan_env": {"issues": []}, "_approve": True}.items():
                stack.enter_context(patch.object(doctor, name, return_value=value))
            health = [broken, broken] + ([healthy] * 3 if healthy_after_upgrade else [broken])
            stack.enter_context(patch.object(doctor, "conda_health_check", side_effect=health))
            dependency = stack.enter_context(patch.object(verify_imports, "_repair_import_dependencies", return_value=(
                {"anaconda-auth"}, [{"action": "conda_dependency_reinstall", "packages": ["anaconda-auth"], "ok": True}])))
            upgrade = stack.enter_context(patch.object(doctor, "conda_upgrade_dependencies", return_value=upgrade_succeeds))
            core = stack.enter_context(patch.object(doctor, "conda_repair_core"))
            remove = stack.enter_context(patch.object(conda_ops, "conda_remove"))
            args = Namespace(json=False, yes=True, debug=False)
            with contextlib.redirect_stdout(io.StringIO()):
                result = doctor._check_and_repair_conda_core(args=args, base_prefix="fixture-prefix", manager="mamba", channels=["conda-forge"])
            dependency.assert_called_once()
            upgrade.assert_called_once_with("fixture-prefix", ["anaconda-auth"], "mamba", ["conda-forge"])
            core.assert_not_called()
            remove.assert_not_called()
            return result

    def test_successful_relink_with_persistent_error_escalates_to_upgrade(self):
        self.assertTrue(self.bootstrap(True, True))

    def test_solver_failure_does_not_remove_packages_or_loop(self):
        self.assertFalse(self.bootstrap(False, False))

    def test_persistent_error_after_upgrade_is_not_success(self):
        self.assertFalse(self.bootstrap(True, False))


if __name__ == "__main__":
    unittest.main()
