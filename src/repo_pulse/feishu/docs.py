import json
import re
import time
import asyncio
import logging
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote

import httpx
import lark_oapi as lark
import lark_oapi.api.docx.v1 as docx_v1
import lark_oapi.api.drive.v1 as drive_v1

from repo_pulse.research.base import (
    ResearchResult,
    TRIAL_VERDICT_CAN_RUN_LOCALLY,
    TRIAL_VERDICT_INSUFFICIENT_INFORMATION,
    TRIAL_VERDICT_NEEDS_API_KEY,
    TRIAL_VERDICT_NEEDS_CLOUD_RESOURCE,
    TRIAL_VERDICT_NEEDS_COMPLEX_SETUP,
    TRIAL_VERDICT_SOURCE_READING_ONLY,
)

_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_DOC_PERMISSION_TYPE = "docx"
_DEFAULT_LINK_SHARE_ENTITY = "tenant_editable"

logger = logging.getLogger(__name__)


class FeishuDocsClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        folder_token: str = "",
        base_url: str = "https://open.feishu.cn/open-apis",
        http_client: Optional[httpx.AsyncClient] = None,
        token_refresh_buffer_seconds: int = 60,
        oapi_client=None,
    ):
        self._app_id = app_id
        self._app_secret = app_secret
        self._folder_token = folder_token
        self._base_url = base_url.rstrip("/")
        self._token_refresh_buffer_seconds = token_refresh_buffer_seconds
        self._http_client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._owns_http_client = http_client is None
        self._oapi_client = oapi_client or _build_oapi_client(
            app_id=app_id,
            app_secret=app_secret,
            base_url=base_url,
        )
        self._tenant_access_token: Optional[str] = None
        self._tenant_access_token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()
        self._document_ids_by_full_name: Dict[str, str] = {}

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()

    async def upsert_project_doc(
        self,
        full_name: str,
        markdown: str,
        existing_doc_url: Optional[str] = None,
    ) -> str:
        document_id = self._document_ids_by_full_name.get(full_name)
        if document_id is None and existing_doc_url:
            document_id = _extract_document_id(existing_doc_url)
            if document_id:
                self._document_ids_by_full_name[full_name] = document_id

        if document_id is None:
            document_id = await self._create_document(title=_document_title(full_name), full_name=full_name)
            self._document_ids_by_full_name[full_name] = document_id
        else:
            await self._replace_document_children(document_id)

        await self._ensure_default_access(document_id)
        await self._append_markdown_blocks(document_id, markdown)
        return _build_doc_url(document_id)

    async def tenant_access_token(self, force_refresh: bool = False) -> str:
        now = time.time()
        if (
            not force_refresh
            and self._tenant_access_token
            and now < self._tenant_access_token_expires_at
        ):
            return self._tenant_access_token

        async with self._token_lock:
            now = time.time()
            if (
                not force_refresh
                and self._tenant_access_token
                and now < self._tenant_access_token_expires_at
            ):
                return self._tenant_access_token

            response = await self._http_client.post(
                f"{self._base_url}/auth/v3/tenant_access_token/internal/",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            response.raise_for_status()
            payload = response.json()
            self._raise_on_feishu_business_error(payload, "get doc tenant token")

            token = payload.get("tenant_access_token")
            if not token:
                raise RuntimeError("Feishu tenant access token missing in docs response")

            expire = int(payload.get("expire", 0) or 0)
            ttl = max(expire - self._token_refresh_buffer_seconds, 0)

            self._tenant_access_token = token
            self._tenant_access_token_expires_at = now + ttl
            return token

    async def _create_document(self, title: str, full_name: str) -> str:
        request_body = docx_v1.CreateDocumentRequestBody.builder().title(title)
        if self._folder_token:
            request_body = request_body.folder_token(self._folder_token)

        response = await self._oapi_client.docx.v1.document.acreate(
            docx_v1.CreateDocumentRequest.builder().request_body(
                request_body.build()
            ).build()
        )
        self._raise_on_feishu_response_error(response, "create docs document")
        document = getattr(getattr(response, "data", None), "document", None)
        document_id = getattr(document, "document_id", None)
        if not document_id:
            raise RuntimeError("Feishu docs create document missing document_id for {0}".format(full_name))
        return str(document_id)

    async def _replace_document_children(self, document_id: str) -> None:
        child_count = await self._count_children(document_id)
        if child_count == 0:
            return

        response = await self._oapi_client.docx.v1.document_block_children.abatch_delete(
            docx_v1.BatchDeleteDocumentBlockChildrenRequest.builder()
            .document_id(document_id)
            .block_id(document_id)
            .request_body(
                docx_v1.BatchDeleteDocumentBlockChildrenRequestBody.builder()
                .start_index(0)
                .end_index(child_count)
                .build()
            )
            .build()
        )
        self._raise_on_feishu_response_error(response, "delete docs children")

    async def _count_children(self, document_id: str) -> int:
        total = 0
        page_token = None
        while True:
            request_builder = (
                docx_v1.GetDocumentBlockChildrenRequest.builder()
                .document_id(document_id)
                .block_id(document_id)
                .page_size(500)
            )
            if page_token:
                request_builder = request_builder.page_token(page_token)

            response = await self._oapi_client.docx.v1.document_block_children.aget(
                request_builder.build()
            )
            self._raise_on_feishu_response_error(response, "get docs children")
            data = getattr(response, "data", None)
            items = getattr(data, "items", None) or []
            total += len(items)
            if not getattr(data, "has_more", False):
                return total
            page_token = getattr(data, "page_token", None)
            if not page_token:
                return total

    async def _append_markdown_blocks(self, document_id: str, markdown: str) -> None:
        blocks = [docx_v1.Block(_markdown_line_to_block(line)) for line in _markdown_lines(markdown)]
        if not blocks:
            return

        response = await self._oapi_client.docx.v1.document_block_children.acreate(
            docx_v1.CreateDocumentBlockChildrenRequest.builder()
            .document_id(document_id)
            .block_id(document_id)
            .request_body(
                docx_v1.CreateDocumentBlockChildrenRequestBody.builder()
                .children(blocks)
                .index(0)
                .build()
            )
            .build()
        )
        self._raise_on_feishu_response_error(response, "create docs children")

    async def _ensure_default_access(self, document_id: str) -> None:
        request = (
            drive_v1.PatchPermissionPublicRequest.builder()
            .type(_DOC_PERMISSION_TYPE)
            .token(document_id)
            .request_body(
                drive_v1.PermissionPublicRequest.builder()
                .link_share_entity(_DEFAULT_LINK_SHARE_ENTITY)
                .build()
            )
            .build()
        )

        try:
            response = await self._oapi_client.drive.v1.permission_public.apatch(request)
            self._raise_on_feishu_response_error(response, "patch docs public permission")
        except Exception as exc:
            logger.warning(
                "Failed to set default edit permission for Feishu doc %s: %s",
                document_id,
                exc,
            )

    async def _authorized_headers(self) -> Dict[str, str]:
        token = await self.tenant_access_token()
        return {"Authorization": "Bearer {0}".format(token)}

    @staticmethod
    def _raise_on_feishu_business_error(payload: Dict[str, object], operation: str) -> None:
        if payload.get("code") == 0:
            return
        code = payload.get("code")
        msg = payload.get("msg") or payload.get("message") or "unknown error"
        raise RuntimeError("Feishu API failed to {0}: code={1}, msg={2}".format(operation, code, msg))

    @staticmethod
    def _raise_on_feishu_response_error(response: Any, operation: str) -> None:
        if response.success():
            return
        code = getattr(response, "code", None)
        msg = getattr(response, "msg", None) or "unknown error"
        raise RuntimeError("Feishu API failed to {0}: code={1}, msg={2}".format(operation, code, msg))


def _build_doc_url(document_id: str) -> str:
    return "https://feishu.cn/docx/{0}".format(document_id)


def _extract_document_id(doc_url: str) -> Optional[str]:
    normalized = (doc_url or "").rstrip("/")
    marker = "/docx/"
    if marker not in normalized:
        return None
    return normalized.split(marker, 1)[1] or None


def _document_title(full_name: str) -> str:
    return "{0} 项目详情".format(full_name)


def _markdown_lines(markdown: str) -> List[str]:
    return [line.strip() for line in markdown.splitlines() if line.strip()]


def _markdown_line_to_block(line: str) -> Dict[str, object]:
    if line.startswith("# "):
        return _build_block(3, "heading1", line[2:].strip())
    if line.startswith("## "):
        return _build_block(4, "heading2", line[3:].strip())
    if line.startswith("- "):
        return _build_block(12, "bullet", line[2:].strip())
    return _build_block(2, "text", line)


def _build_block(block_type: int, block_key: str, text: str) -> Dict[str, object]:
    return {
        "block_type": block_type,
        block_key: {
            "elements": _build_text_elements(text),
        },
    }


def _build_text_elements(text: str) -> List[Dict[str, object]]:
    text = _normalize_inline_markdown(text)
    elements: List[Dict[str, object]] = []
    last_index = 0
    for match in _MARKDOWN_LINK_PATTERN.finditer(text):
        if match.start() > last_index:
            elements.append(_text_run(text[last_index : match.start()]))
        elements.append(
            _text_run(
                match.group(1),
                link_url=quote(match.group(2), safe=""),
            )
        )
        last_index = match.end()

    if last_index < len(text):
        elements.append(_text_run(text[last_index:]))

    return [item for item in elements if item["text_run"]["content"]]


def _text_run(content: str, link_url: Optional[str] = None) -> Dict[str, object]:
    text_run = {"content": content}
    if link_url:
        text_run["text_element_style"] = {"link": {"url": link_url}}
    return {"text_run": text_run}


def _normalize_inline_markdown(text: str) -> str:
    return text.replace("**", "").replace("`", "")


def _build_oapi_client(app_id: str, app_secret: str, base_url: str):
    domain = base_url.rstrip("/")
    if domain.endswith("/open-apis"):
        domain = domain[: -len("/open-apis")]

    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .domain(domain)
        .build()
    )


