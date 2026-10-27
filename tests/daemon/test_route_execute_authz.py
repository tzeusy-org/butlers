"""Regression tests for route.execute authn/authz guardrails.

Verifies that:
- Unauthenticated callers (unknown source_endpoint_identity) are rejected.
- Only trusted control-plane callers (default: Switchboard) can invoke
  route.execute on both messenger and non-messenger butlers.
- Custom trusted_route_callers config is respected.
- Authorized callers pass through normally.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.daemon import ButlerDaemon
from butlers.modules.telegram import TelegramProviderResponseError

pytestmark = pytest.mark.unit


def test_finance_first_import_preserves_registered_switchboard_and_publisher_binding():
    """Fresh real roster discovery must finish before finance patches its publisher."""
    consumer = """
import importlib
import logging
import sys
from unittest.mock import AsyncMock

from pytest import MonkeyPatch

logging.disable(logging.CRITICAL)
from butlers.jobs._roster.finance_jobs import run_insight_scan

publisher = AsyncMock()
with MonkeyPatch.context() as patcher:
    patcher.setattr("butlers.core_tools._domain_events.publish_domain_event_once", publisher)
    assert sys.modules["butlers.core_tools._domain_events"].publish_domain_event_once is publisher
    switchboard = sys.modules["butlers.tools.switchboard"]
    assert callable(switchboard.route)
    assert callable(switchboard.deliver)
    assert switchboard.route is importlib.import_module(
        "butlers.tools.switchboard.routing.route"
    ).route
    assert switchboard.deliver is importlib.import_module(
        "butlers.tools.switchboard.notification.deliver"
    ).deliver
    assert callable(run_insight_scan)
