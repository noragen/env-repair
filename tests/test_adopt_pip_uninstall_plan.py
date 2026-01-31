import unittest


class TestAdoptPipUninstallPlan(unittest.TestCase):
    def test_msgpack_alias_uninstalls_when_versions_match(self):
        from env_repair.repair import _adopt_pip_uninstall_plan

        uninstallable, skipped = _adopt_pip_uninstall_plan(
            pip_to_conda={"msgpack": "msgpack-python"},
            pip_versions={"msgpack": "1.1.2"},
            entries=[{"name": "msgpack-python", "version": "1.1.2", "channel": "conda-forge"}],
        )
        self.assertEqual(uninstallable, ["msgpack"])
        self.assertEqual(skipped, [])

    def test_msgpack_alias_keeps_when_versions_differ(self):
        from env_repair.repair import _adopt_pip_uninstall_plan

        uninstallable, skipped = _adopt_pip_uninstall_plan(
            pip_to_conda={"msgpack": "msgpack-python"},
            pip_versions={"msgpack": "1.1.2"},
            entries=[{"name": "msgpack-python", "version": "1.1.1", "channel": "conda-forge"}],
        )
        self.assertEqual(uninstallable, [])
        self.assertEqual(skipped[0]["pip"], "msgpack")


if __name__ == "__main__":
    unittest.main()

