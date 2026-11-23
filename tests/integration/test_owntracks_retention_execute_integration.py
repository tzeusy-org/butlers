"""The connector executes one Chronicler retention grant in one transaction (S4b-3b).

``execute_grant`` deletes a granted raw row only when it is still exactly the row Chronicler
granted (same logical key, same v1 content digest, retention clock before the cutoff). Any
mismatch refuses the whole grant: nothing is deleted and no receipt is written. A row already
gone counts as already forgotten and is still tombstoned by its logical key, so it cannot be
inserted again. A grant executed twice returns its first receipt and changes nothing.

The connector's parked ``connectors.filtered_events`` copies of a granted fix (an envelope
parked after its ingest attempts were spent; the point itself is still persisted) are withheld
in the same transaction (S4c-4): matched by ``sender_identity`` and ``external_message_id``
``{tst}:location``, never by text. A withhold that fails rolls the whole grant back. A
policy-filtered fix has no point row, so it is outside a grant's reach; the connector stores
it without coordinates instead (bu-s11n0s.73).
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from butlers.chronicler.projection_coverage import raw_row_digest_sql
from butlers.connectors.owntracks_retention import (
    NOT_REPLAYABLE,
    WITHHELD_TEXT,
    GrantMismatch,
    execute_grant,
    parse_grant,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_RECEIVED = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_CUTOFF = _RECEIVED + timedelta(days=1)
_INSERT = """
    INSERT INTO connectors.owntracks_points
        (idempotency_key, ts, lat, lon, endpoint_identity, recorded_at)
    VALUES ($1, $2, 1.3, 103.8, 'owntracks:alice', $2)
    ON CONFLICT DO NOTHING
    RETURNING id
"""
_DIGEST = f"SELECT {raw_row_digest_sql('p')} FROM connectors.owntracks_points p WHERE p.id = $1"


@pytest.fixture
def head_url(fresh_db) -> str:
    return fresh_db("core")


async def _point(pool, key: str, ts: datetime = _RECEIVED) -> dict:
    raw_id = await pool.fetchval(_INSERT, key, ts)
    digest = await pool.fetchval(_DIGEST, raw_id)
    return {"raw_source_id": str(raw_id), "logical_source_key": key, "content_digest": digest}


def _grant(*items: dict, cutoff: datetime = _CUTOFF):
    return parse_grant(
        {"grant_id": str(uuid4()), "cutoff": cutoff.isoformat(), "items": list(items)}
    )


_COPY = """
    INSERT INTO connectors.filtered_events (
        received_at, connector_type, endpoint_identity, external_message_id, source_channel,
        sender_identity, subject_or_preview, filter_reason, status, full_payload
    ) VALUES (now(), $1, $2, $3, $1, $2, 'OwnTracks location event', $4, $5, $6::jsonb)
    RETURNING id
"""


def _envelope(sender: str, external_id: str) -> dict:
    return {
        "source": {"channel": "owntracks", "provider": "owntracks", "endpoint_identity": sender},
        "event": {"external_event_id": external_id, "observed_at": _RECEIVED.isoformat()},
        "sender": {"identity": "owntracks:ph"},
        "payload": {
            "raw": {"_type": "location", "lat": 1.3, "lon": 103.8},
            "normalized_text": "Location update: 1.3, 103.8",
        },
        "control": {"idempotency_key": f"owntracks:{sender}:{external_id}"},
    }


async def _copy(
    pool,
    sender: str,
    external_id: str,
    *,
    channel: str = "owntracks",
) -> str:
    """One parked connector copy of a fix (``error``, attempts spent)."""
    await pool.execute("SELECT connectors.connectors_filtered_events_ensure_partition(now())")
    envelope = _envelope(sender, external_id)
    return str(
        await pool.fetchval(
            _COPY, channel, sender, external_id, "retry_exhausted", "error", envelope
        )
    )


async def _copies(pool) -> dict[str, dict]:
    rows = await pool.fetch("SELECT id::text AS id, full_payload FROM connectors.filtered_events")
    return {row["id"]: row["full_payload"] for row in rows}


def _withheld(sender: str, external_id: str) -> dict:
    return {
        **_envelope(sender, external_id),
        "payload": {"raw": None, "normalized_text": WITHHELD_TEXT},
        "replay_unavailable": NOT_REPLAYABLE,
    }


async def _keys(pool) -> list[str]:
    rows = await pool.fetch(
        "SELECT idempotency_key FROM connectors.owntracks_points ORDER BY idempotency_key"
    )
    return [row["idempotency_key"] for row in rows]


async def test_a_grant_deletes_its_rows_and_leaves_tombstones_and_a_receipt(db, head_url) -> None:
    pool = await db.open(head_url)
    first, second = await _point(pool, "k1"), await _point(pool, "k2")
    await _point(pool, "kept")
    grant = _grant(first, second)
    async with pool.acquire() as conn:
        receipt = await execute_grant(conn, grant)
    assert (receipt.item_count, receipt.deleted_count, receipt.already_forgotten_count) == (2, 2, 0)
    assert not receipt.replayed
    assert await _keys(pool) == ["kept"]
    tombstones = await pool.fetch(
        "SELECT logical_source_key, raw_source_id::text AS raw, content_digest, grant_id "
        "FROM connectors.owntracks_retention_tombstones ORDER BY logical_source_key"
    )
    assert [(t["logical_source_key"], t["raw"], t["content_digest"]) for t in tombstones] == [
        ("k1", first["raw_source_id"], first["content_digest"]),
        ("k2", second["raw_source_id"], second["content_digest"]),
    ]
    assert {t["grant_id"] for t in tombstones} == {grant.grant_id}
    # The webhook replays the forgotten fix: skipped.
    assert await pool.fetchval(_INSERT, "k1", _RECEIVED) is None


async def test_a_replay_racing_the_deleter_does_not_restore_the_row(db, head_url) -> None:
    """The deleter's uncommitted delete and tombstone hold the key: the replay waits, then skips."""
    pool = await db.open(head_url, max_size=3)
    item = await _point(pool, "k1")
    deleter = await pool.acquire()
    try:
        outer = deleter.transaction()
        await outer.start()
        await execute_grant(deleter, _grant(item))
        replay = asyncio.create_task(pool.fetchval(_INSERT, "k1", _RECEIVED))
        await asyncio.sleep(0.5)
        assert not replay.done()
        await outer.commit()
        assert await asyncio.wait_for(replay, timeout=10) is None
    finally:
        await pool.release(deleter)
    assert await _keys(pool) == []


