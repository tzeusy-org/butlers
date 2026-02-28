"""Tests for notify() contact_id parameter and contact-based resolution.

Covers tasks 7.1-7.4 from the contacts-identity-model spec:
  7.1 - notify() accepts contact_id parameter
  7.2 - contact_id resolves to channel identifier (primary preferred)
  7.3 - missing identifier parks action and notifies owner
  7.4 - neither param defaults to owner resolution
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
name = "butler_test"

[[butler.schedule]]
name = "daily-check"
cron = "0 9 * * *"
prompt = "Do the daily check"
"""
    )
    (butler_path / "MANIFESTO.md").write_text("# Test Butler")
    (butler_path / "CLAUDE.md").write_text("Test butler instructions.")
    return butler_path


def _patch_infra(mock_pool: Any = None) -> dict[str, Any]:
    """Patch infrastructure dependencies for daemon tests."""
    if mock_pool is None:
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=None)
        mock_pool.execute = AsyncMock()

    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butler_test"

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.daemon.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "validate_core_credentials": patch(
            "butlers.daemon.validate_core_credentials_async",
            new_callable=AsyncMock,
        ),
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "configure_logging": patch("butlers.core.logging.configure_logging"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
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
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
    }


async def _start_daemon_with_notify(
    butler_dir: Path, patches: dict[str, Any]
) -> tuple[ButlerDaemon, Any]:
    """Start daemon and extract the notify function reference."""
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
        patches["validate_core_credentials"],
        patches["init_telemetry"],
        patches["configure_logging"],
        patches["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp),
        patches["Spawner"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
        patches["get_adapter"],
        patches["shutil_which"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()
        return daemon, notify_fn


def _make_mock_client(*, is_error: bool = False) -> Any:
    """Create a mock switchboard client."""
    mock_call_result = MagicMock()
    mock_call_result.is_error = is_error
    mock_call_result.data = {"status": "sent"}
    mock_call_result.content = [MagicMock(text='{"status":"sent"}')]

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    return mock_client


@pytest.mark.asyncio
class TestNotifyContactIdParameter:
    """Task 7.1 — notify() accepts contact_id parameter."""

    async def test_notify_accepts_contact_id_none(self, butler_dir: Path) -> None:
        """notify() should accept contact_id=None without error."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        with patch.object(
            daemon,
            "_resolve_default_notify_recipient",
            new=AsyncMock(return_value="user@example.com"),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello",
                contact_id=None,
            )

        assert result["status"] == "ok"

    async def test_notify_accepts_contact_id_as_uuid(self, butler_dir: Path) -> None:
        """notify() should accept a valid UUID contact_id and attempt resolution."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        with patch.object(
            daemon,
            "_resolve_contact_channel_identifier",
            new=AsyncMock(return_value="user@example.com"),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello contact",
                contact_id=contact_id,
            )

        assert result["status"] == "ok"

    async def test_notify_contact_id_uses_resolver(self, butler_dir: Path) -> None:
        """notify() should call _resolve_contact_channel_identifier when contact_id provided."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        mock_resolver = AsyncMock(return_value="resolved@example.com")
        with patch.object(daemon, "_resolve_contact_channel_identifier", new=mock_resolver):
            await notify_fn(
                channel="email",
                message="Hello",
                contact_id=contact_id,
            )

        mock_resolver.assert_awaited_once_with(contact_id=contact_id, channel="email")


@pytest.mark.asyncio
class TestNotifyContactIdResolution:
    """Task 7.2 — contact_id resolves to channel identifier, primary preferred."""

    async def test_contact_id_resolved_recipient_used_in_delivery(self, butler_dir: Path) -> None:
        """When contact_id resolves, the resolved identifier should be in the delivery."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000010")
        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        with patch.object(
            daemon,
            "_resolve_contact_channel_identifier",
            new=AsyncMock(return_value="contact@example.com"),
        ):
            result = await notify_fn(
                channel="email",
                message="Test message",
                contact_id=contact_id,
            )

        assert result["status"] == "ok"
        call_args = mock_client.call_tool.await_args
        delivery = call_args.args[1]["notify_request"]["delivery"]
        assert delivery["recipient"] == "contact@example.com"

    async def test_contact_id_resolved_telegram_chat_id(self, butler_dir: Path) -> None:
        """contact_id should resolve telegram chat_id for telegram channel."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        with patch.object(
            daemon,
            "_resolve_contact_channel_identifier",
            new=AsyncMock(return_value="123456789"),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Test",
                contact_id=contact_id,
                intent="send",
            )

        assert result["status"] == "ok"
        call_args = mock_client.call_tool.await_args
        delivery = call_args.args[1]["notify_request"]["delivery"]
        assert delivery["recipient"] == "123456789"

    async def test_resolve_contact_channel_identifier_queries_db(self, butler_dir: Path) -> None:
        """_resolve_contact_channel_identifier should query shared.contact_info."""
        patches = _patch_infra()

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=None)
        mock_pool.execute = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"value": "123456789"})

        # Mock the context manager for pool.acquire()
        @asynccontextmanager
        async def mock_acquire():
            yield mock_conn

        mock_pool.acquire = mock_acquire

        # Override the mock_db.pool in patches
        mock_db = MagicMock()
        mock_db.provision = AsyncMock()
        mock_db.connect = AsyncMock(return_value=mock_pool)
        mock_db.close = AsyncMock()
        mock_db.pool = mock_pool
        mock_db.user = "postgres"
        mock_db.password = "postgres"
        mock_db.host = "localhost"
        mock_db.port = 5432
        mock_db.db_name = "butler_test"

        patches["db_from_env"] = patch("butlers.daemon.Database.from_env", return_value=mock_db)

        daemon, _ = await _start_daemon_with_notify(butler_dir, patches)
        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000020")

        # Reset mock_conn.fetchrow so we only see calls from our test, not daemon startup
        mock_conn.fetchrow.reset_mock()

        result = await daemon._resolve_contact_channel_identifier(
            contact_id=contact_id,
            channel="telegram",
        )

        assert result == "123456789"
        mock_conn.fetchrow.assert_awaited_once()
        call_args = mock_conn.fetchrow.await_args
        query = call_args.args[0]
        assert "shared.contact_info" in query
        assert "ci.contact_id" in query
        assert "ci.type" in query
        assert "is_primary" in query
        assert call_args.args[1] == contact_id
        assert call_args.args[2] == "telegram_chat_id"

    async def test_resolve_contact_channel_identifier_primary_preferred(
        self, butler_dir: Path
    ) -> None:
        """_resolve_contact_channel_identifier orders by is_primary DESC to prefer primary."""
        patches = _patch_infra()

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=None)
        mock_pool.execute = AsyncMock()
        mock_conn = AsyncMock()
        # Returns the primary value (first row from ORDER BY is_primary DESC)
        mock_conn.fetchrow = AsyncMock(return_value={"value": "primary-chat-id"})

        @asynccontextmanager
        async def mock_acquire():
            yield mock_conn

        mock_pool.acquire = mock_acquire

        mock_db = MagicMock()
        mock_db.provision = AsyncMock()
        mock_db.connect = AsyncMock(return_value=mock_pool)
        mock_db.close = AsyncMock()
        mock_db.pool = mock_pool
        mock_db.user = "postgres"
        mock_db.password = "postgres"
        mock_db.host = "localhost"
        mock_db.port = 5432
        mock_db.db_name = "butler_test"

        patches["db_from_env"] = patch("butlers.daemon.Database.from_env", return_value=mock_db)

        daemon, _ = await _start_daemon_with_notify(butler_dir, patches)
        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000021")

        result = await daemon._resolve_contact_channel_identifier(
            contact_id=contact_id,
            channel="telegram",
        )

        assert result == "primary-chat-id"
        # Verify ORDER BY is_primary DESC is in the query
        call_args = mock_conn.fetchrow.await_args
        query = call_args.args[0]
        assert "is_primary DESC" in query

    async def test_resolve_contact_channel_identifier_returns_none_when_not_found(
        self, butler_dir: Path
    ) -> None:
        """_resolve_contact_channel_identifier returns None when no row found."""
        patches = _patch_infra()

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=None)
        mock_pool.execute = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)

        @asynccontextmanager
        async def mock_acquire():
            yield mock_conn

        mock_pool.acquire = mock_acquire

        mock_db = MagicMock()
        mock_db.provision = AsyncMock()
        mock_db.connect = AsyncMock(return_value=mock_pool)
        mock_db.close = AsyncMock()
        mock_db.pool = mock_pool
        mock_db.user = "postgres"
        mock_db.password = "postgres"
        mock_db.host = "localhost"
        mock_db.port = 5432
        mock_db.db_name = "butler_test"

        patches["db_from_env"] = patch("butlers.daemon.Database.from_env", return_value=mock_db)

        daemon, _ = await _start_daemon_with_notify(butler_dir, patches)
        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000022")

        result = await daemon._resolve_contact_channel_identifier(
            contact_id=contact_id,
            channel="email",
        )

        assert result is None

    async def test_resolve_contact_channel_identifier_no_pool_returns_none(
        self, butler_dir: Path
    ) -> None:
        """_resolve_contact_channel_identifier returns None when db pool is None."""
        patches = _patch_infra()
        daemon, _ = await _start_daemon_with_notify(butler_dir, patches)

        # Simulate pool being closed/unavailable after startup
        daemon.db.pool = None

        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000023")

        result = await daemon._resolve_contact_channel_identifier(
            contact_id=contact_id,
            channel="telegram",
        )

        assert result is None

    async def test_resolve_contact_channel_identifier_handles_missing_table(
        self, butler_dir: Path
    ) -> None:
        """_resolve_contact_channel_identifier returns None when table doesn't exist."""
        patches = _patch_infra()

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=None)
        mock_pool.execute = AsyncMock()
        mock_conn = AsyncMock()

        # Simulate asyncpg UndefinedTableError
        class MockUndefinedTableError(Exception):
            __class__ = type(
                "UndefinedTableError",
                (Exception,),
                {"__module__": "asyncpg"},
            )

        # Use the string-based check: "does not exist"
        mock_conn.fetchrow = AsyncMock(
            side_effect=Exception("relation shared.contact_info does not exist")
        )

        @asynccontextmanager
        async def mock_acquire():
            yield mock_conn

        mock_pool.acquire = mock_acquire

        mock_db = MagicMock()
        mock_db.provision = AsyncMock()
        mock_db.connect = AsyncMock(return_value=mock_pool)
        mock_db.close = AsyncMock()
        mock_db.pool = mock_pool
        mock_db.user = "postgres"
        mock_db.password = "postgres"
        mock_db.host = "localhost"
        mock_db.port = 5432
        mock_db.db_name = "butler_test"

        patches["db_from_env"] = patch("butlers.daemon.Database.from_env", return_value=mock_db)

        daemon, _ = await _start_daemon_with_notify(butler_dir, patches)
        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000024")

        result = await daemon._resolve_contact_channel_identifier(
            contact_id=contact_id,
            channel="telegram",
        )

        assert result is None


