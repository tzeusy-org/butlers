"""Tests for search API endpoint.

Verifies the API contract (status codes, response shapes) for the
cross-butler fan-out search endpoint, including result grouping,
snippet extraction, empty queries, and empty results.

Issues: butlers-26h.8.4, 8.5, 8.6
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.search import _extract_snippet, _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _make_session_search_row(
    *,
    session_id=None,
    prompt="test prompt",
    result="test result",
    trigger_source="schedule",
    success=True,
    started_at=None,
    duration_ms=1000,
    matched_field="prompt",
):
    """Create a dict mimicking an asyncpg Record for session search results."""
    return {
        "id": session_id or uuid4(),
        "prompt": prompt,
        "result": result,
        "trigger_source": trigger_source,
        "success": success,
        "started_at": started_at or _NOW,
        "duration_ms": duration_ms,
        "matched_field": matched_field,
    }


def _make_state_search_row(
    *,
    key="test_key",
    value_text='{"foo": "bar"}',
    updated_at=None,
    matched_field="key",
):
    """Create a dict mimicking an asyncpg Record for state search results."""
    return {
        "key": key,
        "value_text": value_text,
        "updated_at": updated_at or _NOW,
        "matched_field": matched_field,
    }


def _app_with_mock_db(
    *,
    fan_out_results: list[dict[str, list]] | None = None,
):
    """Create a FastAPI app with a mocked DatabaseManager.

    fan_out_results is a list of dicts — one per fan_out call in order.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas", "switchboard"]

    if fan_out_results is not None:
        mock_db.fan_out = AsyncMock(side_effect=fan_out_results)
    else:
        mock_db.fan_out = AsyncMock(return_value={})

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app


# ---------------------------------------------------------------------------
# Unit tests: _extract_snippet
# ---------------------------------------------------------------------------


class TestExtractSnippet:
    def test_snippet_centered_on_match(self):
        """Snippet should be centered around the first match."""
        text = "a" * 100 + "MATCH" + "b" * 100
        snippet = _extract_snippet(text, "MATCH", max_len=40)
        assert "MATCH" in snippet

    def test_snippet_with_no_match_returns_start(self):
        """When query not found, return start of text."""
        text = "Hello world, this is a long text"
        snippet = _extract_snippet(text, "MISSING", max_len=10)
        assert snippet.startswith("Hello")

    def test_snippet_short_text_unchanged(self):
        """Short text should be returned without ellipsis."""
        text = "short"
        snippet = _extract_snippet(text, "short", max_len=200)
        assert snippet == "short"

    def test_snippet_empty_text(self):
        """Empty text should return empty string."""
        assert _extract_snippet("", "query") == ""

    def test_snippet_adds_ellipsis(self):
        """Snippet should add ellipsis when trimmed."""
        text = "x" * 50 + "MATCH" + "y" * 50
        snippet = _extract_snippet(text, "MATCH", max_len=20)
        assert "..." in snippet


# ---------------------------------------------------------------------------
# Tests: Response structure
# ---------------------------------------------------------------------------


class TestSearchResponseStructure:
    async def test_returns_grouped_results(self):
        """Response must have 'sessions' and 'state' arrays."""
        app = _app_with_mock_db(fan_out_results=[{}, {}])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "test"})

        assert resp.status_code == 200
        body = resp.json()
        assert "sessions" in body
        assert "state" in body
        assert isinstance(body["sessions"], list)
        assert isinstance(body["state"], list)

    async def test_empty_query_returns_empty_results(self):
        """An empty query string should return empty groups immediately."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": ""})

        assert resp.status_code == 200
        body = resp.json()
        assert body["sessions"] == []
        assert body["state"] == []

    async def test_whitespace_query_returns_empty_results(self):
        """A whitespace-only query should return empty groups."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "   "})

        assert resp.status_code == 200
        body = resp.json()
        assert body["sessions"] == []
        assert body["state"] == []


# ---------------------------------------------------------------------------
# Tests: Fan-out search
# ---------------------------------------------------------------------------