async def test_a_row_already_gone_is_counted_and_still_tombstoned(db, head_url) -> None:
    pool = await db.open(head_url)
    gone = await _point(pool, "gone")
    await pool.execute(
        "DELETE FROM connectors.owntracks_points WHERE id = $1::uuid", gone["raw_source_id"]
    )
    async with pool.acquire() as conn:
        receipt = await execute_grant(conn, _grant(gone))
    assert (receipt.deleted_count, receipt.already_forgotten_count) == (0, 1)
    assert await pool.fetchval(_INSERT, "gone", _RECEIVED) is None


async def test_a_grant_executed_twice_returns_its_first_receipt(db, head_url) -> None:
    pool = await db.open(head_url)
    grant = _grant(await _point(pool, "k1"))
    async with pool.acquire() as conn:
        first = await execute_grant(conn, grant)
        again = await execute_grant(conn, grant)
    assert again.replayed
    assert (again.deleted_count, again.already_forgotten_count) == (first.deleted_count, 0)
    assert await pool.fetchval("SELECT count(*) FROM connectors.owntracks_retention_batches") == 1


async def test_a_concurrent_run_of_the_same_grant_returns_its_receipt(db, head_url) -> None:
    """The lock comes before the receipt read: the second run waits, then replays."""
    pool = await db.open(head_url, max_size=3)
    grant = _grant(await _point(pool, "k1"))
    first = await pool.acquire()
    try:
        outer = first.transaction()
        await outer.start()
        await execute_grant(first, grant)

        async def second_run():
            async with pool.acquire() as conn:
                return await execute_grant(conn, grant)

        second = asyncio.create_task(second_run())
        await asyncio.sleep(0.5)
        assert not second.done()
        await outer.commit()
        assert (await asyncio.wait_for(second, timeout=10)).replayed
    finally:
        await pool.release(first)


@pytest.mark.parametrize(
    "tamper",
    ["digest", "key", "after-cutoff", "at-cutoff"],
)
async def test_a_mismatched_row_refuses_the_whole_grant(db, head_url, tamper) -> None:
    pool = await db.open(head_url)
    good = await _point(pool, "good")
    ts = {"after-cutoff": _CUTOFF + timedelta(hours=1), "at-cutoff": _CUTOFF}.get(tamper, _RECEIVED)
    bad = await _point(pool, "bad", ts)
    if tamper == "digest":
        bad["content_digest"] = "v1:" + "0" * 64
    elif tamper == "key":
        bad["logical_source_key"] = "other"
    async with pool.acquire() as conn:
        with pytest.raises(GrantMismatch):
            await execute_grant(conn, _grant(good, bad))
    assert await _keys(pool) == ["bad", "good"]
    for table in ("owntracks_retention_batches", "owntracks_retention_tombstones"):
        assert await pool.fetchval(f"SELECT count(*) FROM connectors.{table}") == 0


