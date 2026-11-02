"""The safety job debounces on last_changed, not last_updated (bu-jv1moa.18).

Home Assistant bumps ``last_updated`` on any attribute change.  The edge path judges a sensor on
``last_changed`` (state changes only), so the five-minute job must too: a sensor that has been
``on`` for an hour must not look freshly changed because an attribute updated seconds ago.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.jobs import home as home_jobs
from butlers.jobs import home_safety
from butlers.jobs.home_safety import SensorReading, WatchEntry, classify, run_safety_sentinel
from butlers.testing.db import open_pool

_NOW = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)
_LEAK = WatchEntry("binary_sensor.kitchen_leak", "leak", "Kitchen leak sensor")
_ROOT = Path(__file__).resolve().parents[2]


async def _readings_the_job_judges(
    monkeypatch: pytest.MonkeyPatch, *, last_changed: datetime | None, last_updated: datetime
) -> dict[str, SensorReading]:
    watch = [{"entity_id": _LEAK.entity_id, "role": "leak", "owner_label": _LEAK.owner_label}]
    snapshot = [
        {
            "entity_id": _LEAK.entity_id,
            "state": "on",
            "last_changed": last_changed,
            "last_updated": last_updated,
        }
    ]
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=[watch, snapshot])
    monkeypatch.setattr(home_jobs, "_require_ha_source_healthy", AsyncMock())
    reconcile = AsyncMock(return_value=home_safety.SafetyRunResult())
    monkeypatch.setattr(home_safety, "reconcile_safety", reconcile)

    await run_safety_sentinel(pool, None, notify_fn=AsyncMock(), _now=_NOW)

    return reconcile.await_args.args[2]


@pytest.mark.unit
async def test_an_attribute_only_update_does_not_reset_the_debounce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = await _readings_the_job_judges(
        monkeypatch,
        last_changed=_NOW - timedelta(hours=1),
        last_updated=_NOW - timedelta(seconds=10),
    )

    assert readings[_LEAK.entity_id].since == _NOW - timedelta(hours=1)
    assert classify(_LEAK, readings[_LEAK.entity_id], _NOW) == "alarm"


@pytest.mark.unit
async def test_a_state_change_inside_the_window_is_still_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = await _readings_the_job_judges(
        monkeypatch,
        last_changed=_NOW - timedelta(seconds=20),
        last_updated=_NOW - timedelta(seconds=20),
    )

    assert classify(_LEAK, readings[_LEAK.entity_id], _NOW) == "pending"


@pytest.mark.unit
async def test_a_row_without_last_changed_falls_back_to_last_updated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = await _readings_the_job_judges(
        monkeypatch, last_changed=None, last_updated=_NOW - timedelta(minutes=5)
    )

    assert readings[_LEAK.entity_id].since == _NOW - timedelta(minutes=5)
    assert classify(_LEAK, readings[_LEAK.entity_id], _NOW) == "alarm"


def _migration_sql(direction: str) -> list[str]:
    path = _ROOT / "roster/home/migrations/005_ha_entity_snapshot_last_changed.py"
    spec = importlib.util.spec_from_file_location("home_005", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("home_005", "home_004")
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(module, "op", fake_op):
        getattr(module, direction)()
    return statements


async def _has_last_changed(pool: asyncpg.Pool) -> bool:
    return bool(
        await pool.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'ha_entity_snapshot' AND column_name = 'last_changed'"
        )
    )


@pytest.mark.integration
async def test_last_changed_migration_upgrades_downgrades_and_reupgrades(fresh_db) -> None:
    # The real home chain stopped at home_004, holding a pre-home_005 row.
    url = await asyncio.to_thread(fresh_db, "core", "home", revisions={"home": "home_004"})
    pool = await open_pool(url)
    try:
        assert not await _has_last_changed(pool)
        await pool.execute(
            "INSERT INTO ha_entity_snapshot (entity_id, state, last_updated) "
            "VALUES ($1, 'on', now())",
            _LEAK.entity_id,
        )

        for statement in _migration_sql("upgrade"):
            await pool.execute(statement)
        assert await _has_last_changed(pool)
        # Nullable: the existing row has no value until the module's next persist.
        changed = await pool.fetchval(
            "SELECT last_changed FROM ha_entity_snapshot WHERE entity_id = $1", _LEAK.entity_id
        )
        assert changed is None
        await pool.execute(
            "UPDATE ha_entity_snapshot SET last_changed = now() WHERE entity_id = $1",
            _LEAK.entity_id,
        )

        for statement in _migration_sql("downgrade"):
            await pool.execute(statement)
        assert not await _has_last_changed(pool)
        assert await pool.fetchval("SELECT state FROM ha_entity_snapshot") == "on"

        for statement in _migration_sql("upgrade"):
            await pool.execute(statement)
        assert await _has_last_changed(pool)
        assert await pool.fetchval("SELECT count(*) FROM ha_entity_snapshot") == 1
    finally:
        await pool.close()
