"""Tests for the attention-ledger wiring at the notify() boundary (bu-qvnce.8).

Covers the governance layer added on top of the existing approvals_policy
quiet-hours gate:
  - routine implicit-owner decisions held by quiet hours or context bus are
    durably deferred, with a full ``notify.v1`` envelope and a ledger row,
    instead of vanishing without a trace
  - context-bus dnd/sleeping signals are consulted deterministically,
    alongside (not instead of) the existing hour-based quiet-hours policy
  - priority="high" always fails open — bypasses both quiet hours and the
    context bus, consistent with existing notify() semantics
  - a successfully delivered notification is recorded as outcome="delivered"

These tests boot a real ``ButlerDaemon`` with every I/O boundary mocked
(same harness pattern as ``tests/daemon/test_notify_entity_id.py``) so the
actual registered ``notify`` MCP tool closure is exercised end-to-end,
rather than re-testing the pure helpers in isolation (see
``tests/core/test_attention_ledger.py`` for those).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.context_bus import ContextEntry
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


@pytest.fixture
def butler_dir(tmp_path: Path) -> Path:
    """Create a minimal butler directory for testing."""
    butler_path = tmp_path / "test-butler"
    butler_path.mkdir()
    (butler_path / "butler.toml").write_text(
        """
[butler]
name = "test-butler"
port = 9100
description = "Test butler"

[butler.db]
name = "butlers"
schema = "test_butler"

