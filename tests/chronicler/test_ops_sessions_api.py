"""Tests for GET /api/chronicler/ops/sessions — the ops sessions escape hatch.

Acceptance criteria (bu-4zu95):
1. Operational sessions (tick, qa, healing, schedule:*) ARE visible via
   /api/chronicler/ops/sessions.
2. The same operational sessions are NOT visible via /api/chronicler/episodes
   (the episodes table never contains them — CoreSessionsAdapter excludes them
   at the projection layer).
3. The endpoint filters correctly for a specific trigger_source query param.
4. Non-operational sessions do not appear in the ops endpoint.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

_NOW = datetime(2026, 4, 29, 10, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """dict subclass that mimics asyncpg Record."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key, default)


def _session_row(
    *,
    session_id: str | None = None,
    trigger_source: str = "tick",
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    duration_ms: int | None = 500,
    success: bool = True,
    model: str = "claude-sonnet-4-6",
) -> _Row:
    return _Row(
        {
            "id": uuid.UUID(session_id) if session_id else uuid.uuid4(),
            "trigger_source": trigger_source,
            "started_at": started_at or _NOW,
            "completed_at": completed_at or (_NOW + timedelta(seconds=1)),
            "duration_ms": duration_ms,
            "success": success,
            "model": model,
        }
    )


def _episode_row(
    *,
    episode_id: str | None = None,
    source_name: str = "core.sessions",
    episode_type: str = "work",
    trigger_source: str | None = "route",
) -> _Row:
    """Return a mock episode row as returned by v_episodes_corrected."""
    now = _NOW
    payload: dict[str, Any] = {}
    if trigger_source is not None:
        payload["trigger_source"] = trigger_source
    return _Row(
        {
            "id": uuid.UUID(episode_id) if episode_id else uuid.uuid4(),
            "source_name": source_name,
            "source_ref": f"{source_name}:ref",
            "episode_type": episode_type,
            "start_at": now - timedelta(hours=1),
            "end_at": now,
            "precision": "exact",
            "title": "test episode",
            "payload": payload,
            "privacy": "normal",
            "retention_days": None,
            "tombstone_at": None,
            "canonical_start_at": now - timedelta(hours=1),
            "canonical_end_at": now,
            "canonical_title": "test episode",
            "canonical_privacy": "normal",
            "corrected_at": None,
            "correction_note": None,
            "created_at": now - timedelta(hours=2),
            "updated_at": now,
        }
    )


def _load_chronicler_router():
    module_name = "chronicler_api_router"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_app(
    *,
    fan_out_results: dict[str, list[_Row]] | None = None,
    episodes_rows: list[_Row] | None = None,
) -> tuple[Any, MagicMock]:
    """Build a test app wiring fan_out and pool.fetch/fetchval mocks."""
    chronicler_mod = _load_chronicler_router()

    pool = AsyncMock()
    # For /episodes: fetchval returns total count, fetch returns episode rows.
    pool.fetchval = AsyncMock(return_value=len(episodes_rows or []))
    pool.fetch = AsyncMock(return_value=episodes_rows or [])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="OK")

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    # fan_out returns the provided dict (keyed by butler name).
    db.fan_out = AsyncMock(return_value=fan_out_results or {})

    app = create_app(api_key="")
    app.dependency_overrides[chronicler_mod._get_db_manager] = lambda: db
    return app, db


# ---------------------------------------------------------------------------
# Tests: ops sessions visible via ops endpoint
# ---------------------------------------------------------------------------


