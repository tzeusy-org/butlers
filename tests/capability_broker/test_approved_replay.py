"""Central replay consumer controls; owning approval authority and PG are separate.

The rows below model central storage, never an authenticated owning snapshot.
The unavailable public replay path must remain unavailable until that producer
and its durable local dispatch witness exist.
"""

import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import pytest

from butlers.capability_broker.contracts import BrokerRefused, Capability, Reason
from butlers.capability_broker.domains import DomainReceipt
from butlers.core.capability_json import arguments_digest
from butlers.core.goal_admission import Grant, digest, encode_grants
from butlers.core.tool_catalog import RecipientParameters
from butlers.modules.approvals.goal_calls import StoredGoalCall
from butlers.modules.approvals.models import GoalCallLineage
from tests.capability_broker.test_broker import Pool, build
from tests.core.test_goal_stop import StopConnection

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class ReplayConnection(StopConnection):
    def __init__(self, grant):
        super().__init__(grant=grant)
        self.original_digest = arguments_digest({"value": "original"})
        self.action = uuid4()
        self.witness = None
        self.cancel_work = False
        self.row_changes = {}
        self.invocation_status = "returned"
        self.effect_status = "parked"
        self.budget = 1
        self.used = 1

    @asynccontextmanager
    async def transaction(self):
        before = copy.deepcopy(self.__dict__)
        self.in_transaction = True
        try:
            yield
            if self.fail_commit:
                raise RuntimeError("controlled_commit_response_loss")
            self.events.append("commit")
        except BaseException:
            self.__dict__.clear()
            self.__dict__.update(before)
            raise
        finally:
            self.in_transaction = False

    async def fetchrow(self, sql, *args):
        if "SELECT id,status,origin,exposed,catalog_digest,ceiling" in sql:
            return dict(
                id=self.goal,
                status=self.status,
                origin="owner",
                exposed=self.exposed,
                catalog_digest="v1",
                ceiling=encode_grants(self.grants),
            )
        if "FROM switchboard.invocations" in sql:
            return dict(
                id=self.invocation,
                depth=0,
                status=self.invocation_status,
                exposed=False,
                grant_snapshot=encode_grants(self.grants),
                grant_digest=digest(encode_grants(self.grants)),
            )
        if "SELECT e.*,a.owning_domain" in sql:
            return (
                dict(
                    id=self.action,
                    goal_id=self.goal,
                    invocation_id=self.invocation,
                    capability="health__write",
                    args_digest=self.original_digest,
                    status=self.effect_status,
                    owning_domain="health",
                    action_id=self.action,
                    approval_ref=str(self.action),
                    owning_approval_ref=str(self.action),
                    dispatch_intent_id=self.witness,
                    cancellation_pending=self.cancel_work,
                )
                | self.row_changes
            )
        if "UPDATE switchboard.goal_park_associations" in sql:
            if self.witness is not None or self.cancel_work:
                return None
            self.witness = args[2]
            self.events.append("intent")
            return {"effect_id": self.action}
        if "UPDATE switchboard.effects e SET" in sql:
            if (
                self.effect_status != "dispatched"
                or args[6] != self.original_digest
                or args[7] != str(self.action)
                or args[9] != self.witness
            ):
                return None
            self.effect_status = args[2]
            self.events.append("settled")
            return {"id": self.action}
        if "UPDATE switchboard.effects" in sql:
            self.effect_status = "approved" if "SET status='approved'" in sql else "dispatched"
            self.events.append(self.effect_status)
            return {"id": self.action}
        return await super().fetchrow(sql, *args)


def approved(*, edited=False):
    cap = Capability(
        "health",
        "write",
        "record",
        "trusted",
        (),
        approvals_available=True,
        approval_required=True,
        recipient_params=RecipientParameters(),
    )
    broker, _, authorities, domains, _ = build(cap, Grant(cap.identity, park_only=True))
    connection = ReplayConnection(Grant(cap.identity, park_only=True))
    broker.pool = Pool(connection)
    domains.connection = connection
    args = {"value": "edited" if edited else "original"}
    call = StoredGoalCall(
        connection.action,
        "write",
        args,
        GoalCallLineage(
            connection.goal,
            connection.invocation,
            0,
            False,
            "owner",
            connection.action,
            cap.identity,
            connection.original_digest,
            arguments_digest(args),
        ),
    )
    return broker, connection, authorities, domains, call