[[butler.schedule]]
name = "daily-check"
cron = "0 9 * * *"
prompt = "Do the daily check"
"""
    )
    (butler_path / "MANIFESTO.md").write_text("# Test Butler")
    (butler_path / "CLAUDE.md").write_text("Test butler instructions.")
    return butler_path


def _make_runtime_config_row(butler_name: str = "test-butler") -> dict:
    return {
        "butler_name": butler_name,
        "core_groups": None,
        "max_concurrent": 3,
        "max_queued": 10,
        "seeded_at": None,
        "updated_at": None,
    }


def _make_fetchrow_side_effect(*, approvals_policy_row: dict | None):
    """Build an async side_effect for pool.fetchrow.

    - ``runtime_config`` queries → a valid runtime_config row (daemon boot).
    - ``contact_info`` + ``is_primary`` queries → is_primary=True (email guard).
    - ``approvals_policy`` queries → *approvals_policy_row* (the owner-default gate).
    - ``delivery_preferences`` queries → None (per-butler defer path not configured;
      this test suite targets the owner-level approvals_policy + context-bus gate).
    - everything else → None.
    """

    async def _fetchrow(query: str, *args, **kwargs):
        if "runtime_config" in query:
            return _make_runtime_config_row()
        if "contact_info" in query and "is_primary" in query:
            return {"is_primary": True}
        if "approvals_policy" in query:
            return approvals_policy_row
        return None

    return _fetchrow


def _patch_infra(mock_pool: Any) -> dict[str, Any]:
    """Patch infrastructure dependencies for daemon tests (mirrors test_notify_entity_id.py)."""
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
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    mock_credential_store = AsyncMock()
    mock_credential_store.resolve = AsyncMock(return_value=None)

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
        "configure_logging": patch("butlers.core.logging.configure_logging"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.lifecycle.FastMCP"),
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "create_audit_pool": patch.object(
            ButlerDaemon, "_create_audit_pool", new_callable=AsyncMock, return_value=None
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "build_credential_store": patch.object(
            ButlerDaemon,
            "_build_credential_store",
            new_callable=AsyncMock,
            return_value=mock_credential_store,
        ),
        "get_adapter": patch("butlers.lifecycle.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
    }


async def _start_daemon_with_notify(
    butler_dir: Path, patches: dict[str, Any]
) -> tuple[ButlerDaemon, Any]:
    notify_fn = None
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **_decorator_kwargs):
        def decorator(fn):
            nonlocal notify_fn
            if fn.__name__ == "notify":
                notify_fn = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["configure_logging"],
        patches["sync_schedules"],
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
        patches["Spawner"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
        patches["build_credential_store"],
        patches["get_adapter"],
        patches["shutil_which"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()
        return daemon, notify_fn


def _make_mock_pool(*, approvals_policy_row: dict | None) -> AsyncMock:
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_pool = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_pool.fetchval = AsyncMock(return_value=str(uuid.uuid4()))
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(
        side_effect=_make_fetchrow_side_effect(approvals_policy_row=approvals_policy_row)
    )
    mock_pool.fetch = AsyncMock(return_value=[])
    return mock_pool


def _make_mock_client() -> Any:
    mock_call_result = MagicMock()
    mock_call_result.is_error = False
    mock_call_result.data = {"status": "ok", "notification_id": "notif-123"}
    mock_call_result.content = [MagicMock(text='{"status":"ok"}')]

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    return mock_client


@contextmanager
def _configured_owner_default_recipient(daemon: ButlerDaemon):
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


def _ledger_insert_calls(mock_pool: AsyncMock) -> list[tuple[Any, ...]]:
    """Return fetchval call args whose query targets public.attention_ledger."""
    return [
        call.args
        for call in mock_pool.fetchval.await_args_list
        if call.args and "attention_ledger" in call.args[0]
    ]


def _deferred_notification_insert_calls(mock_pool: AsyncMock) -> list[tuple[Any, ...]]:
    """Return fetchval call args whose query persists deferred notifications."""
    return [
        call.args
        for call in mock_pool.fetchval.await_args_list
        if call.args and "INSERT INTO deferred_notifications" in call.args[0]
    ]


_IN_WINDOW_QUIET_POLICY = {"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "UTC"}
_NO_QUIET_POLICY = {"quiet_start_hour": None, "quiet_end_hour": None, "timezone": "UTC"}


def _ledger_row_of(mock_pool: AsyncMock, outcome: str) -> tuple[Any, ...] | None:
    """Return the first attention_ledger INSERT arg tuple whose outcome matches."""
    for args in _ledger_insert_calls(mock_pool):
        if len(args) > 8 and args[8] == outcome:
            return args
    return None


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
        self, butler_dir: Path
    ) -> None:
        mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()

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
        daemon.switchboard_client.call_tool.assert_not_awaited()

        deferred_calls = _deferred_notification_insert_calls(mock_pool)
        assert len(deferred_calls) == 1
        (_, deferred_butler, deferred_channel, deferred_message, _priority, envelope, *_rest) = (
            deferred_calls[0]
        )
        assert deferred_butler == "test-butler"
        assert deferred_channel == "telegram"
        assert deferred_message == "Weekly report"
        assert envelope == {
            "schema_version": "notify.v1",
            "origin_butler": "test-butler",
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

        ledger_calls = _ledger_insert_calls(mock_pool)
        assert len(ledger_calls) == 1
        # INSERT columns: origin_butler, source, channel, intent, priority_label,
        # priority_score, dedup_key, outcome, reason, notification_ref, metadata
        (
            _,
            origin_butler,
            source,
            _channel,
            _intent,
            _plabel,
            _pscore,
            _dk,
            outcome,
            reason,
            _ref,
            _meta,
        ) = ledger_calls[0]
        assert origin_butler == "test-butler"
        assert source == "notify"
        assert outcome == "deferred"
        assert reason == "policy_quiet_hours"

    async def test_explicit_recipient_skips_owner_default_quiet_hours_parking(
        self, butler_dir: Path
    ) -> None:
        """An explicit owner recipient must deliver despite an active owner quiet window."""
        import butlers.core.approvals_hooks as approval_hooks
        from butlers.modules.approvals.email_guard import check_recipient

        mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()
        owner_resolver = AsyncMock(return_value=MagicMock(roles=["owner"]))

        with (
            patch.object(approval_hooks, "_recipient_guard_hook", new=check_recipient),
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=owner_resolver,
            ),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Direct check-in",
                recipient="explicit-owner-chat",
                _why="The owner explicitly selected this recipient.",
                _evidence=[],
            )

        assert result["status"] == "ok"
        owner_resolver.assert_awaited_once_with(mock_pool, "telegram", "explicit-owner-chat")
        assert _deferred_notification_insert_calls(mock_pool) == []
        daemon.switchboard_client.call_tool.assert_awaited_once()
        notify_request = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"]
        assert notify_request["delivery"]["recipient"] == "explicit-owner-chat"

    async def test_entity_target_skips_owner_default_quiet_hours_parking(
        self, butler_dir: Path
    ) -> None:
        """An explicit entity target must not be treated as an implicit owner default."""
        import butlers.core.approvals_hooks as approval_hooks
        from butlers.modules.approvals.email_guard import check_recipient

        mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()
        entity_id = uuid.UUID("00000000-0000-0000-0000-000000000042")
        entity_resolver = AsyncMock(return_value="entity-owner-chat")
        owner_resolver = AsyncMock(return_value=MagicMock(roles=["owner"]))

        with (
            patch.object(approval_hooks, "_recipient_guard_hook", new=check_recipient),
            patch.object(
                daemon,
                "_resolve_entity_channel_identifier",
                new=entity_resolver,
            ),
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=owner_resolver,
            ),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Entity-specific check-in",
                entity_id=entity_id,
                _why="This target was selected by entity id.",
                _evidence=[],
            )

        assert result["status"] == "ok"
        entity_resolver.assert_awaited_once_with(
            entity_id=entity_id,
            channel="telegram",
            msg_context=None,
        )
        owner_resolver.assert_awaited_once_with(mock_pool, "telegram", "entity-owner-chat")
        assert _deferred_notification_insert_calls(mock_pool) == []
        daemon.switchboard_client.call_tool.assert_awaited_once()
        notify_request = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"]
        assert notify_request["delivery"]["recipient"] == "entity-owner-chat"

    async def test_reply_intent_skips_owner_default_quiet_hours_parking(
        self, butler_dir: Path
    ) -> None:
        """A reply is not an eligible owner-default notification for parking."""
        mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()

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
        assert _deferred_notification_insert_calls(mock_pool) == []
        daemon.switchboard_client.call_tool.assert_awaited_once()
        notify_request = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"]
        assert notify_request["delivery"]["intent"] == "reply"
        assert notify_request["delivery"]["recipient"] == "reply-target-chat"

    async def test_non_owner_without_dossier_retries_without_persistence_or_ledger(
        self, butler_dir: Path
    ) -> None:
        """A dossier error wins before quiet-hours or any approval side effect."""
        import butlers.core.approvals_hooks as approval_hooks
        from butlers.modules.approvals.email_guard import check_recipient

        mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()
        match_rules = AsyncMock(side_effect=AssertionError("rules must not be queried"))

        with (
            patch.object(approval_hooks, "_recipient_guard_hook", new=check_recipient),
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
        daemon.switchboard_client.call_tool.assert_not_awaited()
        assert _ledger_insert_calls(mock_pool) == []
        approval_persistence = [
            call
            for call in mock_pool.execute.await_args_list
            if "approval_rules" in call.args[0] or "pending_actions" in call.args[0]
        ]
        assert approval_persistence == []

    async def test_context_bus_dnd_defers_until_signal_expiry_when_no_quiet_hours(
        self, butler_dir: Path
    ) -> None:
        mock_pool = _make_mock_pool(approvals_policy_row=_NO_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()

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
        daemon.switchboard_client.call_tool.assert_not_awaited()

        deferred_calls = [
            call.args
            for call in mock_pool.fetchval.await_args_list
            if call.args and "INSERT INTO deferred_notifications" in call.args[0]
        ]
        assert len(deferred_calls) == 1
        assert deferred_calls[0][5]["schema_version"] == "notify.v1"
        assert deferred_calls[0][6] == dnd_signal.expires_at

        ledger_calls = _ledger_insert_calls(mock_pool)
        assert len(ledger_calls) == 1
        outcome = ledger_calls[0][8]
        reason = ledger_calls[0][9]
        assert outcome == "deferred"
        assert reason == "context_bus:dnd"

    async def test_policy_and_context_holds_use_the_later_delivery_anchor(
        self, butler_dir: Path
    ) -> None:
        mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()
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
        daemon.switchboard_client.call_tool.assert_not_awaited()

    async def test_deferred_persistence_failure_returns_retryable_error_without_delivery(
        self, butler_dir: Path
    ) -> None:
        mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)

        async def _fetchval(query: str, *_args: Any, **_kwargs: Any) -> str:
            if "INSERT INTO deferred_notifications" in query:
                raise RuntimeError("database unavailable")
            return str(uuid.uuid4())

        mock_pool.fetchval = AsyncMock(side_effect=_fetchval)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()

        with (
            _configured_owner_default_recipient(daemon),
            patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
        ):
            result = await notify_fn(channel="telegram", message="Weekly report")

        assert result["status"] == "error"
        assert result["retryable"] is True
        assert result["error"]["code"] == "deferred_notification_persistence_failed"
        daemon.switchboard_client.call_tool.assert_not_awaited()
        ledger_calls = _ledger_insert_calls(mock_pool)
        assert len(ledger_calls) == 1
        assert ledger_calls[0][8] == "failed"
        assert ledger_calls[0][9] == "deferred_persistence_error:RuntimeError"

    async def test_ledger_failure_after_queue_preserves_deferred_result(
        self, butler_dir: Path
    ) -> None:
        """Queue durability wins over best-effort ledger observability."""
        mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()
        ledger_failure = AsyncMock(side_effect=RuntimeError("ledger unavailable"))

        with (
            _configured_owner_default_recipient(daemon),
            patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
            patch("butlers.core_tools._notifications.record_attention_event", new=ledger_failure),
        ):
            result = await notify_fn(channel="telegram", message="Weekly report")

        assert result["status"] == "deferred"
        ledger_failure.assert_awaited_once()
        daemon.switchboard_client.call_tool.assert_not_awaited()
        assert (
            len(
                [
                    call
                    for call in mock_pool.fetchval.await_args_list
                    if "INSERT INTO deferred_notifications" in call.args[0]
                ]
            )
            == 1
        )

    async def test_context_lookup_failure_fails_open(self, butler_dir: Path) -> None:
        mock_pool = _make_mock_pool(approvals_policy_row=_NO_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()

        with (
            _configured_owner_default_recipient(daemon),
            patch(
                "butlers.context_bus.get_active_context",
                new=AsyncMock(side_effect=RuntimeError("context unavailable")),
            ),
        ):
            result = await notify_fn(channel="telegram", message="Weekly report")

        assert result["status"] == "ok"
        daemon.switchboard_client.call_tool.assert_awaited_once()

    async def test_high_priority_bypasses_quiet_hours_and_context_bus(
        self, butler_dir: Path
    ) -> None:
        mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()

        dnd_signal = ContextEntry(
            signal_type="dnd",
            value=None,
            set_by_butler="general",
            set_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            confidence=1.0,
        )
        with (
            _configured_owner_default_recipient(daemon),
            patch(
                "butlers.context_bus.get_active_context", new=AsyncMock(return_value=[dnd_signal])
            ),
        ):
            result = await notify_fn(channel="telegram", message="Urgent!", priority="high")

        assert result["status"] == "ok"
        daemon.switchboard_client.call_tool.assert_awaited_once()

        ledger_calls = _ledger_insert_calls(mock_pool)
        outcomes = [c[8] for c in ledger_calls]
        assert "suppressed" not in outcomes
        assert "delivered" in outcomes

    async def test_successful_delivery_records_delivered_ledger_row(self, butler_dir: Path) -> None:
        mock_pool = _make_mock_pool(approvals_policy_row=_NO_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()

        with (
            _configured_owner_default_recipient(daemon),
            patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
        ):
            result = await notify_fn(channel="telegram", message="All good")

        assert result["status"] == "ok"

        ledger_calls = _ledger_insert_calls(mock_pool)
        assert len(ledger_calls) == 1
        outcome = ledger_calls[0][8]
        notification_ref = ledger_calls[0][10]
        assert outcome == "delivered"
        assert notification_ref == "notif-123"


@pytest.fixture
def switchboard_butler_dir(tmp_path: Path) -> Path:
    """A butler dir named 'switchboard' so notify() takes the self-delivery branch."""
    butler_path = tmp_path / "switchboard"
    butler_path.mkdir()
    (butler_path / "butler.toml").write_text(
        """
