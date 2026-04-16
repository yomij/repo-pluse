from dataclasses import dataclass, field
import json
import re
from typing import List, Optional


README_SECTION_PATTERNS = (
    ("Installation", re.compile(r"\binstallation\b", re.IGNORECASE)),
    ("Quick Start", re.compile(r"\bquick[\s-]*start\b", re.IGNORECASE)),
    ("Getting Started", re.compile(r"\bgetting[\s-]*started\b", re.IGNORECASE)),
    ("Usage", re.compile(r"\busage\b", re.IGNORECASE)),
    ("Run", re.compile(r"\brun\b", re.IGNORECASE)),
    ("Setup", re.compile(r"\bset[\s-]*up\b|\bsetup\b", re.IGNORECASE)),
)
SETUP_FILE_PRIORITY = (
    ".env.example",
    ".env.sample",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yaml",
    "compose.yml",
    "Makefile",
    "package.json",
)
ATX_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
SETEXT_HEADING_UNDERLINE_PATTERN = re.compile(r"^\s{0,3}(=+|-+)\s*$")
FENCED_CODE_BLOCK_PATTERN = re.compile(r"^\s{0,3}([`~]{3,}).*$")
SHELL_PROMPT_PREFIX_PATTERN = re.compile(
    r"^(?:\([^)]+\)\s*)?(?:[\w.-]+@[\w.-]+(?::[^\s]*)?\s*)?[$>%]\s+"
)


@dataclass(frozen=True)
class RepositoryEvidence:
    full_name: str
    repo_url: str
    description: Optional[str] = None
    homepage: Optional[str] = None
    language: Optional[str] = None
    default_branch: str = "main"
    topics: List[str] = field(default_factory=list)
    readme_excerpt: str = ""
    readme_setup_sections: List[str] = field(default_factory=list)
    setup_file_snippets: List[str] = field(default_factory=list)
    runtime_hints: List[str] = field(default_factory=list)
    releases: List[str] = field(default_factory=list)
    recent_commits: List[str] = field(default_factory=list)
    key_paths: List[str] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        fallback = "信息不足以确认"
        sections = [
            "仓库一手证据：",
            f"- 描述：{self.description or fallback}",
            f"- 主页：{self.homepage or fallback}",
            f"- 语言：{self.language or fallback}",
            f"- 默认分支：{self.default_branch or fallback}",
            f"- 主题：{', '.join(self.topics) if self.topics else fallback}",
            f"- README 摘要：{self.readme_excerpt or fallback}",
        ]
        sections.extend(self._format_list_block("README 上手片段", self.readme_setup_sections, fallback))
        sections.extend(self._format_list_block("根目录配置线索", self.setup_file_snippets, fallback))
        sections.append(f"- 运行提示：{'; '.join(self.runtime_hints) if self.runtime_hints else fallback}")
        sections.append(f"- 发布版本：{'; '.join(self.releases) if self.releases else fallback}")
        sections.append(f"- 最近提交：{'; '.join(self.recent_commits) if self.recent_commits else fallback}")
        sections.append(f"- 根目录路径：{', '.join(self.key_paths) if self.key_paths else fallback}")
        return "\n".join(sections)

    @staticmethod
    def _format_list_block(label: str, items: List[str], fallback: str) -> List[str]:
        if not items:
            return [f"- {label}：{fallback}"]
        lines = [f"- {label}："]
        lines.extend("  - {0}".format(item) for item in items)
        return lines


