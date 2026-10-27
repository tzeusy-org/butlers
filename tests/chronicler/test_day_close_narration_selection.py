"""Real writer selection controls for the existing retrospective cache contract."""

from __future__ import annotations

from contextlib import ExitStack, asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from butlers.chronicler.day_close_writer import (
    DAY_CLOSE_TASK_NAME,
    select_day_close_narration,
    write_day_close_cache,
)
from butlers.core.spawner_tool_calls import _merge_tool_call_records
from butlers.core.tool_call_capture import (
    consume_runtime_session_tool_calls,
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
)
from butlers.core_tools._base import ToolContext
from butlers.core_tools._notifications import register_notification_tools
from butlers.mcp_wrappers import _tool_input_fingerprint, _ToolCallLoggingMCP

pytestmark = pytest.mark.unit
DAY = "2026-04-24"
SESSION = UUID("00000000-0000-4000-8000-000000000001")
MESSAGE = "The day settled into focused reading and an evening walk."


def bundle():
    return {
        "name": "chronicler_day_close_bundle",
        "input": {"date_label": DAY, "timezone": "UTC"},
        "outcome": "success",
        "result": {"date": DAY, "episodes": [{}], "events": [], "citations": ["fixture:one"]},
    }


def registered_notify():
    """Read the real registration signature without invoking a handler."""
    handlers = {}

    def decorate(_group, **tool_kwargs):
        def capture(fn):
            if fn.__name__ == "notify":
                assert tool_kwargs == {"exclude_args": ["priority"]}
            handlers[fn.__name__] = fn
            return fn

        return capture

    context = ToolContext(
        daemon=None,
        pool=None,
        spawner=None,
        butler_name="chronicler",
        butler_type="butler",
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_notification_tools(context, None, decorate)
    return handlers["notify"]


def notification():
    payload = {"message": MESSAGE, "channel": "telegram", "intent": "send"}
    return {
        "name": "notify",
        "input": payload,
        "input_fingerprint": _tool_input_fingerprint(registered_notify(), (), payload),
        "outcome": "success",
        "result": {
            "status": "deferred",
            "notification_id": "00000000-0000-4000-8000-000000000002",
            "channel": "telegram",
        },
    }


class Pool:
    def __init__(self):
        self.conn = AsyncMock()
        self.conn.fetchrow.return_value = None
        self.conn.transaction = self.acquire
        self.execute = self.conn.execute

    @asynccontextmanager
    async def acquire(self, **_kwargs):
        yield self.conn


@pytest.mark.parametrize("status", ["deferred", "ok"])
async def test_executed_owner_notify_narration_wins_over_assistant_status(monkeypatch, status):
    pool = Pool()
    saved = AsyncMock()
    monkeypatch.setattr("butlers.chronicler.day_close_writer.upsert_tier2_cache", saved)
    notify = notification()
    if status == "ok":
        notify["result"] = {"status": "ok", "result": {"status": "sent", "notification_id": None}}
    result = SimpleNamespace(
        success=True,
        output="Delivery deferred by quiet hours.",
        session_id=SESSION,
        tool_calls=[bundle(), notify],
    )
    await write_day_close_cache(
        pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=datetime(2026, 4, 25, 1, 5, tzinfo=UTC),
    )
    assert saved.await_count == 1
    assert saved.call_args.kwargs["prose"] == MESSAGE
    assert saved.call_args.kwargs["invalid_reason"] is None
    assert saved.call_args.kwargs["provenance_refs"] == ["fixture:one"]


@pytest.mark.parametrize(
    "species",
    [
        "parser",
        "failed",
        "recipient",
        "session",
        "session-missing",
        "tuple",
        "ambiguous",
        "suppressed",
        "channel",
        "reference",
    ],
)
async def test_unproven_notify_cannot_replace_valid_destination(monkeypatch, species):
    from copy import deepcopy

    calls = [bundle(), notification()]
    if species == "parser":
        del calls[1]["outcome"]
    elif species == "failed":
        calls[1]["outcome"] = "error"
    elif species == "recipient":
        calls[1]["input"]["recipient"] = "fixture-other-recipient"
    elif species == "session":
        calls[1]["session_id"] = "another-session"
    elif species == "tuple":
        calls[0]["input"]["timezone"] = "Asia/Singapore"
    elif species == "ambiguous":
        calls.append(deepcopy(calls[1]))
    elif species == "channel":
        calls[1]["input"]["channel"] = "unsupported-fixture"
    elif species == "reference":
        calls[1]["result"]["notification_id"] = "not-a-reference"
    elif species == "suppressed":
        calls[1]["result"] = {"status": "suppressed"}
    if species in {"recipient", "channel"}:
        calls[1]["input_fingerprint"] = _tool_input_fingerprint(
            registered_notify(),
            (),
            calls[1]["input"],
        )
    pool = Pool()
    pool.conn.fetchrow.return_value = {"prose": MESSAGE, "date_label": DAY, "invalid_reason": None}
    saved = AsyncMock()
    monkeypatch.setattr("butlers.chronicler.day_close_writer.upsert_tier2_cache", saved)
    result = SimpleNamespace(
        success=True,
        output="A plausible but unrelated retrospective.",
        session_id=SESSION,
        tool_calls=calls,
    )
    if species == "session-missing":
        result.session_id = None
    outcome = await write_day_close_cache(
        pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=datetime(2026, 4, 25, 1, 5, tzinfo=UTC),
    )
    assert outcome.invalid_reason == "inadmissible_prose"
    saved.assert_not_awaited()


async def test_parser_duplicate_is_not_a_second_executed_notification(monkeypatch):
    pool = Pool()
    saved = AsyncMock()
    monkeypatch.setattr("butlers.chronicler.day_close_writer.upsert_tier2_cache", saved)
    parser = {"name": "notify", "input": {"message": MESSAGE}}
    result = SimpleNamespace(
        success=True,
        output="Message deferred until morning.",
        session_id=SESSION,
        tool_calls=[bundle(), parser, notification()],
    )
    await write_day_close_cache(
        pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=datetime(2026, 4, 25, 1, 5, tzinfo=UTC),
    )
    assert saved.call_args.kwargs["prose"] == MESSAGE


async def test_real_sanitized_notify_capture_and_merge_selects_intended_narration():
    """Exercise real core registration/capture/merge; effects stay controlled."""

    class MCP:
        def __init__(self):
            self.handlers = {}
            self.metadata = {}

        def tool(self, *args, **kwargs):
            def decorate(fn):
                name = kwargs.get("name") or fn.__name__
                self.handlers[name] = fn
                self.metadata[name] = kwargs
                return fn

            return decorate

    mcp = MCP()
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
    assert mcp.metadata["notify"] == {"exclude_args": ["priority"]}
    token = set_current_runtime_session_id(str(SESSION))
    try:
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "butlers.core_tools._notifications.check_permission",
                    AsyncMock(return_value=SimpleNamespace(allowed=True)),
                )
            )
            stack.enter_context(
                patch(
                    "butlers.core.approvals_hooks.check_recipient",
                    AsyncMock(return_value=SimpleNamespace(allowed=True, dossier_error=None)),
                )
            )
            stack.enter_context(
                patch(
                    "butlers.core.temporal.delivery_db.get_delivery_preferences",
                    AsyncMock(return_value={"timezone": "UTC"}),
                )
            )
            stack.enter_context(
                patch(
                    "butlers.core.temporal.delivery.should_defer_notification",
                    return_value=True,
                )
            )
            stack.enter_context(
                patch(
                    "butlers.core.temporal.delivery.compute_deliver_at",
                    return_value=datetime(2026, 4, 25, 7, tzinfo=UTC),
                )
            )
            queued = stack.enter_context(
                patch(
                    "butlers.core.temporal.delivery_db.insert_deferred_notification",
                    AsyncMock(return_value="00000000-0000-4000-8000-000000000002"),
                )
            )
            ledger = stack.enter_context(
                patch(
                    "butlers.core_tools._notifications.record_attention_event",
                    AsyncMock(),
                )
            )
            result = await mcp.handlers["notify"](channel="telegram", message=MESSAGE)
        executed = consume_runtime_session_tool_calls(str(SESSION))
    finally:
        reset_current_runtime_session_id(token)
        consume_runtime_session_tool_calls(str(SESSION))
    assert result["status"] == "deferred"
    queued.assert_awaited_once()
    ledger.assert_awaited_once()
    daemon.switchboard_client.call_tool.assert_not_awaited()
    pool.execute.assert_not_awaited()
    pool.fetch.assert_not_awaited()
    assert len(executed) == 1 and executed[0]["outcome"] == "success"
    assert "input" not in executed[0]  # No raw message capture widening.
    parsed = [
        {"name": "mcp__chronicler__notify", "input": {"channel": "telegram", "message": MESSAGE}}
    ]
    merged = _merge_tool_call_records(parsed, executed, butler_name="chronicler")
    assert len(merged) == 2  # Default-expanded executed hash differs from raw parser hash.
    selected = select_day_close_narration(
        [bundle(), *merged],
        output="Delivery deferred by quiet hours.",
        session_id=str(SESSION),
        target_date=DAY,
        timezone="UTC",
    )
    assert not selected.invalid and selected.prose == MESSAGE
    assert selected.notification_id == result["notification_id"]


