"""Unit tests for relationship butler's email_identity_enrichment job (bu-qeaou).

Covers the heuristic gating (bulk/noreply, insufficient evidence), the
idempotency guards (already-linked, already-pending, already-proposed-entity),
and the two proposal shapes (link to an existing entity vs. create a new one),
all against a mocked pool.
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import ModuleType
from typing import Any

import pytest

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


class _FakePool:
    """Routes fetch/fetchval/execute calls by SQL shape (mirrors the job's real queries)."""

    def __init__(
        self,
        *,
        sender_rows: list[dict[str, Any]],
        has_email_addresses: set[str] | None = None,
        name_match_ids: dict[str, uuid.UUID] | None = None,
        already_pending_addresses: set[str] | None = None,
        already_proposed_addresses: set[str] | None = None,
    ) -> None:
        self._sender_rows = sender_rows
        self._has_email_addresses = has_email_addresses or set()
        self._name_match_ids = name_match_ids or {}
        self._already_pending = already_pending_addresses or set()
        self._already_proposed = already_proposed_addresses or set()
        self.inserted_actions: list[dict[str, Any]] = []
        self.created_entities: list[tuple[str, str]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM public.ingestion_events" in query:
            return self._sender_rows
        if "FROM relationship.entity_facts" in query and "object = ANY" in query:
            wanted = set(args[0])
            return [{"object": a} for a in wanted if a in self._has_email_addresses]
        if "FROM public.entities" in query and "ILIKE" in query:
            display_name = args[0]
            eid = self._name_match_ids.get(display_name)
            return [{"id": eid}] if eid is not None else []
        raise AssertionError(f"unexpected fetch: {query}")

    async def fetchval(self, query: str, *args: Any) -> Any:
        # Order matters: the INSERT statement's jsonb_build_object(...) also
        # contains the literal substring 'proposed_from_address' as a key, so
        # the INSERT check must be tried before the SELECT-by-that-column check.
        if "INSERT INTO public.entities" in query:
            display_name, address = args[0], args[1]
            self.created_entities.append((display_name, address))
            return uuid.uuid4()
        if "SELECT id FROM pending_actions" in query and "predicate" in query:
            address = args[0]
            return uuid.uuid4() if address in self._already_pending else None
        if "SELECT id FROM public.entities" in query and "proposed_from_address" in query:
            address = args[0]
            return uuid.uuid4() if address in self._already_proposed else None
        raise AssertionError(f"unexpected fetchval: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO pending_actions" in query:
            self.inserted_actions.append(
                {
                    "tool_name": args[1],
                    "tool_args": args[2],
                    "why": args[8],
                    "evidence": args[9],
                }
            )
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {query}")


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
async def test_no_senders_is_a_noop() -> None:
    rjobs = _get_rjobs()
    pool = _FakePool(sender_rows=[])

    result = await rjobs.run_email_identity_enrichment(pool)

    assert result["senders_scanned"] == 0
    assert result["created_new"] == 0
    assert result["linked_existing"] == 0


@pytest.mark.asyncio
async def test_bulk_address_is_filtered_out() -> None:
    rjobs = _get_rjobs()
    pool = _FakePool(
        sender_rows=[
            _event_row("noreply@example.com", thread_id="t1", day_offset=0),
            _event_row("noreply@example.com", thread_id="t2", day_offset=1),
            _event_row("noreply@example.com", thread_id="t3", day_offset=2),
        ]
    )

    result = await rjobs.run_email_identity_enrichment(pool)

    assert result["filtered_bulk"] == 1
    assert result["created_new"] == 0
    assert pool.inserted_actions == []


@pytest.mark.asyncio
async def test_insufficient_thread_evidence_is_skipped() -> None:
    rjobs = _get_rjobs()
    # Only 1 distinct thread, below the 3-thread minimum.
    pool = _FakePool(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t1", day_offset=1),
        ]
    )

    result = await rjobs.run_email_identity_enrichment(pool)

    assert result["insufficient_evidence"] == 1
    assert pool.inserted_actions == []


@pytest.mark.asyncio
async def test_already_linked_address_is_skipped() -> None:
    rjobs = _get_rjobs()
    pool = _FakePool(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t2", day_offset=1),
            _event_row("john.doe@example.com", thread_id="t3", day_offset=2),
        ],
        has_email_addresses={"john.doe@example.com"},
    )

    result = await rjobs.run_email_identity_enrichment(pool)

    assert result["already_linked"] == 1
    assert pool.inserted_actions == []


@pytest.mark.asyncio
async def test_creates_new_entity_and_proposes_link_when_no_match(
    _patch_insight_and_state: list[dict[str, Any]],
) -> None:
    rjobs = _get_rjobs()
    pool = _FakePool(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t2", day_offset=1),
            _event_row("john.doe@example.com", thread_id="t3", day_offset=2),
        ]
    )

    result = await rjobs.run_email_identity_enrichment(pool)

    assert result["created_new"] == 1
    assert result["linked_existing"] == 0
    assert len(pool.created_entities) == 1
    assert pool.created_entities[0] == ("John Doe", "john.doe@example.com")

    assert len(pool.inserted_actions) == 1
    action = pool.inserted_actions[0]
    assert action["tool_name"] == "relationship_assert_fact"
    assert action["tool_args"]["predicate"] == "has-email"
    assert action["tool_args"]["object"] == "john.doe@example.com"
    assert action["tool_args"]["object_kind"] == "literal"

    assert len(_patch_insight_and_state) == 1
    assert "john.doe@example.com" in _patch_insight_and_state[0]["message"]


