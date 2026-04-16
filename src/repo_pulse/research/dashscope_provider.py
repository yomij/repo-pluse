import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import requests

from repo_pulse.observability import log_research_event
from repo_pulse.research.base import (
    Citation,
    ResearchProvider,
    ResearchRequest,
    ResearchResult,
    parse_research_result_payload,
)

logger = logging.getLogger(__name__)


class _ResearchReportStreamError(requests.exceptions.RequestException):
    def __init__(
        self,
        original_exception: requests.exceptions.RequestException,
        *,
        partial_chars: int = 0,
        partial_references: int = 0,
        chunk_count: int = 0,
    ):
        super().__init__(str(original_exception))
        self.original_exception = original_exception
        self.partial_chars = partial_chars
        self.partial_references = partial_references
        self.chunk_count = chunk_count


class DashScopeDeepResearchProvider(ResearchProvider):
    def __init__(
        self,
        research_client,
        structurer_client,
        api_key: str,
        research_model: str = "qwen-deep-research",
        structurer_model: str = "qwen-plus",
        research_timeout_seconds: int = 300,
        structurer_timeout_seconds: int = 60,
        research_max_retries: int = 2,
        research_retry_backoff_seconds: int = 1,
    ):
        self.research_client = research_client
        self.structurer_client = structurer_client
        self.api_key = api_key
        self.research_model = research_model
        self.structurer_model = structurer_model
        self.research_timeout_seconds = research_timeout_seconds
        self.structurer_timeout_seconds = structurer_timeout_seconds
        self.research_max_retries = max(research_max_retries, 0)
        self.research_retry_backoff_seconds = max(research_retry_backoff_seconds, 0)

    async def research(self, request: ResearchRequest) -> ResearchResult:
        started_at = time.perf_counter()

        def _elapsed_ms() -> int:
            return int((time.perf_counter() - started_at) * 1000)

        log_research_event(
            logger,
            event="research.started",
            status="started",
            research_run_id=request.research_run_id,
            repo_full_name=request.full_name,
            provider="dashscope",
            model=self.research_model,
            repo_url=request.repo_url,
            message="research provider started",
        )
        report_messages = [{"role": "user", "content": _build_report_prompt(request)}]
        report_started_at = time.perf_counter()
        logger.info(
            "DashScope research report turn started: repo=%s model=%s timeout=%ss",
            request.full_name,
            self.research_model,
            self.research_timeout_seconds,
        )
        try:
            report_text, references = await self._collect_research_turn_with_retry(
                request,
                report_messages,
                output_format="model_summary_report",
                elapsed_ms_getter=_elapsed_ms,
            )
        except requests.exceptions.RequestException as exc:
            logger.exception(
                "DashScope research report turn failed: repo=%s",
                request.full_name,
            )
            log_research_event(
                logger,
                event="research.failed",
                status="failed",
                research_run_id=request.research_run_id,
                repo_full_name=request.full_name,
                provider="dashscope",
                model=self.research_model,
                repo_url=request.repo_url,
                stage="report_turn",
                exception_type=_request_exception_type(exc),
                root_exception_type=_root_exception_type(exc),
                attempt=getattr(exc, "attempt", 1),
                max_attempts=getattr(exc, "max_attempts", self.research_max_retries + 1),
                partial_chars=getattr(exc, "partial_chars", 0),
                partial_references=getattr(exc, "partial_references", 0),
                chunk_count=getattr(exc, "chunk_count", 0),
                elapsed_ms=_elapsed_ms(),
                message=str(exc),
            )
            raise RuntimeError("DashScope 深度调研请求超时或网络异常（研究报告阶段），请稍后重试。") from exc
        except Exception as exc:
            logger.exception(
                "DashScope research report turn failed: repo=%s",
                request.full_name,
            )
            log_research_event(
                logger,
                event="research.failed",
                status="failed",
                research_run_id=request.research_run_id,
                repo_full_name=request.full_name,
                provider="dashscope",
                model=self.research_model,
                repo_url=request.repo_url,
                stage="report_turn",
                exception_type=_request_exception_type(exc),
                root_exception_type=_root_exception_type(exc),
                attempt=getattr(exc, "attempt", 1),
                max_attempts=getattr(exc, "max_attempts", self.research_max_retries + 1),
                partial_chars=getattr(exc, "partial_chars", 0),
                partial_references=getattr(exc, "partial_references", 0),
                chunk_count=getattr(exc, "chunk_count", 0),
                elapsed_ms=_elapsed_ms(),
                message=str(exc),
            )
            raise
        logger.info(
            "DashScope research report turn finished: repo=%s elapsed=%.2fs chars=%s references=%s",
            request.full_name,
            time.perf_counter() - report_started_at,
            len(report_text or ""),
            len(references),
        )
        log_research_event(
            logger,
            event="research.progress",
            status="running",
            research_run_id=request.research_run_id,
            repo_full_name=request.full_name,
            provider="dashscope",
            model=self.research_model,
            repo_url=request.repo_url,
            stage="report_turn",
            elapsed_ms=int((time.perf_counter() - report_started_at) * 1000),
            message="research provider progress",
        )

        structure_started_at = time.perf_counter()
        logger.info(
            "DashScope structurer started: repo=%s model=%s timeout=%ss",
            request.full_name,
            self.structurer_model,
            self.structurer_timeout_seconds,
        )
        try:
            payload = await self._structure_report(request, report_text, references)
            citations = self._build_citations(payload, references)
            result = parse_research_result_payload(
                payload,
                citations=citations,
                metadata=self._build_metadata(),
            )
        except requests.exceptions.RequestException as exc:
            logger.exception(
                "DashScope structurer failed: repo=%s",
                request.full_name,
            )
            log_research_event(
                logger,
                event="research.failed",
                status="failed",
                research_run_id=request.research_run_id,
                repo_full_name=request.full_name,
                provider="dashscope",
                model=self.structurer_model,
                repo_url=request.repo_url,
                stage="structure_report",
                exception_type=type(exc).__name__,
                elapsed_ms=_elapsed_ms(),
                message=str(exc),
            )
            raise RuntimeError("DashScope 结构化整理请求超时或网络异常（结构化阶段），请稍后重试。") from exc
        except Exception as exc:
            logger.exception(
                "DashScope structurer failed: repo=%s",
                request.full_name,
            )
            log_research_event(
                logger,
                event="research.failed",
                status="failed",
                research_run_id=request.research_run_id,
                repo_full_name=request.full_name,
                provider="dashscope",
                model=self.structurer_model,
                repo_url=request.repo_url,
                stage="structure_report",
                exception_type=type(exc).__name__,
                elapsed_ms=_elapsed_ms(),
                message=str(exc),
            )
            raise
        logger.info(
            "DashScope structurer finished: repo=%s elapsed=%.2fs citations=%s practices=%s",
            request.full_name,
            time.perf_counter() - structure_started_at,
            len(result.citations),
            len(result.best_practices),
        )
        log_research_event(
            logger,
            event="research.progress",
            status="running",
            research_run_id=request.research_run_id,
            repo_full_name=request.full_name,
            provider="dashscope",
            model=self.research_model,
            repo_url=request.repo_url,
            stage="structure_report",
            elapsed_ms=int((time.perf_counter() - structure_started_at) * 1000),
            message="research provider progress",
        )
        log_research_event(
            logger,
            event="research.completed",
            status="completed",
            research_run_id=request.research_run_id,
            repo_full_name=request.full_name,
            provider="dashscope",
            model=self.research_model,
            repo_url=request.repo_url,
            citations_count=len(result.citations),
            best_practices_count=len(result.best_practices),
            elapsed_ms=_elapsed_ms(),
            message="research provider completed",
        )
        return result

    async def _collect_research_turn(
        self,
        messages: List[Dict[str, str]],
        output_format: Optional[str] = None,
    ) -> tuple[str, List[Dict[str, str]]]:
        return await asyncio.to_thread(
            self._collect_research_turn_sync,
            messages,
            output_format,
        )

    async def _collect_research_turn_with_retry(
        self,
        request: ResearchRequest,
        messages: List[Dict[str, str]],
        output_format: Optional[str],
        elapsed_ms_getter,
    ) -> tuple[str, List[Dict[str, str]]]:
        max_attempts = self.research_max_retries + 1
        for attempt in range(1, max_attempts + 1):
            try:
                return await self._collect_research_turn(
                    messages,
                    output_format=output_format,
                )
            except requests.exceptions.RequestException as exc:
                setattr(exc, "attempt", attempt)
                setattr(exc, "max_attempts", max_attempts)
                retryable = _is_retryable_report_exception(exc)
                will_retry = retryable and attempt < max_attempts
                if will_retry:
                    log_research_event(
                        logger,
                        event="research.retry",
                        status="retrying",
                        research_run_id=request.research_run_id,
                        repo_full_name=request.full_name,
                        provider="dashscope",
                        model=self.research_model,
                        repo_url=request.repo_url,
                        stage="report_turn",
                        exception_type=_request_exception_type(exc),
                        root_exception_type=_root_exception_type(exc),
                        attempt=attempt,
                        max_attempts=max_attempts,
                        partial_chars=getattr(exc, "partial_chars", 0),
                        partial_references=getattr(exc, "partial_references", 0),
                        chunk_count=getattr(exc, "chunk_count", 0),
                        elapsed_ms=elapsed_ms_getter(),
                        message=str(exc),
                    )
                    await asyncio.sleep(self._retry_delay_seconds(attempt))
                    continue
                raise
        raise RuntimeError("unreachable")

    def _retry_delay_seconds(self, attempt: int) -> int:
        return self.research_retry_backoff_seconds * attempt

    def _collect_research_turn_sync(
        self,
        messages: List[Dict[str, str]],
        output_format: Optional[str] = None,
    ) -> tuple[str, List[Dict[str, str]]]:
        call_kwargs: Dict[str, Any] = {
            "model": self.research_model,
            "api_key": self.api_key,
            "messages": messages,
            "stream": True,
            "request_timeout": self.research_timeout_seconds,
        }
        if output_format:
            call_kwargs["output_format"] = output_format

        text_parts: List[str] = []
        references: List[Dict[str, str]] = []
        partial_chars = 0
        chunk_count = 0
        try:
            responses = self.research_client.call(**call_kwargs)
            for response in responses:
                chunk_count += 1
                output = _response_output(response)
                message = output.get("message") or {}
                content = _message_content_to_text(message.get("content"))
                if content:
                    text_parts.append(content)
                    partial_chars += len(content)

                extra = message.get("extra") or {}
                deep_research = extra.get("deep_research") or {}
                references.extend(_normalize_references(deep_research.get("references")))
        except requests.exceptions.RequestException as exc:
            raise _ResearchReportStreamError(
                exc,
                partial_chars=partial_chars,
                partial_references=len(_dedupe_references(references)),
                chunk_count=chunk_count,
            ) from exc

        return "".join(text_parts), _dedupe_references(references)

    async def _structure_report(
        self,
        request: ResearchRequest,
        report_text: str,
        references: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        response = await asyncio.to_thread(
            self.structurer_client.call,
            model=self.structurer_model,
            api_key=self.api_key,
            messages=[
                {"role": "system", "content": _build_structurer_system_prompt()},
                {
                    "role": "user",
                    "content": _build_structurer_user_prompt(request, report_text, references),
                },
            ],
            result_format="message",
            response_format={"type": "json_object"},
            request_timeout=self.structurer_timeout_seconds,
        )
        content = _structured_response_content(response)
        return self._parse_payload(content)

    def _build_citations(
        self, payload: Dict[str, Any], fallback_references: List[Dict[str, str]]
    ) -> List[Citation]:
        raw_citations = payload.get("citations")
        if not isinstance(raw_citations, list) or len(raw_citations) == 0:
            raw_citations = fallback_references

        citations = []
        for item in raw_citations:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not url:
                continue
            citations.append(
                Citation(
                    title=str(item.get("title", "")),
                    url=str(url),
                    snippet=item.get("snippet") or item.get("description"),
                )
            )
        return citations

    @staticmethod
    def _parse_payload(output_text: str) -> Dict[str, Any]:
        if not output_text:
            raise ValueError("DashScope structured output is empty")
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError:
            raise ValueError("DashScope structured output is not valid JSON")
        if not isinstance(payload, dict):
            raise ValueError("DashScope structured output must be a JSON object")
        return payload

    def _build_metadata(self) -> Dict[str, str]:
        return {
            "provider": "dashscope",
            "model": self.research_model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "batch_id": uuid4().hex,
        }


def _response_output(response: Any) -> Dict[str, Any]:
    if isinstance(response, dict):
        return response.get("output") or response
    output = getattr(response, "output", None)
    if isinstance(output, dict):
        return output
    if hasattr(response, "model_dump"):
        try:
            payload = response.model_dump()
        except Exception:
            return {}
        if isinstance(payload, dict):
            return payload.get("output") or {}
    return {}


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_message_content_to_text(item) for item in content)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        if isinstance(content.get("content"), str):
            return content["content"]
    return ""


