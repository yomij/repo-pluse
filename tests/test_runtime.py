import asyncio
from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from repo_pulse.digest.service import DigestRequest
from repo_pulse.models import ProjectDetailCache, RepositorySnapshot
from repo_pulse.schemas import RepositoryCandidate

_ABOUT_DOC_URL = "https://example.feishu.cn/docx/about-me"


class _FakeDiscoveryService:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    async def collect_candidates(self, now):
        self.calls.append(now)
        return list(self.candidates)


class _FakeSnapshotRepository:
    def __init__(self, baselines=None):
        self.baselines = baselines or {}
        self.latest_before_calls = []
        self.latest_before_many_calls = []
        self.saved = []

    def latest_before(self, full_name, cutoff):
        self.latest_before_calls.append((full_name, cutoff))
        return self.baselines.get(full_name)

    def latest_before_many(self, full_name, cutoffs):
        self.latest_before_many_calls.append((full_name, list(cutoffs)))
        snapshots = {}
        for cutoff in cutoffs:
            snapshots[cutoff] = self.baselines.get((full_name, cutoff), self.baselines.get(full_name))
        return snapshots

    def save(self, snapshot):
        self.saved.append(snapshot)


class _FakeDetailRepository:
    def __init__(self, cached=None):
        self.cached = cached or {}
        self.get_calls = []

    def get(self, full_name):
        self.get_calls.append(full_name)
        return self.cached.get(full_name)

    def get_valid(self, full_name, now, ttl_seconds):
        self.get_calls.append((full_name, now, ttl_seconds))
        detail = self.cached.get(full_name)
        if detail is None:
            return None
        if ttl_seconds <= 0:
            return None
        if (now - detail.updated_at).total_seconds() >= ttl_seconds:
            return None
        return detail


class _FakeDigestResultCacheRepository:
    def __init__(self):
        self.storage = {}
        self.get_valid_calls = []
        self.upserts = []

    def get_valid(self, kind, now):
        self.get_valid_calls.append((kind, now))
        cache = self.storage.get(kind)
        if cache is None:
            return None
        if getattr(cache, "expires_at", None) <= now:
            return None
        return cache

    def upsert(self, cache):
        self.storage[cache.kind] = cache
        self.upserts.append(cache)

    def get_latest(self, kind):
        return self.storage.get(kind)


class _FakeMessageBuilder:
    def __init__(self):
        self.digests = []

    def build_digest_post(self, digest):
        self.digests.append(digest)
        return type(
            "RichTextPost",
            (),
            {
                "title": "🚀 {0}｜{1}".format(digest.title, digest.window),
                "markdown": "1. **{0}**".format(
                    digest.entries[0].full_name if digest.entries else "empty"
                ),
            },
        )()


class _FakeSummaryLocalizer:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.calls = []

    async def localize(self, text):
        self.calls.append(text)
        return self.mapping.get(text, text)


class _FakeFeishuClient:
    def __init__(self):
        self.chat_id = "default-chat"
        self.sent_texts = []
        self.sent_posts = []
        self.replies = []
        self.reactions_added = []
        self.reactions_removed = []
        self.bot_info_calls = 0
        self.closed = False

    async def get_bot_info(self):
        self.bot_info_calls += 1
        return {"open_id": "ou_bot", "app_name": "Repo Pulse"}

    async def send_text(self, text, receive_id=None):
        self.sent_texts.append((receive_id, text))

    async def send_post(self, title, markdown, receive_id=None):
        self.sent_posts.append((receive_id, title, markdown))

    async def reply_text(self, receive_id, text):
        self.replies.append((receive_id, text))

    async def add_reaction(self, message_id, emoji_type):
        self.reactions_added.append((message_id, emoji_type))
        return {"data": {"reaction_id": "reaction-1"}}

    async def remove_reaction(self, message_id, reaction_id):
        self.reactions_removed.append((message_id, reaction_id))

    async def close(self):
        self.closed = True


class _FakeDetailOrchestrator:
    def __init__(self):
        self.calls = []

    async def generate(self, full_name, repo_url, research_run_id):
        self.calls.append((full_name, repo_url, research_run_id))
        return ProjectDetailCache(
            full_name=full_name,
            doc_url="https://feishu.cn/docx/generated",
            summary_markdown=(
                "# {0} 项目详情\n\n"
                "## 项目简介\n"
                "这是一个 agent 平台。\n\n"
                "## 为什么最近火\n"
                "社区增长很快。\n\n"
                "## 是否适合我\n"
                "适合：平台团队。\n"
                "不适合：完全离线环境。\n\n"
                "## 是否能快速试玩\n"
                "结论：可以快速本地试玩（预计耗时：3-10 分钟）\n\n"
                "## 最短体验路径\n"
                "1. **安装依赖**：运行 `uv sync`。（预期：依赖安装完成；来源：README / Quick Start）\n"
                "2. **启动示例**：运行 `uv run python examples/demo.py`。（预期：终端输出 successful response；来源：README / Quick Start）\n\n"
                "## 前置条件与外部依赖\n"
                "- **Python 3.11+**：示例运行依赖 Python 环境。（来源：README / Quick Start）\n\n"
                "## 常见阻塞与失败信号\n"
                "- 成功信号：示例命令输出 successful response。\n"
                "- 阻塞：**缺少 API Key**：未设置环境变量会导致示例启动失败。（来源：README / Troubleshooting）\n\n"
                "## 最佳实践\n"
                "- 先跑最小 demo\n\n"
                "## 局限与风险\n"
                "- 依赖外部模型接口\n\n"
                "## 参考资料与引用链接\n"
                "- [Repo]({1})\n\n"
                "## 生成元数据\n"
                "- provider: openai\n"
            ).format(full_name, repo_url),
            citations_json="[]",
            updated_at=datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc),
        )


class _SlowDetailOrchestrator(_FakeDetailOrchestrator):
    def __init__(self, delay=0.05):
        super().__init__()
        self.delay = delay

    async def generate(self, full_name, repo_url, research_run_id):
        self.calls.append((full_name, repo_url, research_run_id))
        await asyncio.sleep(self.delay)
        return await super().generate(full_name, repo_url, research_run_id)


class _FailingDiscoveryService(_FakeDiscoveryService):
    async def collect_candidates(self, now):
        self.calls.append(now)
        raise RuntimeError("github unavailable")


class _FakeDigestJob:
    def __init__(self):
        self.calls = []

    async def run(self, now=None, receive_id=None, top_k=None, pre_generate=True):
        self.calls.append((now, receive_id, top_k, pre_generate))


class _FakeScheduler:
    def __init__(self):
        self.started = 0
        self.shutdown_calls = []

    def start(self):
        self.started += 1

    def shutdown(self, wait=False):
        self.shutdown_calls.append(wait)


class _FakeLongConnectionClient:
    def __init__(self):
        self.start_calls = []
        self.stop_calls = 0

    def start(self, loop=None):
        self.start_calls.append(loop)

    def stop(self):
        self.stop_calls += 1


class _FakeDigestDispatcher:
    def __init__(self):
        self.calls = []

    async def run(self, kind, receive_id=None, top_k=None):
        self.calls.append((kind, receive_id, top_k))


def _candidate(
    full_name,
    stars,
    forks,
    topics,
    description,
    *,
    pushed_at=None,
    created_at=None,
    discovery_sources=None,
    is_template=False,
):
    owner, name = full_name.split("/", 1)
    return RepositoryCandidate(
        full_name=full_name,
        name=name,
        owner=owner,
        description=description,
        html_url="https://github.com/{0}".format(full_name),
        language="Python",
        topics=topics,
        stars=stars,
        forks=forks,
        watchers=10,
        pushed_at=pushed_at or datetime(2026, 4, 14, 8, 0, tzinfo=timezone.utc),
        created_at=created_at,
        discovery_sources=discovery_sources or ["active_topic"],
        is_template=is_template,
    )


