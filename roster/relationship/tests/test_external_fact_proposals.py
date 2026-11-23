"""External MCP fact proposals: third-party provenance through park, approval and replay.

REQ-relationship-facts-011 (every scenario) and the Relationship half of
REQ-module-approvals-010 ("A matching standing rule does not approve an
external proposal" with its positive control, and "Owner decision uses the
shared executor"). The importer parks through
``tools/external_fact_proposals.py`` and records the proposal row; the owner
approves through the shared approvals operation bound to the call the typed
dashboard review displayed (REQ-dashboard-approvals-904,
``external_review_fixtures``); the approvals module dispatches the stored call to the
registered ``relationship_assert_fact`` tool with no model session. The
REQ-relationship-facts-005 replay rules apply unchanged.

The owner decision context is the synthetic ``owner_device`` fixture
(``fact_authority_fixtures.synthetic_owner``), standing in for the dashboard's
OwnerAuth admission; it is not authentication evidence.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from fastmcp import FastMCP

from butlers.modules.approvals import operations
from butlers.modules.approvals.external_proposals import (
    EXTERNAL_PROPOSAL_REVIEW_ONLY,
    ExternalReviewBinding,
)
from butlers.modules.approvals.gate import admit_standing_rule
from butlers.modules.approvals.module import ApprovalsModule
from butlers.modules.approvals.previews import args_digest
from butlers.roster_imports import roster_module
from roster.relationship.tests.external_review_fixtures import (
    dashboard_review_binding,
    record_external_proposal,
)
from roster.relationship.tests.fact_authority_fixtures import synthetic_owner

external = roster_module("relationship", "tools/external_fact_proposals")
fact_authority = roster_module("relationship", "tools/fact_authority")
relationship_modules = roster_module("relationship", "modules")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_RECEIVED_AT = datetime(2026, 11, 1, 8, 30, tzinfo=UTC)


@pytest.fixture
async def rel_pool(db):
    """Core + memory + relationship + approvals chains (flat; entity_facts in ``relationship``)."""
    return await db("core", "memory", "relationship", "approvals")


async def _entity(pool: asyncpg.Pool, *, roles: list[str] | None = None) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Synthetic person ' || gen_random_uuid(), 'person', $1) RETURNING id",
        roles or [],
    )


@pytest.fixture
async def people(rel_pool):
    await _entity(rel_pool, roles=["owner"])
    return SimpleNamespace(
        a=await _entity(rel_pool), b=await _entity(rel_pool), c=await _entity(rel_pool)
    )


async def _park(pool, people, *, proposal_id=None, conn=None):
    proposal_id = proposal_id or uuid.uuid4()

    async def run(connection):
        admission = await external.park_external_fact_proposal(
            connection,
            proposal_id=proposal_id,
            subject=people.a,
            predicate="works-at",
            object_entity_id=people.b,
            client_label="synthetic-client",
            evidence_url="https://example.com/evidence",
            received_at=_RECEIVED_AT,
        )
        await record_external_proposal(
            connection,
            proposal_id=proposal_id,
            action_id=admission.action_id,
            subject=people.a,
            object_entity_id=people.b,
            received_at=_RECEIVED_AT,
            evidence_url="https://example.com/evidence",
        )
        return admission

    if conn is not None:
        return proposal_id, await run(conn)
    async with pool.acquire() as acquired:
        return proposal_id, await run(acquired)


async def _approve(pool, action_id: uuid.UUID, *, edits: dict | None = None) -> dict:
    """The owner's dashboard decision, bound to the call its typed review displayed.

    *edits* change the stored call after the review was shown, which the bound
    approve operation must refuse.
    """
    async with synthetic_owner(pool), pool.acquire() as conn, conn.transaction():
        binding = await dashboard_review_binding(conn, action_id)
        if edits:
            args = await conn.fetchval(
                "SELECT tool_args FROM pending_actions WHERE id=$1", action_id
            )
            await conn.execute(
                "UPDATE pending_actions SET tool_args=$2 WHERE id=$1", action_id, args | edits
            )
        return await operations.approve_action(conn, str(action_id), external_review=binding)


async def _dispatch(pool, action_id: uuid.UUID) -> dict:
    """The owning daemon's shared executor, resolving the registered tool by name."""
    relationship = relationship_modules.RelationshipModule()
    mcp = FastMCP("relationship")
    await relationship.register_tools(mcp, None, SimpleNamespace(pool=pool), "relationship")
    approvals = ApprovalsModule()
    await approvals.on_startup({}, pool)

    async def execute(tool_name: str, tool_args: dict):
        tool: Any = await mcp.get_tool(tool_name)
        handler: Any = tool.fn
        return await handler(**tool_args)

    approvals._tool_executor = execute
    approvals._butler_name = "relationship"
    try:
        return await approvals._dispatch_approved_action_by_id(str(action_id))
    finally:
        await approvals.on_shutdown()


