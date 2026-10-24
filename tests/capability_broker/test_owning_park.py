"""Actual broker -> private MCP PARK software path, not an owning SQL commit proof."""

import copy
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from butlers.capability_broker.handoff import verify_handoff
from butlers.capability_broker.owning import PrivateApprovals
from butlers.capability_broker.transport.client import PrivateClient
from butlers.capability_broker.transport.config import TransportConfig
from butlers.capability_broker.transport.protocol import receipt_digest
from butlers.capability_broker.transport.server import PrivateApp, PrivateServer
from butlers.core.goal_stop import GoalStop
from tests.capability_broker.test_goal_stop_association import parked_broker
from tests.capability_broker.test_service_transport import (
    CONTROL,
    SERVICE,
    fixture_reply,
)
from tests.capability_broker.test_service_transport import (
    material as material,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@asynccontextmanager
async def running(material, operation):
    server_context, client_context = material()
    server = PrivateServer(PrivateApp("health", operation), server_context, CONTROL)
    async with server.run():
        client = PrivateClient(
            TransportConfig(f"https://localhost:{server.port}/mcp", SERVICE), client_context
        )
        try:
            yield server, client
        finally:
            await client.aclose()
    assert not server.protocols and not server.handshakes and server.slot_count == 0


async def test_committed_broker_intent_reaches_actual_private_MCP_with_exact_lineage(material):
    broker, connection, _, domains, token, cap = parked_broker()
    observed = []

    async def operation(value, peer, assertion):
        assert not connection.in_transaction and connection.events[-1] == "commit"
        effect = value["identity"]["effect_id"]
        assert effect == value["identity"]["action_id"]
        from uuid import UUID

        assert UUID(effect) in connection.associations
        assert assertion is None
        proof = verify_handoff(
            value["handoff"],
            keys={broker.signer.kid: broker.signer.private_key.public_key()},
            audience="health",
            purpose="PARK",
            now=100,
        )
        claims = proof.payload["d7"]
        assert claims["effect_id"] == effect
        assert claims["goal_id"] == value["identity"]["goal_id"]
        assert claims["invocation_id"] == str(connection.invocation)
        assert claims["approval_ref"] is None and peer.role == "broker-control"
        assert proof.central_rule_match is False
        observed.append(copy.deepcopy(value))
        answer = fixture_reply(value, "parked")
        answer["receipt"]["approval_ref"] = effect
        answer["receipt"]["digest"] = receipt_digest(answer["receipt"])
        return answer

    async with running(material, operation=operation) as (_, client):
        broker.approvals = PrivateApprovals({"health": client})
        result = await broker.call(connection.goal, token, cap.identity, {"value": "software"})
    assert result.status == "parked" and result.approval_ref == str(result.effect_id)
    assert observed[0]["arguments"] == {"value": "software"}
    assert connection.used == 1 and len(observed) == 1 and not domains.calls


@pytest.mark.parametrize("fault", ["unknown", "refused", "reference", "identity", "transport"])
async def test_private_PARK_uncertainty_never_falls_back_or_retries(material, fault):
    broker, connection, _, domains, token, cap = parked_broker()
    calls = []

    async def operation(value, _peer, _assertion):
        calls.append(copy.deepcopy(value))
        if fault == "transport":
            raise RuntimeError("controlled")
        answer = fixture_reply(value, "parked")
        answer["receipt"]["approval_ref"] = value["identity"]["action_id"]
        if fault in {"unknown", "refused"}:
            answer.update(outcome=fault, receipt=None)
        else:
            if fault == "reference":
                answer["receipt"]["approval_ref"] = str(uuid4())
            if fault == "identity":
                answer["identity"]["effect_id"] = str(uuid4())
            answer["receipt"]["digest"] = receipt_digest(answer["receipt"])
        return answer

    async with running(material, operation=operation) as (_, client):
        broker.approvals = PrivateApprovals({"health": client})
        result = await broker.call(connection.goal, token, cap.identity, {})
    assert result.status == "unknown" and result.approval_ref is None
    assert connection.effects[result.effect_id]["status"] == "unknown"
    assert result.effect_id in connection.associations
    assert len(calls) == 1 and not domains.calls and connection.used == 1


async def test_Stop_during_private_PARK_keeps_late_reference_without_resurrection(material):
    broker, connection, _, domains, token, cap = parked_broker()

    async def operation(value, _peer, _assertion):
        await GoalStop(broker.pool).stop_goal(connection.goal, uuid4(), "b" * 64)
        answer = fixture_reply(value, "parked")
        answer["receipt"]["approval_ref"] = value["identity"]["action_id"]
        answer["receipt"]["digest"] = receipt_digest(answer["receipt"])
        return answer

    async with running(material, operation=operation) as (_, client):
        broker.approvals = PrivateApprovals({"health": client})
        result = await broker.call(connection.goal, token, cap.identity, {})
    assert result.status == "unknown" and connection.status == "stopped"
    assert connection.associations[result.effect_id]["approval_ref"] == str(result.effect_id)
    assert connection.work[result.effect_id]["state"] == "pending"
    assert not domains.calls and connection.used == 1
