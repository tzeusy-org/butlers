"""Unit tests for relationship butler's email_identity_enrichment job (bu-qeaou).

Covers the heuristic gating (bulk/noreply, insufficient evidence), the
idempotency guards (already-linked, already-pending, already-proposed-entity),
and the two proposal shapes (link to an existing entity vs. create a new one),
all against the strict kit pool scripted per candidate. The real SQL (sender scan,
has-email lookup, name match, entity INSERT, park, orphan reap) runs on real
Postgres in tests/integration/test_email_identity_enrichment_db.py.
"""

from __future__ import annotations

import logging
import sys
import uuid
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.testing.approval_parking_fake import record_pending_action
from butlers.testing.fake_pool import fake_pool
from butlers.testing.records import records

_MODULE_KEY = "butlers.jobs._roster.relationship_jobs"


def _get_rjobs() -> ModuleType:
    mod = sys.modules.get(_MODULE_KEY)
    if mod is None:
        from butlers.jobs._roster_loader import load_roster_jobs

        mod = load_roster_jobs("relationship")
    return mod


pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 1, tzinfo=UTC)


def _event_row(
    address: str,
    *,
    thread_id: str,
    day_offset: int,
    display_name: str | None = None,
) -> dict[str, Any]:
    return {
        "source_sender_identity": address,
        "source_sender_display_name": display_name,
        "source_thread_identity": thread_id,
        "received_at": _NOW - timedelta(days=day_offset),
    }


def _recurring_sender_rows(addresses: list[str]) -> list[dict[str, Any]]:
    """Return the minimum recurring evidence for each candidate address."""
    return [
        _event_row(address, thread_id=f"{address}-thread-{thread}", day_offset=thread)
        for address in addresses
        for thread in range(3)
    ]


# Per eligible candidate, the job's reads after the sender scan and has-email lookup:
# fetchval pending-proposal check, fetchval proposed-entity check, then (under the
# cap) fetch name match and, with no match, fetchval placeholder-entity INSERT. Each
# outcome below scripts one candidate's answers; SQL text is never inspected.
_PENDING = "pending"
_PROPOSED = "proposed"
_CREATE = "create"
_CAPPED = "capped"


class EnrichmentRun:
    """A strict kit pool scripted for one enrichment run, plus what it wrote."""

    def __init__(
        self,
        sender_rows: list[dict[str, Any]],
        *,
        linked: set[str],
        outcomes: list[str | uuid.UUID],
    ) -> None:
        fetch: list[Any] = [records(sender_rows)]
        if sender_rows:
            fetch.append(records({"object": address} for address in sorted(linked)))
        fetchval: list[Any] = []
        for outcome in outcomes:
            if outcome == _PENDING:
                fetchval.append(uuid.uuid4())
                continue
            fetchval.append(None)
            if outcome == _PROPOSED:
                fetchval.append(uuid.uuid4())
                continue
            fetchval.append(None)
            if outcome == _CAPPED:
                continue
            if isinstance(outcome, uuid.UUID):  # links to this existing entity
                fetch.append(records([{"id": outcome}]))
            else:
                fetch.append(records([]))
                fetchval.append(uuid.uuid4())
        self.pool = fake_pool(
            fetch=AsyncMock(side_effect=fetch),
            fetchval=AsyncMock(side_effect=fetchval),
            execute=AsyncMock(return_value="INSERT 0 1"),
        )

    @property
    def created_entities(self) -> list[tuple[str, str]]:
        """``(display_name, address)`` of each placeholder-entity INSERT (two bound args)."""
        return [
            (call.args[1], call.args[2])
            for call in self.pool.fetchval.await_args_list
            if len(call.args) == 3
        ]

    @property
    def inserted_actions(self) -> list[dict[str, Any]]:
        """Each parked pending action, from ``record_pending_action``'s INSERT args."""
        return [
            {"tool_name": call.args[2], "tool_args": call.args[3]}
            for call in self.pool.execute.await_args_list
        ]


@pytest.fixture
def enrichment_run():
    """Build an :class:`EnrichmentRun` with the narrow park recorder registered on it."""
    import butlers.core.approvals_hooks as _hooks
    from butlers.modules.approvals.email_guard import (
        check_email_recipient,
        check_recipient,
    )

    registrations = []

    def _build(
        sender_rows: list[dict[str, Any]],
        *,
        linked: set[str] | None = None,
        outcomes: list[str | uuid.UUID] | None = None,
    ) -> EnrichmentRun:
        run = EnrichmentRun(sender_rows, linked=linked or set(), outcomes=outcomes or [])
        runtime = _hooks.register_approval_hooks(
            run.pool,
            email_guard=check_email_recipient,
            recipient_guard=check_recipient,
            park_pending_action=record_pending_action,
        )
        registrations.append((run.pool, runtime))
        return run

    yield _build
    for pool, runtime in reversed(registrations):
        _hooks.unregister_approval_hooks(pool, runtime)