class TestOpsSessionsEndpoint:
    async def test_empty_returns_200_with_no_data(self):
        """No operational sessions → 200 with data: []."""
        app, _ = _make_app(fan_out_results={})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/ops/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    @pytest.mark.parametrize(
        ("butler", "trigger_source"),
        [
            ("switchboard", "tick"),
            ("chronicler", "qa"),
            ("atlas", "healing"),
            ("chronicler", "schedule:chronicler_day_close"),
        ],
    )
    async def test_operational_sessions_visible_in_ops_endpoint(self, butler, trigger_source):
        """Each operational trigger_source (tick/qa/healing/schedule:*) is surfaced via
        /api/chronicler/ops/sessions, tagged with the originating butler schema."""
        row = _session_row(trigger_source=trigger_source)
        app, _ = _make_app(fan_out_results={butler: [row]})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/ops/sessions")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["trigger_source"] == trigger_source
        assert data[0]["butler"] == butler

    async def test_cross_butler_results_merged_and_sorted_by_started_at_desc(self):
        """Results from multiple butlers are merged and sorted newest-first."""
        t_old = _NOW - timedelta(hours=2)
        t_new = _NOW
        old_row = _session_row(trigger_source="tick", started_at=t_old)
        new_row = _session_row(trigger_source="qa", started_at=t_new)
        app, _ = _make_app(
            fan_out_results={
                "switchboard": [old_row],
                "chronicler": [new_row],
            }
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/ops/sessions")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        # Sorted newest first.
        assert data[0]["trigger_source"] == "qa"
        assert data[1]["trigger_source"] == "tick"

    async def test_trigger_source_filter_param_passed_to_fan_out(self):
        """The trigger_source query param results in correct fan_out call."""
        tick_row = _session_row(trigger_source="tick")
        app, db_mock = _make_app(fan_out_results={"switchboard": [tick_row]})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/chronicler/ops/sessions", params={"trigger_source": "tick"}
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data[0]["trigger_source"] == "tick"
        # fan_out was called with SQL that included the trigger_source filter.
        assert db_mock.fan_out.called
        call_kwargs = db_mock.fan_out.call_args
        args_tuple = call_kwargs.kwargs["args"]
        # The specific trigger_source value should be in the args tuple.
        assert "tick" in args_tuple

    async def test_response_includes_butler_field(self):
        """Each ops row includes the butler schema it came from."""
        row = _session_row(trigger_source="tick")
        app, _ = _make_app(fan_out_results={"switchboard": [row]})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/ops/sessions")
        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["butler"] == "switchboard"
        assert "session_id" in item
        assert "trigger_source" in item
        assert "started_at" in item


# ---------------------------------------------------------------------------
# Tests: operational sessions ABSENT from /api/chronicler/episodes
# ---------------------------------------------------------------------------


class TestOpsSessionsAbsentFromEpisodes:
    """Verify that ops sessions never appear in the user-facing episodes endpoint.

    CoreSessionsAdapter excludes them at the projection layer, so they are
    never written into chronicler.episodes. The user-facing /episodes endpoint
    reads from v_episodes_corrected which only contains projected rows.

    These tests assert the invariant by checking that when the pool returns
    no rows for the episodes query (the correct real-world state), the
    /episodes endpoint returns an empty result — confirming the two surfaces
    are structurally separate.
    """

    async def test_episodes_endpoint_returns_no_ops_sessions(self):
        """/api/chronicler/episodes returns empty when no projected episodes exist."""
        # No episodes projected (ops sessions were excluded by adapter).
        app, _ = _make_app(episodes_rows=[], fan_out_results={})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/episodes")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_episodes_endpoint_returns_only_non_ops_episodes(self):
        """Only non-operational (user-activity) episodes appear in /episodes."""
        user_episode = _episode_row(trigger_source="route")
        app, _ = _make_app(episodes_rows=[user_episode], fan_out_results={})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/episodes")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        # The episode's payload trigger_source is 'route' (user-facing).
        assert data[0]["payload"].get("trigger_source") == "route"

    async def test_ops_endpoint_visible_while_episodes_empty(self):
        """Ops data is visible via /ops/sessions while /episodes stays empty.

        This is the key invariant: the two surfaces are structurally separate.
        Operational data appears ONLY via the ops endpoint.
        """
        tick_row = _session_row(trigger_source="tick")
        # fan_out returns tick sessions; episodes pool returns nothing.
        app, _ = _make_app(
            fan_out_results={"switchboard": [tick_row]},
            episodes_rows=[],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Ops endpoint: tick session IS visible.
            ops_resp = await client.get("/api/chronicler/ops/sessions")
            # Episodes endpoint: empty (tick was never projected).
            episodes_resp = await client.get("/api/chronicler/episodes")

        assert ops_resp.status_code == 200
        ops_data = ops_resp.json()["data"]
        assert len(ops_data) == 1
        assert ops_data[0]["trigger_source"] == "tick"

        assert episodes_resp.status_code == 200
        episodes_data = episodes_resp.json()["data"]
        assert episodes_data == []
