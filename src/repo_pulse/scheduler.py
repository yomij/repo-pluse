from dataclasses import replace
from datetime import datetime, tzinfo, timezone
from typing import Optional, Protocol, Sequence

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from repo_pulse.digest.service import DigestRequest


class DigestPipelineProtocol(Protocol):
    async def run_digest(
        self,
        digest_request: DigestRequest,
        now: datetime,
        receive_id: Optional[str] = None,
        pre_generate_top_n: int = 0,
    ) -> Sequence[str]:
        ...

    async def pre_generate_details(self, ranked_repos: Sequence[str]) -> None:
        ...


class DigestJob:
    def __init__(
        self,
        pipeline: DigestPipelineProtocol,
        digest_request: DigestRequest,
        pregen_top_n: int,
    ):
        if pregen_top_n < 0:
            raise ValueError("pregen_top_n must be >= 0")
        self.pipeline = pipeline
        self.digest_request = digest_request
        self.pregen_top_n = pregen_top_n

    async def run(
        self,
        now: Optional[datetime] = None,
        receive_id: Optional[str] = None,
        top_k: Optional[int] = None,
        pre_generate: bool = True,
    ) -> None:
        current_time = now or datetime.now(timezone.utc)
        effective_request = self.digest_request
        if top_k is not None:
            effective_request = replace(self.digest_request, top_k=max(top_k, 0))
        ranked_repos = await self.pipeline.run_digest(
            effective_request,
            current_time,
            receive_id=receive_id,
            pre_generate_top_n=self.pregen_top_n if pre_generate else 0,
        )


DailyDigestJob = DigestJob


_WEEKDAY_MAP = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}
_WEEKDAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def _convert_weekday_atom(token: str) -> int:
    lowered = token.strip().lower()
    if lowered in _WEEKDAY_MAP:
        return _WEEKDAY_MAP[lowered]
    if lowered.isdigit():
        number = int(lowered)
        if 0 <= number <= 7:
            if number == 7:
                return 0
            return number
    raise ValueError("Unsupported weekday token in cron_expression")


def _expand_weekday_part(part: str) -> Sequence[int]:
    if "/" in part:
        raise ValueError("Weekday step expressions are not supported")
    if "-" not in part:
        return (_convert_weekday_atom(part),)

    start_raw, end_raw = [item.strip() for item in part.split("-", 1)]
    if not start_raw or not end_raw:
        raise ValueError("Invalid weekday range in cron_expression")
    if start_raw.isdigit() and end_raw.isdigit():
        start_num = int(start_raw)
        end_num = int(end_raw)
        if not (0 <= start_num <= 7 and 0 <= end_num <= 7):
            raise ValueError("Unsupported weekday token in cron_expression")
        if start_num == 0 and end_num == 7:
            return tuple(range(0, 7))

    start = _convert_weekday_atom(start_raw)
    end = _convert_weekday_atom(end_raw)
    if start > end:
        raise ValueError("Descending weekday ranges are not supported")
    return tuple(range(start, end + 1))


def _convert_weekday_field(weekday: str) -> str:
    normalized = weekday.strip().lower()
    if normalized == "*":
        return "*"

    selected = []
    selected_lookup = set()
    for part in weekday.split(","):
        stripped = part.strip()
        if not stripped:
            raise ValueError("Invalid weekday list in cron_expression")
        for weekday_number in _expand_weekday_part(stripped):
            if weekday_number not in selected_lookup:
                selected.append(weekday_number)
                selected_lookup.add(weekday_number)

    if len(selected_lookup) == 7:
        return "*"
    return ",".join(_WEEKDAY_NAMES[item] for item in selected)


def build_scheduler(
    cron_expression: str,
    digest_job: DigestJob,
    scheduler_timezone: Optional[tzinfo] = None,
) -> AsyncIOScheduler:
    effective_timezone = scheduler_timezone or datetime.now().astimezone().tzinfo
    if effective_timezone is None:
        effective_timezone = timezone.utc

    scheduler = AsyncIOScheduler(timezone=effective_timezone)
    scheduler.add_job(
        digest_job.run,
        _build_cron_trigger(cron_expression, effective_timezone),
    )
    return scheduler


def build_digest_scheduler(
    daily_cron: str,
    daily_job: DigestJob,
    weekly_cron: str,
    weekly_job: DigestJob,
    scheduler_timezone: Optional[tzinfo] = None,
) -> AsyncIOScheduler:
    effective_timezone = scheduler_timezone or datetime.now().astimezone().tzinfo
    if effective_timezone is None:
        effective_timezone = timezone.utc

    scheduler = AsyncIOScheduler(timezone=effective_timezone)
    scheduler.add_job(
        daily_job.run,
        _build_cron_trigger(daily_cron, effective_timezone),
    )
    scheduler.add_job(
        weekly_job.run,
        _build_cron_trigger(weekly_cron, effective_timezone),
    )
    return scheduler


def _build_cron_trigger(cron_expression: str, effective_timezone: tzinfo) -> CronTrigger:
    parts = cron_expression.split()
    if len(parts) != 5:
        raise ValueError("cron_expression must contain 5 fields")

    minute, hour, day, month, weekday = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=_convert_weekday_field(weekday),
        timezone=effective_timezone,
    )
