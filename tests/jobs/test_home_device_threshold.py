"""Home device-threshold owner conditions (bu-6im1ew, REQ-owner-condition-ledger-008).

``run_device_health_check`` reconciles its battery and offline classification into the owner
condition ledger as source ``home:device-threshold``, best-effort, beside its notification and
memory-fact paths. Unit tests pin the observation rules and the job's skip/failure paths; the
integration tests run the real job against the real ledger SQL in Postgres.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.core.owner_conditions import compute_fingerprint, list_conditions
from butlers.jobs import home as home_jobs
from butlers.jobs.home import (
    DEVICE_THRESHOLD_SOURCE,
    build_device_threshold_observations,
    run_device_health_check,
)

_BATTERY = "sensor.hall_lock_battery"
_PLUG = "switch.garage_plug"


def _issue(entity_id: str, issue_type: str, severity: str, value: Any) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "friendly_name": f"Name of {entity_id}",
        "issue_type": issue_type,
        "severity": severity,
        "value": value,
        "description": "ignored by the producer",
    }


def _fingerprint(entity_id: str, issue: str) -> str:
    return compute_fingerprint(DEVICE_THRESHOLD_SOURCE, 1, {"entity_id": entity_id, "issue": issue})


# ---------------------------------------------------------------------------
# Observation rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_only_warning_and_critical_qualify_and_unmeasured_batteries_are_carried() -> None:
    observations = build_device_threshold_observations(
        [
            _issue(_BATTERY, "battery", "critical", 8.0),
            _issue("sensor.desk_battery", "battery", "info", 25.0),
            _issue(_PLUG, "offline", "warning", "unavailable"),
        ],
        unmeasured_battery_entity_ids=["sensor.porch_battery"],
    )

    by_fp = {obs.fingerprint: obs for obs in observations}
    assert set(by_fp) == {
        _fingerprint(_BATTERY, "battery"),
        _fingerprint(_PLUG, "offline"),
        _fingerprint("sensor.porch_battery", "battery"),
    }
    battery = by_fp[_fingerprint(_BATTERY, "battery")]
    assert battery.metadata == {
        "entity_id": _BATTERY,
        "friendly_name": f"Name of {_BATTERY}",
        "issue": "battery",
        "severity": "critical",
        "battery_pct": 8.0,
    }
    assert battery.summary == f"Name of {_BATTERY}: battery at 8% (critical)"
    offline = by_fp[_fingerprint(_PLUG, "offline")]
    # Offline carries no level or raw HA state; only the owner-surface fields.
    assert offline.metadata == {
        "entity_id": _PLUG,
        "friendly_name": f"Name of {_PLUG}",
        "issue": "offline",
        "severity": "warning",
    }
    assert offline.summary == f"Name of {_PLUG}: offline (warning)"
    carried = by_fp[_fingerprint("sensor.porch_battery", "battery")]
    # Carried forward without fabricated evidence, so the stored reading is kept.
    assert carried.confirm_only and carried.summary is None and carried.metadata is None


# ---------------------------------------------------------------------------
# Job wiring (ledger stubbed)
# ---------------------------------------------------------------------------


def _row(entity_id: str, state: str, *, hours_ago: float = 0.1) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": {"friendly_name": entity_id},
        "last_updated": datetime.now(UTC) - timedelta(hours=hours_ago),
    }


def _mock_pool(rows: list[dict[str, Any]], *, ha_status: str = "healthy") -> MagicMock:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=rows)
    pool.execute = AsyncMock()
    healthy = ha_status == "healthy"
    pool.fetchrow = AsyncMock(
        return_value={
            "status": ha_status,
            "last_success_at": datetime.now(UTC) if healthy else None,
            "lease_current": healthy,
        }
    )
    return pool


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ha_status", "rows"),
    [("error", [_row(_BATTERY, "8")]), ("healthy", [])],
    ids=["ha-source-unmeasurable", "empty-snapshot"],
)
async def test_an_unmeasurable_source_or_empty_snapshot_never_reconciles(ha_status, rows) -> None:
    pool = _mock_pool(rows, ha_status=ha_status)
    with (
        patch.object(home_jobs, "_send_notify", new_callable=AsyncMock),
        patch.object(home_jobs, "reconcile_owner_condition", new_callable=AsyncMock) as reconcile,
    ):
        result = await run_device_health_check(pool, None)

    assert "error" in result
    reconcile.assert_not_awaited()


@pytest.mark.unit
async def test_a_ledger_failure_leaves_notification_and_memory_facts_working() -> None:
    pool = _mock_pool([_row(_BATTERY, "8"), _row(_PLUG, "unavailable", hours_ago=30)])
    with (
        patch.object(home_jobs, "_send_notify", new_callable=AsyncMock) as notify,
        patch.object(home_jobs, "_store_device_fact", new_callable=AsyncMock) as fact,
        patch.object(
            home_jobs,
            "reconcile_owner_condition",
            new_callable=AsyncMock,
            side_effect=asyncpg.PostgresError("ledger down"),
        ) as reconcile,
    ):
        result = await run_device_health_check(pool, None)

    reconcile.assert_awaited_once()
    assert reconcile.await_args.kwargs["snapshot_complete"] is True
    assert result["critical_count"] == 2
    notify.assert_awaited_once()
    assert fact.await_count == 2


# ---------------------------------------------------------------------------
# Real Postgres: the real job against the real ledger
# ---------------------------------------------------------------------------


async def _home_pool(db) -> asyncpg.Pool:
    """The home butler's real schema: owner_conditions (core) and the home chain."""
    return await db("core", "home", schemas={"home": "home"}, schema="home")


