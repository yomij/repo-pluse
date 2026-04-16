from types import SimpleNamespace

from repo_pulse.digest.service import DailyDigest, DigestEntry
from repo_pulse.feishu.messages import MarkdownDigestBuilder, RichTextPost


def test_markdown_digest_builder_renders_post_title_and_markdown_body():
    digest = DailyDigest(
        title="GitHub 热门日榜",
        window="24h",
        generated_at="2026-04-14T09:30:00+08:00",
        entries=[
            DigestEntry(
                full_name="acme/agent",
                category="ai/agents",
                summary="面向任务自动化的 AI Agent 框架",
                reason="internal raw reason",
                reason_lines=[
                    "⭐ 24h Stars +40 · 🍴 Forks +5",
                    "📊 相对增长 28.6%",
                    "⚡ 最近 24h 内仍有代码更新",
                ],
                repo_url="https://github.com/acme/agent",
                detail_action_value="detail:acme/agent",
                doc_url="https://example.feishu.cn/docx/123",
            )
        ],
    )

    post = MarkdownDigestBuilder().build_digest_post(digest)

    assert isinstance(post, RichTextPost)
    assert post.title == "🚀 GitHub 热门日榜｜24h"
    assert "> ⏱ 数据窗口：24h" in post.markdown
    assert "> 🕒 生成时间：2026-04-14 09:30:00" in post.markdown
    assert "1. **acme/agent**" in post.markdown
    assert "🔥 分类：ai/agents" in post.markdown
    assert "✨ 一句话：面向任务自动化的 AI Agent 框架" in post.markdown
    assert "📈 上榜理由：" in post.markdown
    assert "    1. ⭐ 24h Stars +40 · 🍴 Forks +5" in post.markdown
    assert "    2. 📊 相对增长 28.6%" in post.markdown
    assert "    3. ⚡ 最近 24h 内仍有代码更新" in post.markdown
    assert "[仓库](https://github.com/acme/agent)" in post.markdown
    assert "[文档](https://example.feishu.cn/docx/123)" in post.markdown
    assert "💬 使用 `/a acme/agent` 可获取详情" in post.markdown


def test_markdown_digest_builder_handles_empty_digest():
    digest = DailyDigest(
        title="GitHub 热门周榜",
        window="7d",
        entries=[],
    )

    post = MarkdownDigestBuilder().build_digest_post(digest)

    assert post.title == "🚀 GitHub 热门周榜｜7d"
    assert "🫥 今天还没有符合条件的项目上榜。" in post.markdown


def test_markdown_digest_builder_formats_pretty_generated_time():
    digest = DailyDigest(
        title="GitHub 热门日榜",
        window="24h",
        generated_at="2026-04-14T01:30:00Z",
        entries=[],
    )

    post = MarkdownDigestBuilder().build_digest_post(digest)

    assert "> 🕒 生成时间：2026-04-14 01:30:00" in post.markdown


def test_markdown_digest_builder_build_detail_post_uses_onboarding_sections():
    detail = SimpleNamespace(
        full_name="acme/agent",
        doc_url="https://feishu.cn/docx/doc-123",
        summary_markdown=(
            "# acme/agent 项目详情\n\n"
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
            "1. **安装依赖**：运行 `uv sync`。（预期：依赖安装完成；来源：README）\n"
            "2. **启动示例**：运行 `uv run python examples/demo.py`。（预期：终端输出 successful response；来源：README）\n"
            "3. **验证输出**：观察日志包含 success 标记。（预期：显示 successful response；来源：README）\n"
            "4. **额外步骤**：不应出现在群摘要压缩结果中。（预期：忽略；来源：README）\n\n"
            "## 常见阻塞与失败信号\n"
            "- 成功信号：示例命令输出 successful response。\n"
            "- 阻塞：**缺少 API Key**：未设置环境变量会导致示例启动失败。（来源：README）\n\n"
            "## 局限与风险\n"
            "- 依赖外部模型接口\n"
        ),
    )

    post = MarkdownDigestBuilder().build_detail_post(detail, repo_url="https://github.com/acme/agent")

    assert post.title == "📌 acme/agent"
    assert "**是什么**" in post.markdown
    assert "**为什么最近火**" in post.markdown
    assert "**是否能快速试玩**" in post.markdown
    assert "结论：可以快速本地试玩（预计耗时：3-10 分钟）" in post.markdown
    assert "**3分钟试玩路径**" in post.markdown
    assert "1. 安装依赖：运行 `uv sync`。" in post.markdown
    assert "2. 启动示例：运行 `uv run python examples/demo.py`。" in post.markdown
    assert "3. 验证输出：观察日志包含 success 标记。" in post.markdown
    assert "额外步骤" not in post.markdown
    assert "**适合谁**" in post.markdown
    assert "平台团队。" in post.markdown
    assert "**主要风险**" in post.markdown
    assert "缺少 API Key" in post.markdown
    assert "依赖外部模型接口" not in post.markdown
    assert "**文档链接 + 仓库链接**" in post.markdown
    assert "[文档](https://feishu.cn/docx/doc-123)" in post.markdown
    assert "[仓库](https://github.com/acme/agent)" in post.markdown


