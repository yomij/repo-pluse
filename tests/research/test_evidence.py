import pytest

from repo_pulse.research.evidence import RepositoryEvidence, RepositoryEvidenceBuilder
from repo_pulse.schemas import RepositoryMetadata


class _GitHubClient:
    async def get_repository(self, full_name):
        return RepositoryMetadata(
            full_name=full_name,
            name="agent",
            owner="acme",
            description="Agent runtime",
            html_url="https://github.com/acme/agent",
            homepage="https://agent.acme.dev",
            language="Python",
            topics=["ai", "agents"],
            default_branch="main",
            stars=180,
            forks=20,
            watchers=11,
            pushed_at="2026-04-13T01:00:00Z",
        )

    async def get_readme(self, full_name):
        assert full_name == "acme/agent"
        return (
            "# Agent\n\n"
            "Run demo first.\n\n"
            "## Installation\n\n"
            "uv sync\n"
            "cp .env.example .env\n\n"
            "## Quick Start\n\n"
            "make dev\n"
            "uv run repo-pulse --help\n\n"
            "## Usage\n\n"
            "Open the dashboard once the server is running.\n"
        )

    async def list_releases(self, full_name, per_page=3):
        return ["v1.2.0: Stability fixes"]

    async def list_recent_commits(self, full_name, per_page=5):
        return ["ship eval dashboard", "tighten retry policy"]

    async def list_root_paths(self, full_name):
        return ["docs", "examples", "src", "README.md", ".env.example", "Makefile", "package.json"]

    async def get_file_content(self, full_name, path):
        assert full_name == "acme/agent"
        contents = {
            ".env.example": "OPENAI_API_KEY=\nGITHUB_TOKEN=\n",
            "Makefile": "install:\n\tuv sync\n\ndev:\n\tuv run repo-pulse --help\n",
            "package.json": (
                '{\n'
                '  "name": "agent",\n'
                '  "scripts": {\n'
                '    "dev": "vite",\n'
                '    "start": "node server.js"\n'
                "  }\n"
                "}\n"
            ),
        }
        return contents[path]


@pytest.mark.asyncio
async def test_repository_evidence_builder_collects_first_party_context():
    builder = RepositoryEvidenceBuilder(github_client=_GitHubClient())

    evidence = await builder.build("acme/agent")

    assert evidence.full_name == "acme/agent"
    assert evidence.default_branch == "main"
    assert "Run demo first." in evidence.readme_excerpt
    assert evidence.releases == ["v1.2.0: Stability fixes"]
    assert evidence.recent_commits == ["ship eval dashboard", "tighten retry policy"]
    assert evidence.key_paths == [
        "docs",
        "examples",
        "src",
        "README.md",
        ".env.example",
        "Makefile",
        "package.json",
    ]
    assert any("Installation" in section and "uv sync" in section for section in evidence.readme_setup_sections)
    assert any("Quick Start" in section and "make dev" in section for section in evidence.readme_setup_sections)
    assert any(".env.example" in snippet and "OPENAI_API_KEY" in snippet for snippet in evidence.setup_file_snippets)
    assert any("Makefile" in snippet and "uv run repo-pulse --help" in snippet for snippet in evidence.setup_file_snippets)
    assert any("package.json" in snippet and "scripts" in snippet for snippet in evidence.setup_file_snippets)
    assert "cp .env.example .env" in evidence.runtime_hints
    assert "make dev" in evidence.runtime_hints
    assert "uv run repo-pulse --help" in evidence.runtime_hints
    assert "npm run start" not in evidence.runtime_hints


@pytest.mark.asyncio
async def test_repository_evidence_builder_extracts_setext_sections_and_normalizes_shell_prompts():
    class _SetextReadmeGitHubClient(_GitHubClient):
        async def get_readme(self, full_name):
            assert full_name == "acme/agent"
            return (
                "# Agent\n\n"
                "Getting Started\n"
                "---------------\n\n"
                "$ make dev\n"
                "$ uv run repo-pulse --help\n"
            )

        async def list_root_paths(self, full_name):
            del full_name
            return ["README.md"]

        async def get_file_content(self, full_name, path):
            del full_name, path
            raise AssertionError("no root file fetch expected")

    builder = RepositoryEvidenceBuilder(github_client=_SetextReadmeGitHubClient())

    evidence = await builder.build("acme/agent")

    assert any(
        "Getting Started" in section and "$ make dev" in section
        for section in evidence.readme_setup_sections
    )
    assert "make dev" in evidence.runtime_hints
    assert "uv run repo-pulse --help" in evidence.runtime_hints


@pytest.mark.asyncio
async def test_repository_evidence_builder_does_not_infer_runtime_hints_from_package_json_or_compose_files():
    class _SetupFilesOnlyGitHubClient(_GitHubClient):
        async def get_readme(self, full_name):
            del full_name
            return "# Agent\n\nNo setup commands documented here.\n"

        async def list_root_paths(self, full_name):
            del full_name
            return ["package.json", "docker-compose.yml"]

        async def get_file_content(self, full_name, path):
            assert full_name == "acme/agent"
            contents = {
                "package.json": (
                    '{\n'
                    '  "name": "agent",\n'
                    '  "scripts": {\n'
                    '    "dev": "vite",\n'
                    '    "start": "node server.js"\n'
                    "  }\n"
                    "}\n"
                ),
                "docker-compose.yml": (
                    "services:\n"
                    "  app:\n"
                    "    image: ghcr.io/acme/agent:latest\n"
                ),
            }
            return contents[path]

    builder = RepositoryEvidenceBuilder(github_client=_SetupFilesOnlyGitHubClient())

    evidence = await builder.build("acme/agent")

    assert any("package.json" in snippet for snippet in evidence.setup_file_snippets)
    assert any("docker-compose.yml" in snippet for snippet in evidence.setup_file_snippets)
    assert "npm run dev" not in evidence.runtime_hints
    assert "npm run start" not in evidence.runtime_hints
    assert "docker compose up" not in evidence.runtime_hints


