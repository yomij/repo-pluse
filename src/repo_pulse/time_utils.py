from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo


def build_timezone(timezone_name: str) -> ZoneInfo:
    return ZoneInfo(timezone_name)


def format_display_time(
    value: Optional[str | datetime],
    timezone_name: str,
) -> str:
    if value is None:
        return "未提供"

    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = str(value).strip()
        if not normalized:
            return "未提供"
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return normalized

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(build_timezone(timezone_name))

    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def to_business_datetime(value: datetime, timezone_name: str) -> datetime:
    parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(build_timezone(timezone_name))