async def _context(pool, action_id):
    return await pool.fetchrow(
        "SELECT * FROM relationship.fact_approval_context WHERE action_id=$1", action_id
    )


async def _facts(pool, subject):
    return await pool.fetch(
        "SELECT * FROM relationship.entity_facts WHERE subject=$1 AND validity='active'", subject
    )


# REQ-relationship-facts-011: the importer records src, observed_at and a third-party
# frozen report server-side, never in the stored tool arguments.
async def test_park_records_third_party_context_outside_tool_args(rel_pool, people) -> None:
    proposal_id, admission = await _park(rel_pool, people)
    assert admission.duplicate is False

    context = await _context(rel_pool, admission.action_id)
    assert context["src"] == f"external_mcp:{proposal_id}"
    assert context["observed_at"] == _RECEIVED_AT
    assert context["frozen_report"]["authority"] == "third_party"
    assert context["decision_report"] is None
    action = await rel_pool.fetchrow(
        "SELECT * FROM pending_actions WHERE id=$1", admission.action_id
    )
    assert action["origin"] is None and action["approval_rule_id"] is None
    assert action["deduplication_key"] == f"external_mcp:{proposal_id}"
    args = action["tool_args"]
    assert not {"src", "content_authority", "observed_at", "verified"} & set(args)
    assert (args["subject"], args["predicate"], args["object"], args["object_kind"]) == (
        str(people.a),
        "works-at",
        str(people.b),
        "entity",
    )
    assert args["approval_action_id"] == str(admission.action_id)

    # Re-import: no second action and no second context.
    _, again = await _park(rel_pool, people, proposal_id=proposal_id)
    assert again.duplicate is True and again.action_id == admission.action_id
    assert await rel_pool.fetchval("SELECT count(*) FROM relationship.fact_approval_context") == 1


async def test_park_and_context_roll_back_with_the_callers_transaction(rel_pool, people) -> None:
    async with rel_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="importer failed"):
            async with conn.transaction():
                await _park(rel_pool, people, conn=conn)
                raise RuntimeError("importer failed before recording proposal state")
    assert await rel_pool.fetchval("SELECT count(*) FROM pending_actions") == 0
    assert await rel_pool.fetchval("SELECT count(*) FROM relationship.fact_approval_context") == 0


# REQ-relationship-facts-011: "Approved proposal keeps third-party authority",
# "Owner approval is not refused for third-party authority" and "Provenance comes
# only from the recorded context"; REQ-module-approvals-010: "Owner decision uses
# the shared executor" with no model session.
async def test_owner_approval_writes_verified_third_party_fact(rel_pool, people) -> None:
    proposal_id, admission = await _park(rel_pool, people)

    approved = await _approve(rel_pool, admission.action_id)
    assert "error" not in approved and approved["status"] == "approved"
    # record_admitted_approval accepted the owner decision on a third-party report.
    decision = (await _context(rel_pool, admission.action_id))["decision_report"]
    assert decision["confirmation_source"] == "approved_action"
    assert decision["authority"] == "owner_device"

    executed = await _dispatch(rel_pool, admission.action_id)
    assert "error" not in executed and executed["status"] == "executed"

    (fact,) = await _facts(rel_pool, people.a)
    context = await _context(rel_pool, admission.action_id)
    assert (fact["predicate"], fact["object"]) == ("works-at", str(people.b))
    assert fact["content_authority"] == "third_party"
    assert fact["src"] == context["src"] == f"external_mcp:{proposal_id}"
    assert fact["observed_at"] == context["observed_at"] == _RECEIVED_AT
    assert fact["assert_origin"] == "approved"
    assert fact["assert_action_id"] == admission.action_id
    assert fact["confirmed_at"] is not None
    assert fact["confirmation_source"] == "approved_action"
    assert fact["verified"] is True
    # No model session anywhere on the path: none parked, none attributed.
    assert fact["assert_session_id"] is None
    assert (
        await rel_pool.fetchval(
            "SELECT session_id FROM pending_actions WHERE id=$1", admission.action_id
        )
        is None
    )
    evidence_src = await rel_pool.fetch(
        "SELECT DISTINCT src, origin, session_id FROM relationship.fact_evidence WHERE fact_id=$1",
        fact["id"],
    )
    assert [tuple(row) for row in evidence_src] == [
        (f"external_mcp:{proposal_id}", "approved", None)
    ]


async def test_approval_without_owner_admission_is_refused(rel_pool, people) -> None:
    """Control for the hook: the same third-party report without an owner decision fails."""
    _, admission = await _park(rel_pool, people)
    async with rel_pool.acquire() as conn:
        binding = await dashboard_review_binding(conn, admission.action_id)
    with pytest.raises(PermissionError, match="admitted owner authority"):
        await operations.approve_action(rel_pool, str(admission.action_id), external_review=binding)
    assert (
        await rel_pool.fetchval(
            "SELECT status FROM pending_actions WHERE id=$1", admission.action_id
        )
        == "pending"
    )


