"""Runtime-instance authority for the connector fleet surfaces (bu-6jv4m.11).

``connector_registry`` mixes two kinds of record: executable connector
processes, registered by the ``connector.heartbeat`` tool, and persisted
checkpoint cursors, written by ``cursor_store.save_cursor``. Google Health keeps
one cursor per account *and* per resource, so a single online account produced
several extra rows that never heartbeat.

Nothing in the schema said which was which, so the roster listed all of them:
the live ingestion console showed ``activity``, ``sleep``, ``hrv`` and friends as
separate OFFLINE listening connectors beside the one genuinely-online
``google_health:user:<owner>`` identity, each with a null heartbeat, null uptime
and a checkpoint cursor — and each pulling on fleet attention.

Behavior under test:
  - the roster lists runtime instances only; checkpoint rows are nested under
    their parent as labelled, status-free records
  - two accounts of the same connector_type never collect each other's cursors
  - a row whose ``operational_role`` is ``unknown`` reports the named
    ``unclassified`` liveness — never active, never healthy, never inferred
    ``offline`` — and is counted apart from the fleet rollups
  - fleet liveness (``cross-summary`` and the switchboard
    ``/connectors/summary``) counts executable runtime instances only
  - a checkpoint whose parent cannot be resolved stays visible instead of
    silently disappearing
  - genuine source failures still degrade explicitly rather than fabricating a
    roster
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

_OWNER = "owner@example.test"
_SECOND_OWNER = "second@example.test"
_ACCOUNT_UUID = "00000000-0000-4000-8000-000000000001"
_SECOND_ACCOUNT_UUID = "00000000-0000-4000-8000-000000000002"


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------


def _make_row(data: dict) -> MagicMock:
    """Build a mock asyncpg record."""
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    return row


def _runtime_row(
    *,
    connector_type: str,
    endpoint_identity: str,
    last_heartbeat_at: dt.datetime | None = None,
    state: str = "healthy",
    archived_at: dt.datetime | None = None,
) -> MagicMock:
    """A registry row for an executing connector process."""
    return _make_row(
        {
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
            "state": state,
            "error_message": None,
            "version": "1.0",
            "uptime_s": 3600,
            "last_heartbeat_at": last_heartbeat_at,
            "first_seen_at": dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
            "counter_messages_ingested": 10,
            "counter_messages_failed": 0,
            # Aliases: the cross-summary query selects the counters under
            # these shorter names.
            "messages_ingested": 10,
            "messages_failed": 0,
            "archived_at": archived_at,
            "operational_role": "runtime_instance",
            "parent_endpoint_identity": None,
            "checkpoint_cursor": None,
            "checkpoint_updated_at": None,
        }
    )


def _checkpoint_row(
    *,
    connector_type: str,
    endpoint_identity: str,
    parent_endpoint_identity: str | None,
    checkpoint_cursor: str = "cursor-token",
    checkpoint_updated_at: dt.datetime | None = None,
) -> MagicMock:
    """A registry row that is a persisted cursor, not a process.

    Mirrors what ``cursor_store.save_cursor`` writes for a cursor that declares
    a parent: no instance, no heartbeat, no uptime, ``state`` left at the column
    default.
    """
    return _make_row(
        {
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
            "state": "unknown",
            "error_message": None,
            "version": None,
            "uptime_s": None,
            "last_heartbeat_at": None,
            "first_seen_at": dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
            "counter_messages_ingested": 0,
            "counter_messages_failed": 0,
            "messages_ingested": 0,
            "messages_failed": 0,
            "archived_at": None,
            "operational_role": "checkpoint",
            "parent_endpoint_identity": parent_endpoint_identity,
            "checkpoint_cursor": checkpoint_cursor,
            "checkpoint_updated_at": checkpoint_updated_at,
        }
    )


def _unknown_row(*, connector_type: str, endpoint_identity: str) -> MagicMock:
    """A registry row no producer has claimed — e.g. settings written before any run."""
    return _make_row(
        {
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
            "state": "unknown",
            "error_message": None,
            "version": None,
            "uptime_s": None,
            "last_heartbeat_at": None,
            "first_seen_at": dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
            "counter_messages_ingested": 0,
            "counter_messages_failed": 0,
            "messages_ingested": 0,
            "messages_failed": 0,
            "archived_at": None,
            "operational_role": "unknown",
            "parent_endpoint_identity": None,
            "checkpoint_cursor": None,
            "checkpoint_updated_at": None,
        }
    )


def _google_health_account(
    owner: str, account_uuid: str, *, last_heartbeat_at: dt.datetime
) -> list[MagicMock]:
    """The live Google Health shape: one online account plus its resource cursors."""
    parent = f"google_health:user:{owner}"
    rows = [
        _runtime_row(
            connector_type="google_health",
            endpoint_identity=parent,
            last_heartbeat_at=last_heartbeat_at,
        )
    ]
    rows += [
        _checkpoint_row(
            connector_type="google_health",
            endpoint_identity=f"{parent}:{account_uuid}:{resource}",
            parent_endpoint_identity=parent,
            checkpoint_cursor=f"{resource}-cursor",
        )
        for resource in ("activity", "hrv", "sleep_sessions")
    ]
    return rows


# ---------------------------------------------------------------------------
# App wiring
# ---------------------------------------------------------------------------


def _wire(app: FastAPI, fetch_calls: list) -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=fetch_calls)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return pool


async def _get(app: FastAPI, path: str) -> dict:
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(path)

    assert resp.status_code == 200
    return resp.json()["data"]


async def _summaries(app: FastAPI, registry_rows: list) -> dict:
    """Fetch sequence for /summaries: registry, hourly timeseries, device liveness."""
    _wire(app, [registry_rows, [], []])
    return await _get(app, "/api/ingestion/connectors/summaries")


async def _cross_summary(app: FastAPI, registry_rows: list) -> dict:
    _wire(app, [registry_rows])
    return await _get(app, "/api/ingestion/connectors/cross-summary")


# ---------------------------------------------------------------------------
# Google Health parent-plus-subidentity shape
# ---------------------------------------------------------------------------


async def test_google_health_cursors_do_not_become_offline_connectors(
    app: FastAPI,
) -> None:
    """The regression: per-resource cursors are not separate offline connectors."""
    now = dt.datetime.now(dt.UTC)
    rows = _google_health_account(_OWNER, _ACCOUNT_UUID, last_heartbeat_at=now)

    data = await _summaries(app, rows)

    connectors = data["connectors"]
    assert [c["endpoint_identity"] for c in connectors] == [f"google_health:user:{_OWNER}"]
    assert connectors[0]["operational_role"] == "runtime_instance"
    assert connectors[0]["liveness"] == "online"
    # No sub-identity is presented as its own connector, offline or otherwise.
    assert not any(c["liveness"] == "offline" for c in connectors)


async def test_google_health_cursors_are_inspectable_under_their_parent(
    app: FastAPI,
) -> None:
    """Checkpoint history stays reachable, labelled by the stream it tracks."""
    now = dt.datetime.now(dt.UTC)
    rows = _google_health_account(_OWNER, _ACCOUNT_UUID, last_heartbeat_at=now)

    data = await _summaries(app, rows)

    checkpoints = data["connectors"][0]["checkpoints"]
    assert [c["label"] for c in checkpoints] == [
        f"{_ACCOUNT_UUID}:activity",
        f"{_ACCOUNT_UUID}:hrv",
        f"{_ACCOUNT_UUID}:sleep_sessions",
    ]
    assert [c["checkpoint_cursor"] for c in checkpoints] == [
        "activity-cursor",
        "hrv-cursor",
        "sleep_sessions-cursor",
    ]
    # A checkpoint record carries no liveness, state, or health of any kind —
    # that authority belongs to its parent runtime instance alone.
    assert all("liveness" not in c and "state" not in c for c in checkpoints)
    assert data["unparented_checkpoints"] == []


async def test_multi_account_checkpoints_stay_with_their_own_account(
    app: FastAPI,
) -> None:
    """Two Google Health accounts never collect each other's cursors."""
    now = dt.datetime.now(dt.UTC)
    rows = _google_health_account(
        _OWNER, _ACCOUNT_UUID, last_heartbeat_at=now
    ) + _google_health_account(_SECOND_OWNER, _SECOND_ACCOUNT_UUID, last_heartbeat_at=now)

    data = await _summaries(app, rows)

    by_identity = {c["endpoint_identity"]: c for c in data["connectors"]}
    assert set(by_identity) == {
        f"google_health:user:{_OWNER}",
        f"google_health:user:{_SECOND_OWNER}",
    }
    first = by_identity[f"google_health:user:{_OWNER}"]["checkpoints"]
    second = by_identity[f"google_health:user:{_SECOND_OWNER}"]["checkpoints"]
    assert len(first) == 3
    assert len(second) == 3
    assert all(c["endpoint_identity"].startswith(f"google_health:user:{_OWNER}:") for c in first)
    assert all(
        c["endpoint_identity"].startswith(f"google_health:user:{_SECOND_OWNER}:") for c in second
    )
    assert all(_ACCOUNT_UUID in c["label"] for c in first)
    assert all(_SECOND_ACCOUNT_UUID in c["label"] for c in second)