async def test_the_connector_role_can_execute_a_grant(db, head_url) -> None:
    pool = await db.open(head_url)
    grant = _grant(await _point(pool, _FIX))
    copy = await _copy(pool, "alice", "1:location")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE connector_writer")
            receipt = await execute_grant(conn, grant)
    assert receipt.deleted_count == 1
    assert await _keys(pool) == []
    assert (await _copies(pool))[copy] == _withheld("alice", "1:location")


async def test_a_tombstoned_key_under_another_grant_refuses(db, head_url) -> None:
    """A raw row is granted at most once; a second grant naming it is not executed."""
    pool = await db.open(head_url)
    item = await _point(pool, "k1")
    async with pool.acquire() as conn:
        await execute_grant(conn, _grant(item))
        with pytest.raises(GrantMismatch):
            await execute_grant(conn, _grant(item))
    assert await pool.fetchval("SELECT count(*) FROM connectors.owntracks_retention_batches") == 1


# ── The connector's filtered_events copies (S4c-4) ────────────────────────

#: A granted fix's logical key: ``owntracks:{endpoint_identity}:{tst}:location``.
_FIX = "owntracks:alice:1:location"


async def test_a_grant_withholds_the_connector_copies_of_its_fixes(db, head_url) -> None:
    """Every parked copy of a granted fix is withheld; nothing else is."""
    pool = await db.open(head_url)
    grant = _grant(await _point(pool, _FIX))
    await _point(pool, "owntracks:alice:2:location")
    parked = await _copy(pool, "alice", "1:location")
    # The same fix parked again after a later resend also spent its attempts.
    reparked = await _copy(pool, "alice", "1:location")
    kept = {
        # A fix not in the grant.
        await _copy(pool, "alice", "2:location"),
        # The same fix time from another device.
        await _copy(pool, "bob", "1:location"),
        # A transition and a waypoints event at the granted fix's time.
        await _copy(pool, "alice", "1:transition:enter"),
        await _copy(pool, "alice", "1:waypoints"),
        # Another connector whose identifiers happen to spell the same key.
        await _copy(pool, "alice", "1:location", channel="telegram"),
    }
    before = await _copies(pool)
    async with pool.acquire() as conn:
        await execute_grant(conn, grant)
    after = await _copies(pool)
    assert after[parked] == after[reparked] == _withheld("alice", "1:location")
    assert {copy: after[copy] for copy in kept} == {copy: before[copy] for copy in kept}
    # Rows are kept with their status, so the park audit still counts them.
    statuses = await pool.fetch(
        "SELECT id::text AS id, status FROM connectors.filtered_events WHERE id = ANY($1::uuid[])",
        [parked, reparked],
    )
    assert [row["status"] for row in statuses] == ["error", "error"]


async def test_a_failed_withhold_rolls_back_the_delete(db, head_url) -> None:
    """The withhold is in the grant's transaction: if it fails, nothing is forgotten."""
    pool = await db.open(head_url)
    item = await _point(pool, _FIX)
    copy = await _copy(pool, "alice", "1:location")
    before = await _copies(pool)
    await pool.execute(
        """
        CREATE FUNCTION public.refuse_update() RETURNS trigger LANGUAGE plpgsql AS $fn$
        BEGIN RAISE EXCEPTION 'withhold refused'; END;
        $fn$
        """
    )
    await pool.execute(
        "CREATE TRIGGER refuse_update BEFORE UPDATE ON connectors.filtered_events "
        "FOR EACH ROW EXECUTE FUNCTION public.refuse_update()"
    )
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.RaiseError, match="withhold refused"):
            await execute_grant(conn, _grant(item))
    assert await _keys(pool) == [_FIX]
    for table in ("owntracks_retention_batches", "owntracks_retention_tombstones"):
        assert await pool.fetchval(f"SELECT count(*) FROM connectors.{table}") == 0
    assert (await _copies(pool))[copy] == before[copy]


async def test_a_replayed_grant_leaves_its_withheld_copies_alone(db, head_url) -> None:
    """A second run returns the first receipt and rewrites no copy."""
    pool = await db.open(head_url)
    grant = _grant(await _point(pool, _FIX))
    copy = await _copy(pool, "alice", "1:location")
    xmin = "SELECT xmin::text FROM connectors.filtered_events WHERE id = $1::uuid"
    async with pool.acquire() as conn:
        await execute_grant(conn, grant)
        withheld_at = await pool.fetchval(xmin, copy)
        again = await execute_grant(conn, grant)
    assert again.replayed
    assert await pool.fetchval(xmin, copy) == withheld_at
    assert (await _copies(pool))[copy] == _withheld("alice", "1:location")
