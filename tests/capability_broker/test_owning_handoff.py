"""Real broker/owning orchestration controls; SQL doubles are software only."""

from dataclasses import replace

import pytest

from butlers.capability_broker.contracts import Disposition
from butlers.capability_broker.owning import PrivateApprovals
from butlers.capability_broker.transport.client import PrivateClient
from butlers.capability_broker.transport.config import PeerPolicy, TransportConfig
from butlers.core.capability_json import arguments_digest
from butlers.core.goal_admission import Grant
from tests.capability_broker.test_broker import build
from tests.capability_broker.test_service_transport import material as material

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_committed_broker_park_carries_selected_grant_and_signed_purpose(monkeypatch):
    broker, connection, authorities, _, token = build()
    cap = replace(
        authorities.capability,
        effect_class="record",
        approvals_available=True,
        approval_required=True,
    )
    authorities.capability = cap
    selected = Grant(cap.identity, park_only=True)
    connection.grants = (selected,)
    client = PrivateClient(
        TransportConfig(
            "https://localhost:4200/mcp",
            PeerPolicy("owning-service", "health", frozenset({"DNS:localhost"})),
        ),
        None,
    )
    observed = []

    async def call(self, value, *, assertion=None):
        # Consumer sees only post-commit output; no callback supplies policy.
        assert not connection.in_transaction and connection.events[-1] == "commit"
        assert value["protocol"] == "broker-approvals-v2"
        from butlers.capability_broker.handoff import verify_handoff

        proof = verify_handoff(
            value["handoff"],
            keys={broker.signer.kid: broker.signer.private_key.public_key()},
            audience="health",
            purpose="PARK",
            now=int(broker.clock()),
        )
        assert proof.grant == selected and proof.central_rule_match is False
        assert proof.args_digest == arguments_digest(value["arguments"])
        observed.append(proof)
        # Closed UNKNOWN is not an approval or dispatch receipt.
        return {
            **{k: value[k] for k in ("protocol", "operation", "request_id", "identity")},
            "outcome": "unknown",
            "receipt": None,
        }

    monkeypatch.setattr(PrivateClient, "call", call)
    broker.approvals = PrivateApprovals({"health": client})
    try:
        result = await broker.call(connection.goal, token, cap.mcp_name, {"value": "original"})
    finally:
        await client.aclose()
    assert result.status == "unknown" and len(observed) == 1
    assert observed[0].decision is Disposition.PARK