async def test_orphaned_checkpoint_is_surfaced_not_dropped(app: FastAPI) -> None:
    """A cursor whose parent row is gone stays visible, with no status authority."""
    now = dt.datetime.now(dt.UTC)
    rows = [
        _runtime_row(
            connector_type="gmail",
            endpoint_identity="gmail:user:someone@example.test",
            last_heartbeat_at=now,
        ),
        _checkpoint_row(
            connector_type="google_health",
            endpoint_identity=f"google_health:user:{_OWNER}:{_ACCOUNT_UUID}:activity",
            parent_endpoint_identity=None,
        ),
    ]

    data = await _summaries(app, rows)

    assert [c["connector_type"] for c in data["connectors"]] == ["gmail"]
    orphans = data["unparented_checkpoints"]
    assert len(orphans) == 1
    assert orphans[0]["endpoint_identity"] == (
        f"google_health:user:{_OWNER}:{_ACCOUNT_UUID}:activity"
    )
    assert orphans[0]["parent_endpoint_identity"] is None
    # No parent to strip, so the label falls back to the full identity rather
    # than inventing a shorter name for a record whose owner is unknown.
    assert orphans[0]["label"] == orphans[0]["endpoint_identity"]


async def test_checkpoint_naming_a_missing_parent_is_treated_as_unparented(
    app: FastAPI,
) -> None:
    """A recorded parent that has no registry row does not silently vanish."""
    rows = [
        _checkpoint_row(
            connector_type="google_health",
            endpoint_identity=f"google_health:user:{_OWNER}:{_ACCOUNT_UUID}:sleep_sessions",
            parent_endpoint_identity=f"google_health:user:{_OWNER}",
        ),
    ]

    data = await _summaries(app, rows)

    assert data["connectors"] == []
    assert len(data["unparented_checkpoints"]) == 1


