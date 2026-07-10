"""Tests for the archive review-queue candidate rule (bu-u19yv).

A flag-only ``archive_candidate`` is computed on
``GET /api/ingestion/connectors/summaries`` for an ACTIVE (non-archived)
identity that BOTH:

- last heartbeated strictly more than 30 days ago, AND
- has a newer, currently-online sibling of the same ``connector_type``.

It is a SUGGESTION only — it never affects the fleet-health rollups or
alerting, and can never mask a genuinely-failing live connector. These tests
cover the boundary conditions of the pure rule plus the endpoint surfacing.

Follow-up from bu-33dm2 / PR #3026 (tasks.md 5.1).
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.ingestion_connectors import (
    _ARCHIVE_CANDIDATE_MIN_OFFLINE,
    _get_db_manager,
    _is_archive_candidate,
    _online_identities_by_type,
)

pytestmark = pytest.mark.unit

_NOW = dt.datetime(2026, 7, 10, 12, 0, 0, tzinfo=dt.UTC)
_OLD_HB = _NOW - dt.timedelta(days=45)  # well past the 30d threshold, relative to _NOW


def _row(
    *,
    connector_type: str,
    endpoint_identity: str,
    last_heartbeat_at: dt.datetime | None,
    archived_at: dt.datetime | None = None,
) -> dict:
    return {
        "connector_type": connector_type,
        "endpoint_identity": endpoint_identity,
        "last_heartbeat_at": last_heartbeat_at,
        "archived_at": archived_at,
    }


# ---------------------------------------------------------------------------
# _online_identities_by_type
# ---------------------------------------------------------------------------


def test_online_identities_groups_only_online_non_archived() -> None:
    # _online_identities_by_type calls derive_liveness, which compares against
    # the real wall clock — so "online" heartbeats must be relative to now().
    real_now = dt.datetime.now(dt.UTC)
    online_hb = real_now - dt.timedelta(minutes=1)
    old_hb = real_now - dt.timedelta(days=45)
    rows = [
        _row(connector_type="gh", endpoint_identity="live", last_heartbeat_at=online_hb),
        _row(connector_type="gh", endpoint_identity="dead", last_heartbeat_at=old_hb),
        # online but archived -> must NOT count as a live sibling
        _row(
            connector_type="gh",
            endpoint_identity="archived-online",
            last_heartbeat_at=online_hb,
            archived_at=real_now,
        ),
        _row(connector_type="owntracks", endpoint_identity="phone", last_heartbeat_at=online_hb),
    ]
    result = _online_identities_by_type(rows)
    assert result == {"gh": {"live"}, "owntracks": {"phone"}}


# ---------------------------------------------------------------------------
# _is_archive_candidate — boundaries
# ---------------------------------------------------------------------------


def _candidate(
    *,
    endpoint_identity: str = "dead",
    last_heartbeat_at: dt.datetime | None = _OLD_HB,
    archived_at: dt.datetime | None = None,
    online: dict[str, set[str]] | None = None,
) -> bool:
    return _is_archive_candidate(
        connector_type="gh",
        endpoint_identity=endpoint_identity,
        archived_at=archived_at,
        last_heartbeat_at=last_heartbeat_at,
        online_identities_by_type=online if online is not None else {"gh": {"live"}},
        now=_NOW,
    )


def test_candidate_true_when_old_and_online_sibling() -> None:
    assert _candidate() is True


def test_not_candidate_when_already_archived() -> None:
    assert _candidate(archived_at=_NOW) is False


def test_not_candidate_when_no_newer_sibling() -> None:
    # No online identity of this connector_type at all.
    assert _candidate(online={}) is False


def test_not_candidate_when_only_sibling_is_self() -> None:
    # The only "online" identity recorded is this same identity — no *other*
    # sibling, so not a candidate (defensive; an offline row is never online).
    assert _candidate(endpoint_identity="dead", online={"gh": {"dead"}}) is False


def test_not_candidate_when_sibling_offline() -> None:
    # A sibling exists but is itself offline -> not in online_identities_by_type.
    assert _candidate(online={"gh": set()}) is False


def test_not_candidate_when_never_heartbeated() -> None:
    # No last_heartbeat_at -> no age to compare -> not a candidate.
    assert _candidate(last_heartbeat_at=None) is False


def test_boundary_exactly_30d_is_not_candidate() -> None:
    # Strict >30d: an identity offline for EXACTLY 30 days is not yet a candidate.
    exactly_30d = _NOW - _ARCHIVE_CANDIDATE_MIN_OFFLINE
    assert _candidate(last_heartbeat_at=exactly_30d) is False


def test_boundary_just_over_30d_is_candidate() -> None:
    just_over = _NOW - _ARCHIVE_CANDIDATE_MIN_OFFLINE - dt.timedelta(seconds=1)
    assert _candidate(last_heartbeat_at=just_over) is True


def test_boundary_just_under_30d_is_not_candidate() -> None:
    just_under = _NOW - _ARCHIVE_CANDIDATE_MIN_OFFLINE + dt.timedelta(seconds=1)
    assert _candidate(last_heartbeat_at=just_under) is False


# ---------------------------------------------------------------------------
# Endpoint surfacing
# ---------------------------------------------------------------------------


def _make_registry_row(
    *,
    connector_type: str,
    endpoint_identity: str,
    last_heartbeat_at: dt.datetime | None,
    archived_at: dt.datetime | None = None,
) -> MagicMock:
    data = {
        "connector_type": connector_type,
        "endpoint_identity": endpoint_identity,
        "state": "healthy",
        "error_message": None,
        "version": "1.0",
        "uptime_s": 3600,
        "last_heartbeat_at": last_heartbeat_at,
        "first_seen_at": dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
        "counter_messages_ingested": 10,
        "counter_messages_failed": 0,
        "archived_at": archived_at,
    }
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    return row


def _wire(app: FastAPI, registry_rows: list) -> None:
    pool = AsyncMock()
    # registry fetch, hourly fetch (empty), device fetch (empty)
    pool.fetch = AsyncMock(side_effect=[registry_rows, [], []])
    pool.fetchrow = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db


async def test_summaries_flags_candidate_and_not_the_live_sibling(app: FastAPI) -> None:
    # Live online sibling + a dead >30d identity of the same connector_type,
    # plus a lone offline identity of a DIFFERENT type (no online sibling).
    now = dt.datetime.now(dt.UTC)
    registry_rows = [
        _make_registry_row(
            connector_type="google_health",
            endpoint_identity="live@x",
            last_heartbeat_at=now - dt.timedelta(minutes=1),
        ),
        _make_registry_row(
            connector_type="google_health",
            endpoint_identity="dead-placeholder",
            last_heartbeat_at=now - dt.timedelta(days=45),
        ),
        _make_registry_row(
            connector_type="owntracks",
            endpoint_identity="lonely-offline",
            last_heartbeat_at=now - dt.timedelta(days=60),
        ),
    ]
    _wire(app, registry_rows)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/api/ingestion/connectors/summaries")
    assert resp.status_code == 200
    connectors = {c["endpoint_identity"]: c for c in resp.json()["data"]["connectors"]}

    assert connectors["dead-placeholder"]["archive_candidate"] is True
    # The live sibling itself is never a candidate.
    assert connectors["live@x"]["archive_candidate"] is False
    # Offline but no online sibling of its type -> not a candidate (never masked
    # as archivable just for being quiet).
    assert connectors["lonely-offline"]["archive_candidate"] is False


async def test_archived_identity_is_not_a_candidate(app: FastAPI) -> None:
    now = dt.datetime.now(dt.UTC)
    registry_rows = [
        _make_registry_row(
            connector_type="google_health",
            endpoint_identity="live@x",
            last_heartbeat_at=now - dt.timedelta(minutes=1),
        ),
        _make_registry_row(
            connector_type="google_health",
            endpoint_identity="already-archived",
            last_heartbeat_at=now - dt.timedelta(days=45),
            archived_at=now,
        ),
    ]
    _wire(app, registry_rows)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/api/ingestion/connectors/summaries")
    connectors = {c["endpoint_identity"]: c for c in resp.json()["data"]["connectors"]}
    assert connectors["already-archived"]["archive_candidate"] is False