@pytest.fixture(autouse=True)
def _no_orphan_reap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The orphan reap is real-Postgres-covered in tests/integration; skip it here."""

    async def no_orphans(_pool: Any) -> int:
        return 0

    monkeypatch.setattr(_get_rjobs(), "archive_rejected_identity_enrichment_orphans", no_orphans)


@pytest.fixture(autouse=True)
def _patch_insight_and_state(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Patch propose_insight_candidate (no-op accept) and state_set (no-op)."""
    proposed: list[dict[str, Any]] = []

    async def fake_propose_insight_candidate(_pool: Any, **kwargs: Any) -> dict[str, str]:
        proposed.append(kwargs)
        return {"status": "accepted"}

    broker = import_module("butlers.tools.switchboard.insight.broker")
    monkeypatch.setattr(broker, "propose_insight_candidate", fake_propose_insight_candidate)

    async def fake_state_set(*_args: Any, **_kwargs: Any) -> None:
        return None

    rjobs = _get_rjobs()
    monkeypatch.setattr(rjobs, "state_set", fake_state_set)

    return proposed


@pytest.mark.asyncio
async def test_no_senders_is_a_noop(enrichment_run) -> None:
    rjobs = _get_rjobs()
    run = enrichment_run(sender_rows=[])

    result = await rjobs.run_email_identity_enrichment(run.pool)

    assert result["senders_scanned"] == 0
    assert result["created_new"] == 0
    assert result["linked_existing"] == 0


@pytest.mark.asyncio
async def test_bulk_address_is_filtered_out(enrichment_run) -> None:
    rjobs = _get_rjobs()
    run = enrichment_run(
        sender_rows=[
            _event_row("noreply@example.com", thread_id="t1", day_offset=0),
            _event_row("noreply@example.com", thread_id="t2", day_offset=1),
            _event_row("noreply@example.com", thread_id="t3", day_offset=2),
        ]
    )

    result = await rjobs.run_email_identity_enrichment(run.pool)

    assert result["filtered_bulk"] == 1
    assert result["created_new"] == 0
    assert run.inserted_actions == []


@pytest.mark.asyncio
async def test_insufficient_thread_evidence_is_skipped(enrichment_run) -> None:
    rjobs = _get_rjobs()
    # Only 1 distinct thread, below the 3-thread minimum.
    run = enrichment_run(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t1", day_offset=1),
        ]
    )

    result = await rjobs.run_email_identity_enrichment(run.pool)

    assert result["insufficient_evidence"] == 1
    assert run.inserted_actions == []


@pytest.mark.asyncio
async def test_already_linked_address_is_skipped(enrichment_run) -> None:
    rjobs = _get_rjobs()
    run = enrichment_run(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t2", day_offset=1),
            _event_row("john.doe@example.com", thread_id="t3", day_offset=2),
        ],
        linked={"john.doe@example.com"},
    )

    result = await rjobs.run_email_identity_enrichment(run.pool)

    assert result["already_linked"] == 1
    assert run.inserted_actions == []


@pytest.mark.asyncio
async def test_creates_new_entity_and_proposes_link_when_no_match(
    enrichment_run,
    _patch_insight_and_state: list[dict[str, Any]],
) -> None:
    rjobs = _get_rjobs()
    run = enrichment_run(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t2", day_offset=1),
            _event_row("john.doe@example.com", thread_id="t3", day_offset=2),
        ],
        outcomes=[_CREATE],
    )

    result = await rjobs.run_email_identity_enrichment(run.pool)

    assert result["created_new"] == 1
    assert result["linked_existing"] == 0
    assert len(run.created_entities) == 1
    assert run.created_entities[0] == ("John Doe", "john.doe@example.com")

    assert len(run.inserted_actions) == 1
    action = run.inserted_actions[0]
    assert action["tool_name"] == "relationship_assert_fact"
    assert action["tool_args"]["predicate"] == "has-email"
    assert action["tool_args"]["object"] == "john.doe@example.com"
    assert action["tool_args"]["object_kind"] == "literal"

    assert len(_patch_insight_and_state) == 1
    assert "john.doe@example.com" in _patch_insight_and_state[0]["message"]


