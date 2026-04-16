import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Sequence
from uuid import uuid4

from repo_pulse.models import DigestResultCache, RepositorySnapshot


@dataclass(frozen=True)
class DigestRequest:
    kind: str
    title: str
    window: str
    window_hours: int
    top_k: int


@dataclass
class DigestEntry:
    full_name: str
    category: str
    summary: str
    reason: str
    repo_url: str
    detail_action_value: str
    doc_url: Optional[str] = None
    reason_lines: list[str] = field(default_factory=list)


@dataclass
class DailyDigest:
    title: str
    window: str
    entries: list[DigestEntry]
    generated_at: Optional[str] = None


class DigestPipeline:
    def __init__(
        self,
        discovery_service,
        snapshot_repository,
        detail_repository,
        ranking_service,
        message_builder,
        feishu_client,
        detail_orchestrator,
        top_k: int,
        summary_localizer=None,
        topic_exclude: Optional[Sequence[str]] = None,
        digest_cache_repository=None,
        max_cached_entries: Optional[int] = None,
        cache_ttl_by_kind: Optional[dict[str, int]] = None,
    ):
        self.discovery_service = discovery_service
        self.snapshot_repository = snapshot_repository
        self.detail_repository = detail_repository
        self.digest_cache_repository = digest_cache_repository
        self.ranking_service = ranking_service
        self.message_builder = message_builder
        self.summary_localizer = summary_localizer
        self.feishu_client = feishu_client
        self.detail_orchestrator = detail_orchestrator
        self.top_k = max(top_k, 0)
        self.max_cached_entries = max(max_cached_entries if max_cached_entries is not None else top_k, 0)
        self.cache_ttl_by_kind = dict(cache_ttl_by_kind or {})
        self.topic_exclude = {item.lower() for item in (topic_exclude or [])}
        self._last_repo_urls: dict[str, str] = {}
        self._last_digest_from_cache = False
        self._digest_locks: dict[str, asyncio.Lock] = {}

    async def run_digest(
        self,
        digest_request: DigestRequest,
        now: datetime,
        receive_id: Optional[str] = None,
        pre_generate_top_n: int = 0,
    ) -> Sequence[str]:
        async with self._lock_for_kind(digest_request.kind):
            self._last_digest_from_cache = False
            cached_digest = await self._load_cached_digest(digest_request.kind, now, digest_request.top_k)
            if cached_digest is not None:
                self._last_digest_from_cache = True
                self._last_repo_urls = {
                    entry.full_name: entry.repo_url
                    for entry in cached_digest.entries
                }
                post = self.message_builder.build_digest_post(cached_digest)
                await self.feishu_client.send_post(
                    post.title,
                    post.markdown,
                    receive_id=receive_id,
                )
                return [entry.full_name for entry in cached_digest.entries]

            try:
                digest = await self._build_digest(
                    digest_request=digest_request,
                    now=now,
                    pre_generate_top_n=pre_generate_top_n,
                )
            except Exception:
                stale_digest = await self._load_latest_digest(
                    digest_request.kind,
                    digest_request.top_k,
                )
                if stale_digest is None:
                    raise
                self._last_digest_from_cache = True
                self._last_repo_urls = {
                    entry.full_name: entry.repo_url
                    for entry in stale_digest.entries
                }
                post = self.message_builder.build_digest_post(stale_digest)
                await self.feishu_client.send_post(
                    post.title,
                    post.markdown,
                    receive_id=receive_id,
                )
                return [entry.full_name for entry in stale_digest.entries]

        post = self.message_builder.build_digest_post(digest)
        await self.feishu_client.send_post(
            post.title,
            post.markdown,
            receive_id=receive_id,
        )
        return [entry.full_name for entry in digest.entries]

    async def pre_generate_details(self, ranked_repos: Sequence[str]) -> None:
        if self.detail_orchestrator is None or self._last_digest_from_cache:
            return

        for full_name in ranked_repos:
            repo_url = self._last_repo_urls.get(full_name) or self._repo_url_for(full_name)
            await self.detail_orchestrator.generate(full_name, repo_url, uuid4().hex)

    async def _build_digest(
        self,
        digest_request: DigestRequest,
        now: datetime,
        pre_generate_top_n: int,
    ) -> DailyDigest:
        candidates = await self.discovery_service.collect_candidates(now)
        cutoff_24h, cutoff_7d, baseline_cutoffs = self._baseline_cutoffs(digest_request, now)
        ranked_entries = []
        self._last_repo_urls = {}

        for candidate in candidates:
            baselines = await asyncio.to_thread(
                self.snapshot_repository.latest_before_many,
                candidate.full_name,
                baseline_cutoffs,
            )
            await asyncio.to_thread(
                self.snapshot_repository.save,
                RepositorySnapshot(
                    full_name=candidate.full_name,
                    captured_at=now,
                    stars=candidate.stars,
                    forks=candidate.forks,
                    watchers=candidate.watchers,
                    language=candidate.language,
                    pushed_at=candidate.pushed_at,
                    topics_csv=",".join(candidate.topics),
                ),
            )
            if self._is_excluded(candidate):
                continue

            scored = self.ranking_service.score(
                kind=digest_request.kind,
                candidate=candidate,
                baseline_24h=baselines.get(cutoff_24h),
                baseline_7d=baselines.get(cutoff_7d) if cutoff_7d is not None else None,
                now=now,
            )
            cached_detail = await asyncio.to_thread(
                self.detail_repository.get, candidate.full_name
            )
            repo_url = str(candidate.html_url)
            self._last_repo_urls[candidate.full_name] = repo_url
            ranked_entries.append(
                (
                    scored.score,
                    DigestEntry(
                        full_name=candidate.full_name,
                        category="/".join(scored.categories),
                        summary=candidate.description or "暂无项目描述",
                        reason=scored.reason,
                        reason_lines=list(scored.reason_lines),
                        repo_url=repo_url,
                        detail_action_value=repo_url,
                        doc_url=cached_detail.doc_url if cached_detail else None,
                    ),
                )
            )

        ranked_entries.sort(key=lambda item: item[0], reverse=True)
        cache_entries = [entry for _, entry in ranked_entries[: self.max_cached_entries]]
        await self._localize_summaries(cache_entries)
        await self._hydrate_doc_urls(cache_entries, pre_generate_top_n)
        selected_entries = self._clone_entries(cache_entries[: max(digest_request.top_k, 0)])
        digest = DailyDigest(
            title=digest_request.title,
            window=digest_request.window,
            entries=selected_entries,
            generated_at=now.isoformat(),
        )
        await self._store_cached_digest(
            digest_request=digest_request,
            generated_at=now,
            entries=cache_entries,
        )
        return digest

    def _is_excluded(self, candidate) -> bool:
        candidate_topics = {topic.lower() for topic in candidate.topics}
        return bool(candidate_topics.intersection(self.topic_exclude))

    @staticmethod
    def _repo_url_for(full_name: str) -> str:
        return "https://github.com/{0}".format(full_name)

    async def _localize_summaries(self, entries: Sequence[DigestEntry]) -> None:
        if self.summary_localizer is None:
            return

        for entry in entries:
            try:
                localized = await self.summary_localizer.localize(entry.summary)
            except Exception:
                continue
            if localized:
                entry.summary = localized

    async def _load_cached_digest(
        self,
        kind: str,
        now: datetime,
        requested_top_k: int,
    ) -> Optional[DailyDigest]:
        if self.digest_cache_repository is None:
            return None
        ttl_seconds = self.cache_ttl_by_kind.get(kind, 0)
        if ttl_seconds <= 0:
            return None

        cache = await asyncio.to_thread(self.digest_cache_repository.get_valid, kind, now)
        if cache is None:
            return None

        try:
            payload = json.loads(cache.digest_json)
        except json.JSONDecodeError:
            return None

        entries_payload = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries_payload, list):
            return None
        if requested_top_k > len(entries_payload):
            return None

        entries = self._parse_entries(entries_payload, requested_top_k)
        if entries is None:
            return None
        return DailyDigest(
            title=payload.get("title") or "",
            window=payload.get("window") or "",
            entries=entries,
            generated_at=payload.get("generated_at"),
        )

    async def _load_latest_digest(
        self,
        kind: str,
        requested_top_k: int,
    ) -> Optional[DailyDigest]:
        if self.digest_cache_repository is None:
            return None
        cache = await asyncio.to_thread(self.digest_cache_repository.get_latest, kind)
        if cache is None:
            return None
        try:
            payload = json.loads(cache.digest_json)
        except json.JSONDecodeError:
            return None

        entries_payload = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries_payload, list):
            return None
        if requested_top_k > len(entries_payload):
            return None

        entries = self._parse_entries(entries_payload, requested_top_k)
        if entries is None:
            return None
        return DailyDigest(
            title=payload.get("title") or "",
            window=payload.get("window") or "",
            entries=entries,
            generated_at=payload.get("generated_at"),
        )

    async def _store_cached_digest(
        self,
        digest_request: DigestRequest,
        generated_at: datetime,
        entries: Sequence[DigestEntry],
    ) -> None:
        if self.digest_cache_repository is None:
            return
        ttl_seconds = self.cache_ttl_by_kind.get(digest_request.kind, 0)
        if ttl_seconds <= 0:
            return

        digest = DailyDigest(
            title=digest_request.title,
            window=digest_request.window,
            entries=self._clone_entries(entries),
            generated_at=generated_at.isoformat(),
        )
        cache = DigestResultCache(
            kind=digest_request.kind,
            digest_json=json.dumps(
                {
                    "title": digest.title,
                    "window": digest.window,
                    "entries": [asdict(entry) for entry in digest.entries],
                    "generated_at": digest.generated_at,
                },
                ensure_ascii=False,
            ),
            generated_at=generated_at,
            expires_at=generated_at + timedelta(seconds=ttl_seconds),
        )
        await asyncio.to_thread(self.digest_cache_repository.upsert, cache)

    @staticmethod
    def _clone_entries(entries: Sequence[DigestEntry]) -> list[DigestEntry]:
        return [
            DigestEntry(
                full_name=entry.full_name,
                category=entry.category,
                summary=entry.summary,
                reason=entry.reason,
                repo_url=entry.repo_url,
                detail_action_value=entry.detail_action_value,
                doc_url=entry.doc_url,
                reason_lines=list(entry.reason_lines),
            )
            for entry in entries
        ]

    async def _hydrate_doc_urls(
        self,
        entries: Sequence[DigestEntry],
        pre_generate_top_n: int,
    ) -> None:
        if self.detail_orchestrator is None or pre_generate_top_n <= 0:
            return

        for entry in entries[:pre_generate_top_n]:
            detail = await self.detail_orchestrator.generate(
                entry.full_name,
                entry.repo_url,
                uuid4().hex,
            )
            entry.doc_url = detail.doc_url

    def _lock_for_kind(self, kind: str) -> asyncio.Lock:
        lock = self._digest_locks.get(kind)
        if lock is None:
            lock = asyncio.Lock()
            self._digest_locks[kind] = lock
        return lock

    @staticmethod
    def _parse_entries(
        entries_payload: list[object],
        requested_top_k: int,
    ) -> Optional[list[DigestEntry]]:
        try:
            return [
                DigestEntry(**entry)
                for entry in entries_payload[: max(requested_top_k, 0)]
            ]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _baseline_cutoffs(
        digest_request: DigestRequest,
        now: datetime,
    ) -> tuple[datetime, Optional[datetime], list[datetime]]:
        cutoff_24h = now - timedelta(hours=24)
        if digest_request.kind == "weekly":
            cutoff_7d = now - timedelta(days=7)
            return cutoff_24h, cutoff_7d, [cutoff_7d, cutoff_24h]
        return cutoff_24h, None, [cutoff_24h]