# ---------------------------------------------------------------------------
# Unknown classification — a named unavailable state
# ---------------------------------------------------------------------------


async def test_unknown_role_reports_unclassified_liveness(app: FastAPI) -> None:
    """An unclassified row is never read as active, healthy, or offline."""
    now = dt.datetime.now(dt.UTC)
    rows = [
        _runtime_row(connector_type="gmail", endpoint_identity="gmail:a", last_heartbeat_at=now),
        _unknown_row(connector_type="steam", endpoint_identity="steam:unconfigured"),
    ]

    data = await _summaries(app, rows)

    by_type = {c["connector_type"]: c for c in data["connectors"]}
    assert by_type["steam"]["operational_role"] == "unknown"
    assert by_type["steam"]["liveness"] == "unclassified"
    assert by_type["gmail"]["liveness"] == "online"
    assert data["unclassified_count"] == 1


async def test_unclassified_row_is_excluded_from_fleet_liveness(app: FastAPI) -> None:
    """Fleet liveness counts executable runtime instances only."""
    now = dt.datetime.now(dt.UTC)
    rows = [
        _runtime_row(connector_type="gmail", endpoint_identity="gmail:a", last_heartbeat_at=now),
        _unknown_row(connector_type="steam", endpoint_identity="steam:unconfigured"),
    ]

    data = await _cross_summary(app, rows)

    assert data["total_connectors"] == 1
    assert data["connectors_online"] == 1
    assert data["connectors_offline"] == 0
    assert data["connectors_stale"] == 0
    assert data["connectors_unclassified"] == 1


