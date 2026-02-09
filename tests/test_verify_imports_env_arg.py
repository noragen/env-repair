import unittest


class TestVerifyImportsEnvArg(unittest.TestCase):
    def test_normalize_env_filters_none(self):
        from env_repair.verify_imports import _normalize_env_filters

        self.assertEqual(_normalize_env_filters(None), [])

    def test_normalize_env_filters_str(self):
        from env_repair.verify_imports import _normalize_env_filters

        self.assertEqual(_normalize_env_filters("passivebot"), ["passivebot"])
        self.assertEqual(_normalize_env_filters(""), [])

    def test_normalize_env_filters_list(self):
        from env_repair.verify_imports import _normalize_env_filters

        self.assertEqual(_normalize_env_filters(["passivebot"]), ["passivebot"])
        self.assertEqual(_normalize_env_filters(["", "base", None, 123]), ["base"])

    def test_cli_env_flag_can_be_before_or_after_subcommand(self):
        # Regression test for argparse dest collisions:
        # top-level `--env` (append list) must not be overwritten by verify-imports `--env`.
        from env_repair.cli import build_parser

        p = build_parser()

        a1 = p.parse_args(["--env", "passivebot", "verify-imports", "--full"])
        self.assertEqual(a1.cmd, "verify-imports")
        self.assertEqual(a1.env, ["passivebot"])
        self.assertIsNone(getattr(a1, "env_single", None))

        a2 = p.parse_args(["verify-imports", "--env", "passivebot", "--full"])
        self.assertEqual(a2.cmd, "verify-imports")
        self.assertEqual(a2.env, [])
        self.assertEqual(getattr(a2, "env_single", None), "passivebot")
