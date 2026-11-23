"""The ``inbound_still_unanswered`` premise probe (bu-s11n0s.8 S2).

A break-through line is withdrawn, unsent, once the owner has answered in the thread. The probe
answers False only on a positive owner reply; anything it cannot read is unknown (None), which
the broker delivers stamped "as of" rather than treating as a withdrawal.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.tools.switchboard.insight import premises

pytestmark = pytest.mark.unit

_AFTER = "2026-11-16T09:00:00+00:00"


def _pool(reply: Any) -> MagicMock:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=reply)
    return pool


async def _probe(pool: Any, args: dict[str, Any], owner_email: str | None = "Me@Example.test"):
    with patch(
        "butlers.credential_store.resolve_owner_entity_info",
        AsyncMock(return_value=owner_email),
    ):
        return await premises.PREMISE_PROBES[("switchboard", "inbound_still_unanswered")](
            pool, args
        )


async def test_the_probe_is_registered() -> None:
    assert ("switchboard", "inbound_still_unanswered") in premises.PREMISE_PROBES


async def test_no_owner_reply_means_the_premise_holds() -> None:
    assert await _probe(_pool(None), {"thread": "t1", "after": _AFTER}) is True


async def test_an_owner_reply_after_the_hit_falsifies_the_premise() -> None:
    assert await _probe(_pool(1), {"thread": "t1", "after": _AFTER}) is False


async def test_the_query_is_scoped_to_thread_time_and_owner_and_reads_no_text() -> None:
    pool = _pool(None)

    await _probe(pool, {"thread": "t1", "after": _AFTER})

    query, thread, since, owner_email = pool.fetchval.await_args.args
    assert thread == "t1"
    assert since.isoformat() == _AFTER
    assert owner_email == "me@example.test"
    for column in ("normalized_text", "raw_payload"):
        assert column not in query


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"thread": "", "after": _AFTER},
        {"thread": "t1"},
        {"thread": "t1", "after": "yesterday"},
        {"thread": "t1", "after": "2026-11-16T09:00:00"},
        {"thread": 7, "after": _AFTER},
    ],
)
async def test_malformed_args_are_unknown_never_a_withdrawal(args: dict[str, Any]) -> None:
    pool = _pool(1)

    assert await _probe(pool, args) is None
    pool.fetchval.assert_not_awaited()
