from dataclasses import dataclass, field
from datetime import datetime, timezone

from repo_pulse.models import RepositorySnapshot
from repo_pulse.ranking.topics import TopicClassifier
from repo_pulse.schemas import RepositoryCandidate

UTC = timezone.utc


@dataclass
class ScoredRepository:
    candidate: RepositoryCandidate
    categories: list[str]
    score: float
    star_delta: int
    fork_delta: int
    rank_bucket: int = 0
    recent_star_delta_24h: int = 0
    reason: str = ""
    reason_lines: list[str] = field(default_factory=list)
    baseline_missing: bool = False


class RankingService:
    def __init__(self, classifier: TopicClassifier):
        self.classifier = classifier

    def score(
        self,
        *,
        kind: str,
        candidate: RepositoryCandidate,
        now: datetime,
        baseline_24h: RepositorySnapshot | None = None,
        baseline_7d: RepositorySnapshot | None = None,
        verified_star_delta_24h: int | None = None,
        verified_truncated: bool = False,
        verification_failed: bool = False,
    ) -> ScoredRepository:
        categories = self.classifier.classify(candidate)
        if kind == "daily":
            return self._score_daily(
                candidate=candidate,
                categories=categories,
                baseline_24h=baseline_24h,
                now=now,
                verified_star_delta_24h=verified_star_delta_24h,
                verified_truncated=verified_truncated,
                verification_failed=verification_failed,
            )
        if kind == "weekly":
            return self._score_weekly(
                candidate=candidate,
                categories=categories,
                baseline_7d=baseline_7d,
                baseline_24h=baseline_24h,
                now=now,
            )
        raise ValueError("Unsupported ranking kind: {0}".format(kind))

    def _score_daily(
        self,
        *,
        candidate: RepositoryCandidate,
        categories: list[str],
        baseline_24h: RepositorySnapshot | None,
        now: datetime,
        verified_star_delta_24h: int | None,
        verified_truncated: bool,
        verification_failed: bool,
    ) -> ScoredRepository:
        snapshot_star_delta_24h = self._delta(candidate.stars, baseline_24h.stars if baseline_24h else None)
        fork_delta_24h = self._delta(candidate.forks, baseline_24h.forks if baseline_24h else None)
        verified_available = verified_star_delta_24h is not None
        if verified_available:
            star_delta_24h = max(verified_star_delta_24h or 0, 0)
            rank_bucket = 2 if star_delta_24h > 0 else 0
        else:
            star_delta_24h = snapshot_star_delta_24h
            rank_bucket = 1 if star_delta_24h > 0 else 0

        relative_growth = 0.0
        if verified_available:
            previous_stars = max(candidate.stars - star_delta_24h, 0)
            relative_growth = min(star_delta_24h / max(previous_stars, 25), 4.0)
        elif baseline_24h is not None:
            relative_growth = min(star_delta_24h / max(baseline_24h.stars, 25), 4.0)

        freshness_points = self._daily_freshness_points(candidate, now)
        repo_age_days = self._repo_age_days(candidate, now)
        youth_points = self._daily_youth_points(repo_age_days)
        source_points = self._daily_source_points(candidate)
        cold_start_points = self._daily_cold_start_points(
            baseline_24h=baseline_24h,
            repo_age_days=repo_age_days,
            verified_available=verified_available,
        )
        template_penalty = self.classifier.template_penalty(candidate)

        score = (
            star_delta_24h * 1.0
            + fork_delta_24h * 0.6
            + relative_growth * 6
            + freshness_points
            + youth_points
            + source_points
            + cold_start_points
            - template_penalty
        )
        star_count_label = "≥{0}".format(star_delta_24h) if verified_truncated else "+{0}".format(star_delta_24h)
        if verified_available:
            growth_line = "⭐ 真实 24h Stars {0} · 🍴 Forks +{1}".format(
                star_count_label,
                fork_delta_24h,
            )
        elif verification_failed or baseline_24h is not None:
            growth_line = "⭐ 24h Stars +{0} · 🍴 Forks +{1}（snapshot fallback）".format(
                star_delta_24h,
                fork_delta_24h,
            )
        else:
            growth_line = "⭐ 24h Stars +0 · 🍴 Forks +0"
        reason_lines = [
            growth_line,
            "📊 相对增长 {0:.1f}%".format(relative_growth * 100),
        ]
        project_line = self._daily_project_line(
            baseline_missing=baseline_24h is None,
            cold_start_points=cold_start_points,
            repo_age_days=repo_age_days,
        )
        if project_line:
            reason_lines.append(project_line)
        reason_lines.append(self._daily_update_line(candidate, now))
        return ScoredRepository(
            candidate=candidate,
            categories=categories,
            score=score,
            star_delta=star_delta_24h,
            fork_delta=fork_delta_24h,
            rank_bucket=rank_bucket,
            reason=" | ".join(reason_lines),
            reason_lines=reason_lines,
            baseline_missing=baseline_24h is None,
        )

    def _score_weekly(
        self,
        *,
        candidate: RepositoryCandidate,
        categories: list[str],
        baseline_7d: RepositorySnapshot | None,
        baseline_24h: RepositorySnapshot | None,
        now: datetime,
    ) -> ScoredRepository:
        star_delta_7d = self._delta(candidate.stars, baseline_7d.stars if baseline_7d else None)
        fork_delta_7d = self._delta(candidate.forks, baseline_7d.forks if baseline_7d else None)
        relative_growth_7d = 0.0
        if baseline_7d is not None:
            relative_growth_7d = min(star_delta_7d / max(baseline_7d.stars, 50), 2.0)
        recent_star_delta_24h = self._delta(
            candidate.stars,
            baseline_24h.stars if baseline_24h else None,
        )
        persistence_points = self._weekly_persistence_points(
            star_delta_7d=star_delta_7d,
            recent_star_delta_24h=recent_star_delta_24h,
        )
        freshness_points = self._weekly_freshness_points(candidate, now)
        repo_age_days = self._repo_age_days(candidate, now)
        youth_points = self._weekly_youth_points(repo_age_days)
        source_points = self._weekly_source_points(candidate)
        launch_points = self._weekly_launch_points(candidate, baseline_7d, repo_age_days)
        template_penalty = self.classifier.template_penalty(candidate)

        score = (
            star_delta_7d * 0.45
            + fork_delta_7d * 0.7
            + relative_growth_7d * 10
            + persistence_points
            + freshness_points
            + youth_points
            + source_points
            + launch_points
            - template_penalty
        )
        reason_lines = [
            "⭐ 7d Stars +{0} · 🍴 Forks +{1}".format(star_delta_7d, fork_delta_7d),
            "📊 相对增长 {0:.1f}%".format(relative_growth_7d * 100),
            self._weekly_recent_growth_line(recent_star_delta_24h),
            self._weekly_update_line(candidate, now),
        ]
        project_line = self._weekly_project_line(
            baseline_missing=baseline_7d is None,
            launch_points=launch_points,
        )
        if project_line:
            reason_lines.insert(3, project_line)
        return ScoredRepository(
            candidate=candidate,
            categories=categories,
            score=score,
            star_delta=star_delta_7d,
            fork_delta=fork_delta_7d,
            rank_bucket=1,
            recent_star_delta_24h=recent_star_delta_24h,
            reason=" | ".join(reason_lines),
            reason_lines=reason_lines,
            baseline_missing=baseline_7d is None,
        )

    @staticmethod
    def _delta(current: int, baseline: int | None) -> int:
        if baseline is None:
            return 0
        return max(current - baseline, 0)

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _repo_age_days(self, candidate: RepositoryCandidate, now: datetime) -> float | None:
        if candidate.created_at is None:
            return None
        age_seconds = (self._ensure_utc(now) - self._ensure_utc(candidate.created_at)).total_seconds()
        return max(age_seconds / 86400, 0.0)

    @classmethod
    def _hours_since_push(cls, candidate: RepositoryCandidate, now: datetime) -> float | None:
        if candidate.pushed_at is None:
            return None
        delta_seconds = (cls._ensure_utc(now) - cls._ensure_utc(candidate.pushed_at)).total_seconds()
        return max(delta_seconds / 3600, 0.0)

    def _daily_freshness_points(self, candidate: RepositoryCandidate, now: datetime) -> float:
        age_hours = self._hours_since_push(candidate, now)
        if age_hours is None:
            return 0.0
        if age_hours <= 6:
            return 6.0
        if age_hours <= 24:
            return 3.0
        if age_hours <= 72:
            return 0.0
        return -2.0

    @staticmethod
    def _daily_youth_points(repo_age_days: float | None) -> float:
        if repo_age_days is None:
            return 0.0
        if repo_age_days <= 14:
            return 6.0
        if repo_age_days <= 45:
            return 3.0
        return 0.0

    @staticmethod
    def _daily_source_points(candidate: RepositoryCandidate) -> float:
        sources = set(candidate.discovery_sources)
        points = 0.0
        if "new_hot_recent" in sources or "new_hot" in sources:
            points += 1.0
        if "viral_recent_recall" in sources:
            points += 0.5
        if "active_topic_recent" in sources or "active_topic" in sources:
            points += 0.5
        return min(points, 2.0)

    @staticmethod
    def _daily_cold_start_points(
        *,
        baseline_24h: RepositorySnapshot | None,
        repo_age_days: float | None,
        verified_available: bool,
    ) -> float:
        if verified_available or baseline_24h is not None or repo_age_days is None:
            return 0.0
        if repo_age_days <= 7:
            return 6.0
        if repo_age_days <= 14:
            return 4.0
        if repo_age_days <= 30:
            return 2.0
        return 0.0

    def _weekly_freshness_points(self, candidate: RepositoryCandidate, now: datetime) -> float:
        age_hours = self._hours_since_push(candidate, now)
        if age_hours is None:
            return 0.0
        if age_hours <= 72:
            return 3.0
        if age_hours <= 24 * 7:
            return 1.0
        return -2.0

    @staticmethod
    def _weekly_youth_points(repo_age_days: float | None) -> float:
        if repo_age_days is None:
            return 0.0
        if repo_age_days <= 30:
            return 3.0
        if repo_age_days <= 90:
            return 1.0
        return 0.0

    @staticmethod
    def _weekly_source_points(candidate: RepositoryCandidate) -> float:
        sources = set(candidate.discovery_sources)
        if "established_mover" in sources:
            return 3.0
        if "new_hot" in sources:
            return 1.0
        return 0.0

    @staticmethod
    def _weekly_launch_points(
        candidate: RepositoryCandidate,
        baseline_7d: RepositorySnapshot | None,
        repo_age_days: float | None,
    ) -> float:
        if baseline_7d is not None or repo_age_days is None or repo_age_days > 45:
            return 0.0
        return min(candidate.stars / max(repo_age_days, 7), 25) * 0.8 + min(candidate.forks, 50) * 0.2

    @staticmethod
    def _weekly_persistence_points(star_delta_7d: int, recent_star_delta_24h: int) -> float:
        if recent_star_delta_24h >= max(15, 0.2 * star_delta_7d):
            return 8.0
        if recent_star_delta_24h > 0:
            return 4.0
        return 0.0

    @staticmethod
    def _daily_project_line(
        *,
        baseline_missing: bool,
        cold_start_points: float,
        repo_age_days: float | None,
    ) -> str | None:
        if baseline_missing and cold_start_points > 0:
            return "🆕 新项目，冷启动弱信号 +{0:g}".format(cold_start_points)
        if baseline_missing:
            return "🆕 首次入榜，按冷启动信号处理"
        if repo_age_days is not None and repo_age_days <= 14:
            return "🌱 新项目仍处于快速试错期"
        return None

    @classmethod
    def _daily_update_line(cls, candidate: RepositoryCandidate, now: datetime) -> str:
        age_hours = cls._hours_since_push(candidate, now)
        if age_hours is None:
            return "🕰 暂无最近代码更新时间"
        if age_hours <= 24:
            return "⚡ 最近 24h 内仍有代码更新"
        if age_hours <= 72:
            return "🛠 最近 72h 内有代码更新"
        return "🕰 最近 72h 暂无代码更新"

    @staticmethod
    def _weekly_project_line(*, baseline_missing: bool, launch_points: float) -> str | None:
        if baseline_missing and launch_points > 0:
            return "🆕 新项目，按趋势冷启动处理"
        if baseline_missing:
            return "🆕 首次入榜，趋势仍待继续验证"
        return None

    @staticmethod
    def _weekly_recent_growth_line(recent_star_delta_24h: int) -> str:
        if recent_star_delta_24h > 0:
            return "🔥 近 24h 仍在增长，Stars +{0}".format(recent_star_delta_24h)
        return "⏸ 近 24h 增长已放缓"

    @classmethod
    def _weekly_update_line(cls, candidate: RepositoryCandidate, now: datetime) -> str:
        age_hours = cls._hours_since_push(candidate, now)
        if age_hours is None:
            return "🕰 暂无最近代码更新时间"
        if age_hours <= 72:
            return "⚡ 最近 72h 内仍有代码更新"
        if age_hours <= 24 * 7:
            return "🛠 最近 7d 内有代码更新"
        return "🕰 最近 7d 暂无代码更新"
