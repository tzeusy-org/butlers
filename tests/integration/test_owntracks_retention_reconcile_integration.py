"""Chronicler reconciles a grant against the connector's committed evidence (bu-s11n0s.7 S4b-4b).

``reconcile_ready_grants`` reads, for each ready and unsettled grant, the connector's receipt and
tombstones (``connectors.owntracks_retention_batches`` / ``_tombstones``, core_342) and the raw
rows. Evidence that matches the grant exactly appends ``reconciled``; anything else appends
``failed``; no receipt yet appends nothing. The connector's side is written here directly, as
its committed transaction would leave it, because ``ready`` is unreachable until S4c.
"""

from __future__ import annotations

import json
import shutil
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.chronicler.location_retention_grant import prepare_retention_grant
from butlers.chronicler.location_retention_reconcile import reconcile_ready_grants
from tests.integration import test_owntracks_retention_grant_integration as _grant_suite

# The grant suite's database and helpers: core and Chronicler at head, adapters' runs recorded.
grant_url = _grant_suite.grant_url
chronicler_db = _grant_suite.chronicler_db
runtime_db = _grant_suite.runtime_db
_REVISIONS = _grant_suite._REVISIONS
_cover = _grant_suite._cover
_insert_point = _grant_suite._insert_point

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_STATE = "INSERT INTO location_retention_grant_states (grant_id, state) VALUES ($1, $2)"
_ITEMS = (
    "SELECT raw_source_id, logical_source_key, content_digest "
    "FROM location_retention_grant_items WHERE grant_id = $1 ORDER BY raw_source_id"
)


async def _ready(chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool, *minutes: int) -> UUID:
    for minute in minutes:
        await _cover(chronicler_db, await _insert_point(chronicler_db, minute))
    async with runtime_db.acquire() as conn:
        grant = await prepare_retention_grant(conn, config_revisions=_REVISIONS)
    await chronicler_db.execute(_STATE, grant.id, "ready")
    return grant.id


async def _execute(
    chronicler_db: asyncpg.Pool,
    grant_id: UUID,
    *,
    delete: bool = True,
    item_count: int | None = None,
    tamper=None,
) -> None:
    """The connector's committed result: rows deleted, a tombstone per item, the receipt."""
    items = [tuple(row.values()) for row in await chronicler_db.fetch(_ITEMS, grant_id)]
    tombstones = tamper(items) if tamper else items
    count = len(items) if item_count is None else item_count
    async with chronicler_db.acquire() as conn:
        async with conn.transaction():
            if delete == "restore":
                # A fix restored under its logical key with a new id: still present.
                await conn.execute(
                    "UPDATE connectors.owntracks_points SET id = gen_random_uuid() WHERE id = $1",
                    items[0][0],
                )
                await conn.execute(
                    "DELETE FROM connectors.owntracks_points WHERE id = ANY($1::uuid[])",
                    [item[0] for item in items[1:]],
                )
            elif delete:
                await conn.execute(
                    "DELETE FROM connectors.owntracks_points WHERE id = ANY($1::uuid[])",
                    [item[0] for item in items],
                )
            await conn.executemany(
                "INSERT INTO connectors.owntracks_retention_tombstones "
                "(raw_source_id, logical_source_key, content_digest, grant_id) "
                "VALUES ($1, $2, $3, $4)",
                [(*item, grant_id) for item in tombstones],
            )
            await conn.execute(
                "INSERT INTO connectors.owntracks_retention_batches "
                "(grant_id, item_count, deleted_count, already_forgotten_count) "
                "VALUES ($1, $2, $2, 0)",
                grant_id,
                count,
            )


async def _states(chronicler_db: asyncpg.Pool, grant_id: UUID) -> list[tuple[str, dict]]:
    rows = await chronicler_db.fetch(
        "SELECT state, receipt FROM location_retention_grant_states "
        "WHERE grant_id = $1 ORDER BY id",
        grant_id,
    )

    def receipt(raw) -> dict:
        loaded = json.loads(raw) if isinstance(raw, str) else raw
        return dict(loaded or {})

    return [(row["state"], receipt(row["receipt"])) for row in rows]


async def _reconcile(runtime_db: asyncpg.Pool, **kwargs):
    async with runtime_db.acquire() as conn:
        return await reconcile_ready_grants(conn, **kwargs)


