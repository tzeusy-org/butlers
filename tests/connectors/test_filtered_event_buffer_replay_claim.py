"""The replay drain never holds a transaction across submission (bu-dhloi1.225).

``drain_replay_pending`` claims rows in one short statement under a per-drain
token and lease, calls ``submit_fn`` with no transaction or pooled connection
held and within a deadline, then records each outcome in a second short
statement guarded by the token. A cancelled drain releases what it still holds.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.connectors import filtered_event_buffer as feb

pytestmark = pytest.mark.unit

_ENDPOINT = "gmail:user:alice@example.com"


class _FakeConn:
    def __init__(self, pool: _FakePool) -> None:
        self.pool = pool

    @asynccontextmanager
    async def transaction(self):
        self.pool.open_transactions += 1
        try:
            yield
        finally:
            self.pool.open_transactions -= 1

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.pool.calls.append(("fetch", sql, args))
        return self.pool.rows

    async def execute(self, sql: str, *args: Any) -> str:
        self.pool.calls.append(("execute", sql, args))
        return self.pool.execute_result


class _FakePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self.open_transactions = 0
        self.held_connections = 0
        self.execute_result = "UPDATE 1"

    @asynccontextmanager
    async def acquire(self):
        self.held_connections += 1
        try:
            yield _FakeConn(self)
        finally:
            self.held_connections -= 1


def _row(row_id: int, text: str = "hi") -> dict[str, Any]:
    return {
        "id": row_id,
        "received_at": f"2026-11-09T10:00:0{row_id}Z",
        "external_message_id": f"msg-{row_id}",
        "full_payload": {
            "event": {"external_event_id": f"msg-{row_id}"},
            "payload": {"normalized_text": text},
        },
    }


async def test_submit_runs_after_the_claim_with_no_transaction_or_connection_held() -> None:
    pool = _FakePool([_row(1), _row(2)])
    seen: list[tuple[int, int, int]] = []

    async def submit(envelope: dict) -> None:
        claimed = sum(1 for kind, _, _ in pool.calls if kind == "fetch")
        seen.append((pool.open_transactions, pool.held_connections, claimed))

    await feb.drain_replay_pending(pool, "gmail", _ENDPOINT, submit)  # type: ignore[arg-type]

    assert seen == [(0, 0, 1), (0, 0, 1)]


async def test_claim_stamps_a_fresh_token_and_takes_lapsed_leases() -> None:
    pool = _FakePool([_row(1)])
    await feb.drain_replay_pending(pool, "gmail", _ENDPOINT, AsyncMock())  # type: ignore[arg-type]
    await feb.drain_replay_pending(pool, "gmail", _ENDPOINT, AsyncMock())  # type: ignore[arg-type]

    claims = [(sql, args) for kind, sql, args in pool.calls if kind == "fetch"]
    (sql, first), (_, second) = claims
    assert "FOR UPDATE SKIP LOCKED" in sql and "RETURNING" in sql
    assert "claimed_at < now() - make_interval(secs => $4)" in sql
    assert first[:2] == ("gmail", _ENDPOINT)
    assert isinstance(first[2], uuid.UUID) and first[2] != second[2]
    assert first[3] == feb.REPLAY_LEASE_S


@pytest.mark.parametrize(("fails", "status"), [(False, "replay_complete"), (True, "replay_failed")])
async def test_outcome_is_recorded_only_under_this_drains_token(fails: bool, status: str) -> None:
    pool = _FakePool([_row(1)])

    async def submit(envelope: dict) -> None:
        if fails:
            raise ValueError("synthetic failure")

    await feb.drain_replay_pending(pool, "gmail", _ENDPOINT, submit)  # type: ignore[arg-type]

    token = pool.calls[0][2][2]
    kind, sql, args = pool.calls[-1]
    assert kind == "execute"
    assert args[0] == status
    assert args[2:] == (1, "2026-11-09T10:00:01Z", token)
    assert "claim_token = $5" in sql
    assert "claim_token = NULL" in sql


async def test_cancel_mid_batch_releases_this_drains_claims_and_propagates() -> None:
    pool = _FakePool([_row(1), _row(2), _row(3)])

    async def submit(envelope: dict) -> None:
        if envelope["event"]["external_event_id"] == "msg-2":
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await feb.drain_replay_pending(pool, "gmail", _ENDPOINT, submit)  # type: ignore[arg-type]

    token = pool.calls[0][2][2]
    kinds = [(kind, args[0]) for kind, _, args in pool.calls]
    # Row 1 recorded; then the release hands rows 2 and 3 back to the queue.
    assert kinds[1] == ("execute", "replay_complete")
    kind, sql, args = pool.calls[-1]
    assert (kind, args) == ("execute", (token, "gmail", _ENDPOINT))
    assert "SET status = 'replay_pending'" in sql
    # The drain index's leading columns, so the release does not scan the table.
    assert "connector_type = $2 AND endpoint_identity = $3" in sql
    assert len(pool.calls) == 3


async def test_a_submit_past_its_deadline_fails_inside_the_lease() -> None:
    pool = _FakePool([_row(1)])

    async def submit(envelope: dict) -> None:
        await asyncio.sleep(5)

    await feb.drain_replay_pending(
        pool,  # type: ignore[arg-type]
        "gmail",
        _ENDPOINT,
        submit,
        lease_s=1.0,
        submit_timeout_s=0.05,
    )

    _, _, args = pool.calls[-1]
    assert args[0] == "replay_failed"
    assert "timed out" in args[1]


async def test_the_drain_stops_before_a_submit_that_could_outlast_its_lease(monkeypatch) -> None:
    # Ten rows at a 60 s deadline can exceed a 300 s lease; past it, a reclaimer
    # could submit a row this drain is about to submit (r4512166981).
    pool = _FakePool([_row(1), _row(2), _row(3)])
    now = [1000.0]
    monkeypatch.setattr(feb, "_monotonic", lambda: now[0])
    submitted: list[str] = []

    async def submit(envelope: dict) -> None:
        submitted.append(envelope["event"]["external_event_id"])
        now[0] += 45.0  # each submit uses most of its 60 s deadline

    await feb.drain_replay_pending(
        pool,  # type: ignore[arg-type]
        "gmail",
        _ENDPOINT,
        submit,
        lease_s=150.0,
        submit_timeout_s=60.0,
    )

    # Row 2 starts at 45 s (45 + 60 < 150); row 3 would start at 90 s (90 + 60 >= 150).
    assert submitted == ["msg-1", "msg-2"]
    token = pool.calls[0][2][2]
    assert [args[0] for kind, _, args in pool.calls[1:3]] == ["replay_complete"] * 2
    kind, sql, args = pool.calls[-1]
    assert (kind, args) == ("execute", (token, "gmail", _ENDPOINT))
    assert "SET status = 'replay_pending'" in sql
    assert len(pool.calls) == 4


async def test_a_not_replayable_row_is_refused_before_submit_and_its_claim_cleared() -> None:
    row = _row(1)
    row["full_payload"][feb.REPLAY_UNAVAILABLE_KEY] = "gmail fetch failed: HTTP 404"
    pool = _FakePool([row])
    submit = AsyncMock()

    await feb.drain_replay_pending(pool, "gmail", _ENDPOINT, submit)  # type: ignore[arg-type]

    submit.assert_not_awaited()
    token = pool.calls[0][2][2]
    kind, sql, args = pool.calls[-1]
    assert kind == "execute" and "claim_token = NULL" in sql
    assert args == (
        "replay_failed",
        "not replayable: gmail fetch failed: HTTP 404",
        1,
        "2026-11-09T10:00:01Z",
        token,
    )


def test_the_submit_deadline_must_be_shorter_than_the_lease() -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            feb.drain_replay_pending(
                _FakePool([]),  # type: ignore[arg-type]
                "gmail",
                _ENDPOINT,
                AsyncMock(),
                lease_s=5,
                submit_timeout_s=5,
            )
        )


async def test_a_lost_claim_is_logged_and_left_alone(caplog) -> None:
    pool = _FakePool([_row(1)])
    pool.execute_result = "UPDATE 0"

    with caplog.at_level("INFO", logger=feb.logger.name):
        await feb.drain_replay_pending(pool, "gmail", _ENDPOINT, AsyncMock())  # type: ignore[arg-type]

    assert any(
        "no longer claimed" in r.getMessage() and "id=1" in r.getMessage() for r in caplog.records
    )
    assert not any("replayed message" in r.getMessage() for r in caplog.records)
