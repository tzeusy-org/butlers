"""Tests for the attention-ledger wiring at the notify() boundary (bu-qvnce.8).

Covers the governance layer added on top of the existing approvals_policy
quiet-hours gate:
  - routine implicit-owner decisions held by quiet hours or context bus are
    durably deferred, with a full ``notify.v1`` envelope and a ledger row,
    instead of vanishing without a trace
  - context-bus dnd/sleeping signals are consulted deterministically,
    alongside (not instead of) the existing hour-based quiet-hours policy
  - a model-assigned priority="high" cannot bypass owner attention policy
  - a successfully delivered notification is recorded as outcome="delivered"

These tests boot a real ``ButlerDaemon`` on a migrated database (bu-rvgzmf.2) and
call its registered ``notify`` tool, so the policy row, the deferred queue and the
ledger are real rows (see ``tests/core/test_attention_ledger.py`` for the pure
helpers). The Switchboard client, identity resolution and context bus stay faked.
Every test writes ledger, deferred or policy rows, so each gets a fresh boot.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.context_bus import ContextEntry
from butlers.core.attention_ledger import ATTENTION_LEDGER_SESSION_KEY
from butlers.core.tool_call_capture import (
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
)
from butlers.testing.daemon import write_butler_config
from butlers.testing.db import open_pool

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _butler(
    booted_daemon,
    tmp_path: Path,
    *,
    quiet_hours: bool = False,
    approvals: bool = False,
    name: str = "general",
):
    """Boot *name* with the owner's quiet window set: 22:00-07:00 UTC, or none.

    ``approvals`` enables the Approvals module, whose startup registers the real
    recipient guards and park implementation on the butler's pool.
    """
    chains = ("core", "approvals") if approvals else ("core", name) if name == "switchboard" else ()
    booted = await booted_daemon(
        write_butler_config(
            tmp_path,
            name,
            schema=name,
            extra_toml="[modules.approvals]\n" if approvals else "",
            # A Switchboard validates its roster's ports on startup.
            port=41100 if name == "switchboard" else 0,
        ),
        *chains,
    )
    hours = (22, 7) if quiet_hours else (None, None)
    admin = await open_pool(booted.admin_url)
    try:
        await admin.execute(
            "UPDATE public.approvals_policy"
            " SET quiet_start_hour = $1, quiet_end_hour = $2, timezone = 'UTC' WHERE id = 1",
            *hours,
        )
    finally:
        await admin.close()
    return booted


async def _notify_fn(booted) -> Callable[..., Awaitable[dict[str, Any]]]:
    """The daemon's registered notify tool, called in process.

    In process because these tests pass ``priority`` (excluded from the MCP schema)
    and set the session and origin context variables the tool reads.
    """
    tool = await booted.daemon.mcp.get_tool("notify")
    return tool.fn


def _use_client(monkeypatch: pytest.MonkeyPatch, daemon: Any, client: Any) -> Any:
    monkeypatch.setattr(daemon, "switchboard_client", client)
    return client


class _FetchvalInterceptor:
    """The daemon's pool, except fetchval queries containing *needle* go to *handler*."""

    def __init__(self, pool: Any, needle: str, handler: Callable[..., Awaitable[Any]]) -> None:
        self._pool = pool
        self._needle = needle
        self._handler = handler

    async def fetchval(self, query: str, *args: Any, **kwargs: Any) -> Any:
        if self._needle in query:
            return await self._handler(query, *args)
        return await self._pool.fetchval(query, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pool, name)


def _intercept_fetchval(
    monkeypatch: pytest.MonkeyPatch,
    daemon: Any,
    needle: str,
    handler: Callable[..., Awaitable[Any]],
) -> None:
    """Answer the daemon's fetchval queries containing *needle* with *handler*.

    asyncpg pools have read-only methods, so the daemon gets a delegating pool.
    """
    monkeypatch.setattr(daemon.db, "pool", _FetchvalInterceptor(daemon.db.pool, needle, handler))


async def _ledger_rows(booted) -> list[Any]:
    return await booted.daemon.db.pool.fetch(
        "SELECT origin_butler, source, outcome, reason, notification_ref, metadata::text"
        " AS metadata FROM public.attention_ledger ORDER BY occurred_at, id"
    )


async def _ledger_row_of(booted, outcome: str) -> Any:
    """Return the first attention_ledger row whose outcome matches."""
    return next((row for row in await _ledger_rows(booted) if row["outcome"] == outcome), None)


