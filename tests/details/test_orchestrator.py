import asyncio
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

import pytest

from repo_pulse.models import ProjectDetailCache
from repo_pulse.research.base import (
    Citation,
    OnboardingFact,
    QuickstartStep,
    ResearchResult,
    TRIAL_VERDICT_CAN_RUN_LOCALLY,
)


class _FakeDetailRepository:
    def __init__(self, seeded=None, require_background_thread=False):
        self.storage = seeded or {}
        self.upserts = []
        self.require_background_thread = require_background_thread
        self.get_called_in_main_thread = None
        self.upsert_called_in_main_thread = None

    def get(self, full_name):
        is_main_thread = threading.current_thread() is threading.main_thread()
        self.get_called_in_main_thread = is_main_thread
        if self.require_background_thread and is_main_thread:
            raise RuntimeError("get() should run in worker thread")
        return self.storage.get(full_name)

    def get_valid(self, full_name, now, ttl_seconds):
        detail = self.get(full_name)
        if detail is None or ttl_seconds <= 0:
            return None
        if (now - detail.updated_at).total_seconds() >= ttl_seconds:
            return None
        return detail

    def upsert(self, detail):
        is_main_thread = threading.current_thread() is threading.main_thread()
        self.upsert_called_in_main_thread = is_main_thread
        if self.require_background_thread and is_main_thread:
            raise RuntimeError("upsert() should run in worker thread")
        self.storage[detail.full_name] = detail
        self.upserts.append(detail)


class _FakeResearchProvider:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def research(self, request):
        self.calls.append(request)
        return self.result


class _FakeDocsClient:
    def __init__(self, doc_url):
        self.doc_url = doc_url
        self.calls = []
        self.awaited = False

    async def upsert_project_doc(self, full_name, markdown, existing_doc_url=None):
        self.awaited = True
        self.calls.append(
            {
                "full_name": full_name,
                "markdown": markdown,
                "existing_doc_url": existing_doc_url,
            }
        )
        return self.doc_url


class _SlowResearchProvider(_FakeResearchProvider):
    def __init__(self, result, delay=0.05):
        super().__init__(result=result)
        self.delay = delay

    async def research(self, request):
        self.calls.append(request)
        await asyncio.sleep(self.delay)
        return self.result


def _onboarding_defaults() -> dict:
    return {
        "trial_verdict": TRIAL_VERDICT_CAN_RUN_LOCALLY,
        "trial_requirements": [
            OnboardingFact(
                label="Python 3.11+",
                detail="示例运行依赖 Python 环境。",
                source="README / Quick Start",
            )
        ],
        "trial_time_estimate": "3-10 分钟",
        "quickstart_steps": [
            QuickstartStep(
                label="安装依赖",
                action="运行 `uv sync`。",
                expected_result="依赖安装完成。",
                source="README / Quick Start",
            ),
            QuickstartStep(
                label="启动示例",
                action="运行 `uv run python examples/demo.py`。",
                expected_result="终端输出 successful response。",
                source="README / Quick Start",
            ),
        ],
        "success_signal": "示例命令输出 successful response。",
        "common_blockers": [
            OnboardingFact(
                label="缺少 API Key",
                detail="未设置环境变量会导致示例启动失败。",
                source="README / Troubleshooting",
            )
        ],
    }


def test_parse_repo_reference_extracts_repo_from_github_url():
    from repo_pulse.details.request_parser import parse_repo_reference

    assert (
        parse_repo_reference("请看 https://github.com/openai/openai-python/tree/main")
        == "openai/openai-python"
    )


def test_parse_repo_reference_normalizes_dot_git_suffix():
    from repo_pulse.details.request_parser import parse_repo_reference

    assert (
        parse_repo_reference("https://github.com/openai/openai-python.git")
        == "openai/openai-python"
    )


def test_parse_repo_reference_returns_cleaned_text_when_no_repo_url():
    from repo_pulse.details.request_parser import parse_repo_reference

    assert parse_repo_reference("  请帮我看看这个项目  ") == "请帮我看看这个项目"
    assert parse_repo_reference("   ") is None