[butler]
name = "switchboard"
port = 9110
description = "Switchboard butler"

[butler.db]
name = "butlers"
schema = "switchboard"

[[butler.schedule]]
name = "daily-check"
cron = "0 9 * * *"
prompt = "Do the daily check"
"""
    )
    (butler_path / "MANIFESTO.md").write_text("# Switchboard Butler")
    (butler_path / "CLAUDE.md").write_text("Switchboard butler instructions.")
    return butler_path


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

    async def _run_proxied(self, butler_dir: Path, client: Any) -> tuple[dict[str, Any], AsyncMock]:
        mock_pool = _make_mock_pool(approvals_policy_row=_NO_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = client
        with (
            _configured_owner_default_recipient(daemon),
            patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
        ):
            result = await notify_fn(channel="telegram", message="Report")
        return result, mock_pool

    async def test_proxied_inner_failed_records_failed_ledger_row(self, butler_dir: Path) -> None:
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
        result, mock_pool = await self._run_proxied(butler_dir, client)

        # Return shape unchanged for callers.
        assert result["status"] == "error"
        assert result["error"] == "messenger rejected"

        row = _ledger_row_of(mock_pool, "failed")
        assert row is not None, "expected a failed attention-ledger row"
        assert row[2] == "notify"  # source
        assert row[9] == "delivery_error:messenger rejected"  # reason
        assert row[10] == "notif-fail-1"  # notification_ref

    async def test_proxied_mcp_error_records_failed_ledger_row(self, butler_dir: Path) -> None:
        client = _client_returning(_make_result(is_error=True, content_text="mcp boom"))
        result, mock_pool = await self._run_proxied(butler_dir, client)

        assert result["status"] == "error"
        assert result["error"] == "mcp boom"

        row = _ledger_row_of(mock_pool, "failed")
        assert row is not None
        assert row[9] == "delivery_error:mcp boom"

    async def test_proxied_timeout_records_failed_ledger_row(self, butler_dir: Path) -> None:
        result, mock_pool = await self._run_proxied(butler_dir, _client_raising(TimeoutError()))

        assert result["status"] == "error"
        assert result["retryable"] is True

        row = _ledger_row_of(mock_pool, "failed")
        assert row is not None
        assert "timeout" in row[9]  # reason

    async def test_proxied_unreachable_records_failed_ledger_row(self, butler_dir: Path) -> None:
        result, mock_pool = await self._run_proxied(
            butler_dir, _client_raising(ConnectionError("down"))
        )

        assert result["status"] == "error"
        assert result["retryable"] is True

        row = _ledger_row_of(mock_pool, "failed")
        assert row is not None
        assert "unreachable" in row[9]

    async def test_proxied_unexpected_exception_records_failed_ledger_row(
        self, butler_dir: Path
    ) -> None:
        result, mock_pool = await self._run_proxied(
            butler_dir, _client_raising(ValueError("weird"))
        )

        assert result["status"] == "error"
        assert result["retryable"] is False

        row = _ledger_row_of(mock_pool, "failed")
        assert row is not None
        assert row[9] == "unexpected_error:ValueError"

    async def test_switchboard_self_delivery_failed_records_failed_ledger_row(
        self, switchboard_butler_dir: Path
    ) -> None:
        mock_pool = _make_mock_pool(approvals_policy_row=_NO_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(switchboard_butler_dir, patches)
        daemon.switchboard_client = None  # forces the switchboard self-delivery branch

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

        row = _ledger_row_of(mock_pool, "failed")
        assert row is not None
        assert row[1] == "switchboard"  # origin_butler
        assert row[9] == "delivery_error:no route"
        assert row[10] == "sb-fail-1"

    async def test_switchboard_self_delivery_exception_records_failed_ledger_row(
        self, switchboard_butler_dir: Path
    ) -> None:
        mock_pool = _make_mock_pool(approvals_policy_row=_NO_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(switchboard_butler_dir, patches)
        daemon.switchboard_client = None

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

        row = _ledger_row_of(mock_pool, "failed")
        assert row is not None
        assert row[1] == "switchboard"
        assert row[9] == "unexpected_error:RuntimeError"