async def _deferred_rows(booted) -> list[Any]:
    return await booted.daemon.db.pool.fetch(
        "SELECT butler_name, channel, message, priority, envelope, deliver_at"
        " FROM deferred_notifications ORDER BY deferred_at, id"
    )


async def _approval_rows(booted) -> int:
    """Rows the Approvals module persisted (pending actions plus standing rules)."""
    pool = booted.daemon.db.pool
    actions = await pool.fetchval("SELECT count(*) FROM pending_actions")
    rules = await pool.fetchval("SELECT count(*) FROM approval_rules")
    return actions + rules


def _make_mock_client() -> Any:
    mock_call_result = MagicMock()
    mock_call_result.is_error = False
    mock_call_result.data = {"status": "ok", "notification_id": "notif-123"}
    mock_call_result.content = [MagicMock(text='{"status":"ok"}')]

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    return mock_client


@contextmanager
def _configured_owner_default_recipient(daemon: Any) -> Iterator[None]:
    """Model a configured owner channel for default-recipient notify tests."""
    owner_contact = MagicMock(roles=["owner"])
    with (
        patch.object(
            daemon,
            "_resolve_default_notify_recipient",
            new=AsyncMock(return_value="123456789"),
        ),
        patch(
            "butlers.identity.resolve_contact_by_channel",
            new=AsyncMock(return_value=owner_contact),
        ),
    ):
        yield


