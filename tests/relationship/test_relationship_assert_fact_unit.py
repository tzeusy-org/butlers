"""Unit-level regressions for relationship_assert_fact internals."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from butlers.tools.relationship.relationship_assert_fact import (
    AssertOutcome,
    _assert_on_conn,
    _create_pending_action,
    _upsert_fact,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_PRED_HAS_EMAIL = "has-email"


async def test_supersession_insert_is_conflict_safe() -> None:
    """Concurrent supersession must not use a plain insert for the new active row."""
    old_id = uuid.uuid4()
    new_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "id": old_id,
            "src": "source-a",
            "conf": 1.0,
            "verified": False,
            "last_seen": None,
        }
    )
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.fetchval = AsyncMock(return_value=new_id)

    result = await _upsert_fact(
        conn,
        subject=uuid.uuid4(),
        predicate=_PRED_HAS_EMAIL,
        object="alice@example.com",
        object_kind="literal",
        src="source-b",
        conf=1.0,
        last_seen=None,
        weight=None,
        verified=False,
        primary=None,
    )

    insert_sql = conn.fetchval.call_args.args[0]
    assert result.outcome == AssertOutcome.superseded
    assert "ON CONFLICT (subject, predicate, object)" in insert_sql
    assert "WHERE validity = 'active'" in insert_sql
    assert "DO UPDATE" in insert_sql


# ---------------------------------------------------------------------------
# Owner carve-out: dedup + rationale (regression for duplicate reconciler
# approvals appearing every 30 min in the dashboard with blank why/evidence).
# ---------------------------------------------------------------------------


async def test_create_pending_action_reuses_existing_pending_row() -> None:
    """When dedup_match finds a pending row, no new INSERT and same id returned."""
    existing_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=existing_id)
    conn.execute = AsyncMock()

    returned = await _create_pending_action(
        conn,
        "relationship_assert_fact",
        {"subject": "s", "predicate": "p", "object": "o"},
        "summary",
        dedup_match={"subject": "s", "predicate": "p", "object": "o"},
        why="why",
        evidence=["a", "b"],
    )

    assert returned == existing_id
    # The dedup probe ran, but no INSERT followed.
    probe_sql = conn.fetchval.call_args.args[0]
    assert "SELECT id FROM pending_actions" in probe_sql
    assert "tool_args @> $2::jsonb" in probe_sql
    conn.execute.assert_not_called()


async def test_create_pending_action_inserts_with_why_and_evidence() -> None:
    """When no pending duplicate exists, INSERT includes why and evidence columns."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)  # no existing pending row
    conn.execute = AsyncMock()

    returned = await _create_pending_action(
        conn,
        "relationship_assert_fact",
        {"subject": "s", "predicate": "p", "object": "o"},
        "summary",
        dedup_match={"subject": "s", "predicate": "p", "object": "o"},
        why="human-readable rationale",
        evidence=["ci_id=123", "contact_id=456"],
    )

    assert isinstance(returned, uuid.UUID)
    insert_sql = conn.execute.call_args.args[0]
    assert "INSERT INTO pending_actions" in insert_sql
    assert "why" in insert_sql
    assert "evidence" in insert_sql

    insert_params = conn.execute.call_args.args[1:]
    # Order: (id, tool_name, tool_args, summary, session_id, status,
    #         requested_at, expires_at, why, evidence)
    assert insert_params[1] == "relationship_assert_fact"
    assert insert_params[5] == "pending"
    assert insert_params[8] == "human-readable rationale"
    assert insert_params[9] == ["ci_id=123", "contact_id=456"]


async def test_owner_carveout_passes_dedup_match_and_rationale() -> None:
    """The owner branch of _assert_on_conn must dedup on identity and populate why/evidence."""
    subject_id = uuid.uuid4()
    existing_action_id = uuid.uuid4()

    conn = AsyncMock()

    # Sequence of fetchrow/fetchval calls in _assert_on_conn (owner branch):
    #  1. _validate_predicate: fetchval -> True (predicate is registered)
    #  2. _is_owner_entity:    fetchrow -> {"roles": ["owner"]}
    #  3. _create_pending_action dedup probe: fetchval -> existing_action_id (dedup HIT)
    conn.fetchval = AsyncMock(side_effect=[True, existing_action_id])
    conn.fetchrow = AsyncMock(return_value={"roles": ["owner"]})
    conn.execute = AsyncMock()

    result = await _assert_on_conn(
        conn,
        subject=subject_id,
        predicate=_PRED_HAS_EMAIL,
        object="owner@example.com",
        object_kind="literal",
        src="reconciler",
        conf=1.0,
        last_seen=None,
        weight=None,
        verified=False,
        primary=True,
        wrap_transaction=False,
        why="reconciler rationale",
        evidence=["ci_id=abc"],
    )

    assert result.outcome == AssertOutcome.pending_approval
    assert result.action_id == existing_action_id
    assert result.fact_id is None
    # Dedup HIT means no INSERT (only the validate + dedup probe queries ran).
    conn.execute.assert_not_called()

    # The dedup probe should have matched on the identity triple, not on
    # provenance fields. Inspect the JSONB probe argument.
    dedup_call_args = conn.fetchval.call_args_list[1].args
    probe_jsonb = dedup_call_args[2]
    assert probe_jsonb["subject"] == str(subject_id)
    assert probe_jsonb["predicate"] == _PRED_HAS_EMAIL
    assert probe_jsonb["object"] == "owner@example.com"
    assert probe_jsonb["object_kind"] == "literal"
    # Provenance fields must NOT be in the probe — otherwise two reconciler
    # runs with different `conf` would each create their own pending row.
    assert "src" not in probe_jsonb
    assert "conf" not in probe_jsonb


async def test_owner_carveout_inserts_with_caller_supplied_why() -> None:
    """When no pending duplicate exists, the carve-out INSERT carries the caller's why/evidence."""
    subject_id = uuid.uuid4()

    conn = AsyncMock()
    # fetchval sequence: predicate-registered=True, dedup probe miss=None.
    conn.fetchval = AsyncMock(side_effect=[True, None])
    conn.fetchrow = AsyncMock(return_value={"roles": ["owner"]})
    conn.execute = AsyncMock()

    caller_why = "The contact-info reconciler found a missing has-email triple."
    caller_evidence = ["ci_id=xyz", "contact_id=789", "is_primary=True"]

    result = await _assert_on_conn(
        conn,
        subject=subject_id,
        predicate=_PRED_HAS_EMAIL,
        object="owner@example.com",
        object_kind="literal",
        src="reconciler",
        conf=1.0,
        last_seen=None,
        weight=None,
        verified=False,
        primary=True,
        wrap_transaction=False,
        why=caller_why,
        evidence=caller_evidence,
    )

    assert result.outcome == AssertOutcome.pending_approval
    insert_params = conn.execute.call_args.args[1:]
    # (id, tool_name, tool_args, summary, session_id, status,
    #  requested_at, expires_at, why, evidence)
    assert insert_params[8] == caller_why
    assert insert_params[9] == caller_evidence