@pytest.mark.asyncio
class TestNotifyMissingIdentifierFallback:
    """Task 7.3 — missing identifier parks action and notifies owner."""

    async def test_missing_identifier_returns_pending_status(self, butler_dir: Path) -> None:
        """When contact_id has no matching contact_info, return pending_missing_identifier."""
        patches = _patch_infra()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        mock_db = MagicMock()
        mock_db.provision = AsyncMock()
        mock_db.connect = AsyncMock(return_value=mock_pool)
        mock_db.close = AsyncMock()
        mock_db.pool = mock_pool
        mock_db.user = "postgres"
        mock_db.password = "postgres"
        mock_db.host = "localhost"
        mock_db.port = 5432
        mock_db.db_name = "butler_test"
        patches["db_from_env"] = patch("butlers.daemon.Database.from_env", return_value=mock_db)

        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000030")
        with (
            patch.object(
                daemon,
                "_resolve_contact_channel_identifier",
                new=AsyncMock(return_value=None),  # No identifier found
            ),
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value="owner@example.com"),  # Owner has identifier
            ),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello contact",
                contact_id=contact_id,
            )

        assert result["status"] == "pending_missing_identifier"
        assert result["contact_id"] == str(contact_id)
        assert result["channel"] == "email"

    async def test_missing_identifier_creates_pending_action_row(self, butler_dir: Path) -> None:
        """When missing identifier, a pending_actions row should be inserted."""
        patches = _patch_infra()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        mock_db = MagicMock()
        mock_db.provision = AsyncMock()
        mock_db.connect = AsyncMock(return_value=mock_pool)
        mock_db.close = AsyncMock()
        mock_db.pool = mock_pool
        mock_db.user = "postgres"
        mock_db.password = "postgres"
        mock_db.host = "localhost"
        mock_db.port = 5432
        mock_db.db_name = "butler_test"
        patches["db_from_env"] = patch("butlers.daemon.Database.from_env", return_value=mock_db)

        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000031")
        with (
            patch.object(
                daemon,
                "_resolve_contact_channel_identifier",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value=None),
            ),
        ):
            await notify_fn(
                channel="telegram",
                message="Test message",
                contact_id=contact_id,
            )

        # Verify pending_actions INSERT was called
        mock_pool.execute.assert_awaited()
        insert_call = mock_pool.execute.await_args_list[0]
        query = insert_call.args[0]
        assert "INSERT INTO pending_actions" in query
        assert "notify" in str(insert_call.args)  # tool_name = "notify"

    async def test_missing_identifier_pending_action_includes_contact_id(
        self, butler_dir: Path
    ) -> None:
        """The pending_action tool_args should include the contact_id."""
        patches = _patch_infra()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        mock_db = MagicMock()
        mock_db.provision = AsyncMock()
        mock_db.connect = AsyncMock(return_value=mock_pool)
        mock_db.close = AsyncMock()
        mock_db.pool = mock_pool
        mock_db.user = "postgres"
        mock_db.password = "postgres"
        mock_db.host = "localhost"
        mock_db.port = 5432
        mock_db.db_name = "butler_test"
        patches["db_from_env"] = patch("butlers.daemon.Database.from_env", return_value=mock_db)

        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000032")
        with (
            patch.object(
                daemon,
                "_resolve_contact_channel_identifier",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await notify_fn(
                channel="email",
                message="Important message",
                contact_id=contact_id,
            )

        assert result["status"] == "pending_missing_identifier"
        # The pending_action_id should be a UUID
        assert result.get("pending_action_id") is not None
        # Verify contact_id is in the INSERT args (tool_args JSON)
        # pool.execute(query, action_id, tool_name, tool_args_json, ...) →
        # args = (query, action_id, "notify", tool_args_json, ...)
        insert_call = mock_pool.execute.await_args_list[0]
        # args[3] is tool_args JSON (query=0, action_id=1, tool_name=2, tool_args=3)
        tool_args_json = insert_call.args[3]
        assert str(contact_id) in tool_args_json

    async def test_missing_identifier_notifies_owner_when_owner_has_identifier(
        self, butler_dir: Path
    ) -> None:
        """When missing identifier, owner should be notified if owner has channel identifier."""
        patches = _patch_infra()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        mock_db = MagicMock()
        mock_db.provision = AsyncMock()
        mock_db.connect = AsyncMock(return_value=mock_pool)
        mock_db.close = AsyncMock()
        mock_db.pool = mock_pool
        mock_db.user = "postgres"
        mock_db.password = "postgres"
        mock_db.host = "localhost"
        mock_db.port = 5432
        mock_db.db_name = "butler_test"
        patches["db_from_env"] = patch("butlers.daemon.Database.from_env", return_value=mock_db)

        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000033")
        with (
            patch.object(
                daemon,
                "_resolve_contact_channel_identifier",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value="owner@example.com"),
            ),
        ):
            result = await notify_fn(
                channel="email",
                message="Cannot deliver",
                contact_id=contact_id,
            )

        assert result["status"] == "pending_missing_identifier"
        # Switchboard call_tool should be called once to notify the owner
        mock_client.call_tool.assert_awaited_once()
        call_args = mock_client.call_tool.await_args
        notify_req = call_args.args[1]["notify_request"]
        assert notify_req["delivery"]["intent"] == "send"
        assert notify_req["delivery"]["recipient"] == "owner@example.com"
        assert "missing" in notify_req["delivery"]["message"].lower()

    async def test_missing_identifier_no_owner_notification_when_owner_has_no_identifier(
        self, butler_dir: Path
    ) -> None:
        """When missing identifier and owner has no identifier, skip owner notification."""
        patches = _patch_infra()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        mock_db = MagicMock()
        mock_db.provision = AsyncMock()
        mock_db.connect = AsyncMock(return_value=mock_pool)
        mock_db.close = AsyncMock()
        mock_db.pool = mock_pool
        mock_db.user = "postgres"
        mock_db.password = "postgres"
        mock_db.host = "localhost"
        mock_db.port = 5432
        mock_db.db_name = "butler_test"
        patches["db_from_env"] = patch("butlers.daemon.Database.from_env", return_value=mock_db)

        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000034")
        with (
            patch.object(
                daemon,
                "_resolve_contact_channel_identifier",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value=None),  # Owner also has no identifier
            ),
        ):
            result = await notify_fn(
                channel="email",
                message="Cannot deliver",
                contact_id=contact_id,
            )

        assert result["status"] == "pending_missing_identifier"
        # Switchboard should NOT be called when owner has no identifier
        mock_client.call_tool.assert_not_awaited()

    async def test_missing_identifier_no_pending_action_when_no_pool(
        self, butler_dir: Path
    ) -> None:
        """When db pool is None, pending_action_id should be None."""
        patches = _patch_infra()

        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # Simulate pool being unavailable after startup
        daemon.db.pool = None

        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000035")
        with (
            patch.object(
                daemon,
                "_resolve_contact_channel_identifier",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await notify_fn(
                channel="telegram",
                message="test",
                contact_id=contact_id,
            )

        assert result["status"] == "pending_missing_identifier"
        assert result["pending_action_id"] is None


@pytest.mark.asyncio
class TestNotifyOwnerDefaultResolution:
    """Task 7.4 — neither param defaults to owner resolution."""

    async def test_no_contact_id_no_recipient_calls_default_resolver(
        self, butler_dir: Path
    ) -> None:
        """With neither contact_id nor recipient, default resolution is called."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        mock_default_resolver = AsyncMock(return_value="owner@example.com")
        with patch.object(
            daemon,
            "_resolve_default_notify_recipient",
            new=mock_default_resolver,
        ):
            result = await notify_fn(
                channel="email",
                message="Hello owner",
            )

        assert result["status"] == "ok"
        mock_default_resolver.assert_awaited_once_with(
            channel="email",
            intent="send",
            recipient=None,
        )

    async def test_no_contact_id_no_recipient_contact_resolver_not_called(
        self, butler_dir: Path
    ) -> None:
        """With neither contact_id nor recipient, contact resolver is not called."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        mock_contact_resolver = AsyncMock(return_value="some-value")
        with (
            patch.object(daemon, "_resolve_contact_channel_identifier", new=mock_contact_resolver),
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value="owner@example.com"),
            ),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello",
            )

        assert result["status"] == "ok"
        mock_contact_resolver.assert_not_awaited()

    async def test_explicit_recipient_bypasses_contact_id_resolution(
        self, butler_dir: Path
    ) -> None:
        """Explicit recipient string is used as-is; contact_id=None is ignored."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        mock_contact_resolver = AsyncMock(return_value="other@example.com")
        with patch.object(daemon, "_resolve_contact_channel_identifier", new=mock_contact_resolver):
            result = await notify_fn(
                channel="email",
                message="Hello",
                recipient="explicit@example.com",
                # No contact_id — uses recipient as-is
            )

        assert result["status"] == "ok"
        mock_contact_resolver.assert_not_awaited()

        call_args = mock_client.call_tool.await_args
        delivery = call_args.args[1]["notify_request"]["delivery"]
        assert delivery["recipient"] == "explicit@example.com"


@pytest.mark.asyncio
class TestNotifyContactIdResolutionPriority:
    """Test that contact_id takes priority over recipient string."""

    async def test_contact_id_takes_priority_over_recipient_string(self, butler_dir: Path) -> None:
        """When both contact_id and recipient provided, contact_id resolution wins."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000040")
        with patch.object(
            daemon,
            "_resolve_contact_channel_identifier",
            new=AsyncMock(return_value="contact-resolved@example.com"),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello",
                contact_id=contact_id,
                recipient="explicit@example.com",  # Should be ignored
            )

        assert result["status"] == "ok"
        call_args = mock_client.call_tool.await_args
        delivery = call_args.args[1]["notify_request"]["delivery"]
        # contact_id resolution should win
        assert delivery["recipient"] == "contact-resolved@example.com"

    async def test_backwards_compat_no_contact_id_explicit_recipient(
        self, butler_dir: Path
    ) -> None:
        """Existing calls with recipient and no contact_id remain backwards compatible."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client

        result = await notify_fn(
            channel="email",
            message="Hello",
            recipient="user@example.com",
        )

        assert result["status"] == "ok"
        call_args = mock_client.call_tool.await_args
        delivery = call_args.args[1]["notify_request"]["delivery"]
        assert delivery["recipient"] == "user@example.com"
