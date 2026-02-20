"""Tests for notify react intent functionality."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from butlers.daemon import ButlerDaemon
from butlers.tools.switchboard.routing.contracts import parse_notify_request


@pytest.fixture
def butler_dir(tmp_path: Path) -> Path:
    """Create a minimal butler directory for testing."""
    butler_path = tmp_path / "test-butler"
    butler_path.mkdir()
    (butler_path / "butler.toml").write_text(
        """
[butler]
name = "test"
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


def _patch_infra() -> dict[str, Any]:
    """Patch infrastructure dependencies for daemon tests."""
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=None)

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


@pytest.mark.asyncio
class TestNotifyReactIntent:
    """Test suite for notify react intent."""

    async def _start_daemon_with_notify(
        self, butler_dir: Path, patches: dict[str, Any]
    ) -> tuple[ButlerDaemon, Any]:
        """Start daemon and extract notify tool function."""
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

    async def test_notify_react_intent_accepted(self, butler_dir: Path) -> None:
        """notify with intent='react' should be accepted as valid intent."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # Should fail due to missing emoji, not unsupported intent
        result = await notify_fn(
            channel="telegram",
            message="",  # Empty message is OK for react
            intent="react",
            request_context={"source_thread_identity": "123:456"},
        )
        assert result["status"] == "error"
        assert "emoji" in result["error"].lower()

    async def test_notify_react_requires_emoji(self, butler_dir: Path) -> None:
        """notify with intent='react' requires emoji parameter."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        result = await notify_fn(
            channel="telegram",
            message="",
            intent="react",
            request_context={"source_thread_identity": "123:456"},
        )
        assert result["status"] == "error"
        assert "emoji" in result["error"].lower()

    async def test_notify_react_requires_telegram_channel(self, butler_dir: Path) -> None:
        """notify with intent='react' requires channel='telegram'."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        result = await notify_fn(
            channel="email",
            message="",
            intent="react",
            emoji="👍",
            request_context={"source_thread_identity": "123:456"},
        )
        assert result["status"] == "error"
        assert "telegram" in result["error"].lower()
        assert "not supported" in result["error"].lower()

    async def test_notify_react_requires_request_context(self, butler_dir: Path) -> None:
        """notify with intent='react' requires request_context."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        result = await notify_fn(
            channel="telegram",
            message="",
            intent="react",
            emoji="👍",
        )
        assert result["status"] == "error"
        assert "request_context" in result["error"].lower()

    async def test_notify_react_requires_source_thread_identity(self, butler_dir: Path) -> None:
        """notify with intent='react' requires source_thread_identity in request_context."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        result = await notify_fn(
            channel="telegram",
            message="",
            intent="react",
            emoji="👍",
            request_context={"request_id": "test"},  # Missing source_thread_identity
        )
        assert result["status"] == "error"
        assert "source_thread_identity" in result["error"].lower()

    async def test_notify_react_empty_message_allowed(self, butler_dir: Path) -> None:
        """notify with intent='react' allows empty message."""
        patches = _patch_infra()
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(
            return_value=MagicMock(
                is_error=False,
                data={"status": "ok"},
                content=[MagicMock(text='{"status":"ok"}')],
            )
        )

        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = mock_client
        assert notify_fn is not None

        # Empty message should be OK for react intent
        result = await notify_fn(
            channel="telegram",
            message="",  # Empty message
            intent="react",
            emoji="👍",
            request_context={"source_thread_identity": "123:456"},
        )
        # Should succeed (not fail with empty message validation)
        assert result["status"] == "ok"

    async def test_notify_react_forwards_emoji_to_switchboard(self, butler_dir: Path) -> None:
        """notify with intent='react' should include emoji in notify_request."""
        patches = _patch_infra()
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(
            return_value=MagicMock(
                is_error=False,
                data={"status": "ok"},
                content=[MagicMock(text='{"status":"ok"}')],
            )
        )

        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        daemon.switchboard_client = mock_client
        assert notify_fn is not None

        result = await notify_fn(
            channel="telegram",
            message="",
            intent="react",
            emoji="🔥",
            request_context={"source_thread_identity": "123:456"},
        )
        assert result["status"] == "ok"

        # Verify emoji was passed to switchboard
        mock_client.call_tool.assert_called_once()
        call_args = mock_client.call_tool.call_args
        assert call_args[0][0] == "deliver"
        notify_request = call_args[0][1]["notify_request"]
        assert notify_request["delivery"]["emoji"] == "🔥"
        assert notify_request["delivery"]["intent"] == "react"


class TestNotifyReactContract:
    """Test suite for notify.v1 contract validation of react intent."""

    def test_react_intent_validates_emoji_required(self) -> None:
        """Contract validation should require emoji for react intent."""
        payload = {
            "schema_version": "notify.v1",
            "origin_butler": "health",
            "delivery": {
                "intent": "react",
                "channel": "telegram",
                "message": "",
            },
            "request_context": {
                "request_id": "01916b9d-1234-7000-abcd-123456789abc",
                "source_channel": "telegram",
                "source_endpoint_identity": "test",
                "source_sender_identity": "user123",
                "source_thread_identity": "123:456",
            },
        }

        with pytest.raises(ValidationError) as exc_info:
            parse_notify_request(payload)

        assert "emoji" in str(exc_info.value).lower()

    def test_react_intent_validates_request_context_required(self) -> None:
        """Contract validation should require request_context for react intent."""
        payload = {
            "schema_version": "notify.v1",
            "origin_butler": "health",
            "delivery": {
                "intent": "react",
                "channel": "telegram",
                "message": "",
                "emoji": "👍",
            },
        }

        with pytest.raises(ValidationError) as exc_info:
            parse_notify_request(payload)

        assert "context" in str(exc_info.value).lower()

    def test_react_intent_validates_thread_identity_required(self) -> None:
        """Contract validation should require source_thread_identity for react on telegram."""
        payload = {
            "schema_version": "notify.v1",
            "origin_butler": "health",
            "delivery": {
                "intent": "react",
                "channel": "telegram",
                "message": "",
                "emoji": "👍",
            },
            "request_context": {
                "request_id": "01916b9d-1234-7000-abcd-123456789abc",
                "source_channel": "telegram",
                "source_endpoint_identity": "test",
                "source_sender_identity": "user123",
                # Missing source_thread_identity
            },
        }

        with pytest.raises(ValidationError) as exc_info:
            parse_notify_request(payload)

        assert "thread" in str(exc_info.value).lower()

    def test_react_intent_valid_payload(self) -> None:
        """Contract validation should accept valid react intent payload."""
        payload = {
            "schema_version": "notify.v1",
            "origin_butler": "health",
            "delivery": {
                "intent": "react",
                "channel": "telegram",
                "message": "",
                "emoji": "🎉",
            },
            "request_context": {
                "request_id": "01916b9d-1234-7000-abcd-123456789abc",
                "source_channel": "telegram",
                "source_endpoint_identity": "test",
                "source_sender_identity": "user123",
                "source_thread_identity": "123:456",
            },
        }

        result = parse_notify_request(payload)
        assert result.delivery.intent == "react"
        assert result.delivery.emoji == "🎉"
        assert result.request_context is not None
        assert result.request_context.source_thread_identity == "123:456"
