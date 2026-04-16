import asyncio
import re
from pathlib import Path
from typing import Callable, Sequence

from repo_pulse.feishu.client import FeishuChat, FeishuClient

_ENV_LINE_PATTERN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


async def fetch_chats_for_selection(env_path: Path) -> list[FeishuChat]:
    app_id, app_secret = read_feishu_credentials(env_path)
    client = FeishuClient(app_id=app_id, app_secret=app_secret, chat_id="")
    try:
        return await client.list_chats()
    finally:
        await client.close()


def run_select_chat_id_command(
    *,
    name_filter: str = "",
    env_path: str | Path = ".env",
    input_func: Callable[[str], str] = input,
) -> int:
    path = Path(env_path)

    try:
        chats = asyncio.run(fetch_chats_for_selection(path))
    except Exception as exc:
        print("Failed to fetch Feishu chats: {0}".format(exc))
        return 1

    filtered_chats = _filter_chats(chats, name_filter=name_filter)
    if not filtered_chats:
        print("No Feishu chats matched the current filter.")
        return 1

    print("Feishu chats:")
    for index, chat in enumerate(filtered_chats, start=1):
        suffix = " (external)" if chat.external else ""
        print("{0}. {1} | {2}{3}".format(index, chat.name, chat.chat_id, suffix))

    selected = (input_func("Select chat number to append: ") or "").strip()
    if not selected.isdigit():
        print("Invalid selection.")
        return 1

    selection_index = int(selected) - 1
    if selection_index < 0 or selection_index >= len(filtered_chats):
        print("Invalid selection.")
        return 1

    try:
        chat_ids_value, default_chat_id = append_chat_id_to_env(
            path,
            filtered_chats[selection_index].chat_id,
        )
    except Exception as exc:
        print("Failed to update {0}: {1}".format(path, exc))
        return 1

    print("Updated FEISHU_CHAT_IDS={0}".format(chat_ids_value))
    if default_chat_id:
        print("Default FEISHU_CHAT_ID set to {0}".format(default_chat_id))
    return 0


def read_feishu_credentials(env_path: Path) -> tuple[str, str]:
    lines = _read_env_lines(env_path)
    app_id = _get_env_value(lines, "FEISHU_APP_ID")
    app_secret = _get_env_value(lines, "FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise RuntimeError("FEISHU_APP_ID and FEISHU_APP_SECRET must be configured in .env")
    return app_id, app_secret


def append_chat_id_to_env(env_path: Path, chat_id: str) -> tuple[str, str]:
    normalized_chat_id = (chat_id or "").strip()
    if not normalized_chat_id:
        raise RuntimeError("chat_id must not be empty")

    lines = _read_env_lines(env_path)
    configured_chat_ids = _parse_csv(_get_env_value(lines, "FEISHU_CHAT_IDS"))
    if normalized_chat_id not in configured_chat_ids:
        configured_chat_ids.append(normalized_chat_id)
    lines = _upsert_env_value(lines, "FEISHU_CHAT_IDS", ",".join(configured_chat_ids))

    current_default_chat_id = _get_env_value(lines, "FEISHU_CHAT_ID")
    default_chat_id = current_default_chat_id or normalized_chat_id
    if not current_default_chat_id:
        lines = _upsert_env_value(lines, "FEISHU_CHAT_ID", default_chat_id)

    env_path.write_text("\n".join(lines) + "\n")
    return ",".join(configured_chat_ids), default_chat_id if not current_default_chat_id else ""


def _filter_chats(chats: Sequence[FeishuChat], *, name_filter: str) -> list[FeishuChat]:
    keyword = (name_filter or "").strip().lower()
    if not keyword:
        return list(chats)
    return [chat for chat in chats if keyword in chat.name.lower()]


def _read_env_lines(env_path: Path) -> list[str]:
    if not env_path.exists():
        raise RuntimeError("Env file not found: {0}".format(env_path))
    return env_path.read_text().splitlines()


def _get_env_value(lines: Sequence[str], key: str) -> str:
    for line in lines:
        match = _ENV_LINE_PATTERN.match(line)
        if match and match.group(1) == key:
            return match.group(2).strip()
    return ""


def _parse_csv(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _upsert_env_value(lines: Sequence[str], key: str, value: str) -> list[str]:
    updated_lines = list(lines)
    for index, line in enumerate(updated_lines):
        match = _ENV_LINE_PATTERN.match(line)
        if match and match.group(1) == key:
            updated_lines[index] = "{0}={1}".format(key, value)
            return updated_lines

    updated_lines.append("{0}={1}".format(key, value))
    return updated_lines
