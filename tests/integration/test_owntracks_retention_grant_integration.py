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

import json
import shutil
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.chronicler.adapters.owntracks import OwnTracksPointAdapter
from butlers.chronicler.adapters.owntracks_place_cluster import OwnTracksPlaceClusterAdapter
from butlers.chronicler.adapters.owntracks_ssid import OwnTracksSsidPresenceAdapter
from butlers.chronicler.location_retention_grant import StaleRevisions, prepare_retention_grant
from butlers.chronicler.projection_coverage import COVERED_ADAPTERS, raw_row_digest_sql
from butlers.chronicler.spatial_precision import coarsen

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


_RECORD_RUN = """
INSERT INTO location_projection_attempts (adapter_name, config_revision, last_outcome,
    last_attempt_at, last_success_at)
VALUES ($1, $2, 'succeeded', now(), now())
ON CONFLICT (adapter_name) DO UPDATE SET config_revision = EXCLUDED.config_revision
"""


async def _record_runs(target: asyncpg.Pool, revisions: dict[str, str]) -> None:
    """Each adapter's last recorded run, as its own ``record_attempt`` leaves it."""
    for adapter, revision in revisions.items():
        await target.execute(_RECORD_RUN, adapter, revision)


@pytest.fixture
async def chronicler_db(db, grant_url: str) -> asyncpg.Pool:
    pool = await db.open(grant_url, schema="chronicler", max_size=5)
    await _record_runs(pool, _REVISIONS)
    return pool


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
    episodes: dict[str, UUID] | None = None,
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
            (episodes or {}).get(adapter, uuid4()),
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


