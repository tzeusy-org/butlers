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
import json
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.modules.telegram import TelegramProviderResponseError
from butlers.testing.daemon import FakeRuntime, write_butler_config
from butlers.testing.fake_pool import fake_pool

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


# ---------------------------------------------------------------------------
# Booted route.execute authz: a real daemon called over MCP
#
# These use ``shared_booted_daemon`` (a Messenger with telegram and email
# on synthetic credentials, or a plain butler) and call route.execute over its MCP
# endpoint; the route inbox, delivery pipeline and provider-response handling are
# the production ones. Only the provider transport (``_send_message``, or an
# injected failure) and the owner-identity lookup stand in, patched through
# ``monkeypatch`` so the shared boot is unchanged for the next test. Delivery and
# refusal write no route_inbox or session rows, so the Messenger tests share one
# boot; the trusted-callers test changes config per case and boots its own.
#
# The approvals park/approve/replay tests write ``pending_actions`` rows, so each
# boots a fresh Messenger with the Approvals module (``_approvals_messenger``) and
# runs its cases in that one boot: the production guards park, the daemon's own
# executor replays, and outcomes are read back from the clone. Switchboard
# ``deliver()`` runs in the test with ``route`` sent to the booted MCP endpoint.
# ---------------------------------------------------------------------------

_SYNTHETIC_CREDENTIALS = {
    "BUTLER_TELEGRAM_TOKEN": "synthetic-telegram-token",
    "BUTLER_EMAIL_ADDRESS": "butler@example.test",
    "BUTLER_EMAIL_PASSWORD": "synthetic-email-password",
}


def _booted(test: Any) -> Any:
    """Run *test* in the integration lane, on the session loop the kit's fixtures use."""
    return pytest.mark.integration(pytest.mark.asyncio(loop_scope="session")(test))


async def _shared_messenger(shared_booted_daemon, *modules: str):
    """The module's shared Messenger: these tests deliver or refuse, and queue nothing."""
    modules = modules or ("telegram", "email")
    booted = await shared_booted_daemon(
        "messenger",
        "core",
        "messenger",
        extra_toml="".join(f"[modules.{name}]\n\n" for name in modules),
        registered=True,
        credentials=_SYNTHETIC_CREDENTIALS,
    )
    statuses = {name: status.status for name, status in booted.daemon._module_statuses.items()}
    assert statuses == dict.fromkeys(modules, "active")
    return booted


async def _boot_butler(booted_daemon, config_root: Path, trusted_route_callers=None):
    extra = ""
    if trusted_route_callers is not None:
        items = ", ".join(f'"{caller}"' for caller in trusted_route_callers)
        extra = f"[butler.security]\ntrusted_route_callers = [{items}]\n"
    return await booted_daemon(
        write_butler_config(config_root, "general", extra_toml=extra), registered=True
    )


def _module(booted, name: str) -> Any:
    return next(module for module in booted.daemon._modules if module.name == name)


def _mcp_route(booted):
    """route.execute as a caller sees it: over the daemon's MCP endpoint."""

    async def route_execute(**arguments: Any) -> dict[str, Any]:
        return await booted.call_tool("route.execute", arguments)

    return route_execute


async def _inbox_count(booted) -> int:
    return await booted.daemon.db.pool.fetchval("SELECT count(*) FROM route_inbox")


def _switchboard_pool() -> Any:
    """The Switchboard side of ``deliver()``: not the subject, so a strict DB-free pool.

    With its notification log patched, ``deliver()`` touches its own pool only to
    write the outbound ``message_inbox`` row after a send; nothing else is allowed.
    """
    return fake_pool(execute=None)


def _deliver_through(booted, monkeypatch) -> tuple[Any, list[dict[str, Any]]]:
    """Switchboard ``deliver()`` with ``route`` sent to the booted Messenger over MCP.

    Returns the deliver module and the list of route.execute arguments it sent.
    The notification log is the Switchboard's own bookkeeping and stays patched.
    """
    import copy
    import importlib

    delivery = importlib.import_module("butlers.tools.switchboard.notification.deliver")
    route_execute = _mcp_route(booted)
    routed: list[dict[str, Any]] = []

    async def dispatch(_pool, **kwargs):
        routed.append(copy.deepcopy(kwargs["args"]))
        result = await route_execute(**kwargs["args"])
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
    return delivery, routed


_APPROVALS_MESSENGER_TOML = "[modules.telegram]\n\n[modules.email]\n\n[modules.approvals]\n"


