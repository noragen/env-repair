import unittest

from env_repair.inconsistent import parse_inconsistent


class TestInconsistentParse(unittest.TestCase):
    def test_detects_inconsistent(self):
        txt = "Warning: The environment is inconsistent, please check.\n- numpy\n- scipy\n"
        ok, pkgs = parse_inconsistent(txt)
        self.assertTrue(ok)
        self.assertEqual(set(pkgs), {"numpy", "scipy"})

    def test_no_inconsistent(self):
        ok, pkgs = parse_inconsistent("All good")
        self.assertFalse(ok)
        self.assertEqual(pkgs, [])


if __name__ == "__main__":
    unittest.main()