async def test_unclassified_row_never_counts_as_a_healthy_connector(
    app: FastAPI,
) -> None:
    """An unclassified row is not folded into online — the whole point of the state."""
    rows = [_unknown_row(connector_type="steam", endpoint_identity="steam:unconfigured")]

    data = await _cross_summary(app, rows)

    assert data["connectors_online"] == 0
    assert data["total_connectors"] == 0
    assert data["connectors_unclassified"] == 1


# ---------------------------------------------------------------------------
# Fleet rollups count runtime instances only
# ---------------------------------------------------------------------------


async def test_cross_summary_ignores_checkpoint_rows(app: FastAPI) -> None:
    """Three cursors beside one online account is a fleet of one, fully online."""
    now = dt.datetime.now(dt.UTC)
    rows = _google_health_account(_OWNER, _ACCOUNT_UUID, last_heartbeat_at=now)

    data = await _cross_summary(app, rows)

    assert data["total_connectors"] == 1
    assert data["connectors_online"] == 1
    assert data["connectors_offline"] == 0
    assert data["connectors_unclassified"] == 0


async def test_cross_summary_excludes_checkpoint_message_counters(
    app: FastAPI,
) -> None:
    """Storage rows contribute no volume, so they cannot move the error rate."""
    now = dt.datetime.now(dt.UTC)
    rows = _google_health_account(_OWNER, _ACCOUNT_UUID, last_heartbeat_at=now)

    data = await _cross_summary(app, rows)

    # Only the runtime instance's counters (10 ingested / 0 failed) are counted.
    assert data["total_messages_ingested"] == 10
    assert data["total_messages_failed"] == 0


async def test_runtime_instance_offline_still_counts_as_offline(app: FastAPI) -> None:
    """Excluding storage rows must not also hide a genuinely dead process."""
    rows = [
        _runtime_row(
            connector_type="gmail",
            endpoint_identity="gmail:a",
            last_heartbeat_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=3),
        )
    ]

    data = await _cross_summary(app, rows)

    assert data["total_connectors"] == 1
    assert data["connectors_offline"] == 1
    assert data["connectors_unclassified"] == 0


# ---------------------------------------------------------------------------
# Source failure
# ---------------------------------------------------------------------------


async def test_registry_failure_degrades_explicitly(app: FastAPI) -> None:
    """A failed registry read never fabricates a roster or a classification."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("registry unreachable"))
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    data = await _get(app, "/api/ingestion/connectors/summaries")

    assert data["connector_registry_available"] is False
    assert data["connectors"] == []


async def test_cross_summary_failure_reports_zero_not_healthy(app: FastAPI) -> None:
    """A failed rollup query degrades to zeros, including the unclassified count."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("registry unreachable"))
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    data = await _get(app, "/api/ingestion/connectors/cross-summary")

    assert data["total_connectors"] == 0
    assert data["connectors_online"] == 0
    assert data["connectors_unclassified"] == 0
