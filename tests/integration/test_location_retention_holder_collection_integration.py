"""Chronicler collects Switchboard's holder receipt and makes a grant ready (bu-s11n0s.7 S4c-3).

``collect_holder_receipts`` asks Switchboard (through an injected forgetter: the
``location_retention.forget_copies`` relay in production) to forget its inbox copies of each
prepared, not-yet-ready grant's rows. A ``complete`` reply is recorded and the grant becomes
``ready``; a ``blocked`` reply is recorded and the grant waits. A malformed or unavailable reply
records nothing and fails the call, so the run's failure streak shows it.
"""

from __future__ import annotations

import contextlib
import shutil
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from butlers.chronicler import location_retention_grant as grants
from butlers.chronicler import location_retention_holders as holders
from tests.integration import test_location_retention_holders_integration as _holder_suite
from tests.integration import test_owntracks_retention_grant_integration as _grant_suite

grant_url = _grant_suite.grant_url
chronicler_db = _grant_suite.chronicler_db
runtime_db = _grant_suite.runtime_db
_grant = _holder_suite._grant
_states = _holder_suite._states
_receipts = _holder_suite._receipts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_HOLDER = "switchboard.message_inbox"


class _Switchboard:
    """Answers like Switchboard's forgetter: a count-only receipt per request."""

    def __init__(self, outcome: str = "complete", **override: Any) -> None:
        self.outcome = outcome
        self.override = override
        self.calls: list[tuple[UUID, tuple[str, ...]]] = []

    async def __call__(self, grant_id: UUID, keys: tuple[str, ...]) -> dict[str, Any]:
        self.calls.append((grant_id, keys))
        blocked = 1 if self.outcome == "blocked" else 0
        reply = {
            "outcome": self.outcome,
            "items": len(keys),
            "forgotten": len(keys) - blocked,
            "already_forgotten": 0,
            "absent": 0,
            "blocked": blocked,
        }
        return {**reply, **self.override}


async def _keys(pool: asyncpg.Pool, grant: UUID) -> tuple[str, ...]:
    rows = await pool.fetch(
        "SELECT logical_source_key FROM location_retention_grant_items "
        "WHERE grant_id = $1 ORDER BY raw_source_id",
        grant,
    )
    return tuple(row["logical_source_key"] for row in rows)


async def _receipt_body(pool: asyncpg.Pool, grant: UUID) -> Any:
    return await pool.fetchval(
        "SELECT receipt FROM location_retention_holder_receipts WHERE grant_id = $1 "
        "ORDER BY id DESC LIMIT 1",
        grant,
    )


async def test_a_complete_reply_makes_the_grant_ready(chronicler_db, runtime_db) -> None:
    grant = await _grant(chronicler_db, runtime_db)
    switchboard = _Switchboard()
    async with runtime_db.acquire() as conn:
        result = await holders.collect_holder_receipts(conn, switchboard)

    assert switchboard.calls == [(grant, await _keys(chronicler_db, grant))]
    assert result == {"ready": 1, "blocked": 0}
    assert await _receipts(chronicler_db, grant) == [(_HOLDER, "complete")]
    assert await _states(chronicler_db, grant) == ["ready"]
    body = await _receipt_body(chronicler_db, grant)
    body = body if isinstance(body, dict) else __import__("json").loads(body)
    assert body == {"items": 1, "forgotten": 1, "already_forgotten": 0, "absent": 0, "blocked": 0}

    # A ready grant is not asked again.
    async with runtime_db.acquire() as conn:
        assert await holders.collect_holder_receipts(conn, switchboard) == {
            "ready": 0,
            "blocked": 0,
        }
    assert len(switchboard.calls) == 1


async def test_a_blocked_reply_is_recorded_and_the_grant_waits(chronicler_db, runtime_db) -> None:
    grant = await _grant(chronicler_db, runtime_db)
    async with runtime_db.acquire() as conn:
        assert await holders.collect_holder_receipts(conn, _Switchboard("blocked")) == {
            "ready": 0,
            "blocked": 1,
        }
        # The next run asks again and a cleared block makes it ready.
        assert await holders.collect_holder_receipts(conn, _Switchboard()) == {
            "ready": 1,
            "blocked": 0,
        }
    assert await _receipts(chronicler_db, grant) == [(_HOLDER, "blocked"), (_HOLDER, "complete")]
    assert await _states(chronicler_db, grant) == ["ready"]


async def test_a_recorded_complete_without_ready_is_finished_without_asking(
    chronicler_db, runtime_db
) -> None:
    """A run that stopped between the receipt and ready: the next one appends ready only."""
    grant = await _grant(chronicler_db, runtime_db)
    switchboard = _Switchboard()
    async with runtime_db.acquire() as conn:
        await grants.record_holder_receipt(
            conn, grant, _HOLDER, outcome="complete", receipt={"items": 1}
        )
        assert await holders.collect_holder_receipts(conn, switchboard) == {
            "ready": 1,
            "blocked": 0,
        }
    assert switchboard.calls == []
    assert await _states(chronicler_db, grant) == ["ready"]


