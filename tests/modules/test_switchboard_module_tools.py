"""Unit tests for Switchboard module MCP tool registration."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastmcp import FastMCP as RegisteredFastMCP

from butlers.testing.mcp import registered_tools


async def test_registered_deliver_authenticates_only_material_recovery(monkeypatch, caplog):
    from butlers.modules._roster_switchboard import tools

    pool = object()
    delivery_module = importlib.import_module("butlers.tools.switchboard.notification.deliver")
    actual_deliver = delivery_module.deliver

    async def delivery_boundary(*args, **kwargs):
        if kwargs["notify_request"].get("recovery") is not None:
            return await actual_deliver(*args, **kwargs)
        return {"status": "sent"}

    deliver = AsyncMock(side_effect=delivery_boundary)
    authenticate = AsyncMock(wraps=tools.verify_switchboard_recovery)
    monkeypatch.setattr(delivery_module, "deliver", deliver)
    monkeypatch.setattr(tools, "verify_switchboard_recovery", authenticate)
    mcp = RegisteredFastMCP("remaining4-switchboard-tools")

    tools.register_tools(
        mcp,
        SimpleNamespace(_get_pool=lambda: pool),
        SimpleNamespace(groups=["routing"]),
    )

    ordinary = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": "ordinary",
            "recipient": "owner",
        },
        "recovery": None,
    }
    await (await registered_tools(mcp))["deliver"](source_butler="health", notify_request=ordinary)

    authenticate.assert_not_called()
    assert deliver.await_args.kwargs["verified_context"] is None

    material = {**ordinary, "recovery": {"operation": "handoff"}}
    result = await (await registered_tools(mcp))["deliver"](
        source_butler="relationship", notify_request=material
    )

    authenticate.assert_awaited_once()
    assert deliver.await_args.kwargs["verified_context"] is None
    assert result["error"] == "Approval recovery authority rejected."

    # The real registration proxy logs before the business handler. Exercise
    # its actual FunctionTool, so an inner-only guard cannot satisfy this gate.
    from fastmcp import Client, FastMCP

    from butlers.mcp_wrappers import _SpanWrappingMCP

    caplog.set_level("INFO", logger="butlers.mcp_wrappers")
    registered = FastMCP("registered-approval-boundary")
    tools.register_tools(
        _SpanWrappingMCP(registered, "switchboard", module_name="switchboard"),
        SimpleNamespace(_get_pool=lambda: pool),
        SimpleNamespace(groups=["routing"]),
    )
    async with Client(registered) as client:
        positive = (
            await client.call_tool(
                "deliver",
                {
                    "source_butler": "health",
                    "notify_request": ordinary,
                },
            )
        ).data
        assert positive == {"status": "sent"}
        positive_logs = len(caplog.records)
        assert any(record.name == "butlers.mcp_wrappers" for record in caplog.records)
        calls_before_refusal = deliver.await_count
        refused = (
            await client.call_tool(
                "deliver",
                {
                    "source_butler": "relationship",
                    "notify_request": material,
                },
            )
        ).data
        assert refused["error"] == "Approval recovery authority rejected."
        assert deliver.await_count == calls_before_refusal
        assert len(caplog.records) == positive_logs

    # A post-registration guard must never introduce a group-disabled tool.
    disabled = FastMCP("routing-disabled")
    tools.register_tools(
        _SpanWrappingMCP(disabled, "switchboard", module_name="switchboard"),
        SimpleNamespace(_get_pool=lambda: pool),
        SimpleNamespace(groups=["lifecycle"]),
    )
    assert "deliver" not in {tool.name for tool in await disabled.list_tools()}

    monkeypatch.setattr(delivery_module, "deliver", actual_deliver)
    await _assert_internal_delivery_rendering_mark_is_monotonic(monkeypatch)


async def _assert_internal_delivery_rendering_mark_is_monotonic(monkeypatch):
    import inspect

    from butlers.core.delivery_rendering import inert_delivery_content

    delivery_module = importlib.import_module("butlers.tools.switchboard.notification.deliver")
    from butlers.modules._roster_switchboard import tools

    captured = []

    async def dispatch(_pool, **kwargs):
        captured.append(kwargs["notify_request"])
        return {"status": "sent"}

    monkeypatch.setattr(delivery_module, "_deliver_via_notify_request", dispatch)
    module = SimpleNamespace(_get_pool=lambda: object())
    mcp = RegisteredFastMCP("remaining4-switchboard-tools")
    tools.register_tools(mcp, module, SimpleNamespace(groups=["routing"]))
    assert (
        "render_links_inert"
        not in inspect.signature((await registered_tools(mcp))["deliver"]).parameters
    )
    assert (
        "content_requires_inert_links"
        not in inspect.signature((await registered_tools(mcp))["deliver"]).parameters
    )
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {"intent": "send", "channel": "telegram", "message": "hello", "recipient": "1"},
        "render_links_inert": True,
    }
    await (await registered_tools(mcp))["deliver"](source_butler="health", notify_request=request)
    assert captured[-1].render_links_inert is False
    with inert_delivery_content():
        request["render_links_inert"] = False
        await (await registered_tools(mcp))["deliver"](
            source_butler="health", notify_request=request
        )
        assert captured[-1].render_links_inert is True
        await delivery_module.deliver(
            object(),
            source_butler="health",
            notify_request=request,
            content_requires_inert_links=False,
        )
        assert captured[-1].render_links_inert is True
    await delivery_module.deliver(
        object(), source_butler="health", notify_request=request, content_requires_inert_links=True
    )
    assert captured[-1].render_links_inert is True


async def test_registered_recovery_derives_private_rendering_facts_after_authentication(
    monkeypatch,
):
    monkeypatch.setenv("DASHBOARD_URL", "https://dashboard.example.test")
    import copy
    from contextlib import nullcontext

    from fastmcp import FastMCP

    from butlers.core import approval_delivery_authority as authority
    from butlers.core.approval_delivery_transport import TrustedRecoveryContext
    from butlers.core.delivery_rendering import inert_delivery_content
    from butlers.modules._roster_switchboard import tools

    delivery = importlib.import_module("butlers.tools.switchboard.notification.deliver")
    subject = "approval:relationship:00000000-0000-0000-0000-000000000000"
    trusted = TrustedRecoveryContext(
        "relationship", "relationship", "handoff", "action", subject, f"{subject}:p:1", 1, "single"
    )
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "relationship",
        "delivery": {
            "intent": "approval_request",
            "channel": "telegram",
            "message": "Read https://example.test",
            "recipient": "owner-synthetic",
        },
        "actions": [
            {
                "verb": "open_dashboard",
                "reference": {"kind": "self", "route_key": "approvals_queue", "params": {}},
            }
        ],
        "recovery": {
            key: value
            for key, value in trusted.as_internal_dict().items()
            if key not in {"issuer", "owning_schema"}
        },
    }
    original = None

    async def verify(_tool, arguments, *, proof):
        # The source verifier sees the original signed envelope, before the
        # Switchboard's private rendering fact replaces any public flag.
        assert arguments == {"notify_request": original}
        assert proof == "synthetic-proof"
        return {
            "allowed": True,
            "source_epoch": "synthetic-epoch",
            "context": trusted.as_internal_dict(),
        }

    endpoint = SimpleNamespace(call=verify)
    module = SimpleNamespace(
        _get_pool=lambda: pool,
        _approval_recovery_call=AsyncMock(),
        _approval_recovery_verifiers={
            "relationship": authority.SourceApprovalVerifier(endpoint, "synthetic-epoch")
        },
    )
    principal = authority._PeerPrincipal(
        "relationship", "switchboard:approval-recovery", "synthetic-epoch", lambda: True
    )
    monkeypatch.setattr(
        authority,
        "get_http_request",
        lambda: SimpleNamespace(
            scope={"butlers.approval_peer": principal},
            headers={"x-butlers-approval-proof": "synthetic-proof"},
        ),
    )
    pool = SimpleNamespace(fetchval=AsyncMock(return_value="relationship"))
    monkeypatch.setattr(delivery, "receiver_route_cutover_enabled", lambda: False)
    routed = []

    async def route(_pool, **kwargs):
        routed.append(kwargs)
        return {"result": {"notify_response": {"handoff": {"classification": "confirmed"}}}}

    monkeypatch.setattr(delivery, "route", route)
    mcp = FastMCP("registered-recovery-rendering")
    tools.register_tools(mcp, module, SimpleNamespace(groups=["routing"]))
    registered = await mcp.get_tool("deliver")
    for private, public in [(True, None), (True, False), (False, True)]:
        envelope = copy.deepcopy(request)
        if public is not None:
            envelope["render_links_inert"] = public
        original = copy.deepcopy(envelope)
        with inert_delivery_content() if private else nullcontext():
            result = await registered.fn(source_butler="relationship", notify_request=envelope)
        assert result["status"] == "recovery"
        assert envelope == original
        forwarded = routed[-1]["args"]["input"]["context"]["notify_request"]
        assert forwarded["render_links_inert"] is private
        assert forwarded["delivery"] == original["delivery"]
        assert (
            routed[-1]["internal_context"]["_trusted_approval_recovery"]
            == trusted.as_internal_dict()
        )

    # The internal True-only input also composes monotonically with the private
    # mark; it never becomes a registered model argument.
    original = copy.deepcopy(request)
    with inert_delivery_content():
        await delivery.deliver(
            pool,
            source_butler="relationship",
            notify_request=request,
            verified_context=trusted,
            content_requires_inert_links=False,
        )
    assert routed[-1]["args"]["input"]["context"]["notify_request"]["render_links_inert"] is True
    await delivery.deliver(
        pool,
        source_butler="relationship",
        notify_request=request,
        verified_context=trusted,
        content_requires_inert_links=True,
    )
    assert routed[-1]["args"]["input"]["context"]["notify_request"]["render_links_inert"] is True