def _baseline(full_name, stars, forks, *, captured_at=None):
    return RepositorySnapshot(
        full_name=full_name,
        captured_at=captured_at or datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc),
        stars=stars,
        forks=forks,
        watchers=8,
        language="Python",
        topics_csv="ai,agents",
    )


@pytest.mark.asyncio
async def test_digest_pipeline_ranks_candidates_localizes_summary_sends_post_and_tracks_repo_urls():
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier

    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    candidates = [
        _candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework"),
        _candidate("acme/template", 220, 20, ["ai", "template"], "Template repo"),
    ]
    discovery = _FakeDiscoveryService(candidates=candidates)
    snapshots = _FakeSnapshotRepository(
        baselines={
            "acme/agent": _baseline("acme/agent", 120, 15),
            "acme/template": _baseline("acme/template", 200, 18),
        }
    )
    details = _FakeDetailRepository(
        cached={
            "acme/agent": ProjectDetailCache(
                full_name="acme/agent",
                doc_url="https://feishu.cn/docx/cached-agent",
                summary_markdown="# cached",
                citations_json="[]",
                updated_at=now,
            )
        }
    )
    message_builder = _FakeMessageBuilder()
    localizer = _FakeSummaryLocalizer(
        mapping={"Agent framework": "面向任务自动化的 Agent 框架"}
    )
    feishu = _FakeFeishuClient()
    orchestrator = _FakeDetailOrchestrator()
    pipeline = DigestPipeline(
        discovery_service=discovery,
        snapshot_repository=snapshots,
        detail_repository=details,
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=message_builder,
        summary_localizer=localizer,
        feishu_client=feishu,
        detail_orchestrator=orchestrator,
        top_k=1,
        topic_exclude=["template"],
    )

    ranked = await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now,
    )

    assert ranked == ["acme/agent"]
    assert discovery.calls == [now]
    assert [snapshot.full_name for snapshot in snapshots.saved] == [
        "acme/agent",
        "acme/template",
    ]
    assert localizer.calls == ["Agent framework"]
    assert message_builder.digests[0].entries[0].summary == "面向任务自动化的 Agent 框架"
    assert message_builder.digests[0].entries[0].doc_url == "https://feishu.cn/docx/cached-agent"
    assert feishu.sent_posts == [
        (None, "🚀 GitHub 热门日榜｜24h", "1. **acme/agent**")
    ]


@pytest.mark.asyncio
async def test_digest_pipeline_skips_default_push_when_no_receive_id_and_no_default_chat(caplog):
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier

    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    discovery = _FakeDiscoveryService(
        candidates=[_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]
    )
    feishu = _FakeFeishuClient()
    feishu.chat_id = ""
    pipeline = DigestPipeline(
        discovery_service=discovery,
        snapshot_repository=_FakeSnapshotRepository(
            baselines={"acme/agent": _baseline("acme/agent", 120, 15)}
        ),
        detail_repository=_FakeDetailRepository(),
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=_FakeMessageBuilder(),
        summary_localizer=_FakeSummaryLocalizer(),
        feishu_client=feishu,
        detail_orchestrator=_FakeDetailOrchestrator(),
        top_k=1,
    )

    with caplog.at_level(logging.INFO):
        ranked = await pipeline.run_digest(
            DigestRequest(
                kind="daily",
                title="GitHub 热门日榜",
                window="24h",
                window_hours=24,
                top_k=1,
            ),
            now,
        )

    assert ranked == []
    assert discovery.calls == []
    assert feishu.sent_posts == []
    assert "Skipping daily digest push because no receive_id or default Feishu targets are configured" in caplog.text


@pytest.mark.asyncio
async def test_digest_pipeline_broadcasts_to_all_default_chat_ids():
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier

    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    pipeline = DigestPipeline(
        discovery_service=_FakeDiscoveryService(
            candidates=[_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]
        ),
        snapshot_repository=_FakeSnapshotRepository(
            baselines={"acme/agent": _baseline("acme/agent", 120, 15)}
        ),
        detail_repository=_FakeDetailRepository(),
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=_FakeMessageBuilder(),
        summary_localizer=_FakeSummaryLocalizer(),
        feishu_client=_FakeFeishuClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        top_k=1,
        default_receive_ids=["oc_chat_a", "oc_chat_b"],
    )

    ranked = await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now,
    )

    assert ranked == ["acme/agent"]
    assert pipeline.feishu_client.sent_posts == [
        ("oc_chat_a", "🚀 GitHub 热门日榜｜24h", "1. **acme/agent**"),
        ("oc_chat_b", "🚀 GitHub 热门日榜｜24h", "1. **acme/agent**"),
    ]


@pytest.mark.asyncio
async def test_digest_pipeline_daily_uses_only_24h_baseline_and_real_reason_lines():
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier

    now = datetime(2026, 4, 15, 9, 30, tzinfo=timezone.utc)
    candidate = _candidate(
        "acme/agent",
        180,
        25,
        ["ai", "agents"],
        "Agent framework",
        pushed_at=now - timedelta(hours=2),
        created_at=now - timedelta(days=12),
        discovery_sources=["new_hot"],
    )
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)
    snapshots = _FakeSnapshotRepository(
        baselines={
            ("acme/agent", cutoff_24h): _baseline(
                "acme/agent",
                120,
                15,
                captured_at=cutoff_24h,
            ),
            ("acme/agent", cutoff_7d): _baseline(
                "acme/agent",
                80,
                10,
                captured_at=cutoff_7d,
            ),
        }
    )
    pipeline = DigestPipeline(
        discovery_service=_FakeDiscoveryService(candidates=[candidate]),
        snapshot_repository=snapshots,
        detail_repository=_FakeDetailRepository(),
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=_FakeMessageBuilder(),
        summary_localizer=_FakeSummaryLocalizer(),
        feishu_client=_FakeFeishuClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        top_k=1,
    )

    ranked = await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now,
    )

    assert ranked == ["acme/agent"]
    assert snapshots.latest_before_many_calls == [("acme/agent", [cutoff_24h])]
    reason_lines = pipeline.message_builder.digests[0].entries[0].reason_lines
    assert any("24h Stars +60" in line for line in reason_lines)
    assert any("相对增长" in line for line in reason_lines)
    assert any("最近 24h 内仍有代码更新" in line for line in reason_lines)
    assert all("7d" not in line for line in reason_lines)
    assert all("watcher" not in line.lower() for line in reason_lines)


@pytest.mark.asyncio
async def test_digest_pipeline_weekly_uses_7d_and_24h_baselines_and_still_heating_reason():
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier

    now = datetime(2026, 4, 15, 9, 30, tzinfo=timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)
    candidate = _candidate(
        "acme/trend",
        320,
        40,
        ["ai", "agents"],
        "Trend framework",
        pushed_at=now - timedelta(hours=18),
        created_at=now - timedelta(days=45),
        discovery_sources=["established_mover"],
    )
    snapshots = _FakeSnapshotRepository(
        baselines={
            ("acme/trend", cutoff_7d): _baseline(
                "acme/trend",
                220,
                30,
                captured_at=cutoff_7d,
            ),
            ("acme/trend", cutoff_24h): _baseline(
                "acme/trend",
                295,
                39,
                captured_at=cutoff_24h,
            ),
        }
    )
    pipeline = DigestPipeline(
        discovery_service=_FakeDiscoveryService(candidates=[candidate]),
        snapshot_repository=snapshots,
        detail_repository=_FakeDetailRepository(),
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=_FakeMessageBuilder(),
        summary_localizer=_FakeSummaryLocalizer(),
        feishu_client=_FakeFeishuClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        top_k=1,
    )

    ranked = await pipeline.run_digest(
        DigestRequest(
            kind="weekly",
            title="GitHub 热门周榜",
            window="7d",
            window_hours=24 * 7,
            top_k=1,
        ),
        now,
    )

    assert ranked == ["acme/trend"]
    assert snapshots.latest_before_many_calls == [("acme/trend", [cutoff_7d, cutoff_24h])]
    reason_lines = pipeline.message_builder.digests[0].entries[0].reason_lines
    assert any("7d Stars +100" in line for line in reason_lines)
    assert any("近 24h 仍在增长" in line for line in reason_lines)
    assert any("最近 72h 内仍有代码更新" in line for line in reason_lines)
    assert all("watcher" not in line.lower() for line in reason_lines)