def test_parse_slash_command_supports_aliases_default_top_k_and_cap():
    from repo_pulse.details.request_parser import parse_slash_command

    analyze = parse_slash_command("/analyze openai/openai-python", default_top_k=5, max_top_k=10)
    daily = parse_slash_command("/d", default_top_k=5, max_top_k=10)
    capped = parse_slash_command("/daily 20", default_top_k=5, max_top_k=10)
    weekly = parse_slash_command("/w 8", default_top_k=5, max_top_k=10)
    help_cmd = parse_slash_command("/help", default_top_k=5, max_top_k=10)

    assert analyze.is_slash is True
    assert analyze.command.kind == "analyze"
    assert analyze.command.argument == "openai/openai-python"
    assert daily.command.kind == "daily"
    assert daily.command.top_k == 5
    assert capped.command.kind == "daily"
    assert capped.command.top_k == 10
    assert weekly.command.kind == "weekly"
    assert weekly.command.top_k == 8
    assert help_cmd.command.kind == "help"


def test_parse_slash_command_rejects_legacy_and_invalid_syntax():
    from repo_pulse.details.request_parser import parse_slash_command

    legacy = parse_slash_command("@机器人 日榜", default_top_k=5, max_top_k=10)
    unknown = parse_slash_command("/unknown", default_top_k=5, max_top_k=10)
    missing_arg = parse_slash_command("/a", default_top_k=5, max_top_k=10)
    bad_top_k = parse_slash_command("/daily abc", default_top_k=5, max_top_k=10)

    assert legacy.is_slash is False
    assert legacy.command is None
    assert unknown.is_slash is True
    assert unknown.command is None
    assert "不支持的命令" in unknown.error
    assert missing_arg.command is None
    assert "请提供仓库名" in missing_arg.error
    assert bad_top_k.command is None
    assert "topN" in bad_top_k.error


def test_parse_message_command_supports_legacy_mentions_and_slash_passthrough():
    from repo_pulse.details.request_parser import parse_message_command

    legacy_daily = parse_message_command(
        "@张三 @任意机器人名 日榜 top 20",
        default_top_k=5,
        max_top_k=10,
        allow_legacy_mention_commands=True,
    )
    legacy_analyze = parse_message_command(
        '<at user_id="ou_user">张三</at> <at user_id="ou_bot">随便起的机器人名</at> openai/openai-python',
        default_top_k=5,
        max_top_k=10,
        allow_legacy_mention_commands=True,
    )
    slash_with_mention = parse_message_command(
        '<at user_id="ou_bot">随便起的机器人名</at> /help',
        default_top_k=5,
        max_top_k=10,
        allow_legacy_mention_commands=True,
    )

    assert legacy_daily.is_command is True
    assert legacy_daily.command is not None
    assert legacy_daily.command.kind == "daily"
    assert legacy_daily.command.top_k == 10
    assert legacy_analyze.is_command is True
    assert legacy_analyze.command is not None
    assert legacy_analyze.command.kind == "analyze"
    assert legacy_analyze.command.argument == "openai/openai-python"
    assert slash_with_mention.is_command is True
    assert slash_with_mention.command is not None
    assert slash_with_mention.command.kind == "help"


def test_parse_message_command_can_disable_legacy_mentions():
    from repo_pulse.details.request_parser import parse_message_command

    result = parse_message_command(
        "@任意机器人名 日榜",
        default_top_k=5,
        max_top_k=10,
        allow_legacy_mention_commands=False,
    )

    assert result.is_command is False
    assert result.command is None


def test_build_help_text_lists_all_supported_commands():
    from repo_pulse.details.request_parser import build_help_text

    help_text = build_help_text(
        default_top_k=5,
        max_top_k=10,
        about_doc_url="https://example.feishu.cn/docx/about-me",
    )

    assert "/a <repo|url|keyword>" in help_text
    assert "/analyze <repo|url|keyword>" in help_text
    assert "/d [topN]" in help_text
    assert "/daily [topN]" in help_text
    assert "/w [topN]" in help_text
    assert "/weekly [topN]" in help_text
    assert "/h" in help_text
    assert "/help" in help_text
    assert "5. 关于我" in help_text
    assert (
        "[关于我介绍](https://example.feishu.cn/docx/about-me)"
        in help_text
    )
    assert "@机器人" in help_text


