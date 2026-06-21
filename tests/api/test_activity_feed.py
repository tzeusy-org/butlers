"""Tests for GET /api/butlers/{name}/activity-feed.

Verifies:
- Merged + sorted desc by ts from all three sources (sessions, pending_actions, episodes)
- Limit param: returns at most N events; default 10; cap at 50
- Butler with no activity returns events=[]
- Each event_type is correctly populated from its source
- 503 when butler DB pool not registered
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from asyncpg.exceptions import UndefinedTableError

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.activity_feed import _get_db_manager, _normalize_tz

pytestmark = pytest.mark.unit

_BASE = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Row factories
# ---------------------------------------------------------------------------


def _session_row(
    *,
    offset_minutes: int = 0,
    prompt: str = "Check emails",
    trigger_source: str = "scheduler",
    success: bool = True,
    duration_ms: int = 500,
) -> MagicMock:
    """Return a mock asyncpg Record for the sessions table."""
    ts = _BASE + timedelta(minutes=offset_minutes)
    data = {
        "id": uuid.uuid4(),
        "prompt": prompt,
        "trigger_source": trigger_source,
        "success": success,
        "started_at": ts,
        "completed_at": ts,
        "duration_ms": duration_ms,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _action_row(
    *,
    offset_minutes: int = 0,
    tool_name: str = "send_email",
    agent_summary: str | None = "Send a weekly report",
    status: str = "pending",
) -> MagicMock:
    """Return a mock asyncpg Record for the pending_actions table."""
    ts = _BASE + timedelta(minutes=offset_minutes)
    data = {
        "id": uuid.uuid4(),
        "tool_name": tool_name,
        "agent_summary": agent_summary,
        "status": status,
        "requested_at": ts,
        "session_id": None,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _episode_row(
    *,
    offset_minutes: int = 0,
    content: str = "User prefers morning reports",
    importance: float = 5.0,
    consolidation_status: str = "pending",
) -> MagicMock:
    """Return a mock asyncpg Record for the episodes table."""
    ts = _BASE + timedelta(minutes=offset_minutes)
    data = {
        "id": uuid.uuid4(),
        "content": content,
        "importance": importance,
        "consolidation_status": consolidation_status,
        "created_at": ts,
        "session_id": None,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


# ---------------------------------------------------------------------------
# App builder
# ---------------------------------------------------------------------------


def _make_app(
    *,
    session_rows: list | None = None,
    action_rows: list | None = None,
    episode_rows: list | None = None,
    pool_missing: bool = False,
    butler_name: str = "atlas",
    sessions_raise: bool = False,
    actions_raise: bool = False,
    episodes_raise: bool = False,
):
    """Build a test app with a mocked pool for the given butler.

    Each source table can be configured independently. By default all
    three sources return no rows (empty list).
    """
    mock_pool = AsyncMock()

    session_rows = session_rows or []
    action_rows = action_rows or []
    episode_rows = episode_rows or []

    async def _fetch(sql, *args):
        if "FROM sessions" in sql:
            if sessions_raise:
                raise UndefinedTableError("table sessions does not exist")
            return session_rows
        elif "FROM pending_actions" in sql:
            if actions_raise:
                raise UndefinedTableError("table pending_actions does not exist")
            return action_rows
        elif "FROM episodes" in sql:
            if episodes_raise:
                raise UndefinedTableError("table episodes does not exist")
            return episode_rows
        return []

    mock_pool.fetch = AsyncMock(side_effect=_fetch)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_missing:
        mock_db.pool.side_effect = KeyError(f"No pool for butler: {butler_name}")
    else:
        mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestActivityFeedAllSources:
    """Verify the feed merges all three sources and sorts by ts desc."""

    async def test_merged_and_sorted_desc(self):
        # Session at t+0, action at t+2, episode at t+1 → sorted: action, episode, session
        session = _session_row(offset_minutes=0)
        action = _action_row(offset_minutes=2)
        episode = _episode_row(offset_minutes=1)

        app = _make_app(
            session_rows=[session],
            action_rows=[action],
            episode_rows=[episode],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        assert resp.status_code == 200
        data = resp.json()
        events = data["events"]
        assert len(events) == 3
        # Newest first
        assert events[0]["event_type"] == "approval_raised"
        assert events[1]["event_type"] == "memory_write"
        assert events[2]["event_type"] == "session_completed"

    async def test_timestamps_descending(self):
        session = _session_row(offset_minutes=10)
        action = _action_row(offset_minutes=5)
        episode = _episode_row(offset_minutes=1)

        app = _make_app(
            session_rows=[session],
            action_rows=[action],
            episode_rows=[episode],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        events = resp.json()["events"]
        timestamps = [e["ts"] for e in events]
        assert timestamps == sorted(timestamps, reverse=True), "Events must be sorted desc by ts"


class TestActivityFeedEventTypes:
    """Verify each event_type is correctly populated."""

    async def test_session_completed_event_type(self):
        session = _session_row(prompt="Daily digest", trigger_source="cron", success=True)
        app = _make_app(session_rows=[session])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        events = resp.json()["events"]
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "session_completed"
        assert ev["summary"] == "Daily digest"
        assert ev["metadata"]["trigger_source"] == "cron"
        assert ev["metadata"]["success"] is True

    async def test_approval_raised_event_type(self):
        action = _action_row(
            tool_name="send_telegram",
            agent_summary="Send morning update",
            status="pending",
        )
        app = _make_app(action_rows=[action])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        events = resp.json()["events"]
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "approval_raised"
        assert ev["summary"] == "Send morning update"
        assert ev["metadata"]["tool_name"] == "send_telegram"
        assert ev["metadata"]["status"] == "pending"

    async def test_memory_write_event_type(self):
        episode = _episode_row(content="User always wants concise summaries")
        app = _make_app(episode_rows=[episode])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        events = resp.json()["events"]
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "memory_write"
        assert ev["summary"] == "User always wants concise summaries"
        assert ev["metadata"]["importance"] == 5.0

    async def test_approval_raised_fallback_summary_when_agent_summary_none(self):
        action = _action_row(tool_name="notify", agent_summary=None)
        app = _make_app(action_rows=[action])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        events = resp.json()["events"]
        assert events[0]["summary"] == "Approval requested: notify"

    async def test_entity_id_is_populated(self):
        session = _session_row()
        app = _make_app(session_rows=[session])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        ev = resp.json()["events"][0]
        assert ev["entity_id"] is not None
        # Should be a valid UUID string
        uuid.UUID(ev["entity_id"])  # raises ValueError if invalid


class TestActivityFeedLimit:
    """Verify limit parameter behaviour."""

    async def test_default_limit_is_10(self):
        # Provide 15 sessions; default limit should cap at 10
        sessions = [_session_row(offset_minutes=i) for i in range(15)]
        app = _make_app(session_rows=sessions)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        events = resp.json()["events"]
        assert len(events) == 10

    async def test_custom_limit(self):
        sessions = [_session_row(offset_minutes=i) for i in range(20)]
        app = _make_app(session_rows=sessions)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed?limit=5")

        events = resp.json()["events"]
        assert len(events) == 5

    @pytest.mark.parametrize("limit", [0, 51])
    async def test_limit_out_of_bounds_is_422(self, limit):
        """limit must be within 1..50; below or above bounds returns 422."""
        app = _make_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/atlas/activity-feed?limit={limit}")

        assert resp.status_code == 422

    async def test_limit_1_is_valid(self):
        """limit=1 (lower bound) is accepted and returns at most one event."""
        sessions = [_session_row(offset_minutes=i) for i in range(5)]
        app = _make_app(session_rows=sessions)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed?limit=1")

        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 1

    async def test_limit_caps_merged_results(self):
        """With 3 events from different sources, limit=2 returns only 2."""
        session = _session_row(offset_minutes=0)
        action = _action_row(offset_minutes=1)
        episode = _episode_row(offset_minutes=2)

        app = _make_app(
            session_rows=[session],
            action_rows=[action],
            episode_rows=[episode],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed?limit=2")

        events = resp.json()["events"]
        assert len(events) == 2
        # Newest first: episode (t+2), action (t+1)
        assert events[0]["event_type"] == "memory_write"
        assert events[1]["event_type"] == "approval_raised"


class TestActivityFeedNoActivity:
    """Butler with no data returns events=[]."""

    async def test_empty_butler_returns_empty_events(self):
        app = _make_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        assert resp.status_code == 200
        assert resp.json()["events"] == []

    async def test_missing_tables_silently_skipped(self):
        """When pending_actions and episodes tables don't exist, only sessions come through."""
        session = _session_row(offset_minutes=0, prompt="Test session")
        app = _make_app(
            session_rows=[session],
            actions_raise=True,
            episodes_raise=True,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "session_completed"

    async def test_all_tables_missing_returns_empty(self):
        """When all tables raise, the response is still 200 with empty events."""
        app = _make_app(
            sessions_raise=True,
            actions_raise=True,
            episodes_raise=True,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        assert resp.status_code == 200
        assert resp.json()["events"] == []


class TestActivityFeed503:
    """Returns 503 when butler DB pool not registered."""

    async def test_pool_missing_returns_503(self):
        app = _make_app(pool_missing=True, butler_name="nonexistent")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/nonexistent/activity-feed")

        assert resp.status_code == 503
        assert "nonexistent" in resp.json()["detail"]


class TestActivityFeedSummaryTruncation:
    """Long summaries are truncated at 120 chars with ellipsis."""

    async def test_long_session_prompt_is_truncated(self):
        long_prompt = "x" * 200
        session = _session_row(prompt=long_prompt)
        app = _make_app(session_rows=[session])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        ev = resp.json()["events"][0]
        assert len(ev["summary"]) == 123  # 120 + len("...")
        assert ev["summary"].endswith("...")

    async def test_short_prompt_is_not_truncated(self):
        session = _session_row(prompt="Short prompt")
        app = _make_app(session_rows=[session])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/activity-feed")

        ev = resp.json()["events"][0]
        assert ev["summary"] == "Short prompt"


class TestNormalizeTz:
    """_normalize_tz converts naive datetimes to UTC-aware; leaves aware and None unchanged."""

    def test_normalize_tz_variants(self):
        # Naive datetime gains UTC tzinfo (sole guard of this conversion).
        naive = datetime(2026, 1, 15, 12, 0, 0)
        result = _normalize_tz(naive)
        assert result is not None
        assert result.tzinfo == UTC
        assert result.replace(tzinfo=None) == naive

        # Aware datetime is returned unchanged.
        aware = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        assert _normalize_tz(aware) is aware

        # None passes through.
        assert _normalize_tz(None) is None
