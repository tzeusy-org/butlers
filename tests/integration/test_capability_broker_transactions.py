"""Real sw047 transaction/role contracts, authored but NOT RUN in phase A.

The catalog/policy/domain collaborators are explicit controls. These tests can
prove actual SQL locks/budgets/dedup/exposure, never production catalog wiring or
owning-domain approvals. No new schema or migration is assumed.
"""

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from butlers.capability_broker.assertions import AssertionSigner
from butlers.capability_broker.contracts import (
    BrokerRefused,
    Capability,
    PolicyEvidence,
    Reason,
    mint_token,
)
from butlers.capability_broker.domains import Catalog, DomainReceipt
from butlers.capability_broker.ledger import BrokerLedger
from butlers.capability_broker.service import Broker
from butlers.core.goal_admission import AdmissionUnavailable, Evidence, Grant, digest, encode_grants
from butlers.core.tool_catalog import RecipientParameters
from tests.switchboard.goal_ledger_fixtures import admit

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@dataclass
class ControlledAuthority:
    capability: Capability

    async def catalog(self, connection, principal):
        return Catalog(principal.catalog_digest, (self.capability,))

    async def selection(self, connection, principal, capability, grant):
        raise AssertionError("no_selection_declared_in_control")

    async def policy(self, connection, principal, capability, args):
        return PolicyEvidence("none")


@dataclass
class ControlledDomain:
    calls: list = field(default_factory=list)

    async def dispatch(self, reservation, args, assertion, *, idempotency_key):
        self.calls.append(reservation.effect_id)
        return DomainReceipt("committed", receipt_ref="controlled-domain-receipt")

    async def park(self, *args):
        raise AssertionError("no_approval_bridge_in_phase_A")


async def running(owner, runtime, capability, *, max_calls=10):
    result = await admit(runtime, Evidence("message", "controlled-owner", owner=True))
    await owner.execute("UPDATE switchboard.goals SET status='running' WHERE id=$1", result.goal_id)
    await owner.execute(
        "UPDATE switchboard.budgets SET max_calls=$2 WHERE goal_id=$1", result.goal_id, max_calls
    )
    invocation, (token, hashed) = uuid4(), mint_token()
    grants = encode_grants((Grant(capability.identity),))
    await owner.execute(
        "INSERT INTO switchboard.invocations "
        "(id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens,token_hash) "
        "VALUES ($1,$2,0,'lead',0,'running',$3::jsonb,$4,5,500,$5)",
        invocation,
        result.goal_id,
        grants,
        digest(grants),
        hashed,
    )
    domain = ControlledDomain()
    broker = Broker(
        runtime,
        ControlledAuthority(capability),
        domain,
        AssertionSigner("software-test", Ed25519PrivateKey.generate()),
    )
    return broker, domain, result.goal_id, invocation, token


async def test_real_atomic_budget_race_cannot_forward_twice(ledger):
    owner, runtime = ledger
    cap = Capability(
        "health", "read", "read", "trusted", (), recipient_params=RecipientParameters()
    )
    broker, domain, goal, _, token = await running(owner, runtime, cap, max_calls=1)
    results = await asyncio.gather(
        broker.call(goal, token, cap.mcp_name, {}),
        broker.call(goal, token, cap.mcp_name, {}),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert len(domain.calls) == 1
    assert (
        await owner.fetchval("SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal)
        == 1
    )
    assert (
        await owner.fetchval("SELECT status FROM switchboard.goals WHERE id=$1", goal)
        == "budget_exhausted"
    )


async def test_real_effect_duplicate_returns_original_and_exposure_reads_back(ledger):
    owner, runtime = ledger
    cap = Capability(
        "health",
        "record",
        "record",
        "untrusted",
        (),
        idempotency="key_required",
        recipient_params=RecipientParameters(),
    )
    broker, domain, goal, invocation, token = await running(owner, runtime, cap)
    first = await broker.call(goal, token, cap.mcp_name, {"measurement": 1})
    with pytest.raises(BrokerRefused) as duplicate:
        await broker.call(goal, token, cap.mcp_name, {"measurement": 1.0})
    assert duplicate.value.reason is Reason.DUPLICATE
    assert duplicate.value.effect_id == first.effect_id and len(domain.calls) == 1
    # Independent acquisition after commit proves actual persistence, not an in-memory flag.
    async with runtime.acquire() as connection:
        assert await connection.fetchval("SELECT exposed FROM switchboard.goals WHERE id=$1", goal)
        assert await connection.fetchval(
            "SELECT exposed FROM switchboard.invocations WHERE id=$1", invocation
        )
        row = await connection.fetchrow(
            "SELECT status,receipt_ref FROM switchboard.effects WHERE id=$1", first.effect_id
        )
        assert row["status"] == "committed" and row["receipt_ref"] == "controlled-domain-receipt"


async def test_actual_stop_lock_serializes_before_call_and_refuses_forward(ledger):
    owner, runtime = ledger
    cap = Capability(
        "health", "read", "read", "trusted", (), recipient_params=RecipientParameters()
    )
    broker, domain, goal, _, token = await running(owner, runtime, cap)
    async with owner.acquire() as stop, stop.transaction():
        await stop.fetchrow("SELECT id FROM switchboard.goals WHERE id=$1 FOR UPDATE", goal)
        pending = asyncio.create_task(broker.call(goal, token, cap.mcp_name, {}))
        try:
            await stop.execute("UPDATE switchboard.goals SET status='stopped' WHERE id=$1", goal)
        except BaseException:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            raise
    with pytest.raises(BrokerRefused) as stopped:
        await pending
    assert stopped.value.reason is Reason.GOAL and domain.calls == []


async def test_runtime_role_and_token_revocation_are_checked_on_real_connection(ledger):
    owner, runtime = ledger
    cap = Capability(
        "health", "read", "read", "trusted", (), recipient_params=RecipientParameters()
    )
    broker, domain, goal, invocation, token = await running(owner, runtime, cap)
    async with owner.acquire() as connection, connection.transaction():
        with pytest.raises(AdmissionUnavailable):
            await BrokerLedger(connection).authenticate(goal, token)
    await owner.execute(
        "UPDATE switchboard.invocations SET status='returned',token_hash=NULL WHERE id=$1",
        invocation,
    )
    with pytest.raises(BrokerRefused) as revoked:
        await broker.call(goal, token, cap.mcp_name, {})
    assert revoked.value.reason is Reason.AUTHENTICATION and domain.calls == []
