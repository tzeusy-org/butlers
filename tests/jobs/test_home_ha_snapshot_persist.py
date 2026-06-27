"""DB-level regression test for the home ``ha_entity_snapshot`` write path (bu-rgz7t).

The dashboard API (``roster/home/api/router.py``) and the home scheduled jobs
(``src/butlers/jobs/home.py``) all READ live HA entity state from the
``ha_entity_snapshot`` table, but nothing ever WROTE to it: the only writer the
module had persisted ``ha_state`` SPO facts instead (and was never even
started), so every live-state read returned an empty snapshot.

This test exercises the REAL write+read path against a migrated Postgres:

1. Populate the module's in-memory entity cache (as a WS/REST state update
   would) and call ``HomeAssistantModule._persist_entity_snapshot()`` — the
   writer under test.
2. Read it back through the canonical job reader
   ``butlers.jobs.home._read_entity_snapshot`` and assert it returns the ACTUAL
   current state (not empty, no ``EmptyEntitySnapshotError``).
3. Confirm the dashboard-shaped read (``entity_id, state, attributes,
   last_updated, captured_at`` with ``attributes->>'area_id'``) also resolves.

It also proves a subsequent state update overwrites in place (one row per
entity, not unbounded growth).
"""

from __future__ import annotations

import shutil
from typing import Any
from unittest.mock import MagicMock

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.jobs.home import _read_entity_snapshot
from butlers.modules._roster_home import (
    CachedArea,
    CachedEntity,
    HomeAssistantConfig,
    HomeAssistantModule,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision core + home chains (flat public topology)."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "home"],
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE ha_entity_snapshot")
    yield p
    await p.close()


def _module_with_pool(pool: asyncpg.Pool) -> HomeAssistantModule:
    module = HomeAssistantModule()
    module._config = HomeAssistantConfig()
    db = MagicMock()
    db.pool = pool
    module._db = db
    return module


async def test_persist_then_read_returns_live_state(pool: asyncpg.Pool) -> None:
    """A persisted entity cache is readable through the job read path."""
    module = _module_with_pool(pool)
    module._area_cache = {"area_lr": CachedArea(area_id="area_lr", name="Living Room")}
    module._entity_cache = {
        "sensor.living_room_temperature": CachedEntity(
            entity_id="sensor.living_room_temperature",
            state="22.5",
            attributes={"friendly_name": "Living Room Temperature", "unit_of_measurement": "°C"},
            last_updated="2026-06-27T10:00:00+00:00",
            area_id="area_lr",
        ),
        "light.kitchen": CachedEntity(
            entity_id="light.kitchen",
            state="on",
            attributes={"friendly_name": "Kitchen Light"},
            last_updated="2026-06-27T10:01:00+00:00",
        ),
    }

    # Read path is empty before any write.
    from butlers.jobs.home import EmptyEntitySnapshotError

    with pytest.raises(EmptyEntitySnapshotError):
        await _read_entity_snapshot(pool)

    # Writer under test.
    await module._persist_entity_snapshot()

    # Canonical job read path now returns the actual current state.
    rows = await _read_entity_snapshot(pool)
    by_id: dict[str, Any] = {r["entity_id"]: r for r in rows}
    assert set(by_id) == {"sensor.living_room_temperature", "light.kitchen"}
    assert by_id["sensor.living_room_temperature"]["state"] == "22.5"
    assert by_id["light.kitchen"]["state"] == "on"

    # Domain filter (used by jobs) works too.
    sensor_rows = await _read_entity_snapshot(pool, domain_filter="sensor")
    assert [r["entity_id"] for r in sensor_rows] == ["sensor.living_room_temperature"]

    # Dashboard-shaped read resolves, including registry-derived area_id merged
    # into the attributes JSONB.
    row = await pool.fetchrow(
        "SELECT entity_id, state, attributes, last_updated, captured_at"
        " FROM ha_entity_snapshot WHERE entity_id = $1",
        "sensor.living_room_temperature",
    )
    assert row is not None
    assert row["captured_at"] is not None
    attrs = dict(row["attributes"] or {})
    assert attrs["friendly_name"] == "Living Room Temperature"
    assert attrs["area_id"] == "area_lr"
    assert attrs["area_name"] == "Living Room"

    area_id = await pool.fetchval(
        "SELECT attributes->>'area_id' FROM ha_entity_snapshot WHERE entity_id = $1",
        "sensor.living_room_temperature",
    )
    assert area_id == "area_lr"


async def test_state_update_overwrites_in_place(pool: asyncpg.Pool) -> None:
    """A later state update supersedes the prior row (one row per entity)."""
    module = _module_with_pool(pool)
    module._entity_cache = {
        "sensor.living_room_temperature": CachedEntity(
            entity_id="sensor.living_room_temperature",
            state="22.5",
            attributes={"friendly_name": "Living Room Temperature"},
            last_updated="2026-06-27T10:00:00+00:00",
        )
    }
    await module._persist_entity_snapshot()

    # Simulate an HA state_changed update.
    module._entity_cache["sensor.living_room_temperature"].state = "25.0"
    module._entity_cache[
        "sensor.living_room_temperature"
    ].last_updated = "2026-06-27T10:05:00+00:00"
    await module._persist_entity_snapshot()

    rows = await _read_entity_snapshot(pool)
    assert len(rows) == 1
    assert rows[0]["state"] == "25.0"
