from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect
from sqlmodel import Session, select

from repo_pulse.db import build_engine, init_db


def _make_snapshot(**kwargs):
    from repo_pulse.models import RepositorySnapshot

    defaults = {
        "full_name": "acme/agent",
        "captured_at": datetime(2026, 4, 12, 9, 30, tzinfo=timezone.utc),
        "stars": 120,
        "forks": 12,
        "watchers": 8,
        "language": "Python",
        "topics_csv": "ai,agents",
        "pushed_at": datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc),
    }
    defaults.update(kwargs)
    return RepositorySnapshot(**defaults)


def _make_project_detail(**kwargs):
    from repo_pulse.models import ProjectDetailCache

    defaults = {
        "full_name": "acme/agent",
        "doc_url": "https://example.com/docs",
        "summary_markdown": "info",
        "citations_json": "[]",
        "updated_at": datetime(2026, 4, 13, 12, 0),
    }
    defaults.update(kwargs)
    return ProjectDetailCache(**defaults)


def _make_digest_cache(**kwargs):
    from repo_pulse.models import DigestResultCache

    defaults = {
        "kind": "daily",
        "digest_json": '{"title":"GitHub 热门日榜","window":"24h","entries":[],"generated_at":"2026-04-13T12:00:00+00:00"}',
        "generated_at": datetime(2026, 4, 13, 12, 0),
        "expires_at": datetime(2026, 4, 13, 14, 0),
    }
    defaults.update(kwargs)
    return DigestResultCache(**defaults)


def test_init_db_creates_tables_without_prior_model_import(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'assistant.db'}")
    init_db(engine)

    inspector = inspect(engine)
    tables = {name.lower() for name in inspector.get_table_names()}
    assert "repositorysnapshot" in tables
    assert "projectdetailcache" in tables
    assert "digestresultcache" in tables


def test_snapshot_repository_returns_latest_before_cutoff(tmp_path):
    from repo_pulse.models import RepositorySnapshot
    from repo_pulse.repositories import SnapshotRepository

    engine = build_engine(f"sqlite:///{tmp_path / 'assistant.db'}")
    init_db(engine)
    repo = SnapshotRepository(engine)

    first = _make_snapshot(
        captured_at=datetime(2026, 4, 12, 9, 30, tzinfo=timezone.utc),
        stars=120,
    )
    second = _make_snapshot(
        captured_at=datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc),
        stars=180,
    )

    repo.save(first)
    repo.save(second)

    cutoff = datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc) + timedelta(hours=1)
    snapshot = repo.latest_before("acme/agent", cutoff)

    assert snapshot is not None
    assert isinstance(snapshot, RepositorySnapshot)
    assert snapshot.stars == 120


def test_snapshot_repository_returns_latest_before_many_cutoffs(tmp_path):
    from repo_pulse.repositories import SnapshotRepository

    engine = build_engine(f"sqlite:///{tmp_path / 'assistant.db'}")
    init_db(engine)
    repo = SnapshotRepository(engine)

    first = _make_snapshot(
        captured_at=datetime(2026, 4, 8, 9, 30, tzinfo=timezone.utc),
        stars=90,
        forks=9,
    )
    second = _make_snapshot(
        captured_at=datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc),
        stars=140,
        forks=14,
    )
    third = _make_snapshot(
        captured_at=datetime(2026, 4, 15, 8, 30, tzinfo=timezone.utc),
        stars=150,
        forks=15,
    )

    repo.save(first)
    repo.save(second)
    repo.save(third)

    cutoffs = [
        datetime(2026, 4, 14, 17, 30, tzinfo=timezone(timedelta(hours=8))),
        datetime(2026, 4, 9, 17, 30, tzinfo=timezone(timedelta(hours=8))),
    ]
    snapshots = repo.latest_before_many("acme/agent", cutoffs)

    assert snapshots[cutoffs[0]] is not None
    assert snapshots[cutoffs[0]].stars == 140
    assert snapshots[cutoffs[0]].captured_at.tzinfo is timezone.utc
    assert snapshots[cutoffs[1]] is not None
    assert snapshots[cutoffs[1]].stars == 90
    assert snapshots[cutoffs[1]].captured_at.tzinfo is timezone.utc


def test_project_detail_repository_upsert_and_get(tmp_path):
    from repo_pulse.models import ProjectDetailCache
    from repo_pulse.repositories import ProjectDetailRepository

    engine = build_engine(f"sqlite:///{tmp_path / 'assistant.db'}")
    init_db(engine)
    repo = ProjectDetailRepository(engine)

    detail = _make_project_detail(doc_url="https://example.com", summary_markdown="first")
    repo.upsert(detail)

    retrieved = repo.get("acme/agent")
    assert retrieved is not None
    assert retrieved.doc_url == "https://example.com"

    updated = _make_project_detail(
        doc_url="https://example.com/new",
        summary_markdown="second",
        citations_json='["a"]',
    )
    repo.upsert(updated)

    with Session(engine) as session:
        records = session.exec(select(ProjectDetailCache)).all()
    assert len(records) == 1

    retrieved_again = repo.get("acme/agent")
    assert retrieved_again.doc_url == "https://example.com/new"
    assert retrieved_again.summary_markdown == "second"


def test_project_detail_repository_get_valid_respects_ttl(tmp_path):
    from repo_pulse.repositories import ProjectDetailRepository

    engine = build_engine(f"sqlite:///{tmp_path / 'assistant.db'}")
    init_db(engine)
    repo = ProjectDetailRepository(engine)

    repo.upsert(
        _make_project_detail(
            updated_at=datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
        )
    )

    fresh = repo.get_valid(
        "acme/agent",
        now=datetime(2026, 4, 13, 13, 0, tzinfo=timezone.utc),
        ttl_seconds=7200,
    )
    stale = repo.get_valid(
        "acme/agent",
        now=datetime(2026, 4, 13, 15, 1, tzinfo=timezone.utc),
        ttl_seconds=7200,
    )

    assert fresh is not None
    assert stale is None


