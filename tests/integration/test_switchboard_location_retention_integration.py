"""Switchboard forgets its copies of expired OwnTracks fixes (bu-s11n0s.7, slice S4c-2).

``forget_location_copies`` withholds the location in every ``message_inbox`` copy of a granted
OwnTracks fix that Switchboard handled by policy alone (``skipped``, no session), tombstones the
key so a later envelope for it is withheld on insert, and records a count-only receipt. A copy
that is still processing or was routed onward is left as it is and makes the receipt
``blocked``. Rows on other channels are never touched.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.tools.switchboard.location_retention import (
    WITHHELD_TEXT,
    forget_location_copies,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_TEXT = "Location update: 1.3N, 103.8E, acc 10m"
_SKIPPED = {"request_id": "r", "policy_bypass": True, "triage_decision": "skip"}


@pytest.fixture
async def switchboard_pool(db):
    pool = await db(
        "core", "switchboard", schemas={"switchboard": "switchboard"}, schema="switchboard"
    )
    await pool.execute("SELECT switchboard_message_inbox_ensure_partition(now())")
    return pool


def _key(n: int) -> str:
    return f"owntracks:owntracks:alice:{1774519200 + n}:location"


async def _inbox(
    pool: asyncpg.Pool,
    key: str,
    *,
    channel: str = "owntracks",
    lifecycle: str = "skipped",
    session: UUID | None = None,
    decomposition: dict | None = _SKIPPED,
    copy: int = 0,
) -> UUID:
    row_id = uuid4()
    await pool.execute(
        """
        INSERT INTO switchboard.message_inbox (id, received_at, request_context, raw_payload,
            normalized_text, lifecycle_state, session_id, decomposition_output, ingestion_tier)
        VALUES ($1, now(), $2::jsonb, $3::jsonb, $4, $5, $6, $7::jsonb, 'metadata')
        """,
        row_id,
        {"source_channel": channel, "idempotency_key": key, "dedupe_key": f"idem:{key}:{copy}"},
        {
            "source": {"channel": channel},
            "payload": {"raw": None, "normalized_text": _TEXT},
            "control": {"ingestion_tier": "metadata"},
        },
        _TEXT,
        lifecycle,
        session,
        decomposition,
    )
    return row_id


async def _row(pool: asyncpg.Pool, row_id: UUID) -> asyncpg.Record:
    return await pool.fetchrow(
        "SELECT normalized_text, raw_payload, request_context, lifecycle_state "
        "FROM switchboard.message_inbox WHERE id = $1",
        row_id,
    )


def _json(value) -> dict:
    return json.loads(value) if isinstance(value, str) else dict(value)


def _withheld(row: asyncpg.Record) -> bool:
    payload = _json(row["raw_payload"])["payload"]
    return row["normalized_text"] == WITHHELD_TEXT and payload == {
        "raw": None,
        "normalized_text": WITHHELD_TEXT,
    }


async def _receipts(pool: asyncpg.Pool, grant: UUID) -> list[str]:
    rows = await pool.fetch(
        "SELECT outcome FROM switchboard.location_retention_forgetting "
        "WHERE grant_id = $1 ORDER BY id",
        grant,
    )
    return [row["outcome"] for row in rows]


async def test_a_skipped_copy_is_withheld_and_its_key_tombstoned(switchboard_pool) -> None:
    pool = switchboard_pool
    copy = await _inbox(pool, _key(1))
    grant = uuid4()
    async with pool.acquire() as conn:
        receipt = await forget_location_copies(conn, grant, [_key(1), _key(2)])

    assert receipt.outcome == "complete"
    assert (receipt.item_count, receipt.forgotten_count, receipt.absent_count) == (2, 1, 1)
    assert (receipt.already_forgotten_count, receipt.blocked_count) == (0, 0)
    row = await _row(pool, copy)
    assert _withheld(row)
    # The lifecycle audit and dedupe identity stay.
    assert row["lifecycle_state"] == "skipped"
    assert _json(row["request_context"])["dedupe_key"] == f"idem:{_key(1)}:0"
    keys = await pool.fetch(
        "SELECT logical_source_key, grant_id FROM switchboard.location_retention_forgotten_keys "
        "ORDER BY logical_source_key"
    )
    assert [(k["logical_source_key"], k["grant_id"]) for k in keys] == [
        (_key(1), grant),
        (_key(2), grant),
    ]


@pytest.mark.parametrize(
    ("lifecycle", "session", "decomposition"),
    [
        ("completed", uuid4(), {"routed": ["general"]}),  # routed onward
        ("accepted", None, None),  # still to be processed
        ("metadata_ref", None, None),  # never reached the pipeline's verdict
        ("skipped", uuid4(), _SKIPPED),  # a spot-checked skip that reached a session
        ("skipped", None, {"triage_decision": "skip"}),  # not a policy bypass
        ("skipped", None, {"policy_bypass": True, "triage_decision": "route_to"}),  # not a skip
        ("accepted", None, _SKIPPED),  # a skip verdict on a row not yet finished
    ],
)
async def test_a_copy_not_handled_by_policy_alone_blocks_and_stays(
    switchboard_pool, lifecycle, session, decomposition
) -> None:
    pool = switchboard_pool
    held = await _inbox(
        pool, _key(1), lifecycle=lifecycle, session=session, decomposition=decomposition
    )
    safe = await _inbox(pool, _key(2))
    grant = uuid4()
    async with pool.acquire() as conn:
        receipt = await forget_location_copies(conn, grant, [_key(1), _key(2)])

    assert (receipt.outcome, receipt.blocked_count, receipt.forgotten_count) == ("blocked", 1, 1)
    assert (await _row(pool, held))["normalized_text"] == _TEXT
    assert _withheld(await _row(pool, safe))
    forgotten = await pool.fetch(
        "SELECT logical_source_key FROM switchboard.location_retention_forgotten_keys"
    )
    assert [k["logical_source_key"] for k in forgotten] == [_key(2)]

    async with pool.acquire() as conn:
        again = await forget_location_copies(conn, grant, [_key(1), _key(2)])
    assert (again.outcome, again.blocked_count, again.already_forgotten_count) == ("blocked", 1, 1)
    assert await _receipts(pool, grant) == ["blocked", "blocked"]


async def test_a_blocked_key_keeps_every_copy(switchboard_pool) -> None:
    """A key is forgotten whole or not at all: its policy-only copy waits with the held one."""
    pool = switchboard_pool
    safe = await _inbox(pool, _key(1))
    held = await _inbox(pool, _key(1), lifecycle="accepted", decomposition=None, copy=1)
    async with pool.acquire() as conn:
        receipt = await forget_location_copies(conn, uuid4(), [_key(1)])
    assert (receipt.blocked_count, receipt.forgotten_count) == (1, 0)
    assert (await _row(pool, safe))["normalized_text"] == _TEXT
    assert (await _row(pool, held))["normalized_text"] == _TEXT


async def test_a_complete_grant_replays_its_receipt(switchboard_pool) -> None:
    pool = switchboard_pool
    await _inbox(pool, _key(1))
    grant = uuid4()
    async with pool.acquire() as conn:
        first = await forget_location_copies(conn, grant, [_key(1)])
        again = await forget_location_copies(conn, grant, [_key(1)])
    assert again.replayed and not first.replayed
    assert again.forgotten_count == first.forgotten_count == 1
    assert await _receipts(pool, grant) == ["complete"]


async def test_other_channels_are_never_touched(switchboard_pool) -> None:
    pool = switchboard_pool
    other = await _inbox(pool, _key(1), channel="telegram_bot")
    async with pool.acquire() as conn:
        receipt = await forget_location_copies(conn, uuid4(), [_key(1)])
    assert receipt.absent_count == 1
    assert (await _row(pool, other))["normalized_text"] == _TEXT


async def test_a_later_envelope_for_a_forgotten_key_is_withheld_on_insert(
    switchboard_pool,
) -> None:
    pool = switchboard_pool
    async with pool.acquire() as conn:
        await forget_location_copies(conn, uuid4(), [_key(1)])
    late = await _inbox(pool, _key(1), lifecycle="metadata_ref", decomposition=None)
    fresh = await _inbox(pool, _key(2), lifecycle="metadata_ref", decomposition=None)
    other = await _inbox(pool, _key(1), channel="telegram_bot")

    assert _withheld(await _row(pool, late))
    assert (await _row(pool, fresh))["normalized_text"] == _TEXT
    assert (await _row(pool, other))["normalized_text"] == _TEXT


async def test_an_insert_racing_the_forgetter_waits_and_is_withheld(switchboard_pool) -> None:
    pool = switchboard_pool
    forgetter = await pool.acquire()
    try:
        transaction = forgetter.transaction()
        await transaction.start()
        await forget_location_copies(forgetter, uuid4(), [_key(1)])
        racing = asyncio.create_task(
            _inbox(pool, _key(1), lifecycle="metadata_ref", decomposition=None)
        )
        await asyncio.sleep(0.5)
        assert not racing.done(), "the insert must wait for the forgetter"
        await transaction.commit()
        row_id = await asyncio.wait_for(racing, timeout=10)
    finally:
        await pool.release(forgetter)
    assert _withheld(await _row(pool, row_id))


async def test_the_switchboard_role_can_forget(switchboard_pool) -> None:
    pool = switchboard_pool
    copy = await _inbox(pool, _key(1))
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
        receipt = await forget_location_copies(conn, uuid4(), [_key(1)])
    assert receipt.forgotten_count == 1
    assert _withheld(await _row(pool, copy))


async def test_a_late_insert_as_the_switchboard_role_is_withheld(switchboard_pool) -> None:
    pool = switchboard_pool
    async with pool.acquire() as conn:
        await forget_location_copies(conn, uuid4(), [_key(1)])
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
        await conn.execute(
            """
            INSERT INTO switchboard.message_inbox (received_at, request_context, raw_payload,
                normalized_text)
            VALUES (now(), $1::jsonb, $2::jsonb, $3)
            """,
            {"source_channel": "owntracks", "idempotency_key": _key(1)},
            {"payload": {"raw": None, "normalized_text": _TEXT}},
            _TEXT,
        )
    texts = await pool.fetch("SELECT normalized_text FROM switchboard.message_inbox")
    assert [t["normalized_text"] for t in texts] == [WITHHELD_TEXT]


async def test_the_receipt_is_count_only(switchboard_pool) -> None:
    pool = switchboard_pool
    await _inbox(pool, _key(1))
    grant = uuid4()
    async with pool.acquire() as conn:
        await forget_location_copies(conn, grant, [_key(1)])
    row = await pool.fetchrow(
        "SELECT * FROM switchboard.location_retention_forgetting WHERE grant_id = $1", grant
    )
    assert set(dict(row)) == {
        "id",
        "grant_id",
        "outcome",
        "item_count",
        "forgotten_count",
        "already_forgotten_count",
        "absent_count",
        "blocked_count",
        "recorded_at",
    }