@pytest.mark.parametrize("edited", [False, True])
async def test_stored_approved_call_commits_one_intent_without_a_second_charge_or_forward(edited):
    broker, connection, _, domains, call = approved(edited=edited)
    connection.status = "settling"
    prepared = await broker.prepare_approved_dispatch(call)
    assert prepared.reservation.args_digest == arguments_digest(call.args)
    assert prepared.original_args_digest == connection.original_digest
    assert prepared.approval_ref == str(call.approval_ref)
    assert prepared.reservation.call_id == connection.witness
    assert connection.events == ["approved", "intent", "dispatched", "commit"]
    assert connection.used == 1 and domains.calls == []
    with pytest.raises(BrokerRefused) as duplicate:
        await broker.prepare_approved_dispatch(call)
    assert duplicate.value.reason is Reason.DUPLICATE
    assert domains.calls == [] and connection.used == 1


@pytest.mark.parametrize(
    "invalid",
    [
        "stopped",
        "cancel",
        "reference",
        "original",
        "approved",
        "grant",
        "catalog",
        "selection",
        "resource",
        "exposure",
        "budget",
        "schema",
    ],
)
async def test_stale_or_forged_approved_snapshot_cannot_create_dispatch_intent(invalid):
    broker, connection, authorities, domains, call = approved(edited=True)
    if invalid == "stopped":
        connection.status = "stopped"
    elif invalid == "cancel":
        connection.cancel_work = True
    elif invalid == "reference":
        connection.row_changes["approval_ref"] = str(uuid4())
    elif invalid == "original":
        connection.row_changes["args_digest"] = "a" * 64
    elif invalid == "approved":
        call.args["value"] = "unapproved mutation"
    elif invalid == "grant":
        connection.grants = ()
    elif invalid == "catalog":
        authorities.capability = replace(authorities.capability, name="other")
    elif invalid in {"selection", "resource"}:
        authorities.capability = replace(
            authorities.capability,
            resource_params=("value",),
            selection_bound=(("value", True),),
        )
        connection.grants = (Grant("health__write", (("value", ("original", "edited")),), True),)
        if invalid == "selection":
            authorities.active_selection = False
        else:
            connection.grants = (Grant("health__write", (("value", ("original",)),), True),)
    elif invalid == "exposure":
        connection.exposed = True
        authorities.capability = replace(authorities.capability, owner_authority=True)
    elif invalid == "budget":
        connection.budget = 0
    elif invalid == "schema":
        connection.schema_valid = False
    with pytest.raises(BrokerRefused):
        await broker.prepare_approved_dispatch(call)
    assert connection.witness is None and domains.calls == [] and connection.used == 1


async def test_approved_dispatch_commit_uncertainty_never_returns_forward_authority():
    broker, connection, _, domains, call = approved()
    connection.fail_commit = True
    with pytest.raises(BrokerRefused) as error:
        await broker.prepare_approved_dispatch(call)
    assert error.value.reason is Reason.UNAVAILABLE
    assert connection.witness is None and domains.calls == []
    # The model-facing/unsupplied owning integration remains closed.
    with pytest.raises(BrokerRefused) as missing_producer:
        await broker.replay_approved(call)
    assert missing_producer.value.reason is Reason.UNAVAILABLE


async def test_edited_dispatch_settles_after_Stop_with_original_binding_and_exposure_commit():
    broker, connection, authority, domains, call = approved(edited=True)
    authority.capability = replace(authority.capability, output_trust="untrusted")
    prepared = await broker.prepare_approved_dispatch(call)
    connection.status = "stopped"
    result = await broker.settle_approved_dispatch(
        prepared, DomainReceipt("committed", receipt_ref="controlled-result", value={"ok": True})
    )
    assert result.status == "committed" and result.value == {"ok": True}
    assert connection.effect_status == "committed" and connection.exposed
    assert connection.events[-2:] == ["settled", "commit"]
    assert prepared.original_args_digest != prepared.reservation.args_digest
    assert connection.used == 1 and domains.calls == []
    with pytest.raises(BrokerRefused) as repeat:
        await broker.settle_approved_dispatch(prepared, DomainReceipt("unknown"))
    assert repeat.value.reason is Reason.DUPLICATE
    assert connection.effect_status == "committed"


async def test_settlement_failure_never_returns_content_or_erases_the_dispatch_intent():
    broker, connection, authority, domains, call = approved(edited=True)
    authority.capability = replace(authority.capability, output_trust="untrusted")
    prepared = await broker.prepare_approved_dispatch(call)
    connection.fail_exposure = True
    with pytest.raises(BrokerRefused) as error:
        await broker.settle_approved_dispatch(
            prepared, DomainReceipt("committed", value={"controlled": "not returned"})
        )
    assert error.value.reason is Reason.UNAVAILABLE
    assert connection.effect_status == "dispatched" and not connection.exposed
    assert connection.witness == prepared.reservation.call_id and domains.calls == []