def _extract_citation_title_url(citation):
    if isinstance(citation, dict):
        title = citation.get("title")
        url = citation.get("url")
    else:
        title = getattr(citation, "title", None)
        url = getattr(citation, "url", None)
    if not title or not url:
        return None
    return title, url


def extract_markdown_section(markdown: str, heading: str) -> str:
    if not markdown:
        return ""

    lines = markdown.splitlines()
    target_heading = "## {0}".format(heading)
    collected: List[str] = []
    capturing = False
    for raw_line in lines:
        line = raw_line.rstrip()
        if line == target_heading:
            capturing = True
            continue
        if capturing and line.startswith("## "):
            break
        if capturing and line.strip():
            collected.append(line.strip())
    return "\n".join(collected).strip()


def render_project_markdown(full_name: str, result: ResearchResult) -> str:
    practice_lines = _render_string_list(
        result.best_practices,
        placeholder="- 暂无补充",
    )
    risk_lines = _render_string_list(
        result.risks,
        placeholder="- 暂无明显补充风险",
    )
    citation_lines = _render_citation_list(result.citations)
    metadata_lines = _render_metadata_list(result.metadata)

    return (
        "# {0} 项目详情\n\n"
        "## 项目简介\n"
        "{1}\n\n"
        "## 为什么最近火\n"
        "{2}\n\n"
        "## 是否适合我\n"
        "适合：{3}\n"
        "不适合：{4}\n\n"
        "## 是否能快速试玩\n"
        "{5}\n\n"
        "## 最短体验路径\n"
        "{6}\n\n"
        "## 前置条件与外部依赖\n"
        "{7}\n\n"
        "## 常见阻塞与失败信号\n"
        "{8}\n\n"
        "## 最佳实践\n"
        "{9}\n\n"
        "## 局限与风险\n"
        "{10}\n\n"
        "## 参考资料与引用链接\n"
        "{11}\n\n"
        "## 生成元数据\n"
        "{12}\n"
    ).format(
        full_name,
        result.what_it_is,
        result.why_now,
        result.fit_for or "信息不足以确认",
        result.not_for or "信息不足以确认",
        _render_trial_verdict(result),
        _render_quickstart_steps(
            result.quickstart_steps,
            trial_verdict=result.trial_verdict,
        ),
        _render_onboarding_facts(
            result.trial_requirements,
            placeholder="- 暂无明确前置条件或外部依赖",
            trial_verdict=result.trial_verdict,
            fallback_kind="requirements",
        ),
        _render_blockers_with_success_signal(
            trial_verdict=result.trial_verdict,
            success_signal=result.success_signal,
            blockers=result.common_blockers,
        ),
        practice_lines,
        risk_lines,
        citation_lines,
        metadata_lines,
    )


