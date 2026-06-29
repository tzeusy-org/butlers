"""Tests for the derived ``category`` field on Chronicler episode responses.

The frontend Gantt lane taxonomy keys on stable Activity-lane strings
(``work``, ``play``, ``rest``, ...) — not on raw ``source_name`` values. The
backend must surface the mapping (computed by ``lane_for_category`` over
``category_for``) on every episode payload returned from
``GET /api/chronicler/episodes`` and ``GET /api/chronicler/episodes/{id}``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """asyncpg.Record-like dict subclass."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _episode_row(
    *,
    source_name: str,
    episode_type: str,
    episode_id: str = "00000000-0000-0000-0000-000000000001",
) -> _Row:
    now = datetime.now(UTC)
    return _Row(
        {
            "id": UUID(episode_id),
            "source_name": source_name,
            "source_ref": f"{source_name}:ref",
            "episode_type": episode_type,
            "start_at": now - timedelta(hours=1),
            "end_at": now,
            "precision": "exact",
            "title": "Test episode",
            "payload": {},
            "privacy": "normal",
            "retention_days": None,
            "tombstone_at": None,
            "canonical_start_at": now - timedelta(hours=1),
            "canonical_end_at": now,
            "canonical_title": "Test episode",
            "canonical_privacy": "normal",
            "corrected_at": None,
            "correction_note": None,
            "created_at": now - timedelta(hours=2),
            "updated_at": now,
        }
    )


def _build_app(rows: list[_Row]):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.fetchrow = AsyncMock(return_value=rows[0] if rows else None)
    pool.fetchval = AsyncMock(return_value=len(rows))
    pool.execute = AsyncMock(return_value="OK")

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    app = create_app(api_key="")

    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: db
            break

    return app, pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_name", "episode_type", "expected_category"),
    [
        # The category field carries the life-balance Activity lane (IEA, §4).
        # core.sessions: no trigger_source → tasks → Work lane.
        ("core.sessions", "work", "work"),
        # Calendar is intent: no lane → "other" (and dropped from counting).
        ("google_calendar.completed", "scheduled_block", "other"),
        ("spotify.session_summary", "listening_episode", "play"),
        ("steam.play_history", "play_episode", "play"),
        ("totally.unknown_source", "mystery_type", "other"),
    ],
)
async def test_list_episodes_includes_category_field(
    source_name: str,
    episode_type: str,
    expected_category: str,
) -> None:
    """The episode list response includes a ``category`` (the Activity lane)
    derived from ``(source_name, episode_type)``."""
    rows = [_episode_row(source_name=source_name, episode_type=episode_type)]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/chronicler/episodes")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"], body
    episode = body["data"][0]
    assert episode["source_name"] == source_name
    assert episode["episode_type"] == episode_type
    assert episode["category"] == expected_category


async def test_get_single_episode_includes_category_field() -> None:
    """GET /api/chronicler/episodes/{id} also returns the derived category."""
    row = _episode_row(
        source_name="google_calendar.completed",
        episode_type="scheduled_block",
    )
    app, _ = _build_app([row])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/chronicler/episodes/00000000-0000-0000-0000-000000000001")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Calendar is the intent layer: no Activity lane → "other".
    assert body["category"] == "other"