@pytest.mark.asyncio
async def test_repository_evidence_builder_ignores_headings_inside_fenced_code_blocks():
    class _FencedCodeReadmeGitHubClient(_GitHubClient):
        async def get_readme(self, full_name):
            assert full_name == "acme/agent"
            return (
                "# Agent\n\n"
                "```md\n"
                "# Setup\n\n"
                "Documented example output.\n"
                "```\n\n"
                "~~~text\n"
                "Getting Started\n"
                "---------------\n\n"
                "Sample transcript.\n"
                "~~~\n\n"
                "## Installation\n\n"
                "uv sync\n"
            )

        async def list_root_paths(self, full_name):
            del full_name
            return ["README.md"]

        async def get_file_content(self, full_name, path):
            del full_name, path
            raise AssertionError("no root file fetch expected")

    builder = RepositoryEvidenceBuilder(github_client=_FencedCodeReadmeGitHubClient())

    evidence = await builder.build("acme/agent")

    assert any(
        section.startswith("Installation: uv sync")
        for section in evidence.readme_setup_sections
    )
    assert not any(
        section.startswith("Setup:")
        for section in evidence.readme_setup_sections
    )
    assert not any(
        section.startswith("Getting Started:")
        for section in evidence.readme_setup_sections
    )


@pytest.mark.asyncio
async def test_repository_evidence_builder_keeps_metadata_when_auxiliary_fetches_fail():
    class _PartiallyFailingGitHubClient(_GitHubClient):
        async def get_readme(self, full_name):
            del full_name
            raise RuntimeError("readme unavailable")

        async def list_releases(self, full_name, per_page=3):
            del full_name, per_page
            raise RuntimeError("releases unavailable")

        async def list_recent_commits(self, full_name, per_page=5):
            del full_name, per_page
            raise RuntimeError("commits unavailable")

        async def list_root_paths(self, full_name):
            del full_name
            raise RuntimeError("paths unavailable")

        async def get_file_content(self, full_name, path):
            del full_name, path
            raise RuntimeError("file unavailable")

    builder = RepositoryEvidenceBuilder(github_client=_PartiallyFailingGitHubClient())

    evidence = await builder.build("acme/agent")

    assert evidence.full_name == "acme/agent"
    assert evidence.repo_url == "https://github.com/acme/agent"
    assert evidence.readme_excerpt == ""
    assert evidence.releases == []
    assert evidence.recent_commits == []
    assert evidence.key_paths == []
    assert evidence.readme_setup_sections == []
    assert evidence.setup_file_snippets == []
    assert evidence.runtime_hints == []


@pytest.mark.asyncio
async def test_repository_evidence_builder_skips_zero_limit_requests():
    class _TrackingGitHubClient(_GitHubClient):
        def __init__(self):
            self.readme_calls = 0
            self.release_calls = 0
            self.commit_calls = 0
            self.root_paths_calls = 0

        async def get_readme(self, full_name):
            del full_name
            self.readme_calls += 1
            return "readme"

        async def list_releases(self, full_name, per_page=3):
            del full_name, per_page
            self.release_calls += 1
            return ["v1.0.0"]

        async def list_recent_commits(self, full_name, per_page=5):
            del full_name, per_page
            self.commit_calls += 1
            return ["commit"]

        async def list_root_paths(self, full_name):
            del full_name
            self.root_paths_calls += 1
            return ["src"]

        async def get_file_content(self, full_name, path):
            del full_name, path
            raise AssertionError("file content should not be requested")

    client = _TrackingGitHubClient()
    builder = RepositoryEvidenceBuilder(
        github_client=client,
        readme_char_limit=0,
        release_limit=0,
        commit_limit=0,
    )

    evidence = await builder.build("acme/agent")

    assert client.readme_calls == 0
    assert client.release_calls == 0
    assert client.commit_calls == 0
    assert client.root_paths_calls == 1
    assert evidence.readme_excerpt == ""
    assert evidence.releases == []
    assert evidence.recent_commits == []
    assert evidence.key_paths == ["src"]
    assert evidence.readme_setup_sections == []
    assert evidence.setup_file_snippets == []
    assert evidence.runtime_hints == []


def test_repository_evidence_prompt_block_includes_onboarding_sections_and_missing_placeholder():
    evidence = RepositoryEvidence(
        full_name="acme/agent",
        repo_url="https://github.com/acme/agent",
        readme_excerpt="README says run demo first.",
        readme_setup_sections=["Quick Start: make dev"],
        setup_file_snippets=["Makefile: dev:\n\tuv run repo-pulse --help"],
        runtime_hints=["make dev"],
    )

    block = evidence.to_prompt_block()

    assert "仓库一手证据" in block
    assert "主页：" in block
    assert "主题：" in block
    assert "发布版本：" in block
    assert "最近提交：" in block
    assert "根目录路径：" in block
    assert "README 上手片段：" in block
    assert "根目录配置线索：" in block
    assert "运行提示：" in block
    assert "README says run demo first." in block
    assert "Quick Start: make dev" in block
    assert "Makefile: dev:" in block
    assert "make dev" in block
    assert "信息不足以确认" in block
    assert "Homepage：" not in block
    assert "Topics：" not in block
    assert "Releases：" not in block
    assert "Recent commits：" not in block
    assert "Root paths：" not in block
