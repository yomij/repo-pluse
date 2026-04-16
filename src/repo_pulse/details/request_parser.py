import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional, Sequence

_GITHUB_REPO_PATTERN = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_SLASH_COMMAND_PATTERN = re.compile(r"^\s*/([A-Za-z]+)(?:\s+(.*?))?\s*$")
_LEADING_MENTION_TAG_PATTERN = re.compile(
    r"^\s*(<at\b[^>]*>(.*?)</at>)",
    re.IGNORECASE,
)
_MENTION_TAG_ID_PATTERN = re.compile(
    r"\b(?:user_id|open_id|union_id)=([\"'])(.*?)\1",
    re.IGNORECASE,
)

_ANALYZE_ALIASES = {"a", "analyze"}
_DAILY_ALIASES = {"d", "daily"}
_WEEKLY_ALIASES = {"w", "weekly"}
_HELP_ALIASES = {"h", "help"}
_LEGACY_DAILY_ALIASES = {"日榜", "日报", "daily"}
_LEGACY_WEEKLY_ALIASES = {"周榜", "周报", "weekly"}
_LEGACY_HELP_ALIASES = {"帮助", "help", "菜单"}
_LEGACY_TOP_PREFIXES = {"top"}


@dataclass(frozen=True)
class SlashCommand:
    kind: str
    argument: Optional[str] = None
    top_k: Optional[int] = None


@dataclass(frozen=True)
class SlashCommandParseResult:
    is_slash: bool
    command: Optional[SlashCommand] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class MessageCommandParseResult:
    is_command: bool
    command: Optional[SlashCommand] = None
    error: Optional[str] = None


def parse_slash_command(
    text: str,
    default_top_k: int,
    max_top_k: int,
) -> SlashCommandParseResult:
    normalized = " ".join((text or "").split())
    if not normalized.startswith("/"):
        return SlashCommandParseResult(is_slash=False)

    match = _SLASH_COMMAND_PATTERN.match(normalized)
    if not match:
        return SlashCommandParseResult(is_slash=True, error="命令格式不正确，可使用 /help 查看帮助。")

    raw_command = match.group(1).lower()
    raw_argument = (match.group(2) or "").strip()

    if raw_command in _ANALYZE_ALIASES:
        if not raw_argument:
            return SlashCommandParseResult(
                is_slash=True,
                error="请提供仓库名、GitHub 链接或关键词，例如：/a openai/openai-python",
            )
        return SlashCommandParseResult(
            is_slash=True,
            command=SlashCommand(kind="analyze", argument=raw_argument),
        )

    if raw_command in _DAILY_ALIASES:
        top_k, error = _parse_optional_top_k(raw_argument, default_top_k, max_top_k)
        if error:
            return SlashCommandParseResult(is_slash=True, error=error)
        return SlashCommandParseResult(
            is_slash=True,
            command=SlashCommand(kind="daily", top_k=top_k),
        )

    if raw_command in _WEEKLY_ALIASES:
        top_k, error = _parse_optional_top_k(raw_argument, default_top_k, max_top_k)
        if error:
            return SlashCommandParseResult(is_slash=True, error=error)
        return SlashCommandParseResult(
            is_slash=True,
            command=SlashCommand(kind="weekly", top_k=top_k),
        )

    if raw_command in _HELP_ALIASES:
        return SlashCommandParseResult(
            is_slash=True,
            command=SlashCommand(kind="help"),
        )

    return SlashCommandParseResult(is_slash=True, error="不支持的命令，可使用 /help 查看帮助。")