@pytest.fixture
def _fixed_policy_quiet_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluate ledger policy holds at a stable in-window instant.

    ``notify`` imports ``policy_quiet_hours_deliver_at`` when it is invoked,
    so patch its defining module. The wrapper still delegates to the real
    policy helper, retaining its validation, end-exclusive interval, and
    delivery-anchor behavior while keeping these ledger-path tests independent
    of the host clock.
    """
    import butlers.core.approvals_policy as approvals_policy

    real_policy_quiet_hours_deliver_at = approvals_policy.policy_quiet_hours_deliver_at

    def _at_fixed_quiet_hour(policy: dict[str, Any] | None, *, now: datetime) -> datetime | None:
        return real_policy_quiet_hours_deliver_at(
            policy,
            now=now.replace(hour=23, minute=30, second=0, microsecond=0),
        )

    monkeypatch.setattr(
        approvals_policy,
        "policy_quiet_hours_deliver_at",
        _at_fixed_quiet_hour,
    )


def _make_result(*, is_error: bool, data: Any = None, content_text: str | None = None) -> Any:
    """Build a FastMCP-CallToolResult-shaped mock for switchboard_client.call_tool."""
    result = MagicMock()
    result.is_error = is_error
    result.data = data
    result.content = [MagicMock(text=content_text)] if content_text is not None else []
    return result


def _client_returning(result: Any) -> Any:
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=result)
    return mock_client


def _client_raising(exc: BaseException) -> Any:
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(side_effect=exc)
    return mock_client


@pytest.mark.usefixtures("_fixed_policy_quiet_clock")
class TestQuietHoursLedgerRecording:
    async def test_quiet_hours_defers_full_envelope_and_records_ledger(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        booted = await _butler(booted_daemon, tmp_path, quiet_hours=True)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())

        # The original call keeps recipient=None so the approvals-policy gate
        # applies after owner-default resolution.
        with (
            _configured_owner_default_recipient(daemon),
            patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Weekly report",
                _why="The owner needs the scheduled weekly report.",
                _evidence=[],
            )

        assert result["status"] == "deferred"
        assert result["notification_id"]
        assert result["reason"] == "policy_quiet_hours"
        client.call_tool.assert_not_awaited()

        deferred = await _deferred_rows(booted)
        assert len(deferred) == 1
        assert deferred[0]["butler_name"] == "general"
        assert deferred[0]["channel"] == "telegram"
        assert deferred[0]["message"] == "Weekly report"
        assert deferred[0]["envelope"] == {
            "schema_version": "notify.v1",
            "origin_butler": "general",
            "delivery": {
                "intent": "send",
                "channel": "telegram",
                "message": "Weekly report",
                "recipient": "123456789",
            },
            "decision_dossier": {
                "why": "The owner needs the scheduled weekly report.",
                "evidence": [],
                "blast_radius": None,
                "reversibility": None,
            },
        }

        ledger = await _ledger_rows(booted)
        assert len(ledger) == 1
        assert ledger[0]["origin_butler"] == "general"
        assert ledger[0]["source"] == "notify"
        assert ledger[0]["outcome"] == "deferred"
        assert ledger[0]["reason"] == "policy_quiet_hours"

    async def test_explicit_recipient_obeys_owner_quiet_hours_parking(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit owner recipient follows the same owner quiet window."""
        booted = await _butler(booted_daemon, tmp_path, quiet_hours=True, approvals=True)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())
        owner_contact = MagicMock(roles=["owner"])
        owner_resolver = AsyncMock(return_value=owner_contact)

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=owner_resolver,
            ),
            patch(
                "butlers.identity.resolve_owner_channel_via_definer",
                new=AsyncMock(return_value=(owner_contact, False)),
            ),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Direct check-in",
                recipient="explicit-owner-chat",
                _why="The owner explicitly selected this recipient.",
                _evidence=[],
            )

        assert result["status"] == "deferred"
        owner_resolver.assert_awaited_once_with(daemon.db.pool, "telegram", "explicit-owner-chat")
        deferred = await _deferred_rows(booted)
        assert len(deferred) == 1
        client.call_tool.assert_not_awaited()
        assert deferred[0]["envelope"]["delivery"]["recipient"] == "explicit-owner-chat"

    async def test_entity_target_obeys_owner_quiet_hours_parking(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit entity target cannot escape the owner quiet window."""
        booted = await _butler(booted_daemon, tmp_path, quiet_hours=True, approvals=True)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())
        entity_id = uuid.UUID("00000000-0000-0000-0000-000000000042")
        entity_resolver = AsyncMock(return_value="entity-owner-chat")
        owner_contact = MagicMock(roles=["owner"])
        owner_resolver = AsyncMock(return_value=owner_contact)

        with (
            patch.object(
                daemon,
                "_resolve_entity_channel_identifier",
                new=entity_resolver,
            ),
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=owner_resolver,
            ),
            patch(
                "butlers.identity.resolve_owner_channel_via_definer",
                new=AsyncMock(return_value=(owner_contact, False)),
            ),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Entity-specific check-in",
                entity_id=entity_id,
                _why="This target was selected by entity id.",
                _evidence=[],
            )

        assert result["status"] == "deferred"
        entity_resolver.assert_awaited_once_with(
            entity_id=entity_id,
            channel="telegram",
            msg_context=None,
        )
        owner_resolver.assert_awaited_once_with(daemon.db.pool, "telegram", "entity-owner-chat")
        deferred = await _deferred_rows(booted)
        assert len(deferred) == 1
        client.call_tool.assert_not_awaited()
        assert deferred[0]["envelope"]["delivery"]["recipient"] == "entity-owner-chat"

    async def test_reply_intent_skips_owner_default_quiet_hours_parking(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reply is not an eligible owner-default notification for parking."""
        booted = await _butler(booted_daemon, tmp_path, quiet_hours=True)
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, booted.daemon, _make_mock_client())

        result = await notify_fn(
            channel="telegram",
            message="Acknowledged.",
            intent="reply",
            request_context={
                "request_id": "01900000-0000-0000-0000-000000000000",
                "source_channel": "telegram",
                "source_endpoint_identity": "telegram:reply-target-chat",
                "source_sender_identity": "owner",
                "source_thread_identity": "telegram-thread-42",
            },
        )

        assert result["status"] == "ok"
        assert await _deferred_rows(booted) == []
        client.call_tool.assert_awaited_once()
        notify_request = client.call_tool.await_args.args[1]["notify_request"]
        assert notify_request["delivery"]["intent"] == "reply"
        assert notify_request["delivery"]["recipient"] == "reply-target-chat"

    async def test_non_owner_without_dossier_retries_without_persistence_or_ledger(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dossier error wins before quiet-hours or any approval side effect."""
        booted = await _butler(booted_daemon, tmp_path, quiet_hours=True, approvals=True)
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, booted.daemon, _make_mock_client())
        match_rules = AsyncMock(side_effect=AssertionError("rules must not be queried"))

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=MagicMock(roles=["contact"])),
            ),
            patch("butlers.modules.approvals.rules.match_rules", new=match_rules),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Weekly report",
                recipient="900800700",
            )

        assert result == {
            "status": "error",
            "error": {
                "code": "missing_required_dossier_field",
                "field": "why",
                "message": "why is required for a gated non-owner action; retry with _why.",
                "retryable": True,
            },
            "retryable": True,
        }
        match_rules.assert_not_awaited()
        client.call_tool.assert_not_awaited()
        assert await _ledger_rows(booted) == []
        assert await _approval_rows(booted) == 0

    async def test_context_bus_dnd_defers_until_signal_expiry_when_no_quiet_hours(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        booted = await _butler(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())

        dnd_signal = ContextEntry(
            signal_type="dnd",
            value=None,
            set_by_butler="general",
            set_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            confidence=1.0,
        )
        with (
            patch(
                "butlers.context_bus.get_active_context", new=AsyncMock(return_value=[dnd_signal])
            ),
            _configured_owner_default_recipient(daemon),
        ):
            result = await notify_fn(channel="telegram", message="Weekly report")

        assert result["status"] == "deferred"
        assert result["context_signal"] == "dnd"
        assert result["reason"] == "context_bus:dnd"
        assert result["deliver_at"] == dnd_signal.expires_at.isoformat()
        client.call_tool.assert_not_awaited()

        deferred = await _deferred_rows(booted)
        assert len(deferred) == 1
        assert deferred[0]["envelope"]["schema_version"] == "notify.v1"
        assert deferred[0]["deliver_at"] == dnd_signal.expires_at

        ledger = await _ledger_rows(booted)
        assert len(ledger) == 1
        assert ledger[0]["outcome"] == "deferred"
        assert ledger[0]["reason"] == "context_bus:dnd"

    async def test_policy_and_context_holds_use_the_later_delivery_anchor(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        booted = await _butler(booted_daemon, tmp_path, quiet_hours=True)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())
        dnd_signal = ContextEntry(
            signal_type="dnd",
            value=None,
            set_by_butler="general",
            set_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=2),
            confidence=1.0,
        )

        with (
            _configured_owner_default_recipient(daemon),
            patch(
                "butlers.context_bus.get_active_context", new=AsyncMock(return_value=[dnd_signal])
            ),
        ):
            result = await notify_fn(channel="telegram", message="Weekly report")

        assert result["status"] == "deferred"
        assert result["deliver_at"] == dnd_signal.expires_at.isoformat()
        assert result["reason"] == "policy_quiet_hours+context_bus:dnd"
        client.call_tool.assert_not_awaited()

    async def test_deferred_persistence_failure_returns_retryable_error_without_delivery(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        booted = await _butler(booted_daemon, tmp_path, quiet_hours=True)
        daemon = booted.daemon

        async def _unavailable(_query: str, *_args: Any) -> Any:
            raise RuntimeError("database unavailable")

        _intercept_fetchval(monkeypatch, daemon, "INSERT INTO deferred_notifications", _unavailable)
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())

        with (
            _configured_owner_default_recipient(daemon),
            patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
        ):
            result = await notify_fn(channel="telegram", message="Weekly report")

        assert result["status"] == "error"
        assert result["retryable"] is True
        assert result["error"]["code"] == "deferred_notification_persistence_failed"
        client.call_tool.assert_not_awaited()
        ledger = await _ledger_rows(booted)
        assert len(ledger) == 1
        assert ledger[0]["outcome"] == "failed"
        assert ledger[0]["reason"] == "deferred_persistence_error:RuntimeError"

    async def test_ledger_failure_after_queue_preserves_deferred_result(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Queue durability wins over best-effort ledger observability."""
        booted = await _butler(booted_daemon, tmp_path, quiet_hours=True)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())
        ledger_failure = AsyncMock(side_effect=RuntimeError("ledger unavailable"))

        with (
            _configured_owner_default_recipient(daemon),
            patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
            patch("butlers.core_tools._notifications.record_attention_event", new=ledger_failure),
        ):
            result = await notify_fn(channel="telegram", message="Weekly report")

        assert result["status"] == "deferred"
        ledger_failure.assert_awaited_once()
        client.call_tool.assert_not_awaited()
        assert len(await _deferred_rows(booted)) == 1

    async def test_context_lookup_failure_refuses_without_delivery(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        booted = await _butler(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())

        with (
            _configured_owner_default_recipient(daemon),
            patch(
                "butlers.context_bus.get_active_context",
                new=AsyncMock(side_effect=RuntimeError("context unavailable")),
            ),
        ):
            result = await notify_fn(channel="telegram", message="Weekly report")

        assert result["status"] == "error"
        assert result["error"]["code"] == "attention_context_unavailable"
        client.call_tool.assert_not_awaited()

    @pytest.mark.parametrize("owner_started", [False, True])
    async def test_model_priority_label_cannot_bypass_quiet_hours_and_context_bus(
        self,
        booted_daemon,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        owner_started: bool,
    ) -> None:
        booted = await _butler(booted_daemon, tmp_path, quiet_hours=True)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        client = _use_client(monkeypatch, daemon, _make_mock_client())

        dnd_signal = ContextEntry(
            signal_type="dnd",
            value=None,
            set_by_butler="general",
            set_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            confidence=1.0,
        )
        from butlers.core.attention_urgency import AttentionOrigin, _current_origin

        # Owner content authority is not a creation flag or persisted clause.
        origin_token = _current_origin.set(
            AttentionOrigin(entity_id=uuid.uuid4()) if owner_started else None
        )
        with (
            _configured_owner_default_recipient(daemon),
            patch(
                "butlers.context_bus.get_active_context", new=AsyncMock(return_value=[dnd_signal])
            ),
        ):
            try:
                result = await notify_fn(channel="telegram", message="Urgent!", priority="high")
            finally:
                _current_origin.reset(origin_token)

        assert result["status"] == "deferred"
        client.call_tool.assert_not_awaited()

        outcomes = [row["outcome"] for row in await _ledger_rows(booted)]
        assert "suppressed" not in outcomes
        assert "deferred" in outcomes

        event = json.loads((await _ledger_row_of(booted, "deferred"))["metadata"])
        assert event["attention_cause"] == "routine"
        assert event["downgrade"] == "unverified_urgency"
        assert "Urgent!" not in json.dumps(event)

    # Spec: REQ-core-fleet-events-007, REQ-core-fleet-events-008
    @pytest.mark.parametrize("publish_failure", [False, True])
    async def test_successful_delivery_records_delivered_ledger_row(
        self,
        booted_daemon,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        publish_failure: bool,
    ) -> None:
        booted = await _butler(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        _use_client(monkeypatch, daemon, _make_mock_client())

        publish = AsyncMock(
            side_effect=RuntimeError("publication unavailable") if publish_failure else None
        )
        with (
            patch("butlers.fleet_events.publish_fleet_event", new=publish),
            _configured_owner_default_recipient(daemon),
            patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
        ):
            result = await notify_fn(channel="telegram", message="All good")

        assert result["status"] == "ok"
        publish.assert_awaited_once_with(
            daemon.db.pool,
            "notification",
            {"butler": "general", "channel": "telegram", "intent": "send"},
        )

        ledger = await _ledger_rows(booted)
        assert len(ledger) == 1
        assert ledger[0]["outcome"] == "delivered"
        assert ledger[0]["notification_ref"] == "notif-123"


class TestNotifyFailurePathsRecordFailedLedgerRow:
    """Every terminal notify() dispatch failure stamps ``outcome="failed"`` (bu-zcos8).

    Before this fix, notify()'s delivery-failure returns (switchboard
    self-delivery failure/exception, and the proxied path's inner
    ``status="failed"``, MCP-level error, timeout, unreachable, and unexpected
    exception) returned an error to the caller without writing any attention
    ledger row — so a genuine outage read identically to a benign quiet-hours
    hold on the exact surface built to prove silence is chosen. This is the
    same failure class PR #3171 (bu-hmdqz.3) fixed for the process-boundary
    consumers, now closed for notify() itself. See core-notify spec
    §"Attention Ledger Recording at the notify() Boundary".

    Each test asserts (a) a ``failed`` ledger row with a machine-readable
    reason lands, and (b) the notify() return value keeps its pre-existing
    error shape (unchanged for callers).
    """

    async def _run_proxied(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: Any
    ) -> tuple[dict[str, Any], Any]:
        booted = await _butler(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        _use_client(monkeypatch, daemon, client)
        with (
            _configured_owner_default_recipient(daemon),
            patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
        ):
            result = await notify_fn(channel="telegram", message="Report")
        return result, booted

    async def test_proxied_inner_failed_records_failed_ledger_row(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client_returning(
            _make_result(
                is_error=False,
                data={
                    "status": "failed",
                    "error": "messenger rejected",
                    "retryable": False,
                    "notification_id": "notif-fail-1",
                },
            )
        )
        result, booted = await self._run_proxied(booted_daemon, tmp_path, monkeypatch, client)

        # Return shape unchanged for callers.
        assert result["status"] == "error"
        assert result["error"] == "messenger rejected"

        row = await _ledger_row_of(booted, "failed")
        assert row is not None, "expected a failed attention-ledger row"
        assert row["source"] == "notify"
        assert row["reason"] == "delivery_error:messenger rejected"
        assert row["notification_ref"] == "notif-fail-1"

    async def test_proxied_mcp_error_records_failed_ledger_row(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client_returning(_make_result(is_error=True, content_text="mcp boom"))
        result, booted = await self._run_proxied(booted_daemon, tmp_path, monkeypatch, client)

        assert result["status"] == "error"
        assert result["error"] == "mcp boom"

        row = await _ledger_row_of(booted, "failed")
        assert row is not None
        assert row["reason"] == "delivery_error:mcp boom"

    async def test_proxied_timeout_records_failed_ledger_row(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, booted = await self._run_proxied(
            booted_daemon, tmp_path, monkeypatch, _client_raising(TimeoutError())
        )

        assert result["status"] == "error"
        assert result["retryable"] is True

        row = await _ledger_row_of(booted, "failed")
        assert row is not None
        assert "timeout" in row["reason"]

    async def test_proxied_unreachable_records_failed_ledger_row(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, booted = await self._run_proxied(
            booted_daemon, tmp_path, monkeypatch, _client_raising(ConnectionError("down"))
        )

        assert result["status"] == "error"
        assert result["retryable"] is True

        row = await _ledger_row_of(booted, "failed")
        assert row is not None
        assert "unreachable" in row["reason"]

    async def test_proxied_unexpected_exception_records_failed_ledger_row(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, booted = await self._run_proxied(
            booted_daemon, tmp_path, monkeypatch, _client_raising(ValueError("weird"))
        )

        assert result["status"] == "error"
        assert result["retryable"] is False

        row = await _ledger_row_of(booted, "failed")
        assert row is not None
        assert row["reason"] == "unexpected_error:ValueError"

    async def test_switchboard_self_delivery_failed_records_failed_ledger_row(
        self, booted_daemon, tmp_path: Path
    ) -> None:
        booted = await _butler(booted_daemon, tmp_path, name="switchboard")
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        assert daemon.switchboard_client is None  # the switchboard self-delivery branch

        deliver_mock = AsyncMock(
            return_value={
                "status": "failed",
                "error": "no route",
                "notification_id": "sb-fail-1",
            }
        )
        with (
            _configured_owner_default_recipient(daemon),
            patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                new=deliver_mock,
            ),
        ):
            result = await notify_fn(channel="telegram", message="Report")

        assert result["status"] == "error"
        assert result["error"] == "no route"

        row = await _ledger_row_of(booted, "failed")
        assert row is not None
        assert row["origin_butler"] == "switchboard"
        assert row["reason"] == "delivery_error:no route"
        assert row["notification_ref"] == "sb-fail-1"

    async def test_switchboard_self_delivery_exception_records_failed_ledger_row(
        self, booted_daemon, tmp_path: Path
    ) -> None:
        booted = await _butler(booted_daemon, tmp_path, name="switchboard")
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        assert daemon.switchboard_client is None

        deliver_mock = AsyncMock(side_effect=RuntimeError("kaboom"))
        with (
            _configured_owner_default_recipient(daemon),
            patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                new=deliver_mock,
            ),
        ):
            result = await notify_fn(channel="telegram", message="Report")

        assert result["status"] == "error"
        assert "Direct delivery failed" in result["error"]

        row = await _ledger_row_of(booted, "failed")
        assert row is not None
        assert row["origin_butler"] == "switchboard"
        assert row["reason"] == "unexpected_error:RuntimeError"


class TestRuntimeSessionCorrelation:
    """Every notify-boundary ledger row names the session that made the call.

    Without it the ledger is only queryable by butler and time window, so a
    caller that spawned a session and wants to know what became of the notice
    it asked for has to infer delivery from adjacent state (bu-358jk). The
    stamp is what makes that inference unnecessary.
    """

    async def test_delivered_row_carries_the_runtime_session_id(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        booted = await _butler(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        _use_client(monkeypatch, daemon, _make_mock_client())

        token = set_current_runtime_session_id("sess-runtime-1")
        try:
            with (
                _configured_owner_default_recipient(daemon),
                patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
            ):
                result = await notify_fn(channel="telegram", message="All good")
        finally:
            reset_current_runtime_session_id(token)

        assert result["status"] == "ok"

        ledger = await _ledger_rows(booted)
        assert ledger, "no attention_ledger row was written; the assertion below is vacuous"
        metadata = json.loads(ledger[0]["metadata"])
        assert metadata[ATTENTION_LEDGER_SESSION_KEY] == "sess-runtime-1"

    async def test_failed_row_carries_the_runtime_session_id(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure has to be as correlatable as a success.

        Otherwise a caller finds no row for its session and cannot tell "the
        notice failed" from "the session never sent one".
        """
        booted = await _butler(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)

        failing_client = AsyncMock()
        failing_client.call_tool = AsyncMock(side_effect=ConnectionError("switchboard gone"))
        _use_client(monkeypatch, daemon, failing_client)

        token = set_current_runtime_session_id("sess-runtime-2")
        try:
            with (
                _configured_owner_default_recipient(daemon),
                patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
            ):
                result = await notify_fn(channel="telegram", message="All good")
        finally:
            reset_current_runtime_session_id(token)

        assert result["status"] == "error"

        rows = [row for row in await _ledger_rows(booted) if row["outcome"] == "failed"]
        assert rows, "no failed attention_ledger row was written; the assertion below is vacuous"
        metadata = json.loads(rows[0]["metadata"])
        assert metadata[ATTENTION_LEDGER_SESSION_KEY] == "sess-runtime-2"

    async def test_no_runtime_session_leaves_the_row_unstamped(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A notify() outside a spawned session has no session to name."""
        booted = await _butler(booted_daemon, tmp_path)
        daemon = booted.daemon
        notify_fn = await _notify_fn(booted)
        _use_client(monkeypatch, daemon, _make_mock_client())

        token = set_current_runtime_session_id(None)
        try:
            with (
                _configured_owner_default_recipient(daemon),
                patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
            ):
                result = await notify_fn(channel="telegram", message="All good")
        finally:
            reset_current_runtime_session_id(token)

        assert result["status"] == "ok"

        ledger = await _ledger_rows(booted)
        assert ledger, "no attention_ledger row was written; the assertion below is vacuous"
        metadata_json = ledger[0]["metadata"]
        metadata = json.loads(metadata_json) if metadata_json else {}
        assert ATTENTION_LEDGER_SESSION_KEY not in metadata


@pytest.mark.parametrize("revoked", [False, True])
async def test_verified_cause_is_rechecked_before_actual_notify_dispatch(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revoked: bool
) -> None:
    """Consumer proof only: real current SQL/provenance is owned by PG cases."""
    from butlers.core.attention_urgency import AttentionOrigin, _current_origin

    booted = await _butler(booted_daemon, tmp_path, quiet_hours=True)
    daemon = booted.daemon
    client = _use_client(monkeypatch, daemon, _make_mock_client())
    cause_ref = str(uuid.uuid4())
    reads = 0

    async def evaluate(_query: str, *_args: Any) -> Any:
        nonlocal reads
        reads += 1
        return (
            {"cause": "routine"}
            if revoked and reads > 2
            else {
                "cause": "owner_clause",
                "ref": cause_ref,
            }
        )

    _intercept_fetchval(monkeypatch, daemon, "dashboard_attention.evaluate", evaluate)
    notify_fn = await _notify_fn(booted)
    token = _current_origin.set(AttentionOrigin(entity_id=uuid.uuid4()))
    try:
        with _configured_owner_default_recipient(daemon):
            result = await notify_fn(channel="telegram", message="PRIVATE_CAUSE_BODY_CANARY")
    finally:
        _current_origin.reset(token)
    if revoked:
        assert result["error"]["code"] == "attention_cause_changed"
        client.call_tool.assert_not_awaited()
        event = await _ledger_row_of(booted, "failed")
    else:
        assert result["status"] == "ok"
        client.call_tool.assert_awaited_once()
        event = await _ledger_row_of(booted, "delivered")
    assert reads >= 3
    metadata = json.loads(event["metadata"])
    assert metadata["attention_cause"] == "owner_clause" and metadata["cause_ref"] == cause_ref
    assert "PRIVATE_CAUSE_BODY_CANARY" not in json.dumps(metadata)
