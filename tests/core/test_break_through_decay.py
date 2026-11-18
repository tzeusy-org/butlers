"""Break-through decay: ask once after 30 unopened days, dormant after 90 hitless (bu-s11n0s.8 S4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core import break_through_decay as decay
from butlers.core.break_through_decay import Clause, Stats, decide

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 18, 12, tzinfo=UTC)
D = timedelta(days=1)


def _clause(age_days: int, state=None, changed_days_ago=None) -> Clause:
    return Clause(
        id=uuid.uuid4(),
        created_at=NOW - age_days * D,
        state=state,
        changed_at=None if changed_days_ago is None else NOW - changed_days_ago * D,
    )


def test_a_clause_with_delivered_but_unopened_hits_is_asked_after_30_days():
    c = _clause(31)
    out = decide([c], {c.id: Stats(NOW - 2 * D, delivered_30=3, opened_30=0)}, NOW)
    assert out.ask == (c.id,) and out.dormant == ()


@pytest.mark.parametrize(
    "stats",
    [
        Stats(NOW - D, delivered_30=3, opened_30=1),  # opened
        Stats(NOW - D, delivered_30=0, opened_30=0),  # hits never delivered: nothing unopened
        Stats(None, 0, 0),  # no hits at all is not "unopened"
    ],
)
def test_an_opened_or_undelivered_clause_is_not_asked(stats):
    c = _clause(45)
    assert decide([c], {c.id: stats}, NOW).ask == ()


def test_a_young_clause_is_never_asked_or_dormant():
    c = _clause(29)
    assert decide([c], {c.id: Stats(NOW, 5, 0)}, NOW) == decay.Decision((), ())
    assert decide([c], {}, NOW) == decay.Decision((), ())


def test_zero_hits_for_90_days_goes_dormant_and_a_recent_hit_prevents_it():
    quiet, hit_once = _clause(120), _clause(120)
    stats = {hit_once.id: Stats(NOW - 10 * D, delivered_30=1, opened_30=1)}
    out = decide([quiet, hit_once], stats, NOW)
    assert out.dormant == (quiet.id,)
    assert out.ask == ()


def test_dormant_wins_over_ask_and_a_dormant_clause_is_left_alone():
    c, gone = _clause(200), _clause(200, state="dormant", changed_days_ago=50)
    out = decide([c, gone], {c.id: Stats(NOW - 100 * D, 1, 0)}, NOW)
    assert out.dormant == (c.id,) and out.ask == ()


def test_an_asked_clause_is_not_asked_again_but_can_still_go_dormant():
    asked = _clause(60, state="asked", changed_days_ago=5)
    assert decide([asked], {asked.id: Stats(NOW - D, 2, 0)}, NOW).ask == ()
    old = _clause(200, state="asked", changed_days_ago=100)
    assert decide([old], {}, NOW).dormant == (old.id,)


def test_a_kept_clause_restarts_the_clock_from_the_keep():
    kept_recently = _clause(300, state="kept", changed_days_ago=10)
    stats = {kept_recently.id: Stats(None, 0, 0)}
    assert decide([kept_recently], stats, NOW) == decay.Decision((), ())
    kept_long_ago = _clause(300, state="kept", changed_days_ago=95)
    assert decide([kept_long_ago], {}, NOW).dormant == (kept_long_ago.id,)
    reasked = _clause(300, state="kept", changed_days_ago=40)
    out = decide([reasked], {reasked.id: Stats(NOW - 3 * D, 2, 0)}, NOW)
    assert out.ask == (reasked.id,)


def _pool(clauses: list[Clause], stats_rows: list[dict], applied: dict):
    pool = MagicMock()

    async def fetchval(sql, *args):
        if "decay_clauses" in sql:
            return {
                "clauses": [
                    {
                        "id": str(c.id),
                        "created_at": c.created_at.isoformat(),
                        "state": c.state,
                        "changed_at": c.changed_at.isoformat() if c.changed_at else None,
                    }
                    for c in clauses
                ]
            }
        if "decay_apply" in sql:
            pool.applied = args[0]
            return applied
        return None  # no existing candidate

    pool.fetchval = AsyncMock(side_effect=fetchval)
    pool.fetch = AsyncMock(return_value=stats_rows)
    return pool


async def test_sweep_hands_the_definer_ids_and_queues_one_content_free_line(monkeypatch):
    c = _clause(40)
    pool = _pool(
        [c],
        [{"clause_id": c.id, "last_hit": NOW - D, "delivered_30": 2, "opened_30": 0}],
        {"asked": [str(c.id)], "dormant": []},
    )
    propose = AsyncMock(return_value={"status": "accepted"})
    monkeypatch.setattr(
        decay.importlib,
        "import_module",
        lambda _name: MagicMock(propose_insight_candidate=propose),
    )
    out = await decay.sweep(pool, now=NOW)
    assert out.ask == (c.id,)
    assert pool.applied == {"ask": [str(c.id)], "dormant": []}  # a dict: the codec encodes once
    kwargs = propose.await_args.kwargs
    assert kwargs["category"] == decay.KEEP_CATEGORY and kwargs["priority"] < 90
    assert kwargs["dedup_key"] == f"switchboard:{decay.KEEP_CATEGORY}:{c.id}"
    # content-free: no sender, address, domain, id or message text
    assert str(c.id) not in kwargs["message"] and "@" not in kwargs["message"]


async def test_sweep_does_not_requeue_when_the_definer_refuses_the_ask(monkeypatch):
    c = _clause(40)
    pool = _pool(
        [c],
        [{"clause_id": c.id, "last_hit": NOW, "delivered_30": 1, "opened_30": 0}],
        {"asked": [], "dormant": []},
    )
    propose = AsyncMock()
    monkeypatch.setattr(
        decay.importlib, "import_module", lambda _n: MagicMock(propose_insight_candidate=propose)
    )
    await decay.sweep(pool, now=NOW)
    propose.assert_not_awaited()


async def test_sweep_retries_a_recently_asked_clause_whose_line_never_queued(monkeypatch):
    c = _clause(60, state="asked", changed_days_ago=2)
    pool = _pool([c], [], {"asked": [], "dormant": []})
    propose = AsyncMock(return_value={"status": "accepted"})
    monkeypatch.setattr(
        decay.importlib, "import_module", lambda _n: MagicMock(propose_insight_candidate=propose)
    )
    await decay.sweep(pool, now=NOW)
    assert propose.await_count == 1


async def test_sweep_never_raises():
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=RuntimeError("down"))
    assert await decay.sweep(pool, now=NOW) == decay.Decision((), ())


async def test_sweep_with_no_clauses_touches_nothing():
    pool = _pool([], [], {})
    await decay.sweep(pool, now=NOW)
    pool.fetch.assert_not_awaited()