def _render_trial_verdict(result: ResearchResult) -> str:
    verdict = _trial_verdict_label(result.trial_verdict)
    trial_time_estimate = (result.trial_time_estimate or "").strip()
    if trial_time_estimate:
        return "结论：{0}（预计耗时：{1}）".format(verdict, trial_time_estimate)
    return "结论：{0}".format(verdict)


def _trial_verdict_label(trial_verdict: str) -> str:
    labels = {
        TRIAL_VERDICT_CAN_RUN_LOCALLY: "可以快速本地试玩",
        TRIAL_VERDICT_NEEDS_API_KEY: "需要 API Key 才能完成试玩",
        TRIAL_VERDICT_NEEDS_CLOUD_RESOURCE: "依赖云资源，无法纯本地快速试玩",
        TRIAL_VERDICT_NEEDS_COMPLEX_SETUP: "可以本地运行，但前置搭建较重",
        TRIAL_VERDICT_SOURCE_READING_ONLY: "当前更适合先阅读文档或源码",
        TRIAL_VERDICT_INSUFFICIENT_INFORMATION: "信息不足以确认是否能快速试玩",
    }
    return labels.get(trial_verdict, "信息不足以确认是否能快速试玩")


def _render_quickstart_steps(
    steps: Sequence[object],
    *,
    trial_verdict: str = "",
) -> str:
    lines = []
    for step in steps or []:
        label = _read_field(step, "label")
        action = _read_field(step, "action")
        expected_result = _read_field(step, "expected_result")
        source = _read_field(step, "source")
        if not all((label, action, expected_result, source)):
            continue
        lines.append(
            "{0}. **{1}**：{2}（预期：{3}；来源：{4}）".format(
                len(lines) + 1,
                label,
                action,
                expected_result,
                source,
            )
        )
    if lines:
        return "\n".join(lines)
    fallback = _trial_verdict_quickstart_fallback(trial_verdict)
    if fallback:
        return fallback
    return "1. 信息不足以确认最短体验路径（来源：信息不足以确认）"