async def _approvals_messenger(booted_daemon, tmp_path: Path, *, owner_telegram: str = ""):
    """A fresh Messenger whose Approvals module parks into its real ``pending_actions``.

    Approvals startup registers the production recipient guards, park and replay
    matcher on the daemon's pool, and the daemon wires its own approved-tool
    executor. The relationship chain gives ``public.resolve_owner_triple`` the
    ``entity_facts`` it reads, so an owner channel can be seeded for real.
    """
    from butlers.testing.db import open_pool

    booted = await booted_daemon(
        write_butler_config(
            tmp_path, "messenger", schema="messenger", extra_toml=_APPROVALS_MESSENGER_TOML
        ),
        "core",
        "messenger",
        "approvals",
        registered=True,
        credentials=_SYNTHETIC_CREDENTIALS,
        chain_schemas={"relationship": "relationship"},
    )
    statuses = {name: status.status for name, status in booted.daemon._module_statuses.items()}
    assert statuses == dict.fromkeys(("telegram", "email", "approvals"), "active")
    if owner_telegram:
        admin = await open_pool(booted.admin_url)
        try:
            owner_id = await admin.fetchval(
                "SELECT id FROM public.entities WHERE 'owner' = ANY(roles)"
            ) or await admin.fetchval(
                "INSERT INTO public.entities (canonical_name, entity_type, roles)"
                " VALUES ('Owner', 'person', ARRAY['owner']) RETURNING id"
            )
            await admin.execute(
                "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src)"
                " VALUES ($1, 'has-handle', $2, 'literal', 'test')",
                owner_id,
                f"telegram:{owner_telegram}",
            )
        finally:
            await admin.close()
    return booted


async def _pending_actions(booted) -> list[dict[str, Any]]:
    rows = await booted.daemon.db.pool.fetch(
        "SELECT id, status, tool_name, tool_args, why FROM pending_actions ORDER BY requested_at"
    )
    return [dict(row) for row in rows]


async def _execution_failures(booted, action_id: Any) -> list[str]:
    rows = await booted.daemon.db.pool.fetch(
        "SELECT reason FROM approval_events"
        " WHERE action_id = $1 AND event_type = 'action_execution_failed'",
        action_id,
    )
    return [row["reason"] for row in rows]


_HUMAN_OWNER = {"type": "human", "id": "owner", "authenticated": True}


