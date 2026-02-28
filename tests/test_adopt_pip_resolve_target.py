import unittest

from env_repair.repair import _resolve_adopt_pip_target


class TestAdoptPipResolveTarget(unittest.TestCase):
    def test_resolves_dash_python_alias(self):
        resolved = _resolve_adopt_pip_target(
            pip_name="msgpack",
            available={"msgpack-python", "msgpack-numpy", "other"},
        )
        self.assertEqual(resolved, "msgpack-python")

    def test_prefers_exact_variant(self):
        resolved = _resolve_adopt_pip_target(
            pip_name="msgpack",
            available={"msgpack"},
        )
        self.assertEqual(resolved, "msgpack")


if __name__ == "__main__":
    unittest.main()
