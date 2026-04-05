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
from butlers.identity import ResolvedContact

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
        "db_from_env": patch("butlers.daemon.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "configure_logging": patch("butlers.core.logging.configure_logging"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(
            ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock
        ),
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
        patches["init_telemetry"],
        patches["configure_logging"],
        patches["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp),
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


def _known_contact_patch(email: str = "user@example.com") -> Any:
    """Return a patch that makes resolve_contact_by_channel return a known contact."""
    contact = ResolvedContact(
        contact_id=uuid.UUID("00000000-0000-0000-0000-ffffffffffff"),
        name="Test Contact",
        roles=["owner"],
        entity_id=None,
    )

    async def _mock_resolve(pool: Any, channel_type: str, channel_value: str) -> Any:
        return contact

    return patch("butlers.identity.resolve_contact_by_channel", side_effect=_mock_resolve)


def _make_mock_client(*, is_error: bool = False) -> Any:
    """Create a mock switchboard client."""
    mock_call_result = MagicMock()
    mock_call_result.is_error = is_error
    mock_call_result.data = {"status": "sent"}
    mock_call_result.content = [MagicMock(text='{"status":"sent"}')]

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    return mock_client


def _make_pool_with_conn(fetchrow_return: Any = None, fetchrow_error: Exception | None = None):
    """Build a mock (pool, conn) pair for resolver tests."""
    mock_conn = AsyncMock()
    if fetchrow_error:
        mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_error)
    else:
        mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.execute = AsyncMock()

    @asynccontextmanager
    async def mock_acquire():
        yield mock_conn

    mock_pool.acquire = mock_acquire
    return mock_pool, mock_conn


def _patch_db_in_patches(patches: dict, mock_pool: Any) -> None:
    """Override db_from_env in patches dict with a mock_db using mock_pool."""
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
    patches["db_from_env"] = patch("butlers.daemon.Database.from_env", return_value=mock_db)


@pytest.mark.asyncio
class TestNotifyContactIdParameter:
    """Task 7.1 — notify() accepts contact_id parameter."""

    async def test_notify_contact_id_params(self, butler_dir: Path) -> None:
        """None, UUID, and resolver-call variants all work correctly."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.switchboard_client = _make_mock_client()

        # contact_id=None → succeeds via default resolver
        with (
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value="user@example.com"),
            ),
            _known_contact_patch(),
        ):
            result = await notify_fn(channel="email", message="Hello", contact_id=None)
        assert result["status"] == "ok"

        # contact_id=UUID → succeeds via contact resolver
        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        daemon.switchboard_client = _make_mock_client()
        with (
            patch.object(
                daemon,
                "_resolve_contact_channel_identifier",
                new=AsyncMock(return_value="user@example.com"),
            ),
            _known_contact_patch("user@example.com"),
        ):
            result2 = await notify_fn(
                channel="email", message="Hello contact", contact_id=contact_id
            )
        assert result2["status"] == "ok"

        # contact_id → _resolve_contact_channel_identifier called with correct args
        contact_id2 = uuid.UUID("00000000-0000-0000-0000-000000000002")
        daemon.switchboard_client = _make_mock_client()
        mock_resolver = AsyncMock(return_value="resolved@example.com")
        with patch.object(daemon, "_resolve_contact_channel_identifier", new=mock_resolver):
            await notify_fn(channel="email", message="Hello", contact_id=contact_id2)
        mock_resolver.assert_awaited_once_with(contact_id=contact_id2, channel="email")


@pytest.mark.asyncio
class TestNotifyContactIdResolution:
    """Task 7.2 — contact_id resolves to channel identifier, primary preferred."""

    async def test_resolved_identifier_used_in_delivery(self, butler_dir: Path) -> None:
        """Resolved email and telegram identifiers go into delivery; primary preferred."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # Email: resolved identifier in delivery
        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000010")
        daemon.switchboard_client = _make_mock_client()
        with (
            patch.object(
                daemon,
                "_resolve_contact_channel_identifier",
                new=AsyncMock(return_value="contact@example.com"),
            ),
            _known_contact_patch("contact@example.com"),
        ):
            result = await notify_fn(
                channel="email", message="Test message", contact_id=contact_id
            )
        assert result["status"] == "ok"
        delivery = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"][
            "delivery"
        ]
        assert delivery["recipient"] == "contact@example.com"

        # Telegram: resolved chat_id in delivery
        contact_id2 = uuid.UUID("00000000-0000-0000-0000-000000000011")
        daemon.switchboard_client = _make_mock_client()
        with patch.object(
            daemon,
            "_resolve_contact_channel_identifier",
            new=AsyncMock(return_value="123456789"),
        ):
            result2 = await notify_fn(
                channel="telegram", message="Test", contact_id=contact_id2, intent="send"
            )
        assert result2["status"] == "ok"
        delivery2 = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"][
            "delivery"
        ]
        assert delivery2["recipient"] == "123456789"

    async def test_resolve_contact_channel_identifier_behavior(self, butler_dir: Path) -> None:
        """Queries DB correctly; prefers primary; returns None when not found or on error."""
        # Pool with a result
        mock_pool, mock_conn = _make_pool_with_conn({"value": "123456789"})
        patches = _patch_infra()
        _patch_db_in_patches(patches, mock_pool)
        daemon, _ = await _start_daemon_with_notify(butler_dir, patches)
        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000020")
        mock_conn.fetchrow.reset_mock()

        result = await daemon._resolve_contact_channel_identifier(
            contact_id=contact_id, channel="telegram"
        )
        assert result == "123456789"
        mock_conn.fetchrow.assert_awaited_once()
        call_args = mock_conn.fetchrow.await_args
        query = call_args.args[0]
        assert "public.contact_info" in query
        assert "ci.contact_id" in query and "ci.type" in query and "is_primary" in query
        assert call_args.args[1] == contact_id
        assert call_args.args[2] == "telegram_chat_id"
        assert "is_primary DESC" in query  # primary preferred

        # Returns None when no row found
        mock_pool2, _ = _make_pool_with_conn(None)
        patches2 = _patch_infra()
        _patch_db_in_patches(patches2, mock_pool2)
        daemon2, _ = await _start_daemon_with_notify(butler_dir, patches2)
        assert (
            await daemon2._resolve_contact_channel_identifier(
                contact_id=uuid.UUID("00000000-0000-0000-0000-000000000022"), channel="email"
            )
            is None
        )

        # Returns None when pool is None
        daemon2.db.pool = None
        assert (
            await daemon2._resolve_contact_channel_identifier(
                contact_id=uuid.UUID("00000000-0000-0000-0000-000000000023"), channel="telegram"
            )
            is None
        )

        # Returns None when table doesn't exist
        mock_pool3, _ = _make_pool_with_conn(
            fetchrow_error=Exception("relation public.contact_info does not exist")
        )
        patches3 = _patch_infra()
        _patch_db_in_patches(patches3, mock_pool3)
        daemon3, _ = await _start_daemon_with_notify(butler_dir, patches3)
        assert (
            await daemon3._resolve_contact_channel_identifier(
                contact_id=uuid.UUID("00000000-0000-0000-0000-000000000024"), channel="telegram"
            )
            is None
        )