@_booted
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
    shared_booted_daemon, monkeypatch, origin, reference, verified
):
    """Real approval producer -> deliver -> booted Messenger over MCP -> Telegram adapter.

    A verified dashboard link becomes a URL button from the Messenger's own
    resolution (a forged or in-flight rewritten URL never survives); an
    unverifiable reference delivers inert.
    """
    import copy
    from datetime import UTC, datetime, timedelta

    from butlers.modules.approvals.notifications import build_approval_request_envelope

    monkeypatch.setenv("DASHBOARD_URL", origin)
    booted = await _shared_messenger(shared_booted_daemon, "telegram")
    telegram = _module(booted, "telegram")
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
    delivery, routed = _deliver_through(booted, monkeypatch)
    route_execute = _mcp_route(booted)

    async def dispatch(_pool, **kwargs):
        # An in-flight rewrite of every open_link must not reach the provider.
        routed.append(copy.deepcopy(kwargs["args"]))
        dispatched = copy.deepcopy(kwargs["args"])
        for action in dispatched["input"]["context"]["notify_request"].get("actions", []):
            if action["verb"] == "open_link":
                action.update(
                    url="https://attacker.example.test/unsafe",
                    host="attacker.example.test",
                    label="Untrusted label",
                )
        result = await route_execute(**dispatched)
        return {
            "result": result,
            "transport": result.get("transport", {}),
            **({"error": result["error"]} if "error" in result else {}),
        }

    monkeypatch.setattr(delivery, "route", dispatch)
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
            _switchboard_pool(), source_butler="relationship", notify_request=forged
        )
        assert refusal["status"] == "failed"
        assert routed == []
        post.assert_not_awaited()
    result = await delivery.deliver(
        _switchboard_pool(),
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


@_booted
async def test_scheduled_delivery_parks_full_envelope_and_approved_registered_replay(
    booted_daemon, tmp_path, monkeypatch
):
    """Spec: REQ-butler-messenger-003. A non-owner delivery parks its exact envelope.

    Per channel: the routed route.execute arguments are parked whole in the real
    ``pending_actions`` row; owner approval replays that stored row through the
    daemon's own approved-tool executor (not the caller's later copy), once.
    """
    from butlers.core.delivery_rendering import inert_delivery_content
    from butlers.core.tool_call_capture import get_current_runtime_session_id

    booted = await _approvals_messenger(booted_daemon, tmp_path)
    approvals = _module(booted, "approvals")
    delivery, routed = _deliver_through(booted, monkeypatch)
    cases = [
        ("telegram", "9001", "_send_message"),
        ("email", "non-owner@example.test", "_send_email"),
    ]
    for channel, recipient, send_attr in cases:
        send = AsyncMock(
            return_value={"ok": True, "result": {"message_id": 991}, "message_id": "991"}
        )
        monkeypatch.setattr(_module(booted, channel), send_attr, send)
        routed.clear()
        request = _valid_notify_request()
        request["delivery"].update(
            channel=channel, recipient=recipient, message="Read https://example.test"
        )
        assert get_current_runtime_session_id() is None
        with inert_delivery_content():
            delivered = await delivery.deliver(
                _switchboard_pool(), source_butler="health", notify_request=request
            )
        send.assert_not_awaited()
        parked = [row for row in await _pending_actions(booted) if row["status"] == "pending"]
        assert len(parked) == 1, (channel, delivered, routed)
        row = parked[0]
        assert row["tool_name"] == "route.execute"
        assert row["tool_args"] == routed[0]
        assert row["tool_args"]["input"]["context"]["notify_request"]["render_links_inert"] is True
        assert row["why"].startswith("Messenger received a deterministic delivery proposal")
        request["delivery"]["message"] = "Caller changed content after parking"

        result = await approvals._approve_action(str(row["id"]), actor=_HUMAN_OWNER)

        assert "error" not in result, (channel, result)
        executed = {r["id"]: r for r in await _pending_actions(booted)}[row["id"]]
        assert executed["status"] == "executed"
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
        # A second dispatch of the executed row does not resend.
        await approvals._dispatch_approved_action_by_id(str(row["id"]))
        assert send.await_count == 1
    # One park per channel, each approved and executed once.
    assert [row["status"] for row in await _pending_actions(booted)] == ["executed", "executed"]


@_booted
async def test_attachment_delivery_parks_with_pinned_digests_and_replay_reverifies(
    booted_daemon, tmp_path, monkeypatch
):
    """Spec: REQ-butler-messenger-003 / REQ-core-notify-033 applied to files.

    Inert-required delivery with files parks even for the seeded owner; a non-owner
    send parks under the recipient gate. The parked envelope pins each file's
    sha256, and approved replay re-reads the blob and refuses changed bytes.
    """
    import hashlib
    from contextlib import nullcontext

    from butlers.core.delivery_rendering import inert_delivery_content

    booted = await _approvals_messenger(booted_daemon, tmp_path, owner_telegram="12345")
    approvals = _module(booted, "approvals")
    telegram = _module(booted, "telegram")
    monkeypatch.setattr("butlers.modules.telegram.write_audit_entry", AsyncMock())
    monkeypatch.setattr("butlers.modules.telegram.require_permission", AsyncMock())
    delivery, _routed = _deliver_through(booted, monkeypatch)
    ref = "s3://butlers/health/statement.pdf"
    for inert in (True, False):
        blobs = {ref: b"%PDF reviewed statement"}

        async def get_blob(storage_ref, blobs=blobs):
            return blobs[storage_ref]

        monkeypatch.setattr(
            telegram, "_blob_store", MagicMock(scheme="s3", bucket="butlers", get=get_blob)
        )
        upload = AsyncMock(return_value={"ok": True, "result": {"message_id": 992}})
        send = AsyncMock(return_value={"ok": True, "result": {"message_id": 991}})
        monkeypatch.setattr(telegram, "_upload_file", upload)
        monkeypatch.setattr(telegram, "_send_message", send)
        recipient = "12345" if inert else "9001"
        request = _valid_notify_request()
        request["delivery"].update(
            recipient=recipient,
            message="Your statement.",
            attachments=[
                {
                    "filename": "statement.pdf",
                    "media_type": "application/pdf",
                    "storage_ref": ref,
                    "sha256": hashlib.sha256(blobs[ref]).hexdigest(),
                    "size_bytes": len(blobs[ref]),
                },
                {
                    "filename": "rows.csv",
                    "media_type": "text/csv",
                    "content_text": "private-row,42\n",
                    "sha256": hashlib.sha256(b"private-row,42\n").hexdigest(),
                    "size_bytes": 15,
                },
            ],
        )
        with inert_delivery_content() if inert else nullcontext():
            await delivery.deliver(
                _switchboard_pool(), source_butler="health", notify_request=request
            )
        upload.assert_not_awaited()
        send.assert_not_awaited()
        parked = [row for row in await _pending_actions(booted) if row["status"] == "pending"]
        assert len(parked) == 1, inert
        row = parked[0]
        parked_request = row["tool_args"]["input"]["context"]["notify_request"]
        assert parked_request["delivery"]["attachments"] == request["delivery"]["attachments"]
        assert parked_request["render_links_inert"] is inert
        if not inert:
            blobs[ref] = b"%PDF swapped after review"

        result = await approvals._approve_action(str(row["id"]), actor=_HUMAN_OWNER)

        status = {r["id"]: r["status"] for r in await _pending_actions(booted)}[row["id"]]
        failures = await _execution_failures(booted, row["id"])
        if inert:
            assert "error" not in result, result
            assert status == "executed"
            assert failures == []
            send.assert_awaited_once_with(
                "12345",
                "[health] Your statement.",
                reply_to_message_id=None,
                render_links_inert=True,
            )
            uploads = [call.args for call in upload.await_args_list]
            assert [(item.data, caption, anchor) for _, item, caption, anchor, _ in uploads] == [
                (blobs[ref], None, 991),
                (b"private-row,42\n", None, 991),
            ]
        else:
            upload.assert_not_awaited()
            send.assert_not_awaited()
            # A failed replay stays approved (retryable) and records why.
            assert status == "approved"
            assert len(failures) == 1
            assert "attachment_digest_mismatch" in failures[0]
        logged = repr(delivery.log_notification.await_args_list) + repr(
            delivery._log_notification_best_effort.await_args_list
        )
        assert "rows.csv" in logged and "private-row" not in logged
    assert len(await _pending_actions(booted)) == 2


@_booted
async def test_registered_delivery_replay_rejects_tampered_wrong_tool_and_child_task(
    booted_daemon, tmp_path, monkeypatch
):
    """An approval context authorizes only its exact tool, arguments and task.

    A tampered envelope, a context bound to another tool and a child task each miss
    the real replay matcher, so the real recipient guard parks the arguments it was
    actually given. The control: the exact bound call, in the authorized task,
    delivers without parking.
    """
    import copy

    from butlers.modules.approvals.execution_context import (
        ApprovalExecutionContext,
        approval_tool_args_digest,
        reset_approval_execution_context,
        set_approval_execution_context,
    )

    booted = await _approvals_messenger(booted_daemon, tmp_path)
    # In process: the approval context is a task-local the MCP transport cannot carry.
    route_execute = (await booted.daemon.mcp.get_tool("route.execute")).fn
    telegram = _module(booted, "telegram")
    send = AsyncMock(return_value={"ok": True, "result": {"message_id": 7}})
    monkeypatch.setattr(telegram, "_send_message", send)
    args = dict(
        schema_version="route.v1",
        request_context=_route_request_context(),
        input={"prompt": "Deliver", "context": {"notify_request": _valid_notify_request()}},
    )

    def bind(tool_name: str = "route.execute"):
        return set_approval_execution_context(
            ApprovalExecutionContext(
                action_id=uuid.uuid4(),
                session_id=None,
                actor="human:owner",
                tool_name=tool_name,
                tool_args_digest=approval_tool_args_digest(args),
                authorized_task=asyncio.current_task(),
            )
        )

    sent_args = []
    for case in ("tampered", "wrong_tool", "child_task"):
        token = bind("email_send_message" if case == "wrong_tool" else "route.execute")
        altered = copy.deepcopy(args)
        if case == "tampered":
            altered["input"]["context"]["notify_request"]["delivery"]["message"] = "Changed"
        try:
            call = route_execute(**copy.deepcopy(altered))
            result = await asyncio.create_task(call) if case == "child_task" else await call
        finally:
            reset_approval_execution_context(token)
        assert result["status"] == "error", case
        sent_args.append(altered)
        assert [row["tool_args"] for row in await _pending_actions(booted)] == sent_args, case
        send.assert_not_awaited()

    token = bind()
    try:
        control = await route_execute(**copy.deepcopy(args))
    finally:
        reset_approval_execution_context(token)
    assert control["status"] == "ok", control
    send.assert_awaited_once()
    assert len(await _pending_actions(booted)) == 3


@_booted
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
    shared_booted_daemon, monkeypatch, channel, inert, supported
):
    """Trusted approval recovery over MCP honours each provider's inert-link contract.

    The booted Messenger's registered route.execute runs the real recovery process:
    preflight, provider invocation, result classification and repeat handling. Only
    the handoff ledger's two DB methods are stubbed, because the shared boot must
    not keep once-only rows between cases; that persistence is proven on real
    Postgres in ``tests/integration/test_approval_push_on_park.py``.
    """
    from butlers.core import approval_delivery_authority as authority
    from butlers.core.approval_delivery_transport import MessengerApprovalHandoffRepository
    from butlers.core.approval_delivery_worker import HandoffResult
    from butlers.modules.whatsapp import WhatsAppModule
    from butlers.tools.switchboard.contracts import parse_notify_request
    from butlers.tools.switchboard.notification.self_links import resolve_self_links

    monkeypatch.setenv("DASHBOARD_URL", "https://dashboard.example.test")
    booted = await _shared_messenger(shared_booted_daemon)
    daemon = booted.daemon
    modules = {module.name: module for module in daemon._modules}
    whatsapp = WhatsAppModule()
    whatsapp._send_message = AsyncMock(return_value={"status": "sent", "message_id": "991"})
    monkeypatch.setattr(daemon, "_modules", [*daemon._modules, whatsapp])
    if channel == "telegram":
        monkeypatch.setattr(modules[channel], "supports_inert_link_delivery", supported)

    # Peer provenance the protected companion would install for the Switchboard.
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
    args = {
        "schema_version": "route.v1",
        "request_context": _route_request_context(source_sender_identity="relationship"),
        "input": {
            "prompt": "Deliver approval",
            "context": {
                "notify_request": output,
                "_trusted_approval_recovery": _trusted_recovery_context(),
            },
        },
    }
    route_execute = _mcp_route(booted)

    result = await route_execute(**args)

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
    repeated = await route_execute(**args)
    assert repeated["result"]["notify_response"]["handoff"] == handoff
    assert post.await_count + smtp.call_count + whatsapp._send_message.await_count == 1