@pytest.mark.asyncio
async def test_digest_pipeline_pre_generate_details_uses_last_ranked_repo_urls():
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier

    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    candidate = _candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")
    pipeline = DigestPipeline(
        discovery_service=_FakeDiscoveryService(candidates=[candidate]),
        snapshot_repository=_FakeSnapshotRepository(
            baselines={"acme/agent": _baseline("acme/agent", 120, 15)}
        ),
        detail_repository=_FakeDetailRepository(),
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=_FakeMessageBuilder(),
        summary_localizer=_FakeSummaryLocalizer(),
        feishu_client=_FakeFeishuClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        top_k=1,
    )

    await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now,
    )
    await pipeline.pre_generate_details(["acme/agent"])

    assert len(pipeline.detail_orchestrator.calls) == 1
    full_name, repo_url, research_run_id = pipeline.detail_orchestrator.calls[0]
    assert full_name == "acme/agent"
    assert repo_url == "https://github.com/acme/agent"
    assert research_run_id


@pytest.mark.asyncio
async def test_digest_pipeline_uses_cached_digest_within_daily_ttl():
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier

    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    discovery = _FakeDiscoveryService(
        candidates=[_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]
    )
    cache_repo = _FakeDigestResultCacheRepository()
    pipeline = DigestPipeline(
        discovery_service=discovery,
        snapshot_repository=_FakeSnapshotRepository(
            baselines={"acme/agent": _baseline("acme/agent", 120, 15)}
        ),
        detail_repository=_FakeDetailRepository(),
        digest_cache_repository=cache_repo,
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=_FakeMessageBuilder(),
        summary_localizer=_FakeSummaryLocalizer(),
        feishu_client=_FakeFeishuClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        top_k=10,
        max_cached_entries=10,
        cache_ttl_by_kind={"daily": 7200, "weekly": 86400},
    )

    first = await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now,
    )
    discovery.candidates = [_candidate("acme/other", 999, 99, ["ai"], "Changed")]
    second = await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now.replace(hour=10, minute=30),
    )

    assert first == ["acme/agent"]
    assert second == ["acme/agent"]
    assert discovery.calls == [now]
    assert len(cache_repo.upserts) == 1
    assert len(pipeline.feishu_client.sent_posts) == 2
    assert pipeline.feishu_client.sent_posts[0] == pipeline.feishu_client.sent_posts[1]


@pytest.mark.asyncio
async def test_digest_pipeline_recomputes_after_cache_expiry_and_uses_weekly_ttl():
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier

    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    discovery = _FakeDiscoveryService(
        candidates=[_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]
    )
    cache_repo = _FakeDigestResultCacheRepository()
    pipeline = DigestPipeline(
        discovery_service=discovery,
        snapshot_repository=_FakeSnapshotRepository(
            baselines={"acme/agent": _baseline("acme/agent", 120, 15)}
        ),
        detail_repository=_FakeDetailRepository(),
        digest_cache_repository=cache_repo,
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=_FakeMessageBuilder(),
        summary_localizer=_FakeSummaryLocalizer(),
        feishu_client=_FakeFeishuClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        top_k=10,
        max_cached_entries=10,
        cache_ttl_by_kind={"daily": 7200, "weekly": 86400},
    )

    await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now,
    )
    await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now.replace(hour=12, minute=31),
    )

    await pipeline.run_digest(
        DigestRequest(
            kind="weekly",
            title="GitHub 热门周榜",
            window="7d",
            window_hours=24 * 7,
            top_k=1,
        ),
        now,
    )
    await pipeline.run_digest(
        DigestRequest(
            kind="weekly",
            title="GitHub 热门周榜",
            window="7d",
            window_hours=24 * 7,
            top_k=1,
        ),
        now.replace(hour=21, minute=30),
    )

    assert discovery.calls == [
        now,
        now.replace(hour=12, minute=31),
        now,
    ]


@pytest.mark.asyncio
async def test_digest_pipeline_skips_pre_generation_when_digest_cache_hit():
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier

    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    pipeline = DigestPipeline(
        discovery_service=_FakeDiscoveryService(
            candidates=[_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]
        ),
        snapshot_repository=_FakeSnapshotRepository(
            baselines={"acme/agent": _baseline("acme/agent", 120, 15)}
        ),
        detail_repository=_FakeDetailRepository(),
        digest_cache_repository=_FakeDigestResultCacheRepository(),
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=_FakeMessageBuilder(),
        summary_localizer=_FakeSummaryLocalizer(),
        feishu_client=_FakeFeishuClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        top_k=10,
        max_cached_entries=10,
        cache_ttl_by_kind={"daily": 7200, "weekly": 86400},
    )

    ranked = await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now,
    )
    await pipeline.pre_generate_details(ranked)
    first_call_count = len(pipeline.detail_orchestrator.calls)

    ranked_again = await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now.replace(hour=10, minute=0),
    )
    await pipeline.pre_generate_details(ranked_again)

    assert first_call_count == 1
    assert len(pipeline.detail_orchestrator.calls) == 1


@pytest.mark.asyncio
async def test_digest_job_pre_generates_details_before_first_post_build():
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier
    from repo_pulse.scheduler import DigestJob

    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    message_builder = _FakeMessageBuilder()
    pipeline = DigestPipeline(
        discovery_service=_FakeDiscoveryService(
            candidates=[_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]
        ),
        snapshot_repository=_FakeSnapshotRepository(
            baselines={"acme/agent": _baseline("acme/agent", 120, 15)}
        ),
        detail_repository=_FakeDetailRepository(),
        digest_cache_repository=_FakeDigestResultCacheRepository(),
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=message_builder,
        summary_localizer=_FakeSummaryLocalizer(),
        feishu_client=_FakeFeishuClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        top_k=10,
        max_cached_entries=10,
        cache_ttl_by_kind={"daily": 7200, "weekly": 86400},
    )
    job = DigestJob(
        pipeline=pipeline,
        digest_request=DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        pregen_top_n=1,
    )

    await job.run(now=now, pre_generate=True)

    assert message_builder.digests[0].entries[0].doc_url == "https://feishu.cn/docx/generated"


@pytest.mark.asyncio
async def test_digest_pipeline_falls_back_to_latest_cache_when_refresh_fails():
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier

    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    cache_repo = _FakeDigestResultCacheRepository()
    pipeline = DigestPipeline(
        discovery_service=_FakeDiscoveryService(
            candidates=[_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]
        ),
        snapshot_repository=_FakeSnapshotRepository(
            baselines={"acme/agent": _baseline("acme/agent", 120, 15)}
        ),
        detail_repository=_FakeDetailRepository(),
        digest_cache_repository=cache_repo,
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=_FakeMessageBuilder(),
        summary_localizer=_FakeSummaryLocalizer(),
        feishu_client=_FakeFeishuClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        top_k=10,
        max_cached_entries=10,
        cache_ttl_by_kind={"daily": 3600, "weekly": 86400},
    )

    first = await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now,
    )
    pipeline.discovery_service = _FailingDiscoveryService(candidates=[])

    second = await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now.replace(hour=11),
    )

    assert first == ["acme/agent"]
    assert second == ["acme/agent"]


