import os
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from env_repair.verify_imports import (
    _collect_defaults_disabled_pip_fallback_candidates,
    _conda_search_has_package,
    _dist_has_local_direct_url,
    _extract_missing_module_name,
    _extract_solver_offenders,
    _is_import_timeout_error,
    _is_blacklist_skip_active,
    _is_nacl_sodium_dll_error,
    _is_numba_llvmlite_version_error,
    _is_numba_numpy_upper_bound_error,
    _is_protobuf_generatedfile_mismatch_error,
    _is_python_pin_conflict,
    _legacy_unmaintained_meta,
    _load_verify_imports_blacklist,
    _missing_module_conda_fallback,
    _pick_conda_candidate_for_local_direct_url,
    _preclean_module_roots_for_conda_package,
    _requires_hard_remove_before_reinstall,
    _search_json_has_package,
    _save_verify_imports_blacklist,
    _should_skip_local_unmanaged_dist,
    _stubborn_import_pip_target,
    parse_record_file,
)


class TestVerifyImportsParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(f"__test_tmp_{uuid4().hex}")
        self.tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extract_missing_module_name(self):
        err = "ModuleNotFoundError: No module named 'numpy'"
        self.assertEqual(_extract_missing_module_name(err), "numpy")
        err2 = "ModuleNotFoundError: No module named pyproject_api"
        self.assertEqual(_extract_missing_module_name(err2), "pyproject_api")
        err3 = "No module named 'numpy.linalg'"
        self.assertEqual(_extract_missing_module_name(err3), "numpy")

    def test_extract_solver_offenders(self):
        text = """error    libmamba Could not solve for environment specs
    The following packages are incompatible
    - pysimplegui ==5.0.4 pyhd8ed1ab_0 does not exist (perhaps a typo or a missing channel);
    - tables =* * does not exist (perhaps a typo or a missing channel).
"""
        offenders = _extract_solver_offenders(text)
        self.assertIn("pysimplegui", offenders)
        self.assertIn("tables", offenders)

    def test_blacklist_roundtrip(self):
        prev = Path.cwd()
        os.chdir(self.tmp)
        try:
            data = {"3.13": {"tables": {"conda": "tables", "reason": "solver_incompatible"}}}
            _save_verify_imports_blacklist(data)
            loaded = _load_verify_imports_blacklist()
            self.assertIn("3.13", loaded)
            self.assertIn("tables", loaded["3.13"])
        finally:
            os.chdir(prev)

    def test_parse_record_file_ignores_non_python_top_level_dirs(self):
        rp = self.tmp / "RECORD"
        rp.write_text(
            "\n".join(
                [
                    "typings/bokeh/chart.d.ts,sha256=x,1",
                    "bokeh/__init__.py,sha256=x,1",
                    "bokeh/plotting.py,sha256=x,1",
                    "six.py,sha256=x,1",
                ]
            ),
            encoding="utf-8",
        )
        found = parse_record_file(rp)
        self.assertIn("bokeh", found)
        self.assertIn("six", found)
        self.assertNotIn("typings", found)

    def test_dist_has_local_direct_url(self):
        dist = self.tmp / "passivbot_rust-0.1.0.dist-info"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "direct_url.json").write_text(
            '{"url":"file:///K:/SweepStar/passivbot_rust-0.1.0-cp313-cp313-win_amd64.whl"}',
            encoding="utf-8",
        )
        self.assertTrue(_dist_has_local_direct_url(dist))
        self.assertTrue(_should_skip_local_unmanaged_dist(dist, kind="unknown"))
        self.assertFalse(_should_skip_local_unmanaged_dist(dist, kind="pip"))

    def test_numba_llvmlite_error_detection(self):
        err = (
            "ImportError: Numba requires at least version 0.46.0 of llvmlite.\n"
            "Installed version is 0.44.0rc2.\n"
            "Please update llvmlite."
        )
        self.assertTrue(_is_numba_llvmlite_version_error(err))
        self.assertFalse(_is_numba_llvmlite_version_error("some other import error"))

    def test_numba_numpy_upper_bound_error_detection(self):
        err = "ImportError: Numba needs NumPy 2.3 or less. Got NumPy 2.4."
        self.assertTrue(_is_numba_numpy_upper_bound_error(err))
        self.assertFalse(_is_numba_numpy_upper_bound_error("some other import error"))

    def test_blacklist_skip_overridden_if_conda_installed(self):
        self.assertFalse(
            _is_blacklist_skip_active(
                kind="conda",
                name="altair",
                blocked_names={"altair"},
                initially_installed={"altair"},
            )
        )
        self.assertTrue(
            _is_blacklist_skip_active(
                kind="conda",
                name="altair",
                blocked_names={"altair"},
                initially_installed=set(),
            )
        )

    def test_python_pin_conflict_detection(self):
        text = (
            "pin on python =3.14 * is not installable because it requires\n"
            "  - python =3.14 *, which conflicts with any installable versions previously reported."
        )
        self.assertTrue(_is_python_pin_conflict(text))
        self.assertFalse(_is_python_pin_conflict("some other solver failure"))

    def test_collect_defaults_disabled_pip_fallback_candidates(self):
        conda_items = [
            {
                "dist": "nose-1.3.7.dist-info",
                "kind": "conda",
                "name": "nose",
                "import": "nose",
                "reason": "defaults-disabled: attempting reinstall from configured channels",
            },
            {
                "dist": "numpy-2.3.0.dist-info",
                "kind": "conda",
                "name": "numpy",
                "import": "numpy",
                "reason": None,
            },
        ]
        out = _collect_defaults_disabled_pip_fallback_candidates(
            conda_items=conda_items,
            failed_pkgs=["nose", "numpy"],
        )
        self.assertEqual(len(out), 0)

    def test_legacy_unmaintained_meta(self):
        meta = _legacy_unmaintained_meta("nose")
        self.assertIsNotNone(meta)
        self.assertIn("unmaintained", meta.get("reason", ""))
        self.assertIsNone(_legacy_unmaintained_meta("numpy"))

    def test_collect_defaults_disabled_pip_fallback_candidates_non_legacy(self):
        conda_items = [
            {
                "dist": "foo-1.0.0.dist-info",
                "kind": "conda",
                "name": "foo",
                "import": "foo",
                "reason": "defaults-disabled: attempting reinstall from configured channels",
            }
        ]
        out = _collect_defaults_disabled_pip_fallback_candidates(
            conda_items=conda_items,
            failed_pkgs=["foo"],
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "foo")

    def test_missing_module_conda_fallback(self):
        self.assertEqual(_missing_module_conda_fallback("_brotli"), "brotli-python")
        self.assertEqual(_missing_module_conda_fallback("_argon2_cffi_bindings"), "argon2-cffi-bindings")
        self.assertIsNone(_missing_module_conda_fallback("pkg_resources"))
        self.assertIsNone(_missing_module_conda_fallback("does_not_exist"))

    def test_streamlit_protobuf_mismatch_detection(self):
        err = (
            "File \"C:\\Anaconda3\\Lib\\site-packages\\google\\protobuf\\descriptor.py\", line 1038, in __new__\n"
            "  _message.Message._CheckCalledFromGeneratedFile()"
        )
        self.assertTrue(_is_protobuf_generatedfile_mismatch_error(err))
        self.assertFalse(_is_protobuf_generatedfile_mismatch_error("some other error"))

    def test_preclean_module_root_mapping(self):
        self.assertEqual(_preclean_module_roots_for_conda_package("pyarrow"), ["pyarrow"])
        self.assertEqual(_preclean_module_roots_for_conda_package("scikit-learn"), ["sklearn"])
        self.assertEqual(_preclean_module_roots_for_conda_package("imbalanced-learn"), ["imblearn"])
        self.assertEqual(_preclean_module_roots_for_conda_package("pygithub"), ["github"])
        self.assertEqual(_preclean_module_roots_for_conda_package("pynacl"), ["nacl"])
        self.assertEqual(_preclean_module_roots_for_conda_package("streamlit"), ["streamlit"])
        self.assertEqual(_preclean_module_roots_for_conda_package("numpy"), [])

    def test_stubborn_import_pip_target(self):
        self.assertEqual(_stubborn_import_pip_target("pyarrow"), "pyarrow")
        self.assertEqual(_stubborn_import_pip_target("github"), "pygithub")
        self.assertEqual(_stubborn_import_pip_target("streamlit"), "streamlit")
        self.assertIsNone(_stubborn_import_pip_target("sklearn"))

    def test_hard_remove_before_reinstall_mapping(self):
        self.assertTrue(_requires_hard_remove_before_reinstall("pygithub"))
        self.assertFalse(_requires_hard_remove_before_reinstall("streamlit"))

    def test_nacl_sodium_dll_error_detection(self):
        err = "ImportError: DLL load failed while importing _sodium: Die angegebene Prozedur wurde nicht gefunden."
        self.assertTrue(_is_nacl_sodium_dll_error(err))
        self.assertFalse(_is_nacl_sodium_dll_error("some other import error"))

    def test_import_timeout_detection(self):
        self.assertTrue(_is_import_timeout_error("Import timed out (>30s)"))
        self.assertFalse(_is_import_timeout_error("ModuleNotFoundError: No module named x"))

    def test_search_json_has_package_formats(self):
        payload_a = {"pygithub": [{"name": "pygithub", "version": "2.8.1"}]}
        payload_b = {"result": {"pkgs": [{"name": "pygithub"}, {"name": "requests"}]}}
        self.assertTrue(_search_json_has_package(payload_a, "pygithub"))
        self.assertTrue(_search_json_has_package(payload_b, "pygithub"))
        self.assertFalse(_search_json_has_package(payload_a, "streamlit"))

    def test_conda_search_has_package_guards(self):
        self.assertFalse(_conda_search_has_package("pip", "pygithub", debug=False))
        self.assertFalse(_conda_search_has_package("mamba", "", debug=False))

    def test_pick_conda_candidate_for_local_direct_url_no_manager(self):
        out = _pick_conda_candidate_for_local_direct_url(
            manager="",
            dist_name="hvplot",
            failed_import="hvplot",
            debug=False,
        )
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
