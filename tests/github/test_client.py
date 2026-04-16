from datetime import datetime, timezone

import httpx
import pytest
import respx

from repo_pulse.github.client import GitHubClient


@respx.mock
@pytest.mark.asyncio
async def test_github_client_get_repository_contract():
    route = respx.get("https://api.github.com/repos/acme/agent").mock(
        return_value=httpx.Response(
            200,
            json={
                "full_name": "acme/agent",
                "name": "agent",
                "owner": {"login": "acme"},
                "description": "Agent runtime",
                "html_url": "https://github.com/acme/agent",
                "homepage": "https://agent.acme.dev",
                "language": "Python",
                "topics": ["ai", "agents"],
                "default_branch": "main",
                "stargazers_count": 180,
                "forks_count": 20,
                "watchers_count": 11,
                "pushed_at": "2026-04-13T01:00:00Z",
            },
        )
    )

    client = GitHubClient(token="test-token")
    repo = await client.get_repository("acme/agent")

    assert route.called
    assert repo.full_name == "acme/agent"
    assert str(repo.homepage) == "https://agent.acme.dev/"
    assert repo.default_branch == "main"
    assert repo.pushed_at == datetime(2026, 4, 13, 1, 0, tzinfo=timezone.utc)


@respx.mock
@pytest.mark.asyncio
async def test_github_client_fetches_readme_releases_commits_and_root_paths():
    respx.get("https://api.github.com/repos/acme/agent/readme").mock(
        return_value=httpx.Response(
            200,
            json={"content": "IyBBQ01FIGFnZW50CgpydW4gZGVtbyBmaXJzdAo=", "encoding": "base64"},
        )
    )
    respx.get("https://api.github.com/repos/acme/agent/releases").mock(
        return_value=httpx.Response(
            200,
            json=[{"tag_name": "v1.2.0", "name": "v1.2.0", "body": "Stability fixes"}],
        )
    )
    respx.get("https://api.github.com/repos/acme/agent/commits").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"commit": {"message": "ship eval dashboard"}},
                {"commit": {"message": "tighten retry policy"}},
            ],
        )
    )
    respx.get("https://api.github.com/repos/acme/agent/contents/").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"type": "dir", "name": "docs"},
                {"type": "dir", "name": "examples"},
                {"type": "file", "name": "README.md"},
            ],
        )
    )

    client = GitHubClient(token="test-token")

    readme = await client.get_readme("acme/agent")
    releases = await client.list_releases("acme/agent", per_page=1)
    commits = await client.list_recent_commits("acme/agent", per_page=2)
    paths = await client.list_root_paths("acme/agent")

    assert "run demo first" in readme
    assert releases == ["v1.2.0: Stability fixes"]
    assert commits == ["ship eval dashboard", "tighten retry policy"]
    assert paths == ["docs", "examples", "README.md"]


@respx.mock
@pytest.mark.asyncio
async def test_github_client_fetches_and_decodes_root_file_content():
    respx.get("https://api.github.com/repos/acme/agent/contents/Makefile").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": "aW5zdGFsbDoKXHR1diBzeW5jCgpkZXY6CgkJdXYgcnVuIHJlcG8tcHVsc2UgLS1oZWxwCg==",
                "encoding": "base64",
            },
        )
    )

    client = GitHubClient(token="test-token")
    content = await client.get_file_content("acme/agent", "Makefile")

    assert "install:" in content
    assert "uv sync" in content
    assert "uv run repo-pulse --help" in content


@respx.mock
@pytest.mark.asyncio
async def test_github_client_get_repository_returns_none_on_404():
    route = respx.get("https://api.github.com/repos/acme/missing").mock(return_value=httpx.Response(404))

    client = GitHubClient(token="test-token")
    repo = await client.get_repository("acme/missing")

    assert route.called
    assert repo is None


@respx.mock
@pytest.mark.asyncio
async def test_github_client_get_repository_ignores_invalid_homepage():
    respx.get("https://api.github.com/repos/acme/agent").mock(
        return_value=httpx.Response(
            200,
            json={
                "full_name": "acme/agent",
                "name": "agent",
                "owner": {"login": "acme"},
                "description": "Agent runtime",
                "html_url": "https://github.com/acme/agent",
                "homepage": "agent.acme.dev",
                "language": "Python",
                "topics": ["ai", "agents"],
                "default_branch": "main",
                "stargazers_count": 180,
                "forks_count": 20,
                "watchers_count": 11,
                "pushed_at": "2026-04-13T01:00:00Z",
            },
        )
    )

    client = GitHubClient(token="test-token")
    repo = await client.get_repository("acme/agent")

    assert repo is not None
    assert repo.homepage is None


@respx.mock
@pytest.mark.asyncio
async def test_github_client_list_recent_commits_returns_first_line_only():
    respx.get("https://api.github.com/repos/acme/agent/commits").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"commit": {"message": "feat: add dashboard\n\nIncludes charts and filters"}},
                {"commit": {"message": "fix: handle timeout\nMore details"}},
            ],
        )
    )

    client = GitHubClient(token="test-token")
    commits = await client.list_recent_commits("acme/agent", per_page=2)

    assert commits == ["feat: add dashboard", "fix: handle timeout"]


@respx.mock
@pytest.mark.asyncio
async def test_github_client_get_file_content_returns_empty_string_on_404():
    respx.get("https://api.github.com/repos/acme/agent/contents/.env.example").mock(
        return_value=httpx.Response(404)
    )

    client = GitHubClient(token="test-token")
    content = await client.get_file_content("acme/agent", ".env.example")

    assert content == ""