print("finance-first registration and publisher binding verified")
"""
    completed = subprocess.run(
        [sys.executable, "-c", consumer],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, "fresh finance-first registry/publisher consumer failed"
    assert completed.stdout.strip() == "finance-first registration and publisher binding verified"


@pytest.mark.parametrize(
    "origin,reference,verified",
    [
        ("https://dashboard.example.test", None, True),
        (
            "https://dashboard.example.test",
            {"kind": "self", "route_key": "unknown", "params": {}},
            False,
        ),
        (
            "https://dashboard.example.test",
            {"kind": "self", "route_key": "approval_detail", "params": {"action_id": "invalid"}},
            False,
        ),
        ("http://dashboard.example.test", None, False),
        (
            "https://" + "a" * 63 + "." + "b" * 63 + "." + "c" * 63 + "." + "d" * 63 + ".test",
            None,
            False,
        ),
    ],
)
async def test_registered_approval_self_link_resolves_or_delivers_inert(
    tmp_path, monkeypatch, origin, reference, verified
):
    """Real approval producer -> deliver -> registered Messenger -> Telegram adapter."""
    import copy
    import importlib
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from fastmcp import FastMCP

    from butlers.modules.approvals.notifications import build_approval_request_envelope

    monkeypatch.setenv("DASHBOARD_URL", origin)
    daemon, route_execute = await _start_daemon_with_route_execute(
        _make_butler_toml(tmp_path, butler_name="messenger", modules={"telegram": {}}),
        _patch_infra(),
    )
    telegram = next(module for module in daemon._modules if module.name == "telegram")
    monkeypatch.setattr(
        "butlers.core_tools._routing.resolve_owner_channel_via_definer",
        AsyncMock(return_value="12345"),
    )
    monkeypatch.setattr("butlers.modules.telegram.require_permission", AsyncMock())
    monkeypatch.setattr("butlers.modules.telegram.write_audit_entry", AsyncMock())
    post = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 991}},
            request=httpx.Request("POST", "https://telegram.example.test/sendMessage"),
        )
    )
    monkeypatch.setattr(telegram, "_get_client", lambda: SimpleNamespace(post=post))
    monkeypatch.setattr(telegram, "_base_url", lambda: "https://telegram.example.test")
    mcp = FastMCP("self-link-owner-delivery")
    mcp.tool(name="route.execute")(route_execute)
    registered = await mcp.get_tool("route.execute")
    delivery = importlib.import_module("butlers.tools.switchboard.notification.deliver")
    routed = []

    async def dispatch(_pool, **kwargs):
        routed.append(copy.deepcopy(kwargs["args"]))
        dispatched = copy.deepcopy(kwargs["args"])
        for action in dispatched["input"]["context"]["notify_request"].get("actions", []):
            if action["verb"] == "open_link":
                action.update(
                    url="https://attacker.example.test/unsafe",
                    host="attacker.example.test",
                    label="Untrusted label",
                )
        result = await registered.fn(**dispatched)
        return {
            "result": result,
            "transport": result.get("transport", {}),
            **({"error": result["error"]} if "error" in result else {}),
        }

    monkeypatch.setattr(delivery, "route", dispatch)
    monkeypatch.setattr(
        delivery, "_log_notification_best_effort", AsyncMock(return_value=uuid.uuid4())
    )
    now = datetime.now(UTC)
    action_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    request = build_approval_request_envelope(
        action={
            "id": action_id,
            "requested_at": now,
            "expires_at": now + timedelta(hours=1),
            "tool_name": "email_send_message",
            "tool_args": {
                "to": "owner@example.test",
                "subject": "Review",
                "body": "Synthetic call",
            },
        },
        origin_butler="relationship",
        owner_recipient="12345",
        callback_secret="synthetic-secret",
        dashboard_base_url=origin,
    )
    if reference is not None:
        request["actions"][-1] = {"verb": "open_dashboard", "reference": reference}
    if verified:
        forged = copy.deepcopy(request)
        forged["actions"][-1] = {
            "verb": "open_link",
            "reference": forged["actions"][-1]["reference"],
            "label": "Open dashboard",
            "url": "https://attacker.example.test/unsafe",
            "host": "attacker.example.test",
            "provenance": "dashboard",
        }
        refusal = await delivery.deliver(
            daemon.db.pool, source_butler="relationship", notify_request=forged
        )
        assert refusal["status"] == "failed"
        assert routed == []
        post.assert_not_awaited()
    result = await delivery.deliver(
        daemon.db.pool,
        source_butler="relationship",
        notify_request=request,
        content_requires_inert_links=True,
    )
    assert result["status"] == "sent", (result, routed)
    post.assert_awaited_once()
    payload = post.await_args.kwargs["json"]
    wire = routed[0]["input"]["context"]["notify_request"]
    if verified:
        assert wire["actions"][-1]["verb"] == "open_link"
        button = payload["reply_markup"]["inline_keyboard"][-1][0]
        assert button["url"] == f"{origin}/approvals/{action_id}"
        assert "dashboard.example.test" in button["text"]
        assert "dashboard" in button["text"]
    else:
        assert "[link not verified]" in payload["text"]
        assert all(
            "url" not in button
            for row in payload["reply_markup"]["inline_keyboard"]
            for button in row
        )
    assert payload["link_preview_options"] == {"is_disabled": True}
    assert "parse_mode" not in payload
    assert "callback_data" in payload["reply_markup"]["inline_keyboard"][0][0]


@pytest.fixture
def verified_owner_transport(monkeypatch):
    """Transport tests explicitly model the unique owner identity they address."""
    from types import SimpleNamespace

    async def resolve(_pool, channel, target):
        if (channel, target) in {("telegram", "12345"), ("email", "recipient@example.com")}:
            return SimpleNamespace(roles=["owner"])
        return None

    monkeypatch.setattr(
        "butlers.identity.resolve_channel_contact_with_owner_corroboration", resolve
    )


class _NoopLeaseHeartbeat:
    async def __aenter__(self) -> asyncio.Event:
        return asyncio.Event()

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _EnvelopeApprovalStore:
    """JSON-roundtrip protocol double, not PostgreSQL persistence evidence."""

    def __init__(self):
        self.row = None
        self.parks = 0

    async def park(self, _pool, **kwargs):
        import json

        self.parks += 1
        self.row = {
            "id": kwargs["action_id"],
            "status": "pending",
            "decided_by": None,
            "tool_name": kwargs["tool_name"],
            "tool_args": json.loads(json.dumps(kwargs["tool_args"])),
            "requested_at": kwargs["requested_at"],
            "expires_at": kwargs["expires_at"],
            "session_id": kwargs.get("session_id"),
            "why": kwargs.get("why"),
            "execution_result": None,
        }

    async def fetchrow(self, query, *args):
        if "pending_actions" not in query:
            return None
        if query.startswith("UPDATE pending_actions"):
            assert self.row["status"] == "pending"
            self.row.update(status=args[0], decided_by=args[1], decided_at=args[2])
        return dict(self.row) if self.row else None

    async def fetchval(self, query, *args):
        from datetime import UTC, datetime

        return datetime.now(UTC) if "clock_timestamp" in query else None

    async def execute(self, query, *args):
        if query.startswith("UPDATE pending_actions") and "execution_result" in query:
            self.row.update(status=args[0], execution_result=args[1], decided_at=args[2])
        return "UPDATE 1"


@pytest.mark.parametrize("channel", ["telegram", "email"])
async def test_scheduled_delivery_parks_full_envelope_and_approved_registered_replay(
    tmp_path, monkeypatch, channel
):
    import copy
    import importlib

    from fastmcp import FastMCP

    from butlers.core.delivery_rendering import inert_delivery_content
    from butlers.core.tool_call_capture import get_current_runtime_session_id
    from butlers.modules.approvals.module import ApprovalsModule

    patches = _patch_infra()
    daemon, route_execute = await _start_daemon_with_route_execute(
        _make_butler_toml(tmp_path, butler_name="messenger", modules={"telegram": {}, "email": {}}),
        patches,
    )
    store = _EnvelopeApprovalStore()
    daemon.db.pool = store
    modules = {module.name: module for module in daemon._modules}
    send = AsyncMock(return_value={"ok": True, "result": {"message_id": 991}, "message_id": "991"})
    if channel == "telegram":
        modules[channel]._send_message = send
    else:
        modules[channel]._send_email = send
    monkeypatch.setattr("butlers.modules.approvals.park.park_pending_action", store.park)
    monkeypatch.setattr("butlers.modules.approvals.email_guard.park_pending_action", store.park)
    monkeypatch.setattr(
        "butlers.identity.resolve_channel_contact_with_owner_corroboration",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("butlers.modules.approvals.rules.match_rules", AsyncMock(return_value=None))
    for name in ("_record_approval", "_check_promotion_threshold", "_update_velocity"):
        monkeypatch.setattr(f"butlers.modules.approvals.module.{name}", AsyncMock())
    approvals = ApprovalsModule()
    await approvals.on_startup({}, store)
    mcp = FastMCP("messenger-approved-envelope")
    mcp.tool(name="route.execute")(route_execute)
    registered = await mcp.get_tool("route.execute")
    replayed = []

    async def execute(name, args):
        assert name == "route.execute"
        replayed.append(copy.deepcopy(args))
        return await registered.fn(**args)

    approvals.set_tool_executor(execute)
    delivery = importlib.import_module("butlers.tools.switchboard.notification.deliver")
    routed = []

    async def dispatch(_pool, **kwargs):
        routed.append(copy.deepcopy(kwargs["args"]))
        result = await registered.fn(**kwargs["args"])
        return {
            "result": result,
            "error": result.get("error"),
            "transport": result.get("transport", {}),
        }

    monkeypatch.setattr(delivery, "route", dispatch)
    monkeypatch.setattr(delivery, "log_notification", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(
        delivery, "_log_notification_best_effort", AsyncMock(return_value=uuid.uuid4())
    )
    request = _valid_notify_request()
    request["delivery"].update(
        channel=channel,
        recipient="non-owner@example.test" if channel == "email" else "9001",
        message="Read https://example.test",
    )
    assert get_current_runtime_session_id() is None
    try:
        with inert_delivery_content():
            delivered = await delivery.deliver(
                store, source_butler="health", notify_request=request
            )
        send.assert_not_awaited()
        assert store.row is not None, (delivered, routed)
        assert store.row["status"] == "pending"
        assert store.row["tool_name"] == "route.execute"
        assert store.row["tool_args"] == routed[0]
        assert (
            store.row["tool_args"]["input"]["context"]["notify_request"]["render_links_inert"]
            is True
        )
        assert store.row["why"].startswith("Messenger received a deterministic delivery proposal")
        approved = copy.deepcopy(store.row["tool_args"])
        request["delivery"]["message"] = "Caller changed content after parking"
        result = await approvals._approve_action(
            str(store.row["id"]), actor={"type": "human", "id": "owner", "authenticated": True}
        )
        assert "error" not in result
        assert store.row["status"] == "executed"
        assert replayed == [approved]
        assert store.parks == 1
        if channel == "telegram":
            send.assert_awaited_once_with(
                "9001", "[health] Read https://example.test", render_links_inert=True
            )
        else:
            send.assert_awaited_once_with(
                "non-owner@example.test",
                "[health] Notification",
                "Read https://example.test",
                render_links_inert=True,
            )
        await approvals._dispatch_approved_action_by_id(str(store.row["id"]))
        assert send.await_count == 1
        assert replayed == [approved]
    finally:
        await approvals.on_shutdown()


async def test_messenger_without_approval_runtime_refuses_non_owner_and_preserves_owner(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    daemon, route_execute = await _start_daemon_with_route_execute(
        _make_butler_toml(tmp_path, butler_name="messenger", modules={"telegram": {}}),
        _patch_infra(),
    )
    telegram = next(module for module in daemon._modules if module.name == "telegram")
    telegram._send_message = AsyncMock(return_value={"ok": True, "result": {"message_id": 7}})
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr("butlers.identity.resolve_channel_contact_with_owner_corroboration", lookup)
    args = dict(
        schema_version="route.v1",
        request_context=_route_request_context(),
        input={"prompt": "Deliver", "context": {"notify_request": _valid_notify_request()}},
    )
    refusal = await route_execute(**args)
    assert refusal["status"] == "error"
    telegram._send_message.assert_not_awaited()
    lookup.return_value = SimpleNamespace(roles=["owner"])
    assert (await route_execute(**args))["status"] == "ok"
    telegram._send_message.assert_awaited_once()


async def test_registered_delivery_replay_rejects_tampered_wrong_tool_and_child_task(
    tmp_path, monkeypatch
):
    import copy

    from fastmcp import FastMCP

    from butlers.core.approvals_hooks import EmailGuardDecision
    from butlers.modules.approvals.execution_context import (
        ApprovalExecutionContext,
        approval_tool_args_digest,
        reset_approval_execution_context,
        set_approval_execution_context,
    )
    from butlers.modules.approvals.module import ApprovalsModule

    daemon, route_execute = await _start_daemon_with_route_execute(
        _make_butler_toml(tmp_path, butler_name="messenger", modules={"telegram": {}}),
        _patch_infra(),
    )
    approval = ApprovalsModule()
    await approval.on_startup({}, daemon.db.pool)
    mcp = FastMCP("messenger-negative-envelope")
    mcp.tool(name="route.execute")(route_execute)
    registered = await mcp.get_tool("route.execute")
    telegram = next(module for module in daemon._modules if module.name == "telegram")
    telegram._send_message = AsyncMock(return_value={"ok": True, "result": {"message_id": 7}})
    guard = AsyncMock(
        return_value=EmailGuardDecision(
            allowed=False, reason="parked", action_id=uuid.uuid4(), contact_desc="unknown"
        )
    )
    monkeypatch.setattr("butlers.core.approvals_hooks.check_recipient", guard)
    args = dict(
        schema_version="route.v1",
        request_context=_route_request_context(),
        input={"prompt": "Deliver", "context": {"notify_request": _valid_notify_request()}},
    )
    try:
        for case in ("tampered", "wrong_tool", "child_task"):
            token = set_approval_execution_context(
                ApprovalExecutionContext(
                    action_id=uuid.uuid4(),
                    session_id=None,
                    actor="human:owner",
                    tool_name="email_send_message" if case == "wrong_tool" else "route.execute",
                    tool_args_digest=approval_tool_args_digest(args),
                    authorized_task=asyncio.current_task(),
                )
            )
            altered = copy.deepcopy(args)
            if case == "tampered":
                altered["input"]["context"]["notify_request"]["delivery"]["message"] = "Changed"
            try:
                call = registered.fn(**altered)
                result = await asyncio.create_task(call) if case == "child_task" else await call
            finally:
                reset_approval_execution_context(token)
            assert result["status"] == "error", case
            assert guard.await_args.kwargs["park_tool_args"] == altered
            telegram._send_message.assert_not_awaited()
        assert guard.await_count == 3
    finally:
        await approval.on_shutdown()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _toml_value(v: Any) -> str:
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        items = ", ".join(f'"{i}"' if isinstance(i, str) else str(i) for i in v)
        return f"[{items}]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


@pytest.mark.parametrize(
    ("channel", "inert", "supported"),
    [
        ("telegram", True, True),
        ("email", True, True),
        ("whatsapp", True, False),
        ("whatsapp", False, False),
        ("telegram", False, True),
        ("telegram", True, False),
    ],
)
async def test_registered_recovery_enforces_inert_provider_contract(
    tmp_path, monkeypatch, channel, inert, supported
):
    from types import SimpleNamespace

    from fastmcp import FastMCP

    from butlers.core import approval_delivery_authority as authority
    from butlers.core.approval_delivery_transport import MessengerApprovalHandoffRepository
    from butlers.core.approval_delivery_worker import HandoffResult
    from butlers.modules.whatsapp import WhatsAppModule

    monkeypatch.setenv("DASHBOARD_URL", "https://dashboard.example.test")
    daemon, route_execute = await _start_daemon_with_route_execute(
        _make_butler_toml(tmp_path, butler_name="messenger", modules={"telegram": {}, "email": {}}),
        _patch_infra(),
    )
    modules = {module.name: module for module in daemon._modules}
    whatsapp = WhatsAppModule()
    whatsapp._send_message = AsyncMock(return_value={"status": "sent", "message_id": "991"})
    daemon._modules.append(whatsapp)
    modules["whatsapp"] = whatsapp
    if channel == "telegram":
        modules[channel].supports_inert_link_delivery = supported

    principal = authority._PeerPrincipal(
        "switchboard", "messenger:approval-recovery", "synthetic-epoch", lambda: True
    )
    monkeypatch.setattr(
        authority,
        "get_http_request",
        lambda: SimpleNamespace(scope={"butlers.approval_peer": principal}),
    )
    monkeypatch.setattr(
        "butlers.core_tools._routing.resolve_owner_channel_via_definer",
        AsyncMock(return_value="owner-synthetic"),
    )
    for module_name in ("telegram", "email"):
        monkeypatch.setattr(f"butlers.modules.{module_name}.require_permission", AsyncMock())
        monkeypatch.setattr(f"butlers.modules.{module_name}.write_audit_entry", AsyncMock())

    response = httpx.Response(
        200,
        json={"ok": True, "result": {"message_id": 991}},
        request=httpx.Request("POST", "https://telegram.example.test/sendMessage"),
    )
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(modules["telegram"], "_get_client", lambda: SimpleNamespace(post=post))
    monkeypatch.setattr(modules["telegram"], "_base_url", lambda: "https://telegram.example.test")
    smtp = MagicMock(return_value={"status": "sent", "message_id": "991"})
    monkeypatch.setattr(modules["email"], "_smtp_send", smtp)

    # Stub only the ledger's DB boundary. The actual process method owns
    # preflight, provider invocation/result classification and repeat handling.
    # This is software evidence, not proof of PostgreSQL once-only persistence.
    recorded = None
    availability = []

    async def admit(_self, _context, *, provider_available, preflight_reason):
        availability.append(provider_available)
        if recorded is not None:
            return recorded
        if not provider_available:
            return HandoffResult("safe_retry", preflight_reason)
        return "provider"

    async def store(_self, _context, result):
        nonlocal recorded
        recorded = result
        return result

    monkeypatch.setattr(MessengerApprovalHandoffRepository, "_admit_or_classify", admit)
    monkeypatch.setattr(MessengerApprovalHandoffRepository, "_store_result", store)
    request = _recovery_notify_request()
    request["render_links_inert"] = inert
    request["delivery"].update(
        channel=channel,
        message="Read <b>https://example.test</b> @reader 😀",
    )
    if channel == "email":
        request["delivery"]["subject"] = "Review https://example.test"
    args = {
        "schema_version": "route.v1",
        "request_context": _route_request_context(source_sender_identity="relationship"),
        "input": {
            "prompt": "Deliver approval",
            "context": {
                "notify_request": request,
                "_trusted_approval_recovery": _trusted_recovery_context(),
            },
        },
    }
    registered_mcp = FastMCP("registered-recovery-provider")
    registered_mcp.tool(name="route.execute")(route_execute)
    registered = await registered_mcp.get_tool("route.execute")
    from butlers.tools.switchboard.contracts import parse_notify_request
    from butlers.tools.switchboard.notification.self_links import resolve_self_links

    output = resolve_self_links(parse_notify_request(request)).model_dump(
        mode="json", exclude_none=True
    )
    for action in output.get("actions", []):
        if action["verb"] == "open_link":
            action.update(
                url="https://attacker.example.test/unsafe",
                host="attacker.example.test",
                label="Untrusted label",
            )
    args["input"]["context"]["notify_request"] = output
    result = await registered.fn(**args)
    handoff = result["result"]["notify_response"]["handoff"]
    if inert and not supported:
        assert handoff == {"classification": "safe_retry", "reason_code": "transport_unavailable"}
        assert availability == [False]
        post.assert_not_awaited()
        smtp.assert_not_called()
        whatsapp._send_message.assert_not_awaited()
        assert recorded is None
        return

    assert handoff["classification"] == "confirmed"
    assert availability == [True]
    if channel == "telegram":
        payload = post.await_args.kwargs["json"]
        assert payload["reply_markup"] == {
            "inline_keyboard": [
                [
                    {
                        "text": "Open dashboard.example.test (dashboard)",
                        "url": "https://dashboard.example.test/approvals",
                    }
                ]
            ]
        }
        if inert:
            assert "parse_mode" not in payload
            assert payload["text"] == "[relationship] " + request["delivery"]["message"]
            assert payload["entities"] == [
                {
                    "type": "pre",
                    "offset": 0,
                    "length": len(payload["text"].encode("utf-16-le")) // 2,
                }
            ]
            assert payload["link_preview_options"] == {"is_disabled": True}
        else:
            assert payload["parse_mode"] == "HTML"
    elif channel == "email":
        smtp.assert_called_once_with(
            "owner-synthetic",
            "[relationship] Review https[:]//example[.]test",
            "Read <b>https[:]//example[.]test</b> [@]reader 😀",
        )
    else:
        whatsapp._send_message.assert_awaited_once_with(
            recipient="owner-synthetic",
            text="[relationship] " + request["delivery"]["message"],
        )
    # A confirmed handoff goes through the same real process without resending.
    repeated = await registered.fn(**args)
    assert repeated["result"]["notify_response"]["handoff"] == handoff
    assert post.await_count + smtp.call_count + whatsapp._send_message.await_count == 1


def _make_butler_toml(
    tmp_path: Path,
    *,
    butler_name: str = "test_butler",
    port: int = 9100,
    modules: dict[str, dict] | None = None,
    trusted_route_callers: list[str] | None = None,
) -> Path:
    modules = modules or {}
    toml_lines = [
        "[butler]",
        f'name = "{butler_name}"',
        f"port = {port}",
        'description = "A test butler"',
        "",
        "[butler.db]",
        'name = "butlers"',
        f'schema = "{butler_name}"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    if trusted_route_callers is not None:
        toml_lines.append("")
        toml_lines.append("[butler.security]")
        items = ", ".join(f'"{c}"' for c in trusted_route_callers)
        toml_lines.append(f"trusted_route_callers = [{items}]")
    for mod_name, mod_cfg in modules.items():
        toml_lines.append(f"\n[modules.{mod_name}]")
        for k, v in mod_cfg.items():
            toml_lines.append(f"{k} = {_toml_value(v)}")
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _patch_infra():
    mock_pool = AsyncMock()
    mock_pool.fetchval.return_value = None

    async def fetchrow(query, *args):
        if "runtime_config" in query:
            return {
                "butler_name": args[0],
                "core_groups": None,
                "core_groups_narrowing_reason": None,
                "catalog_read_sensitivity": "normal",
                "max_concurrent": 3,
                "max_queued": 10,
                "tool_exposure_policy": "eager_filtered",
                "blind_spot_preamble_enabled": True,
                "seeded_at": None,
                "updated_at": None,
            }
        return None

    mock_pool.fetchrow.side_effect = fetchrow
    mock_connection = AsyncMock()
    # The runtime row agrees with Git: no conditional update or audit append.
    mock_connection.fetchrow.return_value = None

    @asynccontextmanager
    async def transaction():
        yield

    mock_connection.transaction = MagicMock(side_effect=transaction)

    @asynccontextmanager
    async def acquire():
        yield mock_connection

    mock_pool.acquire = MagicMock(side_effect=acquire)
    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butlers"

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_builder = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.lifecycle.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.lifecycle.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.lifecycle.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.lifecycle.init_telemetry"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.lifecycle.FastMCP"),
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "build_runtime_adapter": patch(
            "butlers.lifecycle.build_runtime_adapter", new=mock_adapter_builder
        ),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


async def _start_daemon_with_route_execute(butler_dir: Path, patches: dict):
    """Boot a daemon and capture the route.execute handler function."""
    route_execute_fn = None
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            nonlocal route_execute_fn
            resolved_name = declared_name or fn.__name__
            if resolved_name == "route.execute":
                route_execute_fn = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
        patches["Spawner"],
        patches["build_runtime_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    return daemon, route_execute_fn


def _route_request_context(
    *,
    source_endpoint_identity: str = "switchboard",
    source_sender_identity: str = "health",
) -> dict[str, Any]:
    return {
        "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
        "received_at": "2026-02-14T00:00:00Z",
        "source_channel": "mcp",
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }


def _valid_notify_request(*, origin_butler: str = "health") -> dict[str, Any]:
    return {
        "schema_version": "notify.v1",
        "origin_butler": origin_butler,
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": "Take your medication.",
            "recipient": "12345",
        },
    }


def _recovery_notify_request() -> dict[str, Any]:
    subject = "approval:relationship:00000000-0000-0000-0000-000000000000"
    return {
        "schema_version": "notify.v1",
        "origin_butler": "relationship",
        "delivery": {
            "intent": "approval_request",
            "channel": "telegram",
            "message": "Synthetic approval.",
            "recipient": "owner-synthetic",
        },
        "actions": [
            {
                "verb": "open_dashboard",
                "reference": {"kind": "self", "route_key": "approvals_queue", "params": {}},
            }
        ],
        "recovery": {
            "operation": "handoff",
            "subject_kind": "action",
            "subject_key": subject,
            "presentation_key": f"{subject}:p:1",
            "presentation_generation": 1,
            "presentation_mode": "single",
        },
    }


def _trusted_recovery_context() -> dict[str, Any]:
    recovery = _recovery_notify_request()["recovery"]
    return {"issuer": "relationship", "owning_schema": "relationship", **recovery}


@pytest.fixture(autouse=True)
def _mock_route_inbox(monkeypatch):
    """Patch route_inbox DB calls so tests don't need a real DB pool."""
    fake_inbox_id = uuid.uuid4()
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_insert",
        AsyncMock(return_value=fake_inbox_id),
    )
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_claim_processing",
        AsyncMock(return_value=uuid.uuid4()),
    )
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_processing_lease_heartbeat",
        MagicMock(side_effect=lambda *_args, **_kwargs: _NoopLeaseHeartbeat()),
    )
    monkeypatch.setattr(
        "butlers.core.route_inbox.route_inbox_renew_processing_claim",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_mark_processed",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_mark_errored",
        AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Tests: unauthenticated/unauthorized callers are rejected
# ---------------------------------------------------------------------------


class TestRouteExecuteAuthz:
    """Verify route.execute authz: untrusted callers rejected; trusted callers allowed."""

    @pytest.mark.usefixtures("verified_owner_transport")
    async def test_authz_reject_and_allow(self, tmp_path: Path) -> None:
        """Untrusted callers rejected with validation_error; trusted switchboard allowed."""
        # Messenger rejects rogue-caller
        patches = _patch_infra()
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            _make_butler_toml(
                tmp_path, butler_name="messenger", modules={"telegram": {}, "email": {}}
            ),
            patches,
        )
        assert route_execute_fn is not None
        telegram_module = next(m for m in daemon._modules if m.name == "telegram")
        telegram_module._send_message = AsyncMock(return_value={"result": {"message_id": 999}})

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="rogue-caller"),
            input={"prompt": "Deliver.", "context": {"notify_request": _valid_notify_request()}},
        )
        telegram_module._send_message.assert_not_awaited()
        assert result["schema_version"] == "route_response.v1"
        assert result["status"] == "error"
        assert result["error"]["class"] == "validation_error"
        assert result["error"]["retryable"] is False
        assert "rogue-caller" in result["error"]["message"]

        # Empty/blank endpoint identity also rejected
        patches3 = _patch_infra()
        subdir3 = tmp_path / "h3"
        subdir3.mkdir()
        _, route_execute_fn3 = await _start_daemon_with_route_execute(
            _make_butler_toml(subdir3, butler_name="health"), patches3
        )
        result3 = await route_execute_fn3(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity=" "),
            input={"prompt": "Run health check."},
        )
        assert result3["status"] == "error"
        assert result3["error"]["class"] == "validation_error"

        # Switchboard caller allowed: messenger delivery proceeds; non-messenger trigger accepted
        patches_ok = _patch_infra()
        (tmp_path / "mess2").mkdir(exist_ok=True)
        daemon_ok, fn_ok = await _start_daemon_with_route_execute(
            _make_butler_toml(
                tmp_path / "mess2", butler_name="messenger", modules={"telegram": {}, "email": {}}
            ),
            patches_ok,
        )
        tg_mod = next(m for m in daemon_ok._modules if m.name == "telegram")
        tg_mod._send_message = AsyncMock(return_value={"result": {"message_id": 321}})
        result_ok = await fn_ok(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="switchboard"),
            input={"prompt": "Deliver.", "context": {"notify_request": _valid_notify_request()}},
        )
        tg_mod._send_message.assert_awaited_once()
        assert result_ok["status"] == "ok"

    # Spec: REQ-approval-delivery-intent-recovery-004; public DTO/correlation cannot create protected approval authority.
    async def test_recovery_requires_authenticated_switchboard_before_provider(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        patches = _patch_infra()
        daemon, route_execute = await _start_daemon_with_route_execute(
            _make_butler_toml(
                tmp_path,
                butler_name="messenger",
                modules={"telegram": {}, "email": {}},
            ),
            patches,
        )
        assert route_execute is not None
        telegram = next(module for module in daemon._modules if module.name == "telegram")
        telegram._send_message = AsyncMock(return_value={"message_id": "provider-ref"})

        repository = MagicMock()
        repository.process = AsyncMock()
        route_payload = {
            "schema_version": "route.v1",
            "request_context": _route_request_context(
                source_endpoint_identity="switchboard",
                source_sender_identity="relationship",
            ),
            "input": {
                "prompt": "Deliver.",
                "context": {
                    "notify_request": _recovery_notify_request(),
                    "_trusted_approval_recovery": _trusted_recovery_context(),
                },
            },
        }

        malformed = {
            **route_payload,
            "input": {
                **route_payload["input"],
                "context": {
                    **route_payload["input"]["context"],
                    "notify_request": {
                        **_recovery_notify_request(),
                        "recovery": {"operation": "handoff"},
                    },
                },
            },
        }
        origin_mismatch = {
            **route_payload,
            "input": {
                **route_payload["input"],
                "context": {
                    **route_payload["input"]["context"],
                    "notify_request": {
                        **_recovery_notify_request(),
                        "origin_butler": "private-origin-sentinel",
                    },
                },
            },
        }
        missing_attestation = {
            **route_payload,
            "input": {
                **route_payload["input"],
                "context": {"notify_request": _recovery_notify_request()},
            },
        }
        mismatched_attestation = _trusted_recovery_context()
        mismatched_attestation["presentation_mode"] = "burst_digest"
        mismatch = {
            **route_payload,
            "input": {
                **route_payload["input"],
                "context": {
                    **route_payload["input"]["context"],
                    "_trusted_approval_recovery": mismatched_attestation,
                },
            },
        }
        cases = [route_payload, malformed, origin_mismatch, missing_attestation, mismatch]
        telegram._send_message.reset_mock()
        repository.process.reset_mock()
        patches["mock_pool"].reset_mock()
        caplog.clear()
        with (
            patch(
                "butlers.core_tools._routing.MessengerApprovalHandoffRepository",
                return_value=repository,
            ),
            patch("butlers.core_tools._routing.trace.get_tracer") as get_tracer,
            patch("butlers.core_tools._routing.extract_trace_context") as extract_context,
            patch("butlers.core_tools._routing.logger") as route_logger,
            patch(
                "butlers.core_tools._routing.resolve_owner_channel_via_definer",
                new=AsyncMock(),
            ) as owner_lookup,
        ):
            refusals = []
            for payload in cases:
                refusals.append(await route_execute(**payload))

        assert all(result == refusals[0] for result in refusals)
        assert refusals[0] == {
            "schema_version": "route_response.v1",
            "status": "error",
            "error": {
                "class": "validation_error",
                "message": "Approval recovery authority rejected.",
                "retryable": False,
            },
        }
        assert "private-origin-sentinel" not in caplog.text
        get_tracer.assert_not_called()
        extract_context.assert_not_called()
        assert route_logger.method_calls == []
        owner_lookup.assert_not_awaited()
        repository.process.assert_not_awaited()
        assert patches["mock_pool"].method_calls == []
        telegram._send_message.assert_not_awaited()

    @pytest.mark.usefixtures("verified_owner_transport")
    async def test_null_recovery_is_ordinary_notify_not_privileged_recovery(
        self, tmp_path: Path
    ) -> None:
        patches = _patch_infra()
        daemon, route_execute = await _start_daemon_with_route_execute(
            _make_butler_toml(
                tmp_path,
                butler_name="messenger",
                modules={"telegram": {}, "email": {}},
            ),
            patches,
        )
        assert route_execute is not None
        telegram = next(module for module in daemon._modules if module.name == "telegram")
        telegram._send_message = AsyncMock(
            return_value={"ok": True, "result": {"message_id": 4321}}
        )
        notify_request = {**_valid_notify_request(), "recovery": None}

        result = await route_execute(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Deliver.", "context": {"notify_request": notify_request}},
        )

        telegram._send_message.assert_awaited_once()
        assert result["status"] == "ok"
        assert result["result"]["notify_response"]["delivery"] == {
            "channel": "telegram",
            "delivery_id": "4321",
        }

    @pytest.mark.usefixtures("verified_owner_transport")
    async def test_telegram_transport_failure_is_content_blind_and_uncertain(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        patches = _patch_infra()
        daemon, route_execute = await _start_daemon_with_route_execute(
            _make_butler_toml(
                tmp_path,
                butler_name="messenger",
                modules={"telegram": {}, "email": {}},
            ),
            patches,
        )
        assert route_execute is not None
        telegram = next(module for module in daemon._modules if module.name == "telegram")
        telegram._send_message = AsyncMock(
            side_effect=httpx.ReadTimeout(
                "private-body-sentinel",
                request=httpx.Request(
                    "POST", "https://api.telegram.org/botsecret-token/sendMessage"
                ),
            )
        )

        with caplog.at_level("WARNING"):
            result = await route_execute(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={
                    "prompt": "Deliver.",
                    "context": {"notify_request": _valid_notify_request()},
                },
            )

        rendered = repr(result) + caplog.text
        assert "private-body-sentinel" not in rendered
        assert "secret-token" not in rendered
        assert result["status"] == "error"
        assert result["error"] == {
            "class": "timeout",
            "message": "Telegram delivery failed.",
            "retryable": False,
            "telegram_effect_uncertain": True,
        }
        assert result["transport"]["outcome"] == "uncertain"
        assert result["transport"]["error_detail"] == "transport_timeout"

    @pytest.mark.usefixtures("verified_owner_transport")
    async def test_permission_unavailable_is_transient_before_native_delivery(
        self, tmp_path, caplog
    ):
        from types import SimpleNamespace

        from butlers.core.permissions import PermissionUnavailable
        from butlers.core_tools._routing import _build_routed_delivery_command

        patches = _patch_infra()
        daemon, route_execute = await _start_daemon_with_route_execute(
            _make_butler_toml(
                tmp_path, butler_name="messenger", modules={"telegram": {}, "email": {}}
            ),
            patches,
        )
        assert route_execute is not None
        modules = {module.name: module for module in daemon._modules}
        permission_pool = AsyncMock()
        permission_pool.fetchrow.side_effect = OSError("private-database-error")
        allowed = SimpleNamespace(allowed=True, dossier_error=None)
        with (
            patch(
                "butlers.core.approvals_hooks.check_recipient",
                new_callable=AsyncMock,
                return_value=allowed,
            ),
            patch(
                "butlers.core.approvals_hooks.check_email_recipient",
                new_callable=AsyncMock,
                return_value=allowed,
            ),
            patch("butlers.modules.email.write_audit_entry", new_callable=AsyncMock),
            patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
            patch.object(modules["email"], "_smtp_send") as smtp,
            patch.object(modules["telegram"], "_get_client") as get_client,
        ):
            modules["email"]._pool = permission_pool
            modules["telegram"]._audit_pool = permission_pool
            for channel, recipient in (("telegram", "12345"), ("email", "recipient@example.com")):
                request = _valid_notify_request()
                request["delivery"].update(
                    channel=channel, recipient=recipient, message="private-body"
                )
                result = await route_execute(
                    schema_version="route.v1",
                    request_context=_route_request_context(),
                    input={"prompt": "Deliver.", "context": {"notify_request": request}},
                )
                assert result["status"] == "error"
                assert result["error"]["class"] == "target_unavailable"
                assert result["error"]["retryable"] is True
                assert result["transport"] == {
                    "outcome": "not_attempted",
                    "retryable": True,
                    "error_class": "pre_transport",
                    "error_detail": "policy_denied",
                }
                assert result["result"]["notify_response"]["error"]["retryable"] is True
                # The native callback retained for execution after approval reads again.
                permission_pool.fetchrow.side_effect = None
                permission_pool.fetchrow.return_value = {"granted": True, "reason": "granted"}
                command = await _build_routed_delivery_command(
                    daemon=daemon,
                    modules_by_name=modules,
                    channel=channel,
                    intent="send",
                    message_text="private-body",
                    origin="health",
                    recipient=recipient,
                    subject=None,
                    notify_context=None,
                )
                permission_pool.fetchrow.side_effect = OSError("private-database-error")
                with pytest.raises(PermissionUnavailable):
                    await command.execute()
                rendered = repr(result) + caplog.text
                assert "private-body" not in rendered
                assert "private-database-error" not in rendered
            smtp.assert_not_called()
            get_client.assert_not_called()

    @pytest.mark.parametrize(
        ("provider_rejected", "message", "outcome", "detail"),
        [
            (
                False,
                "Telegram delivery confirmation is unavailable.",
                "uncertain",
                "transport_connection_lost",
            ),
            (True, "Telegram delivery was rejected.", "rejected", "provider_rejected"),
        ],
    )
    @pytest.mark.usefixtures("verified_owner_transport")
    async def test_telegram_response_failure_preserves_transport_truth(
        self,
        tmp_path: Path,
        provider_rejected: bool,
        message: str,
        outcome: str,
        detail: str,
    ) -> None:
        patches = _patch_infra()
        daemon, route_execute = await _start_daemon_with_route_execute(
            _make_butler_toml(
                tmp_path,
                butler_name="messenger",
                modules={"telegram": {}, "email": {}},
            ),
            patches,
        )
        assert route_execute is not None
        telegram = next(module for module in daemon._modules if module.name == "telegram")
        telegram._send_message = AsyncMock(
            side_effect=TelegramProviderResponseError(
                "telegram_provider_response: invalid message_id",
                provider_rejected=provider_rejected,
            )
        )

        result = await route_execute(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Deliver.", "context": {"notify_request": _valid_notify_request()}},
        )

        assert result["status"] == "error"
        assert result["error"] == {
            "class": "delivery_error",
            "message": message,
            "retryable": False,
            **({"telegram_effect_uncertain": True} if outcome == "uncertain" else {}),
        }
        assert result["transport"]["outcome"] == outcome
        assert result["transport"]["error_detail"] == detail


# ---------------------------------------------------------------------------
# Tests: custom trusted_route_callers config
# ---------------------------------------------------------------------------


class TestRouteExecuteCustomTrustedCallers:
    """Verify custom trusted_route_callers from butler.toml is respected."""

    async def test_custom_trusted_callers(self, tmp_path: Path) -> None:
        """Custom list caller allowed; switchboard rejected when not in list; empty list rejects all."""
        # Custom caller in list → accepted
        patches = _patch_infra()
        d1 = tmp_path / "c1"
        d1.mkdir()
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            _make_butler_toml(
                d1, butler_name="health", trusted_route_callers=["switchboard", "heartbeat"]
            ),
            patches,
        )
        mock_tr = MagicMock(output="ok", success=True, error=None, duration_ms=10)
        daemon.spawner.trigger = AsyncMock(return_value=mock_tr)
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="heartbeat"),
            input={"prompt": "Tick check."},
        )
        assert result["status"] == "accepted"

        # Switchboard excluded from custom list → rejected
        patches2 = _patch_infra()
        d2 = tmp_path / "c2"
        d2.mkdir()
        _, route_execute_fn2 = await _start_daemon_with_route_execute(
            _make_butler_toml(d2, butler_name="health", trusted_route_callers=["internal-only"]),
            patches2,
        )
        result2 = await route_execute_fn2(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="switchboard"),
            input={"prompt": "Run health check."},
        )
        assert result2["status"] == "error"
        assert result2["error"]["class"] == "validation_error"
        assert "switchboard" in result2["error"]["message"]

        # Empty list → everyone rejected
        patches3 = _patch_infra()
        d3 = tmp_path / "c3"
        d3.mkdir()
        _, route_execute_fn3 = await _start_daemon_with_route_execute(
            _make_butler_toml(d3, butler_name="health", trusted_route_callers=[]),
            patches3,
        )
        result3 = await route_execute_fn3(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="switchboard"),
            input={"prompt": "Run health check."},
        )
        assert result3["status"] == "error"
        assert result3["error"]["class"] == "validation_error"