def parse_message_command(
    text: str,
    default_top_k: int,
    max_top_k: int,
    mentions: Optional[Sequence[Mapping[str, Any]]] = None,
    bot_open_id: str = "",
) -> MessageCommandParseResult:
    slash_result = parse_slash_command(
        text,
        default_top_k=default_top_k,
        max_top_k=max_top_k,
    )
    if slash_result.is_slash:
        return MessageCommandParseResult(
            is_command=True,
            command=slash_result.command,
            error=slash_result.error,
        )

    mention_prefixed_body = _strip_leading_bot_mention(
        text,
        mentions=mentions,
        bot_open_id=bot_open_id,
    )
    if not mention_prefixed_body:
        return MessageCommandParseResult(is_command=False)

    slash_after_mention = parse_slash_command(
        mention_prefixed_body,
        default_top_k=default_top_k,
        max_top_k=max_top_k,
    )
    if slash_after_mention.is_slash:
        return MessageCommandParseResult(
            is_command=True,
            command=slash_after_mention.command,
            error=slash_after_mention.error,
        )

    tokens = mention_prefixed_body.split()
    if not tokens:
        return MessageCommandParseResult(is_command=False)

    keyword = tokens[0].lower()
    if keyword in _LEGACY_DAILY_ALIASES:
        top_k, error = _parse_legacy_top_k(tokens[1:], default_top_k, max_top_k)
        if error:
            return MessageCommandParseResult(is_command=True, error=error)
        return MessageCommandParseResult(
            is_command=True,
            command=SlashCommand(kind="daily", top_k=top_k),
        )

    if keyword in _LEGACY_WEEKLY_ALIASES:
        top_k, error = _parse_legacy_top_k(tokens[1:], default_top_k, max_top_k)
        if error:
            return MessageCommandParseResult(is_command=True, error=error)
        return MessageCommandParseResult(
            is_command=True,
            command=SlashCommand(kind="weekly", top_k=top_k),
        )

    if keyword in _LEGACY_HELP_ALIASES:
        return MessageCommandParseResult(
            is_command=True,
            command=SlashCommand(kind="help"),
        )

    return MessageCommandParseResult(
        is_command=True,
        command=SlashCommand(kind="analyze", argument=mention_prefixed_body),
    )


def extract_message_text(message: dict[str, Any]) -> str:
    text = message.get("text")
    if isinstance(text, str) and text.strip():
        return text

    content = message.get("content")
    if isinstance(content, str):
        return _extract_text_from_content(content)

    return ""


def parse_repo_reference(text: str) -> Optional[str]:
    if not text:
        return None

    match = _GITHUB_REPO_PATTERN.search(text)
    if match:
        owner = match.group(1)
        repo = match.group(2)
        if repo.lower().endswith(".git"):
            repo = repo[:-4]
        return f"{owner}/{repo}"

    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return None
    return cleaned


def build_help_text(default_top_k: int, max_top_k: int, about_doc_url: str) -> str:
    lines = [
        "🤖 可用命令",
        "",
        "1. `/a <repo|url|keyword>`",
        "   `/analyze <repo|url|keyword>`",
        "   分析项目详情，例如：`/a openai/openai-python`",
        "",
        "2. `/d [topN]`",
        "   `/daily [topN]`",
        "   触发日榜，例如：`/daily {0}`".format(default_top_k),
        "",
        "3. `/w [topN]`",
        "   `/weekly [topN]`",
        "   触发周榜，例如：`/weekly {0}`".format(default_top_k),
        "",
        "4. `/h`",
        "   `/help`",
        "   查看帮助",
        "",
    ]

    if about_doc_url:
        lines.extend(
            [
                "5. 关于我",
                "   [关于我介绍]({0})".format(about_doc_url),
                "",
            ]
        )

    lines.extend(
        [
            "💬 群聊请先真实 `@机器人` 再输入 slash 命令或 repo/url/keyword。",
            "📩 私聊可直接输入以上 slash 命令。",
            "   `@机器人` 只是占位符，请以群里的实际机器人显示名为准。",
            "",
            "ℹ️ topN 范围：1 - {0}".format(max_top_k),
        ]
    )

    return "\n".join(lines)


