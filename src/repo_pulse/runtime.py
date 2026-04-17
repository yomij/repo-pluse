import asyncio
import logging
import time
from typing import Any, Optional, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from repo_pulse.config import Settings, get_settings
from repo_pulse.db import build_engine, init_db
from repo_pulse.details.orchestrator import DetailOrchestrator
from repo_pulse.details.request_parser import (
    build_help_text,
    extract_message_text,
    parse_message_command,
    parse_repo_reference,
)
from repo_pulse.digest.localization import DashScopeSummaryLocalizer, PassthroughSummaryLocalizer
from repo_pulse.digest.service import DigestPipeline, DigestRequest
from repo_pulse.feishu.client import FeishuClient
from repo_pulse.feishu.docs import FeishuDocsClient
from repo_pulse.feishu.messages import MarkdownDigestBuilder
from repo_pulse.feishu.ws_client import FeishuLongConnectionClient
from repo_pulse.github.client import GitHubClient
from repo_pulse.github.discovery import DiscoveryService
from repo_pulse.observability import log_research_event
from repo_pulse.ranking.scoring import RankingService
from repo_pulse.ranking.topics import TopicClassifier
from repo_pulse.repositories import (
    DigestResultCacheRepository,
    ProjectDetailRepository,
    SnapshotRepository,
)
from repo_pulse.research.base import ResearchProvider, ResearchRequest
from repo_pulse.research.dashscope_provider import DashScopeDeepResearchProvider
from repo_pulse.research.evidence import RepositoryEvidenceBuilder
from repo_pulse.research.openai_provider import OpenAIResearchProvider
from repo_pulse.scheduler import DigestJob, build_digest_scheduler

logger = logging.getLogger(__name__)

PROCESSING_REACTION_EMOJI = "Typing"
PROCESSING_REACTION_MIN_VISIBLE_SECONDS = 1.5


class DisabledResearchProvider(ResearchProvider):
    def __init__(self, reason: str):
        self.reason = reason

    async def research(self, request: ResearchRequest):
        del request
        raise RuntimeError(self.reason)


class DigestDispatcher:
    def __init__(self, digest_jobs):
        self.digest_jobs = digest_jobs

    async def run(self, kind: str, receive_id: Optional[str] = None, top_k: Optional[int] = None):
        job = self.digest_jobs.get(kind)
        if job is None:
            raise ValueError("Unsupported digest kind: {0}".format(kind))
        await job.run(receive_id=receive_id, top_k=top_k, pre_generate=receive_id is None)