def _make_missing_id_patches(butler_dir: Path) -> tuple[dict, Any, Any]:
    """Return patches + daemon startup for missing-identifier tests."""
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
    mock_db.db_name = "butlers"
    patches = _patch_infra()
    patches["db_from_env"] = patch("butlers.daemon.Database.from_env", return_value=mock_db)
    return patches, mock_pool, mock_db


@pytest.mark.asyncio
class TestNotifyMissingIdentifierFallback:
    """Task 7.3 — missing identifier parks action and notifies owner."""

    async def test_missing_identifier_fallback(self, butler_dir: Path) -> None:
        """Parks pending; INSERT includes contact_id; notifies owner if available; skips if not."""
        patches, mock_pool, _ = _make_missing_id_patches(butler_dir)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000030")

        # Returns pending_missing_identifier; contact_id + channel in response
        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client
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
                channel="email", message="Hello contact", contact_id=contact_id
            )
        assert result["status"] == "pending_missing_identifier"
        assert result["contact_id"] == str(contact_id)
        assert result["channel"] == "email"

        # INSERT INTO pending_actions called with contact_id in tool_args
        mock_pool.execute.assert_awaited()
        insert_call = mock_pool.execute.await_args_list[0]
        assert "INSERT INTO pending_actions" in insert_call.args[0]
        assert "notify" in str(insert_call.args)
        assert str(contact_id) in insert_call.args[3]  # tool_args JSON

        # pending_action_id returned
        assert result.get("pending_action_id") is not None

        # Owner notified when owner has identifier
        mock_client.call_tool.assert_awaited_once()
        notify_req = mock_client.call_tool.await_args.args[1]["notify_request"]
        assert notify_req["delivery"]["recipient"] == "owner@example.com"
        assert "missing" in notify_req["delivery"]["message"].lower()

        # No owner notification when owner has no identifier
        mock_client2 = _make_mock_client()
        daemon.switchboard_client = mock_client2
        mock_pool.execute.reset_mock()
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
            result2 = await notify_fn(
                channel="email",
                message="Cannot deliver",
                contact_id=uuid.UUID("00000000-0000-0000-0000-000000000034"),
            )
        assert result2["status"] == "pending_missing_identifier"
        mock_client2.call_tool.assert_not_awaited()

    async def test_missing_identifier_no_pending_action_when_no_pool(
        self, butler_dir: Path
    ) -> None:
        """When db pool is None, pending_action_id is None."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.db.pool = None
        daemon.switchboard_client = _make_mock_client()

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
                contact_id=uuid.UUID("00000000-0000-0000-0000-000000000035"),
            )

        assert result["status"] == "pending_missing_identifier"
        assert result["pending_action_id"] is None


@pytest.mark.asyncio
class TestNotifyOwnerDefaultResolution:
    """Task 7.4 — neither param defaults to owner resolution."""

    async def test_owner_resolution_and_bypasses(self, butler_dir: Path) -> None:
        """No contact_id/recipient → default resolver called; explicit recipient bypasses it."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.switchboard_client = _make_mock_client()

        # Neither → default resolver called
        mock_default_resolver = AsyncMock(return_value="owner@example.com")
        mock_contact_resolver = AsyncMock(return_value="some-value")
        with (
            patch.object(daemon, "_resolve_default_notify_recipient", new=mock_default_resolver),
            patch.object(daemon, "_resolve_contact_channel_identifier", new=mock_contact_resolver),
            _known_contact_patch(),
        ):
            result = await notify_fn(channel="email", message="Hello owner")
        assert result["status"] == "ok"
        mock_default_resolver.assert_awaited_once_with(
            channel="email", intent="send", recipient=None, request_context=None
        )
        mock_contact_resolver.assert_not_awaited()

        # Explicit recipient → contact resolver not called
        daemon.switchboard_client = _make_mock_client()
        mock_contact_resolver2 = AsyncMock(return_value="other@example.com")
        with (
            patch.object(daemon, "_resolve_contact_channel_identifier", new=mock_contact_resolver2),
            _known_contact_patch(),
        ):
            result2 = await notify_fn(
                channel="email", message="Hello", recipient="explicit@example.com"
            )
        assert result2["status"] == "ok"
        mock_contact_resolver2.assert_not_awaited()
        delivery = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"][
            "delivery"
        ]
        assert delivery["recipient"] == "explicit@example.com"