async def test_matching_evidence_reconciles_the_grant_once(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    grant_id = await _ready(chronicler_db, runtime_db, 0, 5)
    await _execute(chronicler_db, grant_id)

    (outcome,) = await _reconcile(runtime_db)

    assert (outcome.grant_id, outcome.state) == (grant_id, "reconciled")
    assert await _states(chronicler_db, grant_id) == [
        ("ready", {}),
        ("reconciled", {"items": 2, "deleted": 2, "already_forgotten": 0}),
    ]
    # Settled: a second run neither reads nor appends anything for it.
    assert await _reconcile(runtime_db) == []
    assert len(await _states(chronicler_db, grant_id)) == 2


async def test_a_grant_without_a_receipt_stays_pending(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    grant_id = await _ready(chronicler_db, runtime_db, 0)

    (outcome,) = await _reconcile(runtime_db)

    assert (outcome.grant_id, outcome.state) == (grant_id, "pending")
    assert await _states(chronicler_db, grant_id) == [("ready", {})]


def _other_digest(items):
    raw_id, key, _ = items[0]
    return [(raw_id, key, "v1:" + "d" * 64), *items[1:]]


def _other_key(items):
    raw_id, _, digest = items[0]
    return [(raw_id, "owntracks:someone-else", digest), *items[1:]]


def _missing(items):
    return items[1:]


def _extra(items):
    return [*items, (uuid4(), "owntracks:extra", "v1:" + "e" * 64)]


@pytest.mark.parametrize(
    ("tamper", "delete", "item_count", "failed"),
    [
        (_other_digest, True, None, {"items": 2, "tombstones": 2, "matched": 1, "present": 0}),
        (_other_key, True, None, {"items": 2, "tombstones": 2, "matched": 1, "present": 0}),
        (_missing, True, 1, {"items": 2, "tombstones": 1, "matched": 1, "present": 0}),
        (_extra, True, None, {"items": 2, "tombstones": 3, "matched": 2, "present": 0}),
        (None, False, None, {"items": 2, "tombstones": 2, "matched": 2, "present": 2}),
        (None, "restore", None, {"items": 2, "tombstones": 2, "matched": 2, "present": 1}),
        (None, True, 3, {"items": 2, "tombstones": 2, "matched": 2, "present": 0}),
    ],
    ids=[
        "digest",
        "key",
        "missing-tombstone",
        "extra-tombstone",
        "rows-still-present",
        "key-restored-under-a-new-id",
        "count",
    ],
)
async def test_evidence_that_differs_from_the_grant_fails_it(
    chronicler_db: asyncpg.Pool,
    runtime_db: asyncpg.Pool,
    tamper,
    delete: bool,
    item_count: int | None,
    failed: dict,
) -> None:
    grant_id = await _ready(chronicler_db, runtime_db, 0, 5)
    await _execute(chronicler_db, grant_id, delete=delete, item_count=item_count, tamper=tamper)

    (outcome,) = await _reconcile(runtime_db)

    assert (outcome.grant_id, outcome.state) == (grant_id, "failed")
    assert await _states(chronicler_db, grant_id) == [("ready", {}), ("failed", failed)]


async def test_only_ready_unsettled_grants_are_reconciled_oldest_first(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    for minute in (0,):
        await _cover(chronicler_db, await _insert_point(chronicler_db, minute))
    async with runtime_db.acquire() as conn:
        withheld = await prepare_retention_grant(conn, config_revisions=_REVISIONS)
    first = await _ready(chronicler_db, runtime_db, 5)
    second = await _ready(chronicler_db, runtime_db, 10)
    failed = await _ready(chronicler_db, runtime_db, 15)
    await chronicler_db.execute(_STATE, failed, "failed")
    for grant_id in (first, second):
        await _execute(chronicler_db, grant_id)

    assert [o.grant_id for o in await _reconcile(runtime_db, limit=1)] == [first]
    assert [o.grant_id for o in await _reconcile(runtime_db)] == [second]
    assert await _states(chronicler_db, withheld.id) == []
    assert [s for s, _ in await _states(chronicler_db, failed)] == ["ready", "failed"]
    with pytest.raises(ValueError):
        await _reconcile(runtime_db, limit=0)


async def test_an_unknown_grant_is_reconciled_when_its_receipt_appears(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    grant_id = await _ready(chronicler_db, runtime_db, 0)
    await chronicler_db.execute(_STATE, grant_id, "unknown")
    await _execute(chronicler_db, grant_id)

    (outcome,) = await _reconcile(runtime_db)

    assert outcome.state == "reconciled"
    assert [s for s, _ in await _states(chronicler_db, grant_id)] == [
        "ready",
        "unknown",
        "reconciled",
    ]
