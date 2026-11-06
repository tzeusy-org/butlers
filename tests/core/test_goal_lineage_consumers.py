"""Verified goal lineage replaces spawner context for its consumers (bu-pkuny2).

RFC 0038 D7: RFC 0028's executor binding, RFC 0021's approval replay and memory
authority stamping read lineage from the verified context only; the domain gate
treats a verified approval_ref as the approval; D6 stores stamp exposed writes
with the goal's lineage pointer and domain events carry the effect's lineage.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core import approvals_hooks
from butlers.core.capability_json import arguments_digest
from butlers.core.goal_lineage import (
    ExposedOwnerAuthorityError,
    VerifiedLineage,
    carrying_forward,
    lineage_scope,
    taint_insert,
    taint_stamp,
    taint_update,
)

pytestmark = pytest.mark.unit


def _writer(*, exposed: bool) -> VerifiedLineage:
    return VerifiedLineage(
        goal_id=uuid.uuid4(),
        invocation_id=uuid.uuid4(),
        depth=0,
        origin="owner",
        exposed=exposed,
        capability="meal_update",
        args_digest="digest",
        call_id=uuid.uuid4(),
    )


def _source(authority: str | None = "owner", *, stamped: bool = False) -> dict[str, Any]:
    goal, invocation = (uuid.uuid4(), uuid.uuid4()) if stamped else (None, None)
    return {
        "taint_goal_id": goal,
        "taint_invocation_id": invocation,
        "content_authority": authority,
        "authority_entity_id": None,
    }


def test_taint_update_names_the_columns_only_under_exposure() -> None:
    assert taint_update(6).assignments == ""
    assert taint_update(6).values == ()
    with lineage_scope(_writer(exposed=False)):
        assert taint_update(6).assignments == ""
    writer = _writer(exposed=True)
    with lineage_scope(writer):
        update = taint_update(6)
    assert update.assignments == ", taint_goal_id = $6, taint_invocation_id = $7"
    assert update.values == (writer.goal_id, writer.invocation_id)


def test_carrying_forward_keeps_the_source_writer_unless_the_call_is_exposed() -> None:
    source = _source(stamped=True)
    pointer = (source["taint_goal_id"], source["taint_invocation_id"])
    with carrying_forward(source):
        assert taint_stamp() == pointer  # No lineage at all: a daemon or session write.
        assert taint_insert(4).values == pointer
        with lineage_scope(_writer(exposed=False)):
            assert taint_stamp() == pointer
        writer = _writer(exposed=True)
        with lineage_scope(writer):
            assert taint_stamp() == (writer.goal_id, writer.invocation_id)
    assert taint_stamp() == (None, None)


def test_carrying_forward_ends_when_its_block_raises() -> None:
    with pytest.raises(RuntimeError), carrying_forward(_source(stamped=True)):
        raise RuntimeError
    assert taint_stamp() == (None, None)


@pytest.mark.parametrize(
    "missing",
    ["taint_goal_id", "taint_invocation_id", "content_authority", "authority_entity_id"],
)
def test_carrying_forward_refuses_a_source_that_did_not_select_its_stamp(missing: str) -> None:
    source = _source()
    del source[missing]
    with pytest.raises(KeyError), carrying_forward(source):
        pass


@pytest.mark.parametrize(
    ("source", "written", "stored"),
    [
        ("owner", "owner", "owner"),
        ("owner_device", "owner", "owner"),
        ("owner", "owner_device", "owner_device"),
        ("third_party", "owner", "mixed"),
        ("mixed", "owner", "mixed"),
        ("system", "owner", "system"),
        (None, "owner", "mixed"),
        ("third_party", "owner_device", "mixed"),
        ("system", "owner_device", "system"),
        (None, "owner_device", "mixed"),
        ("third_party", "system", "mixed"),
        ("mixed", "system", "mixed"),
        ("system", "system", "system"),
        (None, "system", "mixed"),
        ("owner", "system", "system"),
        ("third_party", "third_party", "third_party"),
        ("owner", "third_party", "third_party"),
        (None, "third_party", "third_party"),
    ],
)
async def test_a_successor_keeps_its_sources_non_owner_class(
    source: str | None, written: str, stored: str | None
) -> None:
    from butlers.modules.memory.storage import _stamp_authority

    with carrying_forward(_source(source)):
        authority = await _stamp_authority(MagicMock(), written, None)
    assert authority.authority == stored


async def test_a_successor_still_validates_an_explicit_authority() -> None:
    """The explicit path checks the value first, and None means derive, never store NULL."""
    from butlers.modules.memory.content_authority import validate_authority
    from butlers.modules.memory.storage import _stamp_authority

    with carrying_forward(_source(None)), pytest.raises(Exception, match="(?i)authority"):
        await _stamp_authority(MagicMock(), "not-an-authority", None)
    with pytest.raises(Exception, match="(?i)authority"):
        validate_authority(None)  # type: ignore[arg-type]
    with lineage_scope(_writer(exposed=True)), carrying_forward(_source("owner")):
        with pytest.raises(ExposedOwnerAuthorityError):
            await _stamp_authority(MagicMock(), "owner", None)


async def test_stamp_authority_weakens_a_derived_owner_authority_too() -> None:
    from butlers.modules.memory import storage
    from butlers.modules.memory.content_authority import ContentAuthority

    derived = ContentAuthority("owner", uuid.uuid4())
    sender = uuid.uuid4()
    with patch.object(storage, "resolve_content_authority", AsyncMock(return_value=derived)):
        assert (await storage._stamp_authority(MagicMock(), None, None)) == derived
        with carrying_forward(_source("owner")):
            assert (await storage._stamp_authority(MagicMock(), None, None)) == derived
        with carrying_forward({**_source("third_party"), "authority_entity_id": sender}):
            kept = await storage._stamp_authority(MagicMock(), None, None)
        with carrying_forward(_source("system")):
            system = await storage._stamp_authority(MagicMock(), None, None)
    assert (kept.authority, kept.entity_id) == ("mixed", sender)  # The sender stays named.
    assert (system.authority, system.entity_id) == ("system", None)  # Not the owner's id.
    with patch.object(storage, "resolve_content_authority", AsyncMock(return_value=derived)):
        with carrying_forward(_source(None)):
            unknown = await storage._stamp_authority(MagicMock(), None, None)
    assert (unknown.authority, unknown.entity_id) == ("mixed", None)


_HA_ARGS = {
    "domain": "lock",
    "service": "unlock",
    "target": {"entity_id": ["lock.front"]},
    "data": {},
}


def _lineage(
    capability: str = "ha_call_service",
    args: dict[str, Any] | None = None,
    *,
    approval_ref: str | None = None,
    exposed: bool = False,
    origin: str = "owner",
) -> VerifiedLineage:
    effect = uuid.uuid4() if approval_ref else None
    return VerifiedLineage(
        goal_id=uuid.uuid4(),
        invocation_id=uuid.uuid4(),
        depth=0,
        origin=origin,
        exposed=exposed,
        capability=capability,
        args_digest=arguments_digest(args if args is not None else _HA_ARGS),
        call_id=uuid.uuid4(),
        effect_id=effect,
        approval_ref=approval_ref,
    )


async def test_rfc0028_executor_binding_reads_the_verified_approval():
    from butlers.modules.approvals.execution_context import get_approval_execution_context

    action = uuid.uuid4()
    lineage = _lineage(approval_ref=str(action))
    with lineage_scope(lineage):
        context = get_approval_execution_context(tool_name="ha_call_service", tool_args=_HA_ARGS)
        assert context is not None and context.action_id == action
        assert context.session_id is None  # No spawner session stands in.
        assert context.actor == f"goal:{lineage.goal_id}"
        changed = {**_HA_ARGS, "service": "lock"}
        assert (
            get_approval_execution_context(tool_name="ha_call_service", tool_args=changed) is None
        )

        async def child():
            return get_approval_execution_context(tool_name="ha_call_service", tool_args=_HA_ARGS)

        assert await asyncio.create_task(child()) is None  # Exact-task binding.
    with lineage_scope(_lineage()):  # Verified, but no approval_ref.
        assert (
            get_approval_execution_context(tool_name="ha_call_service", tool_args=_HA_ARGS) is None
        )


async def test_rfc0021_approval_replay_reads_the_verified_approval(monkeypatch):
    from butlers.modules.approvals import email_guard, park
    from butlers.modules.approvals.module import ApprovalsModule

    monkeypatch.setattr(email_guard, "check_email_recipient", AsyncMock())
    monkeypatch.setattr(email_guard, "check_recipient", AsyncMock())
    monkeypatch.setattr(park, "park_pending_action", AsyncMock())
    pool = object()
    module = ApprovalsModule()
    await module.on_startup(config=None, db=SimpleNamespace(pool=pool))
    payload = {"input": {"context": {"notify_request": {"message": "approved"}}}}
    try:

        def replay(args=payload):
            return approvals_hooks.is_approved_tool_execution(
                pool, tool_name="route.execute", tool_args=args
            )

        assert not replay()
        with lineage_scope(_lineage("route.execute", payload, approval_ref=str(uuid.uuid4()))):
            assert replay()
            assert not replay({"input": {}})
        with lineage_scope(_lineage("route.execute", payload)):
            assert not replay()
    finally:
        await module.on_shutdown()


async def test_memory_authority_reads_lineage_not_the_routing_context():
    from butlers.modules.memory.content_authority import resolve_content_authority
    from butlers.modules.memory.storage import _stamp_authority

    owner = uuid.uuid4()
    routed_owner = {"source_entity_id": str(owner)}
    with patch(
        "butlers.modules.memory.content_authority.fetch_owner_entity_id",
        AsyncMock(return_value=owner),
    ):
        assert (await resolve_content_authority(None, routed_owner)).authority == "owner"
        with lineage_scope(_lineage(exposed=True)):
            assert (await resolve_content_authority(None, routed_owner)).authority == (
                "third_party"
            )
            with pytest.raises(ExposedOwnerAuthorityError):
                await _stamp_authority(None, "owner", owner)
        with lineage_scope(_lineage(origin="owner")):
            stamped = await resolve_content_authority(None)
            assert (stamped.authority, stamped.entity_id) == ("owner", owner)
        with lineage_scope(_lineage(origin="obligation")):
            assert (await resolve_content_authority(None, routed_owner)).authority == "system"


def test_fact_authority_reads_lineage_and_refuses_exposed_owner_reports():
    from butlers.core import fact_authority

    with lineage_scope(_lineage(exposed=True)):
        assert fact_authority.current_fact_write_context().authority == "third_party"
        token = fact_authority._current_report.set(fact_authority.FactWriteContext("owner"))
        try:
            with pytest.raises(ExposedOwnerAuthorityError):
                fact_authority.current_fact_write_context()
        finally:
            fact_authority._current_report.reset(token)
    with lineage_scope(_lineage(origin="staffer")):
        assert fact_authority.current_fact_write_context().authority == "system"


def _gate(pool, original):
    from butlers.modules.approvals.gate import _make_gate_wrapper

    return _make_gate_wrapper(
        tool_name="notify",
        original_fn=original,
        pool=pool,
        expiry_hours=72,
        risk_tier=MagicMock(value="medium"),
        rule_precedence=("contact_role", "standing_rule"),
        butler_name="messenger",
    )


async def _notify(message: str, entity_id: str) -> dict[str, Any]:
    raise AssertionError("replaced per test")


async def test_gate_runs_a_verified_approved_call_once_without_parking():
    args = {"message": "hi", "entity_id": str(uuid.uuid4())}
    action = uuid.uuid4()
    lineage = _lineage("notify", args, approval_ref=str(action))
    ran: list[dict[str, Any]] = []

    async def notify(message: str, entity_id: str) -> dict[str, Any]:
        ran.append({"message": message, "entity_id": entity_id})
        return {"status": "sent"}

    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"id": action})
    wrapper = _gate(pool, notify)
    with (
        lineage_scope(lineage),
        patch(
            "butlers.modules.approvals.gate.resolve_action_target_contact",
            AsyncMock(side_effect=AssertionError("an approved goal effect is never re-gated")),
        ),
    ):
        assert await wrapper(**args) == {"status": "sent"}
    assert ran == [args]
    sql, *params = pool.fetchrow.await_args.args
    assert "status = 'approved'" in sql and params == [action, lineage.goal_id, "notify"]

    # No matching approved action in this domain: the call is gated as usual.
    pool.fetchrow = AsyncMock(return_value=None)
    with (
        lineage_scope(lineage),
        patch(
            "butlers.modules.approvals.gate.resolve_action_target_contact",
            AsyncMock(return_value=None),
        ),
    ):
        result = await _gate(pool, notify)(**args)
    assert ran == [args] and "error" in result


async def test_exposed_writes_stamp_the_goal_lineage_pointer():
    from butlers.core.domain_events import record_event
    from butlers.modules.memory.storage import _insert_episode_record

    exposed = _lineage(exposed=True)
    assert taint_stamp() == (None, None)
    with lineage_scope(_lineage()):
        assert taint_stamp() == (None, None)  # Verified but unexposed: untainted.

    conn = MagicMock()
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=uuid.uuid4())
    with lineage_scope(exposed):
        assert taint_stamp() == (exposed.goal_id, exposed.invocation_id)
        await _insert_episode_record(
            conn,
            episode_id=uuid.uuid4(),
            butler="general",
            session_id=None,
            content="from a stranger",
            embedding=[0.0],
            search_text="stranger",
            importance=1.0,
            expires_at=None,
            meta_json={},
            tenant_id="shared",
            request_id=None,
            retention_class="transient",
            sensitivity="normal",
        )
        await record_event(pool, event_type="general.noted", source_butler="general")
    sql, *params = conn.execute.await_args.args
    assert "taint_goal_id, taint_invocation_id" in sql
    assert params[-2:] == [exposed.goal_id, exposed.invocation_id]
    event_sql, *event_params = pool.fetchval.await_args.args
    assert "goal_effect_id, goal_exposed" in event_sql
    assert event_params[-4:] == [exposed.goal_id, exposed.invocation_id, None, True]


async def test_shared_public_writers_name_taint_columns_only_under_exposure():
    """Pinned-revision and stand-in public schemas predate core_313: dark writes stay as before."""
    from butlers.conversation_messages import message_create

    pool = MagicMock()
    pool.execute = AsyncMock()
    await message_create(pool, conversation_id=uuid.uuid4(), role="user", content="hi")
    dark_sql, *dark_params = pool.execute.await_args.args
    assert "taint_" not in dark_sql and len(dark_params) == 18

    exposed = _lineage(exposed=True)
    with lineage_scope(exposed):
        await message_create(pool, conversation_id=uuid.uuid4(), role="user", content="hi")
    sql, *params = pool.execute.await_args.args
    assert "captured_at, taint_goal_id, taint_invocation_id" in sql and "$19, $20" in sql
    assert params[-2:] == [exposed.goal_id, exposed.invocation_id]
