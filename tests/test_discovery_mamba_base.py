import unittest
import os
from unittest.mock import patch


class TestDiscoveryMambaBase(unittest.TestCase):
    def test_uses_mamba_base_environment_key(self):
        import env_repair.discovery as d

        def fake_which(cmd):
            return cmd == "mamba"

        def fake_run_json_cmd(cmd, *, show_json_output):
            if cmd == ["mamba", "info", "--json"]:
                return {
                    "base environment": r"I:\Mambaforge",
                    "envs": [r"I:\Mambaforge", r"I:\Mambaforge\envs\x"],
                }
            return None

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(d, "which", side_effect=fake_which):
                with patch.object(d, "run_json_cmd", side_effect=fake_run_json_cmd):
                    with patch.object(d, "add_env", side_effect=lambda envs, path: envs.add(path) if path else None):
                        envs, base_prefix, manager = d.discover_envs(show_json_output=False)

        self.assertEqual(base_prefix, r"I:\Mambaforge")
        self.assertEqual(manager, "mamba")
        self.assertIn(r"I:\Mambaforge", envs)
