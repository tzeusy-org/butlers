"""Real-Postgres integration tests for the HA sensor-activity adapter (bu-49fqa).

Mocked-pool tests cannot validate the ``ON CONFLICT (source_name, source_ref)
DO UPDATE`` upsert semantics the adapter's idempotency and cross-batch
carryover depend on (see "Mocked-pool vs integration test gap" — PR #2598
class, ~8h main-red from SQL that passed mocked-pool tests but broke against
real Postgres). These tests run the real ``core`` + ``chronicler`` migration
chains, insert real ``connectors.filtered_events`` rows (via the real
``connectors_filtered_events_ensure_partition`` partition-provisioning
function, not a hand-rolled INSERT), and run the adapter against a migrated
Postgres container.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import asyncpg
import pytest

from butlers.chronicler.adapters.home_assistant_sensor_activity import (
    EPISODE_TYPE_ROOM_ACTIVITY,
    EVENT_TYPE_ENTRY,
    SOURCE_NAME,
    HomeAssistantSensorActivityAdapter,
)
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import Episode, Layer, Precision, Privacy
from butlers.chronicler.storage import upsert_episode
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_NOW = datetime(2026, 7, 6, 8, 0, 0, tzinfo=UTC)
_ADAPTER_MODULE = "butlers.chronicler.adapters.home_assistant_sensor_activity"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the core (connectors.filtered_events) + chronicler chains."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=5, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE episodes, point_events CASCADE")
    await p.execute("TRUNCATE TABLE connectors.filtered_events")
    await p.execute("DELETE FROM projection_checkpoints WHERE source_name = $1", SOURCE_NAME)
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_filtered_event(
    pool: asyncpg.Pool,
    *,
    received_at: datetime,
    entity_id: str,
    domain: str,
    device_class: str | None,
    new_state: str | None,
    old_state: str | None = None,
    friendly_name: str | None = None,
) -> None:
    """Insert one real connectors.filtered_events row via the partition function."""
    await pool.execute(
        "SELECT connectors.connectors_filtered_events_ensure_partition($1)", received_at
    )
    raw: dict = {
        "entity_id": entity_id,
        "event_type": "state_changed",
        "domain": domain,
        "device_class": device_class,
        "friendly_name": friendly_name,
    }
    if old_state is not None:
        raw["old_state"] = {"state": old_state}
    if new_state is not None:
        raw["new_state"] = {"state": new_state}

    full_payload = {
        "source": {"channel": "home_assistant", "provider": "home_assistant"},
        "event": {"external_event_id": f"ha:{entity_id}:{int(received_at.timestamp() * 1000)}"},
        "sender": {"identity": entity_id},
        "payload": {"raw": raw},
        "control": {},
    }
    await pool.execute(
        """
        INSERT INTO connectors.filtered_events (
            received_at, connector_type, endpoint_identity, external_message_id,
            source_channel, sender_identity, subject_or_preview, filter_reason,
            status, full_payload, error_detail
        ) VALUES ($1, 'home_assistant', 'home_assistant:test:1', $2, 'home_assistant', $3,
                  NULL, 'insignificant_delta:x:0', 'filtered', $4, NULL)
        """,
        received_at,
        f"ha:{entity_id}:{int(received_at.timestamp() * 1000)}",
        entity_id,
        full_payload,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_motion_projects_uncorroborated_evidence_episode(pool) -> None:
    entity = "binary_sensor.hallway_motion"
    t1 = _NOW
    t2 = _NOW + timedelta(minutes=5)
    await _insert_filtered_event(
        pool,
        received_at=t1,
        entity_id=entity,
        domain="binary_sensor",
        device_class="motion",
        new_state="on",
    )
    await _insert_filtered_event(
        pool,
        received_at=t2,
        entity_id=entity,
        domain="binary_sensor",
        device_class="motion",
        new_state="on",
    )

    adapter = HomeAssistantSensorActivityAdapter()
    result = await adapter.run(pool=pool, chronicler_pool=pool)

    assert result.success
    assert result.episodes_closed == 1

    rows = await pool.fetch(
        "SELECT * FROM episodes WHERE source_name = $1 AND episode_type = $2",
        SOURCE_NAME,
        EPISODE_TYPE_ROOM_ACTIVITY,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["layer"] == "evidence"
    assert row["start_at"] == t1
    assert row["end_at"] == t2


async def test_door_transition_projects_entry_event_point_event(pool) -> None:
    await _insert_filtered_event(
        pool,
        received_at=_NOW,
        entity_id="binary_sensor.front_door",
        domain="binary_sensor",
        device_class="door",
        old_state="off",
        new_state="on",
        friendly_name="Front Door",
    )

    adapter = HomeAssistantSensorActivityAdapter()
    result = await adapter.run(pool=pool, chronicler_pool=pool)

    assert result.success
    assert result.point_events == 1

    rows = await pool.fetch(
        "SELECT * FROM point_events WHERE source_name = $1 AND event_type = $2",
        SOURCE_NAME,
        EVENT_TYPE_ENTRY,
    )
    assert len(rows) == 1
    assert rows[0]["title"] == "Front Door: on"
    assert rows[0]["layer"] == "evidence"


async def test_corroborated_motion_promotes_to_activity_layer(pool) -> None:
    entity = "binary_sensor.office_motion"
    t1 = _NOW
    t2 = _NOW + timedelta(minutes=10)

    # Seed a corroborating occupation_block episode overlapping the span.
    async with pool.acquire() as conn:
        await upsert_episode(
            conn,
            Episode(
                source_name="chronicler.occupation_inferred",
                source_ref=f"chronicler.routines:{uuid4()}:2026-07-06",
                episode_type="occupation_block",
                start_at=_NOW - timedelta(hours=1),
                end_at=_NOW + timedelta(hours=1),
                precision=Precision.HOUR,
                title="Occupation (test)",
                payload={},
                privacy=Privacy.NORMAL,
                layer=Layer.ACTIVITY,
            ),
        )

    await _insert_filtered_event(
        pool,
        received_at=t1,
        entity_id=entity,
        domain="binary_sensor",
        device_class="motion",
        new_state="on",
    )
    await _insert_filtered_event(
        pool,
        received_at=t2,
        entity_id=entity,
        domain="binary_sensor",
        device_class="motion",
        new_state="on",
    )

    adapter = HomeAssistantSensorActivityAdapter()
    result = await adapter.run(pool=pool, chronicler_pool=pool)

    assert result.success
    rows = await pool.fetch(
        "SELECT * FROM episodes WHERE source_name = $1 AND episode_type = $2",
        SOURCE_NAME,
        EPISODE_TYPE_ROOM_ACTIVITY,
    )
    assert len(rows) == 1
    assert rows[0]["layer"] == "activity"
    assert rows[0]["confidence"] == "low"
    assert len(rows[0]["evidence_refs"]) == 1


async def test_rerun_is_idempotent_no_duplicate_rows(pool) -> None:
    entity = "binary_sensor.kitchen_motion"
    await _insert_filtered_event(
        pool,
        received_at=_NOW,
        entity_id=entity,
        domain="binary_sensor",
        device_class="motion",
        new_state="on",
    )

    adapter = HomeAssistantSensorActivityAdapter()
    first = await adapter.run(pool=pool, chronicler_pool=pool)
    assert first.success

    # Second run: watermark has advanced past the only row — no new rows,
    # no duplicate episode, and the checkpoint's watermark stays put.
    second = await adapter.run(pool=pool, chronicler_pool=pool)
    assert second.success
    assert second.rows_projected == 0

    rows = await pool.fetch(
        "SELECT * FROM episodes WHERE source_name = $1 AND episode_type = $2",
        SOURCE_NAME,
        EPISODE_TYPE_ROOM_ACTIVITY,
    )
    assert len(rows) == 1


async def test_checkpoint_watermark_persists_across_runs(pool) -> None:
    entity = "binary_sensor.study_motion"
    await _insert_filtered_event(
        pool,
        received_at=_NOW,
        entity_id=entity,
        domain="binary_sensor",
        device_class="motion",
        new_state="on",
    )

    adapter = HomeAssistantSensorActivityAdapter()
    await adapter.run(pool=pool, chronicler_pool=pool)

    checkpoint = await pool.fetchrow(
        "SELECT watermark FROM projection_checkpoints WHERE source_name = $1 AND subsource = ''",
        SOURCE_NAME,
    )
    assert checkpoint is not None
    assert checkpoint["watermark"] == _NOW


# ---------------------------------------------------------------------------
# Retroactive evidence -> activity promotion re-check (bu-mul8i)
# ---------------------------------------------------------------------------


async def _seed_occupation_block(
    pool: asyncpg.Pool,
    *,
    start_at: datetime,
    end_at: datetime,
) -> None:
    """Insert a corroborating ``occupation_block`` episode covering a window."""
    async with pool.acquire() as conn:
        await upsert_episode(
            conn,
            Episode(
                source_name="chronicler.occupation_inferred",
                source_ref=f"chronicler.routines:{uuid4()}:recheck",
                episode_type="occupation_block",
                start_at=start_at,
                end_at=end_at,
                precision=Precision.HOUR,
                title="Occupation (test)",
                payload={},
                privacy=Privacy.NORMAL,
                layer=Layer.ACTIVITY,
            ),
        )


async def _room_activity_row(pool: asyncpg.Pool) -> asyncpg.Record:
    rows = await pool.fetch(
        "SELECT * FROM episodes WHERE source_name = $1 AND episode_type = $2",
        SOURCE_NAME,
        EPISODE_TYPE_ROOM_ACTIVITY,
    )
    assert len(rows) == 1
    return rows[0]


async def test_late_corroborator_promotes_evidence_span_on_next_run(pool) -> None:
    """A corroborator landing AFTER clustering still promotes the span."""
    entity = "binary_sensor.lounge_motion"
    await _insert_filtered_event(
        pool,
        received_at=_NOW,
        entity_id=entity,
        domain="binary_sensor",
        device_class="motion",
        new_state="on",
    )

    adapter = HomeAssistantSensorActivityAdapter()
    with patch(f"{_ADAPTER_MODULE}._now", return_value=_NOW + timedelta(minutes=1)):
        first = await adapter.run(pool=pool, chronicler_pool=pool)
    assert first.success
    assert (await _room_activity_row(pool))["layer"] == "evidence"

    # The hourly occupation_inferred tick lands only now — after clustering.
    await _seed_occupation_block(
        pool, start_at=_NOW - timedelta(hours=1), end_at=_NOW + timedelta(hours=1)
    )

    with patch(f"{_ADAPTER_MODULE}._now", return_value=_NOW + timedelta(minutes=2)):
        second = await adapter.run(pool=pool, chronicler_pool=pool)

    assert second.success
    assert second.rows_projected == 0  # no new source rows; promotion only
    row = await _room_activity_row(pool)
    assert row["layer"] == "activity"
    assert row["confidence"] == "low"
    assert len(row["evidence_refs"]) == 1
    assert second.episodes_promoted == 1


async def test_promotion_recheck_is_idempotent(pool) -> None:
    """Running the pass again after a promotion is a no-op (no churn)."""
    entity = "binary_sensor.den_motion"
    await _insert_filtered_event(
        pool,
        received_at=_NOW,
        entity_id=entity,
        domain="binary_sensor",
        device_class="motion",
        new_state="on",
    )
    adapter = HomeAssistantSensorActivityAdapter()
    with patch(f"{_ADAPTER_MODULE}._now", return_value=_NOW + timedelta(minutes=1)):
        await adapter.run(pool=pool, chronicler_pool=pool)
    await _seed_occupation_block(
        pool, start_at=_NOW - timedelta(hours=1), end_at=_NOW + timedelta(hours=1)
    )
    with patch(f"{_ADAPTER_MODULE}._now", return_value=_NOW + timedelta(minutes=2)):
        promoted_run = await adapter.run(pool=pool, chronicler_pool=pool)
    assert promoted_run.episodes_promoted == 1
    after_promotion = await _room_activity_row(pool)

    with patch(f"{_ADAPTER_MODULE}._now", return_value=_NOW + timedelta(minutes=3)):
        rerun = await adapter.run(pool=pool, chronicler_pool=pool)

    assert rerun.episodes_promoted == 0
    unchanged = await _room_activity_row(pool)
    assert unchanged["layer"] == "activity"
    assert unchanged["evidence_refs"] == after_promotion["evidence_refs"]
    assert unchanged["updated_at"] == after_promotion["updated_at"]


async def test_span_outside_lookback_window_is_not_promoted(pool) -> None:
    """The re-check is bounded — an older evidence span is left alone."""
    entity = "binary_sensor.attic_motion"
    old = _NOW - timedelta(days=3)
    await _insert_filtered_event(
        pool,
        received_at=old,
        entity_id=entity,
        domain="binary_sensor",
        device_class="motion",
        new_state="on",
    )
    adapter = HomeAssistantSensorActivityAdapter()
    with patch(f"{_ADAPTER_MODULE}._now", return_value=old + timedelta(minutes=1)):
        await adapter.run(pool=pool, chronicler_pool=pool)
    assert (await _room_activity_row(pool))["layer"] == "evidence"

    await _seed_occupation_block(
        pool, start_at=old - timedelta(hours=1), end_at=old + timedelta(hours=1)
    )

    with patch(f"{_ADAPTER_MODULE}._now", return_value=_NOW):
        late = await adapter.run(pool=pool, chronicler_pool=pool)

    assert late.episodes_promoted == 0
    assert (await _room_activity_row(pool))["layer"] == "evidence"


async def test_span_without_corroborator_stays_evidence(pool) -> None:
    """No corroborator ever arrives — the span must remain evidence-layer."""
    entity = "binary_sensor.pantry_motion"
    await _insert_filtered_event(
        pool,
        received_at=_NOW,
        entity_id=entity,
        domain="binary_sensor",
        device_class="motion",
        new_state="on",
    )
    adapter = HomeAssistantSensorActivityAdapter()
    with patch(f"{_ADAPTER_MODULE}._now", return_value=_NOW + timedelta(minutes=1)):
        await adapter.run(pool=pool, chronicler_pool=pool)

    with patch(f"{_ADAPTER_MODULE}._now", return_value=_NOW + timedelta(minutes=2)):
        rerun = await adapter.run(pool=pool, chronicler_pool=pool)

    assert rerun.episodes_promoted == 0
    assert (await _room_activity_row(pool))["layer"] == "evidence"