class DetailRequestHandler:
    def __init__(
        self,
        github_client,
        detail_orchestrator,
        feishu_client,
        about_doc_url: str,
        message_builder=None,
        digest_dispatcher=None,
        manual_digest_default_top_k: int = 5,
        manual_digest_max_top_k: int = 10,
        group_require_bot_mention: bool = True,
    ):
        self.github_client = github_client
        self.detail_orchestrator = detail_orchestrator
        self.feishu_client = feishu_client
        self.message_builder = message_builder or MarkdownDigestBuilder()
        self.digest_dispatcher = digest_dispatcher
        self.manual_digest_default_top_k = manual_digest_default_top_k
        self.manual_digest_max_top_k = manual_digest_max_top_k
        self.about_doc_url = about_doc_url
        self.group_require_bot_mention = group_require_bot_mention
        self._bot_open_id: Optional[str] = None
        self._bot_identity_loaded = False

    async def handle_event(self, payload):
        event = payload.get("event") or {}
        message = event.get("message") or {}
        receive_id = (
            event.get("chat_id")
            or payload.get("chat_id")
            or self.feishu_client.chat_id
        )
        message_text = extract_message_text(message)
        if not message_text:
            return

        message_id = message.get("message_id")
        chat_type = str(message.get("chat_type") or "").strip().lower()
        is_private_chat = chat_type == "p2p"
        mentions = message.get("mentions")
        bot_open_id = ""

        if not is_private_chat and self.group_require_bot_mention:
            bot_open_id = await self._get_bot_open_id()
            if not bot_open_id:
                return
            if _find_runtime_bot_mention(mentions, bot_open_id) is None:
                return
        elif mentions:
            bot_open_id = await self._get_bot_open_id()

        command_result = parse_message_command(
            message_text,
            default_top_k=self.manual_digest_default_top_k,
            max_top_k=self.manual_digest_max_top_k,
            mentions=mentions,
            bot_open_id=bot_open_id,
        )
        if not command_result.is_command:
            return

        if command_result.command is None:
            await self._handle_command(command_result, receive_id)
            return

        await self._execute_with_processing_reaction(
            message_id=message_id,
            action=lambda: self._handle_command(command_result, receive_id),
        )

    async def handle_action(self, payload):
        action = payload.get("action") or {}
        value = action.get("value") or {}
        full_name = value.get("repo")
        repo_url = value.get("detail_action_value")

        if not full_name and isinstance(repo_url, str):
            full_name = parse_repo_reference(repo_url)

        if not full_name:
            return

        receive_id = (
            payload.get("chat_id")
            or self.feishu_client.chat_id
        )
        await self._reply_detail(full_name, receive_id, repo_url=repo_url)

    async def _reply_detail(
        self, query: str, receive_id: str, repo_url: Optional[str] = None
    ) -> None:
        try:
            full_name, resolved_repo_url = await self._resolve_repo(query, repo_url)
        except Exception as exc:
            await self.feishu_client.reply_text(
                receive_id,
                "解析仓库「{0}」失败：{1}".format(query, exc),
            )
            return

        if not full_name:
            await self.feishu_client.reply_text(
                receive_id, "未找到与「{0}」匹配的 GitHub 仓库。".format(query)
            )
            return

        research_run_id = uuid4().hex
        log_research_event(
            logger,
            event="detail.request.received",
            status="started",
            research_run_id=research_run_id,
            repo_full_name=full_name,
            repo_url=resolved_repo_url,
            message="detail request accepted",
        )

        try:
            detail = await self.detail_orchestrator.generate(
                full_name,
                resolved_repo_url,
                research_run_id,
            )
        except Exception as exc:
            await self.feishu_client.reply_text(
                receive_id, "生成 {0} 详情失败：{1}".format(full_name, exc)
            )
            return

        post = self.message_builder.build_detail_post(detail, resolved_repo_url)
        await self.feishu_client.send_post(
            post.title,
            post.markdown,
            receive_id=receive_id,
        )

    async def _handle_command(
        self,
        command_result,
        receive_id: str,
    ) -> None:
        if command_result.command is None:
            await self.feishu_client.send_text(
                command_result.error or "命令格式不正确，可使用 /help 查看帮助。",
                receive_id=receive_id,
            )
            return

        command = command_result.command
        if command.kind == "help":
            await self.feishu_client.send_post(
                "🤖 使用帮助",
                build_help_text(
                    default_top_k=self.manual_digest_default_top_k,
                    max_top_k=self.manual_digest_max_top_k,
                    about_doc_url=self.about_doc_url,
                ),
                receive_id=receive_id,
            )
            return

        if command.kind in {"daily", "weekly"}:
            if self.digest_dispatcher is None:
                return
            await self.digest_dispatcher.run(
                command.kind,
                receive_id=receive_id,
                top_k=command.top_k,
            )
            return

        if command.kind == "analyze":
            await self._reply_detail(command.argument or "", receive_id)

    async def _execute_with_processing_reaction(self, message_id: Optional[str], action) -> None:
        reaction_id = None
        reaction_started_at = None
        if message_id:
            try:
                reaction_started_at = time.monotonic()
                payload = await self.feishu_client.add_reaction(message_id, PROCESSING_REACTION_EMOJI)
                reaction_id = ((payload or {}).get("data") or {}).get("reaction_id")
            except Exception as exc:
                logger.warning(
                    "Failed to add processing reaction for message %s: %s",
                    message_id,
                    exc,
                )
                reaction_id = None
                reaction_started_at = None

        try:
            await action()
        finally:
            if message_id and reaction_id:
                elapsed = 0.0
                if reaction_started_at is not None:
                    elapsed = time.monotonic() - reaction_started_at
                remaining = PROCESSING_REACTION_MIN_VISIBLE_SECONDS - elapsed
                if remaining > 0:
                    await asyncio.sleep(remaining)
                try:
                    await self.feishu_client.remove_reaction(message_id, reaction_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to remove processing reaction %s for message %s: %s",
                        reaction_id,
                        message_id,
                        exc,
                    )

    async def _get_bot_open_id(self) -> str:
        if self._bot_identity_loaded:
            return self._bot_open_id or ""

        self._bot_identity_loaded = True
        get_bot_info = getattr(self.feishu_client, "get_bot_info", None)
        if not callable(get_bot_info):
            return ""

        try:
            bot_info = await get_bot_info()
        except Exception as exc:
            logger.warning("Failed to fetch Feishu bot info: %s", exc)
            return ""

        self._bot_open_id = _extract_bot_open_id(bot_info)
        return self._bot_open_id or ""

    async def _resolve_repo(
        self, query: str, repo_url: Optional[str] = None
    ) -> tuple[Optional[str], Optional[str]]:
        normalized = (query or "").strip()
        if not normalized:
            return None, None

        if "github.com/" in normalized.lower():
            full_name = parse_repo_reference(normalized)
            if full_name:
                repository = await self.github_client.get_repository(full_name)
                if repository is None:
                    return None, None
                return repository.full_name, str(repository.html_url)

        if self._looks_like_full_name(normalized):
            if repo_url and repo_url.strip():
                return normalized, repo_url.strip()
            repository = await self.github_client.get_repository(normalized)
            if repository is None:
                return None, None
            return repository.full_name, str(repository.html_url)

        results = await self.github_client.search_repositories(
            query="{0} archived:false".format(normalized),
            per_page=1,
        )
        if not results:
            return None, None

        candidate = results[0]
        return candidate.full_name, str(candidate.html_url)

    @staticmethod
    def _looks_like_full_name(text: str) -> bool:
        return "/" in text and " " not in text

    @staticmethod
    def _repo_url_for(full_name: str) -> str:
        return "https://github.com/{0}".format(full_name)