def test_render_project_markdown_contains_required_sections():
    from repo_pulse.feishu.docs import render_project_markdown

    result = ResearchResult(
        what_it_is="一个用于自动化任务的项目。",
        why_now="最近社区活跃且版本更新频繁。",
        fit_for="适合希望快速验证 agent workflow 的团队。",
        not_for="不适合需要强事务一致性的核心交易链路。",
        **_onboarding_defaults(),
        best_practices=["先跑最小 demo", "加上监控与告警"],
        risks=["生态变化快，需要关注版本兼容性。"],
        citations=[Citation(title="README", url="https://github.com/acme/agent")],
        metadata={"provider": "openai", "model": "gpt-5", "generated_at": "2026-04-14T09:30:00Z"},
    )

    markdown = render_project_markdown("acme/agent", result)

    assert "项目简介" in markdown
    assert "为什么最近火" in markdown
    assert "是否适合我" in markdown
    assert "是否能快速试玩" in markdown
    assert "最短体验路径" in markdown
    assert "前置条件与外部依赖" in markdown
    assert "常见阻塞与失败信号" in markdown
    assert "最佳实践" in markdown
    assert "局限与风险" in markdown
    assert "参考资料与引用链接" in markdown
    assert "生成元数据" in markdown
    assert "https://github.com/acme/agent" in markdown


def test_render_project_markdown_uses_placeholders_for_empty_sections():
    from repo_pulse.feishu.docs import render_project_markdown

    result = ResearchResult(
        what_it_is="项目简介",
        why_now="关注原因",
        fit_for="适用人群",
        not_for="不适用人群",
        trial_verdict="insufficient_information",
        trial_requirements=[],
        trial_time_estimate="",
        quickstart_steps=[],
        success_signal="",
        common_blockers=[],
        best_practices=[],
        risks=[],
        citations=[],
        metadata={},
    )

    markdown = render_project_markdown("acme/agent", result)

    assert "- 暂无补充" in markdown
    assert "- 暂无明显补充风险" in markdown
    assert "- 暂无公开参考资料" in markdown
    assert "1. 信息不足以确认最短体验路径（来源：信息不足以确认）" in markdown
    assert "- 暂无明确前置条件或外部依赖" in markdown
    assert "- 暂未识别常见阻塞项" in markdown


@pytest.mark.asyncio
async def test_orchestrator_generate_creates_doc_and_updates_cache():
    from repo_pulse.details.orchestrator import DetailOrchestrator

    result = ResearchResult(
        what_it_is="一个用于自动化任务的项目。",
        why_now="最近社区活跃且版本更新频繁。",
        fit_for="适合平台工具团队。",
        not_for="不适合离线内网环境。",
        **_onboarding_defaults(),
        best_practices=["先跑最小 demo", "加上监控与告警"],
        risks=["依赖外部模型接口。"],
        citations=[
            Citation(
                title="README",
                url="https://github.com/acme/agent",
                snippet="Official documentation",
            )
        ],
        metadata={"provider": "openai", "model": "gpt-5", "generated_at": "2026-04-14T09:30:00Z"},
    )
    repository = _FakeDetailRepository(require_background_thread=True)
    research_provider = _FakeResearchProvider(result=result)
    docs_client = _FakeDocsClient(doc_url="https://feishu.cn/docx/abc123")
    orchestrator = DetailOrchestrator(
        detail_repository=repository,
        research_provider=research_provider,
        docs_client=docs_client,
    )

    detail = await orchestrator.generate(
        full_name="acme/agent",
        repo_url="https://github.com/acme/agent",
        research_run_id="run-1",
    )

    assert detail.full_name == "acme/agent"
    assert detail.doc_url == "https://feishu.cn/docx/abc123"
    assert "项目简介" in detail.summary_markdown
    assert "生成元数据" in detail.summary_markdown
    assert json.loads(detail.citations_json) == [
        {
            "title": "README",
            "url": "https://github.com/acme/agent",
            "snippet": "Official documentation",
        }
    ]
    assert detail.updated_at.tzinfo is timezone.utc
    assert len(research_provider.calls) == 1
    assert docs_client.awaited is True
    assert len(docs_client.calls) == 1
    assert repository.get_called_in_main_thread is False
    assert repository.upsert_called_in_main_thread is False
    assert repository.upserts and repository.upserts[0] is detail


