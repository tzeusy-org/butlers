"""D13 admission controls; PostgreSQL/real-factor contracts live in the store owner."""

from unittest.mock import AsyncMock

import pytest

from butlers.core.dashboard_stepup import dashboard_stepup_proof
from butlers.modules.approvals.stepup import run_rule_write
from butlers.testing.fake_pool import AsyncCtx


@pytest.mark.asyncio
async def test_stolen_transport_cannot_reach_rule_writer():
    """An actor, callback or API key is not a dashboard factor or a nonce."""
    writer = AsyncMock()
    connection = AsyncMock()
    result = await run_rule_write(connection, "create", {"tool_name": "send"}, writer)
    assert result == {"error": "dashboard_step_up_required", "open_dashboard": "/approvals"}
    writer.assert_not_awaited()
    connection.fetchval.assert_not_awaited()
    assert dashboard_stepup_proof.get() is None


@pytest.mark.asyncio
async def test_all_six_public_writers_refuse_transport_actor_without_factor():
    """Old actor maps and callbacks cannot reach any permission-granting INSERT."""
    from uuid import uuid4

    from butlers.modules.approvals import autonomy_suggestions, operations
    from butlers.modules.approvals.module import ApprovalsModule

    class Connection:
        fetchrow = AsyncMock(return_value={"suggestion_type": "promotion"})
        execute = AsyncMock()

    connection = Connection()
    module = ApprovalsModule()
    module._db = connection
    actor = {"type": "human", "id": "synthetic-stolen-phone", "authenticated": True}
    action = str(uuid4())
    attempts = (
        operations.create_approval_rule(connection, "send", {}, "Synthetic"),
        operations.create_rule_from_action(connection, action),
        operations.approve_action(connection, action, create_rule=True),
        module._create_approval_rule("send", {}, "Synthetic", actor=actor),
        module._create_rule_from_action(action, actor=actor),
        module._approve_action(action, create_rule=True, actor=actor),
        autonomy_suggestions.confirm_suggestion(connection, action, actor["id"]),
    )
    for attempt in attempts:
        result = await attempt
        assert result["error"] == "dashboard_step_up_required"
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["writer_refusal", "audit_error", "receipt_error", "committed"])
async def test_enlisted_write_rolls_back_nonce_at_every_authoritative_failure(monkeypatch, failure):
    """Storage is a collaborator here; only the real-PG owner proves its authority."""
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from butlers.core.dashboard_stepup import DashboardStepUpProof
    from butlers.modules.approvals import stepup

    boundaries = []

    class Connection:
        active = False

        def is_in_transaction(self):
            return self.active

        @asynccontextmanager
        async def transaction(self):
            self.active = True
            boundaries.append("begin")
            try:
                yield
            except BaseException:
                boundaries.append("rollback")
                raise
            else:
                boundaries.append("commit")
            finally:
                self.active = False

    connection = Connection()
    binding = stepup.RuleBinding("create", "new", "a" * 64, "b" * 64, "synthetic:public")
    monkeypatch.setattr(stepup, "bind_rule_action", AsyncMock(return_value=binding))
    storage = AsyncMock(
        side_effect=[
            {"state": "admitted"},
            {"state": "refused" if failure == "receipt_error" else "completed"},
        ]
    )
    monkeypatch.setattr(stepup, "_store", storage)
    result_id = str(uuid4())

    async def writer(actual):
        assert actual is connection and connection.active
        boundaries.append("rule_and_audit")
        if failure == "writer_refusal":
            return {"error": "synthetic refusal"}
        if failure == "audit_error":
            raise RuntimeError("synthetic authoritative audit error")
        return {"id": result_id, "description": "private synthetic payload"}

    token = dashboard_stepup_proof.set(DashboardStepUpProof("c" * 64, "opaque"))
    try:
        if failure in {"audit_error", "receipt_error"}:
            with pytest.raises(RuntimeError):
                await run_rule_write(connection, "create", {}, writer)
        else:
            result = await run_rule_write(connection, "create", {}, writer)
            assert ("error" in result) == (failure == "writer_refusal")
    finally:
        dashboard_stepup_proof.reset(token)
    assert boundaries == [
        "begin",
        "rule_and_audit",
        "commit" if failure == "committed" else "rollback",
    ]
    if failure in {"receipt_error", "committed"}:
        reference = storage.await_args_list[1].args[3]
        assert reference == {"version": 1, "kind": "create", "id": result_id, "rule_id": result_id}
        assert "private synthetic payload" not in str(reference)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["completed_promotion", "demotion_fallback"])
async def test_suggestion_receipt_has_no_memory_effect_but_demotion_keeps_rule_fallback(
    monkeypatch, decision
):
    """Exercise the actual API seam; storage authority remains the PG owner's job."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from butlers.api.routers import approvals
    from butlers.modules.approvals.models import ApprovalRule
    from butlers.modules.approvals.stepup import _CommittedResult

    rule_id, suggestion_id = uuid4(), uuid4()
    row = dict(
        id=suggestion_id,
        suggestion_type="promotion" if decision == "completed_promotion" else "demotion",
        status="confirmed" if decision == "completed_promotion" else "pending",
        pattern_fingerprint="synthetic",
        tool_name="synthetic_send",
        representative_args={"rule_id": str(rule_id)},
        created_at=datetime.now(UTC),
        resulting_rule_id=rule_id if decision == "completed_promotion" else None,
    )
    updated = dict(row, status="confirmed")

    class Connection:
        fetchrow = AsyncMock(side_effect=[row, row, updated])

        def transaction(self):
            return AsyncCtx(self)

    connection = Connection()

    class Pool:
        fetchrow = AsyncMock(
            return_value=ApprovalRule(
                rule_id, "synthetic_send", {}, "Synthetic", datetime.now(UTC), active=False
            ).to_dict()
        )

        def acquire(self):
            return AsyncCtx(connection)

    pool = Pool()
    memory = AsyncMock()
    monkeypatch.setattr(
        approvals, "_find_named_approvals_pools", AsyncMock(return_value=[("home", pool)])
    )
    monkeypatch.setattr(approvals, "_decision_memory_writer_for", lambda *_: memory)
    enlisted = AsyncMock(return_value=_CommittedResult(updated))
    monkeypatch.setattr(approvals, "run_rule_write", enlisted)
    revoke = AsyncMock(return_value={"id": str(rule_id)})
    monkeypatch.setattr(approvals.approvals_ops, "revoke_approval_rule", revoke)

    result = await approvals.confirm_suggestion(str(suggestion_id), db_mgr=object())
    assert result.data.status == "confirmed"
    if decision == "completed_promotion":
        memory.record_standing_rule.assert_not_awaited()
        pool.fetchrow.assert_not_awaited()
        revoke.assert_not_awaited()
        enlisted.assert_awaited_once()
    else:
        enlisted.assert_not_awaited()
        revoke.assert_awaited_once()
        assert revoke.await_args.kwargs["rule_id"] == str(rule_id)
        memory.record_standing_rule.assert_awaited_once()
        assert memory.record_standing_rule.await_args.kwargs["active"] is False
