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
from fastapi import FastAPI

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


def _make_entity_search_row(
    *,
    entity_id=None,
    canonical_name="Test Entity",
    entity_type="person",
    aliases=None,
):
    """Create a dict mimicking an asyncpg Record for entity search results."""
    return {
        "id": entity_id or uuid4(),
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "aliases": aliases or [],
    }


def _make_contact_search_row(
    *,
    contact_id=None,
    name="Test Contact",
    email=None,
    phone=None,
):
    """Create a dict mimicking an asyncpg Record for contact search results."""
    return {
        "id": contact_id or uuid4(),
        "name": name,
        "email": email,
        "phone": phone,
    }


def _app_with_mock_db(
    app: FastAPI,
    *,
    fan_out_results: list[dict[str, list]] | None = None,
    shared_pool_results: list[list] | None = None,
) -> FastAPI:
    """Wire a FastAPI app with a mocked DatabaseManager.

    Accepts the shared module-scoped ``app`` fixture so that create_app()
    is not called per test.  fan_out_results is a list of dicts — one per
    fan_out call in order.  shared_pool_results is a list of row lists — one
    per pool.fetch() call in order (for entity/contact queries).
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas", "switchboard"]

    if fan_out_results is not None:
        mock_db.fan_out = AsyncMock(side_effect=fan_out_results)
    else:
        mock_db.fan_out = AsyncMock(return_value={})

    # Mock pool() for shared-schema queries (entities, contacts)
    mock_pool = MagicMock()
    if shared_pool_results is not None:
        mock_pool.fetch = AsyncMock(side_effect=shared_pool_results)
    else:
        mock_pool.fetch = AsyncMock(return_value=[])
    mock_db.pool = MagicMock(return_value=mock_pool)

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
    async def test_returns_grouped_results(self, app):
        """Response must have all category arrays inside 'data' envelope."""
        _app_with_mock_db(app, fan_out_results=[{}, {}])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "test"})

        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert "entities" in data
        assert "contacts" in data
        assert "sessions" in data
        assert "state" in data
        for key in ("entities", "contacts", "sessions", "state"):
            assert isinstance(data[key], list)

    async def test_empty_query_returns_empty_results(self, app):
        """An empty query string should return empty groups immediately."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": ""})

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["entities"] == []
        assert data["contacts"] == []
        assert data["sessions"] == []
        assert data["state"] == []

    async def test_whitespace_query_returns_empty_results(self, app):
        """A whitespace-only query should return empty groups."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "   "})

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["entities"] == []
        assert data["contacts"] == []
        assert data["sessions"] == []
        assert data["state"] == []


# ---------------------------------------------------------------------------
# Tests: Fan-out search
# ---------------------------------------------------------------------------


class TestSearchFanOut:
    async def test_sessions_search_results(self, app):
        """Session search results should include id, butler, type, title, snippet, url."""
        sid = uuid4()
        row = _make_session_search_row(
            session_id=sid,
            prompt="deploy the new feature",
            matched_field="prompt",
        )

        _app_with_mock_db(
            app,
            fan_out_results=[
                {"atlas": [row]},  # session fan_out
                {},  # state fan_out
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "deploy"})

        assert resp.status_code == 200
        sessions = resp.json()["data"]["sessions"]
        assert len(sessions) == 1
        result = sessions[0]
        assert result["id"] == str(sid)
        assert result["butler"] == "atlas"
        assert result["type"] == "session"
        assert "deploy" in result["title"]
        assert "deploy" in result["snippet"]
        assert result["url"] == f"/sessions/{sid}"

    async def test_state_search_results(self, app):
        """State search results should include id, butler, type, title, snippet, url."""
        row = _make_state_search_row(
            key="last_deploy_time",
            value_text="2025-01-15T10:00:00Z",
            matched_field="key",
        )

        _app_with_mock_db(
            app,
            fan_out_results=[
                {},  # session fan_out
                {"atlas": [row]},  # state fan_out
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "deploy"})

        assert resp.status_code == 200
        state = resp.json()["data"]["state"]
        assert len(state) == 1
        result = state[0]
        assert result["butler"] == "atlas"
        assert result["type"] == "state"
        assert result["title"] == "last_deploy_time"
        assert "deploy" in result["snippet"]
        assert "/butlers/atlas" in result["url"]

    async def test_cross_butler_results(self, app):
        """Results from multiple butlers should all appear."""
        atlas_row = _make_session_search_row(prompt="atlas deploy")
        sw_row = _make_session_search_row(prompt="switchboard deploy")

        _app_with_mock_db(
            app,
            fan_out_results=[
                {"atlas": [atlas_row], "switchboard": [sw_row]},  # session fan_out
                {},  # state fan_out
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "deploy"})

        assert resp.status_code == 200
        sessions = resp.json()["data"]["sessions"]
        assert len(sessions) == 2
        butlers = {s["butler"] for s in sessions}
        assert butlers == {"atlas", "switchboard"}

    async def test_no_results_found(self, app):
        """When no matches are found, return empty groups."""
        _app_with_mock_db(
            app,
            fan_out_results=[
                {},  # session fan_out (no results)
                {},  # state fan_out (no results)
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "nonexistent"})

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sessions"] == []
        assert data["state"] == []


# ---------------------------------------------------------------------------
# Tests: Entity and contact search
# ---------------------------------------------------------------------------


class TestSearchEntitiesContacts:
    async def test_entity_search_results(self, app):
        """Entity search should return results with proper shape."""
        eid = uuid4()
        entity_row = _make_entity_search_row(
            entity_id=eid,
            canonical_name="Acme Corp",
            entity_type="organization",
            aliases=["ACME", "Acme Inc"],
        )

        _app_with_mock_db(
            app,
            shared_pool_results=[
                [entity_row],  # entity fetch
                [],  # contact fetch
            ],
            fan_out_results=[{}, {}],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "acme"})

        assert resp.status_code == 200
        entities = resp.json()["data"]["entities"]
        assert len(entities) == 1
        result = entities[0]
        assert result["id"] == str(eid)
        assert result["butler"] == "memory"
        assert result["type"] == "entity"
        assert result["title"] == "Acme Corp"
        assert "organization" in result["snippet"]
        assert result["url"] == f"/entities/{eid}"

    async def test_contact_search_results(self, app):
        """Contact search should return results with proper shape."""
        cid = uuid4()
        contact_row = _make_contact_search_row(
            contact_id=cid,
            name="Jane Doe",
            email="jane@example.com",
            phone="+1234567890",
        )

        _app_with_mock_db(
            app,
            shared_pool_results=[
                [],  # entity fetch
                [contact_row],  # contact fetch
            ],
            fan_out_results=[{}, {}],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "jane"})

        assert resp.status_code == 200
        contacts = resp.json()["data"]["contacts"]
        assert len(contacts) == 1
        result = contacts[0]
        assert result["id"] == str(cid)
        assert result["butler"] == "relationship"
        assert result["type"] == "contact"
        assert result["title"] == "Jane Doe"
        assert "jane@example.com" in result["snippet"]
        assert result["url"] == f"/contacts/{cid}"


# ---------------------------------------------------------------------------
# Tests: Result grouping and limits
# ---------------------------------------------------------------------------


class TestSearchResultGrouping:
    async def test_results_grouped_by_category(self, app):
        """Sessions and state results should be in separate groups."""
        session_row = _make_session_search_row(prompt="search target in session")
        state_row = _make_state_search_row(key="search_target_key")

        _app_with_mock_db(
            app,
            fan_out_results=[
                {"atlas": [session_row]},  # session fan_out
                {"atlas": [state_row]},  # state fan_out
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "search"})

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["sessions"]) == 1
        assert len(data["state"]) == 1

    async def test_limit_parameter_respected(self, app):
        """The limit parameter should cap the number of results per category."""
        rows = [_make_session_search_row(prompt=f"match {i}") for i in range(10)]

        _app_with_mock_db(
            app,
            fan_out_results=[
                {"atlas": rows},  # session fan_out
                {},  # state fan_out
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "match", "limit": 3})

        assert resp.status_code == 200
        sessions = resp.json()["data"]["sessions"]
        assert len(sessions) <= 3

    async def test_result_type_session(self, app):
        """Session results should have type='session'."""
        row = _make_session_search_row(
            prompt="specific query text",
            result="other text",
            matched_field="prompt",
        )

        _app_with_mock_db(
            app,
            fan_out_results=[
                {"atlas": [row]},
                {},
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "specific"})

        assert resp.status_code == 200
        result = resp.json()["data"]["sessions"][0]
        assert result["type"] == "session"
        assert "specific" in result["title"]

    async def test_result_snippet_from_result_field(self, app):
        """When a session matches on result, snippet should contain the match."""
        row = _make_session_search_row(
            prompt="other text",
            result="specific query text",
            matched_field="result",
        )

        _app_with_mock_db(
            app,
            fan_out_results=[
                {"atlas": [row]},
                {},
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search", params={"q": "specific"})

        assert resp.status_code == 200
        result = resp.json()["data"]["sessions"][0]
        assert "specific" in result["snippet"]
