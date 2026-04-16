from datetime import datetime, timezone

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from repo_pulse.digest.service import DigestRequest
from repo_pulse.scheduler import DigestJob, build_digest_scheduler, build_scheduler


class _FakePipeline:
    def __init__(self, ranked_repos):
        self.ranked_repos = ranked_repos
        self.calls = []

    async def run_digest(
        self,
        digest_request,
        now,
        receive_id=None,
        pre_generate_top_n=0,
    ):
        self.calls.append(
            ("run_digest", digest_request, now, receive_id, pre_generate_top_n)
        )
        return self.ranked_repos

    async def pre_generate_details(self, repos):
        self.calls.append(("pre_generate_details", repos))


@pytest.mark.asyncio
async def test_digest_job_forwards_pregen_top_n_on_scheduled_runs():
    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    ranked = ["owner/a", "owner/b", "owner/c", "owner/d"]
    pipeline = _FakePipeline(ranked_repos=ranked)
    job = DigestJob(
        pipeline=pipeline,
        digest_request=DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=10,
        ),
        pregen_top_n=2,
    )

    await job.run(now)

    assert pipeline.calls == [
        (
            "run_digest",
            DigestRequest(
                kind="daily",
                title="GitHub 热门日榜",
                window="24h",
                window_hours=24,
                top_k=10,
            ),
            now,
            None,
            2,
        ),
    ]


@pytest.mark.asyncio
async def test_digest_job_can_override_top_k_and_skip_pregeneration_for_manual_runs():
    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    pipeline = _FakePipeline(ranked_repos=["owner/a"])
    job = DigestJob(
        pipeline=pipeline,
        digest_request=DigestRequest(
            kind="weekly",
            title="GitHub 热门周榜",
            window="7d",
            window_hours=168,
            top_k=5,
        ),
        pregen_top_n=3,
    )

    await job.run(now, receive_id="chat-1", top_k=10, pre_generate=False)

    assert pipeline.calls == [
        (
            "run_digest",
            DigestRequest(
                kind="weekly",
                title="GitHub 热门周榜",
                window="7d",
                window_hours=168,
                top_k=10,
            ),
            now,
            "chat-1",
            0,
        ),
    ]


def test_build_scheduler_creates_asyncio_scheduler_and_cron_job():
    digest_job = DigestJob(
        pipeline=_FakePipeline([]),
        digest_request=DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=10,
        ),
        pregen_top_n=1,
    )

    scheduler = build_scheduler("30 9 * * 1-5", digest_job, scheduler_timezone=timezone.utc)

    assert isinstance(scheduler, AsyncIOScheduler)
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1

    job = jobs[0]
    assert job.func == digest_job.run
    assert isinstance(job.trigger, CronTrigger)

    trigger_fields = {field.name: str(field) for field in job.trigger.fields}
    assert trigger_fields["minute"] == "30"
    assert trigger_fields["hour"] == "9"
    assert trigger_fields["day"] == "*"
    assert trigger_fields["month"] == "*"
    assert trigger_fields["day_of_week"] == "mon,tue,wed,thu,fri"


def test_build_scheduler_mon_to_fri_cron_triggers_on_monday():
    digest_job = DigestJob(
        pipeline=_FakePipeline([]),
        digest_request=DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=10,
        ),
        pregen_top_n=1,
    )
    scheduler = build_scheduler("30 9 * * 1-5", digest_job, scheduler_timezone=timezone.utc)
    job = scheduler.get_jobs()[0]

    next_fire = job.trigger.get_next_fire_time(
        previous_fire_time=None,
        now=datetime(2026, 4, 13, 9, 0, tzinfo=timezone.utc),
    )

    assert next_fire == datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)


def test_build_scheduler_raises_for_non_5_part_cron():
    digest_job = DigestJob(
        pipeline=_FakePipeline([]),
        digest_request=DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=10,
        ),
        pregen_top_n=1,
    )

    with pytest.raises(ValueError):
        build_scheduler("30 9 * *", digest_job)


def test_digest_job_raises_when_pregen_top_n_is_negative():
    with pytest.raises(ValueError):
        DigestJob(
            pipeline=_FakePipeline([]),
            digest_request=DigestRequest(
                kind="daily",
                title="GitHub 热门日榜",
                window="24h",
                window_hours=24,
                top_k=10,
            ),
            pregen_top_n=-1,
        )


def test_build_scheduler_supports_zero_to_six_as_all_week():
    digest_job = DigestJob(
        pipeline=_FakePipeline([]),
        digest_request=DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=10,
        ),
        pregen_top_n=1,
    )

    scheduler = build_scheduler("30 9 * * 0-6", digest_job, scheduler_timezone=timezone.utc)

    job = scheduler.get_jobs()[0]
    trigger_fields = {field.name: str(field) for field in job.trigger.fields}
    assert trigger_fields["day_of_week"] == "*"


def test_build_scheduler_supports_zero_to_seven_as_all_week():
    digest_job = DigestJob(
        pipeline=_FakePipeline([]),
        digest_request=DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=10,
        ),
        pregen_top_n=1,
    )

    scheduler = build_scheduler("30 9 * * 0-7", digest_job, scheduler_timezone=timezone.utc)

    job = scheduler.get_jobs()[0]
    trigger_fields = {field.name: str(field) for field in job.trigger.fields}
    assert trigger_fields["day_of_week"] == "*"


def test_build_scheduler_raises_for_unsupported_weekday_step_expression():
    digest_job = DigestJob(
        pipeline=_FakePipeline([]),
        digest_request=DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=10,
        ),
        pregen_top_n=1,
    )

    with pytest.raises(ValueError):
        build_scheduler("30 9 * * */2", digest_job, scheduler_timezone=timezone.utc)


def test_build_digest_scheduler_registers_daily_and_weekly_jobs():
    daily_job = DigestJob(
        pipeline=_FakePipeline([]),
        digest_request=DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=10,
        ),
        pregen_top_n=1,
    )
    weekly_job = DigestJob(
        pipeline=_FakePipeline([]),
        digest_request=DigestRequest(
            kind="weekly",
            title="GitHub 热门周榜",
            window="7d",
            window_hours=168,
            top_k=10,
        ),
        pregen_top_n=1,
    )

    scheduler = build_digest_scheduler(
        daily_cron="30 9 * * 1-5",
        daily_job=daily_job,
        weekly_cron="0 10 * * 1",
        weekly_job=weekly_job,
        scheduler_timezone=timezone.utc,
    )

    assert isinstance(scheduler, AsyncIOScheduler)
    jobs = scheduler.get_jobs()
    assert len(jobs) == 2
    assert {job.func for job in jobs} == {daily_job.run, weekly_job.run}
