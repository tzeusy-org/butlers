"""Tests for per-device liveness in GET /api/ingestion/connectors/summaries.

Multi-device connector_types (e.g. OwnTracks, where several physical devices
post through one shared connector_type) only ever get ONE heartbeat row in
connector_registry — whichever device most recently resolved the connector's
shared endpoint identity. A sibling device can go silent for weeks with zero
dashboard signal (bu-e16to: OwnTracks devices 'tz' and 'el' dead since
2026-04-24, invisible behind the healthy 'th' connector-level heartbeat).

Behavior under test:
  - `devices` is null for a connector_type with only one distinct
    source_sender_identity (single-device connectors are unaffected/unchanged)
  - `devices` is populated for a connector_type with >1 distinct sender
    identity, sorted most-recent-first, each with sender_identity/last_seen_at/stale
  - `stale` is true only when last_seen_at is older than the 48h threshold
  - `device_liveness_available` is true on success, false when the per-device
    query itself raises (and every connector's `devices` falls back to null)
  - the per-device query is skipped entirely when the registry is empty
    (guarded by `if rows:`, same convention as the hourly timeseries fetch)

bu-e16to
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.ingestion_connectors import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(data: dict) -> MagicMock:
    """Build a mock asyncpg record."""
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    return row


def _registry_row(
    *,
    connector_type: str,
    endpoint_identity: str,
    state: str = "healthy",
    first_seen_at: dt.datetime | None = None,
    last_heartbeat_at: dt.datetime | None = None,
    archived_at: dt.datetime | None = None,
    operational_role: str = "runtime_instance",
    parent_endpoint_identity: str | None = None,
    checkpoint_cursor: str | None = None,
    checkpoint_updated_at: dt.datetime | None = None,
) -> MagicMock:
    if first_seen_at is None:
        first_seen_at = dt.datetime(2024, 1, 1, 0, 0, 0, tzinfo=dt.UTC)
    return _make_row(
        {
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
            "state": state,
            "error_message": None,
            "version": "1.0",
            "uptime_s": 3600,
            "last_heartbeat_at": last_heartbeat_at,
            "first_seen_at": first_seen_at,
            "counter_messages_ingested": 10,
            "counter_messages_failed": 0,
            "archived_at": archived_at,
            "operational_role": operational_role,
            "parent_endpoint_identity": parent_endpoint_identity,
            "checkpoint_cursor": checkpoint_cursor,
            "checkpoint_updated_at": checkpoint_updated_at,
        }
    )


def _device_row(
    *, connector_type: str, sender_identity: str, last_seen_at: dt.datetime
) -> MagicMock:
    return _make_row(
        {
            "connector_type": connector_type,
            "sender_identity": sender_identity,
            "last_seen_at": last_seen_at,
        }
    )


def _make_pool_with_fetch_sequence(fetch_calls: list[list]) -> AsyncMock:
    """Build a pool whose fetch() returns successive results from fetch_calls."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=fetch_calls)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    return pool


def _wire_db(app: FastAPI, pool: AsyncMock) -> None:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db