class RuntimeContainer:
    def __init__(
        self,
        engine,
        init_db_func,
        scheduler,
        digest_jobs,
        feishu_client,
        detail_handler=None,
        long_connection_client=None,
        resource_closers: Optional[Sequence] = None,
    ):
        self.engine = engine
        self.init_db_func = init_db_func
        self.scheduler = scheduler
        self.digest_jobs = dict(digest_jobs or {})
        self.feishu_client = feishu_client
        self.detail_handler = detail_handler
        self.long_connection_client = long_connection_client
        self.resource_closers = list(resource_closers or [])
        self._started = False

    async def startup(self) -> None:
        self.init_db_func(self.engine)
        if self.scheduler is not None:
            self.scheduler.start()
        if self.long_connection_client is not None:
            self.long_connection_client.start(loop=asyncio.get_running_loop())
        self._started = True

    async def shutdown(self) -> None:
        if self.scheduler is not None and self._started:
            self.scheduler.shutdown(wait=False)
        if self.long_connection_client is not None and self._started:
            self.long_connection_client.stop()
        self._started = False

        closers = list(self.resource_closers)
        if not closers and hasattr(self.feishu_client, "close"):
            closers.append(self.feishu_client.close)
        for close in closers:
            result = close()
            if hasattr(result, "__await__"):
                await result

    async def handle_event(self, payload):
        if self.detail_handler is None:
            return
        await self.detail_handler.handle_event(payload)

    async def handle_action(self, payload):
        if self.detail_handler is None:
            return
        await self.detail_handler.handle_action(payload)

    async def run_digest_now(
        self,
        kind: str = "daily",
        top_k: Optional[int] = None,
        receive_id: Optional[str] = None,
        pre_generate: bool = True,
    ):
        job = self.digest_jobs[kind]
        await job.run(
            receive_id=receive_id,
            top_k=top_k,
            pre_generate=pre_generate,
        )


