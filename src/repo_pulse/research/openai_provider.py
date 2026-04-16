import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from repo_pulse.observability import log_research_event
from repo_pulse.research.base import (
    Citation,
    ResearchProvider,
    ResearchRequest,
    ResearchResult,
    parse_research_result_payload,
)
from repo_pulse.research.prompts import build_research_prompt

logger = logging.getLogger(__name__)


class OpenAIResearchProvider(ResearchProvider):
    def __init__(self, client, model: str = "gpt-5", reasoning_effort: str = "medium"):
        self.client = client
        self.model = model
        self.reasoning_effort = reasoning_effort

    async def research(self, request: ResearchRequest) -> ResearchResult:
        started_at = time.monotonic()
        current_stage = "openai_response"
        prompt = build_research_prompt(request)
        log_research_event(
            logger,
            event="research.started",
            status="started",
            research_run_id=request.research_run_id,
            repo_full_name=request.full_name,
            repo_url=request.repo_url,
            provider="openai",
            model=self.model,
            message="research provider started",
        )

        try:
            response = await self.client.responses.create(
                model=self.model,
                input=prompt,
                tools=[{"type": "web_search"}],
                include=["web_search_call.action.sources"],
                reasoning={"effort": self.reasoning_effort},
            )
            log_research_event(
                logger,
                event="research.progress",
                status="running",
                research_run_id=request.research_run_id,
                repo_full_name=request.full_name,
                repo_url=request.repo_url,
                provider="openai",
                model=self.model,
                stage="openai_response",
                elapsed_ms=_elapsed_ms(started_at),
                message="research provider progress",
            )

            current_stage = "payload_validation"
            payload = self._parse_payload(getattr(response, "output_text", ""))
            citations = self._build_citations(payload, response)
            result = parse_research_result_payload(
                payload,
                citations=citations,
                metadata=self._build_metadata(),
            )
            log_research_event(
                logger,
                event="research.progress",
                status="running",
                research_run_id=request.research_run_id,
                repo_full_name=request.full_name,
                repo_url=request.repo_url,
                provider="openai",
                model=self.model,
                stage="payload_validation",
                elapsed_ms=_elapsed_ms(started_at),
                message="research provider progress",
            )
            log_research_event(
                logger,
                event="research.completed",
                status="completed",
                research_run_id=request.research_run_id,
                repo_full_name=request.full_name,
                repo_url=request.repo_url,
                provider="openai",
                model=self.model,
                citations_count=len(citations),
                best_practices_count=len(result.best_practices),
                elapsed_ms=_elapsed_ms(started_at),
                message="research provider completed",
            )
            return result
        except Exception as exc:
            log_research_event(
                logger,
                event="research.failed",
                status="failed",
                research_run_id=request.research_run_id,
                repo_full_name=request.full_name,
                repo_url=request.repo_url,
                provider="openai",
                model=self.model,
                stage=current_stage,
                exception_type=type(exc).__name__,
                elapsed_ms=_elapsed_ms(started_at),
                message=str(exc),
            )
            raise

    def _build_citations(self, payload: Dict[str, Any], response: Any) -> List[Citation]:
        citation_payload = payload.get("citations")
        if not isinstance(citation_payload, list) or len(citation_payload) == 0:
            citation_payload = self._extract_sources(self._safe_output_items(response))

        citations = []
        seen_urls = set()
        for item in citation_payload:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not url:
                continue
            normalized_url = str(url)
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            citations.append(
                Citation(
                    title=str(item.get("title", "")),
                    url=normalized_url,
                    snippet=item.get("snippet"),
                )
            )
        return citations

    @staticmethod
    def _safe_output_items(response: Any) -> List[Dict[str, Any]]:
        if not hasattr(response, "model_dump"):
            return []
        try:
            dump = response.model_dump()
        except Exception:
            return []
        if not isinstance(dump, dict):
            return []
        output = dump.get("output")
        if not isinstance(output, list):
            return []
        return output

    @staticmethod
    def _parse_payload(output_text: str) -> Dict[str, Any]:
        if not output_text:
            raise ValueError("Research response output_text is empty")
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError:
            raise ValueError("Research response output_text is not valid JSON")
        if not isinstance(payload, dict):
            raise ValueError("Research response payload must be a JSON object")
        return payload

    @staticmethod
    def _extract_sources(output_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sources: List[Dict[str, Any]] = []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "web_search_call":
                action = item.get("action")
                if not isinstance(action, dict):
                    continue
                action_sources = action.get("sources")
                if not isinstance(action_sources, list):
                    continue
                for source in action_sources:
                    if isinstance(source, dict):
                        sources.append(source)
        return sources

    def _build_metadata(self) -> Dict[str, str]:
        return {
            "provider": "openai",
            "model": self.model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "batch_id": uuid4().hex,
        }


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)