def _render_onboarding_facts(
    facts: Sequence[object],
    *,
    placeholder: str,
    trial_verdict: str = "",
    fallback_kind: str = "",
) -> str:
    lines = []
    for item in facts or []:
        label = _read_field(item, "label")
        detail = _read_field(item, "detail")
        source = _read_field(item, "source")
        if not all((label, detail, source)):
            continue
        lines.append("- **{0}**：{1}（来源：{2}）".format(label, detail, source))
    if lines:
        return "\n".join(lines)
    fallback = _trial_verdict_fact_fallback(trial_verdict, fallback_kind)
    if fallback:
        return fallback
    return placeholder


def _render_blockers_with_success_signal(
    *,
    trial_verdict: str,
    success_signal: str,
    blockers: Sequence[object],
) -> str:
    lines = ["- 成功信号：{0}".format((success_signal or "").strip() or "信息不足以确认")]
    blocker_lines = []
    for item in blockers or []:
        label = _read_field(item, "label")
        detail = _read_field(item, "detail")
        source = _read_field(item, "source")
        if not all((label, detail, source)):
            continue
        blocker_lines.append(
            "- 阻塞：**{0}**：{1}（来源：{2}）".format(label, detail, source)
        )
    if blocker_lines:
        lines.extend(blocker_lines)
    else:
        lines.append(
            _trial_verdict_fact_fallback(trial_verdict, "blockers")
            or "- 暂未识别常见阻塞项"
        )
    return "\n".join(lines)


def _render_string_list(items: Sequence[str], *, placeholder: str) -> str:
    lines = ["- {0}".format(item) for item in (items or []) if item]
    if lines:
        return "\n".join(lines)
    return placeholder


def _render_citation_list(citations: Sequence[object]) -> str:
    lines = []
    for item in citations or []:
        pair = _extract_citation_title_url(item)
        if pair:
            lines.append("- [{0}]({1})".format(pair[0], pair[1]))
    if lines:
        return "\n".join(lines)
    return "- 暂无公开参考资料"


