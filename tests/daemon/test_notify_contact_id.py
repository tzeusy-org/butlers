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


def _make_runtime_config_row(butler_name: str = "test-butler") -> dict:
    """Return a dict-like row for the runtime_config table, as returned by asyncpg.fetchrow."""
    return {
        "butler_name": butler_name,
        "core_groups": None,
        "model": None,
        "runtime_type": "codex",
        "args": "[]",
        "max_concurrent": 3,
        "max_queued": 10,
        "session_timeout_s": 900,
        "seeded_at": None,
        "updated_at": None,
    }


def _make_fetchrow_side_effect(butler_name: str = "test-butler"):
    """Return an async side_effect for pool.fetchrow that returns runtime_config rows
    for runtime_config queries and None for all other queries."""

    async def _fetchrow(query: str, *args, **kwargs):
        if "runtime_config" in query:
            return _make_runtime_config_row(butler_name)
        return None

    return _fetchrow


def _patch_infra(mock_pool: Any = None) -> dict[str, Any]:
    """Patch infrastructure dependencies for daemon tests."""
    if mock_pool is None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=None)
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetchval = AsyncMock(return_value=None)
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_pool = AsyncMock()
        # Support `async with pool.acquire() as conn:` for _ensure_owner_entity
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_pool.fetchval = AsyncMock(return_value=None)
        mock_pool.execute = AsyncMock(return_value=None)
        mock_pool.fetchrow = AsyncMock(side_effect=_make_fetchrow_side_effect())
        mock_pool.fetch = AsyncMock(return_value=[])

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
    mock_pool.execute = AsyncMock(return_value=None)
    # pool-level fetchrow must return runtime_config rows so seed_if_empty works
    mock_pool.fetchrow = AsyncMock(side_effect=_make_fetchrow_side_effect())
    mock_pool.fetch = AsyncMock(return_value=[])

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
class TestNotifyContactIdResolution:
    """Tasks 7.1+7.2 — contact_id resolves to channel identifier, used in delivery."""

    async def test_contact_id_resolves_and_delivers(self, butler_dir: Path) -> None:
        """contact_id=UUID calls resolver; resolved identifier used in delivery payload;
        DB query uses correct table+columns; returns None when not found/error."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # contact_id → resolver called with correct args, result used in delivery
        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000010")
        daemon.switchboard_client = _make_mock_client()
        mock_resolver = AsyncMock(return_value="contact@example.com")
        with (
            patch.object(daemon, "_resolve_contact_channel_identifier", new=mock_resolver),
            _known_contact_patch("contact@example.com"),
        ):
            result = await notify_fn(channel="email", message="Test", contact_id=contact_id)
        assert result["status"] == "ok"
        mock_resolver.assert_awaited_once_with(contact_id=contact_id, channel="email")
        delivery = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"]["delivery"]
        assert delivery["recipient"] == "contact@example.com"

        # DB resolver queries contact_info with primary preference; None when not found
        mock_pool, mock_conn = _make_pool_with_conn({"value": "123456789"})
        patches2 = _patch_infra()
        _patch_db_in_patches(patches2, mock_pool)
        daemon2, _ = await _start_daemon_with_notify(butler_dir, patches2)
        cid = uuid.UUID("00000000-0000-0000-0000-000000000020")
        result2 = await daemon2._resolve_contact_channel_identifier(contact_id=cid, channel="telegram")
        assert result2 == "123456789"
        query = mock_conn.fetchrow.await_args.args[0]
        assert "public.contact_info" in query and "is_primary DESC" in query

        mock_pool3, _ = _make_pool_with_conn(None)
        patches3 = _patch_infra()
        _patch_db_in_patches(patches3, mock_pool3)
        daemon3, _ = await _start_daemon_with_notify(butler_dir, patches3)
        assert await daemon3._resolve_contact_channel_identifier(
            contact_id=uuid.UUID("00000000-0000-0000-0000-000000000022"), channel="email"
        ) is None


def _make_missing_id_patches(butler_dir: Path) -> tuple[dict, Any, Any]:
    """Return patches + daemon startup for missing-identifier tests."""
    mock_conn_inner = AsyncMock()
    mock_conn_inner.execute = AsyncMock(return_value=None)
    mock_conn_inner.fetchrow = AsyncMock(return_value=None)
    mock_conn_inner.fetchval = AsyncMock(return_value=None)
    mock_conn_inner.fetch = AsyncMock(return_value=[])

    mock_pool = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn_inner)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(side_effect=_make_fetchrow_side_effect())
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.fetch = AsyncMock(return_value=[])
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
class TestNotifyMissingIdentifierAndOwner:
    """Tasks 7.3+7.4 — missing identifier parks; no contact_id uses owner resolution."""

    async def test_missing_identifier_parks_and_owner_fallback(self, butler_dir: Path) -> None:
        """Missing identifier → pending_missing_identifier; owner notified if available;
        no contact_id/recipient → owner default resolver called; contact_id wins over recipient."""
        patches, mock_pool, _ = _make_missing_id_patches(butler_dir)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        contact_id = uuid.UUID("00000000-0000-0000-0000-000000000030")

        # Missing identifier → pending_missing_identifier; owner notified
        mock_client = _make_mock_client()
        daemon.switchboard_client = mock_client
        with (
            patch.object(daemon, "_resolve_contact_channel_identifier", new=AsyncMock(return_value=None)),
            patch.object(daemon, "_resolve_default_notify_recipient", new=AsyncMock(return_value="owner@example.com")),
        ):
            result = await notify_fn(channel="email", message="Hello contact", contact_id=contact_id)
        assert result["status"] == "pending_missing_identifier"
        assert result["contact_id"] == str(contact_id)
        mock_client.call_tool.assert_awaited_once()

        # No owner → no notification
        mock_client2 = _make_mock_client()
        daemon.switchboard_client = mock_client2
        with (
            patch.object(daemon, "_resolve_contact_channel_identifier", new=AsyncMock(return_value=None)),
            patch.object(daemon, "_resolve_default_notify_recipient", new=AsyncMock(return_value=None)),
        ):
            result2 = await notify_fn(channel="email", message="Cannot deliver", contact_id=contact_id)
        assert result2["status"] == "pending_missing_identifier"
        mock_client2.call_tool.assert_not_awaited()

        # No contact_id → default owner resolver called
        patches3 = _patch_infra()
        daemon3, notify_fn3 = await _start_daemon_with_notify(butler_dir, patches3)
        daemon3.switchboard_client = _make_mock_client()
        mock_default = AsyncMock(return_value="owner@example.com")
        mock_contact = AsyncMock(return_value="ignored")
        with (
            patch.object(daemon3, "_resolve_default_notify_recipient", new=mock_default),
            patch.object(daemon3, "_resolve_contact_channel_identifier", new=mock_contact),
            _known_contact_patch(),
        ):
            r3 = await notify_fn3(channel="email", message="Hello owner")
        assert r3["status"] == "ok"
        mock_default.assert_awaited_once()
        mock_contact.assert_not_awaited()

        # contact_id wins over explicit recipient
        daemon3.switchboard_client = _make_mock_client()
        with (
            patch.object(daemon3, "_resolve_contact_channel_identifier", new=AsyncMock(return_value="contact-resolved@example.com")),
            _known_contact_patch("contact-resolved@example.com"),
        ):
            r4 = await notify_fn3(
                channel="email", message="Hello",
                contact_id=uuid.UUID("00000000-0000-0000-0000-000000000040"),
                recipient="explicit@example.com",
            )
        assert r4["status"] == "ok"
        delivery = daemon3.switchboard_client.call_tool.await_args.args[1]["notify_request"]["delivery"]
        assert delivery["recipient"] == "contact-resolved@example.com"


@pytest.mark.asyncio
class TestNotifyEmailRecipientValidation:
    """Email recipients must be known contacts; contact_id path also validated."""

    async def test_email_validation(self, butler_dir: Path) -> None:
        """Unknown email → pending_approval; known sent; telegram skips; contact_id path validates."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # Unknown email → parked as pending_approval
        daemon.switchboard_client = _make_mock_client()
        with patch("butlers.identity.resolve_contact_by_channel", new=AsyncMock(return_value=None)):
            result = await notify_fn(channel="email", message="Hello stranger", recipient="hallucinated@example.com")
        assert result["status"] == "pending_approval"

        # Known email → delivered
        daemon.switchboard_client = _make_mock_client()
        with _known_contact_patch():
            result2 = await notify_fn(channel="email", message="Hello known", recipient="known@example.com")
        assert result2["status"] == "ok"

        # contact_id path still validates email
        daemon.switchboard_client = _make_mock_client()
        with (
            patch.object(daemon, "_resolve_contact_channel_identifier", new=AsyncMock(return_value="contact-email@example.com")),
            patch("butlers.identity.resolve_contact_by_channel", new=AsyncMock(return_value=None)),
        ):
            result3 = await notify_fn(
                channel="email", message="Hello via contact_id",
                contact_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
            )
        assert result3["status"] == "pending_approval"
