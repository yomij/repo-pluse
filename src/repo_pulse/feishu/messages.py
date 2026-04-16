from dataclasses import dataclass
from datetime import datetime
import re
from typing import Optional

from repo_pulse.digest.service import DailyDigest
from repo_pulse.feishu.docs import extract_markdown_section

DETAIL_SECTION_DIVIDER = "────────────"


@dataclass(frozen=True)
class RichTextPost:
    title: str
    markdown: str


class MarkdownDigestBuilder:
    def build_digest_post(self, digest: DailyDigest) -> RichTextPost:
        title = "🚀 {0}｜{1}".format(digest.title, digest.window)
        lines = [
            "> ⏱ 数据窗口：{0}".format(digest.window),
            "> 🕒 生成时间：{0}".format(_display_time(digest.generated_at)),
            "",
        ]

        if not digest.entries:
            lines.extend(
                [
                    "🫥 今天还没有符合条件的项目上榜。",
                    "",
                    "💬 稍后可以继续让我重跑一次日报。",
                ]
            )
            return RichTextPost(title=title, markdown="\n".join(lines).strip())

        for index, entry in enumerate(digest.entries, start=1):
            lines.extend(
                [
                    "{0}. **{1}**".format(index, entry.full_name),
                    "   - 🔥 分类：{0}".format(entry.category or "misc"),
                    "   - ✨ 一句话：{0}".format(_single_line(entry.summary)),
                    "   - 📈 上榜理由：",
                ]
            )
            for reason_index, reason in enumerate(entry.reason_lines or [_single_line(entry.reason)], start=1):
                lines.append("    {0}. {1}".format(reason_index, _single_line(reason, limit=200)))

            links = ["[仓库]({0})".format(entry.repo_url)]
            if entry.doc_url:
                links.append("[文档]({0})".format(entry.doc_url))
            lines.extend(
                [
                    "   - 🔗 {0}".format(" · ".join(links)),
                    "",
                ]
            )

        lines.extend(
            [
                "💬 使用 `/a {0}` 可获取详情".format(digest.entries[0].full_name),
                "📚 详情会同步到飞书文档，并回群里摘要",
            ]
        )
        return RichTextPost(title=title, markdown="\n".join(lines).strip())

    def build_detail_post(self, detail, repo_url: Optional[str] = None) -> RichTextPost:
        intro = _extract_first_section(detail.summary_markdown, "项目简介")
        why_now = _extract_first_section(detail.summary_markdown, "为什么最近火")
        trial = _extract_first_section(detail.summary_markdown, "是否能快速试玩")
        quickstart = _extract_first_section(detail.summary_markdown, "最短体验路径", "快速上手")
        fit = _extract_first_section(detail.summary_markdown, "是否适合我", "适合谁用 / 不适合谁用")
        blockers = extract_markdown_section(detail.summary_markdown, "常见阻塞与失败信号")
        risks = extract_markdown_section(detail.summary_markdown, "局限与风险")
        if not trial and quickstart:
            trial = "基于历史缓存：仓库提供快速上手说明，建议查看详情文档确认最新试玩条件。"

        sections = [
            ("🧭 **是什么**", _as_bullet_lines(intro or "信息不足以确认")),
            ("🔥 **为什么最近火**", _as_bullet_lines(why_now or "信息不足以确认")),
            (
                "⚡ **是否能快速试玩**",
                _as_bullet_lines(trial or "结论：信息不足以确认是否能快速试玩"),
            ),
            ("🚀 **3分钟试玩路径**", _as_plain_lines(_compress_quickstart_steps(quickstart))),
            ("👥 **适合谁**", _as_bullet_lines(_extract_fit_for(fit))),
            ("⚠️ **主要风险**", _as_bullet_lines(_extract_main_risks(trial, blockers, risks))),
        ]
        links = []
        if detail.doc_url:
            links.append("- [文档]({0})".format(detail.doc_url))
        if repo_url:
            links.append("- [仓库]({0})".format(repo_url))
        sections.append(("🔗 **相关链接**", links or ["- 暂无链接"]))

        markdown = _render_detail_sections(sections)
        return RichTextPost(title="📌 {0}".format(detail.full_name), markdown=markdown)


def _single_line(text: str, limit: int = 120) -> str:
    normalized = " ".join((text or "").split())
    if not normalized:
        return "暂无补充"
    if len(normalized) <= limit:
        return normalized
    return "{0}...".format(normalized[: limit - 3].rstrip())


def _display_time(value: Optional[str]) -> str:
    normalized = (value or "").strip()
    if not normalized:
        return "未提供"

    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return normalized

    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _extract_first_section(markdown: str, *headings: str) -> str:
    for heading in headings:
        section = extract_markdown_section(markdown, heading)
        if section:
            return section
    return ""


def _compress_quickstart_steps(section: str, *, max_steps: int = 3) -> str:
    lines = [line.strip() for line in (section or "").splitlines() if line.strip()]
    if not lines:
        return "信息不足以确认"

    steps = []
    for line in lines:
        match = re.match(r"^\d+\.\s+\*\*(.+?)\*\*：(.*)$", line)
        if match:
            label = match.group(1).strip()
            body = match.group(2).strip()
            action = body.split("（预期：", 1)[0].strip()
            steps.append("{0}：{1}".format(label, action))
        elif re.match(r"^\d+\.\s+", line):
            steps.append(re.sub(r"^\d+\.\s+", "", line).strip())
        else:
            steps.append(line)
        if len(steps) >= max_steps:
            break

    if not steps:
        return "信息不足以确认"
    return "\n".join("{0}. {1}".format(index, step) for index, step in enumerate(steps, start=1))


def _extract_fit_for(section: str) -> str:
    lines = [line.strip() for line in (section or "").splitlines() if line.strip()]
    if not lines:
        return "信息不足以确认"

    for line in lines:
        if line.startswith("适合："):
            return line[len("适合：") :].strip() or "信息不足以确认"
    return lines[0]


def _extract_main_risks(
    trial_section: str,
    blockers_section: str,
    risks_section: str,
) -> str:
    blocker_lines = []
    for line in (blockers_section or "").splitlines():
        normalized = line.strip()
        if not normalized.startswith("- 阻塞："):
            continue
        text = normalized[len("- 阻塞：") :].strip()
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        if text:
            blocker_lines.append(text)

    if blocker_lines:
        return "\n".join(blocker_lines[:2])

    risk_lines = []
    for line in (risks_section or "").splitlines():
        normalized = line.strip()
        if normalized.startswith("- "):
            risk_lines.append(normalized[2:].strip())
    if risk_lines:
        return "\n".join(risk_lines[:2])
    for line in (trial_section or "").splitlines():
        normalized = line.strip()
        if normalized.startswith("结论："):
            return normalized[len("结论：") :].strip() or "信息不足以确认"
    return "信息不足以确认"


def _render_detail_sections(sections: list[tuple[str, list[str]]]) -> str:
    rendered: list[str] = []
    for index, (title, lines) in enumerate(sections):
        rendered.append(title)
        rendered.extend(lines or ["- 信息不足以确认"])
        if index < len(sections) - 1:
            rendered.extend(["", DETAIL_SECTION_DIVIDER, ""])
    return "\n".join(rendered).strip() or "暂无详情摘要。"


def _as_bullet_lines(text: str) -> list[str]:
    lines = _as_plain_lines(text)
    return [line if line.startswith("- ") else "- {0}".format(line) for line in lines]


def _as_plain_lines(text: str) -> list[str]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines or ["信息不足以确认"]