@pytest.mark.parametrize("returned_channel", [None, "email"])
def test_deferred_channel_is_bound_to_actual_result_and_explicit_request(returned_channel):
    notify = notification()
    notify["result"]["channel"] = returned_channel
    selected = select_day_close_narration(
        [bundle(), notify],
        output=MESSAGE,
        session_id=str(SESSION),
        target_date=DAY,
        timezone="UTC",
    )
    assert selected.invalid


@pytest.mark.parametrize("representation", ["separate", "merged", "JSON-arguments"])
def test_only_recomputed_actual_signature_fingerprint_binds_value_carrier(representation):
    import inspect
    import json

    notify = notification()
    captured = {k: v for k, v in notify.items() if k != "input"}
    payload = dict(notify["input"])
    if representation == "merged":
        bound = inspect.signature(registered_notify()).bind(**payload)
        bound.apply_defaults()
        payload = dict(bound.arguments)
    parser = {"name": "mcp__chronicler__notify", "input": payload}
    if representation == "JSON-arguments":
        parser = {"name": "mcp__chronicler__notify", "arguments": json.dumps(payload)}
    merged = _merge_tool_call_records([parser], [captured], butler_name="chronicler")
    assert len(merged) == (1 if representation == "merged" else 2)
    selected = select_day_close_narration(
        [bundle(), *merged],
        output="Delivery deferred by quiet hours.",
        session_id=str(SESSION),
        target_date=DAY,
        timezone="UTC",
    )
    assert not selected.invalid and selected.prose == MESSAGE
    assert selected.channel == "telegram" and selected.deferred


