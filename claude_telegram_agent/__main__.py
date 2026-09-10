"""CLI entrypoint: claude-telegram-agent {pair|run|status|unpair}."""

from __future__ import annotations

import argparse
import asyncio
import sys

from . import config, daemon, pairing, projects


def _cmd_pair(args: argparse.Namespace) -> int:
    project_roots = [p.strip() for p in args.project_roots.split(",") if p.strip()]
    pairing.pair(args.code, args.broker, project_roots=project_roots, default_mode=args.mode)
    return 0


def _cmd_run(_args: argparse.Namespace) -> int:
    cfg = config.load_config()
    asyncio.run(daemon.run(cfg))
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    try:
        cfg = config.load_config()
    except FileNotFoundError as exc:
        print(exc)
        return 1
    print(f"config: {config.config_path()}")
    print(f"broker_url: {cfg.get('broker_url')}")
    print(f"laptop_id: {cfg.get('laptop_id')}")
    for project in projects.discover(cfg.get("project_roots", [])):
        print(f"  project: {project}")
    return 0


def _cmd_unpair(_args: argparse.Namespace) -> int:
    if config.delete_config():
        print(f"removed {config.config_path()}")
    else:
        print("not paired")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-telegram-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_pair = sub.add_parser("pair", help="pair this laptop with the broker")
    p_pair.add_argument("code", help="pairing code shown by /pair on the phone")
    p_pair.add_argument("--broker", required=True, help="broker base URL, e.g. https://broker.example.com")
    p_pair.add_argument(
        "--project-roots",
        default=".",
        help="comma-separated directories to expose as projects (default: cwd)",
    )
    p_pair.add_argument(
        "--mode",
        default="default",
        choices=["default", "plan", "acceptEdits", "bypassPermissions"],
        help="default permission mode for new sessions",
    )
    p_pair.set_defaults(func=_cmd_pair)

    p_run = sub.add_parser("run", help="connect to the broker and serve sessions")
    p_run.set_defaults(func=_cmd_run)

    p_status = sub.add_parser("status", help="show local config and discovered projects")
    p_status.set_defaults(func=_cmd_status)

    p_unpair = sub.add_parser("unpair", help="remove local pairing config")
    p_unpair.set_defaults(func=_cmd_unpair)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