def _normalize_references(raw_references: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_references, list):
        return []
    normalized = []
    for item in raw_references:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        title = item.get("title")
        if not url or not title:
            continue
        normalized.append(
            {
                "title": str(title),
                "url": str(url),
                "description": str(item.get("description", "")) if item.get("description") else "",
            }
        )
    return normalized


def _dedupe_references(references: List[Dict[str, str]]) -> List[Dict[str, str]]:
    deduped: Dict[str, Dict[str, str]] = {}
    for item in references:
        deduped[item["url"]] = item
    return list(deduped.values())


def _structured_response_content(response: Any) -> str:
    output = _response_output(response)
    choices = output.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") or {}
        return _message_content_to_text(message.get("content"))
    message = output.get("message") or {}
    return _message_content_to_text(message.get("content"))


def _is_retryable_report_exception(exc: requests.exceptions.RequestException) -> bool:
    candidate = getattr(exc, "original_exception", exc)
    return isinstance(
        candidate,
        (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ),
    )


def _request_exception_type(exc: requests.exceptions.RequestException) -> str:
    candidate = getattr(exc, "original_exception", exc)
    return type(candidate).__name__


def _root_exception_type(exc: Exception) -> str:
    candidate = getattr(exc, "original_exception", exc)
    context = getattr(candidate, "__context__", None)
    if isinstance(context, BaseException):
        return type(context).__name__
    args = getattr(candidate, "args", ())
    if args and isinstance(args[0], BaseException):
        return type(args[0]).__name__
    return type(candidate).__name__