@pytest.mark.parametrize(
    "species",
    [
        "message",
        "recipient",
        "forged-parser-hash",
        "missing-hash",
        "missing-carrier",
        "duplicate-carrier",
        "unknown-key",
        "duplicate-JSON-key",
        "malformed-JSON",
    ],
)
def test_unbound_or_ambiguous_parser_values_never_establish_executed_narration(species):
    notify = notification()
    captured = {k: v for k, v in notify.items() if k != "input"}
    parser = {"name": "mcp__chronicler__notify", "input": dict(notify["input"])}
    parsed = [parser]
    if species in {"message", "forged-parser-hash"}:
        parser["input"]["message"] = "A different plausible retrospective."
        if species == "forged-parser-hash":
            # This induces an actual merge with the captured outcome. Its
            # inherited parser input must still be independently recomputed.
            parser["input_fingerprint"] = captured["input_fingerprint"]
    elif species == "recipient":
        parser["input"]["recipient"] = "fixture-other"
    elif species == "missing-hash":
        del captured["input_fingerprint"]
    elif species == "missing-carrier":
        parsed = []
    elif species == "duplicate-carrier":
        parsed.append(dict(parser))
    elif species == "unknown-key":
        parser["input"]["invented_target"] = "fixture-other"
    elif species == "duplicate-JSON-key":
        parser["input"] = '{"message":"first","message":"second","channel":"telegram"}'
    elif species == "malformed-JSON":
        parser["input"] = "{"
    merged = _merge_tool_call_records(parsed, [captured], butler_name="chronicler")
    if species == "forged-parser-hash":
        assert len(merged) == 1 and merged[0]["input"] == parser["input"]
    selected = select_day_close_narration(
        [bundle(), *merged],
        output=MESSAGE,
        session_id=str(SESSION),
        target_date=DAY,
        timezone="UTC",
    )
    assert selected.invalid


@pytest.mark.parametrize("schema", ["unavailable", "changed-default"])
def test_signature_discovery_failure_or_version_mismatch_cannot_guess_defaults(monkeypatch, schema):
    import inspect

    from butlers.chronicler import day_close_writer as writer

    discover = writer._notify_signature
    notify = notification()
    signature = inspect.signature(registered_notify())
    if schema == "changed-default":
        signature = signature.replace(
            parameters=[
                p.replace(default="fixture-changed") if p.name == "priority" else p
                for p in signature.parameters.values()
            ]
        )
    else:
        signature = None
    monkeypatch.setattr("butlers.chronicler.day_close_writer._notify_signature", lambda: signature)
    selected = select_day_close_narration(
        [bundle(), notify],
        output=MESSAGE,
        session_id=str(SESSION),
        target_date=DAY,
        timezone="UTC",
    )
    assert selected.invalid
    # Inspect real registration for each policy drift, not a supplied signature.
    for policy in (None, [], ["recipient"], ["priority", "recipient"]):

        def changed_registration(context, mcp, decorate, policy=policy):
            def changed_policy(group, **kwargs):
                if "exclude_args" in kwargs:
                    if policy is None:
                        kwargs.pop("exclude_args")
                    else:
                        kwargs["exclude_args"] = policy
                return decorate(group, **kwargs)

            register_notification_tools(context, mcp, changed_policy)

        with monkeypatch.context() as changed:
            changed.setattr(
                "butlers.core_tools._notifications.register_notification_tools",
                changed_registration,
            )
            discover.cache_clear()
            try:
                assert discover() is None
            finally:
                discover.cache_clear()


@pytest.mark.parametrize("inner", [{"status": "failed"}, {"status": "refused"}, None])
def test_top_level_ok_without_actual_nested_sent_result_is_not_narration_authority(inner):
    notify = notification()
    notify["result"] = {"status": "ok", "result": inner}
    selected = select_day_close_narration(
        [bundle(), notify],
        output=MESSAGE,
        session_id=str(SESSION),
        target_date=DAY,
        timezone="UTC",
    )
    assert selected.invalid
