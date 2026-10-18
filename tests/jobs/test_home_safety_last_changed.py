"""The safety job debounces on last_changed, not last_updated (bu-jv1moa.18).

Home Assistant bumps ``last_updated`` on any attribute change.  The edge path judges a sensor on
``last_changed`` (state changes only), so the five-minute job must too: a sensor that has been
``on`` for an hour must not look freshly changed because an attribute updated seconds ago.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.jobs import home as home_jobs
from butlers.jobs import home_safety
from butlers.jobs.home_safety import SensorReading, WatchEntry, classify, run_safety_sentinel

pytestmark = pytest.mark.unit

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

    assert "last_changed" in pool.fetch.await_args_list[1].args[0]
    return reconcile.await_args.args[2]


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


async def test_a_state_change_inside_the_window_is_still_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = await _readings_the_job_judges(
        monkeypatch,
        last_changed=_NOW - timedelta(seconds=20),
        last_updated=_NOW - timedelta(seconds=20),
    )

    assert classify(_LEAK, readings[_LEAK.entity_id], _NOW) == "pending"


async def test_a_row_without_last_changed_falls_back_to_last_updated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = await _readings_the_job_judges(
        monkeypatch, last_changed=None, last_updated=_NOW - timedelta(minutes=5)
    )

    assert readings[_LEAK.entity_id].since == _NOW - timedelta(minutes=5)
    assert classify(_LEAK, readings[_LEAK.entity_id], _NOW) == "alarm"


async def test_the_module_persists_last_changed_with_each_entity() -> None:
    from roster.home.modules import CachedEntity, HomeAssistantConfig, HomeAssistantModule

    pool = MagicMock()
    pool.executemany = AsyncMock()
    module = HomeAssistantModule()
    module._config = HomeAssistantConfig()
    module._db = MagicMock(pool=pool)
    module._entity_cache = {
        "binary_sensor.kitchen_leak": CachedEntity(
            entity_id="binary_sensor.kitchen_leak",
            state="on",
            attributes={},
            last_updated="2026-10-19T11:59:50+00:00",
            last_changed="2026-10-19T11:00:00+00:00",
        ),
        "light.kitchen": CachedEntity(entity_id="light.kitchen", state="on", attributes={}),
    }

    await module._persist_entity_snapshot()

    sql, rows = pool.executemany.await_args.args
    assert "last_changed" in sql and "$5::text::timestamptz" in sql
    by_id: dict[str, Any] = {row[0]: row for row in rows}
    assert by_id["binary_sensor.kitchen_leak"][3:] == (
        "2026-10-19T11:59:50+00:00",
        "2026-10-19T11:00:00+00:00",
    )
    assert by_id["light.kitchen"][3:] == (None, None)


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


def test_the_migration_adds_and_drops_a_nullable_last_changed_column() -> None:
    (up,) = _migration_sql("upgrade")
    (down,) = _migration_sql("downgrade")

    assert up == "ALTER TABLE ha_entity_snapshot ADD COLUMN IF NOT EXISTS last_changed TIMESTAMPTZ"
    assert down == "ALTER TABLE ha_entity_snapshot DROP COLUMN IF EXISTS last_changed"