@pytest.mark.asyncio
async def test_digest_pipeline_ignores_incompatible_cached_digest_schema():
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.models import DigestResultCache
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier

    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    cache_repo = _FakeDigestResultCacheRepository()
    cache_repo.storage["daily"] = DigestResultCache(
        kind="daily",
        digest_json='{"title":"GitHub 热门日榜","window":"24h","entries":[{"full_name":"acme/agent","repo_url":"https://github.com/acme/agent","unknown_field":"boom"}],"generated_at":"2026-04-14T08:30:00+00:00"}',
        generated_at=now,
        expires_at=now.replace(hour=10, minute=30),
    )
    discovery = _FakeDiscoveryService(
        candidates=[_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]
    )
    pipeline = DigestPipeline(
        discovery_service=discovery,
        snapshot_repository=_FakeSnapshotRepository(
            baselines={"acme/agent": _baseline("acme/agent", 120, 15)}
        ),
        detail_repository=_FakeDetailRepository(),
        digest_cache_repository=cache_repo,
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=_FakeMessageBuilder(),
        summary_localizer=_FakeSummaryLocalizer(),
        feishu_client=_FakeFeishuClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        top_k=10,
        max_cached_entries=10,
        cache_ttl_by_kind={"daily": 7200, "weekly": 86400},
    )

    ranked = await pipeline.run_digest(
        DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=1,
        ),
        now,
    )

    assert ranked == ["acme/agent"]
    assert discovery.calls == [now]


@pytest.mark.asyncio
async def test_digest_pipeline_deduplicates_concurrent_refreshes():
    from repo_pulse.digest.service import DigestPipeline
    from repo_pulse.ranking.scoring import RankingService
    from repo_pulse.ranking.topics import TopicClassifier

    now = datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc)
    discovery = _FakeDiscoveryService(
        candidates=[_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]
    )
    pipeline = DigestPipeline(
        discovery_service=discovery,
        snapshot_repository=_FakeSnapshotRepository(
            baselines={"acme/agent": _baseline("acme/agent", 120, 15)}
        ),
        detail_repository=_FakeDetailRepository(),
        digest_cache_repository=_FakeDigestResultCacheRepository(),
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=_FakeMessageBuilder(),
        summary_localizer=_FakeSummaryLocalizer(),
        feishu_client=_FakeFeishuClient(),
        detail_orchestrator=_SlowDetailOrchestrator(),
        top_k=10,
        max_cached_entries=10,
        cache_ttl_by_kind={"daily": 7200, "weekly": 86400},
    )

    await asyncio.gather(
        pipeline.run_digest(
            DigestRequest(
                kind="daily",
                title="GitHub 热门日榜",
                window="24h",
                window_hours=24,
                top_k=1,
            ),
            now,
        ),
        pipeline.run_digest(
            DigestRequest(
                kind="daily",
                title="GitHub 热门日榜",
                window="24h",
                window_hours=24,
                top_k=1,
            ),
            now,
        ),
    )

    assert discovery.calls == [now]


@pytest.mark.asyncio
async def test_runtime_container_handles_event_and_replies_with_detail_summary():
    from repo_pulse import runtime
    from repo_pulse.runtime import DetailRequestHandler

    class _FakeGitHubClient:
        def __init__(self):
            self.calls = []

        async def search_repositories(self, query, per_page=1):
            self.calls.append((query, per_page))
            return [_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]

    github = _FakeGitHubClient()
    feishu = _FakeFeishuClient()
    orchestrator = _FakeDetailOrchestrator()
    digest_dispatcher = _FakeDigestDispatcher()
    handler = DetailRequestHandler(
        github_client=github,
        detail_orchestrator=orchestrator,
        feishu_client=feishu,
        digest_dispatcher=digest_dispatcher,
        manual_digest_default_top_k=5,
        manual_digest_max_top_k=10,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {
                    "text": "/a acme agent",
                    "message_id": "om-msg-1",
                },
                "chat_id": "chat-1",
            }
        }
    )

    assert github.calls == [("acme agent archived:false", 1)]
    assert len(orchestrator.calls) == 1
    full_name, repo_url, research_run_id = orchestrator.calls[0]
    assert full_name == "acme/agent"
    assert repo_url == "https://github.com/acme/agent"
    assert research_run_id
    assert digest_dispatcher.calls == []
    assert feishu.reactions_added == [("om-msg-1", "Typing")]
    assert feishu.reactions_removed == [("om-msg-1", "reaction-1")]
    assert feishu.sent_posts[0][0] == "chat-1"
    assert feishu.sent_posts[0][1] == "📌 acme/agent"
    assert "**是什么**" in feishu.sent_posts[0][2]
    assert "这是一个 agent 平台。" in feishu.sent_posts[0][2]
    assert "**为什么最近火**" in feishu.sent_posts[0][2]
    assert "社区增长很快。" in feishu.sent_posts[0][2]
    assert "**是否能快速试玩**" in feishu.sent_posts[0][2]
    assert "可以快速本地试玩" in feishu.sent_posts[0][2]
    assert "**3分钟试玩路径**" in feishu.sent_posts[0][2]
    assert "安装依赖" in feishu.sent_posts[0][2]
    assert "**适合谁**" in feishu.sent_posts[0][2]
    assert "平台团队。" in feishu.sent_posts[0][2]
    assert "**主要风险**" in feishu.sent_posts[0][2]
    assert "缺少 API Key" in feishu.sent_posts[0][2]
    assert "**文档链接 + 仓库链接**" in feishu.sent_posts[0][2]
    assert "[文档](https://feishu.cn/docx/generated)" in feishu.sent_posts[0][2]
    assert "[仓库](https://github.com/acme/agent)" in feishu.sent_posts[0][2]


@pytest.mark.asyncio
async def test_runtime_container_handles_analyze_command_with_github_url():
    from repo_pulse.runtime import DetailRequestHandler

    class _UnusedGitHubClient:
        def __init__(self):
            self.calls = []
            self.repo_calls = []

        async def get_repository(self, full_name):
            self.repo_calls.append(full_name)
            return SimpleNamespace(
                full_name=full_name,
                html_url="https://github.com/{0}".format(full_name),
            )

        async def search_repositories(self, query, per_page=1):
            self.calls.append((query, per_page))
            return []

    github = _UnusedGitHubClient()
    feishu = _FakeFeishuClient()
    orchestrator = _FakeDetailOrchestrator()
    handler = DetailRequestHandler(
        github_client=github,
        detail_orchestrator=orchestrator,
        feishu_client=feishu,
        digest_dispatcher=None,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {
                    "text": "/a https://github.com/openai/openai-python",
                    "message_id": "om-msg-url",
                },
                "chat_id": "chat-url",
            }
        }
    )

    assert github.calls == []
    assert github.repo_calls == ["openai/openai-python"]
    assert len(orchestrator.calls) == 1
    full_name, repo_url, research_run_id = orchestrator.calls[0]
    assert full_name == "openai/openai-python"
    assert repo_url == "https://github.com/openai/openai-python"
    assert research_run_id
    assert feishu.sent_posts


@pytest.mark.asyncio
async def test_runtime_container_rejects_invalid_direct_full_name_before_research():
    from repo_pulse.runtime import DetailRequestHandler

    class _GitHubClient:
        async def get_repository(self, full_name):
            assert full_name == "missing/repo"
            return None

        async def search_repositories(self, query, per_page=1):
            raise AssertionError("search_repositories should not be used for direct full_name")

    feishu = _FakeFeishuClient()
    handler = DetailRequestHandler(
        github_client=_GitHubClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        feishu_client=feishu,
        digest_dispatcher=None,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {"text": "/a missing/repo", "message_id": "om-invalid"},
                "chat_id": "chat-invalid",
            }
        }
    )

    assert feishu.replies == [("chat-invalid", "未找到与「missing/repo」匹配的 GitHub 仓库。")]


