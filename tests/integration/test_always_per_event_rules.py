# ruff: noqa: F811  (pytest fixtures imported from test_dashboard_stepup are used by parameter name)
"""No standing rule for an always_per_event tool, at the real seams (RFC 0038 D13).

Real PostgreSQL with the real step-up authority: every public rule write (dashboard operations,
the MCP module methods, approve-with-rule, the dashboard routes) refuses ``calendar_respond`` with
one typed reason, leaves no rule, no ``rule_created`` event and no consumed step-up nonce, and an
ordinary approval of it still completes without feeding promotion.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from butlers.api.models.approval import ApprovalRuleFromActionRequest
from butlers.api.routers import approvals as routes
from butlers.modules.approvals import operations
from butlers.modules.approvals.per_event import RULE_REFUSED_ALWAYS_PER_EVENT
from butlers.modules.approvals.stepup import dashboard_stepup_proof
from butlers.testing.db_manager import db_manager
from tests.integration.test_dashboard_stepup import (  # noqa: F401  (fixtures)
    authority,
    authority_db_url,
    proof_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

ACTOR = {"type": "human", "id": "synthetic-owner", "authenticated": True}
PER_EVENT_TOOL = "calendar_respond"
ORDINARY_TOOL = "synthetic_send"


async def _park(pool, tool_name: str) -> UUID:
    action_id = uuid4()
    await pool.execute(
        "INSERT INTO pending_actions(id,tool_name,tool_args,status,requested_at) "
        "VALUES($1,$2,$3,'pending',now())",
        action_id,
        tool_name,
        {"recipient": "synthetic"},
    )
    return action_id


async def _nothing_written(pool, action_id: UUID | None = None) -> None:
    assert await pool.fetchval("SELECT count(*) FROM approval_rules") == 0
    assert (
        await pool.fetchval("SELECT count(*) FROM approval_events WHERE event_type='rule_created'")
        == 0
    )
    if action_id is not None:
        status = await pool.fetchval("SELECT status FROM pending_actions WHERE id=$1", action_id)
        assert status == "pending", "a refused combined approval must not approve the action"


def _module(pool):
    from butlers.modules.approvals.module import ApprovalsModule

    module = ApprovalsModule()
    module._db = pool
    return module


def _constraints() -> dict:
    return {"recipient": {"type": "exact", "value": "synthetic"}}


async def test_direct_create_is_refused_before_step_up_is_consumed(authority):
    pool = authority[0]
    parameters = dict(
        tool_name=PER_EVENT_TOOL,
        arg_constraints=_constraints(),
        description="Synthetic",
        expires_at=None,
        max_uses=1,
    )
    proof = await proof_for(authority, "create", parameters)
    token = dashboard_stepup_proof.set(proof)
    try:
        refused = await operations.create_approval_rule(pool, **parameters)
        via_module = await _module(pool)._create_approval_rule(**parameters, actor=ACTOR)
    finally:
        dashboard_stepup_proof.reset(token)

    assert refused == {"error": RULE_REFUSED_ALWAYS_PER_EVENT}
    assert via_module == {"error": RULE_REFUSED_ALWAYS_PER_EVENT}
    await _nothing_written(pool)
    # The valid proof was never spent: the same factor still creates an ordinary rule.
    ordinary = dict(parameters, tool_name=ORDINARY_TOOL)
    proof = await proof_for(authority, "create", ordinary)
    token = dashboard_stepup_proof.set(proof)
    try:
        created = await operations.create_approval_rule(pool, **ordinary)
    finally:
        dashboard_stepup_proof.reset(token)
    assert "error" not in created
    assert await pool.fetchval("SELECT count(*) FROM approval_rules") == 1


@pytest.mark.parametrize("entry", ["operations", "module"])
async def test_create_from_action_is_refused(authority, entry):
    pool = authority[0]
    action_id = await _park(pool, PER_EVENT_TOOL)
    parameters = dict(action_id=str(action_id), constraint_overrides=None)
    token = dashboard_stepup_proof.set(await proof_for(authority, "from_action", parameters))
    try:
        if entry == "operations":
            result = await operations.create_rule_from_action(pool, **parameters)
        else:
            result = await _module(pool)._create_rule_from_action(**parameters, actor=ACTOR)
    finally:
        dashboard_stepup_proof.reset(token)

    assert result == {"error": RULE_REFUSED_ALWAYS_PER_EVENT}
    await _nothing_written(pool, action_id)


@pytest.mark.parametrize("entry", ["operations", "module"])
async def test_approve_with_rule_cannot_mint_a_rule_or_approve(authority, entry):
    pool = authority[0]
    action_id = await _park(pool, PER_EVENT_TOOL)
    token = dashboard_stepup_proof.set(
        await proof_for(authority, "approve_with_rule", dict(action_id=str(action_id)))
    )
    try:
        if entry == "operations":
            result = await operations.approve_action(pool, str(action_id), create_rule=True)
        else:
            result = await _module(pool)._approve_action(
                str(action_id), create_rule=True, actor=ACTOR
            )
    finally:
        dashboard_stepup_proof.reset(token)

    assert result == {"error": RULE_REFUSED_ALWAYS_PER_EVENT}
    await _nothing_written(pool, action_id)


async def test_ordinary_approval_completes_and_feeds_no_promotion(authority):
    pool = authority[0]
    action_id = await _park(pool, PER_EVENT_TOOL)

    result = await operations.approve_action(pool, str(action_id))

    assert "error" not in result and result["status"] == "approved"
    assert await pool.fetchval("SELECT count(*) FROM autonomy_approval_history") == 0
    assert await pool.fetchval("SELECT count(*) FROM autonomy_suggestions") == 0


async def test_an_ordinary_tool_still_records_promotion_history(authority):
    pool = authority[0]
    action_id = await _park(pool, ORDINARY_TOOL)

    result = await operations.approve_action(pool, str(action_id))

    assert "error" not in result
    assert await pool.fetchval("SELECT count(*) FROM autonomy_approval_history") == 1


async def test_module_suggestions_are_refused_for_a_per_event_action(authority):
    pool = authority[0]
    action_id = await _park(pool, PER_EVENT_TOOL)

    assert await _module(pool)._suggest_rule_constraints(str(action_id)) == {
        "error": RULE_REFUSED_ALWAYS_PER_EVENT
    }


@pytest.fixture
def routed(authority, monkeypatch):
    pool = authority[0]

    async def find_action_pool(_db, _action_id):
        return "general", pool

    async def find_named(_db, _table, **_kw):
        return [("general", pool)]

    monkeypatch.setattr(routes, "_find_action_pool", find_action_pool)
    monkeypatch.setattr(routes, "_find_named_approvals_pools", find_named)
    return pool, db_manager({"general": pool})


async def test_dashboard_routes_answer_409_with_the_typed_reason(routed):
    pool, db = routed
    action_id = await _park(pool, PER_EVENT_TOOL)

    create = routes.ApprovalRuleCreateRequest(
        butler="general",
        tool_name=PER_EVENT_TOOL,
        arg_constraints=_constraints(),
        description="Synthetic",
    )
    with pytest.raises(HTTPException) as direct:
        await routes.create_rule(create, db_mgr=db)
    with pytest.raises(HTTPException) as from_action:
        await routes.create_rule_from_action(
            ApprovalRuleFromActionRequest(action_id=str(action_id)), db_mgr=db
        )
    with pytest.raises(HTTPException) as suggestions:
        await routes.get_rule_suggestions(str(action_id), db_mgr=db)
    with pytest.raises(HTTPException) as combined:
        await routes.approve_owning_action(
            parsed_id=action_id,
            actor_id="synthetic-owner",
            edits=None,
            action_butler="general",
            target_pool=pool,
            db_mgr=db,
            mcp_mgr=SimpleNamespace(),
            create_rule=True,
        )

    for refused in (direct, from_action, suggestions, combined):
        assert refused.value.status_code == 409
        assert refused.value.detail == RULE_REFUSED_ALWAYS_PER_EVENT
    await _nothing_written(pool, action_id)


async def test_the_action_view_flags_a_per_event_tool_as_rule_ineligible(routed):
    pool, _db = routed
    per_event = await _park(pool, PER_EVENT_TOOL)
    ordinary = await _park(pool, ORDINARY_TOOL)

    async def eligible(action_id: UUID) -> bool:
        row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id=$1", action_id)
        from butlers.modules.approvals.per_event import per_event_rule_refusal

        return per_event_rule_refusal(row["tool_name"]) is None

    assert await eligible(per_event) is False
    assert await eligible(ordinary) is True


async def _seed_promotion_suggestion(pool, tool_name: str) -> str:
    from butlers.modules.approvals import autonomy_suggestions

    suggestion = await autonomy_suggestions.create_promotion_suggestion(
        pool, uuid4().hex * 2, tool_name, {"recipient": "synthetic"}, 5
    )
    return suggestion["id"]


async def _no_rule_and_still_pending(pool, suggestion_id: str) -> None:
    await _nothing_written(pool)
    status = await pool.fetchval(
        "SELECT status FROM autonomy_suggestions WHERE id=$1::uuid", suggestion_id
    )
    assert status == "pending", "a refused confirmation must leave the suggestion pending"


@pytest.mark.parametrize("entry", ["operations", "module"])
async def test_a_legacy_promotion_suggestion_cannot_mint_a_rule(authority, entry):
    from butlers.modules.approvals import autonomy_suggestions

    pool = authority[0]
    suggestion_id = await _seed_promotion_suggestion(pool, PER_EVENT_TOOL)
    token = dashboard_stepup_proof.set(
        await proof_for(authority, "promotion", dict(suggestion_id=suggestion_id))
    )
    try:
        if entry == "operations":
            result = await autonomy_suggestions.confirm_suggestion(pool, suggestion_id, ACTOR["id"])
        else:
            result = await _module(pool)._confirm_promotion_suggestion(suggestion_id, actor=ACTOR)
    finally:
        dashboard_stepup_proof.reset(token)

    assert result == {"error": RULE_REFUSED_ALWAYS_PER_EVENT}
    await _no_rule_and_still_pending(pool, suggestion_id)


async def test_the_dashboard_confirm_route_answers_409_for_a_legacy_suggestion(routed):
    pool, db = routed
    suggestion_id = await _seed_promotion_suggestion(pool, PER_EVENT_TOOL)

    with pytest.raises(HTTPException) as refused:
        await routes.confirm_suggestion(suggestion_id, db_mgr=db)

    assert refused.value.status_code == 409
    assert refused.value.detail == RULE_REFUSED_ALWAYS_PER_EVENT
    await _no_rule_and_still_pending(pool, suggestion_id)


async def test_an_existing_active_rule_never_auto_approves_a_per_event_call(authority):
    """The gate wrapper parks first: a pre-existing rule is not consulted (REQ-module-calendar-029)."""
    from fastmcp import FastMCP

    from butlers.config import parse_approval_config
    from butlers.modules.approvals.gate import apply_approval_gates
    from butlers.modules.calendar import CalendarModule

    pool = authority[0]
    arguments = {"entry_id": "entry", "request_id": "request", "response_status": "accepted"}
    await pool.execute(
        "INSERT INTO approval_rules(id, tool_name, arg_constraints, description, created_at, active) "
        "VALUES($1,$2,$3,'Synthetic legacy rule',now(),true)",
        uuid4(),
        PER_EVENT_TOOL,
        {key: {"type": "exact", "value": value} for key, value in arguments.items()},
    )
    executed: list[dict] = []
    mcp = FastMCP("per-event-rule-bypass")

    @mcp.tool(name=PER_EVENT_TOOL)
    async def calendar_respond(entry_id: str, request_id: str, response_status: str) -> dict:
        executed.append({"entry_id": entry_id})
        return {"status": "executed"}

    class Preparer:
        public_arguments = frozenset(arguments)

        async def prepare_and_park(self, *, tool_args, requested_at, expires_at, dossier):
            action_id = uuid4()
            await pool.execute(
                "INSERT INTO pending_actions(id,tool_name,tool_args,status,requested_at,expires_at) "
                "VALUES($1,$2,$3,'pending',$4,$5)",
                action_id,
                PER_EVENT_TOOL,
                tool_args,
                requested_at,
                expires_at,
            )
            return {"status": "pending_approval", "action_id": str(action_id)}

    module = CalendarModule()
    module._butler_name = "messenger"
    module._config = module.config_schema(provider="google", response_enabled=True)
    config = parse_approval_config({"enabled": True, "gated_tools": {PER_EVENT_TOOL: {}}})
    await apply_approval_gates(
        mcp,
        config,
        pool,
        "messenger",
        tool_metadata=module.tool_metadata(),
        canonical_preparers={PER_EVENT_TOOL: Preparer()},
    )

    wrapped = (await mcp.get_tool(PER_EVENT_TOOL)).fn
    result = await wrapped(
        **arguments,
        _why="Owner chose this occurrence",
        _evidence=[],
        _blast_radius="self",
        _reversibility="compensable",
    )

    assert result["status"] == "pending_approval"
    assert executed == [], "the matching rule must not run the handler"
    row = await pool.fetchrow(
        "SELECT status FROM pending_actions WHERE id=$1", UUID(result["action_id"])
    )
    assert row["status"] == "pending"
    assert await pool.fetchval("SELECT count(*) FROM approval_rules WHERE active") == 1