@pytest.mark.parametrize(
    "override",
    [
        {"outcome": "forgotten", "forgotten": 0, "blocked": 1},  # an unknown outcome
        {"items": 2},  # not this grant's item count
        {"forgotten": 0},  # the counts do not add up to the items
        {"forgotten": 0, "blocked": 1},  # complete with a blocked key
        {"lat": 1},  # an extra field
        {"forgotten": -1, "absent": 2},
        {"forgotten": True},
    ],
)
async def test_a_malformed_reply_records_nothing(chronicler_db, runtime_db, override) -> None:
    grant = await _grant(chronicler_db, runtime_db)
    async with runtime_db.acquire() as conn:
        with pytest.raises(holders.HolderReplyMalformed):
            await holders.collect_holder_receipts(conn, _Switchboard(**override))
    assert await _receipts(chronicler_db, grant) == []
    assert await _states(chronicler_db, grant) == []


async def test_a_reply_that_is_not_an_object_records_nothing(chronicler_db, runtime_db) -> None:
    grant = await _grant(chronicler_db, runtime_db)

    async def forget(grant_id: UUID, keys: tuple[str, ...]) -> Any:
        return ["complete"]

    async with runtime_db.acquire() as conn:
        with pytest.raises(holders.HolderReplyMalformed):
            await holders.collect_holder_receipts(conn, forget)
    assert await _receipts(chronicler_db, grant) == []


async def test_an_unavailable_switchboard_records_nothing(chronicler_db, runtime_db) -> None:
    grant = await _grant(chronicler_db, runtime_db)

    async def forget(grant_id: UUID, keys: tuple[str, ...]) -> Any:
        raise holders.HoldersUnavailable("RelayUnavailable")

    async with runtime_db.acquire() as conn:
        with pytest.raises(holders.HoldersUnavailable):
            await holders.collect_holder_receipts(conn, forget)
    assert await _receipts(chronicler_db, grant) == []


async def test_an_empty_grant_is_never_sent(chronicler_db, runtime_db) -> None:
    async with runtime_db.acquire() as conn:
        empty = await grants.prepare_retention_grant(conn, config_revisions=_grant_suite._REVISIONS)
    assert empty.item_count == 0
    switchboard = _Switchboard()
    async with runtime_db.acquire() as conn:
        assert await holders.collect_holder_receipts(conn, switchboard) == {
            "ready": 0,
            "blocked": 0,
        }
    assert switchboard.calls == []


async def test_a_run_asks_at_most_the_limit_least_recently_asked_first(
    chronicler_db, runtime_db
) -> None:
    """A grant that stays blocked does not starve the grants behind it."""
    first = await _grant(chronicler_db, runtime_db, minute=0)
    second = await _grant(chronicler_db, runtime_db, minute=1)
    switchboard = _Switchboard("blocked")
    async with runtime_db.acquire() as conn:
        await holders.collect_holder_receipts(conn, switchboard, limit=1)
        await holders.collect_holder_receipts(conn, switchboard, limit=1)
        await holders.collect_holder_receipts(conn, switchboard, limit=1)
    assert [call[0] for call in switchboard.calls] == [first, second, first]


async def test_a_failed_ask_does_not_stop_the_others_and_fails_at_the_end(
    chronicler_db, runtime_db
) -> None:
    bad = await _grant(chronicler_db, runtime_db, minute=0)
    good = await _grant(chronicler_db, runtime_db, minute=1)
    switchboard = _Switchboard()

    async def forget(grant_id: UUID, keys: tuple[str, ...]) -> Any:
        if grant_id == bad:
            raise holders.HoldersUnavailable("RelayRefused")
        return await switchboard(grant_id, keys)

    async with runtime_db.acquire() as conn:
        with pytest.raises(holders.HoldersUnavailable):
            await holders.collect_holder_receipts(conn, forget)
    assert await _receipts(chronicler_db, bad) == []
    assert await _states(chronicler_db, good) == ["ready"]


async def test_a_grant_that_keeps_failing_is_asked_after_the_others(
    chronicler_db, runtime_db
) -> None:
    """A failed ask records no receipt, but it still moves the grant to the back."""
    bad = await _grant(chronicler_db, runtime_db, minute=0)
    other = await _grant(chronicler_db, runtime_db, minute=1)
    asked: list[UUID] = []

    async def forget(grant_id: UUID, keys: tuple[str, ...]) -> Any:
        asked.append(grant_id)
        if grant_id == bad:
            raise holders.HolderReplyMalformed("not a count-only receipt")
        return await _Switchboard("blocked")(grant_id, keys)

    async with runtime_db.acquire() as conn:
        for _ in range(3):
            with contextlib.suppress(holders.HolderReplyMalformed):
                await holders.collect_holder_receipts(conn, forget, limit=1)
    assert asked == [bad, other, bad]
