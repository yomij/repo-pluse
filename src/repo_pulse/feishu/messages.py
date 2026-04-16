from dataclasses import dataclass
from datetime import datetime
import re
from typing import Optional

from repo_pulse.digest.service import DailyDigest
from repo_pulse.feishu.docs import extract_markdown_section
from repo_pulse.time_utils import format_display_time


@dataclass(frozen=True)
class RichTextPost:
    title: str
    markdown: str


class MarkdownDigestBuilder:
    def __init__(self, scheduler_timezone: str = "Asia/Shanghai"):
        self.scheduler_timezone = scheduler_timezone

    def build_digest_post(self, digest: DailyDigest) -> RichTextPost:
        title = "🚀 {0}｜{1}".format(digest.title, digest.window)
        lines = [
            "> ⏱ 数据窗口：{0}".format(digest.window),
            "> 🕒 生成时间：{0}".format(
                format_display_time(digest.generated_at, self.scheduler_timezone)
            ),
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
            "**是什么**",
            intro or "信息不足以确认",
            "**为什么最近火**",
            why_now or "信息不足以确认",
            "**是否能快速试玩**",
            trial or "结论：信息不足以确认是否能快速试玩",
            "**3分钟试玩路径**",
            _compress_quickstart_steps(quickstart),
            "**适合谁**",
            _extract_fit_for(fit),
            "**主要风险**",
            _extract_main_risks(trial, blockers, risks),
        ]
        links = []
        if detail.doc_url:
            links.append("[文档]({0})".format(detail.doc_url))
        if repo_url:
            links.append("[仓库]({0})".format(repo_url))
        sections.extend(
            [
                "**文档链接 + 仓库链接**",
                " · ".join(links) if links else "暂无链接",
            ]
        )

        markdown = "\n\n".join(section for section in sections if section).strip() or "暂无详情摘要。"
        return RichTextPost(title="📌 {0}".format(detail.full_name), markdown=markdown)


def _single_line(text: str, limit: int = 120) -> str:
    normalized = " ".join((text or "").split())
    if not normalized:
        return "暂无补充"
    if len(normalized) <= limit:
        return normalized
    return "{0}...".format(normalized[: limit - 3].rstrip())

def _extract_first_section(markdown: str, *headings: str) -> str:
    for heading in headings:
        section = extract_markdown_section(markdown, heading)
        if section:
            return section
    return ""


def _compress_quickstart_steps(section: str, *, max_steps: int = 3) -> str:
    parsed_steps = _parse_quickstart_steps(section)
    if parsed_steps:
        return "\n".join(
            "{0}. {1}".format(index, _summarize_quickstart_step(step))
            for index, step in enumerate(parsed_steps[:max_steps], start=1)
        )

    lines = [line.strip() for line in (section or "").splitlines() if line.strip()]
    if not lines:
        return "信息不足以确认"

    steps = []
    for line in lines:
        if re.match(r"^\d+\.\s+", line):
            steps.append(re.sub(r"^\d+\.\s+", "", line).strip())
        else:
            steps.append(line)
        if len(steps) >= max_steps:
            break

    return "\n".join("{0}. {1}".format(index, step) for index, step in enumerate(steps, start=1))


def _parse_quickstart_steps(section: str) -> list[dict[str, object]]:
    lines = [line.rstrip() for line in (section or "").splitlines()]
    steps: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    in_code_block = False
    code_lines: list[str] = []

    def flush_current() -> None:
        nonlocal current
        if current is not None:
            steps.append(current)
            current = None

    def flush_code_block() -> None:
        nonlocal code_lines
        if current is not None and code_lines:
            current.setdefault("commands", []).append(" ".join(code_lines).strip())
        code_lines = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("```"):
            if in_code_block:
                flush_code_block()
                in_code_block = False
            else:
                in_code_block = True
                code_lines = []
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        match = re.match(r"^\d+\.\s+\*\*(.+?)\*\*(?::|：)?\s*(.*)$", line)
        if match:
            flush_current()
            current = {
                "label": match.group(1).strip(),
                "action": "",
                "commands": [],
            }
            inline_body = match.group(2).strip()
            if inline_body:
                current["action"] = inline_body.split("（预期：", 1)[0].strip()
            continue

        plain_match = re.match(r"^\d+\.\s+(.*)$", line)
        if plain_match:
            flush_current()
            steps.append({"label": "", "action": plain_match.group(1).strip(), "commands": []})
            continue

        if current is None:
            continue

        if line.startswith("动作："):
            current["action"] = line[len("动作：") :].strip()
            continue
        if line.startswith("预期：") or line.startswith("来源："):
            continue

    if in_code_block:
        flush_code_block()
    flush_current()
    return steps


def _summarize_quickstart_step(step: dict[str, object]) -> str:
    label = str(step.get("label", "")).strip()
    commands = [command for command in step.get("commands", []) if isinstance(command, str) and command.strip()]
    safe_commands = [command for command in commands if _is_inline_summary_command(command)]
    if commands and len(safe_commands) == len(commands):
        body = "运行 {0}".format("；".join("`{0}`".format(command) for command in commands[:2]))
    else:
        body = str(step.get("action", "")).strip() or "查看详情文档中的代码示例"
    if label:
        return "{0}：{1}".format(label, body)
    return body


def _is_inline_summary_command(command: str) -> bool:
    normalized = (command or "").strip()
    if not normalized:
        return False
    if "\n" in normalized or "\\n" in normalized:
        return False
    return len(normalized) <= 80


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
        return "\n".join("- {0}".format(item) for item in blocker_lines[:2])

    risk_lines = []
    for line in (risks_section or "").splitlines():
        normalized = line.strip()
        if normalized.startswith("- "):
            risk_lines.append(normalized[2:].strip())
    if risk_lines:
        return "\n".join("- {0}".format(item) for item in risk_lines[:2])
    for line in (trial_section or "").splitlines():
        normalized = line.strip()
        if normalized.startswith("结论："):
            return normalized[len("结论：") :].strip() or "信息不足以确认"
    return "信息不足以确认"
