"""Tests for the attention-ledger wiring at the notify() boundary (bu-qvnce.8).

Covers the governance layer added on top of the existing approvals_policy
quiet-hours gate:
  - every suppression decision (quiet hours, context bus) is recorded to
    ``public.attention_ledger`` with a machine-readable reason instead of
    vanishing without a trace
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
    - ``approvals_policy`` queries → *approvals_policy_row* (the quiet-hours gate).
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


def _ledger_insert_calls(mock_pool: AsyncMock) -> list[tuple[Any, ...]]:
    """Return fetchval call args whose query targets public.attention_ledger."""
    return [
        call.args
        for call in mock_pool.fetchval.await_args_list
        if call.args and "attention_ledger" in call.args[0]
    ]


_ALL_DAY_QUIET_POLICY = {"quiet_start_hour": 0, "quiet_end_hour": 23, "timezone": "UTC"}
_NO_QUIET_POLICY = {"quiet_start_hour": None, "quiet_end_hour": None, "timezone": "UTC"}


class TestQuietHoursLedgerRecording:
    async def test_quiet_hours_suppresses_and_records_ledger(self, butler_dir: Path) -> None:
        mock_pool = _make_mock_pool(approvals_policy_row=_ALL_DAY_QUIET_POLICY)
        patches = _patch_infra(mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = _make_mock_client()

        with patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])):
            result = await notify_fn(channel="telegram", message="Weekly report")

        assert result["status"] == "suppressed_quiet_hours"
        daemon.switchboard_client.call_tool.assert_not_awaited()

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
        assert outcome == "suppressed"
        assert reason == "quiet_hours"

    async def test_context_bus_dnd_suppresses_when_no_quiet_hours(self, butler_dir: Path) -> None:
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
        with patch(
            "butlers.context_bus.get_active_context", new=AsyncMock(return_value=[dnd_signal])
        ):
            result = await notify_fn(channel="telegram", message="Weekly report")

        assert result["status"] == "suppressed_context_bus"
        assert result["context_signal"] == "dnd"
        daemon.switchboard_client.call_tool.assert_not_awaited()

        ledger_calls = _ledger_insert_calls(mock_pool)
        assert len(ledger_calls) == 1
        outcome = ledger_calls[0][8]
        reason = ledger_calls[0][9]
        assert outcome == "suppressed"
        assert reason == "context_bus:dnd"

    async def test_high_priority_bypasses_quiet_hours_and_context_bus(
        self, butler_dir: Path
    ) -> None:
        mock_pool = _make_mock_pool(approvals_policy_row=_ALL_DAY_QUIET_POLICY)
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
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value="123456789"),
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
            patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value="123456789"),
            ),
        ):
            result = await notify_fn(channel="telegram", message="All good")

        assert result["status"] == "ok"

        ledger_calls = _ledger_insert_calls(mock_pool)
        assert len(ledger_calls) == 1
        outcome = ledger_calls[0][8]
        notification_ref = ledger_calls[0][10]
        assert outcome == "delivered"
        assert notification_ref == "notif-123"
