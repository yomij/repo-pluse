import asyncio
import re
from typing import Any


_CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")


class PassthroughSummaryLocalizer:
    async def localize(self, text: str) -> str:
        return _normalize_text(text)


class DashScopeSummaryLocalizer:
    def __init__(self, generation_client, api_key: str, model: str = "qwen-plus"):
        self.generation_client = generation_client
        self.api_key = api_key
        self.model = model

    async def localize(self, text: str) -> str:
        normalized = _normalize_text(text)
        if not normalized or _contains_cjk(normalized):
            return normalized

        try:
            localized = await asyncio.to_thread(self._localize_sync, normalized)
        except Exception:
            return normalized
        return _normalize_text(localized) or normalized

    def _localize_sync(self, text: str) -> str:
        response = self.generation_client.call(
            model=self.model,
            api_key=self.api_key,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是 GitHub 项目日报助手。"
                        "请把用户给出的英文仓库简介翻译成自然、简洁的一句中文。"
                        "保留产品名、组织名、技术名词，不要扩写，不要加引号，不要分点。"
                    ),
                },
                {
                    "role": "user",
                    "content": "请翻译为一句中文：{0}".format(text),
                },
            ],
            result_format="message",
        )
        return _response_to_text(response)


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_PATTERN.search(text or ""))


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _response_to_text(response: Any) -> str:
    if isinstance(response, list) and response:
        return "".join(_response_to_text(item) for item in response)

    if isinstance(response, dict):
        output = response.get("output") or response
        if isinstance(output, dict):
            choices = output.get("choices")
            if isinstance(choices, list) and choices:
                message = choices[0].get("message") or {}
                return _content_to_text(message.get("content"))
            message = output.get("message") or {}
            return _content_to_text(message.get("content"))
        return ""

    if hasattr(response, "model_dump"):
        try:
            payload = response.model_dump()
        except Exception:
            return ""
        return _response_to_text(payload)

    output = getattr(response, "output", None)
    if output is not None:
        return _response_to_text(output)

    return ""


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_content_to_text(item) for item in content)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        nested = content.get("content")
        if nested is not None:
            return _content_to_text(nested)
    return ""