class RepositoryEvidenceBuilder:
    def __init__(
        self,
        github_client,
        readme_char_limit: int = 4000,
        release_limit: int = 3,
        commit_limit: int = 5,
    ):
        self.github_client = github_client
        self.readme_char_limit = max(readme_char_limit, 0)
        self.release_limit = max(release_limit, 0)
        self.commit_limit = max(commit_limit, 0)

    async def build(self, full_name: str) -> RepositoryEvidence:
        metadata = await self.github_client.get_repository(full_name)
        if metadata is None:
            raise RuntimeError("Repository not found: {0}".format(full_name))

        readme_text = ""
        if self.readme_char_limit > 0:
            try:
                readme_text = await self.github_client.get_readme(full_name)
            except Exception:
                readme_text = ""

        releases: List[str] = []
        if self.release_limit > 0:
            try:
                releases = list(
                    await self.github_client.list_releases(
                        full_name,
                        per_page=self.release_limit,
                    )
                    or []
                )
            except Exception:
                releases = []

        recent_commits: List[str] = []
        if self.commit_limit > 0:
            try:
                recent_commits = list(
                    await self.github_client.list_recent_commits(
                        full_name,
                        per_page=self.commit_limit,
                    )
                    or []
                )
            except Exception:
                recent_commits = []

        try:
            key_paths = list(await self.github_client.list_root_paths(full_name) or [])
        except Exception:
            key_paths = []

        readme_setup_sections = self._extract_readme_setup_sections(readme_text)
        setup_file_snippets, runtime_hints = await self._collect_setup_file_clues(full_name, key_paths)
        runtime_hints = self._merge_runtime_hints(
            self._extract_runtime_hints_from_text(readme_text),
            runtime_hints,
        )

        return RepositoryEvidence(
            full_name=metadata.full_name,
            repo_url=str(metadata.html_url),
            description=metadata.description,
            homepage=str(metadata.homepage) if metadata.homepage else None,
            language=metadata.language,
            default_branch=metadata.default_branch or "main",
            topics=list(metadata.topics or []),
            readme_excerpt=self._clip_text(readme_text),
            readme_setup_sections=readme_setup_sections,
            setup_file_snippets=setup_file_snippets,
            runtime_hints=runtime_hints,
            releases=releases[: self.release_limit],
            recent_commits=recent_commits[: self.commit_limit],
            key_paths=key_paths,
        )

    def _clip_text(self, value: Optional[str], limit: Optional[int] = None) -> str:
        text = (value or "").strip()
        clip_limit = self.readme_char_limit if limit is None else max(limit, 0)
        if clip_limit <= 0:
            return ""
        return text[:clip_limit]

    def _extract_readme_setup_sections(self, readme_text: str) -> List[str]:
        sections = self._parse_readme_sections(readme_text)
        if not sections:
            return []

        sections_by_priority = {label: [] for label, _ in README_SECTION_PATTERNS}
        seen: set[str] = set()
        for heading, body in sections:
            normalized_heading = re.sub(r"[^a-z0-9]+", " ", heading.lower()).strip()

            for label, pattern in README_SECTION_PATTERNS:
                if not pattern.search(normalized_heading):
                    continue
                rendered = heading
                clipped_body = self._clip_text(body, limit=500)
                if clipped_body:
                    rendered = "{0}: {1}".format(heading, clipped_body)
                if rendered not in seen:
                    sections_by_priority[label].append(rendered)
                    seen.add(rendered)
                break

        collected: List[str] = []
        for label, _ in README_SECTION_PATTERNS:
            collected.extend(sections_by_priority[label])
        return collected

    def _parse_readme_sections(self, readme_text: str) -> List[tuple[str, str]]:
        text = (readme_text or "").strip()
        if not text:
            return []

        lines = text.splitlines()
        section_starts: List[tuple[int, int, str]] = []
        fence_marker_char = ""
        fence_marker_length = 0
        line_index = 0
        while line_index < len(lines):
            current_line = lines[line_index]
            fence_match = FENCED_CODE_BLOCK_PATTERN.match(current_line)
            if fence_marker_char:
                if (
                    fence_match
                    and fence_match.group(1)[0] == fence_marker_char
                    and len(fence_match.group(1)) >= fence_marker_length
                ):
                    fence_marker_char = ""
                    fence_marker_length = 0
                line_index += 1
                continue

            if fence_match:
                fence_marker_char = fence_match.group(1)[0]
                fence_marker_length = len(fence_match.group(1))
                line_index += 1
                continue

            atx_match = ATX_HEADING_PATTERN.match(current_line)
            if atx_match:
                section_starts.append((line_index, line_index + 1, atx_match.group(1).strip()))
                line_index += 1
                continue

            if (
                line_index + 1 < len(lines)
                and current_line.strip()
                and SETEXT_HEADING_UNDERLINE_PATTERN.match(lines[line_index + 1])
            ):
                section_starts.append((line_index, line_index + 2, current_line.strip()))
                line_index += 2
                continue

            line_index += 1

        sections: List[tuple[str, str]] = []
        for index, (start_line, body_start_line, heading) in enumerate(section_starts):
            del start_line
            next_start_line = (
                section_starts[index + 1][0]
                if index + 1 < len(section_starts)
                else len(lines)
            )
            body = "\n".join(lines[body_start_line:next_start_line]).strip()
            sections.append((heading, body))
        return sections

    async def _collect_setup_file_clues(self, full_name: str, key_paths: List[str]) -> tuple[List[str], List[str]]:
        fetch_file_content = getattr(self.github_client, "get_file_content", None)
        if not callable(fetch_file_content):
            return [], []

        available_paths = set(key_paths or [])
        snippets: List[str] = []
        runtime_hints: List[str] = []
        for path in SETUP_FILE_PRIORITY:
            if path not in available_paths:
                continue
            try:
                content = await fetch_file_content(full_name, path)
            except Exception:
                continue
            if not content.strip():
                continue
            snippet = self._extract_setup_file_snippet(path, content)
            if snippet:
                snippets.append(snippet)
            runtime_hints = self._merge_runtime_hints(runtime_hints, self._extract_runtime_hints_from_file(path, content))
        return snippets, runtime_hints

    def _extract_setup_file_snippet(self, path: str, content: str) -> str:
        if path in {".env.example", ".env.sample"}:
            keys = [
                line.split("=", 1)[0].strip()
                for line in content.splitlines()
                if line.strip() and not line.lstrip().startswith("#") and "=" in line
            ]
            snippet = ", ".join(keys[:6])
            return "{0}: {1}".format(path, snippet or self._clip_text(content, limit=240))

        if path == "package.json":
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                return "package.json: {0}".format(self._clip_text(content, limit=240))
            scripts = payload.get("scripts") if isinstance(payload, dict) else None
            if isinstance(scripts, dict) and scripts:
                rendered_scripts = "; ".join(
                    "{0}={1}".format(name, command)
                    for name, command in list(scripts.items())[:6]
                )
                return "package.json: scripts {0}".format(rendered_scripts)
            return "package.json: {0}".format(self._clip_text(content, limit=240))

        return "{0}: {1}".format(path, self._clip_text(content, limit=240))

    def _extract_runtime_hints_from_file(self, path: str, content: str) -> List[str]:
        hints: List[str] = []
        if path == "Makefile":
            for line in content.splitlines():
                if line.strip().endswith(":") and not line.startswith("\t"):
                    target = line.split(":", 1)[0].strip()
                    if target in {"dev", "run", "start", "serve", "setup"}:
                        hints.append("make {0}".format(target))

        return hints

    def _extract_runtime_hints_from_text(self, text: str) -> List[str]:
        hints: List[str] = []
        for raw_line in (text or "").splitlines():
            candidate = self._normalize_command_candidate(raw_line)
            if self._looks_like_command(candidate):
                hints.append(candidate)
        return hints

    def _normalize_command_candidate(self, raw_line: str) -> str:
        line = raw_line.strip().strip("`")
        if not line:
            return ""
        candidate = re.sub(r"^[*-]\s*", "", line).strip()
        candidate = SHELL_PROMPT_PREFIX_PATTERN.sub("", candidate, count=1)
        return candidate.strip()

    @staticmethod
    def _merge_runtime_hints(*groups: List[str]) -> List[str]:
        merged: List[str] = []
        for group in groups:
            for hint in group:
                normalized = hint.strip()
                if not normalized or normalized in merged:
                    continue
                merged.append(normalized)
        return merged[:8]

    @staticmethod
    def _looks_like_command(candidate: str) -> bool:
        command_prefixes = (
            "make ",
            "npm ",
            "pnpm ",
            "yarn ",
            "uv ",
            "python ",
            "poetry ",
            "docker ",
            "cp ",
            "cp\t",
            "./",
        )
        return candidate.startswith(command_prefixes)
