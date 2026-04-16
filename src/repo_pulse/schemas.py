from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl


class RepositoryCandidate(BaseModel):
    full_name: str
    name: str
    owner: str
    description: Optional[str] = None
    html_url: HttpUrl
    language: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    stars: int
    forks: int
    watchers: int
    created_at: Optional[datetime] = None
    pushed_at: Optional[datetime] = None
    discovery_sources: List[str] = Field(default_factory=list)
    is_template: bool = False


class RepositoryMetadata(BaseModel):
    full_name: str
    name: str
    owner: str
    description: Optional[str] = None
    html_url: HttpUrl
    homepage: Optional[HttpUrl] = None
    language: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    default_branch: str = "main"
    stars: int
    forks: int
    watchers: int
    pushed_at: Optional[datetime] = None
