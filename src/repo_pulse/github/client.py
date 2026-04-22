import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from pydantic import HttpUrl, TypeAdapter, ValidationError

from repo_pulse.schemas import RepositoryCandidate, RepositoryMetadata

UTC = timezone.utc


@dataclass(frozen=True)
class StargazerVerificationResult:
    count: int = 0
    verified: bool = False
    truncated: bool = False
    failed_reason: str | None = None


class GitHubClient:
    _http_url_adapter = TypeAdapter(HttpUrl)

    def __init__(self, token: str, base_url: str = "https://api.github.com"):
        self.token = token
        self.base_url = base_url

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _get(self, path: str, params: dict | None = None):
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers(),
            timeout=20.0,
        ) as client:
            response = await client.get(path, params=params)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def _post_graphql(self, query: str, variables: dict):
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers(),
            timeout=20.0,
        ) as client:
            response = await client.post(
                "/graphql",
                json={"query": query, "variables": variables},
            )
            response.raise_for_status()
            return response.json()

    async def search_repositories(
        self,
        query: str,
        per_page: int = 50,
        sort: str = "updated",
        order: str = "desc",
    ) -> list[RepositoryCandidate]:
        payload = await self._get(
            "/search/repositories",
            params={"q": query, "sort": sort, "order": order, "per_page": per_page},
        )
        if payload is None:
            return []

        return [
            RepositoryCandidate(
                full_name=item["full_name"],
                name=item["name"],
                owner=item["owner"]["login"],
                description=item.get("description"),
                html_url=item["html_url"],
                language=item.get("language"),
                topics=item.get("topics", []),
                stars=item["stargazers_count"],
                forks=item["forks_count"],
                watchers=item["watchers_count"],
                created_at=item.get("created_at"),
                pushed_at=item.get("pushed_at"),
                is_template=bool(item.get("is_template", False)),
            )
            for item in payload.get("items", [])
        ]

    async def count_recent_stargazers(
        self,
        full_name: str,
        *,
        now: datetime,
        page_size: int = 100,
        max_pages: int = 20,
    ) -> StargazerVerificationResult:
        if not self.token:
            return StargazerVerificationResult(failed_reason="missing_token")

        if "/" not in full_name:
            return StargazerVerificationResult(failed_reason="invalid_full_name")

        owner, name = full_name.split("/", 1)
        cutoff = self._ensure_utc(now) - timedelta(hours=24)
        page_size = max(1, min(int(page_size or 100), 100))
        max_pages = max(1, int(max_pages or 1))
        count = 0
        after = None
        crossed_cutoff = False

        query = """
        query RepoPulseRecentStargazers(
          $owner: String!,
          $name: String!,
          $pageSize: Int!,
          $after: String
        ) {
          repository(owner: $owner, name: $name) {
            stargazers(
              first: $pageSize,
              after: $after,
              orderBy: {field: STARRED_AT, direction: DESC}
            ) {
              pageInfo {
                hasNextPage
                endCursor
              }
              edges {
                starredAt
              }
            }
          }
        }
        """

        try:
            for page_index in range(max_pages):
                payload = await self._post_graphql(
                    query,
                    {
                        "owner": owner,
                        "name": name,
                        "pageSize": page_size,
                        "after": after,
                    },
                )
                if payload.get("errors"):
                    return StargazerVerificationResult(
                        count=count,
                        failed_reason="graphql_errors",
                    )

                repository = ((payload.get("data") or {}).get("repository"))
                if repository is None:
                    return StargazerVerificationResult(failed_reason="not_found")
                stargazers = repository.get("stargazers") or {}
                edges = stargazers.get("edges") or []
                for edge in edges:
                    starred_at = self._parse_datetime(edge.get("starredAt"))
                    if starred_at is None:
                        continue
                    if starred_at < cutoff:
                        crossed_cutoff = True
                        break
                    count += 1

                if crossed_cutoff:
                    return StargazerVerificationResult(count=count, verified=True)

                page_info = stargazers.get("pageInfo") or {}
                has_next_page = bool(page_info.get("hasNextPage"))
                after = page_info.get("endCursor")
                if not has_next_page or not after:
                    return StargazerVerificationResult(count=count, verified=True)

                if page_index == max_pages - 1:
                    return StargazerVerificationResult(
                        count=count,
                        verified=True,
                        truncated=True,
                    )
        except (httpx.HTTPError, TypeError, ValueError):
            return StargazerVerificationResult(
                count=count,
                failed_reason="request_failed",
            )

        return StargazerVerificationResult(count=count, verified=True)

    async def get_repository(self, full_name: str) -> RepositoryMetadata | None:
        payload = await self._get("/repos/{0}".format(full_name))
        if payload is None:
            return None

        homepage = self._parse_homepage(payload.get("homepage"))
        return RepositoryMetadata(
            full_name=payload["full_name"],
            name=payload["name"],
            owner=payload["owner"]["login"],
            description=payload.get("description"),
            html_url=payload["html_url"],
            homepage=homepage,
            language=payload.get("language"),
            topics=payload.get("topics", []),
            default_branch=payload.get("default_branch") or "main",
            stars=payload["stargazers_count"],
            forks=payload["forks_count"],
            watchers=payload["watchers_count"],
            pushed_at=payload.get("pushed_at"),
        )

    @classmethod
    def _parse_homepage(cls, value: object) -> HttpUrl | None:
        if not value:
            return None
        try:
            return cls._http_url_adapter.validate_python(value)
        except ValidationError:
            return None

    async def get_readme(self, full_name: str) -> str:
        payload = await self._get("/repos/{0}/readme".format(full_name))
        return self._decode_file_content(payload)

    async def get_file_content(self, full_name: str, path: str) -> str:
        payload = await self._get("/repos/{0}/contents/{1}".format(full_name, path.lstrip("/")))
        return self._decode_file_content(payload)

    async def list_releases(self, full_name: str, per_page: int = 3) -> list[str]:
        payload = await self._get(
            "/repos/{0}/releases".format(full_name),
            params={"per_page": per_page},
        )
        if not payload:
            return []
        return [
            "{0}: {1}".format(item.get("tag_name") or item.get("name") or "untagged", item.get("body") or "")
            for item in payload
        ]

    async def list_recent_commits(self, full_name: str, per_page: int = 5) -> list[str]:
        payload = await self._get(
            "/repos/{0}/commits".format(full_name),
            params={"per_page": per_page},
        )
        if not payload:
            return []
        commit_messages: list[str] = []
        for item in payload:
            message = ((item.get("commit") or {}).get("message") or "").strip()
            if not message:
                continue
            first_line = message.splitlines()[0].strip()
            if first_line:
                commit_messages.append(first_line)
        return commit_messages

    async def list_root_paths(self, full_name: str) -> list[str]:
        payload = await self._get("/repos/{0}/contents/".format(full_name))
        if not payload:
            return []
        return [item.get("name") for item in payload if item.get("name")]

    @staticmethod
    def _decode_file_content(payload: object) -> str:
        if not isinstance(payload, dict):
            return ""

        content = payload.get("content") or ""
        if payload.get("encoding") == "base64" and content:
            try:
                return base64.b64decode(content).decode("utf-8", errors="ignore")
            except Exception:
                return ""
        return str(content)

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
