import io
import json
import contextlib
import unittest
from unittest import mock


class TestOneShotCli(unittest.TestCase):
    def test_cli_env_flag_can_be_before_or_after_subcommand(self):
        from env_repair.cli import build_parser

        p = build_parser()

        a1 = p.parse_args(["--env", "passivebot", "one-shot", "-y"])
        self.assertEqual(a1.cmd, "one-shot")
        self.assertEqual(a1.env, ["passivebot"])
        self.assertIsNone(getattr(a1, "env_single", None))

        a2 = p.parse_args(["one-shot", "--env", "passivebot", "-y"])
        self.assertEqual(a2.cmd, "one-shot")
        self.assertEqual(a2.env, [])
        self.assertEqual(getattr(a2, "env_single", None), "passivebot")

    def test_one_shot_dispatch_and_json_report(self):
        import env_repair.cli as cli

        ok_payload = {"ok": True, "exit_code": 0, "report": [{"dummy": True}]}
        with (
            mock.patch.object(cli, "fix_inconsistent", return_value=ok_payload) as p_fix_inc,
            mock.patch.object(cli, "run", return_value=ok_payload) as p_run,
            mock.patch.object(cli, "verify_imports", return_value=ok_payload) as p_verify,
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            rc = cli.main(["one-shot", "--env", "base", "-y", "--json"])

        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue().strip())
        self.assertEqual(payload.get("env"), "base")
        self.assertIn("steps", payload)
        self.assertTrue(p_fix_inc.called)
        self.assertTrue(p_run.called)
        self.assertTrue(p_verify.called)

    def test_one_shot_plan_mode_skips_execution(self):
        import env_repair.cli as cli

        with (
            mock.patch.object(cli, "fix_inconsistent") as p_fix_inc,
            mock.patch.object(cli, "run") as p_run,
            mock.patch.object(cli, "verify_imports") as p_verify,
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            rc = cli.main(["one-shot", "--env", "base", "--plan", "--json"])

        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue().strip())
        self.assertTrue(payload.get("planned"))
        self.assertEqual(payload.get("env"), "base")
        self.assertFalse(p_fix_inc.called)
        self.assertFalse(p_run.called)
        self.assertFalse(p_verify.called)


if __name__ == "__main__":
    unittest.main()
