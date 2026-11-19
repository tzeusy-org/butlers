"""A dead address that writes again clears its undeliverable-email fact (bu-s11n0s.90).

Mock-level: the claim and clear rules without a database. The real-database round trip is in
``test_automated_evidence_sync.py`` (Docker, CI).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from butlers.roster_imports import roster_module

_facts = roster_module("relationship", "tools/automated_evidence_facts")

pytestmark = pytest.mark.unit

NOW = datetime(2030, 1, 10, 12, 0, tzinfo=UTC)


def _fact(address: str, observed_at: datetime) -> dict:
    return {
        "id": f"fact-{address}",
        "subject": f"s-{address}",
        "object": address,
        "observed_at": observed_at,
    }


async def test_only_a_strictly_later_message_from_the_exact_address_clears() -> None:
    facts = [
        _fact("a@x.test", NOW - timedelta(hours=3)),
        _fact("b@x.test", NOW - timedelta(hours=3)),
        _fact("c@x.test", NOW - timedelta(hours=3)),
    ]
    latest = {
        "a@x.test": ("m1", NOW - timedelta(hours=1)),  # later: clears
        "b@x.test": ("m2", NOW - timedelta(hours=3)),  # same instant: does not
        # c@x.test never wrote
    }
    retract = AsyncMock(return_value="fact-a@x.test")
    with patch.object(_facts, "retract_state_fact", new=retract):
        cleared, errors = await _facts._clear_revived(object(), facts, latest)
    assert (cleared, errors) == (1, 0)
    retract.assert_awaited_once()
    call = retract.await_args
    assert call is not None
    args, kwargs = call
    assert args[1:] == ("s-a@x.test", "undeliverable-email", "a@x.test")
    assert kwargs["observed_before"] == NOW - timedelta(hours=1)
    packet = kwargs["packet"]
    assert packet.items[0]["ref"] == "switchboard.message_inbox:m1"
    assert (packet.src, packet.origin) == (_facts.SOURCE, "direct")


async def test_a_repeat_finds_nothing_to_clear() -> None:
    facts = [_fact("a@x.test", NOW - timedelta(hours=3))]
    with patch.object(_facts, "retract_state_fact", new=AsyncMock(return_value=None)):
        cleared, errors = await _facts._clear_revived(object(), facts, {"a@x.test": ("m1", NOW)})
    assert (cleared, errors) == (0, 0)


async def test_a_failed_clear_is_an_error_not_a_crash() -> None:
    facts = [_fact("a@x.test", NOW - timedelta(hours=3))]
    with patch.object(_facts, "retract_state_fact", new=AsyncMock(side_effect=RuntimeError("x"))):
        cleared, errors = await _facts._clear_revived(object(), facts, {"a@x.test": ("m1", NOW)})
    assert (cleared, errors) == (0, 1)


def test_the_fact_address_is_normalised_the_way_it_was_keyed() -> None:
    assert _facts._address("  Bob@Example.TEST ") == "bob@example.test"