def _parse_optional_top_k(
    raw_argument: str,
    default_top_k: int,
    max_top_k: int,
) -> tuple[int, Optional[str]]:
    if not raw_argument:
        return default_top_k, None
    if not raw_argument.isdigit():
        return 0, "topN 必须是数字，例如：/daily 5"
    bounded_top_k = min(max(int(raw_argument), 1), max_top_k)
    return bounded_top_k, None


def _parse_legacy_top_k(
    tokens: list[str],
    default_top_k: int,
    max_top_k: int,
) -> tuple[int, Optional[str]]:
    if not tokens:
        return default_top_k, None
    if len(tokens) == 1:
        return _parse_optional_top_k(tokens[0], default_top_k, max_top_k)
    if len(tokens) == 2 and tokens[0].lower() in _LEGACY_TOP_PREFIXES:
        return _parse_optional_top_k(tokens[1], default_top_k, max_top_k)
    return 0, "topN 必须是数字，例如：日榜 top 5"


def _strip_leading_bot_mention(
    text: str,
    mentions: Optional[Sequence[Mapping[str, Any]]],
    bot_open_id: str,
) -> str:
    normalized_bot_open_id = (bot_open_id or "").strip()
    if not normalized_bot_open_id or not mentions:
        return ""

    bot_mention = _find_bot_mention(mentions, normalized_bot_open_id)
    if bot_mention is None:
        return ""

    remaining = (text or "").strip()
    if not remaining:
        return ""

    tag_match = _LEADING_MENTION_TAG_PATTERN.match(remaining)
    if tag_match is not None:
        if not _tag_matches_bot_mention(
            tag_match.group(1),
            bot_mention,
            normalized_bot_open_id,
        ):
            return ""
        return " ".join(remaining[tag_match.end() :].split())

    stripped = _strip_leading_plain_bot_mention(
        remaining,
        bot_mention,
    )
    return " ".join(stripped.split()) if stripped is not None else ""


def _find_bot_mention(
    mentions: Optional[Sequence[Mapping[str, Any]]],
    bot_open_id: str,
) -> Optional[Mapping[str, Any]]:
    if mentions is None:
        return None

    for mention in mentions:
        if bot_open_id in _mention_ids(mention):
            return mention
    return None


def _tag_matches_bot_mention(
    tag_text: str,
    bot_mention: Optional[Mapping[str, Any]],
    bot_open_id: str,
) -> bool:
    tag_ids = {
        match.group(2).strip()
        for match in _MENTION_TAG_ID_PATTERN.finditer(tag_text)
        if match.group(2).strip()
    }
    candidate_ids = {bot_open_id}
    if bot_mention is not None:
        candidate_ids.update(_mention_ids(bot_mention))
    if not tag_ids:
        return False
    return bool(tag_ids & candidate_ids)


def _strip_leading_plain_bot_mention(
    text: str,
    bot_mention: Optional[Mapping[str, Any]],
) -> Optional[str]:
    if bot_mention is None:
        return None
    marker = _field(bot_mention, "key")
    if not isinstance(marker, str):
        return None
    normalized_marker = marker.strip()
    if not normalized_marker:
        return None
    return _strip_plain_marker(text, normalized_marker)


def _strip_plain_marker(text: str, marker: str) -> Optional[str]:
    if not marker or not text.startswith(marker):
        return None
    if len(text) > len(marker) and not text[len(marker)].isspace():
        return None
    return text[len(marker) :].lstrip()


def _mention_ids(mention: Mapping[str, Any]) -> set[str]:
    mention_id = _field(mention, "id")
    return {
        value.strip()
        for value in (
            _field(mention_id, "open_id"),
            _field(mention_id, "user_id"),
            _field(mention_id, "union_id"),
        )
        if isinstance(value, str) and value.strip()
    }


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _extract_text_from_content(content: str) -> str:
    if not content:
        return ""

    try:
        content_json = json.loads(content)
    except json.JSONDecodeError:
        return content

    text = content_json.get("text") if isinstance(content_json, dict) else None
    return text if isinstance(text, str) else ""