@pytest.mark.asyncio
async def test_runtime_container_handles_direct_full_name_resolve_error():
    from repo_pulse.runtime import DetailRequestHandler

    class _GitHubClient:
        def __init__(self):
            self.search_calls = []

        async def get_repository(self, full_name):
            assert full_name == "acme/agent"
            raise RuntimeError("github unavailable")

        async def search_repositories(self, query, per_page=1):
            self.search_calls.append((query, per_page))
            return []

    github = _GitHubClient()
    feishu = _FakeFeishuClient()
    handler = DetailRequestHandler(
        github_client=github,
        detail_orchestrator=_FakeDetailOrchestrator(),
        feishu_client=feishu,
        digest_dispatcher=None,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {"text": "/a acme/agent", "message_id": "om-direct-error"},
                "chat_id": "chat-direct-error",
            }
        }
    )

    assert github.search_calls == []
    assert feishu.replies
    assert feishu.replies[0][0] == "chat-direct-error"
    assert "github unavailable" in feishu.replies[0][1]


@pytest.mark.asyncio
async def test_runtime_logs_detail_request_and_passes_research_run_id(caplog):
    from repo_pulse.runtime import DetailRequestHandler

    class _GitHubClient:
        async def search_repositories(self, query, per_page=1):
            del query, per_page
            return [_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]

    class _ObservingDetailOrchestrator:
        def __init__(self):
            self.calls = []

        async def generate(self, full_name, repo_url, research_run_id):
            self.calls.append((full_name, repo_url, research_run_id))
            return ProjectDetailCache(
                full_name=full_name,
                doc_url="https://feishu.cn/docx/generated",
                summary_markdown="## 项目简介\nok",
                citations_json="[]",
                updated_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
            )

    caplog.set_level(logging.INFO)
    orchestrator = _ObservingDetailOrchestrator()
    handler = DetailRequestHandler(
        github_client=_GitHubClient(),
        detail_orchestrator=orchestrator,
        feishu_client=_FakeFeishuClient(),
        digest_dispatcher=None,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {"text": "/a acme agent", "message_id": "om-msg-1"},
                "chat_id": "chat-1",
            }
        }
    )

    full_name, repo_url, research_run_id = orchestrator.calls[0]
    assert full_name == "acme/agent"
    assert repo_url == "https://github.com/acme/agent"
    assert research_run_id
    event_payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]
    assert any(
        payload["event"] == "detail.request.received"
        and payload["research_run_id"] == research_run_id
        for payload in event_payloads
    )


@pytest.mark.asyncio
async def test_runtime_logs_action_detail_request_and_passes_research_run_id(caplog):
    from repo_pulse.runtime import DetailRequestHandler

    class _GitHubClient:
        async def get_repository(self, full_name):
            return SimpleNamespace(
                full_name=full_name,
                html_url="https://github.com/{0}".format(full_name),
            )

    class _ObservingDetailOrchestrator:
        def __init__(self):
            self.calls = []

        async def generate(self, full_name, repo_url, research_run_id):
            self.calls.append((full_name, repo_url, research_run_id))
            return ProjectDetailCache(
                full_name=full_name,
                doc_url="https://feishu.cn/docx/generated",
                summary_markdown="## 项目简介\nok",
                citations_json="[]",
                updated_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
            )

    caplog.set_level(logging.INFO)
    orchestrator = _ObservingDetailOrchestrator()
    handler = DetailRequestHandler(
        github_client=_GitHubClient(),
        detail_orchestrator=orchestrator,
        feishu_client=_FakeFeishuClient(),
        digest_dispatcher=None,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_action(
        {
            "chat_id": "chat-id",
            "action": {
                "value": {
                    "repo": "acme/agent",
                    "detail_action_value": "https://github.com/acme/agent",
                }
            },
        }
    )

    _, _, research_run_id = orchestrator.calls[0]
    payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]
    assert any(
        payload["event"] == "detail.request.received"
        and payload["research_run_id"] == research_run_id
        for payload in payloads
    )


@pytest.mark.asyncio
async def test_runtime_action_with_trusted_repo_url_bypasses_github_validation():
    from repo_pulse.runtime import DetailRequestHandler

    class _FailingGitHubClient:
        async def get_repository(self, full_name):
            raise RuntimeError("github unavailable")

        async def search_repositories(self, query, per_page=1):
            del query, per_page
            return []

    feishu = _FakeFeishuClient()
    orchestrator = _FakeDetailOrchestrator()
    handler = DetailRequestHandler(
        github_client=_FailingGitHubClient(),
        detail_orchestrator=orchestrator,
        feishu_client=feishu,
        digest_dispatcher=None,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_action(
        {
            "chat_id": "chat-trusted",
            "action": {
                "value": {
                    "repo": "acme/agent",
                    "detail_action_value": "https://github.com/acme/agent",
                }
            },
        }
    )

    assert len(orchestrator.calls) == 1
    assert feishu.sent_posts
    assert feishu.replies == []


@pytest.mark.asyncio
async def test_runtime_action_with_trusted_repo_url_survives_evidence_fetch_failures():
    from repo_pulse.details.orchestrator import DetailOrchestrator
    from repo_pulse.research.base import ResearchResult
    from repo_pulse.runtime import DetailRequestHandler

    class _FailingGitHubClient:
        async def get_repository(self, full_name):
            raise RuntimeError("github unavailable")

        async def search_repositories(self, query, per_page=1):
            del query, per_page
            return []

    class _FailingEvidenceBuilder:
        async def build(self, full_name):
            assert full_name == "acme/agent"
            raise RuntimeError("github unavailable")

    class _CapturingResearchProvider:
        def __init__(self):
            self.calls = []

        async def research(self, request):
            self.calls.append(request)
            return ResearchResult(
                what_it_is="项目简介",
                why_now="热度原因",
                fit_for="平台团队",
                not_for="离线环境",
                trial_verdict="can_run_locally",
                trial_requirements=[
                    {
                        "label": "Python 3.11+",
                        "detail": "运行示例依赖 Python 环境。",
                        "source": "README / Quick Start",
                    }
                ],
                trial_time_estimate="3-10 分钟",
                quickstart_steps=[
                    {
                        "label": "启动 demo",
                        "action": "运行 `uv run python examples/demo.py`。",
                        "expected_result": "终端输出 successful response。",
                        "source": "README / Quick Start",
                    }
                ],
                success_signal="示例命令输出 successful response。",
                common_blockers=[
                    {
                        "label": "缺少 API Key",
                        "detail": "未设置环境变量会导致示例失败。",
                        "source": "README / Troubleshooting",
                    }
                ],
                risks=[],
            )

    class _DetailRepository:
        def __init__(self):
            self.storage = {}

        def get(self, full_name):
            return self.storage.get(full_name)

        def get_valid(self, full_name, now, ttl_seconds):
            detail = self.get(full_name)
            if detail is None or ttl_seconds <= 0:
                return None
            if (now - detail.updated_at).total_seconds() >= ttl_seconds:
                return None
            return detail

        def upsert(self, detail):
            self.storage[detail.full_name] = detail

    class _DocsClient:
        async def upsert_project_doc(self, full_name, markdown, existing_doc_url=None):
            del full_name, markdown, existing_doc_url
            return "https://feishu.cn/docx/trusted"

    research_provider = _CapturingResearchProvider()
    orchestrator = DetailOrchestrator(
        detail_repository=_DetailRepository(),
        research_provider=research_provider,
        docs_client=_DocsClient(),
        evidence_builder=_FailingEvidenceBuilder(),
    )
    feishu = _FakeFeishuClient()
    handler = DetailRequestHandler(
        github_client=_FailingGitHubClient(),
        detail_orchestrator=orchestrator,
        feishu_client=feishu,
        digest_dispatcher=None,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_action(
        {
            "chat_id": "chat-trusted",
            "action": {
                "value": {
                    "repo": "acme/agent",
                    "detail_action_value": "https://github.com/acme/agent",
                }
            },
        }
    )

    assert len(research_provider.calls) == 1
    assert research_provider.calls[0].evidence.full_name == "acme/agent"
    assert research_provider.calls[0].evidence.repo_url == "https://github.com/acme/agent"
    assert feishu.sent_posts
    assert feishu.replies == []


@pytest.mark.asyncio
async def test_runtime_container_handles_manual_digest_command_with_clamped_top_k():
    from repo_pulse.runtime import DetailRequestHandler

    class _UnusedGitHubClient:
        def __init__(self):
            self.calls = []

        async def search_repositories(self, query, per_page=1):
            self.calls.append((query, per_page))
            return []

    github = _UnusedGitHubClient()
    feishu = _FakeFeishuClient()
    orchestrator = _FakeDetailOrchestrator()
    digest_dispatcher = _FakeDigestDispatcher()
    handler = DetailRequestHandler(
        github_client=github,
        detail_orchestrator=orchestrator,
        feishu_client=feishu,
        digest_dispatcher=digest_dispatcher,
        manual_digest_default_top_k=5,
        manual_digest_max_top_k=10,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {
                    "text": "/d 20",
                    "message_id": "om-msg-2",
                },
                "chat_id": "chat-2",
            }
        }
    )

    assert digest_dispatcher.calls == [("daily", "chat-2", 10)]
    assert github.calls == []
    assert orchestrator.calls == []
    assert feishu.replies == []
    assert feishu.reactions_added == [("om-msg-2", "Typing")]
    assert feishu.reactions_removed == [("om-msg-2", "reaction-1")]


