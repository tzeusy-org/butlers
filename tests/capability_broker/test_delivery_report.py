"""A domain's delivery report becomes a reply binding only through the broker (RFC 0038 S2.2).

The broker derives the reply ref from the report's scope and the owner entity and channel it
recorded itself at reserve, so a domain cannot steer which identity a reply binds to, and a
prompt never outlives the ``awaits_reply_seconds`` the call named.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.capability_broker.domains import DeliveryReport, DomainReceipt
from butlers.capability_broker.forwarder import parse_delivery_report
from butlers.core.reply_scope import scoped_reply_ref
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
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]
database = pytest.mark.asyncio(loop_scope="session")
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)

ASK = {"entity_id": str(OWNER), "message": "Shall I book it?", "channel": "telegram"}
SCOPE = dict(transport="telegram", endpoint="telegram:bot:@butler", conversation="telegram:100")


def report(expires=None, **over):
    fields = {**SCOPE, "locator": "100:77", "prompt_expires_at": expires, **over}
    return DeliveryReport(**fields)


async def sent(w, capability, args, rep):
    goal = await w.goal(_grant(capability))
    _, token = await w.invocation(goal, _grant(capability))
    w.forwarder.receipt = DomainReceipt("committed", "r", {}, report=rep)
    result = await w.broker.call(goal, token, capability.identity, args)
    return await w.effect(result.effect_id)


def inbound_ref(locator="100:77"):
    """What admission derives from the owner's reply, from the inbound message's own scope."""
    return scoped_reply_ref(
        **SCOPE, recipient_entity=OWNER, recipient_channel="telegram_bot", locator=locator
    )


@database
async def test_the_reply_ref_is_derived_from_the_effects_own_recipient(broker_db_url):
    async with world(broker_db_url) as w:
        in_ten = datetime.now(UTC) + timedelta(minutes=10)
        effect = await sent(w, NOTIFY, {**ASK, "awaits_reply_seconds": 600}, report(in_ten))

        assert effect["delivery_ref"] == inbound_ref()
        assert (effect["recipient_entity"], effect["recipient_channel"]) == (OWNER, "telegram_bot")
        assert effect["prompt_accepts_input"] is True
        assert effect["prompt_expires_at"] == in_ten  # inside the window the call named


@database
async def test_the_expiry_never_exceeds_the_window_the_call_named(broker_db_url):
    async with world(broker_db_url) as w:
        far = datetime.now(UTC) + timedelta(days=30)
        effect = await sent(w, NOTIFY, {**ASK, "awaits_reply_seconds": 120}, report(far))

        assert effect["prompt_accepts_input"] is True
        assert effect["prompt_expires_at"] <= datetime.now(UTC) + timedelta(seconds=120)


@database
async def test_a_message_not_asked_to_await_an_answer_is_bound_but_accepts_none(broker_db_url):
    async with world(broker_db_url) as w:
        far = datetime.now(UTC) + timedelta(hours=1)
        effect = await sent(
            w, NOTIFY, ASK, report(far)
        )  # the domain claims a prompt; the call did not

        assert effect["delivery_ref"] == inbound_ref()
        assert (effect["prompt_accepts_input"], effect["prompt_expires_at"]) == (False, None)


@pytest.mark.parametrize("asked", [None, 0, 29, 86401, True, "300"])
@database
async def test_an_unusable_window_binds_no_prompt(broker_db_url, asked):
    async with world(broker_db_url) as w:
        far = datetime.now(UTC) + timedelta(hours=1)
        effect = await sent(w, NOTIFY, {**ASK, "awaits_reply_seconds": asked}, report(far))
        assert effect["prompt_accepts_input"] is False


@database
async def test_a_lapsed_or_naive_expiry_binds_no_prompt(broker_db_url):
    async with world(broker_db_url) as w:
        args = {**ASK, "awaits_reply_seconds": 300}
        lapsed = await sent(
            w, NOTIFY, args, report(datetime.now(UTC) - timedelta(seconds=1), locator="100:1")
        )
        naive = await sent(
            w, NOTIFY, args, report(datetime.now() + timedelta(minutes=5), locator="100:2")
        )
        assert (lapsed["prompt_accepts_input"], naive["prompt_accepts_input"]) == (False, False)


@database
async def test_a_call_with_no_recorded_recipient_binds_nothing(broker_db_url):
    async with world(broker_db_url) as w:
        # SEND names no channel, so the broker recorded none: nothing to scope a reply to.
        effect = await sent(
            w,
            SEND,
            {"to": str(OWNER), "text": "ok?", "awaits_reply_seconds": 300},
            report(datetime.now(UTC) + timedelta(minutes=5)),
        )
        assert effect["delivery_ref"] is None and effect["prompt_accepts_input"] is False


@database
async def test_a_report_with_a_different_scope_binds_a_different_ref(broker_db_url):
    async with world(broker_db_url) as w:
        soon = datetime.now(UTC) + timedelta(minutes=5)
        args = {**ASK, "awaits_reply_seconds": 300}
        other = await sent(w, NOTIFY, args, report(soon, locator="100:78"))
        assert other["delivery_ref"] == inbound_ref("100:78") != inbound_ref("100:77")


@database
async def test_no_report_is_the_behaviour_it_always_was(broker_db_url):
    async with world(broker_db_url) as w:
        effect = await sent(w, NOTIFY, {**ASK, "awaits_reply_seconds": 300}, None)
        assert effect["status"] == "committed" and effect["delivery_ref"] is None


def test_the_forwarder_reads_only_an_exactly_well_formed_report():
    good = {**SCOPE, "locator": "100:77", "prompt_expires_at": "2030-01-01T00:00:00+00:00"}
    parsed = parse_delivery_report(good)
    assert parsed is not None and parsed.prompt_expires_at.tzinfo is not None
    no_expiry = {k: v for k, v in good.items() if k != "prompt_expires_at"}
    assert parse_delivery_report(no_expiry) == report(None)
    for bad in (
        None,
        "x",
        [],
        {},
        {**good, "extra": "x"},
        {**good, "locator": ""},
        {**good, "endpoint": 5},
        {k: v for k, v in good.items() if k != "conversation"},
    ):
        assert parse_delivery_report(bad) is None
    # An expiry that is not a usable aware time is no prompt, but the delivery still reports.
    for expiry in ("soon", "2030-01-01T00:00:00", 5):
        parsed = parse_delivery_report({**good, "prompt_expires_at": expiry})
        assert parsed is not None and parsed.prompt_expires_at is None