def create_runtime_container(settings: Optional[Settings] = None) -> RuntimeContainer:
    effective_settings = settings or get_settings()
    default_feishu_chat_ids = _resolve_default_feishu_chat_ids(effective_settings)
    primary_feishu_receive_id = default_feishu_chat_ids[0] if default_feishu_chat_ids else ""
    engine = build_engine(effective_settings.database_url)
    snapshot_repository = SnapshotRepository(engine)
    detail_repository = ProjectDetailRepository(engine)
    digest_cache_repository = DigestResultCacheRepository(engine)
    github_client = GitHubClient(token=effective_settings.github_token)
    feishu_client = FeishuClient(
        app_id=effective_settings.feishu_app_id,
        app_secret=effective_settings.feishu_app_secret,
        chat_id=primary_feishu_receive_id,
    )
    docs_client = FeishuDocsClient(
        app_id=effective_settings.feishu_app_id,
        app_secret=effective_settings.feishu_app_secret,
        folder_token=effective_settings.feishu_doc_folder_token,
    )
    message_builder = MarkdownDigestBuilder()
    summary_localizer = _build_summary_localizer(effective_settings)

    research_provider, resource_closers = _build_research_provider(effective_settings)
    evidence_builder = RepositoryEvidenceBuilder(
        github_client=github_client,
        readme_char_limit=effective_settings.research_readme_char_limit,
        release_limit=effective_settings.research_release_limit,
        commit_limit=effective_settings.research_commit_limit,
    )
    detail_orchestrator = DetailOrchestrator(
        detail_repository=detail_repository,
        research_provider=research_provider,
        docs_client=docs_client,
        evidence_builder=evidence_builder,
        cache_ttl_seconds=effective_settings.detail_cache_ttl_seconds,
    )
    digest_pipeline = DigestPipeline(
        discovery_service=DiscoveryService(
            client=github_client,
            include_topics=effective_settings.topic_include,
        ),
        snapshot_repository=snapshot_repository,
        detail_repository=detail_repository,
        digest_cache_repository=digest_cache_repository,
        ranking_service=RankingService(classifier=TopicClassifier()),
        message_builder=message_builder,
        summary_localizer=summary_localizer,
        feishu_client=feishu_client,
        detail_orchestrator=detail_orchestrator,
        top_k=effective_settings.digest_top_k,
        max_cached_entries=max(
            effective_settings.digest_top_k,
            effective_settings.manual_digest_max_top_k,
        ),
        cache_ttl_by_kind={
            "daily": effective_settings.daily_digest_cache_ttl_seconds,
            "weekly": effective_settings.weekly_digest_cache_ttl_seconds,
        },
        default_receive_ids=default_feishu_chat_ids,
        topic_exclude=effective_settings.topic_exclude,
    )
    daily_digest_job = DigestJob(
        pipeline=digest_pipeline,
        digest_request=DigestRequest(
            kind="daily",
            title="GitHub 热门日榜",
            window="24h",
            window_hours=24,
            top_k=effective_settings.digest_top_k,
        ),
        pregen_top_n=effective_settings.pregen_top_n,
    )
    weekly_digest_job = DigestJob(
        pipeline=digest_pipeline,
        digest_request=DigestRequest(
            kind="weekly",
            title="GitHub 热门周榜",
            window="7d",
            window_hours=24 * 7,
            top_k=effective_settings.digest_top_k,
        ),
        pregen_top_n=effective_settings.pregen_top_n,
    )
    digest_jobs = {
        "daily": daily_digest_job,
        "weekly": weekly_digest_job,
    }
    scheduler = build_digest_scheduler(
        daily_cron=effective_settings.daily_digest_cron,
        daily_job=daily_digest_job,
        weekly_cron=effective_settings.weekly_digest_cron,
        weekly_job=weekly_digest_job,
        scheduler_timezone=ZoneInfo(effective_settings.scheduler_timezone),
    )

    detail_handler = DetailRequestHandler(
        github_client=github_client,
        detail_orchestrator=detail_orchestrator,
        feishu_client=feishu_client,
        message_builder=message_builder,
        digest_dispatcher=DigestDispatcher(digest_jobs),
        manual_digest_default_top_k=effective_settings.manual_digest_default_top_k,
        manual_digest_max_top_k=effective_settings.manual_digest_max_top_k,
        about_doc_url=effective_settings.feishu_about_doc_url,
        group_require_bot_mention=effective_settings.feishu_group_require_bot_mention,
    )
    container = RuntimeContainer(
        engine=engine,
        init_db_func=init_db,
        scheduler=scheduler,
        digest_jobs=digest_jobs,
        feishu_client=feishu_client,
        detail_handler=detail_handler,
        resource_closers=[feishu_client.close, docs_client.close, *resource_closers],
    )
    if effective_settings.feishu_long_connection_enabled:
        container.long_connection_client = FeishuLongConnectionClient(
            app_id=effective_settings.feishu_app_id,
            app_secret=effective_settings.feishu_app_secret,
            container=container,
            encrypt_key=effective_settings.feishu_event_encrypt_key,
            verification_token=effective_settings.feishu_event_verification_token,
        )
    return container