async def _set_entity(
    pool: asyncpg.Pool, entity_id: str, state: str, *, hours_ago: float = 0.1
) -> None:
    attributes = {
        "friendly_name": f"Name of {entity_id}",
        "device_class": "battery",
        "ieee_address": "0x00158d0001",
    }
    await pool.execute(
        "INSERT INTO ha_entity_snapshot (entity_id, state, attributes, last_updated) "
        "VALUES ($1, $2, $3::jsonb, now() - $4 * interval '1 hour') "
        "ON CONFLICT (entity_id) DO UPDATE SET state = EXCLUDED.state, "
        "attributes = EXCLUDED.attributes, last_updated = EXCLUDED.last_updated",
        entity_id,
        state,
        json.dumps(attributes),
        hours_ago,
    )


async def _set_health(pool: asyncpg.Pool, *, healthy: bool) -> None:
    await pool.execute(
        "INSERT INTO ha_source_health (source, status, last_success_at) VALUES "
        "('home_assistant', $1, now() - ($2 * interval '1 minute')) "
        "ON CONFLICT (source) DO UPDATE SET status = EXCLUDED.status, "
        "last_success_at = EXCLUDED.last_success_at",
        "healthy",
        0 if healthy else 30,
    )


async def _run(pool: asyncpg.Pool) -> dict[str, Any]:
    with (
        patch.object(home_jobs, "_send_notify", new_callable=AsyncMock),
        patch.object(home_jobs, "_store_device_fact", new_callable=AsyncMock),
    ):
        return await run_device_health_check(pool, None)


async def _conditions(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    _total, rows = await list_conditions(pool, source=DEVICE_THRESHOLD_SOURCE)
    for row in rows:
        if isinstance(row.get("metadata"), str):
            row["metadata"] = json.loads(row["metadata"])
    return rows


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_low_battery_opens_and_an_info_level_recovery_resolves(db) -> None:
    pool = await _home_pool(db)
    await _set_health(pool, healthy=True)
    # Task 2.1: a reading HA has not changed for weeks is still HA's current value.
    await _set_entity(pool, _BATTERY, "15", hours_ago=24 * 30)
    await _set_entity(pool, "sensor.desk_battery", "25")

    await _run(pool)
    rows = await _conditions(pool)
    assert len(rows) == 1 and rows[0]["state"] in ("open", "aging")
    assert rows[0]["metadata"] == {
        "entity_id": _BATTERY,
        "friendly_name": f"Name of {_BATTERY}",
        "issue": "battery",
        "severity": "warning",
        "battery_pct": 15.0,
    }

    await _set_entity(pool, _BATTERY, "25")
    await _run(pool)
    rows = await _conditions(pool)
    assert [row["state"] for row in rows] == ["resolved"]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_unavailable_battery_is_carried_until_a_numeric_reading(db) -> None:
    pool = await _home_pool(db)
    await _set_health(pool, healthy=True)
    await _set_entity(pool, _BATTERY, "8")
    await _run(pool)

    for state in ("unavailable", "unknown"):
        await _set_entity(pool, _BATTERY, state)
        await _run(pool)
        battery = [r for r in await _conditions(pool) if r["metadata"]["issue"] == "battery"]
        assert len(battery) == 1 and battery[0]["state"] in ("open", "aging")
        assert battery[0]["metadata"]["battery_pct"] == 8.0

    await _set_entity(pool, _BATTERY, "90")
    await _run(pool)
    battery = [r for r in await _conditions(pool) if r["metadata"]["issue"] == "battery"]
    assert [r["state"] for r in battery] == ["resolved"]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_offline_escalation_keeps_one_episode(db) -> None:
    pool = await _home_pool(db)
    await _set_health(pool, healthy=True)
    await _set_entity(pool, _PLUG, "unavailable", hours_ago=2)
    await _run(pool)
    await _set_entity(pool, _PLUG, "unavailable", hours_ago=30)
    await _run(pool)

    rows = await _conditions(pool)
    assert len(rows) == 1 and rows[0]["episode"] == 1
    assert rows[0]["metadata"]["severity"] == "critical"
    assert rows[0]["fingerprint"] == _fingerprint(_PLUG, "offline")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_unmeasurable_source_or_empty_snapshot_leaves_conditions_active(db) -> None:
    pool = await _home_pool(db)
    await _set_health(pool, healthy=True)
    await _set_entity(pool, _BATTERY, "8")
    await _run(pool)

    await _set_entity(pool, _BATTERY, "90")
    await _set_health(pool, healthy=False)
    assert (await _run(pool))["error"] == "ha_source_unmeasurable"
    await _set_health(pool, healthy=True)
    await pool.execute("DELETE FROM ha_entity_snapshot")
    assert (await _run(pool))["error"] == "no_entity_snapshot"

    assert [r["state"] in ("open", "aging") for r in await _conditions(pool)] == [True]
