from datetime import datetime, timezone

import httpx
import pytest
import respx

from repo_pulse.github.client import GitHubClient
from repo_pulse.github.discovery import DiscoveryService


class _FakeGitHubClient:
    def __init__(self, results_by_query):
        self.results_by_query = results_by_query
        self.calls = []

    async def search_repositories(
        self,
        query: str,
        per_page: int = 50,
        sort: str = "updated",
        order: str = "desc",
    ):
        self.calls.append((query, per_page, sort, order))
        return self.results_by_query.get((query, per_page, sort, order), [])


@pytest.mark.asyncio
async def test_collect_candidates_queries_three_channels_and_merges_discovery_sources():
    now = datetime(2026, 4, 15, 9, 30, tzinfo=timezone.utc)
    active_topic_candidate = {
        "full_name": "acme/agent",
        "name": "agent",
        "owner": "acme",
        "description": "An AI agent framework",
        "html_url": "https://github.com/acme/agent",
        "language": "Python",
        "topics": ["ai", "agents"],
        "stars": 180,
        "forks": 20,
        "watchers": 11,
        "created_at": datetime(2026, 4, 10, 1, 0, tzinfo=timezone.utc),
        "pushed_at": datetime(2026, 4, 13, 1, 0, tzinfo=timezone.utc),
    }
    new_hot_duplicate = {
        "full_name": "acme/agent",
        "name": "agent",
        "owner": "acme",
        "description": "Fresh AI agent framework",
        "html_url": "https://github.com/acme/agent",
        "language": "Python",
        "topics": ["ai", "agents"],
        "stars": 180,
        "forks": 20,
        "watchers": 11,
        "created_at": datetime(2026, 4, 10, 1, 0, tzinfo=timezone.utc),
        "pushed_at": datetime(2026, 4, 13, 1, 0, tzinfo=timezone.utc),
    }
    mover_candidate = {
        "full_name": "acme/mover",
        "name": "mover",
        "owner": "acme",
        "description": "Momentum repo",
        "html_url": "https://github.com/acme/mover",
        "language": "Python",
        "topics": ["ai"],
        "stars": 260,
        "forks": 30,
        "watchers": 11,
        "created_at": datetime(2026, 2, 10, 1, 0, tzinfo=timezone.utc),
        "pushed_at": datetime(2026, 4, 14, 4, 0, tzinfo=timezone.utc),
    }
    client = _FakeGitHubClient(
        results_by_query={
            ("topic:ai archived:false", 30, "updated", "desc"): [
                type("Candidate", (), active_topic_candidate)()
            ],
            ("topic:ai archived:false created:>=2026-03-16 stars:>=10", 30, "stars", "desc"): [
                type("Candidate", (), new_hot_duplicate)()
            ],
            ("topic:ai archived:false pushed:>=2026-04-08 stars:>=50", 30, "stars", "desc"): [
                type("Candidate", (), mover_candidate)()
            ],
        }
    )
    service = DiscoveryService(client=client, include_topics=["ai"])

    candidates = await service.collect_candidates(now=now)

    assert client.calls == [
        ("topic:ai archived:false", 30, "updated", "desc"),
        ("topic:ai archived:false created:>=2026-03-16 stars:>=10", 30, "stars", "desc"),
        ("topic:ai archived:false pushed:>=2026-04-08 stars:>=50", 30, "stars", "desc"),
    ]
    assert [candidate.full_name for candidate in candidates] == [
        "acme/agent",
        "acme/mover",
    ]
    assert candidates[0].discovery_sources == ["active_topic", "new_hot"]
    assert candidates[1].discovery_sources == ["established_mover"]


@respx.mock
@pytest.mark.asyncio
async def test_collect_candidates_returns_empty_when_no_topics():
    route = respx.get("https://api.github.com/search/repositories").mock(
        return_value=httpx.Response(200, json={"items": []})
    )

    client = GitHubClient(token="test-token")
    service = DiscoveryService(client=client, include_topics=[])

    candidates = await service.collect_candidates(now=datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc))

    assert not route.called
    assert candidates == []


@respx.mock
@pytest.mark.asyncio
async def test_github_client_search_repositories_contract():
    query = "topic:ai topic:agents archived:false"
    route = respx.get("https://api.github.com/search/repositories").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "full_name": "acme/agent",
                        "name": "agent",
                        "owner": {"login": "acme"},
                        "description": "An AI agent framework",
                        "html_url": "https://github.com/acme/agent",
                        "language": "Python",
                        "topics": ["ai", "agents"],
                        "stargazers_count": 180,
                        "forks_count": 20,
                        "watchers_count": 11,
                        "created_at": "2026-04-01T01:00:00Z",
                        "pushed_at": "2026-04-13T01:00:00Z",
                        "is_template": True,
                    }
                ]
            },
        )
    )

    client = GitHubClient(token="test-token")
    candidates = await client.search_repositories(query=query, per_page=30)

    assert route.called
    request = route.calls.last.request
    assert request.url.params["q"] == query
    assert request.url.params["sort"] == "updated"
    assert request.url.params["order"] == "desc"
    assert request.url.params["per_page"] == "30"
    assert request.headers["Authorization"] == "Bearer test-token"

    assert len(candidates) == 1
    repo = candidates[0]
    assert repo.owner == "acme"
    assert repo.stars == 180
    assert repo.forks == 20
    assert repo.watchers == 11
    assert repo.topics == ["ai", "agents"]
    assert repo.created_at == datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc)
    assert repo.is_template is True
    assert repo.pushed_at == datetime(2026, 4, 13, 1, 0, tzinfo=timezone.utc)
