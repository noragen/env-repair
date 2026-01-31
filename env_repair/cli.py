import argparse
import json
import os
import sys

from .doctor import (
    cache_check,
    cache_fix,
    diagnose_clobber,
    diagnose_inconsistent,
    diagnose_ssl,
    fix_inconsistent,
    rebuild,
    rollback,
    run,
)
from .verify_imports import verify_imports
from .subprocess_utils import OperationInterrupted
from .i18n import t


def build_parser():
    lang = "auto"
    p = argparse.ArgumentParser(
        prog="env-repair",
        description=t("help_desc", lang=lang),
        add_help=False,
    )
    p.add_argument("-h", "--help", action="help", help=t("help_help", lang=lang))
    sub = p.add_subparsers(dest="cmd")

    rb = sub.add_parser(
        "rollback",
        help=t("help_cmd_rollback", lang=lang),
        description=t("help_cmd_rollback", lang=lang),
        add_help=False,
    )
    rb.add_argument("-h", "--help", action="help", help=t("help_help", lang=lang))
    rb.add_argument("--env", required=True, help=t("help_env_single", lang=lang))
    rb.add_argument("--to", default="prev", help=t("help_to", lang=lang))
    rb.add_argument("--dry-run", action="store_true", help=t("help_dry_run", lang=lang))
    rb.add_argument("-y", "--yes", action="store_true", help=t("help_yes", lang=lang))
    rb.add_argument("--json", action="store_true", help=t("help_json", lang=lang))
    rb.add_argument("--debug", action="store_true", help=t("help_debug", lang=lang))
    rb.add_argument("--plan", action="store_true", help=t("help_plan", lang=lang))

    rb2 = sub.add_parser(
        "rebuild",
        help=t("help_cmd_rebuild", lang=lang),
        description=t("help_cmd_rebuild", lang=lang),
        add_help=False,
    )
    rb2.add_argument("-h", "--help", action="help", help=t("help_help", lang=lang))
    rb2.add_argument("--env", required=True, help=t("help_env_single", lang=lang))
    rb2.add_argument("--to", required=True, help=t("help_to_rebuild", lang=lang))
    rb2.add_argument("--verify", action="store_true", help=t("help_verify", lang=lang))
    rb2.add_argument("--plan", action="store_true", help=t("help_plan", lang=lang))
    rb2.add_argument("-y", "--yes", action="store_true", help=t("help_yes", lang=lang))
    rb2.add_argument("--json", action="store_true", help=t("help_json", lang=lang))
    rb2.add_argument("--debug", action="store_true", help=t("help_debug", lang=lang))

    dc = sub.add_parser(
        "diagnose-clobber",
        help=t("help_cmd_diagnose_clobber", lang=lang),
        description=t("help_cmd_diagnose_clobber", lang=lang),
        add_help=False,
    )
    dc.add_argument("-h", "--help", action="help", help=t("help_help", lang=lang))
    dc.add_argument("--env", required=True, help=t("help_env_single", lang=lang))
    dc.add_argument("--logfile", required=True, help=t("help_logfile", lang=lang))
    dc.add_argument("--json", action="store_true", help=t("help_json", lang=lang))
    dc.add_argument("--debug", action="store_true", help=t("help_debug", lang=lang))

    di = sub.add_parser(
        "diagnose-inconsistent",
        help=t("help_cmd_diagnose_inconsistent", lang=lang),
        description=t("help_cmd_diagnose_inconsistent", lang=lang),
        add_help=False,
    )
    di.add_argument("-h", "--help", action="help", help=t("help_help", lang=lang))
    di.add_argument("--env", required=True, help=t("help_env_single", lang=lang))
    di.add_argument("--json", action="store_true", help=t("help_json", lang=lang))
    di.add_argument("--debug", action="store_true", help=t("help_debug", lang=lang))

    fi = sub.add_parser(
        "fix-inconsistent",
        help=t("help_cmd_fix_inconsistent", lang=lang),
        description=t("help_cmd_fix_inconsistent", lang=lang),
        add_help=False,
    )
    fi.add_argument("-h", "--help", action="help", help=t("help_help", lang=lang))
    fi.add_argument("--env", required=True, help=t("help_env_single", lang=lang))
    fi.add_argument("--level", choices=["safe", "normal", "rebuild"], default="safe", help=t("help_level_inconsistent", lang=lang))
    fi.add_argument("--plan", action="store_true", help=t("help_plan", lang=lang))
    fi.add_argument("-y", "--yes", action="store_true", help=t("help_yes", lang=lang))
    fi.add_argument("--json", action="store_true", help=t("help_json", lang=lang))
    fi.add_argument("--debug", action="store_true", help=t("help_debug", lang=lang))

    cc = sub.add_parser(
        "cache-check",
        help=t("help_cmd_cache_check", lang=lang),
        description=t("help_cmd_cache_check", lang=lang),
        add_help=False,
    )
    cc.add_argument("-h", "--help", action="help", help=t("help_help", lang=lang))
    cc.add_argument("--json", action="store_true", help=t("help_json", lang=lang))
    cc.add_argument("--debug", action="store_true", help=t("help_debug", lang=lang))

    cf = sub.add_parser(
        "cache-fix",
        help=t("help_cmd_cache_fix", lang=lang),
        description=t("help_cmd_cache_fix", lang=lang),
        add_help=False,
    )
    cf.add_argument("-h", "--help", action="help", help=t("help_help", lang=lang))
    cf.add_argument("--level", choices=["safe", "targeted", "aggressive"], default="safe", help=t("help_level_cache", lang=lang))
    cf.add_argument("--plan", action="store_true", help=t("help_plan", lang=lang))
    cf.add_argument("-y", "--yes", action="store_true", help=t("help_yes", lang=lang))
    cf.add_argument("--json", action="store_true", help=t("help_json", lang=lang))
    cf.add_argument("--debug", action="store_true", help=t("help_debug", lang=lang))

    ds = sub.add_parser(
        "diagnose-ssl",
        help=t("help_cmd_diagnose_ssl", lang=lang),
        description=t("help_cmd_diagnose_ssl", lang=lang),
        add_help=False,
    )
    ds.add_argument("-h", "--help", action="help", help=t("help_help", lang=lang))
    ds.add_argument("--env", help=t("help_env_single", lang=lang))
    ds.add_argument("--base", action="store_true", help=t("help_base", lang=lang))
    ds.add_argument("--json", action="store_true", help=t("help_json", lang=lang))
    ds.add_argument("--debug", action="store_true", help=t("help_debug", lang=lang))

    vi = sub.add_parser(
        "verify-imports",
        help="Check if installed packages can be imported",
        description="Verify that packages are importable (finds missing DLLs, etc.)",
        add_help=False,
    )
    vi.add_argument("-h", "--help", action="help", help=t("help_help", lang=lang))
    vi.add_argument("--env", help=t("help_env_single", lang=lang))
    vi.add_argument("--full", action="store_true", help="Check all packages (default: critical only)")
    vi.add_argument("--json", action="store_true", help=t("help_json", lang=lang))
    vi.add_argument("--debug", action="store_true", help=t("help_debug", lang=lang))
    vi.add_argument("--fix", action="store_true", help="Attempt to automatically fix broken imports")

    p.add_argument(
        "--env",
        action="append",
        default=[],
        help=t("help_env_multi", lang=lang),
    )
    p.add_argument("--fix", action="store_true", help=t("help_fix", lang=lang))
    p.add_argument("--adopt-pip", action="store_true", help=t("help_adopt_pip", lang=lang))
    p.add_argument(
        "--keep-pip",
        action="store_true",
        help=t("help_keep_pip", lang=lang),
    )
    p.add_argument(
        "--prefer",
        choices=["auto", "conda", "pip"],
        default="auto",
        help=t("help_prefer", lang=lang),
    )
    p.add_argument("--pip-fallback", action="store_true", help=t("help_pip_fallback", lang=lang))
    p.add_argument("--no-pip-fallback", action="store_true", help=t("help_no_pip_fallback", lang=lang))
    p.add_argument("--channel", action="append", default=[], help=t("help_channel", lang=lang))
    p.add_argument("--no-channels-from-condarc", action="store_true", help=t("help_no_channels_from_condarc", lang=lang))
    p.add_argument("--no-default-channels", action="store_true", help=t("help_no_default_channels", lang=lang))
    p.add_argument("--ignore-pinned", action="store_true", help=t("help_ignore_pinned", lang=lang))
    p.add_argument("--force-reinstall", action="store_true", help=t("help_force_reinstall", lang=lang))
    p.add_argument("--snapshot", help=t("help_snapshot", lang=lang))
    p.add_argument("--json", action="store_true", help=t("help_json", lang=lang))
    p.add_argument("--debug", action="store_true", help=t("help_debug", lang=lang))
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "debug", False):
        # Make subprocess executions print conda/mamba command lines for transparency.
        os.environ["ENV_REPAIR_SHOW_CMDS"] = "1"
    try:
        if args.cmd == "rollback":
            result = rollback(args)
        elif args.cmd == "rebuild":
            result = rebuild(args)
        elif args.cmd == "diagnose-clobber":
            result = diagnose_clobber(args)
        elif args.cmd == "diagnose-inconsistent":
            result = diagnose_inconsistent(args)
        elif args.cmd == "fix-inconsistent":
            result = fix_inconsistent(args)
        elif args.cmd == "cache-check":
            result = cache_check(args)
        elif args.cmd == "cache-fix":
            result = cache_fix(args)
        elif args.cmd == "diagnose-ssl":
            result = diagnose_ssl(args)
        elif args.cmd == "verify-imports":
            result = verify_imports(args)
        else:
            result = run(args)
    except (KeyboardInterrupt, OperationInterrupted):
        print(t("interrupted", lang="auto"), file=sys.stderr)
        return 130
    if getattr(args, "json", False):
        print(json.dumps(result.get("report"), indent=2))
    return int(result.get("exit_code", 0 if result.get("ok") else 1))


if __name__ == "__main__":
    raise SystemExit(main())
