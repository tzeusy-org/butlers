"""Actual broker orchestration with stateful SQL controls; no real PG/transport claim."""

from dataclasses import replace
from uuid import uuid4

import pytest

from butlers.capability_broker.contracts import BrokerRefused, Capability
from butlers.capability_broker.ledger import BrokerLedger
from butlers.core.goal_admission import Grant
from butlers.core.goal_stop import GoalStop
from butlers.core.tool_catalog import RecipientParameters
from tests.capability_broker.test_broker import Pool, build
from tests.core.test_goal_stop import StopConnection

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def parked_broker():
    capability = Capability(
        "health",
        "write",
        "record",
        "trusted",
        (),
        approvals_available=True,
        recipient_params=RecipientParameters(),
    )
    broker, connection, authority, domain, token = build(
        capability, Grant(capability.identity, park_only=True)
    )
    connection = StopConnection(grant=Grant(capability.identity, park_only=True))
    broker.pool = Pool(connection)
    domain.connection = connection
    return broker, connection, authority, domain, token, capability


async def test_park_association_commits_before_the_owning_forward():
    broker, connection, _, domain, token, cap = parked_broker()
    original = domain.park

    async def park(reservation, arguments, assertion):
        assert reservation.effect_id in connection.associations
        assert connection.events[-1] == "commit"
        return await original(reservation, arguments, assertion)

    domain.park = park
    result = await broker.call(connection.goal, token, cap.identity, {"value": "controlled"})
    assert result.status == "parked" and len(domain.calls) == 1
    assert connection.used == 1


async def test_lost_PARK_response_keeps_a_durable_association_for_Stop():
    broker, connection, _, domain, token, cap = parked_broker()

    async def lost(reservation, arguments, assertion):
        domain.calls.append(reservation)
        raise TimeoutError("controlled_missing_response")

    domain.park = lost
    result = await broker.call(connection.goal, token, cap.identity, {})
    assert result.status == "unknown" and len(domain.calls) == 1
    assert result.effect_id in connection.associations
    receipt = await GoalStop(broker.pool).stop_goal(connection.goal, uuid4(), "b" * 64)
    assert receipt.cancellation_work == 1 and receipt.owning_cancellation_pending
    assert connection.effects[result.effect_id]["status"] == "unknown"
    assert result.effect_id in connection.work
    assert connection.used == 1 and len(domain.calls) == 1


async def test_Stop_during_PARK_attaches_late_reference_without_resurrection_or_second_action():
    broker, connection, _, domain, token, cap = parked_broker()
    original = domain.park

    async def park(reservation, arguments, assertion):
        local = await GoalStop(broker.pool).stop_goal(connection.goal, uuid4(), "b" * 64)
        assert local.cancellation_work == 1 and not connection.in_transaction
        return await original(reservation, arguments, assertion)

    domain.park = park
    result = await broker.call(connection.goal, token, cap.identity, {})
    reservation = domain.calls[0][0]
    assert result.status == "unknown" and result.approval_ref is None
    assert connection.associations[result.effect_id]["approval_ref"] == "owner-row"
    assert connection.effects[result.effect_id]["status"] == "requested"
    assert connection.work[result.effect_id]["state"] == "pending"
    assert connection.status == "stopped" and connection.used == 1 and len(domain.calls) == 1
    async with connection.transaction():
        assert (
            await BrokerLedger(connection).settle(reservation, "parked", reference="owner-row")
            == "unknown"
        )
    with pytest.raises(BrokerRefused):
        async with connection.transaction():
            await BrokerLedger(connection).settle(reservation, "parked", reference="different-row")
    assert connection.associations[result.effect_id]["approval_ref"] == "owner-row"
    with pytest.raises(BrokerRefused):
        async with connection.transaction():
            await BrokerLedger(connection).settle(
                replace(reservation, args_digest="c" * 64), "parked", reference="owner-row"
            )
    assert len(domain.calls) == 1 and connection.used == 1