# ---------------------------------------------------------------------------
# Tests: config parsing for trusted_route_callers
# ---------------------------------------------------------------------------


class TestTrustedRouteCallersConfig:
    """Verify config parsing of [butler.security].trusted_route_callers."""

    def test_trusted_callers_config_parsing(self, tmp_path: Path) -> None:
        """Default is ('switchboard',); custom list parsed; empty gives ();
        non-list value raises ConfigError."""
        from butlers.config import ConfigError, load_config

        # Default
        assert load_config(
            _make_butler_toml(tmp_path, butler_name="test_butler")
        ).trusted_route_callers == ("switchboard",)

        # Custom list
        d2 = tmp_path / "c2"
        d2.mkdir()
        cfg2 = load_config(
            _make_butler_toml(
                d2,
                butler_name="test_butler",
                trusted_route_callers=["switchboard", "heartbeat", "admin"],
            )
        )
        assert cfg2.trusted_route_callers == ("switchboard", "heartbeat", "admin")

        # Empty list
        d3 = tmp_path / "c3"
        d3.mkdir()
        cfg3 = load_config(
            _make_butler_toml(d3, butler_name="test_butler", trusted_route_callers=[])
        )
        assert cfg3.trusted_route_callers == ()

        # Non-list raises ConfigError
        (tmp_path / "bad.toml").write_text("")
        d4 = tmp_path / "c4"
        d4.mkdir()
        (d4 / "butler.toml").write_text("""
[butler]
name = "test_butler"
port = 9100
description = "A test butler"

[butler.db]
name = "butlers"
schema = "test_butler"

[butler.security]
trusted_route_callers = "switchboard"

[[butler.schedule]]
name = "daily-check"
cron = "0 9 * * *"
prompt = "Do the daily check"
""")
        with pytest.raises(ConfigError, match=r"Invalid butler\.security\.trusted_route_callers"):
            load_config(d4)
