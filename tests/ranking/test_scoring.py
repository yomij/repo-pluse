from datetime import datetime, timedelta, timezone

import pytest

from repo_pulse.models import RepositorySnapshot
from repo_pulse.ranking.scoring import RankingService
from repo_pulse.ranking.topics import TopicClassifier
from repo_pulse.schemas import RepositoryCandidate


UTC = timezone.utc


def _baseline_snapshot(*, stars: int, forks: int, watchers: int = 9, captured_at: datetime | None = None):
    return RepositorySnapshot(
        full_name="acme/agent",
        captured_at=captured_at or datetime(2026, 4, 8, 9, 30, tzinfo=UTC),
        stars=stars,
        forks=forks,
        watchers=watchers,
        language="Python",
        topics_csv="ai,agents",
    )


def _candidate(
    *,
    full_name: str = "acme/agent",
    topics: list[str] | None = None,
    description: str = "AI agent runtime",
    stars: int = 180,
    forks: int = 20,
    watchers: int = 11,
    pushed_at: datetime | None = None,
    created_at: datetime | None = None,
    discovery_sources: list[str] | None = None,
    is_template: bool = False,
) -> RepositoryCandidate:
    owner, name = full_name.split("/", 1)
    return RepositoryCandidate(
        full_name=full_name,
        name=name,
        owner=owner,
        description=description,
        html_url="https://github.com/{0}".format(full_name),
        language="Python",
        topics=topics or ["ai", "agents"],
        stars=stars,
        forks=forks,
        watchers=watchers,
        pushed_at=pushed_at or datetime(2026, 4, 15, 7, 0, tzinfo=UTC),
        created_at=created_at,
        discovery_sources=discovery_sources or ["active_topic"],
        is_template=is_template,
    )


def test_daily_ranking_prefers_higher_relative_growth_with_same_24h_star_gain():
    service = RankingService(classifier=TopicClassifier())
    now = datetime(2026, 4, 15, 9, 30, tzinfo=UTC)

    small_base = _candidate(
        full_name="acme/small-base",
        stars=150,
        forks=25,
        created_at=now - timedelta(days=20),
        discovery_sources=["new_hot"],
    )
    large_base = _candidate(
        full_name="acme/large-base",
        stars=1100,
        forks=25,
        created_at=now - timedelta(days=20),
        discovery_sources=["new_hot"],
    )

    scored_small = service.score(
        kind="daily",
        candidate=small_base,
        baseline_24h=_baseline_snapshot(stars=50, forks=20),
        now=now,
        verified_star_delta_24h=100,
    )
    scored_large = service.score(
        kind="daily",
        candidate=large_base,
        baseline_24h=_baseline_snapshot(stars=1000, forks=20),
        now=now,
        verified_star_delta_24h=100,
    )

    assert scored_small.star_delta == 100
    assert scored_large.star_delta == 100
    assert scored_small.score > scored_large.score
    assert any("24h Stars +100" in line for line in scored_small.reason_lines)
    assert any("相对增长" in line for line in scored_small.reason_lines)


def test_daily_ranking_prefers_verified_growth_over_cold_start_with_higher_total_stars():
    service = RankingService(classifier=TopicClassifier())
    now = datetime(2026, 4, 15, 9, 30, tzinfo=UTC)

    heating_repo = _candidate(
        full_name="acme/heating-repo",
        stars=120,
        forks=24,
        created_at=now - timedelta(days=45),
        discovery_sources=["active_topic_recent"],
    )
    cold_start = _candidate(
        full_name="acme/cold-start",
        stars=1200,
        forks=90,
        created_at=now - timedelta(days=3),
        discovery_sources=["viral_recent_recall"],
    )

    scored_verified = service.score(
        kind="daily",
        candidate=heating_repo,
        baseline_24h=_baseline_snapshot(stars=110, forks=20),
        now=now,
        verified_star_delta_24h=10,
    )
    scored_cold_start = service.score(kind="daily", candidate=cold_start, baseline_24h=None, now=now)

    assert scored_verified.baseline_missing is False
    assert scored_cold_start.baseline_missing is True
    assert scored_verified.score > scored_cold_start.score
    assert scored_verified.star_delta == 10
    assert scored_cold_start.star_delta == 0


