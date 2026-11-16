"""A grant becomes ready only with every holder's complete receipt (bu-s11n0s.7, S4c-1).

``record_holder_receipt`` appends what Chronicler read back from a holder of copies derived from
a grant's raw rows: ``complete`` (at most once per grant and holder) or ``blocked`` (may repeat,
never counts). ``append_grant_state(..., "ready")`` is refused with ``HoldersIncomplete`` unless
every holder in ``REQUIRED_HOLDERS`` has a complete receipt for that grant. Receipts are
count-only, and none is taken once the grant is ready.
"""

from __future__ import annotations

import shutil
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.chronicler import location_retention_grant as grants
from tests.integration import test_owntracks_retention_grant_integration as _grant_suite

grant_url = _grant_suite.grant_url
chronicler_db = _grant_suite.chronicler_db
runtime_db = _grant_suite.runtime_db

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _grant(chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool, minute: int = 0) -> UUID:
    point = await _grant_suite._insert_point(chronicler_db, minute)
    await _grant_suite._cover(chronicler_db, point)
    async with runtime_db.acquire() as conn:
        return (
            await grants.prepare_retention_grant(conn, config_revisions=_grant_suite._REVISIONS)
        ).id


async def _states(target: asyncpg.Pool, grant: UUID) -> list[str]:
    rows = await target.fetch(
        "SELECT state FROM location_retention_grant_states WHERE grant_id = $1 ORDER BY id", grant
    )
    return [row["state"] for row in rows]


async def _receipts(target: asyncpg.Pool, grant: UUID) -> list[tuple[str, str]]:
    rows = await target.fetch(
        "SELECT holder, outcome FROM location_retention_holder_receipts "
        "WHERE grant_id = $1 ORDER BY id",
        grant,
    )
    return [(row["holder"], row["outcome"]) for row in rows]


async def _complete_all(conn, grant: UUID) -> None:
    for holder in grants.REQUIRED_HOLDERS:
        await grants.record_holder_receipt(
            conn, grant, holder, outcome="complete", receipt={"sanitized": 1}
        )


async def test_ready_is_refused_without_every_complete_receipt(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    grant = await _grant(chronicler_db, runtime_db)
    (holder,) = grants.REQUIRED_HOLDERS
    async with runtime_db.acquire() as conn:
        with pytest.raises(grants.HoldersIncomplete):
            await grants.append_grant_state(conn, grant, "ready")
        await grants.record_holder_receipt(
            conn, grant, holder, outcome="blocked", receipt={"blocked": 1}
        )
        await grants.record_holder_receipt(
            conn, grant, holder, outcome="blocked", receipt={"blocked": 1}
        )
        with pytest.raises(grants.HoldersIncomplete):
            await grants.append_grant_state(conn, grant, "ready")
    assert await _states(chronicler_db, grant) == []

    async with runtime_db.acquire() as conn:
        await _complete_all(conn, grant)
        await grants.append_grant_state(conn, grant, "ready", receipt={"holders": 1})
    assert await _states(chronicler_db, grant) == ["ready"]


async def test_a_complete_receipt_is_for_its_own_grant_only(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    done = await _grant(chronicler_db, runtime_db)
    other = await _grant(chronicler_db, runtime_db, minute=5)
    async with runtime_db.acquire() as conn:
        await _complete_all(conn, done)
        with pytest.raises(grants.HoldersIncomplete):
            await grants.append_grant_state(conn, other, "ready")


async def test_complete_is_recorded_once_and_nothing_after_ready(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    grant = await _grant(chronicler_db, runtime_db)
    (holder,) = grants.REQUIRED_HOLDERS
    async with runtime_db.acquire() as conn:
        await _complete_all(conn, grant)
        with pytest.raises(grants.GrantStateRefused):
            await grants.record_holder_receipt(conn, grant, holder, outcome="complete")
        await grants.append_grant_state(conn, grant, "ready")
        with pytest.raises(grants.GrantStateRefused):
            await grants.record_holder_receipt(conn, grant, holder, outcome="blocked")
    assert await _receipts(chronicler_db, grant) == [(holder, "complete")]


@pytest.mark.parametrize(
    ("holder", "outcome", "receipt", "error"),
    [
        ("switchboard.nowhere", "complete", {}, ValueError),
        (grants.REQUIRED_HOLDERS[0], "forgotten", {}, ValueError),
        (grants.REQUIRED_HOLDERS[0], "complete", {"lat": 1.3}, ValueError),
        (grants.REQUIRED_HOLDERS[0], "complete", {"n": -1}, ValueError),
    ],
)
async def test_a_malformed_receipt_writes_nothing(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool, holder, outcome, receipt, error
) -> None:
    grant = await _grant(chronicler_db, runtime_db)
    async with runtime_db.acquire() as conn:
        with pytest.raises(error):
            await grants.record_holder_receipt(
                conn, grant, holder, outcome=outcome, receipt=receipt
            )
    assert await _receipts(chronicler_db, grant) == []


async def test_a_receipt_for_a_missing_grant_is_refused(runtime_db: asyncpg.Pool) -> None:
    async with runtime_db.acquire() as conn:
        with pytest.raises(grants.GrantStateRefused):
            await grants.record_holder_receipt(
                conn, uuid4(), grants.REQUIRED_HOLDERS[0], outcome="complete"
            )
