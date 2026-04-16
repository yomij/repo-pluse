from repo_pulse.cli import build_parser, main


def test_build_parser_supports_run_digest_dry_run():
    parser = build_parser()

    args = parser.parse_args(["run-digest", "--dry-run"])

    assert args.command == "run-digest"
    assert args.dry_run is True


def test_main_run_digest_dry_run_returns_zero_and_prints_hint(capsys):
    exit_code = main(["run-digest", "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry-run" in captured.out.lower()
