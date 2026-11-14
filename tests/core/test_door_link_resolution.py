"""A vendor door resolves independently in the sender and the receiver, or degrades inert."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any, cast

import pytest
from pydantic import ValidationError

from butlers.core import delivery_rendering
from butlers.core import vendor_doors as flag
from butlers.tools.switchboard.contracts import (
    NotifyRequestV1,
    parse_notify_request,
)
from butlers.tools.switchboard.notification import self_links

DOOR = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ROW = {
    "id": DOOR,
    "version": 4,
    "kind": "cancel",
    "host": "example.com",
    "template": "https://my.shop.example.com/orders/{order}/cancel",
    "slot_types": {"order": "order_ref"},
}
REF = {"kind": "door", "door_id": str(DOOR), "version": 4, "slots": {"order": "A-1"}}
FALLBACK_URL = "https://dash.example.com/butlers/finance?tab=finances"
DOOR_URL = "https://my.shop.example.com/orders/A-1/cancel"
MARKER = "[link not verified]"


class Conn:
    def __init__(self, row=ROW, error: BaseException | None = None, delay: float = 0):
        self.row, self.error, self.delay = row, error, delay
        self.in_transaction = False

    async def fetchrow(self, query, *args):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.row

    def is_in_transaction(self):
        return self.in_transaction


class Pool:
    def __init__(self, conn: Conn | None = None, acquire_error: Exception | None = None):
        self.conn = conn or Conn()
        self.acquire_error = acquire_error
        self.acquired = 0

    @contextlib.asynccontextmanager
    async def acquire(self, timeout=None):  # noqa: ASYNC109
        if self.acquire_error:
            raise self.acquire_error
        self.acquired += 1
        yield self.conn


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    monkeypatch.setattr(flag, "VENDOR_DOORS_LIVE", True)
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example.com")


def _request(intent: str = "send", channel: str = "telegram", **extra) -> NotifyRequestV1:
    payload = {
        "schema_version": "notify.v1",
        "origin_butler": "finance",
        "delivery": {
            "intent": intent,
            "channel": channel,
            "recipient": "12345",
            "message": "Your plan renews soon.",
        },
        "actions": [{"verb": "open_door", "reference": dict(REF)}],
        **extra,
    }
    return parse_notify_request(payload)


def _urls(request: NotifyRequestV1) -> list[str]:
    return [getattr(a, "url", "") for a in request.actions or ()]


@pytest.mark.parametrize("intent", ["send", "insight"])
async def test_a_live_door_resolves_to_a_server_minted_vendor_link(intent):
    out = await self_links.resolve_request_links(_request(intent), Pool())
    assert _urls(out) == [DOOR_URL]
    action = out.actions[0]
    assert action.verb == "open_link" and action.provenance == "vendor_door"
    assert MARKER not in out.delivery.message


async def _assert_degraded_to_dashboard(out: NotifyRequestV1) -> None:
    assert out.delivery.message.endswith(MARKER)
    assert _urls(out) == [FALLBACK_URL]
    assert out.actions[0].provenance == "dashboard"
    assert DOOR_URL not in str(out.model_dump())


async def test_stale_version_degrades_to_the_fixed_dashboard_page():
    pool = Pool(Conn(row={**ROW, "version": 5}))
    await _assert_degraded_to_dashboard(await self_links.resolve_request_links(_request(), pool))


async def test_revoked_door_is_absent_from_the_view_and_degrades():
    pool = Pool(Conn(row=None))
    await _assert_degraded_to_dashboard(await self_links.resolve_request_links(_request(), pool))


async def test_flag_off_degrades_without_reading_the_registry(monkeypatch):
    monkeypatch.setattr(flag, "VENDOR_DOORS_LIVE", False)
    await _assert_degraded_to_dashboard(await self_links.resolve_request_links(_request(), Pool()))


async def test_a_slow_read_times_out_and_degrades(monkeypatch):
    from butlers.core import vendor_door_resolver

    monkeypatch.setattr(vendor_door_resolver, "READ_TIMEOUT_S", 0.01)
    pool = Pool(Conn(delay=1))

    async def slow(*args, **kwargs):
        return await vendor_door_resolver.resolve_door(*args, deadline_s=0.01, **kwargs)

    monkeypatch.setattr(vendor_door_resolver, "resolve_door", slow)
    await _assert_degraded_to_dashboard(await self_links.resolve_request_links(_request(), pool))


async def test_a_pool_failure_degrades():
    pool = Pool(acquire_error=OSError("pool down"))
    await _assert_degraded_to_dashboard(await self_links.resolve_request_links(_request(), pool))


async def test_no_pool_degrades():
    await _assert_degraded_to_dashboard(await self_links.resolve_request_links(_request(), None))


async def test_a_cancelled_read_still_propagates():
    pool = Pool(Conn(error=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await self_links.resolve_request_links(_request(), pool)


async def test_exposure_never_reads_the_registry_and_gives_plain_text():
    pool = Pool()
    token = delivery_rendering._inert_content.set(True)
    try:
        out = await self_links.resolve_request_links(_request(), pool)
    finally:
        delivery_rendering._inert_content.reset(token)
    assert pool.acquired == 0
    assert out.actions is None and out.delivery.message.endswith(MARKER)


async def test_a_request_marked_inert_is_plain_text_too():
    # The contract refuses a door under inert links at construction; a server-side copy that
    # was marked inert afterwards (model_copy does not re-validate) must still never link.
    pool = Pool()
    marked = _request().model_copy(update={"render_links_inert": True})
    out = await self_links.resolve_request_links(marked, pool)
    assert pool.acquired == 0 and out.actions is None and out.delivery.message.endswith(MARKER)


async def test_a_channel_without_buttons_is_plain_text():
    pool = Pool()
    out = await self_links.resolve_request_links(
        _request(channel="email"), pool, supports_buttons=False
    )
    assert pool.acquired == 0 and out.actions is None and out.delivery.message.endswith(MARKER)


async def test_an_unresolvable_fallback_leaves_text_and_marker_with_no_url(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL")
    pool = Pool(Conn(row=None))
    out = await self_links.resolve_request_links(_request(), pool)
    assert out.delivery.message.endswith(MARKER)
    assert all(not url for url in _urls(out))


async def test_the_registry_read_uses_its_own_connection_outside_any_transaction():
    conn = Conn()
    seen: list[bool] = []
    original = conn.fetchrow

    async def spy(query, *args):
        seen.append(conn.is_in_transaction())
        return await original(query, *args)

    conn.fetchrow = spy  # type: ignore[method-assign]
    pool = Pool(conn)
    await self_links.resolve_request_links(_request(), pool)
    assert pool.acquired == 1 and seen == [False]


async def test_a_request_without_a_door_takes_the_ordinary_path():
    request = parse_notify_request(
        {
            "schema_version": "notify.v1",
            "origin_butler": "finance",
            "delivery": {"intent": "send", "channel": "telegram", "message": "hi"},
        }
    )
    pool = Pool()
    assert await self_links.resolve_request_links(request, pool) == request
    assert pool.acquired == 0


# --- the fallback is the only non-door action a send may carry -------------------------------


def test_a_producer_cannot_submit_the_fallback_or_an_open_link_on_send():
    for action in (
        {
            "verb": "open_dashboard",
            "reference": {"kind": "self", "route_key": "finance_subscriptions", "params": {}},
        },
        {
            "verb": "open_link",
            "reference": REF,
            "label": "x",
            "url": "https://evil.example/",
            "host": "evil.example",
            "provenance": "vendor_door",
        },
    ):
        with pytest.raises(ValidationError):
            parse_notify_request({**_request().model_dump(mode="json"), "actions": [action]})


async def test_only_the_fixed_fallback_route_is_admitted_on_send():
    payload = _request().model_dump(mode="json", exclude_none=True)
    payload["actions"] = [
        {
            "verb": "open_dashboard",
            "reference": {"kind": "self", "route_key": "approvals_queue", "params": {}},
        }
    ]
    with pytest.raises(ValidationError):
        NotifyRequestV1.model_validate(
            payload,
            context=__import__(
                "butlers.tools.switchboard.contracts", fromlist=["x"]
            )._RESOLVED_LINK_CONTEXT,
        )


# --- the receiver re-resolves from the delivered form ----------------------------------------


async def test_the_receiver_ignores_a_delivered_url_and_rereads_the_registry():
    sent = await self_links.resolve_request_links(_request(), Pool())
    wire = sent.model_dump(mode="json", exclude_none=True)
    wire["actions"][0]["url"] = "https://evil.example/steal"
    received = await self_links.parse_delivered_notify_request_resolving_doors(
        wire, Pool(Conn(row={**ROW, "version": 9}))
    )
    assert _urls(received) == [FALLBACK_URL]
    assert received.delivery.message.endswith(MARKER)


async def test_the_receiver_rechecks_exposure_from_the_delivered_request():
    sent = await self_links.resolve_request_links(_request(), Pool())
    wire = sent.model_dump(mode="json", exclude_none=True)
    wire["render_links_inert"] = True
    pool = Pool()
    received = await self_links.parse_delivered_notify_request_resolving_doors(wire, pool)
    assert pool.acquired == 0 and received.actions is None
    assert received.delivery.message.endswith(MARKER)


async def test_the_receiver_resolves_a_live_door():
    wire = (await self_links.resolve_request_links(_request(), Pool())).model_dump(
        mode="json", exclude_none=True
    )
    received = await self_links.parse_delivered_notify_request_resolving_doors(wire, Pool())
    assert _urls(cast(Any, received)) == [DOOR_URL]


async def test_a_delivered_fallback_survives_the_receiver_parse():
    degraded = await self_links.resolve_request_links(_request(), Pool(Conn(row=None)))
    wire = degraded.model_dump(mode="json", exclude_none=True)
    received = await self_links.parse_delivered_notify_request_resolving_doors(wire, Pool())
    assert _urls(received) == [FALLBACK_URL]


async def test_a_forged_foreign_open_link_on_send_is_refused_by_the_receiver():
    wire = (await self_links.resolve_request_links(_request(), Pool())).model_dump(
        mode="json", exclude_none=True
    )
    wire["actions"] = [
        {
            "verb": "open_link",
            "reference": {"kind": "self", "route_key": "approvals_queue", "params": {}},
            "label": "x",
            "url": "https://dash.example.com/approvals",
            "host": "dash.example.com",
            "provenance": "dashboard",
        }
    ]
    with pytest.raises(ValidationError):
        await self_links.parse_delivered_notify_request_resolving_doors(wire, Pool())


# --- approval requests are untouched ---------------------------------------------------------


async def test_approval_request_resolution_is_unchanged_and_reads_no_registry():
    token = "tok"
    request = parse_notify_request(
        {
            "schema_version": "notify.v1",
            "origin_butler": "finance",
            "delivery": {
                "intent": "approval_request",
                "channel": "telegram",
                "recipient": "1",
                "message": "Approve?",
            },
            "actions": [
                {"verb": "approve", "callback_token": token},
                {
                    "verb": "open_dashboard",
                    "reference": {"kind": "self", "route_key": "approvals_queue", "params": {}},
                },
            ],
        }
    )
    pool = Pool()
    out = await self_links.resolve_request_links(request, pool)
    assert out == self_links.resolve_self_links(request) and pool.acquired == 0


def test_the_logged_request_never_keeps_a_door_url():
    import importlib
    import inspect

    deliver = importlib.import_module("butlers.tools.switchboard.notification.deliver")
    source = inspect.getsource(deliver._deliver_via_notify_request)
    assert 'logged_action.pop("url", None)' in source


# --- Telegram renders the resolved link as one server-built button ---------------------------


async def _telegram_command(actions, **kwargs):
    from unittest.mock import AsyncMock

    from butlers.core_tools._routing import _build_routed_delivery_command

    telegram = type("T", (), {"_send_message": AsyncMock(return_value={"message_id": "1"})})()
    command = await _build_routed_delivery_command(
        daemon=None,
        modules_by_name={"telegram": telegram},
        channel="telegram",
        intent="send",
        message_text="Your plan renews soon.",
        origin="finance",
        recipient="12345",
        subject=None,
        notify_context=None,
        actions=actions,
        **kwargs,
    )
    assert command is not None
    await command.execute()
    return telegram._send_message


async def test_a_resolved_door_is_one_url_button_and_the_text_carries_no_url():
    out = await self_links.resolve_request_links(_request(), Pool())
    send = await _telegram_command(out.actions)
    args, kwargs = send.await_args
    assert DOOR_URL not in args[1]
    assert kwargs["reply_markup"] == {
        "inline_keyboard": [[{"text": "Open example.com (vendor_door)", "url": DOOR_URL}]]
    }
    assert kwargs["server_built_reply_markup"] is True


async def test_a_degraded_door_is_the_fixed_page_button_with_the_marker_in_the_text():
    out = await self_links.resolve_request_links(_request(), Pool(Conn(row=None)))
    send = await _telegram_command(out.actions)
    assert send.await_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["url"] == FALLBACK_URL


async def test_plain_text_fallback_sends_no_button():
    send = await _telegram_command(None)
    send.assert_awaited_once_with("12345", "[finance] Your plan renews soon.")
