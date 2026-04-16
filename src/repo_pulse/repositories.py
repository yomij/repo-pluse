from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, desc, select

from repo_pulse.models import DigestResultCache, ProjectDetailCache, RepositorySnapshot

UTC = timezone.utc


def _ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SnapshotRepository:
    def __init__(self, engine):
        self.engine = engine

    def save(self, snapshot: RepositorySnapshot) -> None:
        with Session(self.engine) as session:
            snapshot.captured_at = _ensure_utc(snapshot.captured_at)
            snapshot.pushed_at = _ensure_utc(snapshot.pushed_at)
            session.add(snapshot)
            session.commit()

    def latest_before(self, full_name: str, cutoff):
        cutoff = _ensure_utc(cutoff)
        with Session(self.engine) as session:
            statement = (
                select(RepositorySnapshot)
                .where(RepositorySnapshot.full_name == full_name)
                .where(RepositorySnapshot.captured_at <= cutoff)
                .order_by(desc(RepositorySnapshot.captured_at))
            )
            snapshot = session.exec(statement).first()
            if snapshot:
                snapshot.captured_at = _ensure_utc(snapshot.captured_at)
                snapshot.pushed_at = _ensure_utc(snapshot.pushed_at)
            return snapshot

    def latest_before_many(self, full_name: str, cutoffs):
        cutoff_pairs = [
            (cutoff, _ensure_utc(cutoff))
            for cutoff in cutoffs
            if _ensure_utc(cutoff) is not None
        ]
        if not cutoff_pairs:
            return {}

        normalized_cutoffs = [normalized for _, normalized in cutoff_pairs]
        max_cutoff = max(normalized_cutoffs)
        with Session(self.engine) as session:
            snapshots = session.exec(
                select(RepositorySnapshot)
                .where(RepositorySnapshot.full_name == full_name)
                .where(RepositorySnapshot.captured_at <= max_cutoff)
                .order_by(desc(RepositorySnapshot.captured_at))
            ).all()

        normalized_snapshots = []
        for snapshot in snapshots:
            snapshot.captured_at = _ensure_utc(snapshot.captured_at)
            snapshot.pushed_at = _ensure_utc(snapshot.pushed_at)
            normalized_snapshots.append(snapshot)

        results = {}
        for original_cutoff, normalized_cutoff in cutoff_pairs:
            results[original_cutoff] = next(
                (
                    snapshot
                    for snapshot in normalized_snapshots
                    if snapshot.captured_at <= normalized_cutoff
                ),
                None,
            )
        return results


class ProjectDetailRepository:
    def __init__(self, engine):
        self.engine = engine

    def get(self, full_name: str):
        with Session(self.engine) as session:
            detail = session.exec(
                select(ProjectDetailCache).where(ProjectDetailCache.full_name == full_name)
            ).first()
            if detail:
                detail.updated_at = _ensure_utc(detail.updated_at)
            return detail

    def get_latest(self, full_name: str):
        return self.get(full_name)

    def get_valid(self, full_name: str, now: datetime, ttl_seconds: int):
        detail = self.get(full_name)
        if detail is None:
            return None
        if ttl_seconds <= 0:
            return None

        normalized_now = _ensure_utc(now)
        if normalized_now is None:
            return None
        age_seconds = (normalized_now - detail.updated_at).total_seconds()
        if age_seconds >= ttl_seconds:
            return None
        return detail

    def upsert(self, detail: ProjectDetailCache) -> None:
        detail.updated_at = _ensure_utc(detail.updated_at)
        with Session(self.engine) as session:
            existing = session.exec(
                select(ProjectDetailCache).where(ProjectDetailCache.full_name == detail.full_name)
            ).first()
            if existing:
                existing.doc_url = detail.doc_url
                existing.summary_markdown = detail.summary_markdown
                existing.citations_json = detail.citations_json
                existing.updated_at = detail.updated_at
            else:
                session.add(
                    ProjectDetailCache(
                        full_name=detail.full_name,
                        doc_url=detail.doc_url,
                        summary_markdown=detail.summary_markdown,
                        citations_json=detail.citations_json,
                        updated_at=detail.updated_at,
                    )
                )
            session.commit()


class DigestResultCacheRepository:
    def __init__(self, engine):
        self.engine = engine

    def get_valid(self, kind: str, now: datetime):
        now = _ensure_utc(now)
        with Session(self.engine) as session:
            cache = session.exec(
                select(DigestResultCache)
                .where(DigestResultCache.kind == kind)
                .where(DigestResultCache.expires_at > now)
            ).first()
            if cache:
                cache.generated_at = _ensure_utc(cache.generated_at)
                cache.expires_at = _ensure_utc(cache.expires_at)
            return cache

    def get_latest(self, kind: str):
        with Session(self.engine) as session:
            cache = session.exec(
                select(DigestResultCache)
                .where(DigestResultCache.kind == kind)
                .order_by(desc(DigestResultCache.generated_at))
            ).first()
            if cache:
                cache.generated_at = _ensure_utc(cache.generated_at)
                cache.expires_at = _ensure_utc(cache.expires_at)
            return cache

    def upsert(self, cache: DigestResultCache) -> None:
        with Session(self.engine) as session:
            existing = session.exec(
                select(DigestResultCache).where(DigestResultCache.kind == cache.kind)
            ).first()
            if existing:
                existing.digest_json = cache.digest_json
                existing.generated_at = _ensure_utc(cache.generated_at)
                existing.expires_at = _ensure_utc(cache.expires_at)
            else:
                cache.generated_at = _ensure_utc(cache.generated_at)
                cache.expires_at = _ensure_utc(cache.expires_at)
                session.add(cache)
            session.commit()
