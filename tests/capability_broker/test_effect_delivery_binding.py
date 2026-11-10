"""The broker records an owner delivery's binding: recipient at reserve, the rest at commit.

RFC 0038 S2.2 (bu-dhloi1.295, sw_066). The broker records the owner recipient and the named
channel when it reserves the effect. The domain's receipt reports the delivery reference and, for
a prompt, how long it accepts an answer; the broker writes those with the commit, only for a
committed effect, and only an owner delivery with a recorded channel can accept input. The trigger
then allows that write once, and only in that transition.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from butlers.capability_broker.domains import Delivery, DomainReceipt
from tests.capability_broker.test_broker_ledger import (
    NOTIFY,
    OWNER,
    SEND,
    _grant,
    docker_available,
    world,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)

ASK = {"entity_id": str(OWNER), "message": "Shall I book it?", "channel": "telegram"}


def prompt_receipt(expires=None):
    expires = expires or datetime.now(UTC) + timedelta(minutes=30)
    return DomainReceipt(
        "committed", "r", {}, delivery=Delivery(ref=f"tg:{uuid4()}", prompt_expires_at=expires)
    )


async def sent(w, capability, args, receipt):
    goal = await w.goal(_grant(capability))
    _, token = await w.invocation(goal, _grant(capability))
    w.forwarder.receipt = receipt
    result = await w.broker.call(goal, token, capability.identity, args)
    return result, await w.effect(result.effect_id)


async def test_an_owner_prompt_records_its_binding(broker_db_url):
    async with world(broker_db_url) as w:
        receipt = prompt_receipt()
        result, effect = await sent(w, NOTIFY, ASK, receipt)

        assert result.status == "committed" and effect["status"] == "committed"
        assert (effect["recipient_entity"], effect["recipient_channel"]) == (OWNER, "telegram")
        assert effect["delivery_ref"] == receipt.delivery.ref
        assert effect["prompt_accepts_input"] is True
        assert effect["prompt_expires_at"] == receipt.delivery.prompt_expires_at


async def test_the_recipient_is_recorded_before_dispatch(broker_db_url):
    async with world(broker_db_url) as w:
        goal = await w.goal(_grant(NOTIFY))
        _, token = await w.invocation(goal, _grant(NOTIFY))
        seen = {}

        async def spy(*args, **kwargs):
            seen["row"] = await w.owner.fetchrow(
                "SELECT status, recipient_entity, recipient_channel, delivery_ref "
                "FROM switchboard.effects WHERE goal_id = $1",
                goal,
            )
            return prompt_receipt()

        w.forwarder.dispatch = spy
        await w.broker.call(goal, token, NOTIFY.identity, ASK)

        assert seen["row"]["status"] == "dispatched"
        assert (seen["row"]["recipient_entity"], seen["row"]["recipient_channel"]) == (
            OWNER,
            "telegram",
        )
        assert seen["row"]["delivery_ref"] is None


async def test_a_delivery_without_a_prompt_binds_no_expiry(broker_db_url):
    async with world(broker_db_url) as w:
        plain = DomainReceipt("committed", "r", {}, delivery=Delivery(ref=f"tg:{uuid4()}"))
        _, effect = await sent(w, NOTIFY, ASK, plain)

        assert effect["delivery_ref"] == plain.delivery.ref
        assert (effect["prompt_accepts_input"], effect["prompt_expires_at"]) == (False, None)


async def test_a_receipt_with_no_delivery_changes_nothing(broker_db_url):
    async with world(broker_db_url) as w:
        _, effect = await sent(w, NOTIFY, ASK, DomainReceipt("committed", "r", {}))

        assert effect["status"] == "committed" and effect["delivery_ref"] is None
        assert effect["prompt_accepts_input"] is False


async def test_a_prompt_with_no_known_channel_is_not_recorded_as_accepting_input(broker_db_url):
    async with world(broker_db_url) as w:
        # SEND names no channel, so the broker has none to record: the binding check could not hold.
        _, effect = await sent(w, SEND, {"to": str(OWNER), "text": "ok?"}, prompt_receipt())

        assert effect["recipient_channel"] is None
        assert effect["prompt_accepts_input"] is False and effect["prompt_expires_at"] is None


async def test_a_failed_delivery_binds_nothing(broker_db_url):
    async with world(broker_db_url) as w:
        failed = DomainReceipt("failed", "r", {}, delivery=Delivery(ref="tg:never"))
        _, effect = await sent(w, NOTIFY, ASK, failed)

        assert effect["status"] == "failed" and effect["delivery_ref"] is None


async def test_a_recorded_binding_never_changes(broker_db_url):
    async with world(broker_db_url) as w:
        _, effect = await sent(w, NOTIFY, ASK, prompt_receipt())
        for column, value in (
            ("delivery_ref", "tg:forged"),
            ("recipient_channel", "email"),
            ("recipient_entity", uuid4()),
            ("prompt_expires_at", datetime.now(UTC) + timedelta(days=1)),
            ("prompt_accepts_input", False),
        ):
            with pytest.raises(asyncpg.PostgresError):
                await w.owner.execute(
                    f"UPDATE switchboard.effects SET {column} = $2 WHERE id = $1",
                    effect["id"],
                    value,
                )
