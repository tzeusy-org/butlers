"""Construction, transaction refusal and deterministic result ordering."""

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.queue_claim import ClaimQueue, claim_batch

pytestmark = pytest.mark.unit

_QUEUE = ClaimQueue(
    table="episodes",
    key="id",
    eligible="q.ready={arg0}",
    order_by="q.id",
    claim_set=(("ready", "false"),),
    returning=("id",),
    reclaim_expired=False,
)


@pytest.mark.parametrize("bad", ["episodes;drop", "a--b", "a/*b", "'episodes'", "a b", "a.b.c"])
def test_rejects_untrusted_queue_identifiers(bad):
    with pytest.raises(ValueError):
        replace(_QUEUE, table=bad)


@pytest.mark.parametrize(
    "changes",
    [
        {"eligible": "true;DELETE FROM episodes"},
        {"reclaim_expired": True},
        {"returning": ("ready",)},
    ],
)
def test_rejects_incomplete_queue_contract(changes):
    with pytest.raises(ValueError):
        replace(_QUEUE, **changes)


async def test_transaction_is_required_before_any_sql():
    conn = MagicMock(is_in_transaction=lambda: False, fetch=AsyncMock())
    with pytest.raises(RuntimeError, match="transaction"):
        await claim_batch(conn, _QUEUE, limit=1, owner="worker", lease_seconds=30, args=(True,))
    conn.fetch.assert_not_awaited()


async def test_locked_order_survives_unordered_update_returning_and_values_stay_bound():
    conn = MagicMock(is_in_transaction=lambda: True)
    conn.fetch = AsyncMock(side_effect=[[{"id": 2}, {"id": 1}], [{"id": 1}, {"id": 2}]])
    marker = "not SQL; -- a bound value"
    rows = await claim_batch(
        conn, _QUEUE, limit=2, owner="worker", lease_seconds=30, args=(marker,)
    )
    assert [row["id"] for row in rows] == [2, 1]
    for call in conn.fetch.await_args_list:
        assert marker not in call.args[0]
        assert marker in call.args[1:]
