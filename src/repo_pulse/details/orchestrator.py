import asyncio
import json
import logging
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone

from repo_pulse.feishu.docs import render_project_markdown
from repo_pulse.models import ProjectDetailCache
from repo_pulse.observability import log_research_event
from repo_pulse.research.base import ResearchRequest
from repo_pulse.research.evidence import RepositoryEvidence

logger = logging.getLogger(__name__)


class DetailOrchestrator:
    def __init__(
        self,
        detail_repository,
        research_provider,
        docs_client,
        evidence_builder=None,
        cache_ttl_seconds: int = 86400,
    ):
        self.detail_repository = detail_repository
        self.research_provider = research_provider
        self.docs_client = docs_client
        self.evidence_builder = evidence_builder
        self.cache_ttl_seconds = max(cache_ttl_seconds, 0)
        self._repo_locks: dict[str, asyncio.Lock] = {}

    async def generate(
        self, full_name: str, repo_url: str, research_run_id: str
    ) -> ProjectDetailCache:
        started_at = time.monotonic()

        try:
            async with self._lock_for(full_name):
                cached = await self._load_valid_cache(full_name)
                if cached:
                    log_research_event(
                        logger,
                        event="detail.cache.hit",
                        status="ok",
                        research_run_id=research_run_id,
                        repo_full_name=full_name,
                        repo_url=repo_url,
                        elapsed_ms=_elapsed_ms(started_at),
                        message="detail cache hit",
                    )
                    return cached

                existing = await asyncio.to_thread(self.detail_repository.get, full_name)
                log_research_event(
                    logger,
                    event="detail.cache.miss",
                    status="ok",
                    research_run_id=research_run_id,
                    repo_full_name=full_name,
                    repo_url=repo_url,
                    message="detail cache miss",
                )

                evidence = None
                if self.evidence_builder is not None:
                    try:
                        evidence = await self.evidence_builder.build(full_name)
                    except Exception as exc:
                        evidence = RepositoryEvidence(
                            full_name=full_name,
                            repo_url=repo_url,
                        )
                        log_research_event(
                            logger,
                            event="detail.evidence.fallback",
                            status="degraded",
                            research_run_id=research_run_id,
                            repo_full_name=full_name,
                            repo_url=repo_url,
                            exception_type=type(exc).__name__,
                            message="detail evidence fallback",
                        )

                research_result = await self.research_provider.research(
                    ResearchRequest(
                        full_name=full_name,
                        repo_url=repo_url,
                        research_run_id=research_run_id,
                        evidence=evidence,
                    )
                )
                summary_markdown = render_project_markdown(full_name, research_result)
                log_research_event(
                    logger,
                    event="detail.doc_sync.started",
                    status="started",
                    research_run_id=research_run_id,
                    repo_full_name=full_name,
                    repo_url=repo_url,
                    message="detail doc sync started",
                )
                if existing and existing.doc_url:
                    doc_url = await self.docs_client.upsert_project_doc(
                        full_name,
                        summary_markdown,
                        existing_doc_url=existing.doc_url,
                    )
                else:
                    doc_url = await self.docs_client.upsert_project_doc(
                        full_name,
                        summary_markdown,
                    )
                log_research_event(
                    logger,
                    event="detail.doc_sync.completed",
                    status="ok",
                    research_run_id=research_run_id,
                    repo_full_name=full_name,
                    repo_url=repo_url,
                    message="detail doc sync completed",
                )
                serialized_citations = _serialize_citations(
                    getattr(research_result, "citations", None)
                )
                citations_json = json.dumps(serialized_citations, ensure_ascii=False)

                detail = ProjectDetailCache(
                    full_name=full_name,
                    doc_url=doc_url,
                    summary_markdown=summary_markdown,
                    citations_json=citations_json,
                    updated_at=datetime.now(timezone.utc),
                )
                await asyncio.to_thread(self.detail_repository.upsert, detail)
                log_research_event(
                    logger,
                    event="detail.completed",
                    status="ok",
                    research_run_id=research_run_id,
                    repo_full_name=full_name,
                    repo_url=repo_url,
                    citations_count=len(serialized_citations),
                    best_practices_count=len(getattr(research_result, "best_practices", None) or []),
                    elapsed_ms=_elapsed_ms(started_at),
                    message="detail generation completed",
                )
                return detail
        except Exception as exc:
            log_research_event(
                logger,
                event="detail.failed",
                status="failed",
                research_run_id=research_run_id,
                repo_full_name=full_name,
                repo_url=repo_url,
                elapsed_ms=_elapsed_ms(started_at),
                exception_type=type(exc).__name__,
                message=str(exc),
            )
            raise

    async def _load_valid_cache(self, full_name: str):
        if self.cache_ttl_seconds <= 0:
            return None
        return await asyncio.to_thread(
            self.detail_repository.get_valid,
            full_name,
            datetime.now(timezone.utc),
            self.cache_ttl_seconds,
        )

    def _lock_for(self, full_name: str) -> asyncio.Lock:
        lock = self._repo_locks.get(full_name)
        if lock is None:
            lock = asyncio.Lock()
            self._repo_locks[full_name] = lock
        return lock


def _serialize_citations(citations):
    if not citations:
        return []

    serialized = []
    for item in citations:
        normalized = _normalize_citation(item)
        if normalized:
            serialized.append(normalized)
    return serialized


def _normalize_citation(citation):
    if is_dataclass(citation):
        payload = asdict(citation)
    elif isinstance(citation, dict):
        payload = citation
    else:
        title = getattr(citation, "title", None)
        url = getattr(citation, "url", None)
        snippet = getattr(citation, "snippet", None)
        payload = {"title": title, "url": url, "snippet": snippet}

    title = payload.get("title")
    url = payload.get("url")
    if not title or not url:
        return None
    return {"title": title, "url": url, "snippet": payload.get("snippet")}


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)