async def _get_summaries(app: FastAPI) -> dict:
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/connectors/summaries")

    assert resp.status_code == 200
    return resp.json()["data"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_devices_null_for_single_device_connector(app: FastAPI) -> None:
    """A connector_type with only one distinct sender_identity gets devices=None."""
    now = dt.datetime.now(dt.UTC)
    registry_rows = [_registry_row(connector_type="gmail", endpoint_identity="user@example.com")]
    device_rows = [
        _device_row(
            connector_type="gmail", sender_identity="gmail:user@example.com", last_seen_at=now
        )
    ]
    pool = _make_pool_with_fetch_sequence([registry_rows, [], device_rows])
    _wire_db(app, pool)

    data = await _get_summaries(app)

    assert data["device_liveness_available"] is True
    connector = data["connectors"][0]
    assert connector["devices"] is None


async def test_devices_populated_for_multi_device_connector_with_stale_flag(
    app: FastAPI,
) -> None:
    """A connector_type with 3 distinct sender identities gets a sorted devices list.

    Regression scenario mirrors bu-e16to: 'th' active recently, 'el' and 'tz'
    both stale (>48h), sorted most-recent-first.
    """
    now = dt.datetime.now(dt.UTC)
    registry_rows = [_registry_row(connector_type="owntracks", endpoint_identity="owntracks:th")]
    device_rows = [
        _device_row(
            connector_type="owntracks",
            sender_identity="owntracks:th",
            last_seen_at=now - dt.timedelta(hours=1),
        ),
        _device_row(
            connector_type="owntracks",
            sender_identity="owntracks:el",
            last_seen_at=now - dt.timedelta(days=70),
        ),
        _device_row(
            connector_type="owntracks",
            sender_identity="owntracks:tz",
            last_seen_at=now - dt.timedelta(days=70, hours=1),
        ),
    ]
    pool = _make_pool_with_fetch_sequence([registry_rows, [], device_rows])
    _wire_db(app, pool)

    data = await _get_summaries(app)

    assert data["device_liveness_available"] is True
    connector = data["connectors"][0]
    devices = connector["devices"]
    assert devices is not None
    assert [d["sender_identity"] for d in devices] == [
        "owntracks:th",
        "owntracks:el",
        "owntracks:tz",
    ]
    by_identity = {d["sender_identity"]: d for d in devices}
    assert by_identity["owntracks:th"]["stale"] is False
    assert by_identity["owntracks:el"]["stale"] is True
    assert by_identity["owntracks:tz"]["stale"] is True


async def test_devices_stale_threshold_boundary(app: FastAPI) -> None:
    """A device just under 48h is not stale; just over 48h is stale."""
    now = dt.datetime.now(dt.UTC)
    registry_rows = [_registry_row(connector_type="owntracks", endpoint_identity="owntracks:a")]
    device_rows = [
        _device_row(
            connector_type="owntracks",
            sender_identity="owntracks:a",
            last_seen_at=now - dt.timedelta(hours=47, minutes=59),
        ),
        _device_row(
            connector_type="owntracks",
            sender_identity="owntracks:b",
            last_seen_at=now - dt.timedelta(hours=48, minutes=1),
        ),
    ]
    pool = _make_pool_with_fetch_sequence([registry_rows, [], device_rows])
    _wire_db(app, pool)

    data = await _get_summaries(app)

    by_identity = {d["sender_identity"]: d for d in data["connectors"][0]["devices"]}
    assert by_identity["owntracks:a"]["stale"] is False
    assert by_identity["owntracks:b"]["stale"] is True


async def test_device_liveness_query_failure_falls_back_gracefully(app: FastAPI) -> None:
    """If the per-device query raises, devices=None everywhere and the flag flips false.

    The rest of the response (hourly_events, today) must be
    unaffected — a per-device liveness failure is not a whole-endpoint failure.
    """
    registry_rows = [_registry_row(connector_type="owntracks", endpoint_identity="owntracks:th")]

    pool = AsyncMock()
    # 1st call: registry. 2nd call: hourly (empty). 3rd call: device query raises.
    pool.fetch = AsyncMock(side_effect=[registry_rows, [], Exception("DB error")])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    _wire_db(app, pool)

    data = await _get_summaries(app)

    assert data["device_liveness_available"] is False
    connector = data["connectors"][0]
    assert connector["devices"] is None
    # Unrelated fields are unaffected by the device-liveness failure.
    assert connector["hourly_events"] == [0] * 24


async def test_devices_badge_suppressed_once_registry_has_a_row_per_known_device(
    app: FastAPI,
) -> None:
    """Fully migrated connector_type: registry_row_counts >= known device count.

    Once every device the fallback knows about (from ingestion_events) also has
    its own connector_registry row (bu-86zll: OwnTracks now registers one row
    per resolved device), each row's own state/last_heartbeat_at is already
    device-accurate -- the ingestion_events-derived `devices` badge is no longer
    needed and must be suppressed so it doesn't double up or disagree with the
    per-row liveness.
    """
    now = dt.datetime.now(dt.UTC)
    registry_rows = [
        _registry_row(connector_type="owntracks", endpoint_identity="owntracks:a"),
        _registry_row(connector_type="owntracks", endpoint_identity="owntracks:b"),
    ]
    device_rows = [
        _device_row(connector_type="owntracks", sender_identity="owntracks:a", last_seen_at=now),
        _device_row(connector_type="owntracks", sender_identity="owntracks:b", last_seen_at=now),
    ]
    pool = _make_pool_with_fetch_sequence([registry_rows, [], device_rows])
    _wire_db(app, pool)

    data = await _get_summaries(app)

    for connector in data["connectors"]:
        assert connector["devices"] is None


async def test_devices_badge_stays_visible_during_partial_registry_migration(
    app: FastAPI,
) -> None:
    """Partial migration: only some known devices have registered their own row.

    A connector_type where one device ('a') has already started registering its
    own connector_registry row post-bu-86zll but a sibling ('b', still known only
    via ingestion_events -- e.g. dead/not yet posted since the fix deployed) has
    NOT yet registered one must keep the `devices` badge. Gating on a flat
    ``registry_row_counts > 1`` would suppress the badge the instant a *second*
    row of any kind appears, hiding 'b' behind neither its own row nor the
    fallback badge -- exactly the bu-e16to invisibility bug this badge exists to
    prevent. The badge must only disappear once the registry has caught up to
    *every* device the fallback already knows about.
    """
    now = dt.datetime.now(dt.UTC)
    # Registry already has TWO rows ('a' and 'b' have freshly registered their
    # own row post-bu-86zll) -- a flat ">1 rows" gate would already suppress
    # the badge here. But 'c' has zero rows (dead/not yet posted since the fix
    # deployed), matching the described bu-e16to failure mode exactly.
    registry_rows = [
        _registry_row(connector_type="owntracks", endpoint_identity="owntracks:a"),
        _registry_row(connector_type="owntracks", endpoint_identity="owntracks:b"),
    ]
    # ingestion_events has seen THREE distinct devices historically.
    device_rows = [
        _device_row(connector_type="owntracks", sender_identity="owntracks:a", last_seen_at=now),
        _device_row(connector_type="owntracks", sender_identity="owntracks:b", last_seen_at=now),
        _device_row(
            connector_type="owntracks",
            sender_identity="owntracks:c",
            last_seen_at=now - dt.timedelta(days=70),
        ),
    ]
    pool = _make_pool_with_fetch_sequence([registry_rows, [], device_rows])
    _wire_db(app, pool)

    data = await _get_summaries(app)

    # Both registered rows ('a' and 'b') must still carry the badge -- with 'c'
    # still invisible in the registry itself, the badge is the only place its
    # staleness is surfaced.
    assert len(data["connectors"]) == 2
    for connector in data["connectors"]:
        devices = connector["devices"]
        assert devices is not None
        assert {d["sender_identity"] for d in devices} == {
            "owntracks:a",
            "owntracks:b",
            "owntracks:c",
        }


async def test_device_query_skipped_when_registry_empty(app: FastAPI) -> None:
    """Empty registry means zero rows -- the device-liveness fetch is skipped."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    _wire_db(app, pool)

    data = await _get_summaries(app)

    assert data["connectors"] == []
    assert data["device_liveness_available"] is True
    # Only the registry fetch happens; hourly + device queries are both skipped.
    assert pool.fetch.call_count == 1
