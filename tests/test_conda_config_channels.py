import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestCondaConfigChannels(unittest.TestCase):
    def test_only_reads_top_level_channels_list(self):
        import env_repair.conda_config as cc

        mirrors = (
            "mirrored_channels:\n"
            "  conda-forge:\n"
            "    - https://conda.anaconda.org/conda-forge\n"
            "    - https://prefix.dev/conda-forge\n"
        )
        configs = [
            "channels:\n  - conda-forge\n" + mirrors,
            mirrors + "channels:\n  - conda-forge\n",
            "channels:\n- conda-forge\n\n# next setting\n" + mirrors,
            "channels: # repositories\n  # primary\n  - conda-forge\n"
            "proxy_servers:\n  https: http://proxy:8080\n"
            "custom_multichannels:\n  other:\n    - unrelated-channel\n",
            "custom_multichannels:\n  channels:\n    - unrelated-channel\n"
            "channels:\n  - conda-forge\n",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / ".condarc"
            with patch.dict(os.environ, {"CONDARC": str(config_path)}):
                for config in configs:
                    with self.subTest(config=config):
                        config_path.write_text(config, encoding="utf-8")
                        channels = cc.load_conda_channels(
                            has_conda=True, has_mamba=True
                        )
                        self.assertEqual(channels, ["conda-forge"])

    def test_falls_back_to_mamba_when_no_conda(self):
        import env_repair.conda_config as cc

        calls = []

        def fake_run_json_cmd(cmd, *, show_json_output):
            calls.append(cmd)
            if cmd[:3] == ["mamba", "config", "list"]:
                return {"channels": ["conda-forge", "nodefaults"]}
            return None

        with patch.object(cc, "load_conda_channels_from_condarc", return_value=[]):
            with patch.object(cc, "run_json_cmd", side_effect=fake_run_json_cmd):
                ch = cc.load_conda_channels(base_prefix=None, has_conda=False, has_mamba=True, show_json_output=False)

        self.assertEqual(ch, ["conda-forge", "nodefaults"])
        self.assertIn(["mamba", "config", "list", "--json"], calls)

    def test_prefers_conda_when_available(self):
        import env_repair.conda_config as cc

        calls = []

        def fake_run_json_cmd(cmd, *, show_json_output):
            calls.append(cmd)
            if cmd[:2] == ["conda", "config"]:
                return {"channels": ["defaults", "anaconda"]}
            return None

        with patch.object(cc, "load_conda_channels_from_condarc", return_value=[]):
            with patch.object(cc, "run_json_cmd", side_effect=fake_run_json_cmd):
                ch = cc.load_conda_channels(base_prefix=None, has_conda=True, has_mamba=True, show_json_output=False)

        self.assertEqual(ch, ["defaults", "anaconda"])
        self.assertIn(["conda", "config", "--show", "channels", "--json"], calls)

