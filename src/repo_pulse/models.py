from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel

UTC = timezone.utc


class RepositorySnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    full_name: str = Field(index=True)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    stars: int
    forks: int
    watchers: int
    language: Optional[str] = None
    pushed_at: Optional[datetime] = None
    topics_csv: str = ""


class ProjectDetailCache(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    full_name: str = Field(index=True, unique=True)
    doc_url: str
    summary_markdown: str
    citations_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)


class DigestResultCache(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(index=True, unique=True)
    digest_json: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    expires_at: datetime = Field(index=True)
