import unittest

import env_repair as ed


class TestCaseConflicts(unittest.TestCase):
    def test_same_version_case_conflict_detected(self):
        entries = [
            {"name": "Automat", "version": "25.4.16", "channel": "pypi"},
            {"name": "automat", "version": "25.4.16", "channel": "conda-forge"},
            {"name": "Bottleneck", "version": "1.6.0", "channel": "pypi"},
            {"name": "bottleneck", "version": "1.6.0", "channel": "conda-forge"},
        ]
        pip_items, conda_force = ed.find_same_version_case_conflicts(entries)
        self.assertEqual({i["name"] for i in pip_items}, {"Automat", "Bottleneck"})
        self.assertEqual(set(conda_force), {"automat", "bottleneck"})

    def test_different_versions_not_selected(self):
        entries = [
            {"name": "Automat", "version": "25.4.16", "channel": "pypi"},
            {"name": "automat", "version": "25.4.15", "channel": "conda-forge"},
        ]
        pip_items, conda_force = ed.find_same_version_case_conflicts(entries)
        self.assertEqual(pip_items, [])
        self.assertEqual(conda_force, [])


if __name__ == "__main__":
    unittest.main()

