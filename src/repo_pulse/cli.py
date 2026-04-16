import argparse
from typing import List, Optional

from repo_pulse.feishu.chat_selector import run_select_chat_id_command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repo-pulse")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    run_digest_parser = subparsers.add_parser("run-digest")
    run_digest_parser.add_argument("--dry-run", action="store_true")

    select_chat_parser = subparsers.add_parser("select-chat-id")
    select_chat_parser.add_argument("--name", default="")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run-digest" and args.dry_run:
        print("dry-run: digest command parsed successfully.")
    if args.command == "select-chat-id":
        return run_select_chat_id_command(name_filter=args.name, env_path=".env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
