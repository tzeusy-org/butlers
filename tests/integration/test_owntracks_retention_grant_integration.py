"""Real-Postgres proofs for the prepared retention grant (bu-s11n0s.7, slice 4a).

``prepare_retention_grant`` names a raw OwnTracks row only when every covered adapter holds an
exact coverage record for it at that adapter's current config revision and for the row's
current content digest, no adapter holds it in an open carry, its retention clock is strictly
older than the policy cutoff and its arrival has settled. The grant deletes nothing.

Coverage records are written here directly (as the adapters would), so each exclusion is
exercised alone: an uncovered adapter, an open member, a legacy-revision record, a record for
other content, and a record under another revision.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.chronicler.adapters.owntracks import OwnTracksPointAdapter
from butlers.chronicler.adapters.owntracks_place_cluster import OwnTracksPlaceClusterAdapter
from butlers.chronicler.adapters.owntracks_ssid import OwnTracksSsidPresenceAdapter
from butlers.chronicler.location_retention_grant import prepare_retention_grant
from butlers.chronicler.projection_coverage import COVERED_ADAPTERS, raw_row_digest_sql

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_T0 = datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC)
_ENDPOINT = "owntracks:alice"
_REVISIONS = {
    "owntracks.points": OwnTracksPointAdapter().coverage_revision,
    "owntracks.place_cluster": OwnTracksPlaceClusterAdapter().coverage_revision,
    "owntracks.ssid_presence": OwnTracksSsidPresenceAdapter(
        ssid_places={"home-wifi": "home"}
    ).coverage_revision,
}
_OTHER_REVISION = "v1:" + "c" * 64
_OTHER_DIGEST = "v1:" + "b" * 64


@pytest.fixture
def grant_url(fresh_db) -> str:
    """Core (raw evidence + core_340) and Chronicler at head."""
    from tests.chronicler._private_memory_setup import prepare_chronicler_current_head_sync

    url = fresh_db(
        "core",
        "chronicler",
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    prepare_chronicler_current_head_sync(url)
    return url


@pytest.fixture
async def chronicler_db(db, grant_url: str) -> asyncpg.Pool:
    return await db.open(grant_url, schema="chronicler", max_size=5)


@pytest.fixture
async def runtime_db(db, grant_url: str) -> asyncpg.Pool:
    async def _as_runtime_role(conn: asyncpg.Connection) -> None:
        await conn.execute("SET ROLE butler_chronicler_rw")

    return await db.open(grant_url, schema="chronicler", init=_as_runtime_role, max_size=2)


async def _insert_point(target: asyncpg.Pool, minutes: int) -> UUID:
    ts = _T0 + timedelta(minutes=minutes)
    return await target.fetchval(
        """
        INSERT INTO connectors.owntracks_points
            (idempotency_key, ts, lat, lon, accuracy, trigger, endpoint_identity, raw_payload,
             recorded_at)
        VALUES ($1, $2, 1.3, 103.8, 10.0, 'p', $3, '{"_type": "location"}'::jsonb, $2)
        RETURNING id
        """,
        f"owntracks:{_ENDPOINT}:{int(ts.timestamp())}:location",
        ts,
        _ENDPOINT,
    )


async def _cover(
    target: asyncpg.Pool,
    raw_id: UUID,
    *,
    skip: str | None = None,
    revision: dict[str, str] | None = None,
    digest: dict[str, str] | None = None,
) -> None:
    """Exact coverage for every adapter except ``skip``, with per-adapter overrides."""
    row = await target.fetchrow(
        f"SELECT idempotency_key, retention_at, {raw_row_digest_sql()} AS digest "
        "FROM connectors.owntracks_points WHERE id = $1",
        raw_id,
    )
    for adapter in COVERED_ADAPTERS:
        if adapter == skip:
            continue
        await target.execute(
            """
            INSERT INTO location_projection_coverage (adapter_name, raw_source_id,
                config_revision, content_digest, logical_source_key, retention_at, disposition,
                output_episode_ids, projection_batch_id)
            VALUES ($1, $2, $3, $4, $5, $6, 'projected', ARRAY[$7::uuid], $8)
            """,
            adapter,
            raw_id,
            (revision or {}).get(adapter, _REVISIONS[adapter]),
            (digest or {}).get(adapter, row["digest"]),
            row["idempotency_key"],
            row["retention_at"],
            uuid4(),
            uuid4(),
        )


async def _hold(target: asyncpg.Pool, raw_id: UUID, adapter: str) -> None:
    await target.execute(
        """
        INSERT INTO location_projection_open_members (adapter_name, carry_keys, raw_source_id,
            logical_source_key, retention_at, content_digest, joined_batch_id)
        SELECT $1, ARRAY['stay:owntracks:alice'], id, idempotency_key, retention_at,
               """
        + raw_row_digest_sql()
        + """, $3
        FROM connectors.owntracks_points WHERE id = $2
        """,
        adapter,
        raw_id,
        uuid4(),
    )


async def _granted(target: asyncpg.Pool) -> set[UUID]:
    rows = await target.fetch("SELECT raw_source_id FROM location_retention_grant_items")
    return {row["raw_source_id"] for row in rows}


async def test_only_rows_covered_exactly_at_the_current_revisions_are_granted(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    full = await _insert_point(chronicler_db, 0)
    await _cover(chronicler_db, full)

    uncovered = await _insert_point(chronicler_db, 5)
    await _cover(chronicler_db, uncovered, skip="owntracks.ssid_presence")

    held = await _insert_point(chronicler_db, 10)
    await _cover(chronicler_db, held)
    await _hold(chronicler_db, held, "owntracks.place_cluster")

    legacy = await _insert_point(chronicler_db, 15)
    await _cover(
        chronicler_db,
        legacy,
        revision={"owntracks.points": "legacy"},
        digest={"owntracks.points": "legacy"},
    )

    other_content = await _insert_point(chronicler_db, 20)
    await _cover(chronicler_db, other_content, digest={"owntracks.place_cluster": _OTHER_DIGEST})

    stale_revision = await _insert_point(chronicler_db, 25)
    await _cover(chronicler_db, stale_revision, revision={"owntracks.points": _OTHER_REVISION})

    # The runtime role prepares: its privileges are part of the proof.
    async with runtime_db.acquire() as conn:
        grant = await prepare_retention_grant(conn, config_revisions=_REVISIONS)

    assert await _granted(chronicler_db) == {full}
    assert (grant.item_count, grant.capped) == (1, False)
    header = await chronicler_db.fetchrow(
        "SELECT * FROM location_retention_grants WHERE id = $1", grant.id
    )
    policy = await chronicler_db.fetchrow("SELECT days, version FROM location_retention_policy")
    assert (header["item_count"], header["policy_version"], header["retention_days"]) == (
        1,
        policy["version"],
        policy["days"],
    )
    assert header["config_revisions"] == _REVISIONS
    item = await chronicler_db.fetchrow("SELECT * FROM location_retention_grant_items")
    assert item["grant_id"] == grant.id
    assert item["content_digest"] == await chronicler_db.fetchval(
        f"SELECT {raw_row_digest_sql()} FROM connectors.owntracks_points WHERE id = $1", full
    )


async def test_a_granted_row_is_never_granted_twice_and_every_run_leaves_a_receipt(
    chronicler_db: asyncpg.Pool,
) -> None:
    first = await _insert_point(chronicler_db, 0)
    await _cover(chronicler_db, first)
    async with chronicler_db.acquire() as conn:
        one = await prepare_retention_grant(conn, config_revisions=_REVISIONS)
        again = await prepare_retention_grant(conn, config_revisions=_REVISIONS)
    assert (one.item_count, again.item_count) == (1, 0)
    assert await chronicler_db.fetchval("SELECT count(*) FROM location_retention_grants") == 2


async def test_a_limited_run_is_capped_and_takes_the_oldest_rows(
    chronicler_db: asyncpg.Pool,
) -> None:
    rows = [await _insert_point(chronicler_db, m) for m in (10, 0, 5)]
    for raw_id in rows:
        await _cover(chronicler_db, raw_id)
    async with chronicler_db.acquire() as conn:
        grant = await prepare_retention_grant(conn, config_revisions=_REVISIONS, limit=2)
    assert (grant.item_count, grant.capped) == (2, True)
    assert await _granted(chronicler_db) == {rows[1], rows[2]}


async def test_rows_inside_the_horizon_are_not_granted(chronicler_db: asyncpg.Pool) -> None:
    recent = await chronicler_db.fetchval(
        """
        INSERT INTO connectors.owntracks_points
            (idempotency_key, ts, lat, lon, accuracy, trigger, endpoint_identity, raw_payload,
             recorded_at)
        VALUES ('owntracks:alice:recent:location', now() - interval '2 days', 1.3, 103.8, 10.0,
                'p', 'owntracks:alice', '{}'::jsonb, now() - interval '2 days')
        RETURNING id
        """
    )
    await _cover(chronicler_db, recent)
    async with chronicler_db.acquire() as conn:
        grant = await prepare_retention_grant(conn, config_revisions=_REVISIONS)
    assert grant.item_count == 0
    assert await _granted(chronicler_db) == set()
