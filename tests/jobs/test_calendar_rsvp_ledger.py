"""The docket's agreement ledger writer (bu-5ii4gj.9 S5, REQ-calendar-invitation-docket-006)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.jobs import calendar_rsvp_ledger as ledger

pytestmark = pytest.mark.unit

MARKER = {"policy_id": str(uuid4()), "policy_version": 3, "verdict": "declined"}


@pytest.mark.parametrize(
    ("status", "by", "outcome"),
    [
        ("approved", "owner", "approved_as_prepared"),
        ("executed", "owner", "approved_as_prepared"),
        ("rejected", "owner", "owner_rejected"),
        ("rejected", "system:rsvp_docket", None),
        ("rejected", "system:expiry", None),
        ("approved", None, None),
        ("pending", "owner", None),
        ("expired", "system:expiry", None),
    ],
)
def test_only_the_owners_own_decisions_are_outcomes(status, by, outcome) -> None:
    assert ledger.decided_outcome(status, by) == outcome


@pytest.mark.parametrize(
    ("current", "outcome"),
    [
        ("declined", "answered_same"),
        ("Declined", "answered_same"),
        ("accepted", "answered_elsewhere"),
        ("tentative", "answered_elsewhere"),
        ("needsAction", None),
        (None, None),
        ("", None),
    ],
)
def test_an_answer_outside_the_card_agrees_only_when_it_is_the_cards_verdict(
    current, outcome
) -> None:
    assert ledger.answered_outcome("declined", current) == outcome


def test_there_is_no_edited_outcome() -> None:
    assert ledger.OUTCOMES == (
        "approved_as_prepared",
        "owner_rejected",
        "answered_same",
        "answered_elsewhere",
    )
    with pytest.raises(ValueError):
        import asyncio

        asyncio.run(
            ledger.record_outcome(AsyncMock(), action_id=uuid4(), marker=MARKER, outcome="edited")
        )


async def test_a_write_goes_through_the_recorder_and_a_failure_is_raised_for_counting() -> None:
    pool = AsyncMock()
    pool.fetchval.return_value = True
    assert await ledger.record_outcome(
        pool, action_id=uuid4(), marker=MARKER, outcome="owner_rejected"
    )
    assert "calendar_rsvp_ledger_record" in pool.fetchval.await_args.args[0]
    assert pool.fetchval.await_args.args[2:] == (
        __import__("uuid").UUID(MARKER["policy_id"]),
        3,
        "owner_rejected",
    )
    assert await ledger.record_outcome(pool, action_id=uuid4(), marker=MARKER, outcome=None) is None
    pool.fetchval.side_effect = RuntimeError("down")
    with pytest.raises(ledger.LedgerUnavailable):
        await ledger.record_outcome(
            pool, action_id=uuid4(), marker=MARKER, outcome="owner_rejected"
        )


def test_the_owners_own_response_is_read_from_the_calendar_row() -> None:
    rows = [
        {
            "instance_id": "a",
            "event_metadata": {"attendees": [{"self": False, "response_status": "x"}]},
        },
        {
            "instance_id": "b",
            "event_metadata": {"attendees": [{"self": True, "response_status": "declined"}]},
        },
    ]
    assert ledger.owner_response(rows, "b") == "declined"
    assert ledger.owner_response(rows, "a") is None
    assert ledger.owner_response(rows, "missing") is None
