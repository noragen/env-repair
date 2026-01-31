import unittest

from env_repair.verify_imports import (
    _collect_explicit_removals,
    _collect_wrapper_removals,
    _extract_replacement_from_search_json,
    _is_boto_vendored_six_moves_error,
    _is_removed_collections_mapping_error,
    CRITICAL_PIP_PACKAGES,
)


class TestVerifyImportsWrapperMapping(unittest.TestCase):
    def test_extracts_anaconda_auth_from_conda_token_search_json(self):
        sample = {
            "query": {"query": "conda-token=0.7.0", "type": "search"},
            "result": {
                "status": "OK",
                "pkgs": [
                    {
                        "name": "conda-token",
                        "version": "0.7.0",
                        "depends": ["anaconda-auth >=0.10.0", "conda"],
                    }
                ],
            },
        }
        self.assertEqual(_extract_replacement_from_search_json(sample, target="anaconda-auth"), "anaconda-auth")

    def test_returns_none_if_missing(self):
        self.assertIsNone(_extract_replacement_from_search_json({}, target="anaconda-auth"))
        self.assertIsNone(_extract_replacement_from_search_json({"result": {"pkgs": [{}]}}, target="anaconda-auth"))

    def test_collects_wrapper_removals_only_if_replacement_installed(self):
        plan = [
            {"kind": "conda", "name": "anaconda-auth", "uninstall": {"kind": "conda", "name": "conda-token"}},
            {"kind": "conda", "name": "something-else", "uninstall": {"kind": "pip", "name": "foo"}},
        ]
        conda_rm, pip_rm = _collect_wrapper_removals(plan, conda_installed={"anaconda-auth"})
        self.assertEqual(conda_rm, ["conda-token"])
        self.assertEqual(pip_rm, [])

    def test_collects_explicit_removals(self):
        plan = [
            {"kind": "remove", "uninstall": {"kind": "conda", "name": "boto"}},
            {"kind": "remove", "uninstall": {"kind": "pip", "name": "some-pip-pkg"}},
            {"kind": "conda", "name": "something-else", "uninstall": {"kind": "pip", "name": "foo"}},
        ]
        conda_rm, pip_rm = _collect_explicit_removals(plan)
        self.assertEqual(conda_rm, ["boto"])
        self.assertEqual(pip_rm, ["some-pip-pkg"])

    def test_detects_removed_collections_mapping_error(self):
        err = "ImportError: cannot import name 'Mapping' from 'collections' (C:\\\\Python\\\\Lib\\\\collections\\\\__init__.py)"
        self.assertTrue(_is_removed_collections_mapping_error(err))
        self.assertFalse(_is_removed_collections_mapping_error("some other error"))

    def test_detects_boto_vendored_six_moves_error(self):
        err = "ModuleNotFoundError: No module named 'boto.vendored.six.moves'"
        self.assertTrue(_is_boto_vendored_six_moves_error(err))
        self.assertFalse(_is_boto_vendored_six_moves_error("some other error"))

    def test_critical_pip_packages_contains_pip_basics(self):
        self.assertIn("pip", CRITICAL_PIP_PACKAGES)
        self.assertIn("setuptools", CRITICAL_PIP_PACKAGES)
        self.assertIn("wheel", CRITICAL_PIP_PACKAGES)


if __name__ == "__main__":
    unittest.main()