def _build_structurer_system_prompt() -> str:
    return (
        "你是面向中文工程团队的技术研究整理助手。"
        "你必须输出严格 JSON，不允许输出额外解释文本。"
    )


def _build_structurer_user_prompt(
    request: ResearchRequest,
    report_text: str,
    references: List[Dict[str, str]],
) -> str:
    schema_text = (
        "{\n"
        '  "what_it_is": "字符串",\n'
        '  "why_now": "字符串",\n'
        '  "fit_for": "字符串",\n'
        '  "not_for": "字符串",\n'
        '  "trial_verdict": "can_run_locally | needs_api_key | needs_cloud_resource | needs_complex_setup | source_reading_only | insufficient_information",\n'
        '  "trial_requirements": [\n'
        "    {\n"
        '      "label": "字符串",\n'
        '      "detail": "字符串",\n'
        '      "source": "字符串"\n'
        "    }\n"
        "  ],\n"
        '  "trial_time_estimate": "字符串",\n'
        '  "quickstart_steps": [\n'
        "    {\n"
        '      "label": "字符串",\n'
        '      "action": "字符串",\n'
        '      "expected_result": "字符串",\n'
        '      "source": "字符串"\n'
        "    }\n"
        "  ],\n"
        '  "success_signal": "字符串",\n'
        '  "common_blockers": [\n'
        "    {\n"
        '      "label": "字符串",\n'
        '      "detail": "字符串",\n'
        '      "source": "字符串"\n'
        "    }\n"
        "  ],\n"
        '  "best_practices": ["字符串"],\n'
        '  "risks": ["字符串"],\n'
        '  "citations": [\n'
        '    {"title": "字符串", "url": "字符串", "snippet": "字符串，可选"}\n'
        "  ],\n"
        '  "metadata": {\n'
        '    "provider": "字符串",\n'
        '    "model": "字符串",\n'
        '    "generated_at": "ISO8601 字符串",\n'
        '    "batch_id": "字符串"\n'
        "  }\n"
        "}"
    )
    return (
        "请把下面的研究报告整理为严格 JSON，结构固定为：\n"
        + schema_text
        + "\n要求：\n"
        + "- what_it_is / why_now / fit_for / not_for 必须是非空字符串；\n"
        + "- best_practices / risks 必须是数组；\n"
        + "- citations 尽量引用官方来源，且保留 URL；\n"
        + "- quickstart 已移除，必须改用 quickstart_steps；不要输出 quickstart 字段。\n"
        + "- trial_requirements 与 common_blockers 必须是包含 label、detail、source 的对象数组。\n"
        + "- quickstart_steps 必须是包含 label、action、expected_result、source 的对象数组，并且必须给出最短且现实可行的首次运行路径。\n"
        + "- 仅在仓库材料或权威公开来源明确支持时才能写出具体命令；优先参考官方文档与仓库作者提供的示例。\n"
        + "- 不能确认命令时，必须明确写“信息不足以确认”，不要编造命令、脚本或运行步骤。\n"
        + "- 阻塞项必须放入 common_blockers，不要埋在 risks；risks 仅用于更广义的工程风险。\n"
        + "- trial_verdict、quickstart_steps、success_signal 三者必须相互一致，不得互相矛盾。\n"
        + "- 如果无法确认某个字段，请明确写出“信息不足以确认”；\n"
        + "- 仓库：{0}\n".format(request.full_name)
        + "- 链接：{0}\n\n".format(request.repo_url)
        + "{0}\n\n".format(
            request.evidence.to_prompt_block() if request.evidence else "仓库一手证据：信息不足以确认"
        )
        + "研究报告：\n{0}\n\n".format(report_text)
        + "候选参考资料：\n{0}".format(json.dumps(references, ensure_ascii=False))
    )