@pytest.mark.asyncio
async def test_runtime_container_handles_help_command_with_help_text():
    from repo_pulse.runtime import DetailRequestHandler

    class _UnusedGitHubClient:
        async def search_repositories(self, query, per_page=1):
            del query, per_page
            return []

    feishu = _FakeFeishuClient()
    handler = DetailRequestHandler(
        github_client=_UnusedGitHubClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        feishu_client=feishu,
        digest_dispatcher=None,
        manual_digest_default_top_k=5,
        manual_digest_max_top_k=10,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {
                    "text": "/h",
                    "message_id": "om-msg-help",
                },
                "chat_id": "chat-help",
            }
        }
    )

    assert feishu.replies == []
    assert feishu.sent_texts == []
    assert feishu.sent_posts and feishu.sent_posts[0][0] == "chat-help"
    assert feishu.sent_posts[0][1] == "🤖 使用帮助"
    assert "/a <repo|url|keyword>" in feishu.sent_posts[0][2]
    assert "5. 关于我" in feishu.sent_posts[0][2]
    assert _ABOUT_DOC_URL in feishu.sent_posts[0][2]
    assert feishu.reactions_added == [("om-msg-help", "Typing")]
    assert feishu.reactions_removed == [("om-msg-help", "reaction-1")]


@pytest.mark.asyncio
async def test_runtime_container_handles_weekly_digest_command_with_processing_reaction():
    from repo_pulse.runtime import DetailRequestHandler

    class _UnusedGitHubClient:
        async def search_repositories(self, query, per_page=1):
            del query, per_page
            return []

    feishu = _FakeFeishuClient()
    digest_dispatcher = _FakeDigestDispatcher()
    handler = DetailRequestHandler(
        github_client=_UnusedGitHubClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        feishu_client=feishu,
        digest_dispatcher=digest_dispatcher,
        manual_digest_default_top_k=5,
        manual_digest_max_top_k=10,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {
                    "text": "/w 8",
                    "message_id": "om-msg-weekly",
                },
                "chat_id": "chat-weekly",
            }
        }
    )

    assert digest_dispatcher.calls == [("weekly", "chat-weekly", 8)]
    assert feishu.reactions_added == [("om-msg-weekly", "Typing")]
    assert feishu.reactions_removed == [("om-msg-weekly", "reaction-1")]


@pytest.mark.asyncio
async def test_runtime_container_handles_legacy_mention_daily_command():
    from repo_pulse.runtime import DetailRequestHandler

    class _UnusedGitHubClient:
        def __init__(self):
            self.calls = []

        async def search_repositories(self, query, per_page=1):
            self.calls.append((query, per_page))
            return []

    github = _UnusedGitHubClient()
    feishu = _FakeFeishuClient()
    orchestrator = _FakeDetailOrchestrator()
    digest_dispatcher = _FakeDigestDispatcher()
    handler = DetailRequestHandler(
        github_client=github,
        detail_orchestrator=orchestrator,
        feishu_client=feishu,
        digest_dispatcher=digest_dispatcher,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {
                    "text": '<at user_id="ou_bot">Repo Pulse</at> 日榜 top 20',
                    "message_id": "om-msg-legacy",
                    "mentions": [
                        {
                            "key": "@_user_1",
                            "id": {"open_id": "ou_bot"},
                            "name": "Repo Pulse",
                        }
                    ],
                },
                "chat_id": "chat-legacy",
            }
        }
    )

    assert github.calls == []
    assert orchestrator.calls == []
    assert digest_dispatcher.calls == [("daily", "chat-legacy", 10)]
    assert feishu.sent_texts == []
    assert feishu.sent_posts == []
    assert feishu.reactions_added == [("om-msg-legacy", "Typing")]
    assert feishu.reactions_removed == [("om-msg-legacy", "reaction-1")]


@pytest.mark.asyncio
async def test_runtime_container_ignores_group_member_mention_commands():
    from repo_pulse.runtime import DetailRequestHandler

    class _UnusedGitHubClient:
        def __init__(self):
            self.calls = []

        async def search_repositories(self, query, per_page=1):
            self.calls.append((query, per_page))
            return []

    github = _UnusedGitHubClient()
    feishu = _FakeFeishuClient()
    orchestrator = _FakeDetailOrchestrator()
    digest_dispatcher = _FakeDigestDispatcher()
    handler = DetailRequestHandler(
        github_client=github,
        detail_orchestrator=orchestrator,
        feishu_client=feishu,
        digest_dispatcher=digest_dispatcher,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {
                    "text": '<at user_id="ou_user">张三</at> 日榜',
                    "message_id": "om-msg-user-mention",
                    "mentions": [
                        {
                            "key": "@_user_1",
                            "id": {"open_id": "ou_user"},
                            "name": "张三",
                        }
                    ],
                },
                "chat_id": "chat-user-mention",
            }
        }
    )

    assert github.calls == []
    assert orchestrator.calls == []
    assert digest_dispatcher.calls == []
    assert feishu.sent_texts == []
    assert feishu.sent_posts == []
    assert feishu.reactions_added == []
    assert feishu.reactions_removed == []


@pytest.mark.asyncio
async def test_runtime_container_replies_to_invalid_bot_mention_command_without_reaction():
    from repo_pulse.runtime import DetailRequestHandler

    class _UnusedGitHubClient:
        def __init__(self):
            self.calls = []

        async def search_repositories(self, query, per_page=1):
            self.calls.append((query, per_page))
            return []

    github = _UnusedGitHubClient()
    feishu = _FakeFeishuClient()
    orchestrator = _FakeDetailOrchestrator()
    digest_dispatcher = _FakeDigestDispatcher()
    handler = DetailRequestHandler(
        github_client=github,
        detail_orchestrator=orchestrator,
        feishu_client=feishu,
        digest_dispatcher=digest_dispatcher,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {
                    "text": '<at user_id="ou_bot">Repo Pulse</at> /unknown',
                    "message_id": "om-msg-invalid-command",
                    "mentions": [
                        {
                            "key": "@_user_1",
                            "id": {"open_id": "ou_bot"},
                            "name": "Repo Pulse",
                        }
                    ],
                },
                "chat_id": "chat-invalid-command",
            }
        }
    )

    assert github.calls == []
    assert orchestrator.calls == []
    assert digest_dispatcher.calls == []
    assert feishu.sent_texts == [
        ("chat-invalid-command", "不支持的命令，可使用 /help 查看帮助。")
    ]
    assert feishu.sent_posts == []
    assert feishu.reactions_added == []
    assert feishu.reactions_removed == []