@pytest.mark.asyncio
async def test_prefers_stored_display_name_over_local_part(
    _patch_insight_and_state: list[dict[str, Any]],
) -> None:
    """When a stored From: display name exists, the proposed entity uses it (bu-vs9cr)."""
    rjobs = _get_rjobs()
    pool = _FakePool(
        sender_rows=[
            _event_row(
                "hsbc.bank.singapore@example.com",
                thread_id="t1",
                day_offset=0,
                display_name="Alice Tan",
            ),
            _event_row("hsbc.bank.singapore@example.com", thread_id="t2", day_offset=1),
            _event_row("hsbc.bank.singapore@example.com", thread_id="t3", day_offset=2),
        ]
    )

    result = await rjobs.run_email_identity_enrichment(pool)

    assert result["created_new"] == 1
    # Real stored name is used, NOT the local-part guess "Hsbc Bank Singapore".
    assert pool.created_entities[0] == ("Alice Tan", "hsbc.bank.singapore@example.com")


@pytest.mark.asyncio
async def test_falls_back_to_local_part_when_no_stored_name(
    _patch_insight_and_state: list[dict[str, Any]],
) -> None:
    """Legacy rows with no stored name still derive the name from the local-part."""
    rjobs = _get_rjobs()
    pool = _FakePool(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t2", day_offset=1),
            _event_row("john.doe@example.com", thread_id="t3", day_offset=2),
        ]
    )

    result = await rjobs.run_email_identity_enrichment(pool)

    assert result["created_new"] == 1
    assert pool.created_entities[0] == ("John Doe", "john.doe@example.com")


@pytest.mark.asyncio
async def test_links_to_existing_entity_when_name_matches() -> None:
    rjobs = _get_rjobs()
    existing_id = uuid.uuid4()
    pool = _FakePool(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t2", day_offset=1),
            _event_row("john.doe@example.com", thread_id="t3", day_offset=2),
        ],
        name_match_ids={"John Doe": existing_id},
    )

    result = await rjobs.run_email_identity_enrichment(pool)

    assert result["linked_existing"] == 1
    assert result["created_new"] == 0
    assert pool.created_entities == []

    action = pool.inserted_actions[0]
    assert action["tool_args"]["subject"] == str(existing_id)


@pytest.mark.asyncio
async def test_already_pending_proposal_is_not_duplicated() -> None:
    rjobs = _get_rjobs()
    pool = _FakePool(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t2", day_offset=1),
            _event_row("john.doe@example.com", thread_id="t3", day_offset=2),
        ],
        already_pending_addresses={"john.doe@example.com"},
    )

    result = await rjobs.run_email_identity_enrichment(pool)

    assert result["already_pending"] == 1
    assert pool.inserted_actions == []
    assert pool.created_entities == []


@pytest.mark.asyncio
async def test_already_proposed_entity_is_not_recreated() -> None:
    """A previously created-then-rejected proposal must not spawn a duplicate entity."""
    rjobs = _get_rjobs()
    pool = _FakePool(
        sender_rows=[
            _event_row("john.doe@example.com", thread_id="t1", day_offset=0),
            _event_row("john.doe@example.com", thread_id="t2", day_offset=1),
            _event_row("john.doe@example.com", thread_id="t3", day_offset=2),
        ],
        already_proposed_addresses={"john.doe@example.com"},
    )

    result = await rjobs.run_email_identity_enrichment(pool)

    assert result["already_proposed"] == 1
    assert pool.inserted_actions == []
    assert pool.created_entities == []