def _resolve_default_feishu_chat_ids(settings: Settings) -> list[str]:
    return [
        str(chat_id).strip()
        for chat_id in settings.feishu_chat_ids
        if str(chat_id).strip()
    ]


def _find_runtime_bot_mention(
    mentions: Optional[Sequence[dict[str, Any]]],
    bot_open_id: str,
) -> Optional[dict[str, Any]]:
    normalized_bot_open_id = (bot_open_id or "").strip()
    if not normalized_bot_open_id or not mentions:
        return None

    for mention in mentions:
        if normalized_bot_open_id in _mention_ids(mention):
            return mention
    return None


def _mention_ids(mention: dict[str, Any]) -> set[str]:
    mention_ids: set[str] = set()
    mention_id = mention.get("id")
    if isinstance(mention_id, dict):
        for key in ("open_id", "union_id", "user_id"):
            value = mention_id.get(key)
            if isinstance(value, str) and value.strip():
                mention_ids.add(value.strip())

    for key in ("open_id", "union_id", "user_id"):
        value = mention.get(key)
        if isinstance(value, str) and value.strip():
            mention_ids.add(value.strip())

    return mention_ids


def _extract_bot_open_id(bot_info: Any) -> str:
    if isinstance(bot_info, dict):
        nested = bot_info.get("bot")
        if isinstance(nested, dict):
            return str(nested.get("open_id") or "").strip()
        return str(bot_info.get("open_id") or "").strip()
    return str(getattr(bot_info, "open_id", "") or "").strip()


def _build_research_provider(settings: Settings) -> tuple[ResearchProvider, list]:
    provider_name = (settings.research_provider or "openai").strip().lower()
    if provider_name == "dashscope":
        if not settings.dashscope_api_key:
            return DisabledResearchProvider("DASHSCOPE_API_KEY 未配置，无法生成项目详情。"), []

        client = _build_dashscope_generation_client(settings.dashscope_base_url)
        return (
            DashScopeDeepResearchProvider(
                research_client=client,
                structurer_client=client,
                api_key=settings.dashscope_api_key,
                research_model=settings.dashscope_model,
                structurer_model=settings.dashscope_structurer_model,
                research_timeout_seconds=settings.dashscope_research_timeout_seconds,
                structurer_timeout_seconds=settings.dashscope_structurer_timeout_seconds,
                research_max_retries=settings.dashscope_research_max_retries,
                research_retry_backoff_seconds=settings.dashscope_research_retry_backoff_seconds,
                structurer_max_retries=settings.dashscope_structurer_max_retries,
                structurer_retry_backoff_seconds=settings.dashscope_structurer_retry_backoff_seconds,
            ),
            [],
        )

    if provider_name == "openai":
        if not settings.openai_api_key:
            return DisabledResearchProvider("OPENAI_API_KEY 未配置，无法生成项目详情。"), []

        from openai import AsyncOpenAI

        normalized_openai_base_url = settings.openai_base_url.strip() or None
        client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=normalized_openai_base_url,
        )
        closers = [client.close] if hasattr(client, "close") else []
        return (
            OpenAIResearchProvider(
                client=client,
                model=settings.openai_model,
                reasoning_effort=settings.openai_reasoning_effort,
            ),
            closers,
        )

    raise ValueError("Unsupported research_provider: {0}".format(settings.research_provider))


def _build_summary_localizer(settings: Settings):
    if settings.dashscope_api_key:
        return DashScopeSummaryLocalizer(
            generation_client=_build_dashscope_generation_client(settings.dashscope_base_url),
            api_key=settings.dashscope_api_key,
            model=settings.dashscope_structurer_model,
        )
    return PassthroughSummaryLocalizer()


def _build_dashscope_generation_client(base_url: str):
    import dashscope

    if base_url:
        dashscope.base_http_api_url = base_url
    return dashscope.Generation