@pytest.mark.asyncio
async def test_runtime_container_can_disable_legacy_mention_commands():
    from repo_pulse.runtime import DetailRequestHandler

    class _UnusedGitHubClient:
        def __init__(self):
            self.calls = []

        async def search_repositories(self, query, per_page=1):
            self.calls.append((query, per_page))
            return []

    github = _UnusedGitHubClient()
    feishu = _FakeFeishuClient()
    orchestrator = _FakeDetailOrchestrator()
    digest_dispatcher = _FakeDigestDispatcher()
    handler = DetailRequestHandler(
        github_client=github,
        detail_orchestrator=orchestrator,
        feishu_client=feishu,
        digest_dispatcher=digest_dispatcher,
        about_doc_url=_ABOUT_DOC_URL,
        allow_legacy_mention_commands=False,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {
                    "text": '<at user_id="ou_bot">Repo Pulse</at> 日榜',
                    "message_id": "om-msg-legacy-off",
                    "mentions": [
                        {
                            "key": "@_user_1",
                            "id": {"open_id": "ou_bot"},
                            "name": "Repo Pulse",
                        }
                    ],
                },
                "chat_id": "chat-legacy-off",
            }
        }
    )

    assert github.calls == []
    assert orchestrator.calls == []
    assert digest_dispatcher.calls == []
    assert feishu.sent_texts == []
    assert feishu.sent_posts == []
    assert feishu.reactions_added == []
    assert feishu.reactions_removed == []


@pytest.mark.asyncio
async def test_runtime_container_removes_reaction_even_when_detail_generation_fails():
    from repo_pulse.runtime import DetailRequestHandler

    class _FailingDetailOrchestrator:
        async def generate(self, full_name, repo_url, research_run_id):
            del full_name, repo_url, research_run_id
            raise RuntimeError("boom")

    class _GitHubClient:
        async def search_repositories(self, query, per_page=1):
            del query, per_page
            return [_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]

    feishu = _FakeFeishuClient()
    handler = DetailRequestHandler(
        github_client=_GitHubClient(),
        detail_orchestrator=_FailingDetailOrchestrator(),
        feishu_client=feishu,
        digest_dispatcher=None,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {
                    "text": "/a acme agent",
                    "message_id": "om-msg-fail",
                },
                "chat_id": "chat-fail",
            }
        }
    )

    assert feishu.replies == [("chat-fail", "生成 acme/agent 详情失败：boom")]
    assert feishu.reactions_added == [("om-msg-fail", "Typing")]
    assert feishu.reactions_removed == [("om-msg-fail", "reaction-1")]


