"""Real-Postgres coverage for OwnTracks SSID presence projection."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
import pytest

from butlers.chronicler.adapters.owntracks import OwnTracksPointAdapter
from butlers.chronicler.adapters.owntracks_ssid import (
    EPISODE_TYPE_HOME_PRESENCE,
    EPISODE_TYPE_WORK_PRESENCE,
    SOURCE_NAME,
    SSID_PLACE_STATE_KEY,
    OwnTracksSsidPresenceAdapter,
)
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.jobs import run_project_owntracks_ssid
from butlers.core.state import state_set
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_NOW = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)
_ENDPOINT = "owntracks:alice"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
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
    await p.execute("TRUNCATE TABLE connectors.owntracks_points CASCADE")
    await p.execute("TRUNCATE TABLE episodes, point_events, projection_checkpoints CASCADE")
    await p.execute("DELETE FROM state WHERE key = $1", SSID_PLACE_STATE_KEY)
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.close()


async def _insert_point(
    pool: asyncpg.Pool,
    minute: int,
    ssid: str | None,
    *,
    point_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> None:
    ts = _NOW + timedelta(minutes=minute)
    raw_payload: dict[str, object] = {"_type": "location", "lat": 1.3, "lon": 103.8}
    if ssid is not None:
        raw_payload["SSID"] = ssid
    await pool.execute(
        """
        INSERT INTO connectors.owntracks_points
            (id, idempotency_key, ts, lat, lon, accuracy, trigger, event,
             endpoint_identity, raw_payload, recorded_at)
        VALUES (COALESCE($1, gen_random_uuid()), $2, $3, 1.3, 103.8, 10.0, 'p', NULL, $4, $5, $3)
        """,
        point_id,
        idempotency_key or f"ssid-test:{minute}",
        ts,
        _ENDPOINT,
        raw_payload,
    )


async def test_state_mapping_projects_gapped_runs_and_skips_unlabelled_ssid(
    pool: asyncpg.Pool,
) -> None:
    await state_set(pool, SSID_PLACE_STATE_KEY, {"Corp WiFi": "work"})

    # First mapped run, an unlabelled SSID boundary, then another mapped run.
    # The unlabelled point is within max_gap of both runs but must never bridge them.
    for minute, ssid in [
        (0, "Corp WiFi"),
        (10, "Corp WiFi"),
        (20, "Cafe WiFi"),
        (30, "Corp WiFi"),
        (40, "Corp WiFi"),
    ]:
        await _insert_point(pool, minute, ssid)

    await OwnTracksPointAdapter().project(pool, chronicler_pool=pool, since=None)
    result = await run_project_owntracks_ssid(pool, None)

    assert result["source_name"] == SOURCE_NAME
    assert result["episodes_closed"] == 2
    rows = await pool.fetch(
        """
        SELECT * FROM episodes
        WHERE source_name = $1 AND episode_type = $2
        ORDER BY start_at
        """,
        SOURCE_NAME,
        EPISODE_TYPE_WORK_PRESENCE,
    )
    assert [(row["start_at"], row["end_at"]) for row in rows] == [
        (_NOW, _NOW + timedelta(minutes=10)),
        (_NOW + timedelta(minutes=30), _NOW + timedelta(minutes=40)),
    ]
    assert all(row["precision"] == "minute" for row in rows)
    assert all(row["layer"] == "activity" for row in rows)
    assert all(row["confidence"] == "medium" for row in rows)
    assert all(row["payload"]["place"] == "work" for row in rows)
    assert all("Cafe WiFi" not in str(row["payload"]) for row in rows)
    assert all(row["evidence_refs"] for row in rows)

    # An empty scheduled poll must not erase the open endpoint carryover. The
    # next point extends the second run in place instead of becoming an
    # unprojected singleton or creating a duplicate episode.
    empty_result = await run_project_owntracks_ssid(pool, None)
    assert empty_result["episodes_closed"] == 0

    await _insert_point(pool, 50, "Corp WiFi")
    resumed_result = await run_project_owntracks_ssid(pool, None)
    assert resumed_result["episodes_closed"] == 1

    resumed_rows = await pool.fetch(
        """
        SELECT * FROM episodes
        WHERE source_name = $1 AND episode_type = $2
        ORDER BY start_at
        """,
        SOURCE_NAME,
        EPISODE_TYPE_WORK_PRESENCE,
    )
    assert len(resumed_rows) == 2
    assert resumed_rows[-1]["start_at"] == _NOW + timedelta(minutes=30)
    assert resumed_rows[-1]["end_at"] == _NOW + timedelta(minutes=50)
    assert resumed_rows[-1]["payload"]["point_count"] == 3

    source_state = await pool.fetchrow(
        "SELECT * FROM source_adapter_state WHERE source_name = $1",
        SOURCE_NAME,
    )
    assert source_state is not None
    assert source_state["chronicler_compatibility"] == "supported"
    assert source_state["active"] is True

    checkpoint = await pool.fetchrow(
        """
        SELECT watermark, carryover
        FROM projection_checkpoints
        WHERE source_name = $1 AND subsource = ''
        """,
        SOURCE_NAME,
    )
    assert checkpoint is not None
    assert checkpoint["watermark"] == _NOW + timedelta(minutes=50)
    assert checkpoint["carryover"][_ENDPOINT]["point_count"] == 3

    # Removing the owner mapping makes the retained evidence unlabelled. A
    # deterministic replay must retire the previously derived claims rather
    # than leave stale Work-lane episodes active forever.
    await state_set(pool, SSID_PLACE_STATE_KEY, {})
    replay_result = await run_project_owntracks_ssid(pool, None)
    assert any("SSID place mapping changed" in warning for warning in replay_result["warnings"])
    assert (
        await pool.fetchval(
            """
            SELECT count(*) FROM episodes
            WHERE source_name = $1 AND tombstone_at IS NULL
            """,
            SOURCE_NAME,
        )
        == 0
    )

    # Relabelling the same network replays the same stable source refs, revives
    # those canonical rows, and changes their lane/type without duplication.
    await state_set(pool, SSID_PLACE_STATE_KEY, {"Corp WiFi": "home"})
    await run_project_owntracks_ssid(pool, None)
    relabelled_rows = await pool.fetch(
        """
        SELECT episode_type, payload, tombstone_at
        FROM episodes
        WHERE source_name = $1
        ORDER BY start_at
        """,
        SOURCE_NAME,
    )
    assert len(relabelled_rows) == 2
    assert all(row["episode_type"] == EPISODE_TYPE_HOME_PRESENCE for row in relabelled_rows)
    assert all(row["payload"]["place"] == "home" for row in relabelled_rows)
    assert all(row["tombstone_at"] is None for row in relabelled_rows)


async def test_equal_timestamp_rows_cross_batches_without_loss_or_duplicates(
    pool: asyncpg.Pool,
) -> None:
    await state_set(pool, SSID_PLACE_STATE_KEY, {"Corp WiFi": "work"})
    boundary_ids = [UUID(int=value) for value in range(1, 6)]
    for index, point_id in enumerate(boundary_ids):
        await _insert_point(
            pool,
            0,
            "Corp WiFi",
            point_id=point_id,
            idempotency_key=f"equal-ts:{index}",
        )
    final_id = UUID(int=6)
    await _insert_point(
        pool,
        10,
        "Corp WiFi",
        point_id=final_id,
        idempotency_key="equal-ts:final",
    )

    # Simulate a pre-upgrade timestamp-only checkpoint after the first two rows
    # were already incorporated into the open span. The safe upgrade must not
    # count those boundary observations twice while it establishes a UUID
    # tie-breaker and pages through all five rows sharing the same timestamp.
    await pool.execute(
        """
        INSERT INTO projection_checkpoints (source_name, subsource, watermark, carryover)
        VALUES ($1, '', $2, $3)
        """,
        SOURCE_NAME,
        _NOW,
        {
            _ENDPOINT: {
                "ssid": "Corp WiFi",
                "start_at": _NOW.isoformat(),
                "end_at": _NOW.isoformat(),
                "point_count": 2,
            }
        },
    )

    adapter = OwnTracksSsidPresenceAdapter(
        ssid_places={"Corp WiFi": "work"},
        batch_limit=2,
    )
    first = await adapter.run(pool=pool, chronicler_pool=pool)
    second = await adapter.run(pool=pool, chronicler_pool=pool)
    third = await adapter.run(pool=pool, chronicler_pool=pool)
    repeated = await adapter.run(pool=pool, chronicler_pool=pool)

    assert [first.episodes_closed, second.episodes_closed, third.episodes_closed] == [0, 0, 1]
    assert repeated.episodes_closed == 0

    rows = await pool.fetch(
        "SELECT * FROM episodes WHERE source_name = $1 ORDER BY start_at",
        SOURCE_NAME,
    )
    assert len(rows) == 1
    assert rows[0]["start_at"] == _NOW
    assert rows[0]["end_at"] == _NOW + timedelta(minutes=10)
    assert rows[0]["payload"]["point_count"] == 6

    checkpoint = await pool.fetchrow(
        """
        SELECT watermark, carryover
        FROM projection_checkpoints
        WHERE source_name = $1 AND subsource = ''
        """,
        SOURCE_NAME,
    )
    assert checkpoint["watermark"] == _NOW + timedelta(minutes=10)
    assert checkpoint["carryover"]["_source_cursor"] == {
        "watermark": (_NOW + timedelta(minutes=10)).isoformat(),
        "uuid": str(final_id),
    }
    assert checkpoint["carryover"][_ENDPOINT]["point_count"] == 6

    # Simulate an interruption that persisted the UUID cursor but not its
    # matching relational watermark. Recovery must replay bounded pages and
    # converge through the stable source ref rather than duplicate the episode.
    interrupted_carryover = dict(checkpoint["carryover"])
    interrupted_carryover["_source_cursor"] = {
        "watermark": _NOW.isoformat(),
        "uuid": str(final_id),
    }
    await pool.execute(
        """
        UPDATE projection_checkpoints
        SET carryover = $2
        WHERE source_name = $1 AND subsource = ''
        """,
        SOURCE_NAME,
        interrupted_carryover,
    )

    recovered = [await adapter.run(pool=pool, chronicler_pool=pool) for _ in range(3)]
    assert any(
        "SSID source cursor missing or invalid" in warning for warning in recovered[0].warnings
    )
    assert [result.episodes_closed for result in recovered] == [0, 0, 1]

    recovered_rows = await pool.fetch(
        "SELECT * FROM episodes WHERE source_name = $1 ORDER BY start_at",
        SOURCE_NAME,
    )
    assert len(recovered_rows) == 1
    assert recovered_rows[0]["payload"]["point_count"] == 6