class TestSearchFanOut:
    async def test_sessions_search_results(self):
        """Session search results should include butler, matched_field, snippet, data."""
        sid = uuid4()
        row = _make_session_search_row(
            session_id=sid,
            prompt="deploy the new feature",
            matched_field="prompt",
        )

        app = _app_with_mock_db(
            fan_out_results=[
                {"atlas": [row]},  # session fan_out
                {},  # state fan_out
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "deploy"})

        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 1
        result = sessions[0]
        assert result["butler"] == "atlas"
        assert result["matched_field"] == "prompt"
        assert "deploy" in result["snippet"]
        assert result["data"]["id"] == str(sid)

    async def test_state_search_results(self):
        """State search results should include butler, matched_field, snippet, data."""
        row = _make_state_search_row(
            key="last_deploy_time",
            value_text="2025-01-15T10:00:00Z",
            matched_field="key",
        )

        app = _app_with_mock_db(
            fan_out_results=[
                {},  # session fan_out
                {"atlas": [row]},  # state fan_out
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "deploy"})

        assert resp.status_code == 200
        state = resp.json()["state"]
        assert len(state) == 1
        result = state[0]
        assert result["butler"] == "atlas"
        assert result["matched_field"] == "key"
        assert "deploy" in result["snippet"]
        assert result["data"]["key"] == "last_deploy_time"

    async def test_cross_butler_results(self):
        """Results from multiple butlers should all appear."""
        atlas_row = _make_session_search_row(prompt="atlas deploy")
        sw_row = _make_session_search_row(prompt="switchboard deploy")

        app = _app_with_mock_db(
            fan_out_results=[
                {"atlas": [atlas_row], "switchboard": [sw_row]},  # session fan_out
                {},  # state fan_out
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "deploy"})

        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 2
        butlers = {s["butler"] for s in sessions}
        assert butlers == {"atlas", "switchboard"}

    async def test_no_results_found(self):
        """When no matches are found, return empty groups."""
        app = _app_with_mock_db(
            fan_out_results=[
                {},  # session fan_out (no results)
                {},  # state fan_out (no results)
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "nonexistent"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["sessions"] == []
        assert body["state"] == []


# ---------------------------------------------------------------------------
# Tests: Result grouping and limits
# ---------------------------------------------------------------------------


class TestSearchResultGrouping:
    async def test_results_grouped_by_category(self):
        """Sessions and state results should be in separate groups."""
        session_row = _make_session_search_row(prompt="search target in session")
        state_row = _make_state_search_row(key="search_target_key")

        app = _app_with_mock_db(
            fan_out_results=[
                {"atlas": [session_row]},  # session fan_out
                {"atlas": [state_row]},  # state fan_out
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "search"})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["sessions"]) == 1
        assert len(body["state"]) == 1

    async def test_limit_parameter_respected(self):
        """The limit parameter should cap the number of results per category."""
        rows = [_make_session_search_row(prompt=f"match {i}") for i in range(10)]

        app = _app_with_mock_db(
            fan_out_results=[
                {"atlas": rows},  # session fan_out
                {},  # state fan_out
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "match", "limit": 3})

        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) <= 3

    async def test_result_matched_field_prompt(self):
        """When a session matches on prompt, matched_field should be 'prompt'."""
        row = _make_session_search_row(
            prompt="specific query text",
            result="other text",
            matched_field="prompt",
        )

        app = _app_with_mock_db(
            fan_out_results=[
                {"atlas": [row]},
                {},
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "specific"})

        assert resp.status_code == 200
        result = resp.json()["sessions"][0]
        assert result["matched_field"] == "prompt"

    async def test_result_matched_field_result(self):
        """When a session matches on result, matched_field should be 'result'."""
        row = _make_session_search_row(
            prompt="other text",
            result="specific query text",
            matched_field="result",
        )

        app = _app_with_mock_db(
            fan_out_results=[
                {"atlas": [row]},
                {},
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "specific"})

        assert resp.status_code == 200
        result = resp.json()["sessions"][0]
        assert result["matched_field"] == "result"
        assert "specific" in result["snippet"]