@_booted
async def test_messenger_without_approval_runtime_refuses_non_owner_and_preserves_owner(
    shared_booted_daemon, monkeypatch
):
    from types import SimpleNamespace

    booted = await _shared_messenger(shared_booted_daemon, "telegram")
    route_execute = _mcp_route(booted)
    telegram = _module(booted, "telegram")
    monkeypatch.setattr(
        telegram, "_send_message", AsyncMock(return_value={"ok": True, "result": {"message_id": 7}})
    )
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


class TestRouteExecuteAuthz:
    """Verify route.execute authz: untrusted callers rejected; trusted callers allowed."""

    @_booted
    @pytest.mark.usefixtures("verified_owner_transport")
    async def test_authz_reject_and_allow(self, shared_booted_daemon, monkeypatch) -> None:
        """Untrusted callers rejected with validation_error; trusted switchboard allowed."""
        # Messenger rejects rogue-caller
        messenger = await _shared_messenger(shared_booted_daemon)
        telegram_module = _module(messenger, "telegram")
        monkeypatch.setattr(
            telegram_module,
            "_send_message",
            AsyncMock(return_value={"result": {"message_id": 999}}),
        )

        result = await _mcp_route(messenger)(
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

        # Empty/blank endpoint identity also rejected, and nothing is queued
        butler = await shared_booted_daemon(registered=True)
        result3 = await _mcp_route(butler)(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity=" "),
            input={"prompt": "Run health check."},
        )
        assert result3["status"] == "error"
        assert result3["error"]["class"] == "validation_error"
        assert await _inbox_count(butler) == 0

        # Switchboard caller allowed: messenger delivery proceeds
        monkeypatch.setattr(
            telegram_module,
            "_send_message",
            AsyncMock(return_value={"result": {"message_id": 321}}),
        )
        result_ok = await _mcp_route(messenger)(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="switchboard"),
            input={"prompt": "Deliver.", "context": {"notify_request": _valid_notify_request()}},
        )
        telegram_module._send_message.assert_awaited_once()
        assert result_ok["status"] == "ok"

    # Spec: REQ-approval-delivery-intent-recovery-004; public DTO/correlation cannot create
    # protected approval authority.
    @_booted
    async def test_recovery_requires_authenticated_switchboard_before_provider(
        self, shared_booted_daemon, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        booted = await _shared_messenger(shared_booted_daemon)
        route_execute = _mcp_route(booted)
        telegram = _module(booted, "telegram")
        monkeypatch.setattr(
            telegram, "_send_message", AsyncMock(return_value={"message_id": "provider-ref"})
        )

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
            caplog.at_level("DEBUG", logger="butlers"),
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
        # Refused before any route state was written.
        assert await _inbox_count(booted) == 0
        telegram._send_message.assert_not_awaited()

    @_booted
    @pytest.mark.usefixtures("verified_owner_transport")
    async def test_null_recovery_is_ordinary_notify_not_privileged_recovery(
        self, shared_booted_daemon, monkeypatch
    ) -> None:
        booted = await _shared_messenger(shared_booted_daemon)
        telegram = _module(booted, "telegram")
        monkeypatch.setattr(
            telegram,
            "_send_message",
            AsyncMock(return_value={"ok": True, "result": {"message_id": 4321}}),
        )
        notify_request = {**_valid_notify_request(), "recovery": None}

        result = await _mcp_route(booted)(
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

    @_booted
    @pytest.mark.usefixtures("verified_owner_transport")
    async def test_telegram_transport_failure_is_content_blind_and_uncertain(
        self, shared_booted_daemon, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        booted = await _shared_messenger(shared_booted_daemon)
        telegram = _module(booted, "telegram")
        monkeypatch.setattr(
            telegram,
            "_send_message",
            AsyncMock(
                side_effect=httpx.ReadTimeout(
                    "private-body-sentinel",
                    request=httpx.Request(
                        "POST", "https://api.telegram.org/botsecret-token/sendMessage"
                    ),
                )
            ),
        )

        with caplog.at_level("WARNING"):
            result = await _mcp_route(booted)(
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

    @_booted
    @pytest.mark.usefixtures("verified_owner_transport")
    async def test_permission_unavailable_is_transient_before_native_delivery(
        self, shared_booted_daemon, monkeypatch, caplog
    ):
        from types import SimpleNamespace

        from butlers.core.permissions import PermissionUnavailable
        from butlers.core_tools._routing import _build_routed_delivery_command

        booted = await _shared_messenger(shared_booted_daemon)
        daemon = booted.daemon
        route_execute = _mcp_route(booted)
        modules = {module.name: module for module in daemon._modules}
        # The permission store itself failing is the fault under test.
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
            monkeypatch.setattr(modules["email"], "_pool", permission_pool)
            monkeypatch.setattr(modules["telegram"], "_audit_pool", permission_pool)
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

    @_booted
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
        shared_booted_daemon,
        monkeypatch,
        provider_rejected: bool,
        message: str,
        outcome: str,
        detail: str,
    ) -> None:
        booted = await _shared_messenger(shared_booted_daemon)
        telegram = _module(booted, "telegram")
        monkeypatch.setattr(
            telegram,
            "_send_message",
            AsyncMock(
                side_effect=TelegramProviderResponseError(
                    "telegram_provider_response: invalid message_id",
                    provider_rejected=provider_rejected,
                )
            ),
        )

        result = await _mcp_route(booted)(
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


class _AcceptedRouteProbe:
    """Retain this route's task; report closed state before daemon teardown."""

    def __init__(self, custom, monkeypatch):
        self.custom = custom
        self.runtime = custom.runtime
        self.tasks = {}
        self.trigger_state = "not_entered"
        create = asyncio.create_task
        trigger = custom.daemon.spawner.trigger

        def capture(coro, *args, **kwargs):
            task = create(coro, *args, **kwargs)
            name = kwargs.get("name", "")
            if isinstance(name, str) and name.startswith("route-inbox-"):
                try:
                    key = uuid.UUID(name.removeprefix("route-inbox-"))
                except ValueError:
                    pass
                else:
                    self.tasks[key] = task
            return task

        async def observed_trigger(*args, **kwargs):
            self.trigger_state = "entered"
            try:
                result = await trigger(*args, **kwargs)
            except asyncio.CancelledError:
                self.trigger_state = "cancelled"
                raise
            except Exception:
                self.trigger_state = "raised"
                raise
            self.trigger_state = "successful" if result.success else "refused"
            return result

        monkeypatch.setattr(asyncio, "create_task", capture)
        monkeypatch.setattr(custom.daemon.spawner, "trigger", observed_trigger)

    async def snapshot(self, accepted):
        key = uuid.UUID(accepted["inbox_id"])
        task = self.tasks.get(key)
        locations = []
        current = task.get_coro() if task is not None else None
        for _ in range(16):
            frame = getattr(current, "cr_frame", None) or getattr(current, "gi_frame", None)
            if frame is not None:
                locations.append(
                    {
                        "file": Path(frame.f_code.co_filename).name,
                        "function": frame.f_code.co_name,
                        "line": frame.f_lineno,
                    }
                )
            current = getattr(current, "cr_await", None) or getattr(current, "gi_yieldfrom", None)
            if current is None:
                break
        state = {
            "await_locations": locations,
            "task": "missing"
            if task is None
            else ("cancelled" if task.cancelled() else "terminal" if task.done() else "live"),
            "trigger": self.trigger_state,
            "same_fake": self.custom.runtime is self.runtime,
            "fake_invocations": len(self.runtime.invocations),
            "adapter_pool_count": len(self.custom.daemon.spawner._adapter_pool),
            "adapter_pool_same_fake": bool(self.custom.daemon.spawner._adapter_pool)
            and all(
                adapter is self.runtime
                for adapter in self.custom.daemon.spawner._adapter_pool.values()
            ),
            "task_exception": bool(
                task is not None
                and task.done()
                and not task.cancelled()
                and task.exception() is not None
            ),
            "inbox": "unavailable",
        }
        # The readiness criterion remains the original ten-second wait. This
        # separate bounded read describes its failure; it never retries dispatch.
        try:
            async with asyncio.timeout(2):
                row = await self.custom.daemon.db.pool.fetchrow(
                    "SELECT r.lifecycle_state, r.session_id IS NOT NULL AS session_bound, "
                    "r.processing_claim_id IS NOT NULL AS claim_present, "
                    "s.completed_at IS NOT NULL AS session_completed, "
                    "s.success IS TRUE AS session_success, s.error IS NOT NULL AS session_error "
                    "FROM route_inbox r LEFT JOIN sessions s ON s.id=r.session_id WHERE r.id=$1",
                    key,
                )
            if row:
                state["inbox"] = {
                    "lifecycle_state": row["lifecycle_state"]
                    if row["lifecycle_state"] in {"accepted", "processing", "processed", "errored"}
                    else "unknown",
                    **{
                        key: row[key] is True
                        for key in (
                            "session_bound",
                            "claim_present",
                            "session_completed",
                            "session_success",
                            "session_error",
                        )
                    },
                }
        except Exception:
            pass
        return state


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["successful", "refused", "raised", "live", "wrong_fake"])
async def test_accepted_route_probe_distinguishes_positioned_states(monkeypatch, mode):
    """These classify software controls; they do not explain a hosted failure."""
    runtime = FakeRuntime()
    selected = FakeRuntime() if mode == "wrong_fake" else runtime
    release = asyncio.Event()

    async def trigger():
        if mode == "live":
            await release.wait()
        if mode == "raised":
            raise RuntimeError("synthetic positioned failure")
        if mode in {"successful", "wrong_fake"}:
            await selected.invoke("synthetic", "", {}, {})
        return SimpleNamespace(success=mode != "refused")

    pool = SimpleNamespace(fetchrow=AsyncMock(return_value=None))
    spawner = SimpleNamespace(trigger=trigger, _adapter_pool={"synthetic": selected})
    custom = SimpleNamespace(
        runtime=runtime, daemon=SimpleNamespace(spawner=spawner, db=SimpleNamespace(pool=pool))
    )
    probe = _AcceptedRouteProbe(custom, monkeypatch)
    key = uuid.uuid4()
    task = asyncio.create_task(spawner.trigger(), name=f"route-inbox-{key}")
    try:
        if mode == "live":
            await asyncio.sleep(0)
        else:
            await asyncio.gather(task, return_exceptions=True)
        state = await probe.snapshot({"inbox_id": str(key)})
        assert state["task"] == ("live" if mode == "live" else "terminal")
        assert state["trigger"] == {"live": "entered", "wrong_fake": "successful"}.get(mode, mode)
        assert state["task_exception"] is (mode == "raised")
        assert state["adapter_pool_same_fake"] is (mode != "wrong_fake")
        assert state["same_fake"] is True
        assert state["fake_invocations"] == (1 if mode == "successful" else 0)
        # Mismatched inbox identity must not borrow a terminal task's authority.
        assert (await probe.snapshot({"inbox_id": str(uuid.uuid4())}))["task"] == "missing"
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert task.done()


class TestRouteExecuteCustomTrustedCallers:
    """Verify custom trusted_route_callers from butler.toml is respected."""

    @_booted
    async def test_custom_trusted_callers(self, booted_daemon, tmp_path: Path, monkeypatch) -> None:
        """Custom list caller allowed; switchboard rejected when not in list; empty list rejects all."""
        # Custom caller in list → accepted, and its session runs
        custom = await _boot_butler(
            booted_daemon, tmp_path / "c1", trusted_route_callers=["switchboard", "heartbeat"]
        )
        with monkeypatch.context() as observed:
            probe = _AcceptedRouteProbe(custom, observed)
            result = await _mcp_route(custom)(
                schema_version="route.v1",
                request_context=_route_request_context(source_endpoint_identity="heartbeat"),
                input={"prompt": "Tick check."},
            )
            assert result["status"] == "accepted"
            try:
                await custom.runtime.wait_for_invocations(1)
            except TimeoutError as exc:
                raise AssertionError(
                    "accepted_route_wait="
                    + json.dumps(await probe.snapshot(result), sort_keys=True)
                ) from exc

        # Switchboard excluded from custom list → rejected
        internal = await _boot_butler(
            booted_daemon, tmp_path / "c2", trusted_route_callers=["internal-only"]
        )
        result2 = await _mcp_route(internal)(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="switchboard"),
            input={"prompt": "Run health check."},
        )
        assert result2["status"] == "error"
        assert result2["error"]["class"] == "validation_error"
        assert "switchboard" in result2["error"]["message"]

        # Empty list → everyone rejected
        closed = await _boot_butler(booted_daemon, tmp_path / "c3", trusted_route_callers=[])
        result3 = await _mcp_route(closed)(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="switchboard"),
            input={"prompt": "Run health check."},
        )
        assert result3["status"] == "error"
        assert result3["error"]["class"] == "validation_error"
        for rejecting in (internal, closed):
            assert await _inbox_count(rejecting) == 0
        # One fake runtime serves every boot in a test: only the accepted route ran.
        assert len(custom.runtime.invocations) == 1


@pytest.mark.parametrize("mode", ["refused", "live"])
@_booted
async def test_accepted_route_probe_positions_real_route_stage(
    booted_daemon, tmp_path, monkeypatch, mode
):
    """Validate stage measurement on real routes, not a hosted-cause claim."""
    from butlers.core.spawner import SpawnerResult

    custom = await _boot_butler(
        booted_daemon, tmp_path / "positioned", trusted_route_callers=["heartbeat"]
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    real_trigger = custom.daemon.spawner.trigger

    async def positioned(*args, **kwargs):
        entered.set()
        if mode == "refused":
            return SpawnerResult(success=False, error="synthetic positioned refusal")
        await release.wait()
        return await real_trigger(*args, **kwargs)

    with monkeypatch.context() as observed:
        observed.setattr(custom.daemon.spawner, "trigger", positioned)
        probe = _AcceptedRouteProbe(custom, observed)
        accepted = await _mcp_route(custom)(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="heartbeat"),
            input={"prompt": "Synthetic stage control."},
        )
        assert accepted["status"] == "accepted"
        task = probe.tasks[uuid.UUID(accepted["inbox_id"])]
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            if mode == "refused":
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
            state = await probe.snapshot(accepted)
            assert state["same_fake"] is True
            assert state["fake_invocations"] == 0
            assert state["task"] == ("terminal" if mode == "refused" else "live")
            assert state["trigger"] == ("refused" if mode == "refused" else "entered")
            assert state["inbox"]["lifecycle_state"] == (
                "errored" if mode == "refused" else "processing"
            )
            assert state["inbox"]["session_bound"] is False
        finally:
            release.set()
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        if mode == "live":
            await custom.runtime.wait_for_invocations(1)
            restored = await probe.snapshot(accepted)
            assert restored["task"] == "terminal"
            assert restored["trigger"] == "successful"
            assert restored["inbox"]["lifecycle_state"] == "processed"
            assert restored["inbox"]["session_bound"] is True
            assert restored["inbox"]["session_success"] is True
            assert restored["fake_invocations"] == 1
        assert task.done()


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