@pytest.mark.parametrize("field", ["intent", "original"])
async def test_settlement_rejects_a_different_intent_or_original_effect_digest(field):
    broker, connection, _, _, call = approved(edited=True)
    prepared = await broker.prepare_approved_dispatch(call)
    wrong = (
        replace(prepared, reservation=replace(prepared.reservation, call_id=uuid4()))
        if field == "intent"
        else replace(prepared, original_args_digest="c" * 64)
    )
    with pytest.raises(BrokerRefused):
        await broker.settle_approved_dispatch(wrong, DomainReceipt("committed"))
    assert connection.effect_status == "dispatched" and not connection.exposed


async def test_actual_call_PARK_modes_cannot_admit_third_party_actuation():
    cap = Capability(
        "home",
        "actuate",
        "actuate",
        "trusted",
        (),
        approvals_available=True,
        always_per_event=True,
        recipient_params=RecipientParameters(),
    )
    broker, connection, _, domains, token = build(cap, Grant(cap.identity, park_only=True))
    original = connection.fetchrow

    async def third_party_row(sql, *args):
        row = await original(sql, *args)
        if "FROM switchboard.goals" in sql and row is not None:
            return dict(row, origin="third_party")
        return row

    connection.fetchrow = third_party_row
    with pytest.raises(BrokerRefused) as refused:
        await broker.call(connection.goal, token, cap.identity, {})
    assert refused.value.reason is Reason.POLICY
    assert connection.used == 0 and connection.effect is None and domains.calls == []


@pytest.mark.parametrize("fault", [None, "claim_reply_loss", "execute_reply_loss", "stop"])
async def test_production_replay_commits_intent_witness_and_attempts_execute_once(
    monkeypatch, fault
):
    """Real orchestration/codec with SQL and peer protocol doubles, not PG."""
    from butlers.capability_broker.handoff import verify_handoff
    from butlers.capability_broker.owning import PrivateApprovals
    from butlers.capability_broker.transport.client import PrivateClient
    from butlers.capability_broker.transport.config import PeerPolicy, TransportConfig
    from butlers.modules.approvals.goal_dispatch import call_wire

    broker, conn, _, domains, call = approved(edited=True)
    conn.row_changes.update(
        park_call_id=uuid4(),
        selected_grant=encode_grants(conn.grants)[0],
        parked_catalog_digest="v1",
        allow_standing_rule=False,
    )
    original_fetchrow = conn.fetchrow

    async def fetchrow(sql, *args):
        if "SELECT id,status FROM switchboard.goals" in sql:
            return {"id": conn.goal, "status": conn.status}
        if "SET owning_witness_digest=" in sql:
            assert conn.in_transaction and args[2] == conn.witness
            assert args[3] == arguments_digest(call_wire(call))
            conn.events.append("owning_witness")
            return {"effect_id": conn.action}
        return await original_fetchrow(sql, *args)

    monkeypatch.setattr(conn, "fetchrow", fetchrow)
    seen = []

    async def exchange(self, value, *, assertion=None):
        assert assertion is None and not conn.in_transaction
        op = value["operation"]
        proof = verify_handoff(
            value["handoff"],
            keys={broker.signer.kid: broker.signer.private_key.public_key()},
            audience="health",
            purpose=op,
            now=int(broker.clock()),
        )
        assert proof.grant == conn.grants[0]
        seen.append(op)
        if op == "SNAPSHOT":
            result, receipt = "approved", {"version": 3, "call": call_wire(call)}
        elif op in {"CLAIM", "WITNESS"}:
            assert conn.witness is not None and conn.events[-1] == "commit"
            if fault == "claim_reply_loss" and op == "CLAIM":
                raise ConnectionError("controlled_committed_claim_reply_loss")
            if fault == "stop":
                conn.status = "stopped"
            result, receipt = (
                "claimed",
                {
                    "intent_id": str(conn.witness),
                    "snapshot_digest": arguments_digest(call_wire(call)),
                },
            )
        elif op == "EXECUTE":
            assert "owning_witness" in conn.events and conn.events[-1] == "commit"
            if fault == "execute_reply_loss":
                raise ConnectionError("controlled_consumption_reply_loss")
            result, receipt = "unknown", None
        else:
            raise AssertionError("unexpected_operation")
        return {
            **{k: value[k] for k in ("protocol", "operation", "request_id", "identity")},
            "outcome": result,
            "receipt": receipt,
        }

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
        result = await broker.replay_approved(conn.goal, conn.action)
        assert result.status == "unknown" and result.value is None
        assert seen.count("EXECUTE") == (0 if fault == "stop" else 1)
        assert seen.count("WITNESS") == (1 if fault == "claim_reply_loss" else 0)
        assert conn.used == 1 and not domains.calls
        with pytest.raises(BrokerRefused):
            await broker.replay_approved(conn.goal, conn.action)
        assert seen.count("EXECUTE") == (0 if fault == "stop" else 1)
    finally:
        await client.aclose()