def _build_report_prompt(request: ResearchRequest) -> str:
    evidence_block = (
        request.evidence.to_prompt_block() if request.evidence else "仓库一手证据：信息不足以确认"
    )
    return (
        "你是面向中文工程团队的技术研究助手，允许保留英文术语（如 API、SDK、release notes）。\n\n"
        "仓库：{0}\n".format(request.full_name)
        + "仓库链接：{0}\n".format(request.repo_url)
        + "{0}\n\n".format(evidence_block)
        + "要求：\n"
        + "1) 不要向用户提问澄清问题，直接完成调研并给出最终研究报告。\n"
        + "2) 重点回答这个仓库能否快速试玩、最短体验路径是什么、成功信号是什么、会先卡在哪里。\n"
        + "3) 可使用公开网络资料，但优先使用 evidence 中的一手资料；当 evidence 与公开网络资料冲突时，必须明确标注冲突点与不确定项，不要静默合并。\n"
        + "4) 如果命令无法从一手资料确认，必须明确写出“信息不足以确认”，不要编造命令。\n"
        + "5) 优先使用官方仓库、官方文档、README、examples、setup 文件里的证据。\n"
        + "6) citations 优先官方来源（仓库 / docs / blog / release notes），社区来源仅作补充并明确标注。\n"
        + "7) 输出可读性好的研究报告，使用清晰小节与要点，不要输出 JSON。\n"
        + "8) 内容强调工程落地，结论适配中文工程团队阅读习惯。\n"
        + "9) 如果某一项无法确认，请明确写出“信息不足以确认”。"
    )
