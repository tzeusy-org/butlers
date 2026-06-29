"""Integration test: Spotify projection → GET /api/chronicler/episodes.

Verifies that when ``connectors.spotify_listening_sessions`` rows are projected
by the SpotifySessionAdapter and stored, the Chronicler episodes API returns
them with ``source_name='spotify.session_summary'``.

This is a mock-backed test (no live DB required).  The integration goal is to
exercise the full path from adapter output → API response, not to test live SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.chronicler.adapters.spotify import SOURCE_NAME, SpotifySessionAdapter
from butlers.chronicler.models import Episode, Precision

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC)
_EPISODE_ID = UUID("00000000-0000-0000-0000-000000000042")


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

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key, default)


def _session_row(
    *,
    started_at: datetime = _NOW,
    ended_at: datetime | None = None,
    idempotency_key: str = "spotify:ep:session:1711447200000",
    context_name: str | None = "Deep Focus",
) -> dict:
    return {
        "id": 1,
        "idempotency_key": idempotency_key,
        "endpoint_identity": "spotify_user_client:spotify:user123",
        "spotify_user_id": "user123",
        "started_at": started_at,
        "ended_at": ended_at or (started_at + timedelta(minutes=30)),
        "duration_seconds": 1800,
        "track_count": 5,
        "track_names": ["Song A", "Song B"],
        "context_uri": "spotify:playlist:abc",
        "context_name": context_name,
        "recorded_at": started_at,
    }


def _episode_db_row(
    *,
    source_name: str = SOURCE_NAME,
    episode_type: str = "listening_episode",
    episode_id: UUID = _EPISODE_ID,
) -> _Row:
    """Build a row that mimics what ``v_episodes_corrected`` returns."""
    return _Row(
        {
            "id": episode_id,
            "source_name": source_name,
            "source_ref": "connectors.spotify_listening_sessions:spotify:ep:session:1711447200000",
            "episode_type": episode_type,
            "start_at": _NOW,
            "end_at": _NOW + timedelta(minutes=30),
            "precision": "exact",
            "title": "Listened to Deep Focus",
            "payload": {
                "idempotency_key": "spotify:ep:session:1711447200000",
                "endpoint_identity": "spotify_user_client:spotify:user123",
                "spotify_user_id": "user123",
                "track_count": 5,
                "duration_seconds": 1800,
                "context_uri": "spotify:playlist:abc",
                "context_name": "Deep Focus",
            },
            "privacy": "sensitive",
            "retention_days": None,
            "tombstone_at": None,
            "canonical_start_at": _NOW,
            "canonical_end_at": _NOW + timedelta(minutes=30),
            "canonical_title": "Listened to Deep Focus",
            "canonical_privacy": "sensitive",
            "corrected_at": None,
            "correction_note": None,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


class _NullCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Adapter projection test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_projects_spotify_session_to_listening_episode() -> None:
    """SpotifySessionAdapter emits one listening_episode per session row.

    Seeds a mock source pool with one ``connectors.spotify_listening_sessions``
    row and asserts the adapter produces a listening_episode with the correct
    source_name, episode_type, precision, and source_ref.
    """
    session = _session_row()
    mock_row = MagicMock(**session, **{"__getitem__": lambda s, k: session[k]})

    source_conn = AsyncMock()
    source_conn.fetchval = AsyncMock(return_value=True)  # table exists
    source_conn.fetch = AsyncMock(return_value=[mock_row])
    source_pool = AsyncMock()
    source_pool.acquire = MagicMock(return_value=_AsyncCtx(source_conn))

    chronicler_conn = AsyncMock()
    chronicler_conn.transaction = MagicMock(return_value=_NullCtx())
    chronicler_conn.fetchrow = AsyncMock(return_value=None)
    chronicler_pool = AsyncMock()
    chronicler_pool.acquire = MagicMock(return_value=_AsyncCtx(chronicler_conn))

    upserted: list[Episode] = []

    async def _capture_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    adapter = SpotifySessionAdapter()
    with patch("butlers.chronicler.adapters.spotify.upsert_episode", side_effect=_capture_upsert):
        result = await adapter.project(
            source_pool,
            chronicler_pool=chronicler_pool,
            since=None,
        )

    assert result.rows_projected == 1
    assert result.episodes_closed == 1
    assert len(upserted) == 1

    ep = upserted[0]
    assert ep.source_name == SOURCE_NAME
    assert ep.episode_type == "listening_episode"
    assert ep.precision == Precision.EXACT
    assert ep.source_ref == "connectors.spotify_listening_sessions:spotify:ep:session:1711447200000"
    assert ep.payload["context_name"] == "Deep Focus"


# ---------------------------------------------------------------------------
# API round-trip test
# ---------------------------------------------------------------------------


def _build_app_with_episodes(rows: list[_Row]):
    """Build a test app whose chronicler pool returns the given episode rows."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.fetchval = AsyncMock(return_value=len(rows))

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    app = create_app(api_key="")

    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: db
            break

    return app, pool


@pytest.mark.asyncio
async def test_api_returns_spotify_episodes_by_source_name() -> None:
    """GET /api/chronicler/episodes?source_name=spotify.session_summary returns
    projected Spotify listening episodes.

    Simulates the result of having run the SpotifySessionAdapter by seeding
    the mock pool with a v_episodes_corrected row whose source_name matches
    ``spotify.session_summary`` and asserts the API surfaces it correctly.
    """
    rows = [_episode_db_row()]
    app, pool = _build_app_with_episodes(rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/episodes",
            params={"source_name": SOURCE_NAME},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"], f"Expected episodes in response, got: {body}"

    ep = body["data"][0]
    assert ep["source_name"] == SOURCE_NAME
    assert ep["episode_type"] == "listening_episode"
    # Music folds into the Play Activity lane (IEA reframe).
    assert ep["category"] == "play"


@pytest.mark.asyncio
async def test_api_returns_multiple_spotify_sessions_projected() -> None:
    """When multiple Spotify session rows are projected, all appear via the API."""
    rows = [
        _episode_db_row(episode_id=UUID("00000000-0000-0000-0000-000000000001")),
        _episode_db_row(episode_id=UUID("00000000-0000-0000-0000-000000000002")),
        _episode_db_row(episode_id=UUID("00000000-0000-0000-0000-000000000003")),
    ]
    app, pool = _build_app_with_episodes(rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/episodes",
            params={"source_name": SOURCE_NAME},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["data"]) == 3
    assert all(ep["source_name"] == SOURCE_NAME for ep in body["data"])
    assert all(ep["category"] == "play" for ep in body["data"])
