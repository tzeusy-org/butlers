"""Correlating a wake's completion with a reaction receipt (bu-6jv4m.8).

The verdict function is pure and carries the load-bearing rule: the sweep
may only ever conclude ``unreported``. A task that ran, exited cleanly, and
said nothing is silence -- not success -- and no path through these tests is
allowed to turn it into ``acted``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from butlers.core.domain_event_reaction_sweep import (
    ORPHAN_WAKE_AFTER,
    REACTION_GRACE,
    decide_reaction_verdict,
    reconcile_reaction_lifecycle,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
_EVENT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _candidate(**overrides) -> dict:
    row = {
        "event_id": _EVENT_ID,
        "task_name": "domain-event-x-finance",
        "delivered_at": _NOW - timedelta(minutes=5),
        "last_run_at": None,
        "until_at": _NOW + timedelta(minutes=5),
        "task_id": uuid.uuid4(),
        "latest_status": "scheduled",
    }
    row.update(overrides)
    return row


class TestVerdict:
    def test_a_wake_still_inside_its_window_is_left_alone(self) -> None:
        assert decide_reaction_verdict(_candidate(), now=_NOW) is None

    def test_a_wake_that_just_ran_is_marked_running(self) -> None:
        verdict = decide_reaction_verdict(
            _candidate(last_run_at=_NOW - timedelta(minutes=1)), now=_NOW
        )
        assert verdict == "running"

    def test_a_wake_already_marked_running_is_not_re_marked(self) -> None:
        verdict = decide_reaction_verdict(
            _candidate(last_run_at=_NOW - timedelta(minutes=1), latest_status="running"),
            now=_NOW,
        )
        assert verdict is None

    def test_a_wake_that_ran_and_never_filed_a_receipt_is_unreported(self) -> None:
        verdict = decide_reaction_verdict(
            _candidate(last_run_at=_NOW - REACTION_GRACE - timedelta(minutes=1)),
            now=_NOW,
        )
        assert verdict == "unreported"

    def test_a_completed_wake_is_never_promoted_to_acted(self) -> None:
        """Positive control: fails the moment a clean completion is read as success."""
        for latest in ("scheduled", "running"):
            verdict = decide_reaction_verdict(
                _candidate(
                    last_run_at=_NOW - REACTION_GRACE - timedelta(minutes=1),
                    latest_status=latest,
                ),
                now=_NOW,
            )
            assert verdict == "unreported"
            assert verdict != "acted"

    def test_a_wake_whose_window_lapsed_without_running_is_unreported(self) -> None:
        verdict = decide_reaction_verdict(
            _candidate(until_at=_NOW - REACTION_GRACE - timedelta(minutes=1)), now=_NOW
        )
        assert verdict == "unreported"

    def test_a_vanished_task_is_unreported_only_after_the_orphan_horizon(self) -> None:
        recent = _candidate(task_id=None, until_at=None, delivered_at=_NOW - timedelta(minutes=1))
        assert decide_reaction_verdict(recent, now=_NOW) is None
        stale = _candidate(
            task_id=None,
            until_at=None,
            delivered_at=_NOW - ORPHAN_WAKE_AFTER - timedelta(minutes=1),
        )
        assert decide_reaction_verdict(stale, now=_NOW) == "unreported"


class TestSweep:
    async def test_the_sweep_reads_only_its_own_subscriber_rows(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        summary = await reconcile_reaction_lifecycle(pool, subscriber_butler="finance", now=_NOW)
        assert summary == {"examined": 0, "running": 0, "unreported": 0}
        sql, *args = pool.fetch.await_args.args
        assert "d.subscriber_butler = $1" in sql
        assert args[0] == "finance"

    async def test_verdicts_are_appended_as_receipts(self, monkeypatch) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                _candidate(last_run_at=_NOW - timedelta(minutes=1)),
                _candidate(
                    event_id=uuid.uuid4(),
                    last_run_at=_NOW - REACTION_GRACE - timedelta(minutes=1),
                ),
            ]
        )
        recorded: list[dict] = []

        async def _fake_record(_pool, **kwargs):
            recorded.append(kwargs)
            return str(uuid.uuid4())

        monkeypatch.setattr(
            "butlers.core.domain_event_reaction_sweep.record_reaction", _fake_record
        )
        summary = await reconcile_reaction_lifecycle(pool, subscriber_butler="finance", now=_NOW)

        assert summary == {"examined": 2, "running": 1, "unreported": 1}
        assert [entry["status"] for entry in recorded] == ["running", "unreported"]
        assert all(entry["session_id"] is None for entry in recorded)

    async def test_a_receipt_that_landed_mid_sweep_wins(self, monkeypatch) -> None:
        """A conflict means a session closed the wake first; the sweep yields."""
        from butlers.core.domain_event_reactions import DomainEventReactionError

        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[_candidate(last_run_at=_NOW - REACTION_GRACE - timedelta(minutes=1))]
        )

        async def _conflict(_pool, **_kwargs):
            raise DomainEventReactionError("already closed")

        monkeypatch.setattr("butlers.core.domain_event_reaction_sweep.record_reaction", _conflict)
        summary = await reconcile_reaction_lifecycle(pool, subscriber_butler="finance", now=_NOW)
        assert summary == {"examined": 1, "running": 0, "unreported": 0}
