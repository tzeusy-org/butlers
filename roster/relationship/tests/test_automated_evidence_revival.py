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


async def test_a_bounce_newer_than_the_message_keeps_the_mark() -> None:
    """bounce1 (day 1), message (day 5), bounce2 (day 7): the day-7 evidence must not be erased."""
    day = lambda n: NOW + timedelta(days=n)  # noqa: E731
    facts = [_fact("a@x.test", day(1))]
    latest = {"a@x.test": ("m1", day(5))}
    retract = AsyncMock(return_value="x")
    with patch.object(_facts, "retract_state_fact", new=retract):
        newer_bounce = await _facts._clear_revived(object(), facts, latest, {"a@x.test": day(7)})
        older_bounce = await _facts._clear_revived(object(), facts, latest, {"a@x.test": day(3)})
    assert newer_bounce == (0, 0)
    assert older_bounce == (1, 0)
    retract.assert_awaited_once()


class _SyncPool:
    """Enough pool for ``sync``: the dead-fact read and the state checkpoint."""

    def __init__(self, dead):
        self.fetch = AsyncMock(return_value=dead)


async def _run_sync(dead, *, revival_ckpt=None):
    seen = {}

    async def read_senders(addresses, since):
        seen["since"] = since
        seen["addresses"] = list(addresses)
        return {}

    states = {}

    async def state_get(_pool, key):
        return (
            revival_ckpt.isoformat() if key == _facts.REVIVAL_STATE_KEY and revival_ckpt else None
        )

    async def state_set(_pool, key, value):
        states[key] = value

    pool = _SyncPool(dead)
    with (
        patch.object(_facts, "state_get", new=state_get),
        patch.object(_facts, "state_set", new=state_set),
        patch.object(_facts, "resolve_contacts_by_channel_bulk", new=AsyncMock(return_value={})),
    ):
        stats = await _facts.sync(
            pool, read_inbox=AsyncMock(return_value=[]), read_senders=read_senders
        )
    return pool, stats, seen, states


async def test_only_this_jobs_own_marks_are_read_for_clearing() -> None:
    pool, stats, _seen, _states = await _run_sync([_fact("a@x.test", NOW)])
    assert "src = $2" in _facts._ACTIVE_DEAD_FACTS_SQL
    call = pool.fetch.await_args
    assert call is not None
    assert call.args[1:] == (_facts._MAX_DEAD_FACTS, _facts.SOURCE)
    assert stats["errors"] == 0


async def test_the_first_run_reads_back_to_the_oldest_mark_and_later_runs_are_bounded() -> None:
    old, newer = NOW - timedelta(days=20), NOW - timedelta(days=2)
    facts = [_fact("a@x.test", old), _fact("b@x.test", newer)]
    _p, _s, first, states = await _run_sync(facts)
    assert first["since"] == old  # no revival checkpoint yet: nothing is assumed examined
    assert _facts.REVIVAL_STATE_KEY in states

    ckpt = NOW - timedelta(hours=6)
    _p, _s, later, _st = await _run_sync(facts, revival_ckpt=ckpt)
    assert later["since"] == ckpt - _facts._OVERLAP  # only mail since the last pass can matter
    assert later["addresses"] == ["a@x.test", "b@x.test"]
