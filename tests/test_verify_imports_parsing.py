import os
import tempfile
import unittest
from pathlib import Path

from env_repair.verify_imports import (
    _dist_has_local_direct_url,
    _is_blacklist_skip_active,
    _is_numba_llvmlite_version_error,
    _is_python_pin_conflict,
    _should_skip_local_unmanaged_dist,
    _extract_missing_module_name,
    _extract_solver_offenders,
    _load_verify_imports_blacklist,
    parse_record_file,
    _save_verify_imports_blacklist,
)


class TestVerifyImportsParsing(unittest.TestCase):
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
    ├─ pysimplegui ==5.0.4 pyhd8ed1ab_0 does not exist (perhaps a typo or a missing channel);
    └─ tables =* * does not exist (perhaps a typo or a missing channel).
"""
        offenders = _extract_solver_offenders(text)
        self.assertIn("pysimplegui", offenders)
        self.assertIn("tables", offenders)

    def test_blacklist_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            prev = Path.cwd()
            os.chdir(td)
            try:
                data = {"3.13": {"tables": {"conda": "tables", "reason": "solver_incompatible"}}}
                _save_verify_imports_blacklist(data)
                loaded = _load_verify_imports_blacklist()
                self.assertIn("3.13", loaded)
                self.assertIn("tables", loaded["3.13"])
            finally:
                os.chdir(prev)

    def test_parse_record_file_ignores_non_python_top_level_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "RECORD"
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
        with tempfile.TemporaryDirectory() as td:
            dist = Path(td) / "passivbot_rust-0.1.0.dist-info"
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
            "  └─ python =3.14 *, which conflicts with any installable versions previously reported."
        )
        self.assertTrue(_is_python_pin_conflict(text))
        self.assertFalse(_is_python_pin_conflict("some other solver failure"))


if __name__ == "__main__":
    unittest.main()
