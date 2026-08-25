"""Append-only reaction receipts for a fanned-out domain event (bu-6jv4m.8).

Mocked-pool style mirroring tests/core/test_domain_event_wake.py. These pin
the vocabulary, the typed-evidence validation, and the "a wake closes
exactly once" conflict boundary. Real-Postgres coverage of the unique
partial index lives in tests/integration/test_domain_event_bus_roundtrip.py.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.core.domain_event_reactions import (
    REACTION_STATUSES,
    REPORTABLE_REACTION_STATUSES,
    TERMINAL_REACTION_STATUSES,
    DomainEventReactionError,
    has_terminal_reaction,
    is_terminal_reaction,
    latest_reaction_for,
    list_reactions_for_event,
    record_reaction,
    validate_evidence,
)

pytestmark = pytest.mark.unit

_EVENT_ID = "11111111-1111-1111-1111-111111111111"


def _pool(*, fetchval=None, fetchrow=None, fetch=None) -> AsyncMock:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=fetchval)
    pool.fetchrow = AsyncMock(return_value=fetchrow)
    pool.fetch = AsyncMock(return_value=fetch if fetch is not None else [])
    return pool


class TestVocabulary:
    def test_the_lifecycle_vocabulary_is_exactly_the_declared_one(self) -> None:
        assert REACTION_STATUSES == {
            "scheduled",
            "running",
            "acted",
            "ignored",
            "deferred",
            "failed",
            "unreported",
        }

    def test_terminal_statuses_are_the_five_outcomes(self) -> None:
        assert TERMINAL_REACTION_STATUSES == {
            "acted",
            "ignored",
            "deferred",
            "failed",
            "unreported",
        }
        assert is_terminal_reaction("acted") is True
        assert is_terminal_reaction("running") is False

    def test_unreported_is_not_self_reportable(self) -> None:
        """`unreported` is the sweep's verdict about silence, never a claim a session makes."""
        assert "unreported" not in REPORTABLE_REACTION_STATUSES
        assert REPORTABLE_REACTION_STATUSES == {"acted", "ignored", "deferred", "failed"}


class TestEvidenceValidation:
    def test_no_evidence_is_valid(self) -> None:
        assert validate_evidence(None) == []
        assert validate_evidence([]) == []

    def test_a_typed_reference_is_accepted(self) -> None:
        evidence = validate_evidence(
            [{"kind": "task", "ref": "pre-budget-trip-42", "label": "scheduled a pre-budget"}]
        )
        assert evidence == [
            {"kind": "task", "ref": "pre-budget-trip-42", "label": "scheduled a pre-budget"}
        ]

    def test_evidence_must_be_a_list(self) -> None:
        with pytest.raises(DomainEventReactionError, match="list"):
            validate_evidence({"kind": "task", "ref": "x"})

    def test_an_untyped_reference_is_refused(self) -> None:
        with pytest.raises(DomainEventReactionError, match="kind"):
            validate_evidence([{"ref": "x"}])

    def test_an_unknown_evidence_kind_is_refused(self) -> None:
        with pytest.raises(DomainEventReactionError, match="vibes"):
            validate_evidence([{"kind": "vibes", "ref": "x"}])

    def test_an_empty_reference_is_refused(self) -> None:
        with pytest.raises(DomainEventReactionError, match="ref"):
            validate_evidence([{"kind": "task", "ref": "   "}])

    def test_an_undeclared_evidence_key_is_refused(self) -> None:
        with pytest.raises(DomainEventReactionError, match="payload"):
            validate_evidence([{"kind": "task", "ref": "x", "payload": {"a": 1}}])


class TestRecordReaction:
    async def test_an_unknown_status_is_refused_before_any_write(self) -> None:
        pool = _pool()
        with pytest.raises(DomainEventReactionError, match="probably_fine"):
            await record_reaction(
                pool,
                event_id=_EVENT_ID,
                subscriber_butler="finance",
                status="probably_fine",
            )
        pool.fetchval.assert_not_awaited()

    async def test_invalid_evidence_is_refused_before_any_write(self) -> None:
        pool = _pool()
        with pytest.raises(DomainEventReactionError):
            await record_reaction(
                pool,
                event_id=_EVENT_ID,
                subscriber_butler="finance",
                status="acted",
                evidence=[{"kind": "nope", "ref": "x"}],
            )
        pool.fetchval.assert_not_awaited()

    async def test_a_receipt_is_appended_with_its_session_and_evidence(self) -> None:
        reaction_id = uuid.uuid4()
        pool = _pool(fetchval=reaction_id)

        recorded = await record_reaction(
            pool,
            event_id=_EVENT_ID,
            subscriber_butler="finance",
            status="acted",
            session_id="session-abc",
            task_name="domain-event-x-finance",
            note="Opened a pre-budget for the trip.",
            evidence=[{"kind": "task", "ref": "pre-budget-trip-42"}],
        )

        assert recorded == str(reaction_id)
        sql, *args = pool.fetchval.await_args.args
        assert "INSERT INTO public.domain_event_reactions" in sql
        assert args[0] == uuid.UUID(_EVENT_ID)
        assert args[1] == "finance"
        assert args[2] == "acted"
        assert args[3] == "session-abc"
        assert args[4] == "domain-event-x-finance"
        assert json.loads(args[6]) == [{"kind": "task", "ref": "pre-budget-trip-42"}]

    async def test_a_second_terminal_receipt_for_one_wake_fails_closed(self) -> None:
        pool = _pool()
        pool.fetchval = AsyncMock(side_effect=asyncpg.UniqueViolationError("duplicate key"))

        with pytest.raises(DomainEventReactionError, match="already closed"):
            await record_reaction(
                pool,
                event_id=_EVENT_ID,
                subscriber_butler="finance",
                status="ignored",
            )

    async def test_an_in_flight_step_does_not_close_the_wake(self) -> None:
        """Positive control: this fails if an in-flight step is ever treated as terminal."""
        pool = _pool(fetchval=None)
        assert (
            await has_terminal_reaction(pool, event_id=_EVENT_ID, subscriber_butler="finance")
        ) is False
        _, _, _, statuses = pool.fetchval.await_args.args
        assert set(statuses) == TERMINAL_REACTION_STATUSES
        assert "running" not in statuses
        assert "scheduled" not in statuses


class TestReaders:
    async def test_latest_reaction_is_none_when_nothing_was_recorded(self) -> None:
        pool = _pool(fetchrow=None)
        assert (
            await latest_reaction_for(pool, event_id=_EVENT_ID, subscriber_butler="finance")
        ) is None

    async def test_latest_reaction_reads_the_newest_row_for_the_pair(self) -> None:
        pool = _pool(fetchrow={"id": uuid.uuid4(), "status": "acted"})
        latest = await latest_reaction_for(pool, event_id=_EVENT_ID, subscriber_butler="finance")
        assert latest is not None
        assert latest["status"] == "acted"
        sql, *args = pool.fetchrow.await_args.args
        assert "ORDER BY recorded_at DESC" in sql
        assert args == [uuid.UUID(_EVENT_ID), "finance"]

    async def test_the_full_trace_for_an_event_is_ordered_oldest_first(self) -> None:
        pool = _pool(fetch=[{"status": "scheduled"}, {"status": "acted"}])
        trace = await list_reactions_for_event(pool, event_id=_EVENT_ID)
        assert [row["status"] for row in trace] == ["scheduled", "acted"]
        sql, *_ = pool.fetch.await_args.args
        assert "ORDER BY recorded_at ASC" in sql
