from repo_pulse.cli import build_parser, main


def test_build_parser_supports_run_digest_dry_run():
    parser = build_parser()

    args = parser.parse_args(["run-digest", "--dry-run"])

    assert args.command == "run-digest"
    assert args.dry_run is True


def test_build_parser_supports_select_chat_id_name_filter():
    parser = build_parser()

    args = parser.parse_args(["select-chat-id", "--name", "repo"])

    assert args.command == "select-chat-id"
    assert args.name == "repo"


def test_main_run_digest_dry_run_returns_zero_and_prints_hint(capsys):
    exit_code = main(["run-digest", "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry-run" in captured.out.lower()


def test_main_select_chat_id_delegates_to_command(monkeypatch):
    captured = {}

    def _fake_command(*, name_filter: str, env_path: str = ".env"):
        captured["name_filter"] = name_filter
        captured["env_path"] = env_path
        return 0

    monkeypatch.setattr("repo_pulse.cli.run_select_chat_id_command", _fake_command)

    exit_code = main(["select-chat-id", "--name", "repo"])

    assert exit_code == 0
    assert captured == {"name_filter": "repo", "env_path": ".env"}
