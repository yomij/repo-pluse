import pytest

from repo_pulse.config import Settings


def test_env_example_includes_daily_stargazer_settings():
    env_example = open(".env.example", "r", encoding="utf-8").read()

    assert "DAILY_STARGAZER_VERIFY_ENABLED=" in env_example
    assert "DAILY_STARGAZER_CONCURRENCY=" in env_example
    assert "DAILY_STARGAZER_PAGE_SIZE=" in env_example
    assert "DAILY_STARGAZER_MAX_PAGES=" in env_example


def test_settings_parse_csv_lists_and_defaults(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_app_id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "cli_app_secret")
    monkeypatch.setenv("FEISHU_CHAT_IDS", "oc_group_a,oc_group_b")
    monkeypatch.setenv(
        "FEISHU_ABOUT_DOC_URL",
        "https://example.feishu.cn/docx/about-me",
    )
    monkeypatch.setenv("TOPIC_INCLUDE", "ai,llm,agents,devtools")
    monkeypatch.delenv("RESEARCH_PROVIDER", raising=False)

    settings = Settings(_env_file=None)

    assert settings.daily_digest_cron == "30 18 * * 1-5"
    assert settings.weekly_digest_cron == "30 18 * * 0"
    assert settings.scheduler_timezone == "Asia/Shanghai"
    assert settings.digest_top_k == 10
    assert settings.pregen_top_n == 5
    assert settings.manual_digest_default_top_k == 5
    assert settings.manual_digest_max_top_k == 10
    assert (
        settings.feishu_about_doc_url
        == "https://example.feishu.cn/docx/about-me"
    )
    assert settings.feishu_chat_ids == ["oc_group_a", "oc_group_b"]
    assert settings.feishu_doc_folder_token == ""
    assert settings.feishu_long_connection_enabled is True
    assert settings.feishu_group_require_bot_mention is True
    assert not hasattr(settings, "feishu_allow_legacy_mention_commands")
    assert settings.feishu_event_encrypt_key == ""
    assert settings.feishu_event_verification_token == ""
    assert settings.research_provider == "dashscope"
    assert settings.dashscope_api_key == ""
    assert settings.openai_api_key == ""
    assert settings.openai_base_url == ""
    assert settings.openai_model == "gpt-5"
    assert settings.openai_reasoning_effort == "medium"
    assert settings.dashscope_model == "qwen-deep-research"
    assert settings.dashscope_structurer_model == "qwen-plus"
    assert settings.dashscope_research_timeout_seconds == 600
    assert settings.dashscope_structurer_timeout_seconds == 600
    assert settings.dashscope_research_max_retries == 2
    assert settings.dashscope_research_retry_backoff_seconds == 1
    assert settings.dashscope_structurer_max_retries == 2
    assert settings.dashscope_structurer_retry_backoff_seconds == 1
    assert settings.research_readme_char_limit == 4000
    assert settings.research_release_limit == 3
    assert settings.research_commit_limit == 5
    assert settings.detail_cache_ttl_seconds == 86400
    assert settings.daily_digest_cache_ttl_seconds == 7200
    assert settings.weekly_digest_cache_ttl_seconds == 86400
    assert settings.daily_stargazer_verify_enabled is True
    assert settings.daily_stargazer_concurrency == 4
    assert settings.daily_stargazer_page_size == 100
    assert settings.daily_stargazer_max_pages == 20
    assert settings.topic_include == ["ai", "llm", "agents", "devtools"]


def test_settings_allow_missing_feishu_about_doc_url(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_app_id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "cli_app_secret")
    monkeypatch.delenv("FEISHU_ABOUT_DOC_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.feishu_about_doc_url == ""


def test_settings_allow_missing_feishu_chat_ids(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_app_id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "cli_app_secret")
    monkeypatch.delenv("FEISHU_CHAT_IDS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.feishu_chat_ids == []
    assert "feishu_chat_id" not in settings.model_dump()


def test_settings_can_disable_group_require_bot_mention_from_env(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_app_id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "cli_app_secret")
    monkeypatch.setenv("FEISHU_GROUP_REQUIRE_BOT_MENTION", "false")

    settings = Settings(_env_file=None)

    assert settings.feishu_group_require_bot_mention is False


def test_settings_scheduler_timezone_can_be_configured(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_app_id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "cli_app_secret")
    monkeypatch.setenv("SCHEDULER_TIMEZONE", "UTC")

    settings = Settings(_env_file=None)

    assert settings.scheduler_timezone == "UTC"


def test_settings_reject_invalid_scheduler_timezone(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_app_id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "cli_app_secret")
    monkeypatch.setenv("SCHEDULER_TIMEZONE", "Asia/Not-A-Real-City")

    with pytest.raises(ValueError):
        Settings(_env_file=None)