@pytest.mark.asyncio
async def test_orchestrator_generate_serializes_citations_robustly():
    from repo_pulse.details.orchestrator import DetailOrchestrator

    result = ResearchResult(
        what_it_is="一个用于自动化任务的项目。",
        why_now="最近社区活跃且版本更新频繁。",
        fit_for="适合平台工具团队。",
        not_for="不适合离线内网环境。",
        **_onboarding_defaults(),
        best_practices=[],
        risks=[],
        citations=[
            {"title": "README", "url": "https://github.com/acme/agent"},
            object(),
        ],
        metadata={"provider": "openai", "model": "gpt-5", "generated_at": "2026-04-14T09:30:00Z"},
    )
    repository = _FakeDetailRepository(require_background_thread=True)
    research_provider = _FakeResearchProvider(result=result)
    docs_client = _FakeDocsClient(doc_url="https://feishu.cn/docx/robust")
    orchestrator = DetailOrchestrator(
        detail_repository=repository,
        research_provider=research_provider,
        docs_client=docs_client,
    )

    detail = await orchestrator.generate(
        full_name="acme/agent",
        repo_url="https://github.com/acme/agent",
        research_run_id="run-2",
    )

    assert json.loads(detail.citations_json) == [
        {"title": "README", "url": "https://github.com/acme/agent", "snippet": None}
    ]


@pytest.mark.asyncio
async def test_orchestrator_generate_returns_cached_detail_without_research():
    from repo_pulse.details.orchestrator import DetailOrchestrator

    cached = ProjectDetailCache(
        full_name="acme/agent",
        doc_url="https://feishu.cn/docx/cached",
        summary_markdown="cached markdown",
        citations_json="[]",
        updated_at=datetime.now(timezone.utc),
    )
    repository = _FakeDetailRepository(seeded={"acme/agent": cached})
    research_provider = _FakeResearchProvider(
        result=ResearchResult(
            what_it_is="unused",
            why_now="unused",
            fit_for="unused",
            not_for="unused",
            **_onboarding_defaults(),
            risks=[],
        )
    )
    docs_client = _FakeDocsClient(doc_url="https://feishu.cn/docx/new")
    orchestrator = DetailOrchestrator(
        detail_repository=repository,
        research_provider=research_provider,
        docs_client=docs_client,
    )

    detail = await orchestrator.generate(
        full_name="acme/agent",
        repo_url="https://github.com/acme/agent",
        research_run_id="run-cached",
    )

    assert detail is cached
    assert research_provider.calls == []
    assert docs_client.awaited is False
    assert docs_client.calls == []
    assert repository.upserts == []


@pytest.mark.asyncio
async def test_orchestrator_generate_researches_again_when_cached_detail_is_expired():
    from repo_pulse.details.orchestrator import DetailOrchestrator

    now = datetime.now(timezone.utc)
    stale = ProjectDetailCache(
        full_name="acme/agent",
        doc_url="https://feishu.cn/docx/stale",
        summary_markdown="stale markdown",
        citations_json="[]",
        updated_at=now - timedelta(hours=3),
    )
    repository = _FakeDetailRepository(seeded={"acme/agent": stale})
    research_provider = _FakeResearchProvider(
        result=ResearchResult(
            what_it_is="新的简介",
            why_now="新的热度原因",
            fit_for="适合平台团队。",
            not_for="不适合离线环境。",
            **_onboarding_defaults(),
            risks=[],
        )
    )
    docs_client = _FakeDocsClient(doc_url="https://feishu.cn/docx/stale")
    orchestrator = DetailOrchestrator(
        detail_repository=repository,
        research_provider=research_provider,
        docs_client=docs_client,
        cache_ttl_seconds=7200,
    )

    detail = await orchestrator.generate(
        full_name="acme/agent",
        repo_url="https://github.com/acme/agent",
        research_run_id="run-expired",
    )

    assert detail.summary_markdown != "stale markdown"
    assert len(research_provider.calls) == 1
    assert docs_client.calls[0]["existing_doc_url"] == "https://feishu.cn/docx/stale"


