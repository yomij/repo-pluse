import json
import re
from dataclasses import dataclass
from typing import Any, Optional

_GITHUB_REPO_PATTERN = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_SLASH_COMMAND_PATTERN = re.compile(r"^\s*/([A-Za-z]+)(?:\s+(.*?))?\s*$")
_LEADING_MENTION_TAG_PATTERN = re.compile(r"^\s*<at\b[^>]*>.*?</at>", re.IGNORECASE)
_LEADING_PLAIN_MENTION_PATTERN = re.compile(r"^\s*[@＠][^\s]+")

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
    allow_legacy_mention_commands: bool = True,
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

    if not allow_legacy_mention_commands:
        return MessageCommandParseResult(is_command=False)

    legacy_body = _strip_leading_mentions(text)
    if not legacy_body:
        return MessageCommandParseResult(is_command=False)

    slash_after_mention = parse_slash_command(
        legacy_body,
        default_top_k=default_top_k,
        max_top_k=max_top_k,
    )
    if slash_after_mention.is_slash:
        return MessageCommandParseResult(
            is_command=True,
            command=slash_after_mention.command,
            error=slash_after_mention.error,
        )

    tokens = legacy_body.split()
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
        command=SlashCommand(kind="analyze", argument=legacy_body),
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


def build_help_text(default_top_k: int, max_top_k: int) -> str:
    return "\n".join(
        [
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
            "💬 也支持先 `@机器人` 再输入以上命令或仓库名。",
            "   `@机器人` 只是占位符，请以群里的实际机器人显示名为准。",
            "",
            "ℹ️ topN 范围：1 - {0}".format(max_top_k),
        ]
    )


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


def _strip_leading_mentions(text: str) -> str:
    remaining = (text or "").strip()
    stripped_any = False
    while remaining:
        tag_match = _LEADING_MENTION_TAG_PATTERN.match(remaining)
        if tag_match is not None:
            remaining = remaining[tag_match.end() :].lstrip()
            stripped_any = True
            continue

        plain_match = _LEADING_PLAIN_MENTION_PATTERN.match(remaining)
        if plain_match is not None:
            remaining = remaining[plain_match.end() :].lstrip()
            stripped_any = True
            continue
        break

    return " ".join(remaining.split()) if stripped_any else ""


def _extract_text_from_content(content: str) -> str:
    if not content:
        return ""

    try:
        content_json = json.loads(content)
    except json.JSONDecodeError:
        return content

    text = content_json.get("text") if isinstance(content_json, dict) else None
    return text if isinstance(text, str) else ""
