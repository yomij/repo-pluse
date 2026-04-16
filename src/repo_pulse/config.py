from functools import lru_cache
from typing import Annotated, List, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    database_url: str = "sqlite:///./data/app.db"
    github_token: str = ""
    feishu_app_id: str
    feishu_app_secret: str
    feishu_chat_id: str
    feishu_about_doc_url: str = ""
    feishu_doc_folder_token: str = ""
    feishu_long_connection_enabled: bool = True
    feishu_allow_legacy_mention_commands: bool = True
    feishu_event_encrypt_key: str = ""
    feishu_event_verification_token: str = ""
    research_provider: str = "dashscope"
    openai_api_key: str = ""
    openai_model: str = "gpt-5"
    openai_reasoning_effort: str = "medium"
    dashscope_api_key: str = ""
    dashscope_model: str = "qwen-deep-research"
    dashscope_structurer_model: str = "qwen-plus"
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    dashscope_research_timeout_seconds: int = 600
    dashscope_structurer_timeout_seconds: int = 600
    dashscope_research_max_retries: int = 2
    dashscope_research_retry_backoff_seconds: int = 1
    research_readme_char_limit: int = 4000
    research_release_limit: int = 3
    research_commit_limit: int = 5
    digest_cron: str = "30 9 * * 1-5"
    daily_digest_cron: str = "30 9 * * 1-5"
    weekly_digest_cron: str = "30 9 * * 1"
    detail_cache_ttl_seconds: int = 86400
    daily_digest_cache_ttl_seconds: int = 7200
    weekly_digest_cache_ttl_seconds: int = 86400
    digest_top_k: int = 10
    pregen_top_n: int = 5
    manual_digest_default_top_k: int = 5
    manual_digest_max_top_k: int = 10
    topic_include: Annotated[List[str], NoDecode] = Field(default_factory=list)
    topic_exclude: Annotated[List[str], NoDecode] = Field(default_factory=list)

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    @field_validator("topic_include", "topic_exclude", mode="before")
    @classmethod
    def parse_csv_lists(cls, value: Union[str, List[str]]) -> List[str]:
        if isinstance(value, list):
            return value
        if not value:
            return []
        return [item.strip().lower() for item in value.split(",") if item.strip()]

    @field_validator("feishu_about_doc_url", mode="before")
    @classmethod
    def validate_feishu_about_doc_url(cls, value: str) -> str:
        return (value or "").strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