@pytest.mark.asyncio
async def test_prefers_stored_display_name_over_local_part(
    enrichment_run,
    _patch_insight_and_state: list[dict[str, Any]],
) -> None:
    """When a stored From: display name exists, the proposed entity uses it (bu-vs9cr)."""
    rjobs = _get_rjobs()
    run = enrichment_run(
        sender_rows=[
            _event_row(
                "hsbc.bank.singapore@example.com",
                thread_id="t1",
                day_offset=0,
                display_name="Alice Tan",
            ),
            _event_row("hsbc.bank.singapore@example.com", thread_id="t2", day_offset=1),
            _event_row("hsbc.bank.singapore@example.com", thread_id="t3", day_offset=2),
        ],
        outcomes=[_CREATE],
    )

    result = await rjobs.run_email_identity_enrichment(run.pool)

    assert result["created_new"] == 1
    # Real stored name is used, NOT the local-part guess "Hsbc Bank Singapore".
    assert run.created_entities[0] == ("Alice Tan", "hsbc.bank.singapore@example.com")


@pytest.mark.asyncio
async def test_falls_back_to_local_part_when_no_stored_name(
    enrichment_run,
    _patch_insight_and_state: list[dict[str, Any]],
) -> None:
    """Legacy rows with no stored name still derive the name from the local-part."""
    rjobs = _get_rjobs()
    run = enrichment_run(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t2", day_offset=1),
            _event_row("john.doe@example.com", thread_id="t3", day_offset=2),
        ],
        outcomes=[_CREATE],
    )

    result = await rjobs.run_email_identity_enrichment(run.pool)

    assert result["created_new"] == 1
    assert run.created_entities[0] == ("John Doe", "john.doe@example.com")


@pytest.mark.asyncio
async def test_links_to_existing_entity_when_name_matches(enrichment_run) -> None:
    rjobs = _get_rjobs()
    existing_id = uuid.uuid4()
    run = enrichment_run(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t2", day_offset=1),
            _event_row("john.doe@example.com", thread_id="t3", day_offset=2),
        ],
        outcomes=[existing_id],
    )

    result = await rjobs.run_email_identity_enrichment(run.pool)

    assert result["linked_existing"] == 1
    assert result["created_new"] == 0
    assert run.created_entities == []

    action = run.inserted_actions[0]
    assert action["tool_args"]["subject"] == str(existing_id)


@pytest.mark.asyncio
async def test_already_pending_proposal_is_not_duplicated(enrichment_run) -> None:
    rjobs = _get_rjobs()
    run = enrichment_run(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t2", day_offset=1),
            _event_row("john.doe@example.com", thread_id="t3", day_offset=2),
        ],
        outcomes=[_PENDING],
    )

    result = await rjobs.run_email_identity_enrichment(run.pool)

    assert result["already_pending"] == 1
    assert run.inserted_actions == []
    assert run.created_entities == []


@pytest.mark.asyncio
async def test_already_proposed_entity_is_not_recreated(enrichment_run) -> None:
    """A previously created-then-rejected proposal must not spawn a duplicate entity."""
    rjobs = _get_rjobs()
    run = enrichment_run(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t2", day_offset=1),
            _event_row("john.doe@example.com", thread_id="t3", day_offset=2),
        ],
        outcomes=[_PROPOSED],
    )

    result = await rjobs.run_email_identity_enrichment(run.pool)

    assert result["already_proposed"] == 1
    assert run.inserted_actions == []
    assert run.created_entities == []


@pytest.mark.asyncio
async def test_proposes_exactly_the_per_run_cap_without_truncation(
    enrichment_run,
    _patch_insight_and_state: list[dict[str, Any]],
) -> None:
    """Exactly a full run is complete; only a surplus candidate marks truncation."""
    rjobs = _get_rjobs()
    addresses = [f"person{index}@example.com" for index in range(10)]
    run = enrichment_run(_recurring_sender_rows(addresses), outcomes=[_CREATE] * 10)

    result = await rjobs.run_email_identity_enrichment(run.pool)

    assert result["created_new"] == 10
    assert result["linked_existing"] == 0
    assert result["proposal_cap_truncated"] is False
    assert result["truncated"] is False
    assert [action["tool_args"]["object"] for action in run.inserted_actions] == addresses
    assert len(_patch_insight_and_state) == 10


@pytest.mark.asyncio
async def test_stops_at_per_run_proposal_cap_and_reports_truncation(
    enrichment_run,
    _patch_insight_and_state: list[dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A surplus eligible sender is neither proposed nor silently discarded."""
    rjobs = _get_rjobs()
    addresses = [f"person{index}@example.com" for index in range(11)]
    run = enrichment_run(_recurring_sender_rows(addresses), outcomes=[_CREATE] * 10 + [_CAPPED])

    with caplog.at_level(logging.WARNING, logger=rjobs.__name__):
        result = await rjobs.run_email_identity_enrichment(run.pool)

    assert result["created_new"] == 10
    assert result["linked_existing"] == 0
    assert result["proposal_cap_truncated"] is True
    assert result["truncated"] is True
    assert [action["tool_args"]["object"] for action in run.inserted_actions] == addresses[:10]
    assert len(_patch_insight_and_state) == 10
    assert "proposal cap=10 reached" in caplog.text
