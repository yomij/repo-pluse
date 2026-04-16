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

    assert "> 🕒 生成时间：2026-04-14 09:30:00" in post.markdown


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
            "1. **安装依赖**\n\n"
            "动作：安装项目依赖。\n\n"
            "```bash\n"
            "uv sync\n"
            "```\n\n"
            "预期：依赖安装完成。\n"
            "来源：[README](https://github.com/acme/agent#quick-start)\n\n"
            "2. **启动示例**\n\n"
            "动作：启动官方 demo。\n\n"
            "```bash\n"
            "uv run python examples/demo.py\n"
            "```\n\n"
            "预期：终端输出 successful response。\n"
            "来源：[README](https://github.com/acme/agent#quick-start)\n\n"
            "3. **验证输出**\n\n"
            "动作：确认关键日志。\n\n"
            "预期：显示 successful response。\n"
            "来源：README\n\n"
            "4. **额外步骤**\n\n"
            "动作：不应出现在群摘要压缩结果中。\n\n"
            "预期：忽略。\n"
            "来源：README\n\n"
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
    assert (
        "**3分钟试玩路径**\n\n"
        "1. 安装依赖：运行 `uv sync`\n"
        "2. 启动示例：运行 `uv run python examples/demo.py`\n"
        "3. 验证输出：确认关键日志。"
    ) in post.markdown
    assert "；2. 启动示例" not in post.markdown
    assert "额外步骤" not in post.markdown
    assert "**适合谁**" in post.markdown
    assert "平台团队。" in post.markdown
    assert "**主要风险**" in post.markdown
    assert "缺少 API Key" in post.markdown
    assert "依赖外部模型接口" not in post.markdown
    assert "**文档链接 + 仓库链接**" in post.markdown
    assert "[文档](https://feishu.cn/docx/doc-123)" in post.markdown
    assert "[仓库](https://github.com/acme/agent)" in post.markdown


def test_markdown_digest_builder_detail_post_uses_action_for_long_code_examples():
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
            "结论：可以快速本地试玩\n\n"
            "## 最短体验路径\n"
            "1. **运行最小示例**\n\n"
            "动作：执行同步 Chat Completions 调用。\n\n"
            "```python\n"
            "from openai import OpenAI\\nclient = OpenAI()\\ncompletion = client.chat.completions.create(model='gpt-4', messages=[{'role': 'user', 'content': 'hello'}])\\nprint(completion.choices[0].message.content)\n"
            "```\n\n"
            "预期：输出模型响应。\n"
            "来源：[README](https://github.com/acme/agent#usage)\n\n"
            "## 常见阻塞与失败信号\n"
            "- 成功信号：输出模型响应。\n"
            "- 暂未识别常见阻塞项\n\n"
            "## 局限与风险\n"
            "- 依赖外部模型接口\n"
        ),
    )

    post = MarkdownDigestBuilder().build_detail_post(detail, repo_url="https://github.com/acme/agent")

    assert "1. 运行最小示例：执行同步 Chat Completions 调用。" in post.markdown
    assert "client.chat.completions.create" not in post.markdown
    assert "from openai import OpenAI" not in post.markdown


def test_markdown_digest_builder_detail_post_formats_risks_with_newlines():
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
            "结论：需要 API Key 才能完成试玩\n\n"
            "## 最短体验路径\n"
            "1. 信息不足以确认最短体验路径（来源：信息不足以确认）\n\n"
            "## 常见阻塞与失败信号\n"
            "- 成功信号：信息不足以确认\n"
            "- 阻塞：**缺少 API Key**：未设置环境变量会导致示例启动失败。（来源：README）\n"
            "- 阻塞：**网络失败**：无法访问外部 API。（来源：Docs）\n\n"
            "## 局限与风险\n"
        ),
    )

    post = MarkdownDigestBuilder().build_detail_post(detail, repo_url=None)

    assert "**主要风险**\n\n- 缺少 API Key" in post.markdown
    assert "\n- 网络失败" in post.markdown
    assert "；- 网络失败" not in post.markdown


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