@pytest.mark.asyncio
async def test_orchestrator_generate_deduplicates_concurrent_requests():
    from repo_pulse.details.orchestrator import DetailOrchestrator

    repository = _FakeDetailRepository()
    research_provider = _SlowResearchProvider(
        result=ResearchResult(
            what_it_is="一个用于自动化任务的项目。",
            why_now="最近社区活跃且版本更新频繁。",
            fit_for="适合平台工具团队。",
            not_for="不适合离线内网环境。",
            **_onboarding_defaults(),
            risks=[],
        )
    )
    docs_client = _FakeDocsClient(doc_url="https://feishu.cn/docx/once")
    orchestrator = DetailOrchestrator(
        detail_repository=repository,
        research_provider=research_provider,
        docs_client=docs_client,
        cache_ttl_seconds=7200,
    )

    first, second = await asyncio.gather(
        orchestrator.generate("acme/agent", "https://github.com/acme/agent", "run-a"),
        orchestrator.generate("acme/agent", "https://github.com/acme/agent", "run-b"),
    )

    assert first.doc_url == "https://feishu.cn/docx/once"
    assert second.doc_url == "https://feishu.cn/docx/once"
    assert len(research_provider.calls) == 1
    assert len(docs_client.calls) == 1


@pytest.mark.asyncio
async def test_orchestrator_logs_cache_miss_doc_sync_and_completion(caplog):
    from repo_pulse.details.orchestrator import DetailOrchestrator

    caplog.set_level(logging.INFO)
    repository = _FakeDetailRepository(require_background_thread=True)
    research_provider = _FakeResearchProvider(
        result=ResearchResult(
            what_it_is="一个用于自动化任务的项目。",
            why_now="最近社区活跃且版本更新频繁。",
            fit_for="适合平台工具团队。",
            not_for="不适合离线内网环境。",
            **_onboarding_defaults(),
            best_practices=["先跑最小 demo"],
            risks=["依赖外部模型接口。"],
            citations=[
                Citation(
                    title="README",
                    url="https://github.com/acme/agent",
                    snippet="Official documentation",
                )
            ],
        )
    )
    orchestrator = DetailOrchestrator(
        detail_repository=repository,
        research_provider=research_provider,
        docs_client=_FakeDocsClient(doc_url="https://feishu.cn/docx/abc123"),
    )

    await orchestrator.generate(
        full_name="acme/agent",
        repo_url="https://github.com/acme/agent",
        research_run_id="run-3",
    )

    payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]
    assert research_provider.calls[0].research_run_id == "run-3"
    assert any(
        payload["event"] == "detail.cache.miss"
        and payload["research_run_id"] == "run-3"
        for payload in payloads
    )
    assert any(
        payload["event"] == "detail.doc_sync.started"
        and payload["research_run_id"] == "run-3"
        for payload in payloads
    )
    assert any(
        payload["event"] == "detail.doc_sync.completed"
        and payload["research_run_id"] == "run-3"
        for payload in payloads
    )
    assert any(
        payload["event"] == "detail.completed"
        and payload["research_run_id"] == "run-3"
        and payload["citations_count"] == 1
        and payload["best_practices_count"] == 1
        and isinstance(payload["elapsed_ms"], int)
        for payload in payloads
    )


@pytest.mark.asyncio
async def test_orchestrator_logs_cache_hit_without_research(caplog):
    from repo_pulse.details.orchestrator import DetailOrchestrator

    caplog.set_level(logging.INFO)
    cached = ProjectDetailCache(
        full_name="acme/agent",
        doc_url="https://feishu.cn/docx/cached",
        summary_markdown="cached markdown",
        citations_json="[]",
        updated_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
    )
    orchestrator = DetailOrchestrator(
        detail_repository=_FakeDetailRepository(seeded={"acme/agent": cached}),
        research_provider=_FakeResearchProvider(
            result=ResearchResult(what_it_is="unused", why_now="unused")
        ),
        docs_client=_FakeDocsClient(doc_url="https://feishu.cn/docx/new"),
    )

    detail = await orchestrator.generate(
        full_name="acme/agent",
        repo_url="https://github.com/acme/agent",
        research_run_id="run-4",
    )

    assert detail is cached
    payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]
    assert any(
        payload["event"] == "detail.cache.hit"
        and payload["research_run_id"] == "run-4"
        and isinstance(payload["elapsed_ms"], int)
        for payload in payloads
    )
    assert not any(payload["event"] == "detail.doc_sync.started" for payload in payloads)