async def test_real_owner_decision_enters_goal_gate_before_action_lock(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from butlers.api.routers import approvals as route
    from butlers.modules.approvals import goal_control

    action_id = uuid4()
    events = []

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            yield

        async def fetchrow(self, sql, *args):
            if "FOR UPDATE" in sql:
                assert events == ["goal_gate"]
                events.append("action_lock")
            return None

    conn = Conn()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    async def gate(*args):
        events.append("goal_gate")
        return None

    monkeypatch.setattr(goal_control, "lock_goal_decision", gate)
    monkeypatch.setattr(
        route.approvals_ops, "approve_action", AsyncMock(return_value={"error": "not found"})
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        await route.approve_owning_action(
            parsed_id=action_id,
            actor_id="owner",
            edits=None,
            action_butler="health",
            target_pool=Pool(),
            db_mgr=SimpleNamespace(),
            mcp_mgr=SimpleNamespace(),
        )
    assert refused.value.status_code == 404
    assert events == ["goal_gate", "action_lock"]


@pytest.mark.parametrize("via_tls", ["direct", "tls", "factory"])
async def test_fixed_owning_consumer_commits_witness_then_consumes_once_before_handler(
    material, via_tls, monkeypatch
):
    import asyncio
    import copy
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import UUID, uuid4

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from butlers.capability_broker.assertions import AssertionSigner
    from butlers.capability_broker.catalog import project_catalog
    from butlers.capability_broker.contracts import BrokerRefused, Decision, Principal, Reservation
    from butlers.capability_broker.handoff import sign_handoff
    from butlers.capability_broker.transport.protocol import reply, request
    from butlers.modules.approvals.execution_context import get_approval_execution_context
    from butlers.modules.approvals.goal_dispatch import GoalDispatch
    from butlers.modules.approvals.models import ActionStatus
    from tests.modules.approvals.test_goal_control import (
        DurableStore,
        identity_for,
        registered_send_catalog,
    )

    class Store(DurableStore):
        def __init__(self):
            super().__init__()
            self.lock = asyncio.Lock()
            self.in_transaction = False
            self.commits = 0

        @asynccontextmanager
        async def transaction(self):
            async with self.lock:
                self.in_transaction = True
                try:
                    async with super().transaction():
                        yield self
                    self.commits += 1
                finally:
                    self.in_transaction = False

        async def fetchrow(self, sql, *args):
            if "SET park_authorization=$2" in sql:
                self.gate.update(
                    park_authorization=copy.deepcopy(args[1]),
                    park_authorization_digest=args[2],
                    park_call_id=args[3],
                    state_version=self.gate["state_version"] + 1,
                )
                return copy.deepcopy(self.gate)
            if "SET phase='dispatch_intent'" in sql:
                if self.gate["dispatch_intent_id"] is not None:
                    return None
                self.gate.update(
                    phase="dispatch_intent",
                    dispatch_intent_id=args[1],
                    dispatch_snapshot_digest=args[2],
                    dispatch_consumed_at=None,
                    state_version=self.gate["state_version"] + 1,
                )
                return copy.deepcopy(self.gate)
            if "SET dispatch_consumed_at=" in sql:
                if self.gate["dispatch_consumed_at"] is not None:
                    return None
                self.gate.update(
                    dispatch_consumed_at="observed-consume",
                    state_version=self.gate["state_version"] + 1,
                )
                return {"effect_id": args[0]}
            return await super().fetchrow(sql, *args)

    import time

    now = int(time.time())
    from contextlib import AsyncExitStack

    from butlers.capability_broker.transport.server import PrivateApp, PrivateServer
    from tests.capability_broker.test_service_transport import CONTROL, SERVICE

    async with AsyncExitStack() as cleanup:
        store = Store()
        catalog = await registered_send_catalog()
        cap = project_catalog(catalog, domain="mail").capabilities[0]
        grant = Grant(cap.identity, park_only=True)
        principal = Principal(store.goal_id, uuid4(), 0, "owner", False, "fleet-test", (grant,))
        original = {"message": "original", "recipient": "test-recipient"}
        store.arguments, store.digest = original, arguments_digest(original)
        reservation = Reservation(
            principal,
            cap,
            Decision(Disposition.PARK),
            uuid4(),
            store.action_id,
            arguments_digest(original),
            grant,
        )
        signer = AssertionSigner("fixture", Ed25519PrivateKey.generate())
        calls = []

        async def handler(**kwargs):
            assert not store.in_transaction and store.gate["dispatch_consumed_at"] is not None
            context = get_approval_execution_context(tool_name="send", tool_args=kwargs)
            assert context is not None and context.action_id == store.action_id
            calls.append(copy.deepcopy(kwargs))
            return {"provider_result": "not-an-effect-receipt"}

        op = GoalDispatch(
            store,
            "mail",
            catalog,
            {"send": handler},
            {"fixture": signer.private_key.public_key()},
            clock=lambda: now,
        )
        peer = SimpleNamespace(role="broker-control", domain="mail")
        if via_tls != "direct":
            server_context, client_context = material()
            if via_tls == "factory":
                import base64
                import socket
                from types import MethodType

                from butlers.capability_broker import production
                from butlers.config import CapabilityBrokerTransportConfig
                from butlers.daemon import ButlerDaemon

                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                settings = CapabilityBrokerTransportConfig.model_validate(
                    {
                        "enabled": True,
                        "role": "owning-service",
                        "domain": "mail",
                        "listen_host": "127.0.0.1",
                        "listen_port": port,
                        "peer": {
                            "role": "broker-control",
                            "domain": "mail",
                            "sans": sorted(CONTROL.sans),
                            "client_certificate": True,
                        },
                        "material": {
                            "ca_path": "/fixture-only/ca",
                            "certificate_path": "/fixture-only/cert",
                            "private_key_path": "/fixture-only/key",
                        },
                        "verification_keys": {
                            "fixture": base64.b64encode(
                                signer.private_key.public_key().public_bytes_raw()
                            ).decode()
                        },
                    }
                )
                daemon = SimpleNamespace(
                    config=SimpleNamespace(name="mail", capability_broker_transport=settings),
                    db=store,
                    _modules=[SimpleNamespace(name="approvals")],
                    _active_modules=[SimpleNamespace(name="approvals")],
                    tool_catalog=catalog,
                    _gated_tool_originals={"send": handler},
                )
                daemon._broker_owning_originals = MethodType(
                    ButlerDaemon._broker_owning_originals, daemon
                )
                monkeypatch.setattr(production, "material_context", lambda *a, **k: server_context)
                await production.start_private_transport(daemon)
                cleanup.push_async_callback(daemon._capability_transport_owner.aclose)
                server = daemon._capability_transport_owner.server
                op = server.app.operation
            else:
                server = PrivateServer(
                    PrivateApp("mail", op), server_context, replace(CONTROL, domain="mail")
                )
                await cleanup.enter_async_context(server.run())
            client = PrivateClient(
                TransportConfig(
                    f"https://localhost:{server.port}/mcp", replace(SERVICE, domain="mail")
                ),
                client_context,
            )
            cleanup.push_async_callback(client.aclose)

            async def invoke(value, _peer):
                return await client.call(value)
        else:
            invoke = op
        identity = {
            "goal_id": str(store.goal_id),
            "effect_id": str(store.action_id),
            "action_id": str(store.action_id),
            "owning_domain": "mail",
            "capability": cap.identity,
            "args_digest": reservation.args_digest,
        }

        def envelope(purpose, res=reservation, version=None):
            value = {
                "protocol": "broker-approvals-v2",
                "operation": purpose,
                "request_id": str(uuid4()),
                "identity": dict(identity),
                "handoff": sign_handoff(
                    signer,
                    res,
                    purpose=purpose,
                    issued_at=now,
                    original_args_digest=reservation.args_digest,
                    approval_ref=str(store.action_id) if version is not None else None,
                    snapshot_version=version,
                ),
            }
            if purpose == "PARK":
                value["arguments"] = copy.deepcopy(original)
            return request(value, "mail")

        parked = envelope("PARK")
        result = reply(await invoke(parked, peer), parked)
        assert result["outcome"] == "parked" and store.gate["park_call_id"] == reservation.call_id

        def assert_refusal(error):
            if isinstance(error, ExceptionGroup):
                assert error.exceptions
                for child in error.exceptions:
                    assert_refusal(child)
            else:
                assert isinstance(error, (ValueError, BrokerRefused))

        for field, forged in (
            ("handoff", parked["handoff"][:-1] + "x"),
            ("arguments", {"message": "changed"}),
        ):
            bad = {**parked, field: forged}
            with pytest.raises((ValueError, BrokerRefused, ExceptionGroup)) as denied:
                await invoke(bad, peer)
            assert_refusal(denied.value)
        store.row["tool_args"] = {"message": "edited", "recipient": "test-recipient"}
        await op.owner.transition(
            identity_for(store),
            target_status=ActionStatus.APPROVED,
            decided_by="human:owner",
            event_actor="owner",
            event_reason="fixture",
        )
        snapshot_request = envelope("SNAPSHOT")
        snapshot = reply(await invoke(snapshot_request, peer), snapshot_request)["receipt"]
        assert snapshot["call"]["args"] == store.row["tool_args"]
        approved = replace(
            reservation,
            call_id=uuid4(),
            args_digest=arguments_digest(store.row["tool_args"]),
            decision=Decision(Disposition.DISPATCH),
        )
        claim = envelope("CLAIM", approved, snapshot["version"])
        witness = reply(await invoke(claim, peer), claim)["receipt"]
        lookup = envelope("WITNESS", approved, snapshot["version"])
        assert reply(await invoke(lookup, peer), lookup)["receipt"] == witness and calls == []
        execute = envelope("EXECUTE", approved, snapshot["version"])
        outcomes = await asyncio.gather(
            invoke(execute, peer), invoke(execute, peer), return_exceptions=True
        )
        assert sum(isinstance(v, dict) for v in outcomes) == 1
        assert len(calls) == 1 and calls[0] == store.row["tool_args"]
        assert next(v for v in outcomes if isinstance(v, dict))["outcome"] == "unknown"
        assert store.gate["dispatch_intent_id"] == UUID(witness["intent_id"])
        with pytest.raises((ValueError, ExceptionGroup)) as denied:
            await invoke(lookup, peer)
        assert_refusal(denied.value)


@pytest.mark.parametrize(
    "mode,park_only,exposed,approved",
    [
        ("default", False, False, True),
        ("default", False, True, True),
        ("default", True, False, False),
        ("always_per_event", False, False, False),
    ],
)
async def test_authenticated_owning_rule_uses_selected_grant_not_central_match_flag(
    mode, park_only, exposed, approved
):
    import copy
    import time
    from types import SimpleNamespace
    from uuid import UUID, uuid4

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from butlers.capability_broker.assertions import AssertionSigner
    from butlers.capability_broker.catalog import project_catalog
    from butlers.capability_broker.contracts import Decision, Principal, Reservation
    from butlers.capability_broker.handoff import sign_handoff
    from butlers.modules.approvals.goal_dispatch import GoalDispatch
    from tests.modules.approvals.test_goal_control import RuleStore, registered_send_catalog

    class Store(RuleStore):
        async def fetchrow(self, sql, *args):
            if "SET park_authorization=$2" in sql:
                self.gate.update(
                    park_authorization=copy.deepcopy(args[1]),
                    park_authorization_digest=args[2],
                    park_call_id=args[3],
                    state_version=self.gate["state_version"] + 1,
                )
                return copy.deepcopy(self.gate)
            return await super().fetchrow(sql, *args)

    store = Store()
    catalog = await registered_send_catalog(mode=mode)
    cap = project_catalog(catalog, domain="mail").capabilities[0]
    grant = Grant(cap.identity, park_only=park_only)
    principal = Principal(
        store.goal_id, uuid4(), 0, "owner", exposed, "fixture-current-fleet", (grant,)
    )
    reservation = Reservation(
        principal, cap, Decision(Disposition.PARK), uuid4(), store.action_id, store.digest, grant
    )
    signer = AssertionSigner("fixture", Ed25519PrivateKey.generate())
    now = int(time.time())
    envelope = {
        "protocol": "broker-approvals-v2",
        "operation": "PARK",
        "request_id": str(uuid4()),
        "identity": {
            "goal_id": str(store.goal_id),
            "effect_id": str(store.action_id),
            "action_id": str(store.action_id),
            "owning_domain": "mail",
            "capability": cap.identity,
            "args_digest": store.digest,
        },
        "arguments": store.arguments,
        "handoff": sign_handoff(signer, reservation, purpose="PARK", issued_at=now),
    }
    dispatch = GoalDispatch(
        store, "mail", catalog, {}, {"fixture": signer.private_key.public_key()}, clock=lambda: now
    )
    result = await dispatch(envelope, SimpleNamespace(role="broker-control", domain="mail"))
    assert result["outcome"] == "parked"
    assert store.gate["park_authorization"]["central_rule_match"] is False
    assert store.gate["park_call_id"] == reservation.call_id
    assert store.row["status"] == ("approved" if approved else "pending")
    assert store.rule["use_count"] == int(approved)
    assert (store.row.get("approval_rule_id") == store.rule["id"]) is approved
    assert (
        store.row["goal_id"] == store.goal_id
        and UUID(str(store.row["invocation_id"])) == principal.invocation_id
    )
    if mode == "always_per_event":
        from butlers.modules.approvals.operations import create_rule_from_action

        assert "error" in await create_rule_from_action(
            store, str(store.action_id), actor_id="owner"
        )


@pytest.mark.parametrize(
    "fault", ["key", "expired", "purpose", "audience", "grant", "mode", "peer", "identity"]
)
async def test_fixed_owning_boundary_refuses_untrusted_purpose_before_sql(fault):
    from types import SimpleNamespace
    from uuid import uuid4

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from butlers.capability_broker.assertions import AssertionSigner
    from butlers.capability_broker.catalog import project_catalog
    from butlers.capability_broker.contracts import BrokerRefused, Decision, Principal, Reservation
    from butlers.capability_broker.handoff import sign_handoff
    from butlers.modules.approvals.goal_dispatch import GoalDispatch
    from tests.modules.approvals.test_goal_control import DurableStore, registered_send_catalog

    store = DurableStore()
    catalog = await registered_send_catalog()
    cap = project_catalog(catalog, domain="mail").capabilities[0]
    grant = Grant(cap.identity, park_only=True)
    principal = Principal(store.goal_id, uuid4(), 0, "owner", False, "fleet", (grant,))
    reservation = Reservation(
        principal, cap, Decision(Disposition.PARK), uuid4(), store.action_id, store.digest, grant
    )
    trusted = AssertionSigner("test", Ed25519PrivateKey.generate())
    signing = AssertionSigner("test", Ed25519PrivateKey.generate()) if fault == "key" else trusted
    if fault == "grant":
        reservation = replace(reservation, selected_grant=None)
    if fault == "mode":
        reservation = replace(reservation, capability=replace(cap, always_per_event=True))
    with pytest.raises((BrokerRefused, ValueError)):
        token = sign_handoff(
            signing,
            reservation,
            purpose="SNAPSHOT" if fault == "purpose" else "PARK",
            issued_at=1 if fault == "expired" else 100,
        )
        value = {
            "protocol": "broker-approvals-v2",
            "operation": "PARK",
            "request_id": str(uuid4()),
            "identity": {
                "goal_id": str(uuid4() if fault == "identity" else store.goal_id),
                "effect_id": str(store.action_id),
                "action_id": str(store.action_id),
                "owning_domain": "mail",
                "capability": cap.identity,
                "args_digest": store.digest,
            },
            "arguments": store.arguments,
            "handoff": token,
        }
        dispatch = GoalDispatch(
            store,
            "other" if fault == "audience" else "mail",
            catalog,
            {},
            {"test": trusted.private_key.public_key()},
            clock=lambda: 100,
        )
        await dispatch(
            value,
            SimpleNamespace(
                role="owning-service" if fault == "peer" else "broker-control",
                domain=dispatch.domain,
            ),
        )
    assert store.gate is None and store.row is None and not store.events


@pytest.mark.parametrize(
    "entry",
    [
        "reject",
        "module_reject",
        "module_expire",
        "touch_expire",
        "defer_expire",
        "defer",
        "rollback",
    ],
)
async def test_actual_negative_and_defer_consumers_keep_goal_gate_atomic(entry):
    import copy
    from datetime import UTC, datetime, timedelta

    from butlers.modules.approvals import operations
    from butlers.modules.approvals.delivery_lifecycle import defer_pending_action
    from butlers.modules.approvals.models import PendingAction
    from butlers.modules.approvals.module import ApprovalsModule
    from tests.modules.approvals.test_goal_approval_lineage import ApprovalStore, action_row

    class Store(ApprovalStore):
        def __init__(self, row):
            super().__init__(row)
            self.order = []

        async def fetch(self, sql, *args):
            if "FROM pending_actions" in sql:
                return [copy.deepcopy(self.row)]
            if "FROM approval_delivery_presentations" in sql:
                return [
                    {
                        "id": self.intent_id,
                        "intent_id": self.intent_id,
                        "presentation_generation": 1,
                        "state": "ready",
                    }
                ]
            raise AssertionError("unexpected_negative_consumer_query")

        async def fetchrow(self, sql, *args):
            if "pending_actions" in sql and "FOR UPDATE" in sql:
                self.order.append("action")
            if "UPDATE pending_actions SET expires_at" in sql:
                self.row["expires_at"] = args[0]
                return copy.deepcopy(self.row)
            return await super().fetchrow(sql, *args)

        async def execute(self, sql, *args):
            if "pg_advisory_xact_lock(hashtext('approval-goal:'" in sql:
                self.order.append("goal")
            if "INSERT INTO approval_delivery_presentations" in sql:
                return "INSERT 0 1"
            return await super().execute(sql, *args)

    expired = entry in {"module_expire", "touch_expire", "defer_expire"}
    # live-clock: Module expiry and DB-clock fallback are uninjected; keep relative expiry coherent.
    now = datetime.now(UTC)
    store = Store(action_row(expires_at=now + timedelta(hours=-1 if expired else 1)))
    action = PendingAction.from_row(store.row)
    if entry == "rollback":
        store.refuse_event = True
        before = copy.deepcopy((store.row, store.gate, store.presentations, store.events))
        with pytest.raises(ValueError, match="controlled_unmigrated_event_constraint"):
            await operations.reject_action(store, str(action.id), actor_id="owner")
        assert (store.row, store.gate, store.presentations, store.events) == before
        return
    if entry == "reject":
        result = await operations.reject_action(store, str(action.id), actor_id="owner")
        assert "error" not in result
    elif entry in {"module_reject", "module_expire"}:
        module = ApprovalsModule()
        module._db = store
        module._decision_memory_writer = None
        if entry == "module_reject":
            await module._reject_action(
                str(action.id), actor={"type": "human", "id": "owner", "authenticated": True}
            )
        else:
            assert (await module._expire_stale_actions())["expired_count"] == 1
    elif entry == "touch_expire":
        result = await operations.expire_pending_action_if_stale(store, action, now=now)
        assert "expired" in result["error"]
    else:
        result = await defer_pending_action(
            store, action_id=action.id, hours=2, actor="owner", now=now
        )
        assert result.changed and result.expired_instead is expired
    assert store.order and store.order[0] == "goal"
    assert store.gate["phase"] == ("pending" if entry == "defer" else "negative_terminal")
    assert store.row["status"] == (
        "pending" if entry == "defer" else "expired" if expired else "rejected"
    )
    assert not store.gate["stop_fenced"] and store.gate["dispatch_intent_id"] is None
    assert any(p["state"] == "delivered" for p in store.presentations)


@pytest.mark.parametrize("entry", ["deny", "defer", "abandon"])
async def test_actual_negative_API_audit_scope_takes_goal_gate_first(monkeypatch, entry):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from fastapi import HTTPException

    from butlers.api.routers import approvals as route
    from butlers.modules.approvals import goal_control
    from butlers.modules.approvals.delivery_lifecycle import DeferTransition

    events = []

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            events.append("transaction")
            yield

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield Conn()

    pool = Pool()

    async def find(*args):
        return "mail", pool

    async def gate(*args):
        events.append("goal_gate")
        return None

    async def decide(*args, **kwargs):
        assert events == ["transaction", "goal_gate"]
        events.append("decision")
        return {"error": "not found"} if entry != "defer" else DeferTransition(None, False)

    monkeypatch.setattr(route, "_find_action_pool", find)
    monkeypatch.setattr(route, "_decision_memory_writer_for", lambda *args: None)
    monkeypatch.setattr(goal_control, "lock_goal_decision", gate)
    monkeypatch.setattr(route.approvals_ops, "reject_action", decide)
    monkeypatch.setattr(route.approvals_ops, "abandon_approved_action", decide)
    monkeypatch.setattr(route, "defer_pending_action", decide)
    with pytest.raises(HTTPException) as refused:
        if entry == "deny":
            await route.deny_approval(
                str(uuid4()),
                SimpleNamespace(state=SimpleNamespace()),
                route.ApprovalDenyRequest(),
                db_mgr=SimpleNamespace(),
                callback_actor=None,
            )
        elif entry == "abandon":
            await route.abandon_approval(
                str(uuid4()),
                route.ApprovalAbandonRequest(reason="controlled"),
                db_mgr=SimpleNamespace(),
            )
        else:
            await route.defer_approval(
                str(uuid4()), route.ApprovalDeferRequest(hours=2), db_mgr=SimpleNamespace()
            )
    assert refused.value.status_code == 404
    assert events == ["transaction", "goal_gate", "decision"]


@pytest.mark.parametrize("fault", [None, "event", "executed", "race"])
async def test_actual_approved_abandon_preserves_gate_event_and_execution_fences(fault):
    import copy

    from butlers.modules.approvals.operations import abandon_approved_action
    from tests.modules.approvals.test_goal_approval_lineage import ApprovalStore, action_row

    class Store(ApprovalStore):
        def __init__(self):
            super().__init__(action_row(status="approved"))
            self.row["goal_approved_args_digest"] = self.row["goal_args_digest"]
            self.gate.update(
                phase="approved", state_version=2, approved_args_digest=self.row["goal_args_digest"]
            )
            self.order = []
            self.refuse_event = fault == "event"
            if fault == "executed":
                self.row["execution_result"] = {"success": True}

        async def fetchrow(self, sql, *args):
            if "UPDATE pending_actions SET status" in sql:
                self.order.append("action")
                if fault == "race":
                    self.row["execution_result"] = {"success": True}
                    return None
                assert args[0] == "abandoned" and args[4] == "approved"
                if self.row["status"] != args[4] or self.row.get("execution_result") is not None:
                    return None
                self.row.update(status=args[0], decided_by=args[1], decided_at=args[2])
                return copy.deepcopy(self.row)
            return await super().fetchrow(sql, *args)

        async def execute(self, sql, *args):
            if "pg_advisory_xact_lock(hashtext('approval-goal:'" in sql:
                self.order.append("goal")
            return await super().execute(sql, *args)

    store = Store()
    before = copy.deepcopy((store.row, store.gate, store.events))
    result = await abandon_approved_action(
        store, str(store.row["id"]), "controlled", actor_id="owner"
    )
    if fault == "event":
        assert "error" in result
        assert (store.row, store.gate, store.events) == before
    elif fault in {"executed", "race"}:
        assert "execution result" in result["error"]
        assert store.gate == before[1] and not store.events
    else:
        assert result["status"] == "abandoned"
        assert store.order[0] == "goal"
        assert store.gate["phase"] == "negative_terminal" and store.gate["state_version"] == 3
        assert store.gate["approved_args_digest"] == before[1]["approved_args_digest"]
        assert len(store.events) == 1 and store.events[0][0] == "action_abandoned"


@pytest.mark.parametrize("edited,lost_ack", [(False, False), (True, False), (True, True)])
async def test_real_health_park_only_replays_stored_original_once(monkeypatch, edited, lost_ack):
    """Real registration/Broker/owning consumer; SQL and private peer are software controls."""
    import asyncio
    import copy
    import time
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import UUID, uuid4

    from butlers.capability_broker.catalog import project_catalog
    from butlers.capability_broker.contracts import BrokerRefused, Decision, Reservation
    from butlers.capability_broker.handoff import sign_handoff, verify_handoff
    from butlers.capability_broker.ledger import BrokerLedger
    from butlers.capability_broker.transport.protocol import identity, reply, request
    from butlers.modules.approvals.goal_dispatch import GoalDispatch
    from butlers.modules.approvals.models import ActionStatus
    from tests.capability_broker.test_published_catalog import registered_health_record_broker
    from tests.modules.approvals.test_goal_control import DurableStore

    class HealthStore(DurableStore):
        def __init__(self):
            super().__init__()
            self.lock = asyncio.Lock()
            self.in_transaction = False

        @asynccontextmanager
        async def transaction(self):
            async with self.lock:
                self.in_transaction = True
                try:
                    async with super().transaction():
                        yield self
                finally:
                    self.in_transaction = False

        async def fetchrow(self, sql, *args):
            if "current_schema()" in sql and "current_user" in sql:
                return dict(
                    schema="health",
                    role="butler_health_rw",
                    unsafe=False,
                    schemas=["pg_catalog", "health", "public"],
                )
            if "SET park_authorization=$2" in sql:
                self.gate.update(
                    park_authorization=copy.deepcopy(args[1]),
                    park_authorization_digest=args[2],
                    park_call_id=args[3],
                    state_version=self.gate["state_version"] + 1,
                )
                return copy.deepcopy(self.gate)
            if "SET phase='dispatch_intent'" in sql:
                if self.gate["dispatch_intent_id"] is not None:
                    return None
                self.gate.update(
                    phase="dispatch_intent",
                    dispatch_intent_id=args[1],
                    dispatch_snapshot_digest=args[2],
                    dispatch_consumed_at=None,
                    state_version=self.gate["state_version"] + 1,
                )
                return copy.deepcopy(self.gate)
            if "SET dispatch_consumed_at=" in sql:
                if self.gate["dispatch_consumed_at"] is not None:
                    return None
                self.gate.update(
                    dispatch_consumed_at="software-observed-consume",
                    state_version=self.gate["state_version"] + 1,
                )
                return {"effect_id": args[0]}
            return await super().fetchrow(sql, *args)

    (
        broker,
        conn,
        domains,
        token,
        _,
        catalog,
        originals,
        facts,
    ) = await registered_health_record_broker(monkeypatch, park_only=True, approvals_enabled=True)
    store = HealthStore()
    store.goal_id = conn.goal
    original = {"type": "weight", "value": 72.5}
    store.arguments, store.digest = original, arguments_digest(original)
    now = int(time.time())
    broker.clock = lambda: now
    op = GoalDispatch(
        store,
        "health",
        catalog,
        originals,
        {broker.signer.kid: broker.signer.private_key.public_key()},
        clock=lambda: now,
    )
    peer = SimpleNamespace(role="broker-control", domain="health")
    seen = []

    async def exchange(self, value, *, assertion=None):
        assert not conn.in_transaction and conn.events[-1] == "commit"
        seen.append(copy.deepcopy(value))
        store.action_id = UUID(value["identity"]["action_id"])
        return await op(request(value, "health"), peer)

    monkeypatch.setattr(PrivateClient, "call", exchange)
    client = PrivateClient(
        TransportConfig(
            "https://localhost:4200/mcp",
            PeerPolicy("owning-service", "health", frozenset({"DNS:localhost"})),
        ),
        None,
    )
    broker.approvals = PrivateApprovals({"health": client})
    try:
        result = await broker.call(conn.goal, token, "health__measurement_log", original)
    finally:
        await client.aclose()
    assert result.status == "parked" and conn.used == 1 and facts == [] and domains.calls == []
    parked = seen[0]
    proof = verify_handoff(
        parked["handoff"], keys=op.keys, audience="health", purpose="PARK", now=now
    )
    assert proof.grant == conn.grants[0] and proof.grant.park_only
    assert proof.central_rule_match is False and store.row["tool_args"] == original
    assert store.row["invocation_id"] == conn.invocation
    assert store.gate["park_call_id"] == UUID(proof.payload["d7"]["call_id"])
    async with conn.transaction():
        principal = await BrokerLedger(conn).authenticate(conn.goal, token)
    cap = next(
        c
        for c in project_catalog(catalog, domain="health").capabilities
        if c.identity == "health__measurement_log"
    )
    reservation = Reservation(
        principal,
        cap,
        Decision(Disposition.PARK),
        UUID(proof.payload["d7"]["call_id"]),
        result.effect_id,
        store.digest,
        conn.grants[0],
    )

    def envelope(purpose, res=reservation, version=None):
        return request(
            {
                "protocol": "broker-approvals-v2",
                "operation": purpose,
                "request_id": str(uuid4()),
                "identity": parked["identity"],
                "handoff": sign_handoff(
                    broker.signer,
                    res,
                    purpose=purpose,
                    issued_at=now,
                    original_args_digest=store.digest,
                    approval_ref=str(store.action_id) if version is not None else None,
                    snapshot_version=version,
                ),
            },
            "health",
        )

    # Pending state cannot execute, even with a genuine signed request.
    with pytest.raises((ValueError, BrokerRefused)):
        await op(envelope("SNAPSHOT"), peer)
    assert facts == []
    if edited:
        store.row["tool_args"] = {"type": "weight", "value": 73.0}
    await op.owner.transition(
        identity(parked["identity"]),
        target_status=ActionStatus.APPROVED,
        decided_by="human:owner",
        event_actor="owner",
        event_reason="record",
    )
    snap_request = envelope("SNAPSHOT")
    snap = reply(await op(snap_request, peer), snap_request)["receipt"]
    assert snap["call"]["args"] == store.row["tool_args"] and facts == []
    # The same production validator used by central approved preparation still
    # resolves the edited snapshot against the complete current publication.
    async with conn.transaction():
        checked, decision = await BrokerLedger(conn).resolve_call(
            principal,
            cap.identity,
            snap["call"]["args"],
            arguments_digest(snap["call"]["args"]),
            broker.authorities,
        )
    assert checked == cap and decision.disposition is Disposition.PARK
    approved = replace(
        reservation,
        call_id=uuid4(),
        args_digest=arguments_digest(store.row["tool_args"]),
        decision=Decision(Disposition.DISPATCH),
    )
    claim = envelope("CLAIM", approved, snap["version"])
    witness = reply(await op(claim, peer), claim)["receipt"]
    lookup = envelope("WITNESS", approved, snap["version"])
    assert reply(await op(lookup, peer), lookup)["receipt"] == witness and facts == []
    execute = envelope("EXECUTE", approved, snap["version"])

    async def invoke():
        outcome = await op(execute, peer)
        assert not store.in_transaction and store.gate["dispatch_consumed_at"] is not None
        if lost_ack:
            raise ConnectionError("controlled_execute_reply_loss")
        return outcome

    outcomes = await asyncio.gather(invoke(), invoke(), return_exceptions=True)
    assert len(facts) == 1 and facts[0]["metadata"]["value"] == (73.0 if edited else 72.5)
    assert sum(isinstance(v, dict) for v in outcomes) == (0 if lost_ack else 1)
    assert sum(isinstance(v, ConnectionError) for v in outcomes) == (1 if lost_ack else 0)
    assert store.gate["dispatch_intent_id"] == UUID(witness["intent_id"])
    with pytest.raises((ValueError, BrokerRefused)):
        await op(execute, peer)
    assert len(facts) == 1 and domains.calls == []


@pytest.mark.parametrize(
    "edited,lost_ack,denied,standing",
    [
        (False, False, False, False),
        (True, False, False, False),
        (True, True, False, False),
        (False, False, True, False),
        (False, False, False, True),
    ],
)
async def test_native_email_registered_factory_parks_and_replays_once(
    monkeypatch, material, unused_tcp_port, edited, lost_ack, denied, standing
):
    """Real registration/Broker/owning consumer; SQL and private peer are software controls."""
    import asyncio
    import copy
    import time
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import UUID, uuid4

    from butlers.capability_broker.catalog import project_catalog
    from butlers.capability_broker.contracts import BrokerRefused, Decision, Reservation
    from butlers.capability_broker.handoff import sign_handoff, verify_handoff
    from butlers.capability_broker.ledger import BrokerLedger
    from butlers.capability_broker.transport.protocol import identity, reply, request
    from butlers.modules.approvals.models import ActionStatus
    from tests.capability_broker.test_published_catalog import registered_native_email_broker
    from tests.modules.approvals.test_goal_control import RuleStore

    class EmailStore(RuleStore):
        def __init__(self):
            super().__init__()
            self.lock = asyncio.Lock()
            self.in_transaction = False
            self.rule.update(
                tool_name="email_send_message",
                arg_constraints={
                    "to": {"type": "exact", "value": "owner@example.invalid"},
                    "subject": {"type": "any"},
                    "body": {"type": "any"},
                },
            )

        def is_in_transaction(self):
            return self.in_transaction

        @asynccontextmanager
        async def transaction(self):
            async with self.lock:
                self.in_transaction = True
                try:
                    async with super().transaction():
                        yield self
                finally:
                    self.in_transaction = False

        async def fetch(self, sql, *args):
            assert self.in_transaction
            if "FROM approval_rules" in sql:
                assert "FOR UPDATE" in sql
                return [dict(self.rule)] if standing else []
            if "FROM public.entities" in sql:
                assert "FOR SHARE" in sql
                return [{"id": native.owner}]
            raise AssertionError("unsupported_email_store_query")

        async def fetchrow(self, sql, *args):
            if "public.resolve_owner_triple" in sql:
                assert self.in_transaction
                return {"entity_id": native.owner, "is_primary": True}
            if "current_schema()" in sql and "current_user" in sql:
                return dict(
                    schema="messenger",
                    role="butler_messenger_rw",
                    unsafe=False,
                    schemas=["pg_catalog", "messenger", "public"],
                )
            if "SET park_authorization=$2" in sql:
                self.gate.update(
                    park_authorization=copy.deepcopy(args[1]),
                    park_authorization_digest=args[2],
                    park_call_id=args[3],
                    state_version=self.gate["state_version"] + 1,
                )
                return copy.deepcopy(self.gate)
            if "SET phase='dispatch_intent'" in sql:
                if self.gate["dispatch_intent_id"] is not None:
                    return None
                self.gate.update(
                    phase="dispatch_intent",
                    dispatch_intent_id=args[1],
                    dispatch_snapshot_digest=args[2],
                    dispatch_consumed_at=None,
                    state_version=self.gate["state_version"] + 1,
                )
                return copy.deepcopy(self.gate)
            if "SET dispatch_consumed_at=" in sql:
                if self.gate["dispatch_consumed_at"] is not None:
                    return None
                self.gate.update(
                    dispatch_consumed_at="software-observed-consume",
                    state_version=self.gate["state_version"] + 1,
                )
                return {"effect_id": args[0]}
            return await super().fetchrow(sql, *args)

    native = await registered_native_email_broker(monkeypatch)
    broker, conn, domains, token = native.broker, native.conn, native.domains, native.token
    catalog, facts = native.catalog, native.sends
    conn.exposed = standing
    conn.grants = (
        Grant(
            "messenger__email_send_message",
            resources=(
                (
                    "to",
                    (
                        "owner@example.invalid",
                        "other@example.invalid",
                    ),
                ),
            ),
        ),
    )
    store = EmailStore()
    store.goal_id = conn.goal
    original = {"to": "owner@example.invalid", "subject": "original", "body": "controlled"}
    store.arguments, store.digest = original, arguments_digest(original)
    now = int(time.time())
    broker.clock = lambda: now
    import base64

    from butlers.capability_broker import production
    from butlers.config import CapabilityBrokerTransportConfig
    from tests.capability_broker.test_service_transport import CONTROL

    # Actual captured daemon: active-module property, final catalog and original
    # handler map all come from real Messenger registration, not a stub.
    native.daemon.db = SimpleNamespace(pool=store)
    native.daemon.config.capability_broker_transport = (
        CapabilityBrokerTransportConfig.model_validate(
            {
                "enabled": True,
                "role": "owning-service",
                "domain": "messenger",
                "listen_host": "127.0.0.1",
                "listen_port": unused_tcp_port,
                "peer": {
                    "role": "broker-control",
                    "domain": "messenger",
                    "sans": sorted(CONTROL.sans),
                    "client_certificate": True,
                },
                "material": {
                    "ca_path": "fixture-ca",
                    "certificate_path": "fixture-cert",
                    "private_key_path": "fixture-key",
                },
                "verification_keys": {
                    broker.signer.kid: base64.b64encode(
                        broker.signer.private_key.public_key().public_bytes_raw()
                    ).decode()
                },
            }
        )
    )
    server_context, _ = material()
    monkeypatch.setattr(production, "material_context", lambda *a, **k: server_context)
    await production.start_private_transport(native.daemon)
    op = native.daemon._capability_transport_owner.server.app.operation
    peer = SimpleNamespace(role="broker-control", domain="messenger")
    seen, peer_errors = [], []

    async def exchange(self, value, *, assertion=None):
        assert not conn.in_transaction and conn.events[-1] == "commit"
        seen.append(copy.deepcopy(value))
        store.action_id = UUID(value["identity"]["action_id"])
        try:
            return await op(request(value, "messenger"), peer)
        except BaseException as exc:
            peer_errors.append(exc)
            raise

    monkeypatch.setattr(PrivateClient, "call", exchange)
    client = PrivateClient(
        TransportConfig(
            "https://localhost:4200/mcp",
            PeerPolicy("owning-service", "messenger", frozenset({"DNS:localhost"})),
        ),
        None,
    )
    broker.approvals = PrivateApprovals({"messenger": client})
    try:
        try:
            result = await broker.call(conn.goal, token, "messenger__email_send_message", original)
        except BrokerRefused:
            assert store.row is None and store.gate is None and facts == []
            assert seen == [] and native.permissions.reads == 0
            raise
    finally:
        await client.aclose()
        await production.close_private_transport(native.daemon)
    assert peer_errors == []
    assert result.status == "parked" and conn.used == 1 and facts == [] and domains.calls == []
    parked = seen[0]
    proof = verify_handoff(
        parked["handoff"], keys=op.keys, audience="messenger", purpose="PARK", now=now
    )
    assert proof.grant == conn.grants[0] and not proof.grant.park_only
    assert proof.central_rule_match is False and store.row["tool_args"] == original
    assert store.row["invocation_id"] == conn.invocation
    assert store.gate["park_call_id"] == UUID(proof.payload["d7"]["call_id"])
    async with conn.transaction():
        principal = await BrokerLedger(conn).authenticate(conn.goal, token)
    cap = next(
        c
        for c in project_catalog(catalog, domain="messenger").capabilities
        if c.identity == "messenger__email_send_message"
    )
    reservation = Reservation(
        principal,
        cap,
        Decision(Disposition.PARK),
        UUID(proof.payload["d7"]["call_id"]),
        result.effect_id,
        store.digest,
        conn.grants[0],
    )

    def envelope(purpose, res=reservation, version=None):
        return request(
            {
                "protocol": "broker-approvals-v2",
                "operation": purpose,
                "request_id": str(uuid4()),
                "identity": parked["identity"],
                "handoff": sign_handoff(
                    broker.signer,
                    res,
                    purpose=purpose,
                    issued_at=now,
                    original_args_digest=store.digest,
                    approval_ref=str(store.action_id) if version is not None else None,
                    snapshot_version=version,
                ),
            },
            "messenger",
        )

    # Pending state cannot execute, even with a genuine signed request.
    if not standing:
        with pytest.raises((ValueError, BrokerRefused)):
            await op(envelope("SNAPSHOT"), peer)
    assert facts == []
    if edited:
        store.row["tool_args"] = {
            "to": "other@example.invalid",
            "subject": "edited",
            "body": "controlled",
        }
    assert store.row["status"] == ("approved" if standing else "pending")
    assert store.rule["use_count"] == int(standing)
    if not standing:
        await op.owner.transition(
            identity(parked["identity"]),
            target_status=ActionStatus.APPROVED,
            decided_by="human:owner",
            event_actor="owner",
            event_reason="record",
        )
    snap_request = envelope("SNAPSHOT")
    snap = reply(await op(snap_request, peer), snap_request)["receipt"]
    assert snap["call"]["args"] == store.row["tool_args"] and facts == []
    # The same production validator used by central approved preparation still
    # resolves the edited snapshot against the complete current publication.
    async with conn.transaction():
        checked, decision = await BrokerLedger(conn).resolve_call(
            principal,
            cap.identity,
            snap["call"]["args"],
            arguments_digest(snap["call"]["args"]),
            broker.authorities,
        )
    assert checked == cap and decision.disposition is Disposition.PARK
    assert native.identity_reads == ["owner", "email", "owner", "email"]
    from butlers.capability_broker.policy import bind_recipient_policy

    async with conn.transaction():
        args = snap["call"]["args"]
        evidence = await broker.authorities.policy(conn, principal, cap, args)
        bound = bind_recipient_policy(cap, args, arguments_digest(args), evidence)
        assert bound.recipient == ("non_owner" if edited else "owner")
        if edited:
            held_grants = conn.grants
            conn.grants = (Grant(cap.identity, resources=(("to", (original["to"],)),)),)
            narrowed = replace(principal, grants=conn.grants)
            with pytest.raises(BrokerRefused):
                await BrokerLedger(conn).resolve_call(
                    narrowed, cap.identity, args, arguments_digest(args), broker.authorities
                )
            conn.grants = held_grants
    native.permissions.granted = not denied
    approved = replace(
        reservation,
        call_id=uuid4(),
        args_digest=arguments_digest(store.row["tool_args"]),
        decision=Decision(Disposition.DISPATCH),
    )
    claim = envelope("CLAIM", approved, snap["version"])
    stale = envelope(
        "CLAIM", replace(approved, args_digest=arguments_digest({"stale": True})), snap["version"]
    )
    with pytest.raises((ValueError, BrokerRefused)):
        await op(stale, peer)
    assert facts == [] and store.gate["dispatch_intent_id"] is None
    witness = reply(await op(claim, peer), claim)["receipt"]
    lookup = envelope("WITNESS", approved, snap["version"])
    assert reply(await op(lookup, peer), lookup)["receipt"] == witness and facts == []
    execute = envelope("EXECUTE", approved, snap["version"])

    async def invoke():
        outcome = await op(execute, peer)
        assert not store.in_transaction and store.gate["dispatch_consumed_at"] is not None
        if lost_ack:
            raise ConnectionError("controlled_execute_reply_loss")
        return outcome

    outcomes = await asyncio.gather(invoke(), invoke(), return_exceptions=True)
    from butlers.core.permissions import PermissionDenied

    assert not [
        v
        for v in outcomes
        if isinstance(v, BaseException)
        and not isinstance(v, (ValueError, PermissionDenied, ConnectionError))
    ]
    assert len(facts) == (0 if denied else 1), outcomes
    if facts:
        assert facts[0] == store.row["tool_args"]
    assert native.permissions.reads == 1
    assert sum(isinstance(v, PermissionDenied) for v in outcomes) == int(denied)
    assert sum(isinstance(v, dict) for v in outcomes) == (0 if lost_ack or denied else 1)
    assert sum(isinstance(v, ConnectionError) for v in outcomes) == (1 if lost_ack else 0)
    assert store.gate["dispatch_intent_id"] == UUID(witness["intent_id"])
    with pytest.raises((ValueError, BrokerRefused)):
        await op(execute, peer)
    assert len(facts) == (0 if denied else 1) and domains.calls == []


@pytest.mark.parametrize(
    "branch,matched",
    [
        ("owner", True),
        ("changed-owner", False),
        ("non-owner", False),
        ("pins", True),
        ("unbounded-to", False),
        ("park-only", False),
        ("always-per-event", False),
    ],
)
async def test_native_email_exposed_rule_requires_current_owner_or_all_exact_pins(
    monkeypatch, branch, matched
):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.core.tool_catalog import ToolCatalog
    from butlers.modules.approvals.goal_rules import matching_goal_rule
    from butlers.modules.approvals.models import GoalCallLineage
    from tests.capability_broker.test_published_catalog import registered_native_email_broker
    from tests.modules.approvals.test_goal_control import RuleStore

    native = await registered_native_email_broker(monkeypatch)
    conn = native.conn
    to = "other@example.invalid" if branch in {"non-owner", "pins"} else "owner@example.invalid"
    args = {"to": to, "subject": "free text", "body": "untrusted text"}
    if branch == "pins":
        # Include every declared optional dossier/default field: a three-field
        # content pin must not masquerade as the complete-template branch.
        for name, schema in native.catalog["email_send_message"].input_schema["properties"].items():
            args.setdefault(name, schema.get("default"))
    rule = RuleStore().rule
    rule.update(
        tool_name="email_send_message",
        created_at=datetime.now(UTC),
        arg_constraints={
            "to": {"type": "any"} if branch == "unbounded-to" else {"type": "exact", "value": to},
            "subject": {"type": "any"},
            "body": {"type": "any"},
        },
    )
    if branch == "pins":
        rule["arg_constraints"] = {k: {"type": "exact", "value": v} for k, v in args.items()}
    fetch = conn.fetch

    async def rows(sql, *values):
        if "FROM approval_rules" in sql:
            assert conn.in_transaction and "FOR UPDATE" in sql
            return [rule]
        if "FROM public.entities" in sql and branch == "changed-owner":
            assert conn.in_transaction and "FOR SHARE" in sql
            return [{"id": uuid4()}]
        return await fetch(sql, *values)

    conn.fetch = rows
    catalog = native.catalog
    if branch == "always-per-event":
        descriptors = tuple(
            replace(d, approval_policy={**d.approval_policy, "mode": "always_per_event"})
            if d.canonical_name == "email_send_message"
            else d
            for d in catalog.descriptors
        )
        catalog = ToolCatalog(descriptors, True, catalog.generation_digest)
    request = SimpleNamespace(
        origin_butler="messenger",
        tool_name="email_send_message",
        tool_args=args,
        goal_lineage=GoalCallLineage(
            conn.goal,
            conn.invocation,
            0,
            True,
            "owner",
            uuid4(),
            "messenger__email_send_message",
            arguments_digest(args),
        ),
    )
    grant = Grant(request.goal_lineage.goal_capability, park_only=branch == "park-only")
    async with conn.transaction():
        result = await matching_goal_rule(conn, request, catalog, grant)
    assert (result is not None) is matched
    if result is not None:
        assert result.id == rule["id"]
    assert native.sends == [] and conn.used == 0