def _render_metadata_list(metadata: dict[str, str]) -> str:
    lines = []
    for key, value in (metadata or {}).items():
        if key and value:
            lines.append("- {0}: {1}".format(key, value))
    if lines:
        return "\n".join(lines)
    return "- 暂无元数据"


def _read_field(item: object, field_name: str) -> str:
    if isinstance(item, dict):
        value = item.get(field_name, "")
    else:
        value = getattr(item, field_name, "")
    if isinstance(value, str):
        return value.strip()
    return ""


def _trial_verdict_quickstart_fallback(trial_verdict: str) -> str:
    source = "基于 trial_verdict 推断"
    fallbacks = {
        TRIAL_VERDICT_NEEDS_API_KEY: (
            "1. **准备 API Key / 账号凭证**：先按官方文档申请并配置所需凭证，"
            "具体命令信息不足以确认（预期：凭证可用于后续试玩；来源：{0}）"
        ),
        TRIAL_VERDICT_NEEDS_CLOUD_RESOURCE: (
            "1. **准备云资源或托管服务**：先接通试玩依赖的外部云资源，"
            "具体接入步骤信息不足以确认（预期：外部资源可用于后续试玩；来源：{0}）"
        ),
        TRIAL_VERDICT_NEEDS_COMPLEX_SETUP: (
            "1. **完成较重的本地前置搭建**：先准备多服务或重型运行环境，"
            "具体步骤信息不足以确认（预期：本地环境可支撑首次试玩；来源：{0}）"
        ),
        TRIAL_VERDICT_SOURCE_READING_ONLY: (
            "1. **先阅读官方文档或源码**：当前没有明确的可执行最短路径，"
            "建议先确认体验入口（预期：明确下一步体验方式；来源：{0}）"
        ),
    }
    template = fallbacks.get(trial_verdict)
    if template is None:
        return ""
    return template.format(source)


def _trial_verdict_fact_fallback(trial_verdict: str, fallback_kind: str) -> str:
    source = "基于 trial_verdict 推断"
    fallbacks = {
        "requirements": {
            TRIAL_VERDICT_NEEDS_API_KEY: (
                "- **API Key / 凭证**：试玩前需要准备对应的 API Key、Token 或账号凭证；"
                "具体配置步骤信息不足以确认（来源：{0}）"
            ),
            TRIAL_VERDICT_NEEDS_CLOUD_RESOURCE: (
                "- **云资源 / 托管服务**：试玩依赖外部云资源或托管服务；"
                "具体资源类型信息不足以确认（来源：{0}）"
            ),
            TRIAL_VERDICT_NEEDS_COMPLEX_SETUP: (
                "- **重型本地环境**：试玩前需要完成较重的本地环境或多服务搭建；"
                "具体步骤信息不足以确认（来源：{0}）"
            ),
            TRIAL_VERDICT_SOURCE_READING_ONLY: (
                "- **官方文档 / 源码**：当前更适合先阅读文档或源码来确认体验入口"
                "（来源：{0}）"
            ),
        },
        "blockers": {
            TRIAL_VERDICT_NEEDS_API_KEY: (
                "- 阻塞：**缺少 API Key / 凭证**：未准备所需凭证前无法完成试玩。"
                "（来源：{0}）"
            ),
            TRIAL_VERDICT_NEEDS_CLOUD_RESOURCE: (
                "- 阻塞：**缺少云资源**：未准备外部云资源或托管服务前无法完成试玩。"
                "（来源：{0}）"
            ),
            TRIAL_VERDICT_NEEDS_COMPLEX_SETUP: (
                "- 阻塞：**前置搭建较重**：需要较重的本地环境或多服务搭建，"
                "难以快速试玩。（来源：{0}）"
            ),
            TRIAL_VERDICT_SOURCE_READING_ONLY: (
                "- 阻塞：**缺少明确 runnable path**：当前材料更适合先阅读文档或源码，"
                "暂无明确可执行体验入口。（来源：{0}）"
            ),
        },
    }
    template = fallbacks.get(fallback_kind, {}).get(trial_verdict)
    if template is None:
        return ""
    return template.format(source)
