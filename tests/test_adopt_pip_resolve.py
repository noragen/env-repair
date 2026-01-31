import unittest

from env_repair.repair import (
    PYPI_TO_CONDA_NAME_MAP,
    _adopt_pip_core_pattern,
    _pypi_to_conda_override,
    _resolve_adopt_pip_target,
)


class TestAdoptPipResolve(unittest.TestCase):
    def test_pypi_to_conda_override_map_is_normalized(self):
        # Keys should be directly usable with normalize_name().
        self.assertEqual(PYPI_TO_CONDA_NAME_MAP.get("opencv-python"), "opencv")
        self.assertEqual(PYPI_TO_CONDA_NAME_MAP.get("python-levenshtein"), "levenshtein")

    def test_pypi_to_conda_override_is_case_insensitive(self):
        self.assertEqual(_pypi_to_conda_override("Build"), "python-build")
        self.assertEqual(_pypi_to_conda_override("opencv-PYTHON"), "opencv")

    def test_core_pattern_replaces_separators(self):
        self.assertEqual(_adopt_pip_core_pattern("langchain_community"), "langchain*community")
        self.assertEqual(_adopt_pip_core_pattern("langchain-community"), "langchain*community")
        self.assertEqual(_adopt_pip_core_pattern("Flask-Cors"), "Flask*Cors")

    def test_core_pattern_does_not_inject_wildcards(self):
        self.assertEqual(_adopt_pip_core_pattern("pysha3"), "pysha3")
        self.assertEqual(_adopt_pip_core_pattern("0xcontractaddresses"), "0xcontractaddresses")

    def test_pass1_prefers_core_pattern_over_exact_name(self):
        # Mirrors adopt-pip pass1 term generation policy: if core differs, skip exact.
        pip_name = "streamlit-chat"
        core = _adopt_pip_core_pattern(pip_name)
        self.assertEqual(core, "streamlit*chat")
        terms = [core] if core != pip_name else [pip_name]
        self.assertEqual(terms, ["streamlit*chat"])

        pip_name2 = "pysha3"
        core2 = _adopt_pip_core_pattern(pip_name2)
        self.assertEqual(core2, "pysha3")
        terms2 = [core2] if core2 != pip_name2 else [pip_name2]
        self.assertEqual(terms2, ["pysha3"])

    def test_resolves_by_alnum_match(self):
        available = {"langchain-community", "something-else"}
        self.assertEqual(_resolve_adopt_pip_target(pip_name="langchain_community", available=available), "langchain-community")

    def test_resolves_msgpack_python_suffix(self):
        available = {"msgpack-python", "other"}
        self.assertEqual(_resolve_adopt_pip_target(pip_name="msgpack", available=available), "msgpack-python")

    def test_resolves_python_prefix_alias(self):
        available = {"python-build", "other"}
        self.assertEqual(_resolve_adopt_pip_target(pip_name="build", available=available), "python-build")

    def test_resolves_py_suffix_alias(self):
        available = {"ccxt-py", "other"}
        self.assertEqual(_resolve_adopt_pip_target(pip_name="ccxt", available=available), "ccxt-py")

    def test_resolves_safe_prefix_alias(self):
        available = {"safe-pysha3", "other"}
        self.assertEqual(_resolve_adopt_pip_target(pip_name="pysha3", available=available), "safe-pysha3")

    def test_prefers_exact_match(self):
        available = {"foo-bar", "foo_bar"}
        self.assertEqual(_resolve_adopt_pip_target(pip_name="foo_bar", available=available), "foo_bar")


if __name__ == "__main__":
    unittest.main()
