import unittest

from env_repair.repair import _adopt_pip_force_uninstall_plan


class TestAdoptPipForceUninstall(unittest.TestCase):
    def test_force_uninstall_pysha3_when_safe_present(self):
        pip_versions = {"pysha3": "1.0.2"}
        entries = [
            {"name": "safe-pysha3", "version": "1.0.4", "channel": "conda-forge"},
            {"name": "pysha3", "version": "1.0.2", "channel": "pypi"},
        ]
        pip_rm, relink, details = _adopt_pip_force_uninstall_plan(pip_versions=pip_versions, entries=entries)
        self.assertEqual(pip_rm, ["pysha3"])
        self.assertEqual(relink, ["safe-pysha3"])
        self.assertEqual(len(details), 1)

    def test_no_force_uninstall_if_replacement_missing(self):
        pip_versions = {"pysha3": "1.0.2"}
        entries = [{"name": "pysha3", "version": "1.0.2", "channel": "pypi"}]
        pip_rm, relink, details = _adopt_pip_force_uninstall_plan(pip_versions=pip_versions, entries=entries)
        self.assertEqual(pip_rm, [])
        self.assertEqual(relink, [])
        self.assertEqual(details, [])


if __name__ == "__main__":
    unittest.main()