def test_markdown_digest_builder_build_detail_post_risk_fallbacks_to_risks_section():
    detail = SimpleNamespace(
        full_name="acme/agent",
        doc_url="https://feishu.cn/docx/doc-456",
        summary_markdown=(
            "# acme/agent 项目详情\n\n"
            "## 项目简介\n"
            "这是一个 agent 平台。\n\n"
            "## 为什么最近火\n"
            "社区增长很快。\n\n"
            "## 是否适合我\n"
            "适合：平台团队。\n"
            "不适合：完全离线环境。\n\n"
            "## 是否能快速试玩\n"
            "结论：信息不足以确认是否能快速试玩\n\n"
            "## 最短体验路径\n"
            "1. 信息不足以确认最短体验路径（来源：信息不足以确认）\n\n"
            "## 常见阻塞与失败信号\n"
            "- 成功信号：信息不足以确认\n"
            "- 暂未识别常见阻塞项\n\n"
            "## 局限与风险\n"
            "- 依赖外部模型接口\n"
        ),
    )

    post = MarkdownDigestBuilder().build_detail_post(detail, repo_url=None)

    assert "**主要风险**" in post.markdown
    assert "依赖外部模型接口" in post.markdown


def test_markdown_digest_builder_build_detail_post_risk_fallbacks_to_trial_verdict():
    detail = SimpleNamespace(
        full_name="acme/agent",
        doc_url=None,
        summary_markdown=(
            "# acme/agent 项目详情\n\n"
            "## 项目简介\n"
            "这是一个 agent 平台。\n\n"
            "## 为什么最近火\n"
            "社区增长很快。\n\n"
            "## 是否适合我\n"
            "适合：平台团队。\n"
            "不适合：完全离线环境。\n\n"
            "## 是否能快速试玩\n"
            "结论：需要 API Key 才能完成试玩（预计耗时：5-15 分钟）\n\n"
            "## 最短体验路径\n"
            "1. **准备 API Key / 账号凭证**：先按官方文档申请并配置所需凭证，具体命令信息不足以确认"
            "（预期：凭证可用于后续试玩；来源：基于 trial_verdict 推断）\n\n"
            "## 常见阻塞与失败信号\n"
            "- 成功信号：信息不足以确认\n"
            "- 暂未识别常见阻塞项\n\n"
            "## 局限与风险\n"
        ),
    )

    post = MarkdownDigestBuilder().build_detail_post(detail, repo_url=None)

    assert "**主要风险**" in post.markdown
    assert "需要 API Key 才能完成试玩" in post.markdown


def test_markdown_digest_builder_build_detail_post_supports_legacy_cached_sections():
    detail = SimpleNamespace(
        full_name="acme/agent",
        doc_url="https://feishu.cn/docx/legacy",
        summary_markdown=(
            "# acme/agent 项目详情\n\n"
            "## 项目简介\n"
            "这是一个 agent 平台。\n\n"
            "## 为什么最近火\n"
            "社区增长很快。\n\n"
            "## 适合谁用 / 不适合谁用\n"
            "适合平台团队。\n"
            "不适合完全离线环境。\n\n"
            "## 快速上手\n"
            "先运行官方 demo。\n\n"
            "## 局限与风险\n"
            "- 依赖外部模型接口\n"
        ),
    )

    post = MarkdownDigestBuilder().build_detail_post(detail, repo_url="https://github.com/acme/agent")

    assert "**是否能快速试玩**" in post.markdown
    assert "基于历史缓存" in post.markdown
    assert "**3分钟试玩路径**" in post.markdown
    assert "1. 先运行官方 demo。" in post.markdown
    assert "**适合谁**" in post.markdown
    assert "适合平台团队。" in post.markdown
