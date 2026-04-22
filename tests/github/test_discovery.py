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


class _FakeSleep:
    def __init__(self):
        self.calls = []

    async def __call__(self, seconds: float):
        self.calls.append(seconds)


@pytest.mark.asyncio
async def test_collect_candidates_daily_queries_four_channels_and_merges_discovery_sources():
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
    viral_duplicate = {
        "full_name": "acme/agent",
        "name": "agent",
        "owner": "acme",
        "description": "Viral AI agent framework",
        "html_url": "https://github.com/acme/agent",
        "language": "Python",
        "topics": ["ai", "agents"],
        "stars": 220,
        "forks": 22,
        "watchers": 13,
        "created_at": datetime(2026, 4, 11, 1, 0, tzinfo=timezone.utc),
        "pushed_at": datetime(2026, 4, 15, 1, 0, tzinfo=timezone.utc),
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
            ("topic:ai archived:false", 15, "updated", "desc"): [
                type("Candidate", (), active_topic_candidate)()
            ],
            ("topic:ai archived:false created:>=2026-04-01 stars:>=5", 15, "updated", "desc"): [
                type("Candidate", (), new_hot_duplicate)()
            ],
            ("topic:ai archived:false pushed:>=2026-04-12 stars:>=30", 15, "updated", "desc"): [
                type("Candidate", (), mover_candidate)()
            ],
            ("topic:ai archived:false created:>=2026-04-01 stars:>=5", 10, "stars", "desc"): [
                type("Candidate", (), viral_duplicate)()
            ],
        }
    )
    service = DiscoveryService(client=client, include_topics=["ai"])

    candidates = await service.collect_candidates(now=now, kind="daily")

    assert client.calls == [
        ("topic:ai archived:false", 15, "updated", "desc"),
        ("topic:ai archived:false created:>=2026-04-01 stars:>=5", 15, "updated", "desc"),
        ("topic:ai archived:false pushed:>=2026-04-12 stars:>=30", 15, "updated", "desc"),
        ("topic:ai archived:false created:>=2026-04-01 stars:>=5", 10, "stars", "desc"),
    ]
    assert [candidate.full_name for candidate in candidates] == [
        "acme/agent",
        "acme/mover",
    ]
    assert candidates[0].discovery_sources == [
        "active_topic_recent",
        "new_hot_recent",
        "viral_recent_recall",
    ]
    assert candidates[1].discovery_sources == ["established_active"]


@pytest.mark.asyncio
async def test_collect_candidates_batches_requests_serially_and_sleeps_between_batches():
    now = datetime(2026, 4, 15, 9, 30, tzinfo=timezone.utc)
    sleeper = _FakeSleep()
    client = _FakeGitHubClient(results_by_query={})
    service = DiscoveryService(
        client=client,
        include_topics=["ai"],
        search_requests_per_window=2,
        search_window_seconds=7.0,
        sleep_func=sleeper,
    )

    await service.collect_candidates(now=now, kind="daily")

    assert client.calls == [
        ("topic:ai archived:false", 15, "updated", "desc"),
        ("topic:ai archived:false created:>=2026-04-01 stars:>=5", 15, "updated", "desc"),
        ("topic:ai archived:false pushed:>=2026-04-12 stars:>=30", 15, "updated", "desc"),
        ("topic:ai archived:false created:>=2026-04-01 stars:>=5", 10, "stars", "desc"),
    ]
    assert sleeper.calls == [7.0]


@pytest.mark.asyncio
async def test_collect_candidates_daily_uses_scheduler_timezone_for_date_cutoffs():
    now = datetime(2026, 4, 15, 16, 30, tzinfo=timezone.utc)
    client = _FakeGitHubClient(results_by_query={})
    service = DiscoveryService(
        client=client,
        include_topics=["ai"],
        scheduler_timezone="Asia/Shanghai",
    )

    await service.collect_candidates(now=now, kind="daily")

    assert client.calls == [
        ("topic:ai archived:false", 15, "updated", "desc"),
        ("topic:ai archived:false created:>=2026-04-02 stars:>=5", 15, "updated", "desc"),
        ("topic:ai archived:false pushed:>=2026-04-13 stars:>=30", 15, "updated", "desc"),
        ("topic:ai archived:false created:>=2026-04-02 stars:>=5", 10, "stars", "desc"),
    ]


@pytest.mark.asyncio
async def test_collect_candidates_weekly_keeps_existing_three_channels():
    now = datetime(2026, 4, 15, 9, 30, tzinfo=timezone.utc)
    client = _FakeGitHubClient(results_by_query={})
    service = DiscoveryService(client=client, include_topics=["ai"])

    await service.collect_candidates(now=now, kind="weekly")

    assert client.calls == [
        ("topic:ai archived:false", 30, "updated", "desc"),
        ("topic:ai archived:false created:>=2026-03-16 stars:>=10", 30, "stars", "desc"),
        ("topic:ai archived:false pushed:>=2026-04-08 stars:>=50", 30, "stars", "desc"),
    ]


@respx.mock
@pytest.mark.asyncio
async def test_collect_candidates_returns_empty_when_no_topics():
    route = respx.get("https://api.github.com/search/repositories").mock(
        return_value=httpx.Response(200, json={"items": []})
    )

    client = GitHubClient(token="test-token")
    service = DiscoveryService(client=client, include_topics=[])

    candidates = await service.collect_candidates(
        now=datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc),
        kind="daily",
    )

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


@respx.mock
@pytest.mark.asyncio
async def test_github_client_count_recent_stargazers_stops_when_crossing_cutoff():
    route = respx.post("https://api.github.com/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "stargazers": {
                                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                                "edges": [
                                    {"starredAt": "2026-04-15T08:00:00Z"},
                                    {"starredAt": "2026-04-14T18:00:00Z"},
                                    {"starredAt": "2026-04-14T08:00:00Z"},
                                ],
                            }
                        }
                    }
                },
            )
        ]
    )

    client = GitHubClient(token="test-token")
    result = await client.count_recent_stargazers(
        "acme/agent",
        now=datetime(2026, 4, 15, 9, 30, tzinfo=timezone.utc),
        page_size=100,
        max_pages=20,
    )

    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-token"
    assert result.verified is True
    assert result.truncated is False
    assert result.count == 2
    assert result.failed_reason is None


@respx.mock
@pytest.mark.asyncio
async def test_github_client_count_recent_stargazers_marks_truncated_when_page_budget_exhausted():
    route = respx.post("https://api.github.com/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "stargazers": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                            "edges": [
                                {"starredAt": "2026-04-15T08:00:00Z"},
                                {"starredAt": "2026-04-15T07:00:00Z"},
                            ],
                        }
                    }
                }
            },
        )
    )

    client = GitHubClient(token="test-token")
    result = await client.count_recent_stargazers(
        "acme/agent",
        now=datetime(2026, 4, 15, 9, 30, tzinfo=timezone.utc),
        page_size=2,
        max_pages=1,
    )

    assert route.called
    assert result.verified is True
    assert result.truncated is True
    assert result.count == 2


@pytest.mark.asyncio
async def test_github_client_count_recent_stargazers_returns_fallback_state_without_token():
    client = GitHubClient(token="")

    result = await client.count_recent_stargazers(
        "acme/agent",
        now=datetime(2026, 4, 15, 9, 30, tzinfo=timezone.utc),
        page_size=100,
        max_pages=20,
    )

    assert result.verified is False
    assert result.truncated is False
    assert result.count == 0
    assert result.failed_reason == "missing_token"
