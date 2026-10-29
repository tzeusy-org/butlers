"""Confirm admission uses the existing notify guards and a dark trusted producer."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.config import ButlerType
from butlers.core_tools._base import ToolContext
from butlers.core_tools._notifications import register_notification_tools

pytestmark = pytest.mark.unit
_OPTIONS = [{"value": "yes", "label": "Work"}, {"value": "no", "label": "Not work"}]


@pytest.fixture
def notification(monkeypatch):
    guards = {}
    for module, name, value in (
        ("butlers.core_tools._notifications", "check_permission", SimpleNamespace(allowed=True)),
        (
            "butlers.core.attention_urgency",
            "evaluate_interruption",
            SimpleNamespace(interrupts=False, cause="routine", ref=None),
        ),
        (
            "butlers.core.approvals_hooks",
            "check_recipient",
            SimpleNamespace(allowed=True, reason="owner", dossier_error=None),
        ),
        (
            "butlers.core.approvals_hooks",
            "check_email_recipient",
            SimpleNamespace(allowed=True, reason="owner", dossier_error=None),
        ),
        ("butlers.core.temporal.delivery_db", "get_delivery_preferences", None),
        ("butlers.core.approvals_policy", "get_approvals_policy_quiet_hours", None),
        ("butlers.core_tools._notifications", "get_suppressing_context", None),
        ("butlers.core_tools._notifications", "record_attention_event", None),
        ("butlers.fleet_events", "publish_fleet_event", None),
    ):
        mock = AsyncMock(return_value=value)
        monkeypatch.setattr(f"{module}.{name}", mock)
        guards[name] = mock
    monkeypatch.setattr(
        "butlers.core_tools._notifications.get_current_runtime_session_id",
        lambda: "trusted-session",
    )
    daemon = SimpleNamespace(
        db=SimpleNamespace(pool=object()),
        switchboard_client=SimpleNamespace(
            call_tool=AsyncMock(
                return_value=SimpleNamespace(is_error=False, data={"status": "sent"})
            )
        ),
        _approval_push_runtime=None,
        _resolve_default_notify_recipient=AsyncMock(return_value="42"),
    )
    tools = {}

    def register(group, **kwargs):
        def decorate(fn):
            tools[fn.__name__] = fn
            return fn

        return decorate

    register_notification_tools(
        ToolContext(daemon, daemon.db.pool, None, "test", ButlerType.BUTLER, False, False, None),
        None,
        register,
    )
    return tools["notify"], daemon, guards


async def test_confirm_validates_then_remains_dark(notification):
    notify, daemon, guards = notification
    for args in (
        {},
        {"options": _OPTIONS[:1]},
        {"options": _OPTIONS, "timeout_seconds": 5},
        {"options": _OPTIONS, "on_timeout_value": "other"},
        {"options": [_OPTIONS[0], _OPTIONS[0]]},
        {"options": _OPTIONS, "timeout_seconds": True},
    ):
        result = await notify(intent="confirm", message="Work?", **args)
        assert result["status"] == "error" and result["code"] == "confirm_validation"
    result = await notify(intent="confirm", message="Work?", options=_OPTIONS)
    assert result["status"] == "error" and result["code"] == "confirm_runtime_unavailable"
    daemon.switchboard_client.call_tool.assert_not_awaited()
    guards["check_recipient"].assert_not_awaited()


async def test_confirm_uses_recipient_guard_before_trusted_producer(notification):
    notify, daemon, guards = notification
    confirm_id = str(uuid4())
    daemon._confirm_notify = AsyncMock(
        return_value={"status": "accepted", "confirm_id": confirm_id}
    )
    guards["check_recipient"].return_value = SimpleNamespace(
        allowed=False,
        reason="no_rule",
        dossier_error=None,
        contact_desc="non-owner",
        action_id=uuid4(),
    )
    result = await notify(
        intent="confirm", message="Work?", options=_OPTIONS, recipient="42", _why="Ask about work"
    )
    assert result["status"] == "pending_approval"
    daemon._confirm_notify.assert_not_awaited()
    guard_args = guards["check_recipient"].call_args.kwargs["park_tool_args"]
    assert guard_args["options"] == _OPTIONS
    guards["check_recipient"].return_value = SimpleNamespace(
        allowed=True, reason="owner", dossier_error=None
    )
    result = await notify(intent="confirm", message="Work?", options=_OPTIONS)
    assert result == {"status": "ok", "confirm_id": confirm_id}
    payload = daemon._confirm_notify.call_args.args[0]
    assert payload == {
        "message": "Work?",
        "options": _OPTIONS,
        "timeout_seconds": 600,
        "on_timeout_value": None,
        "channel": "telegram",
        "recipient": "42",
        "runtime_session_id": "trusted-session",
    }
    daemon.switchboard_client.call_tool.assert_not_awaited()
    daemon._confirm_notify.side_effect = TimeoutError("synthetic lost acceptance receipt")
    result = await notify(intent="confirm", message="Work?", options=_OPTIONS)
    assert result == {"status": "error", "code": "confirm_acceptance_unconfirmed"}


async def test_email_confirm_is_numbered_send_without_callback_registration(notification):
    notify, daemon, guards = notification
    daemon._confirm_notify = AsyncMock()
    result = await notify(
        channel="email",
        intent="confirm",
        message="Work?",
        options=_OPTIONS,
        recipient="owner@example.test",
    )
    assert result["status"] == "ok" and "confirm_id" not in result
    daemon._confirm_notify.assert_not_awaited()
    request = daemon.switchboard_client.call_tool.call_args.args[1]["notify_request"]
    assert request["delivery"] == {
        "intent": "send",
        "channel": "email",
        "message": "Work?\n\n1. Work\n2. Not work",
        "recipient": "42",
    }
    assert (
        guards["check_email_recipient"].call_args.kwargs["rule_match_args"]["message"]
        == request["delivery"]["message"]
    )