async def test_revisions_the_adapters_did_not_last_run_with_grant_nothing(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    """S4b (1): a stale caller revision is refused inside the grant transaction."""
    covered = await _insert_point(chronicler_db, 0)
    await _cover(chronicler_db, covered, revision={"owntracks.points": _OTHER_REVISION})
    # The caller asserts the old revision, but points last ran at the current one.
    stale = {**_REVISIONS, "owntracks.points": _OTHER_REVISION}
    async with runtime_db.acquire() as conn:
        with pytest.raises(StaleRevisions):
            await prepare_retention_grant(conn, config_revisions=stale)
    assert await _granted(chronicler_db) == set()
    assert await chronicler_db.fetchval("SELECT count(*) FROM location_retention_grants") == 0


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


# ── S4b-2: preparation forgets Chronicler's own copies ────────────────────────

_EXACT = (1.2345678, 103.8765432)


async def _sources(target: asyncpg.Pool) -> None:
    for source in COVERED_ADAPTERS:
        await target.execute(
            "INSERT INTO source_adapter_state (source_name, chronicler_compatibility) "
            "VALUES ($1, 'supported') ON CONFLICT DO NOTHING",
            source,
        )


async def _point_event(target: asyncpg.Pool, raw_id: UUID) -> UUID:
    return await target.fetchval(
        """
        INSERT INTO point_events (source_name, source_ref, event_type, occurred_at, payload)
        SELECT 'owntracks.points', 'connectors.owntracks_points:' || idempotency_key,
               'location', ts, jsonb_build_object('lat', $2::float8, 'lon', $3::float8)
        FROM connectors.owntracks_points WHERE id = $1
        RETURNING id
        """,
        raw_id,
        *_EXACT,
    )


async def _episode(target: asyncpg.Pool, source: str, payload: dict, *, event: UUID | None) -> UUID:
    episode = await target.fetchval(
        """
        INSERT INTO episodes (source_name, source_ref, episode_type, start_at, end_at, payload)
        VALUES ($1, $2, 'location_summary', $3, $3, ($4::text)::jsonb)
        RETURNING id
        """,
        source,
        f"test:{uuid4()}",
        _T0,
        json.dumps(payload),
    )
    if event is not None:
        await target.execute(
            "INSERT INTO episode_event_links (episode_id, event_id) VALUES ($1, $2)",
            episode,
            event,
        )
    return episode


async def _payload(target: asyncpg.Pool, episode: UUID) -> dict:
    raw = await target.fetchval("SELECT payload FROM episodes WHERE id = $1", episode)
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


async def test_preparation_forgets_point_events_and_coarsens_every_summary_and_anchor(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    await _sources(chronicler_db)
    granted = await _insert_point(chronicler_db, 0)
    kept = await _insert_point(chronicler_db, 5)  # uncovered: not granted
    granted_event = await _point_event(chronicler_db, granted)
    kept_event = await _point_event(chronicler_db, kept)
    movement = await _episode(
        chronicler_db,
        "owntracks.points",
        {"start_lat": _EXACT[0], "start_lon": _EXACT[1], "end_lat": 1.31, "end_lon": 103.81},
        event=granted_event,
    )
    place = await _episode(
        chronicler_db,
        "owntracks.place_cluster",
        {"centroid_lat": _EXACT[0], "centroid_lon": _EXACT[1], "label": "Home"},
        event=None,
    )
    untouched = await _episode(
        chronicler_db,
        "owntracks.points",
        {"start_lat": _EXACT[0], "start_lon": _EXACT[1]},
        event=kept_event,
    )
    await _cover(
        chronicler_db,
        granted,
        episodes={"owntracks.points": movement, "owntracks.place_cluster": place},
    )
    carryover = {
        "owntracks:alice": {"start_lat": 1.0, "start_lon": 2.0},
        "_trail_anchors": {"owntracks:alice": {"ts": _T0.isoformat(), "lat": 1.3, "lon": 103.8}},
        "_trail_state": {
            "version": 1,
            "endpoints": {
                "owntracks:alice": {"seed_id": None, "pending_ids": [], "anchor_id": str(granted)}
            },
        },
    }
    await chronicler_db.execute(
        "INSERT INTO projection_checkpoints (source_name, subsource, carryover) "
        "VALUES ('owntracks.points', '', ($1::text)::jsonb) "
        "ON CONFLICT (source_name, subsource) DO UPDATE SET carryover = EXCLUDED.carryover",
        json.dumps(carryover),
    )

    async with runtime_db.acquire() as conn:
        grant = await prepare_retention_grant(conn, config_revisions=_REVISIONS)

    assert (grant.item_count, grant.point_events_forgotten) == (1, 1)
    assert (grant.episodes_coarsened, grant.anchors_coarsened) == (2, 1)
    events = await chronicler_db.fetch("SELECT id FROM point_events")
    assert {row["id"] for row in events} == {kept_event}
    tombstone = await chronicler_db.fetchrow("SELECT * FROM location_evidence_tombstones")
    assert tombstone["point_event_id"] == granted_event
    assert list(tombstone["episode_ids"]) == [movement]
    receipt = await chronicler_db.fetchrow("SELECT * FROM location_forgetting_receipts")
    assert (receipt["point_events_forgotten"], receipt["episodes_coarsened"]) == (1, 2)
    assert receipt["cutoff"] == grant.cutoff

    cell = coarsen(*_EXACT)
    moved = await _payload(chronicler_db, movement)
    assert (moved["start_lat"], moved["start_lon"]) == (cell.lat, cell.lon)
    assert (moved["end_lat"], moved["end_lon"]) == (
        coarsen(1.31, 103.81).lat,
        coarsen(1.31, 103.81).lon,
    )
    assert moved["spatial_scheme"] == "eqcell-150m-v1"
    stayed = await _payload(chronicler_db, place)
    assert (stayed["centroid_lat"], stayed["centroid_lon"], stayed["label"]) == (
        cell.lat,
        cell.lon,
        "Home",
    )
    assert await _payload(chronicler_db, untouched) == {
        "start_lat": _EXACT[0],
        "start_lon": _EXACT[1],
    }

    raw = await chronicler_db.fetchval(
        "SELECT carryover FROM projection_checkpoints "
        "WHERE source_name = 'owntracks.points' AND subsource = ''"
    )
    after = json.loads(raw) if isinstance(raw, str) else dict(raw)
    anchor = coarsen(1.3, 103.8)
    assert after["_trail_anchors"]["owntracks:alice"] == {
        "ts": _T0.isoformat(),
        "lat": anchor.lat,
        "lon": anchor.lon,
    }
    assert after["owntracks:alice"] == carryover["owntracks:alice"]
    assert after["_trail_state"] == carryover["_trail_state"]


async def _cache(
    target: asyncpg.Pool,
    key: str,
    refs: object,
    *,
    hours: tuple[int, int] = (-1, 1),
    superseded: bool = False,
) -> None:
    await target.execute(
        "INSERT INTO tier2_cache (cache_key, start_at, end_at, prose, provenance_refs, "
        "superseded_at) VALUES ($1, $2, $3, 'You walked from home.', ($4::text)::jsonb, "
        "CASE WHEN $5 THEN now() END)",
        key,
        _T0 + timedelta(hours=hours[0]),
        _T0 + timedelta(hours=hours[1]),
        json.dumps(refs),
        superseded,
    )


async def _cache_keys(target: asyncpg.Pool) -> set[str]:
    return {row["cache_key"] for row in await target.fetch("SELECT cache_key FROM tier2_cache")}


async def test_preparation_deletes_every_cached_prose_that_cites_a_granted_row(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    # Prose cannot be coarsened: a cache row citing a forgotten fix would keep it.
    await _sources(chronicler_db)
    granted = await _insert_point(chronicler_db, 0)
    event = await _point_event(chronicler_db, granted)
    movement = await _episode(
        chronicler_db,
        "owntracks.points",
        {"start_lat": _EXACT[0], "start_lon": _EXACT[1]},
        event=event,
    )
    await _cover(chronicler_db, granted, episodes={"owntracks.points": movement})
    point_ref = await chronicler_db.fetchval(
        "SELECT source_ref FROM point_events WHERE id = $1", event
    )
    movement_ref = await chronicler_db.fetchval(
        "SELECT source_ref FROM episodes WHERE id = $1", movement
    )
    await _cache(chronicler_db, "cites-point", ["calendar:x", point_ref], hours=(24, 25))
    await _cache(chronicler_db, "cites-movement", [movement_ref], hours=(24, 25))
    await _cache(
        chronicler_db, "superseded-cites-point", [point_ref], hours=(24, 25), superseded=True
    )
    await _cache(chronicler_db, "half-readable-cites-point", [point_ref, 7], hours=(24, 25))
    await _cache(chronicler_db, "uncited-day-of-the-fix", [])
    await _cache(chronicler_db, "cites-others-that-day", ["calendar:x"])
    await _cache(chronicler_db, "uncited-other-day", [], hours=(24, 25))

    async with runtime_db.acquire() as conn:
        grant = await prepare_retention_grant(conn, config_revisions=_REVISIONS)

    assert grant.item_count == 1
    assert grant.cache_rows_invalidated == 5
    assert await _cache_keys(chronicler_db) == {"cites-others-that-day", "uncited-other-day"}


@pytest.mark.parametrize(
    "refs", [{"refs": ["x"]}, ["calendar:x", 7], "connectors.owntracks_points:x"], ids=str
)
async def test_a_cache_row_whose_provenance_cannot_be_read_rolls_the_preparation_back(
    chronicler_db: asyncpg.Pool, refs: object
) -> None:
    await _sources(chronicler_db)
    granted = await _insert_point(chronicler_db, 0)
    await _cover(chronicler_db, granted)
    await _cache(chronicler_db, "unreadable", refs)

    async with chronicler_db.acquire() as conn:
        with pytest.raises(ValueError, match="provenance"):
            await prepare_retention_grant(conn, config_revisions=_REVISIONS)

    assert await chronicler_db.fetchval("SELECT count(*) FROM location_retention_grants") == 0
    assert await _cache_keys(chronicler_db) == {"unreadable"}


async def test_an_unreadable_cache_row_on_another_day_does_not_block_preparation(
    chronicler_db: asyncpg.Pool,
) -> None:
    await _sources(chronicler_db)
    granted = await _insert_point(chronicler_db, 0)
    await _cover(chronicler_db, granted)
    await _cache(chronicler_db, "unreadable-elsewhere", {"refs": []}, hours=(24, 25))

    async with chronicler_db.acquire() as conn:
        grant = await prepare_retention_grant(conn, config_revisions=_REVISIONS)

    assert (grant.item_count, grant.cache_rows_invalidated) == (1, 0)
    assert await _cache_keys(chronicler_db) == {"unreadable-elsewhere"}


async def test_a_summary_it_cannot_coarsen_rolls_the_whole_preparation_back(
    chronicler_db: asyncpg.Pool,
) -> None:
    await _sources(chronicler_db)
    granted = await _insert_point(chronicler_db, 0)
    event = await _point_event(chronicler_db, granted)
    odd = await _episode(
        chronicler_db,
        "owntracks.points",
        {"start_lat": _EXACT[0], "start_lon": _EXACT[1], "home_lat": 1.0, "home_lon": 2.0},
        event=event,
    )
    await _cover(chronicler_db, granted, episodes={"owntracks.points": odd})
    async with chronicler_db.acquire() as conn:
        with pytest.raises(ValueError, match="does not know"):
            await prepare_retention_grant(conn, config_revisions=_REVISIONS)
    assert await chronicler_db.fetchval("SELECT count(*) FROM location_retention_grants") == 0
    assert await chronicler_db.fetchval("SELECT count(*) FROM location_evidence_tombstones") == 0
    assert await chronicler_db.fetchval("SELECT count(*) FROM point_events") == 1
    assert (await _payload(chronicler_db, odd))["start_lat"] == _EXACT[0]