def test_project_detail_repository_get_latest_returns_cache_regardless_of_age(tmp_path):
    from repo_pulse.repositories import ProjectDetailRepository

    engine = build_engine(f"sqlite:///{tmp_path / 'assistant.db'}")
    init_db(engine)
    repo = ProjectDetailRepository(engine)

    repo.upsert(
        _make_project_detail(
            updated_at=datetime(2026, 4, 10, 12, 0, tzinfo=timezone(timedelta(hours=8))),
        )
    )

    latest = repo.get_latest("acme/agent")

    assert latest is not None
    assert latest.full_name == "acme/agent"
    assert latest.updated_at == datetime(2026, 4, 10, 4, 0, tzinfo=timezone.utc)
    assert latest.updated_at.tzinfo is timezone.utc


def test_digest_result_cache_repository_upsert_and_get_valid(tmp_path):
    from repo_pulse.models import DigestResultCache
    from repo_pulse.repositories import DigestResultCacheRepository

    engine = build_engine(f"sqlite:///{tmp_path / 'assistant.db'}")
    init_db(engine)
    repo = DigestResultCacheRepository(engine)

    repo.upsert(_make_digest_cache())

    valid = repo.get_valid("daily", datetime(2026, 4, 13, 13, 0, tzinfo=timezone.utc))
    assert valid is not None
    assert valid.kind == "daily"

    repo.upsert(
        _make_digest_cache(
            digest_json='{"title":"GitHub 热门日榜","window":"24h","entries":[{"full_name":"acme/agent"}],"generated_at":"2026-04-13T13:00:00+00:00"}',
            generated_at=datetime(2026, 4, 13, 13, 0),
            expires_at=datetime(2026, 4, 13, 15, 0),
        )
    )

    with Session(engine) as session:
        records = session.exec(select(DigestResultCache)).all()
    assert len(records) == 1

    valid_again = repo.get_valid("daily", datetime(2026, 4, 13, 14, 0, tzinfo=timezone.utc))
    assert valid_again is not None
    assert '"acme/agent"' in valid_again.digest_json

    expired = repo.get_valid("daily", datetime(2026, 4, 13, 16, 0, tzinfo=timezone.utc))
    assert expired is None


def test_digest_result_cache_repository_get_latest_returns_expired_cache(tmp_path):
    from repo_pulse.repositories import DigestResultCacheRepository

    engine = build_engine(f"sqlite:///{tmp_path / 'assistant.db'}")
    init_db(engine)
    repo = DigestResultCacheRepository(engine)

    cache = _make_digest_cache(
        generated_at=datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 4, 13, 14, 0, tzinfo=timezone.utc),
    )
    repo.upsert(cache)

    latest = repo.get_latest("daily")

    assert latest is not None
    assert latest.kind == "daily"
    assert latest.expires_at == datetime(2026, 4, 13, 14, 0, tzinfo=timezone.utc)


def test_datetimes_round_trip_remain_utc(tmp_path):
    from repo_pulse.repositories import ProjectDetailRepository, SnapshotRepository

    engine = build_engine(f"sqlite:///{tmp_path / 'assistant.db'}")
    init_db(engine)

    snapshot_repo = SnapshotRepository(engine)
    detail_repo = ProjectDetailRepository(engine)

    naive_snapshot = _make_snapshot(captured_at=datetime(2026, 4, 12, 9, 30))
    snapshot_repo.save(naive_snapshot)
    result_snapshot = snapshot_repo.latest_before(
        "acme/agent", datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc)
    )
    assert result_snapshot is not None
    assert result_snapshot.captured_at.tzinfo is timezone.utc

    naive_detail = _make_project_detail(updated_at=datetime(2026, 4, 13, 12, 0))
    detail_repo.upsert(naive_detail)
    result_detail = detail_repo.get("acme/agent")
    assert result_detail is not None
    assert result_detail.updated_at.tzinfo is timezone.utc


def test_latest_before_normalizes_non_utc_cutoff(tmp_path):
    from repo_pulse.repositories import SnapshotRepository

    engine = build_engine(f"sqlite:///{tmp_path / 'assistant.db'}")
    init_db(engine)
    repo = SnapshotRepository(engine)

    repo.save(
        _make_snapshot(
            captured_at=datetime(2026, 4, 12, 9, 30, tzinfo=timezone.utc),
            stars=100,
        )
    )
    cutoff = datetime(2026, 4, 13, 1, 0, tzinfo=timezone(timedelta(hours=3)))

    snapshot = repo.latest_before("acme/agent", cutoff)

    assert snapshot is not None
    assert snapshot.captured_at.tzinfo is timezone.utc


def test_project_detail_repository_upsert_keeps_input_detail_usable(tmp_path):
    from repo_pulse.repositories import ProjectDetailRepository

    engine = build_engine(f"sqlite:///{tmp_path / 'assistant.db'}")
    init_db(engine)
    repo = ProjectDetailRepository(engine)

    detail = _make_project_detail(
        doc_url="https://example.com/new",
        summary_markdown="fresh markdown",
    )

    repo.upsert(detail)

    assert inspect(detail).expired_attributes == set()
    assert detail.full_name == "acme/agent"
    assert detail.doc_url == "https://example.com/new"
    assert detail.summary_markdown == "fresh markdown"
