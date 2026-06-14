"""Tests for live GitHub PR metadata fetch + TTL cache (bu-raryf).

Covers:
- ``parse_pr_url`` parsing of GitHub PR URLs.
- ``_map_check_runs_to_ci_status`` collapsing check-runs into a compact status.
- ``GithubPrClient.fetch`` populating additions/deletions + ci_status from a
  mocked GitHub API.
- Graceful degradation: no token / GitHub error / non-200 -> unavailable.
- TTL cache: a second call within the window does not re-hit the API.
"""

from __future__ import annotations

import httpx
import pytest

from butlers.core.qa.github_pr import (
    GithubPrClient,
    PrMetadata,
    _map_check_runs_to_ci_status,
    parse_pr_url,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# parse_pr_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/Tzeusy/butlers/pull/1653", ("Tzeusy", "butlers", 1653)),
        ("https://github.com/owner/repo.git/pull/7", ("owner", "repo", 7)),
        ("http://github.com/a-b/c_d/pull/42/files", ("a-b", "c_d", 42)),
        (None, None),
        ("", None),
        ("https://github.com/owner/repo", None),
        ("not a url", None),
    ],
)
def test_parse_pr_url(url, expected) -> None:
    assert parse_pr_url(url) == expected


# ---------------------------------------------------------------------------
# _map_check_runs_to_ci_status
# ---------------------------------------------------------------------------


def test_map_check_runs_empty_is_unknown() -> None:
    assert _map_check_runs_to_ci_status([]) == "unknown"


def test_map_check_runs_incomplete_is_pending() -> None:
    runs = [
        {"status": "completed", "conclusion": "success"},
        {"status": "in_progress", "conclusion": None},
    ]
    assert _map_check_runs_to_ci_status(runs) == "pending"


def test_map_check_runs_failure_is_failing() -> None:
    runs = [
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "failure"},
    ]
    assert _map_check_runs_to_ci_status(runs) == "failing"


def test_map_check_runs_all_success_is_passing() -> None:
    runs = [
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "skipped"},
    ]
    assert _map_check_runs_to_ci_status(runs) == "passing"


def test_map_check_runs_only_neutral_is_unknown() -> None:
    runs = [{"status": "completed", "conclusion": "neutral"}]
    assert _map_check_runs_to_ci_status(runs) == "unknown"


# ---------------------------------------------------------------------------
# GithubPrClient.fetch — mocked transport
# ---------------------------------------------------------------------------


def _passing_transport(call_counter: list[int]) -> httpx.MockTransport:
    """A mock transport returning a passing PR with +12/-3 diff stats."""

    def handler(request: httpx.Request) -> httpx.Response:
        call_counter.append(1)
        path = request.url.path
        if path.endswith("/pulls/1653"):
            return httpx.Response(
                200,
                json={
                    "additions": 12,
                    "deletions": 3,
                    "head": {"sha": "deadbeef"},
                },
            )
        if "/check-runs" in path:
            return httpx.Response(
                200,
                json={"check_runs": [{"status": "completed", "conclusion": "success"}]},
            )
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


async def test_fetch_populates_ci_status_and_diff_stats() -> None:
    calls: list[int] = []
    client = GithubPrClient(transport=_passing_transport(calls))

    meta = await client.fetch("Tzeusy", "butlers", 1653, token="t0ken")

    assert meta == PrMetadata(ci_status="passing", additions=12, deletions=3)
    # One call for the PR, one for check-runs.
    assert sum(calls) == 2


async def test_fetch_caches_within_ttl() -> None:
    calls: list[int] = []
    client = GithubPrClient(transport=_passing_transport(calls))

    first = await client.fetch("Tzeusy", "butlers", 1653, token="t0ken")
    before = sum(calls)
    second = await client.fetch("Tzeusy", "butlers", 1653, token="t0ken")
    after = sum(calls)

    assert first == second
    # No additional API calls on the cached second fetch.
    assert after == before


async def test_fetch_no_token_is_unavailable_and_no_api_call() -> None:
    calls: list[int] = []
    client = GithubPrClient(transport=_passing_transport(calls))

    meta = await client.fetch("Tzeusy", "butlers", 1653, token=None)

    assert meta == PrMetadata(ci_status=None, additions=None, deletions=None)
    assert sum(calls) == 0


async def test_fetch_http_error_degrades_gracefully() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = GithubPrClient(transport=httpx.MockTransport(handler))
    meta = await client.fetch("Tzeusy", "butlers", 1653, token="t0ken")

    assert meta == PrMetadata(ci_status=None, additions=None, deletions=None)


async def test_fetch_404_pr_degrades_gracefully() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = GithubPrClient(transport=httpx.MockTransport(handler))
    meta = await client.fetch("Tzeusy", "butlers", 9999, token="t0ken")

    assert meta == PrMetadata(ci_status=None, additions=None, deletions=None)
