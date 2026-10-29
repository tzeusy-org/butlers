"""Deterministic Relationship reminder jobs (bu-idc5mx.4).

``upcoming-dates-check`` and ``relationship-maintenance`` run as job-mode schedules: fixed
templates over the existing reads, sent through the owner-notify boundary, with no session and
no model. The seams (date read, overdue evaluation, tier ranking, urgency, notify) are injected,
so these tests need no database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.tools.relationship.reminders import (
    ALL_CLEAR_MESSAGE,
    run_relationship_maintenance,
    run_upcoming_dates_check,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)


def _notify(outcome: str = "delivered") -> AsyncMock:
    return AsyncMock(return_value=outcome)


# ---------------------------------------------------------------------------
# upcoming-dates-check
# ---------------------------------------------------------------------------


async def test_dates_job_sends_one_templated_message_sorted_by_date():
    dates_fn = AsyncMock(
        return_value=[
            {"contact_name": "Carol", "label": "Birthday", "upcoming_date": "2026-10-27"},
            {"contact_name": "Alice", "label": "Birthday", "upcoming_date": "2026-10-22"},
            {"contact_name": "Bob", "label": "Anniversary", "upcoming_date": "2026-10-23"},
        ]
    )
    notify = _notify()

    result = await run_upcoming_dates_check(
        MagicMock(), dates_fn=dates_fn, notify_fn=notify, now=_NOW
    )

    assert dates_fn.await_args.kwargs == {"days_ahead": 7}
    notify.assert_awaited_once_with(
        "Upcoming dates this week:\n"
        "- Alice: Birthday, Oct 22 (today)\n"
        "- Bob: Anniversary, Oct 23 (tomorrow)\n"
        "- Carol: Birthday, Oct 27 (in 5 days)"
    )
    assert result == {"dates": 3, "notify": "delivered"}


async def test_dates_job_is_silent_on_a_successful_empty_read():
    notify = _notify()

    result = await run_upcoming_dates_check(
        MagicMock(), dates_fn=AsyncMock(return_value=[]), notify_fn=notify, now=_NOW
    )

    notify.assert_not_awaited()
    assert result == {"dates": 0, "notify": None}


async def test_dates_job_failed_read_sends_nothing_and_propagates():
    notify = _notify()

    with pytest.raises(RuntimeError, match="posture read failed"):
        await run_upcoming_dates_check(
            MagicMock(),
            dates_fn=AsyncMock(side_effect=RuntimeError("posture read failed")),
            notify_fn=notify,
            now=_NOW,
        )

    notify.assert_not_awaited()


# ---------------------------------------------------------------------------
# relationship-maintenance
# ---------------------------------------------------------------------------


def _contact(name: str, *, days: float | None, cadence: int) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "name": name,
        "days_since_last_interaction": days,
        "effective_cadence": cadence,
    }


def _evaluation(contacts: list[dict[str, Any]], *, unmeasurable: int = 0) -> AsyncMock:
    return AsyncMock(
        return_value={
            "contacts": contacts,
            "cadence_available": unmeasurable == 0,
            "unmeasurable_count": unmeasurable,
        }
    )


def _ranking(*contacts: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"contact_id": c["id"], "dunbar_tier": 15} for c in contacts]


def _urgency_by(order: list[dict[str, Any]], bonuses: dict[str, float] | None = None):
    """An urgency seam that ranks the given tier ranking in ``order`` and honours ``top_n``."""
    bonuses = bonuses or {}
    position = {c["id"]: i for i, c in enumerate(order)}
    names = {c["id"]: c["name"] for c in order}

    async def urgency(pool, *, tier_ranking, top_n):
        ranked = sorted(
            (e for e in tier_ranking if e["contact_id"] in position),
            key=lambda e: position[e["contact_id"]],
        )
        return [
            {
                "contact_id": e["contact_id"],
                "context_bonus": bonuses.get(names[e["contact_id"]], 0.0),
                "urgency": 10.0 - position[e["contact_id"]],
            }
            for e in ranked
        ][:top_n]

    return AsyncMock(side_effect=urgency)


async def test_maintenance_unmeasurable_cadence_sends_nothing_not_even_all_clear():
    notify = _notify()
    rank_fn = AsyncMock()

    result = await run_relationship_maintenance(
        MagicMock(),
        evaluate_fn=_evaluation([], unmeasurable=2),
        rank_fn=rank_fn,
        notify_fn=notify,
    )

    notify.assert_not_awaited()
    rank_fn.assert_not_awaited()
    assert result == {"status": "unmeasurable", "unmeasurable_count": 2, "notify": None}


async def test_maintenance_healthy_empty_sends_the_exact_all_clear():
    notify = _notify()

    result = await run_relationship_maintenance(
        MagicMock(), evaluate_fn=_evaluation([]), rank_fn=AsyncMock(), notify_fn=notify
    )

    assert ALL_CLEAR_MESSAGE == (
        "All your key relationships are up to date — no overdue check-ins this week."
    )
    notify.assert_awaited_once_with(ALL_CLEAR_MESSAGE)
    assert result == {"status": "all_clear", "suggested": 0, "notify": "delivered"}


async def test_maintenance_ranks_only_eligible_contacts_by_urgency_top_three():
    eligible = [
        _contact("Ann", days=40.0, cadence=14),
        _contact("Ben", days=30.4, cadence=21),
        _contact("Cat", days=50.0, cadence=45),
        _contact("Dan", days=200.0, cadence=120),
    ]
    bonus_only = _contact("Eve", days=3.0, cadence=14)  # not overdue: outside the eligible set
    ann, ben, cat, dan = eligible
    urgency_fn = _urgency_by([bonus_only, cat, ann, dan, ben], bonuses={"Cat": 3.0, "Ann": 0.5})
    notify = _notify()

    result = await run_relationship_maintenance(
        MagicMock(),
        evaluate_fn=_evaluation(eligible),
        rank_fn=AsyncMock(return_value=_ranking(*eligible, bonus_only)),
        urgency_fn=urgency_fn,
        notify_fn=notify,
    )

    ranked_ids = {e["contact_id"] for e in urgency_fn.await_args.kwargs["tier_ranking"]}
    assert ranked_ids == {c["id"] for c in eligible}
    assert urgency_fn.await_args.kwargs["top_n"] == 3
    notify.assert_awaited_once_with(
        "Reconnect this week:\n"
        "1. Cat: 50 days since you last talked (every 45 days); a date is coming up; "
        "gift pending\n"
        "2. Ann: 40 days since you last talked (every 14 days)\n"
        "3. Dan: 200 days since you last talked (every 120 days)"
    )
    assert result == {
        "status": "suggested",
        "suggested": 3,
        "overdue": 4,
        "unranked": 0,
        "notify": "delivered",
    }


async def test_maintenance_fewer_than_three_overdue_suggests_only_those():
    ann = _contact("Ann", days=None, cadence=14)
    notify = _notify("suppressed")

    result = await run_relationship_maintenance(
        MagicMock(),
        evaluate_fn=_evaluation([ann]),
        rank_fn=AsyncMock(return_value=_ranking(ann)),
        urgency_fn=_urgency_by([ann]),
        notify_fn=notify,
    )

    notify.assert_awaited_once_with(
        "Reconnect this week:\n1. Ann: no conversation on record (every 14 days)"
    )
    assert result["suggested"] == 1
    assert result["notify"] == "suppressed"


async def test_maintenance_ranking_gap_is_counted_not_backfilled():
    ann = _contact("Ann", days=40.0, cadence=14)
    ghost = _contact("Ghost", days=90.0, cadence=14)  # eligible, absent from the tier ranking
    notify = _notify()

    result = await run_relationship_maintenance(
        MagicMock(),
        evaluate_fn=_evaluation([ann, ghost]),
        rank_fn=AsyncMock(return_value=_ranking(ann)),
        urgency_fn=_urgency_by([ann, ghost]),
        notify_fn=notify,
    )

    assert "Ghost" not in notify.await_args.args[0]
    assert (result["suggested"], result["overdue"], result["unranked"]) == (1, 2, 1)


async def test_maintenance_overdue_but_nothing_ranked_sends_nothing():
    ghost = _contact("Ghost", days=90.0, cadence=14)
    notify = _notify()

    result = await run_relationship_maintenance(
        MagicMock(),
        evaluate_fn=_evaluation([ghost]),
        rank_fn=AsyncMock(return_value=[]),
        urgency_fn=_urgency_by([]),
        notify_fn=notify,
    )

    notify.assert_not_awaited()
    assert result == {
        "status": "unranked",
        "suggested": 0,
        "overdue": 1,
        "unranked": 1,
        "notify": None,
    }