@pytest.mark.asyncio
async def test_runtime_container_keeps_typing_reaction_visible_for_minimum_duration(monkeypatch):
    from repo_pulse import runtime
    from repo_pulse.runtime import DetailRequestHandler

    class _GitHubClient:
        async def search_repositories(self, query, per_page=1):
            del query, per_page
            return [_candidate("acme/agent", 180, 25, ["ai", "agents"], "Agent framework")]

    feishu = _FakeFeishuClient()
    sleep_calls = []
    monotonic_values = iter([100.0, 100.2])

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: next(monotonic_values)))
    monkeypatch.setattr(runtime, "asyncio", SimpleNamespace(sleep=_fake_sleep))

    handler = DetailRequestHandler(
        github_client=_GitHubClient(),
        detail_orchestrator=_FakeDetailOrchestrator(),
        feishu_client=feishu,
        digest_dispatcher=None,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_event(
        {
            "event": {
                "message": {
                    "text": "/a acme agent",
                    "message_id": "om-msg-visible",
                },
                "chat_id": "chat-visible",
            }
        }
    )

    assert feishu.reactions_added == [("om-msg-visible", "Typing")]
    assert sleep_calls == [
        pytest.approx(runtime.PROCESSING_REACTION_MIN_VISIBLE_SECONDS - 0.2)
    ]


@pytest.mark.asyncio
async def test_runtime_container_prefers_chat_id_over_open_chat_id_for_action_replies():
    from repo_pulse.runtime import DetailRequestHandler

    class _UnusedGitHubClient:
        async def get_repository(self, full_name):
            return SimpleNamespace(
                full_name=full_name,
                html_url="https://github.com/{0}".format(full_name),
            )

        async def search_repositories(self, query, per_page=1):
            del query, per_page
            return []

    feishu = _FakeFeishuClient()
    orchestrator = _FakeDetailOrchestrator()
    handler = DetailRequestHandler(
        github_client=_UnusedGitHubClient(),
        detail_orchestrator=orchestrator,
        feishu_client=feishu,
        digest_dispatcher=None,
        about_doc_url=_ABOUT_DOC_URL,
    )

    await handler.handle_action(
        {
            "open_chat_id": "open-chat-id",
            "chat_id": "chat-id",
            "action": {
                "value": {
                    "repo": "acme/agent",
                    "detail_action_value": "https://github.com/acme/agent",
                }
            },
        }
    )

    assert len(orchestrator.calls) == 1
    full_name, repo_url, research_run_id = orchestrator.calls[0]
    assert full_name == "acme/agent"
    assert repo_url == "https://github.com/acme/agent"
    assert research_run_id
    assert feishu.sent_posts
    assert feishu.sent_posts[0][0] == "chat-id"


@pytest.mark.asyncio
async def test_runtime_container_startup_shutdown_and_manual_digest():
    from repo_pulse.runtime import RuntimeContainer

    init_db_calls = []
    daily_job = _FakeDigestJob()
    weekly_job = _FakeDigestJob()
    scheduler = _FakeScheduler()
    feishu = _FakeFeishuClient()
    orchestrator = _FakeDetailOrchestrator()
    long_connection = _FakeLongConnectionClient()
    container = RuntimeContainer(
        engine="engine-1",
        init_db_func=lambda engine: init_db_calls.append(engine),
        scheduler=scheduler,
        digest_jobs={"daily": daily_job, "weekly": weekly_job},
        feishu_client=feishu,
        detail_handler=None,
        long_connection_client=long_connection,
    )

    await container.startup()
    await container.run_digest_now(kind="weekly", top_k=7, receive_id="chat-x", pre_generate=False)
    await container.shutdown()

    assert init_db_calls == ["engine-1"]
    assert scheduler.started == 1
    assert len(long_connection.start_calls) == 1
    assert long_connection.start_calls[0] is not None
    assert daily_job.calls == []
    assert weekly_job.calls == [(None, "chat-x", 7, False)]
    assert scheduler.shutdown_calls == [False]
    assert long_connection.stop_calls == 1
    assert feishu.closed is True
    assert orchestrator.calls == []


def test_create_app_without_explicit_container_builds_runtime_container(monkeypatch):
    from repo_pulse.main import create_app

    class _FactoryContainer:
        def __init__(self):
            self.started = 0
            self.stopped = 0

        async def startup(self):
            self.started += 1

        async def shutdown(self):
            self.stopped += 1

        async def handle_event(self, payload):
            del payload

        async def handle_action(self, payload):
            del payload

        async def run_digest_now(self, kind="daily", top_k=None, receive_id=None, pre_generate=True):
            del kind, top_k, receive_id, pre_generate
            return None

    built = _FactoryContainer()
    monkeypatch.setattr("repo_pulse.main.create_runtime_container", lambda: built)

    app = create_app()

    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert app.state.container is built
        assert built.started == 1

    assert built.stopped == 1


def test_create_runtime_container_uses_real_feishu_docs_client():
    from repo_pulse.config import Settings
    from repo_pulse.feishu.docs import FeishuDocsClient
    from repo_pulse.feishu.ws_client import FeishuLongConnectionClient
    from repo_pulse.runtime import create_runtime_container

    container = create_runtime_container(
        Settings(
            feishu_app_id="app-id",
            feishu_app_secret="app-secret",
            feishu_chat_ids=["chat-id"],
            feishu_about_doc_url=_ABOUT_DOC_URL,
            database_url="sqlite:///:memory:",
            _env_file=None,
        )
    )

    assert isinstance(container.detail_handler.detail_orchestrator.docs_client, FeishuDocsClient)
    assert isinstance(container.long_connection_client, FeishuLongConnectionClient)
    assert container.detail_handler.about_doc_url == _ABOUT_DOC_URL


def test_create_runtime_container_ignores_legacy_feishu_chat_id_env(monkeypatch):
    from repo_pulse.config import Settings
    from repo_pulse.runtime import create_runtime_container

    monkeypatch.setenv("FEISHU_CHAT_ID", "oc_legacy")
    monkeypatch.delenv("FEISHU_CHAT_IDS", raising=False)

    container = create_runtime_container(
        Settings(
            feishu_app_id="app-id",
            feishu_app_secret="app-secret",
            feishu_about_doc_url=_ABOUT_DOC_URL,
            database_url="sqlite:///:memory:",
            _env_file=None,
        )
    )

    assert container.digest_jobs["daily"].pipeline.default_receive_ids == []
    assert container.digest_jobs["weekly"].pipeline.default_receive_ids == []


def test_create_runtime_container_can_disable_feishu_long_connection():
    from repo_pulse.config import Settings
    from repo_pulse.runtime import create_runtime_container

    container = create_runtime_container(
        Settings(
            feishu_app_id="app-id",
            feishu_app_secret="app-secret",
            feishu_chat_ids=["chat-id"],
            feishu_about_doc_url=_ABOUT_DOC_URL,
            database_url="sqlite:///:memory:",
            feishu_long_connection_enabled=False,
            _env_file=None,
        )
    )

    assert container.long_connection_client is None
    assert container.detail_handler.allow_legacy_mention_commands is True


def test_create_runtime_container_can_disable_legacy_mention_commands():
    from repo_pulse.config import Settings
    from repo_pulse.runtime import create_runtime_container

    container = create_runtime_container(
        Settings(
            feishu_app_id="app-id",
            feishu_app_secret="app-secret",
            feishu_chat_ids=["chat-id"],
            feishu_about_doc_url=_ABOUT_DOC_URL,
            database_url="sqlite:///:memory:",
            feishu_allow_legacy_mention_commands=False,
            _env_file=None,
        )
    )

    assert container.detail_handler.allow_legacy_mention_commands is False


def test_create_runtime_container_passes_detail_cache_and_evidence_limits(monkeypatch):
    from repo_pulse.config import Settings
    from repo_pulse.runtime import create_runtime_container

    settings = Settings(
        feishu_app_id="app-id",
        feishu_app_secret="app-secret",
        feishu_chat_ids=["chat-id"],
        feishu_about_doc_url=_ABOUT_DOC_URL,
        database_url="sqlite:///:memory:",
        detail_cache_ttl_seconds=7200,
        research_readme_char_limit=3000,
        research_release_limit=2,
        research_commit_limit=4,
        dashscope_api_key="dash-key",
        _env_file=None,
    )

    container = create_runtime_container(settings)

    orchestrator = container.detail_handler.detail_orchestrator
    assert orchestrator.cache_ttl_seconds == 7200
    assert orchestrator.evidence_builder.readme_char_limit == 3000
    assert orchestrator.evidence_builder.release_limit == 2
    assert orchestrator.evidence_builder.commit_limit == 4


def test_build_research_provider_uses_dashscope_when_selected(monkeypatch):
    from repo_pulse.config import Settings
    from repo_pulse.runtime import _build_research_provider

    captured = {}

    class _FakeProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "repo_pulse.runtime.DashScopeDeepResearchProvider",
        _FakeProvider,
    )
    monkeypatch.setattr(
        "repo_pulse.runtime._build_dashscope_generation_client",
        lambda base_url: "dash-client:{0}".format(base_url),
    )

    provider, closers = _build_research_provider(
        Settings(
            feishu_app_id="app-id",
            feishu_app_secret="app-secret",
            feishu_chat_ids=["chat-id"],
            feishu_about_doc_url=_ABOUT_DOC_URL,
            research_provider="dashscope",
            dashscope_api_key="dash-key",
            dashscope_base_url="https://dashscope.aliyuncs.com/api/v1",
            _env_file=None,
        )
    )

    assert isinstance(provider, _FakeProvider)
    assert closers == []
    assert captured["api_key"] == "dash-key"
    assert captured["research_client"] == "dash-client:https://dashscope.aliyuncs.com/api/v1"
    assert captured["structurer_client"] == "dash-client:https://dashscope.aliyuncs.com/api/v1"
    assert captured["research_timeout_seconds"] == 600
    assert captured["structurer_timeout_seconds"] == 600
    assert captured["research_max_retries"] == 2
    assert captured["research_retry_backoff_seconds"] == 1


def test_build_research_provider_returns_disabled_when_dashscope_key_missing():
    from repo_pulse.config import Settings
    from repo_pulse.runtime import DisabledResearchProvider, _build_research_provider

    provider, closers = _build_research_provider(
        Settings(
            feishu_app_id="app-id",
            feishu_app_secret="app-secret",
            feishu_chat_ids=["chat-id"],
            feishu_about_doc_url=_ABOUT_DOC_URL,
            research_provider="dashscope",
            _env_file=None,
        )
    )

    assert isinstance(provider, DisabledResearchProvider)
    assert closers == []
    assert provider.reason == "DASHSCOPE_API_KEY 未配置，无法生成项目详情。"


def test_build_summary_localizer_uses_dashscope_when_key_present(monkeypatch):
    from repo_pulse.config import Settings
    from repo_pulse.runtime import _build_summary_localizer

    captured = {}

    class _FakeLocalizer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "repo_pulse.runtime.DashScopeSummaryLocalizer",
        _FakeLocalizer,
    )
    monkeypatch.setattr(
        "repo_pulse.runtime._build_dashscope_generation_client",
        lambda base_url: "dash-client:{0}".format(base_url),
    )

    localizer = _build_summary_localizer(
        Settings(
            feishu_app_id="app-id",
            feishu_app_secret="app-secret",
            feishu_chat_ids=["chat-id"],
            feishu_about_doc_url=_ABOUT_DOC_URL,
            research_provider="dashscope",
            dashscope_api_key="dash-key",
            dashscope_structurer_model="qwen-plus",
            dashscope_base_url="https://dashscope.aliyuncs.com/api/v1",
            _env_file=None,
        )
    )

    assert isinstance(localizer, _FakeLocalizer)
    assert captured["generation_client"] == "dash-client:https://dashscope.aliyuncs.com/api/v1"
    assert captured["api_key"] == "dash-key"
    assert captured["model"] == "qwen-plus"


@pytest.mark.asyncio
async def test_build_summary_localizer_returns_passthrough_when_dashscope_key_missing():
    from repo_pulse.config import Settings
    from repo_pulse.runtime import _build_summary_localizer

    localizer = _build_summary_localizer(
        Settings(
            feishu_app_id="app-id",
            feishu_app_secret="app-secret",
            feishu_chat_ids=["chat-id"],
            feishu_about_doc_url=_ABOUT_DOC_URL,
            research_provider="dashscope",
            _env_file=None,
        )
    )

    assert await localizer.localize("Agent framework") == "Agent framework"
