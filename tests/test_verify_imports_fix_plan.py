import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
import shutil

from env_repair.verify_imports import attempt_fix


class TestVerifyImportsFixPlan(unittest.TestCase):
    @patch("env_repair.verify_imports.conda_install", return_value=True)
    @patch("env_repair.verify_imports.conda_install_capture", return_value=(True, "", ""))
    @patch("env_repair.verify_imports.load_conda_channels", return_value=["conda-forge"])
    @patch("env_repair.verify_imports.which", return_value="mamba")
    @patch("env_repair.verify_imports.get_env_package_entries")
    @patch("env_repair.verify_imports.is_conda_env", return_value=True)
    def test_streamlit_protobuf_mismatch_adds_protobuf_repair(
        self,
        _mock_is_conda_env,
        mock_get_entries,
        _mock_which,
        _mock_load_channels,
        _mock_install_capture,
        _mock_install,
    ):
        mock_get_entries.return_value = [
            {"name": "streamlit", "version": "1.8.0", "channel": "conda-forge"},
            {"name": "protobuf", "version": "6.33.4", "channel": "conda-forge"},
        ]

        dist = Path(f"__test_tmp_fix_{uuid4().hex}") / "streamlit-1.8.0.dist-info"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "METADATA").write_text("Name: streamlit\nVersion: 1.8.0\n", encoding="utf-8")

        failures = [
            {
                "dist": dist.name,
                "dist_path": dist,
                "import": "streamlit",
                "ok": False,
                "error": (
                    "Traceback\n"
                    "  File \".../google/protobuf/descriptor.py\", line 1038, in __new__\n"
                    "    _message.Message._CheckCalledFromGeneratedFile()\n"
                ),
            }
        ]

        report = attempt_fix(
            failures,
            python_exe="C:\\Anaconda3\\python.exe",
            env_path="C:\\Anaconda3",
            manager="mamba",
            base_prefix="C:\\Anaconda3",
            debug=False,
        )
        shutil.rmtree(dist.parent, ignore_errors=True)

        plan_names = [p.get("name") for p in report.get("plan") or [] if p.get("kind") == "conda"]
        self.assertIn("protobuf>=4,<7", plan_names)
        self.assertIn("streamlit>=1.30", plan_names)


if __name__ == "__main__":
    unittest.main()
