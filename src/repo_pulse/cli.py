import argparse
from typing import List, Optional


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repo-pulse")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    run_digest_parser = subparsers.add_parser("run-digest")
    run_digest_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run-digest" and args.dry_run:
        print("dry-run: digest command parsed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
