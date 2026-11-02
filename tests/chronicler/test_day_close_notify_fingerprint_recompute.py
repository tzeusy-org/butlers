"""Day-close recovery recomputes a notify fingerprint from a persisted row in a later process.

``day_close_recovery`` reads ``sessions.tool_calls`` long after the session's process is
gone, so the per-process fingerprint key it ran under is lost. The executed notify
capture persists the allowlisted notify view, and a call whose persisted view drops
nothing carries a plain sha256 the recovery path can recompute. A call that set a
non-allowlisted field keeps a keyed fingerprint, so recovery fails closed for it.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from contextlib import ExitStack
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from butlers.chronicler.day_close_writer import select_day_close_narration
from butlers.core import tool_call_capture
from butlers.core.tool_call_capture import (
    consume_runtime_session_tool_calls,
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
)
from butlers.core_tools._base import ToolContext
from butlers.core_tools._notifications import register_notification_tools
from butlers.mcp_wrappers import _ToolCallLoggingMCP

pytestmark = pytest.mark.unit

DAY = "2026-04-24"
SESSION = UUID("00000000-0000-4000-8000-000000000011")
NOTIFICATION_ID = "00000000-0000-4000-8000-000000000012"
MESSAGE = "The day settled into focused reading and an evening walk."
SENTINEL = "SENTINEL-NOTIFY-SUBJECT-4410"


def _bundle() -> dict:
    return {
        "name": "chronicler_day_close_bundle",
        "input": {"date_label": DAY, "timezone": "UTC"},
        "outcome": "success",
        "result": {"date": DAY, "episodes": [{}], "events": [], "citations": ["fixture:one"]},
    }


async def _executed_notify(**arguments) -> dict:
    """Run the real registered notify through the real capture wrapper (deferred path)."""

    class _Recorder:
        def __init__(self) -> None:
            self.handlers: dict = {}

        def tool(self, *args, **kwargs):
            def decorate(fn):
                self.handlers[kwargs.get("name") or fn.__name__] = fn
                return fn

            return decorate

    mcp = _Recorder()
    proxy = _ToolCallLoggingMCP(mcp, "chronicler", module_name="core")
    pool = AsyncMock()
    daemon = SimpleNamespace(
        db=SimpleNamespace(pool=pool),
        switchboard_client=SimpleNamespace(call_tool=AsyncMock()),
        _resolve_default_notify_recipient=AsyncMock(return_value="fixture-owner"),
        _approval_push_runtime=None,
    )
    context = ToolContext(
        daemon=daemon,
        pool=pool,
        spawner=None,
        butler_name="chronicler",
        butler_type="butler",
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_notification_tools(context, proxy, lambda _group, **kwargs: proxy.tool(**kwargs))
    handlers = mcp.handlers
    token = set_current_runtime_session_id(str(SESSION))
    try:
        with ExitStack() as stack:
            for target, value in (
                (
                    "butlers.core_tools._notifications.check_permission",
                    AsyncMock(return_value=SimpleNamespace(allowed=True)),
                ),
                (
                    "butlers.core.approvals_hooks.check_recipient",
                    AsyncMock(return_value=SimpleNamespace(allowed=True, dossier_error=None)),
                ),
                (
                    "butlers.core.temporal.delivery_db.get_delivery_preferences",
                    AsyncMock(return_value={"timezone": "UTC"}),
                ),
                (
                    "butlers.core.temporal.delivery_db.insert_deferred_notification",
                    AsyncMock(return_value=NOTIFICATION_ID),
                ),
                ("butlers.core_tools._notifications.record_attention_event", AsyncMock()),
            ):
                stack.enter_context(patch(target, value))
            stack.enter_context(
                patch("butlers.core.temporal.delivery.should_defer_notification", return_value=True)
            )
            stack.enter_context(
                patch(
                    "butlers.core.temporal.delivery.compute_deliver_at",
                    return_value=datetime(2026, 4, 25, 7, tzinfo=UTC),
                )
            )
            result = await handlers["notify"](**arguments)
        executed = consume_runtime_session_tool_calls(str(SESSION))
    finally:
        reset_current_runtime_session_id(token)
        consume_runtime_session_tool_calls(str(SESSION))
    assert result["status"] == "deferred"
    assert len(executed) == 1
    return executed[0]


def _in_a_later_process(record: dict, monkeypatch) -> list[dict]:
    """Round-trip through JSON (the persisted row) and rotate the per-process key."""
    monkeypatch.setattr(tool_call_capture, "_FINGERPRINT_KEY", secrets.token_bytes(32))
    return json.loads(json.dumps([_bundle(), record]))


def _select(calls: list[dict]):
    return select_day_close_narration(
        calls, output=None, session_id=str(SESSION), target_date=DAY, timezone="UTC"
    )


async def test_recovery_recomputes_a_lossless_notify_fingerprint_in_a_later_process(
    monkeypatch,
) -> None:
    record = await _executed_notify(channel="telegram", message=MESSAGE)

    assert record["input"]["message"] == MESSAGE
    assert record["input"]["channel"] == "telegram"

    selected = _select(_in_a_later_process(record, monkeypatch))

    assert not selected.invalid
    assert selected.prose == MESSAGE
    assert selected.deferred and selected.notification_id == NOTIFICATION_ID


async def test_a_notify_that_sets_a_non_allowlisted_field_stays_keyed_and_fails_closed(
    monkeypatch,
) -> None:
    record = await _executed_notify(channel="telegram", message=MESSAGE, subject=SENTINEL)
    plain = {
        hashlib.sha256(json.dumps(view, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        for view in (record["input"],)
    }

    assert SENTINEL not in json.dumps(record)
    assert record["input_fingerprint"] not in plain

    selected = _select(_in_a_later_process(record, monkeypatch))

    assert selected.invalid
