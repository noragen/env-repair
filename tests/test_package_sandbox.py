import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_repair import cli, package_sandbox


class TestPackageSandbox(unittest.TestCase):
    def test_generic_cli_preserves_non_qt_parameters(self):
        args = cli.build_parser().parse_args(["sandbox", "--env", "fixture", "--target", "scipy",
                                             "--conda-package", "numpy>=2", "--pypi-package", "requests==2.32.5",
                                             "--python-version", "3.13", "--check-import", "scipy.linalg"])
        self.assertEqual(args.cmd, "sandbox")
        self.assertEqual(args.target, ["scipy"])
        with patch("env_repair.discovery.discover_envs", return_value=(["fixture"], "root", "mamba")), \
                patch("env_repair.discovery.select_envs", return_value=["fixture"]), \
                patch("env_repair.discovery.get_python_exe", return_value="source-python"), \
                patch.object(package_sandbox, "test_packages", return_value={"ok": True}) as test:
            self.assertTrue(package_sandbox.run_sandbox(args)["ok"])
        self.assertEqual(test.call_args.kwargs["targets"], ["scipy"])
        self.assertEqual(test.call_args.kwargs["conda_specs"], ["numpy>=2"])
        self.assertEqual(test.call_args.kwargs["extra_imports"], ["scipy.linalg"])

    def test_target_inventory_ignores_unrelated_local_artifacts_metadata_and_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "site"
            meta = root / "conda-meta"
            meta.mkdir()
            for name in ("scipy", "unrelated"):
                dist = site / f"{name}-1.dist-info"
                dist.mkdir(parents=True)
                (dist / "METADATA").write_text(f"Name: {name}\nVersion: 1\n", encoding="utf-8")
                record = {"name": name, "version": "1", "files": [f"site/{name}-1.dist-info/METADATA"]}
                if name == "unrelated":
                    record["url"] = "file:///local-build.conda"
                (meta / f"{name}.json").write_text(json.dumps(record), encoding="utf-8")
            (site / "broken-1.dist-info").mkdir()
            (meta / "pinned").write_text("python=3.15\nunrelated=1", encoding="utf-8")
            with patch("env_repair.discovery.get_site_packages", return_value=[str(site)]):
                inventory = package_sandbox._inventory(root, "python", targets=["scipy"])
            self.assertEqual([record["name"] for record in inventory["records"]], ["scipy"])
            self.assertEqual([item["name"] for item in inventory["packages"]], ["scipy"])
            self.assertIsNone(inventory["pins"])

    def test_unknown_target_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta = root / "conda-meta"
            meta.mkdir()
            (meta / "python.json").write_text('{"name":"python","version":"3.15"}', encoding="utf-8")
            with patch("env_repair.discovery.get_site_packages", return_value=[]), self.assertRaisesRegex(ValueError, "not found"):
                package_sandbox._inventory(root, "python", targets=["missing"])

    def test_pip_only_source_does_not_require_conda_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "site/requests-1.dist-info"
            dist.mkdir(parents=True)
            (dist / "METADATA").write_text("Name: requests\nVersion: 1\n", encoding="utf-8")
            (dist / "INSTALLER").write_text("pip", encoding="utf-8")
            with patch("env_repair.discovery.get_site_packages", return_value=[str(dist.parent)]):
                inventory = package_sandbox._inventory(root, "python", targets=["requests"])
            self.assertEqual(inventory["records"], [])
            self.assertEqual(inventory["packages"][0]["kind"], "pypi")

    def test_generic_sandbox_resolves_only_roots_and_checks_installed_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = {"records": [{"name": "scipy", "version": "1"}], "packages": []}
            installed = {"records": [], "packages": [{"name": "scipy", "imports": ["scipy"]},
                                                       {"name": "numpy", "imports": ["numpy"]}]}
            with patch.object(package_sandbox.tempfile, "mkdtemp", return_value=directory), \
                    patch.object(package_sandbox, "_report_directory", return_value=root.parent / (root.name + "-reports")), \
                    patch.object(package_sandbox, "_inventory", side_effect=[inventory, installed]) as read, \
                    patch.object(package_sandbox, "run_cmd_live_capture", return_value=(0, "", "")) as live, \
                    patch.object(package_sandbox, "run_cmd_capture", return_value=(0, "@EXPLICIT\n", "")), \
                    patch.object(package_sandbox, "_site_directory", side_effect=ValueError("multiple site directories")) as locate, \
                    patch.object(package_sandbox, "_checks", return_value=[]) as checks, \
                    contextlib.redirect_stdout(io.StringIO()):
                result = package_sandbox.test_packages(env_path="source", python_exe="python", manager="mamba",
                                                       channels=["conda-forge"], python_spec="python=3.13",
                                                       targets=["scipy"], conda_specs=["scipy>=2"])
            reports = Path(result["report_path"]).parent
            self.addCleanup(shutil.rmtree, reports)
            specs = (reports / "requirements.txt").read_text().splitlines()
            self.assertEqual(set(specs), {"python=3.13", "scipy>=2", "pip"})
            self.assertEqual(read.call_args_list[0].kwargs["targets"], ["scipy"])
            self.assertEqual(checks.call_args.args[1], installed["packages"])
            self.assertEqual(checks.call_args.args[2], ())
            self.assertTrue(result["ok"])
            self.assertEqual(result["scope"], "target-packages")
            self.assertTrue(result["apply_blocked"])
            self.assertEqual(live.call_count, 1)
            locate.assert_not_called()

    def test_copy_site_uses_target_sysconfig_not_ambiguous_search_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            legacy = root / "Lib/site-packages"
            current = root / "lib/python/site-packages"
            legacy.mkdir(parents=True)
            current.mkdir(parents=True)
            payload = {"prefix": str(root), "purelib": str(current)}
            with patch("env_repair.discovery.get_site_packages", return_value=[str(current), str(root), str(legacy)]) as search, \
                    patch.object(package_sandbox, "run_cmd_capture", return_value=(0, json.dumps(payload), "")) as capture:
                selected = package_sandbox._site_directory("sandbox-python", expected_prefix=root)
            self.assertEqual(Path(selected), current.resolve())
            search.assert_not_called()
            self.assertEqual(capture.call_args.args[0][:3], ["sandbox-python", "-I", "-c"])
            self.assertEqual(capture.call_args.kwargs["timeout"], 30)

    def test_copy_site_rejects_external_relative_missing_and_wrong_prefix_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for payload in ({"prefix": str(root), "purelib": str(root.parent / "outside")},
                            {"prefix": str(root), "purelib": "relative/site-packages"},
                            {"prefix": str(root)},
                            {"prefix": str(root.parent), "purelib": str(root)},
                            {"prefix": str(root), "purelib": str(root)}, []):
                with self.subTest(payload=payload), patch.object(package_sandbox, "run_cmd_capture", return_value=(0, json.dumps(payload), "")):
                    with self.assertRaises(package_sandbox.SandboxInventoryError):
                        package_sandbox._site_directory("sandbox-python", expected_prefix=root)

    def test_copy_site_reports_interpreter_and_json_failures_without_guessing(self):
        for code, output, error in ((1, "", "Python startup failed"), (0, "not JSON", "")):
            with self.subTest(code=code), patch.object(package_sandbox, "run_cmd_capture", return_value=(code, output, error)):
                with self.assertRaises(package_sandbox.SandboxInventoryError):
                    package_sandbox._site_directory("sandbox-python")

    def test_real_interpreter_supplies_its_own_copy_destination(self):
        import sysconfig

        self.assertEqual(package_sandbox._site_directory(sys.executable, expected_prefix=sys.prefix),
                         str(Path(sysconfig.get_path("purelib")).resolve()))

    def test_local_copy_uses_sysconfig_destination_and_sandbox_still_validates_and_cleans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            dist = source / "localpkg-1.dist-info"
            dist.mkdir(parents=True)
            (source / "localpkg.py").write_text("value = 1\n", encoding="utf-8")
            (dist / "METADATA").write_text("Name: localpkg\nVersion: 1\n", encoding="utf-8")
            (dist / "RECORD").write_text("localpkg.py,,\nlocalpkg-1.dist-info/METADATA,,\nlocalpkg-1.dist-info/RECORD,,\n", encoding="utf-8")
            workspace = root / "sandbox"
            workspace.mkdir()
            prefix = workspace / "environment"
            site = prefix / "lib/python/site-packages"
            inventory = {"records": [], "packages": [{"name": "localpkg", "version": "1", "kind": "local", "dist": str(dist), "imports": ["localpkg"]}]}

            def capture(command, **kwargs):
                if "-c" in command:
                    return 0, json.dumps({"prefix": str(prefix), "purelib": str(site)}), ""
                return 0, "@EXPLICIT\n", ""

            def validate(python, packages, imports):
                self.assertEqual((site / "localpkg.py").read_bytes(), (source / "localpkg.py").read_bytes())
                self.assertFalse((prefix / "Lib/site-packages/localpkg.py").exists())
                return []

            with patch.object(package_sandbox.tempfile, "mkdtemp", return_value=str(workspace)), \
                    patch.object(package_sandbox, "_report_directory", return_value=root / "reports"), \
                    patch.object(package_sandbox, "_inventory", return_value=inventory), \
                    patch.object(package_sandbox, "run_cmd_live_capture", return_value=(0, "", "")), \
                    patch.object(package_sandbox, "run_cmd_capture", side_effect=capture), \
                    patch.object(package_sandbox, "_checks", side_effect=validate), contextlib.redirect_stdout(io.StringIO()):
                result = package_sandbox.test_packages(env_path=source, python_exe="source-python", manager="mamba",
                                                       channels=[], python_spec="python=3.15", targets=["localpkg"])
            self.assertTrue(result["ok"])
            self.assertTrue(result["cleanup"]["ok"])
            self.assertFalse(workspace.exists())
            self.assertEqual(result["local_copy_site"], str(site.resolve()))
            self.assertTrue((source / "localpkg.py").is_file())

    def test_failure_categories_do_not_misclassify_network_as_missing_package(self):
        cases = {"SSLError: No matching distribution found": "network", "PackagesNotFoundError: missing": "candidate-unavailable",
                 "ResolutionImpossible": "dependency-conflict", "requires a different Python": "python-compatibility",
                 "DLL load failed": "binary-runtime", "No module named custom": "missing-import",
                 "Permission denied": "permission", "Import timed out": "timeout", "unsupportedwheel": "unsupported-wheel",
                 "cannot import name X": "import-api-mismatch", "unrecognized output": "unknown"}
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(package_sandbox.classify_failure(message), expected)

    def test_failed_stage_preserves_diagnostics_without_retrying_or_removing(self):
        report = {}
        with patch.object(package_sandbox, "run_cmd_live_capture", return_value=(1, "No matching distribution found", "SSLError")) as live:
            with self.assertRaises(ValueError):
                package_sandbox._run_stage(["pip", "download"], stage="download", report=report)
        self.assertEqual(report["failure_category"], "network")
        self.assertEqual(report["stages"][0]["stdout"], "No matching distribution found")
        self.assertEqual(live.call_count, 1)

    def test_no_targets_urls_and_env_repair_requests_are_rejected(self):
        for specs in ([], ["--upgrade"], ["https://example.test/package.whl"], ["env-repair"]):
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                package_sandbox.test_packages(env_path="source", python_exe="python", manager="mamba", channels=[],
                                               python_spec="python=3.13", conda_specs=specs)
