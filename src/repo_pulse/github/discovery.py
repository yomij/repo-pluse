import asyncio
from datetime import datetime, timedelta, timezone

from repo_pulse.github.client import GitHubClient
from repo_pulse.schemas import RepositoryCandidate

UTC = timezone.utc


class DiscoveryService:
    def __init__(self, client: GitHubClient, include_topics: list[str]):
        self.client = client
        self.include_topics = include_topics

    async def collect_candidates(self, now: datetime) -> list[RepositoryCandidate]:
        if not self.include_topics:
            return []

        requests = self._build_requests(now)
        results = await asyncio.gather(
            *(
                self.client.search_repositories(
                    query=request["query"],
                    per_page=request["per_page"],
                    sort=request["sort"],
                    order="desc",
                )
                for request in requests
            )
        )

        deduped: dict[str, RepositoryCandidate] = {}
        for request, candidates in zip(requests, results):
            for candidate in candidates:
                normalized = self._normalize_candidate(candidate)
                deduped[normalized.full_name] = self._merge_candidate(
                    existing=deduped.get(normalized.full_name),
                    candidate=normalized,
                    source=request["source"],
                )
        return list(deduped.values())

    def _build_requests(self, now: datetime) -> list[dict[str, str | int]]:
        current = self._ensure_utc(now)
        created_cutoff = (current - timedelta(days=30)).date().isoformat()
        pushed_cutoff = (current - timedelta(days=7)).date().isoformat()
        requests: list[dict[str, str | int]] = []
        for topic in self.include_topics:
            requests.extend(
                [
                    {
                        "source": "active_topic",
                        "query": f"topic:{topic} archived:false",
                        "per_page": 30,
                        "sort": "updated",
                    },
                    {
                        "source": "new_hot",
                        "query": f"topic:{topic} archived:false created:>={created_cutoff} stars:>=10",
                        "per_page": 30,
                        "sort": "stars",
                    },
                    {
                        "source": "established_mover",
                        "query": f"topic:{topic} archived:false pushed:>={pushed_cutoff} stars:>=50",
                        "per_page": 30,
                        "sort": "stars",
                    },
                ]
            )
        return requests

    @staticmethod
    def _normalize_candidate(candidate) -> RepositoryCandidate:
        if isinstance(candidate, RepositoryCandidate):
            return candidate
        if hasattr(candidate, "model_dump"):
            payload = candidate.model_dump()
        else:
            payload = {
                field_name: getattr(candidate, field_name)
                for field_name in RepositoryCandidate.model_fields
                if hasattr(candidate, field_name)
            }
        return RepositoryCandidate(**payload)

    @classmethod
    def _merge_candidate(
        cls,
        existing: RepositoryCandidate | None,
        candidate: RepositoryCandidate,
        source: str,
    ) -> RepositoryCandidate:
        if existing is None:
            return candidate.model_copy(update={"discovery_sources": [source]})

        discovery_sources = list(existing.discovery_sources)
        if source not in discovery_sources:
            discovery_sources.append(source)

        return existing.model_copy(
            update={
                "description": existing.description or candidate.description,
                "language": existing.language or candidate.language,
                "topics": cls._merge_topics(existing.topics, candidate.topics),
                "stars": max(existing.stars, candidate.stars),
                "forks": max(existing.forks, candidate.forks),
                "watchers": max(existing.watchers, candidate.watchers),
                "created_at": cls._earliest(existing.created_at, candidate.created_at),
                "pushed_at": cls._latest(existing.pushed_at, candidate.pushed_at),
                "discovery_sources": discovery_sources,
                "is_template": existing.is_template or candidate.is_template,
            }
        )

    @staticmethod
    def _merge_topics(left: list[str], right: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for topic in [*left, *right]:
            if topic in seen:
                continue
            merged.append(topic)
            seen.add(topic)
        return merged

    @staticmethod
    def _earliest(left: datetime | None, right: datetime | None) -> datetime | None:
        values = [value for value in (left, right) if value is not None]
        return min(values) if values else None

    @staticmethod
    def _latest(left: datetime | None, right: datetime | None) -> datetime | None:
        values = [value for value in (left, right) if value is not None]
        return max(values) if values else None

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