@pytest.mark.asyncio
async def test_orchestrator_logs_detail_failed_on_doc_sync_error(caplog):
    from repo_pulse.details.orchestrator import DetailOrchestrator

    class _FailingDocsClient:
        async def upsert_project_doc(self, full_name, markdown):
            del full_name, markdown
            raise RuntimeError("doc sync boom")

    caplog.set_level(logging.INFO)
    orchestrator = DetailOrchestrator(
        detail_repository=_FakeDetailRepository(),
        research_provider=_FakeResearchProvider(
            result=ResearchResult(
                what_it_is="一个用于自动化任务的项目。",
                why_now="最近社区活跃且版本更新频繁。",
                fit_for="适合平台工具团队。",
                not_for="不适合离线内网环境。",
                **_onboarding_defaults(),
                best_practices=[],
                risks=[],
            )
        ),
        docs_client=_FailingDocsClient(),
    )

    with pytest.raises(RuntimeError, match="doc sync boom"):
        await orchestrator.generate(
            full_name="acme/agent",
            repo_url="https://github.com/acme/agent",
            research_run_id="run-7",
        )

    payloads = [record.event_data for record in caplog.records if hasattr(record, "event_data")]
    assert any(
        payload["event"] == "detail.failed"
        and payload["research_run_id"] == "run-7"
        and payload["exception_type"] == "RuntimeError"
        and isinstance(payload["elapsed_ms"], int)
        for payload in payloads
    )


@pytest.mark.asyncio
async def test_orchestrator_passes_repository_evidence_to_research_provider():
    from repo_pulse.details.orchestrator import DetailOrchestrator
    from repo_pulse.research.evidence import RepositoryEvidence

    class _EvidenceBuilder:
        async def build(self, full_name):
            assert full_name == "acme/agent"
            return RepositoryEvidence(
                full_name="acme/agent",
                repo_url="https://github.com/acme/agent",
                description="Agent runtime",
                homepage="https://agent.acme.dev",
                language="Python",
                default_branch="main",
                topics=["ai", "agents"],
                readme_excerpt="Run demo first.",
                releases=["v1.2.0: Stability fixes"],
                recent_commits=["ship eval dashboard"],
                key_paths=["docs", "examples", "src"],
            )

    repository = _FakeDetailRepository()
    research_provider = _FakeResearchProvider(
        result=ResearchResult(
            what_it_is="项目简介",
            why_now="热度原因",
            fit_for="平台团队",
            not_for="离线环境",
            **_onboarding_defaults(),
            risks=[],
        )
    )
    orchestrator = DetailOrchestrator(
        detail_repository=repository,
        research_provider=research_provider,
        docs_client=_FakeDocsClient(doc_url="https://feishu.cn/docx/evidence"),
        evidence_builder=_EvidenceBuilder(),
    )

    await orchestrator.generate("acme/agent", "https://github.com/acme/agent", "run-evidence")

    assert research_provider.calls[0].evidence.readme_excerpt == "Run demo first."
    assert research_provider.calls[0].evidence.releases == ["v1.2.0: Stability fixes"]


@pytest.mark.asyncio
async def test_orchestrator_uses_minimal_evidence_when_builder_fails():
    from repo_pulse.details.orchestrator import DetailOrchestrator

    class _FailingEvidenceBuilder:
        async def build(self, full_name):
            assert full_name == "acme/agent"
            raise RuntimeError("github unavailable")

    research_provider = _FakeResearchProvider(
        result=ResearchResult(
            what_it_is="项目简介",
            why_now="热度原因",
            fit_for="平台团队",
            not_for="离线环境",
            **_onboarding_defaults(),
            risks=[],
        )
    )
    orchestrator = DetailOrchestrator(
        detail_repository=_FakeDetailRepository(),
        research_provider=research_provider,
        docs_client=_FakeDocsClient(doc_url="https://feishu.cn/docx/minimal-evidence"),
        evidence_builder=_FailingEvidenceBuilder(),
    )

    await orchestrator.generate("acme/agent", "https://github.com/acme/agent", "run-minimal")

    evidence = research_provider.calls[0].evidence
    assert evidence.full_name == "acme/agent"
    assert evidence.repo_url == "https://github.com/acme/agent"
    assert evidence.description is None
    assert evidence.readme_excerpt == ""