def test_weekly_ranking_prefers_repo_still_growing_in_last_24h():
    service = RankingService(classifier=TopicClassifier())
    now = datetime(2026, 4, 15, 9, 30, tzinfo=UTC)

    still_heating = _candidate(
        full_name="acme/still-heating",
        stars=320,
        forks=40,
        pushed_at=now - timedelta(hours=18),
        created_at=now - timedelta(days=40),
        discovery_sources=["established_mover"],
    )
    cooling = _candidate(
        full_name="acme/cooling",
        stars=320,
        forks=40,
        pushed_at=now - timedelta(hours=18),
        created_at=now - timedelta(days=40),
        discovery_sources=["established_mover"],
    )

    scored_heating = service.score(
        kind="weekly",
        candidate=still_heating,
        baseline_7d=_baseline_snapshot(stars=220, forks=30, captured_at=now - timedelta(days=7)),
        baseline_24h=_baseline_snapshot(stars=290, forks=38, captured_at=now - timedelta(days=1)),
        now=now,
    )
    scored_cooling = service.score(
        kind="weekly",
        candidate=cooling,
        baseline_7d=_baseline_snapshot(stars=220, forks=30, captured_at=now - timedelta(days=7)),
        baseline_24h=_baseline_snapshot(stars=320, forks=40, captured_at=now - timedelta(days=1)),
        now=now,
    )

    assert scored_heating.star_delta == 100
    assert scored_cooling.star_delta == 100
    assert scored_heating.recent_star_delta_24h == 30
    assert scored_cooling.recent_star_delta_24h == 0
    assert scored_heating.score > scored_cooling.score
    assert any("近 24h 仍在增长" in line for line in scored_heating.reason_lines)


def test_watchers_changes_do_not_affect_scores():
    service = RankingService(classifier=TopicClassifier())
    now = datetime(2026, 4, 15, 9, 30, tzinfo=UTC)

    low_watchers = _candidate(
        full_name="acme/watchers-low",
        watchers=1,
        created_at=now - timedelta(days=20),
    )
    high_watchers = _candidate(
        full_name="acme/watchers-high",
        watchers=999,
        created_at=now - timedelta(days=20),
    )

    scored_low = service.score(
        kind="daily",
        candidate=low_watchers,
        baseline_24h=_baseline_snapshot(stars=140, forks=15, watchers=0),
        now=now,
    )
    scored_high = service.score(
        kind="daily",
        candidate=high_watchers,
        baseline_24h=_baseline_snapshot(stars=140, forks=15, watchers=500),
        now=now,
    )

    assert scored_low.score == scored_high.score
    assert "watcher" not in " ".join(scored_low.reason_lines).lower()


def test_missing_created_at_does_not_raise_and_adds_no_youth_or_cold_start_bonus():
    service = RankingService(classifier=TopicClassifier())
    now = datetime(2026, 4, 15, 9, 30, tzinfo=UTC)
    candidate = _candidate(
        full_name="acme/no-created-at",
        stars=100,
        forks=10,
        created_at=None,
        discovery_sources=["active_topic"],
        pushed_at=now - timedelta(hours=2),
    )

    scored = service.score(kind="daily", candidate=candidate, baseline_24h=None, now=now)

    assert scored.score == pytest.approx(6.5)
    assert all("新项目" not in line for line in scored.reason_lines)


def test_daily_ranking_uses_snapshot_delta_when_verification_unavailable():
    service = RankingService(classifier=TopicClassifier())
    now = datetime(2026, 4, 15, 9, 30, tzinfo=UTC)
    candidate = _candidate(
        full_name="acme/fallback",
        stars=180,
        forks=20,
        created_at=now - timedelta(days=20),
        discovery_sources=["active_topic_recent"],
    )

    scored = service.score(
        kind="daily",
        candidate=candidate,
        baseline_24h=_baseline_snapshot(stars=140, forks=15),
        now=now,
        verification_failed=True,
    )

    assert scored.star_delta == 40
    assert any("fallback" in line.lower() for line in scored.reason_lines)


def test_template_like_repositories_are_stably_penalized():
    service = RankingService(classifier=TopicClassifier())
    now = datetime(2026, 4, 15, 9, 30, tzinfo=UTC)
    clean = _candidate(
        full_name="acme/clean",
        topics=["ai", "agents"],
        created_at=now - timedelta(days=15),
        discovery_sources=["established_mover"],
    )
    template = _candidate(
        full_name="acme/template",
        topics=["ai", "awesome-list"],
        is_template=True,
        created_at=now - timedelta(days=15),
        discovery_sources=["established_mover"],
    )

    scored_clean = service.score(
        kind="weekly",
        candidate=clean,
        baseline_7d=_baseline_snapshot(stars=120, forks=15, captured_at=now - timedelta(days=7)),
        baseline_24h=_baseline_snapshot(stars=170, forks=18, captured_at=now - timedelta(days=1)),
        now=now,
    )
    scored_template = service.score(
        kind="weekly",
        candidate=template,
        baseline_7d=_baseline_snapshot(stars=120, forks=15, captured_at=now - timedelta(days=7)),
        baseline_24h=_baseline_snapshot(stars=170, forks=18, captured_at=now - timedelta(days=1)),
        now=now,
    )

    assert scored_clean.score > scored_template.score
    assert scored_clean.score - scored_template.score == pytest.approx(10.0)