# REQ-relationship-facts-011: "An owner edit is refused, not attributed"; the owner
# rejects instead (REQ-dashboard-approvals-904).
async def test_owner_edit_after_review_is_refused_before_any_fact(rel_pool, people) -> None:
    _, admission = await _park(rel_pool, people)
    approved = await _approve(rel_pool, admission.action_id, edits={"object": str(people.c)})
    assert approved == {"error": EXTERNAL_PROPOSAL_REVIEW_ONLY}

    assert await _facts(rel_pool, people.a) == []
    assert (await _context(rel_pool, admission.action_id))["decision_report"] is None
    assert (
        await rel_pool.fetchval(
            "SELECT status FROM pending_actions WHERE id=$1", admission.action_id
        )
        == "pending"
    )
    # The edited call no longer matches its proposal, so no later review offers it.
    async with rel_pool.acquire() as conn:
        with pytest.raises(AssertionError, match="no longer matches"):
            await dashboard_review_binding(conn, admission.action_id)


# REQ-relationship-facts-011: "Provenance parameters in stored arguments are refused".
# The dashboard review shows no approvable call for such a row, and the executor is the
# second line: a binding to the tampered call (which no review issues) still writes nothing.
@pytest.mark.parametrize("key", ["src", "content_authority", "observed_at"])
async def test_stored_provenance_arguments_raise_before_any_fact_row(rel_pool, people, key) -> None:
    _, admission = await _park(rel_pool, people)
    action_id = admission.action_id
    args = await rel_pool.fetchval("SELECT tool_args FROM pending_actions WHERE id=$1", action_id)
    tampered = args | {key: "owner-self"}
    await rel_pool.execute(
        "UPDATE pending_actions SET tool_args=$2 WHERE id=$1", action_id, tampered
    )
    async with rel_pool.acquire() as conn:
        with pytest.raises(AssertionError, match="more than this review can show"):
            await dashboard_review_binding(conn, action_id)
    async with synthetic_owner(rel_pool), rel_pool.acquire() as conn, conn.transaction():
        approved = await operations.approve_action(
            conn,
            str(action_id),
            external_review=ExternalReviewBinding(action_id, args_digest(tampered)),
        )
    assert "error" not in approved

    executed = await _dispatch(rel_pool, admission.action_id)
    assert "unexpected keyword argument" in executed["error"] and key in executed["error"]
    assert await _facts(rel_pool, people.a) == []
    assert await rel_pool.fetchval("SELECT count(*) FROM relationship.fact_evidence") == 0
    assert (
        await rel_pool.fetchval(
            "SELECT status FROM pending_actions WHERE id=$1", admission.action_id
        )
        == "approved"
    )


# REQ-module-approvals-010: "A matching standing rule does not approve an external
# proposal", with the positive control that the same admitted owner rule is selected
# by the gate's admission for the ordinary call.
async def test_admitted_standing_rule_matches_ordinary_call_not_external_park(
    rel_pool, people
) -> None:
    rule_args = {
        "subject": str(people.a),
        "predicate": "works-at",
        "object": str(people.b),
        "object_kind": "entity",
    }
    rule_id = uuid.uuid4()
    await rel_pool.execute(
        "INSERT INTO approval_rules (id, tool_name, arg_constraints, description, active) "
        "VALUES ($1, 'relationship_assert_fact', $2, 'synthetic owner rule', true)",
        rule_id,
        {key: {"type": "exact", "value": value} for key, value in rule_args.items()},
    )
    await rel_pool.execute(
        "INSERT INTO approval_events (event_type, rule_id, actor, event_metadata) "
        "VALUES ('rule_created', $1, 'synthetic-owner-fixture', '{}'::jsonb)",
        rule_id,
    )
    async with synthetic_owner(rel_pool):
        await fact_authority.record_admitted_rule(rel_pool, rule_id)

    admitted = await admit_standing_rule(
        rel_pool, "relationship_assert_fact", rule_args | {"conf": 1.0}, frozenset()
    )
    assert admitted.rule is not None and admitted.rule["id"] == rule_id

    _, admission = await _park(rel_pool, people)
    row = await rel_pool.fetchrow(
        "SELECT status, approval_rule_id FROM pending_actions WHERE id=$1", admission.action_id
    )
    assert dict(row) == {"status": "pending", "approval_rule_id": None}
    assert await rel_pool.fetchval("SELECT use_count FROM approval_rules WHERE id=$1", rule_id) == 0
    assert (await _context(rel_pool, admission.action_id))["decision_report"] is None
    assert await _facts(rel_pool, people.a) == []
    assert timedelta(days=14) == await rel_pool.fetchval(
        "SELECT expires_at - requested_at FROM pending_actions WHERE id=$1", admission.action_id
    )