@pytest.mark.asyncio
class TestNotifyContactIdResolutionPriority:
    """Test that contact_id takes priority over recipient string."""

    async def test_contact_id_and_backwards_compat(self, butler_dir: Path) -> None:
        """contact_id wins over explicit recipient; no contact_id uses recipient as-is."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # contact_id wins
        daemon.switchboard_client = _make_mock_client()
        with (
            patch.object(
                daemon,
                "_resolve_contact_channel_identifier",
                new=AsyncMock(return_value="contact-resolved@example.com"),
            ),
            _known_contact_patch("contact-resolved@example.com"),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello",
                contact_id=uuid.UUID("00000000-0000-0000-0000-000000000040"),
                recipient="explicit@example.com",
            )
        assert result["status"] == "ok"
        delivery = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"][
            "delivery"
        ]
        assert delivery["recipient"] == "contact-resolved@example.com"

        # No contact_id → explicit recipient used
        daemon.switchboard_client = _make_mock_client()
        with _known_contact_patch():
            result2 = await notify_fn(
                channel="email", message="Hello", recipient="user@example.com"
            )
        assert result2["status"] == "ok"
        delivery2 = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"][
            "delivery"
        ]
        assert delivery2["recipient"] == "user@example.com"


@pytest.mark.asyncio
class TestNotifyEmailRecipientValidation:
    """Validate that unknown email recipients are rejected to prevent hallucinated sends."""

    async def test_email_validation(self, butler_dir: Path) -> None:
        """Unknown parked; known sent; telegram skips validation; contact_id also validates."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # Unknown email → parked as pending_approval
        daemon.switchboard_client = _make_mock_client()
        with patch("butlers.identity.resolve_contact_by_channel", new=AsyncMock(return_value=None)):
            result = await notify_fn(
                channel="email", message="Hello stranger", recipient="hallucinated@example.com"
            )
        assert result["status"] == "pending_approval"
        assert "pending_action_id" in result
        daemon.switchboard_client.call_tool.assert_not_awaited()

        # Known email → delivered
        daemon.switchboard_client = _make_mock_client()
        with _known_contact_patch():
            result2 = await notify_fn(
                channel="email", message="Hello known", recipient="known@example.com"
            )
        assert result2["status"] == "ok"
        daemon.switchboard_client.call_tool.assert_awaited_once()

        # Telegram not validated
        daemon.switchboard_client = _make_mock_client()
        mock_resolve = AsyncMock(return_value=None)
        with patch("butlers.identity.resolve_contact_by_channel", new=mock_resolve):
            result3 = await notify_fn(channel="telegram", message="Hello", recipient="12345")
        assert result3["status"] == "ok"
        mock_resolve.assert_not_awaited()

        # contact_id path still validates email (bug fix: guard must run regardless of path)
        daemon.switchboard_client = _make_mock_client()
        mock_resolve2 = AsyncMock(return_value=None)
        with (
            patch.object(
                daemon,
                "_resolve_contact_channel_identifier",
                new=AsyncMock(return_value="contact-email@example.com"),
            ),
            patch("butlers.identity.resolve_contact_by_channel", new=mock_resolve2),
        ):
            result4 = await notify_fn(
                channel="email",
                message="Hello via contact_id",
                contact_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
            )
        assert result4["status"] == "pending_approval", (
            "contact_id path must NOT bypass email validation"
        )
        mock_resolve2.assert_awaited_once()
