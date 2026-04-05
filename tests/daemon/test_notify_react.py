"""Tests for notify react intent functionality."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from butlers.daemon import ButlerDaemon
from butlers.tools.switchboard.routing.contracts import parse_notify_request

pytestmark = pytest.mark.unit


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
    mock_db.db_name = "butlers"

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

    def _mock_ok_client(self) -> AsyncMock:
        """Return a mock switchboard client that returns ok."""
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(
            return_value=MagicMock(
                is_error=False,
                data={"status": "ok"},
                content=[MagicMock(text='{"status":"ok"}')],
            )
        )
        return mock_client

    async def test_notify_react_validation_and_delivery(self, butler_dir: Path) -> None:
        """Validation errors: emoji required, telegram only, request_context, thread identity;
        successful delivery: empty/omitted message, emoji+intent forwarded."""
        patches = _patch_infra()
        daemon, notify_fn = await self._start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # Validation: intent accepted but emoji required
        r_no_emoji = await notify_fn(
            channel="telegram", message="", intent="react",
            request_context={"source_thread_identity": "123:456"},
        )
        assert r_no_emoji["status"] == "error"
        assert "emoji" in r_no_emoji["error"].lower()

        # Validation: non-telegram rejected
        r_email = await notify_fn(
            channel="email", message="", intent="react", emoji="👍",
            request_context={"source_thread_identity": "123:456"},
        )
        assert r_email["status"] == "error"
        assert "telegram" in r_email["error"].lower()

        # Validation: missing request_context
        r_no_ctx = await notify_fn(channel="telegram", message="", intent="react", emoji="👍")
        assert r_no_ctx["status"] == "error"
        assert "request_context" in r_no_ctx["error"].lower()

        # Validation: missing source_thread_identity
        r_no_thread = await notify_fn(
            channel="telegram", message="", intent="react", emoji="👍",
            request_context={"request_id": "test"},
        )
        assert r_no_thread["status"] == "error"
        assert "source_thread_identity" in r_no_thread["error"].lower()

        # Successful delivery: empty message allowed
        daemon.switchboard_client = self._mock_ok_client()
        r1 = await notify_fn(
            channel="telegram", message="", intent="react", emoji="👍",
            request_context={"source_thread_identity": "123:456"},
        )
        assert r1["status"] == "ok"

        # Successful delivery: omitted message normalized to empty string
        daemon.switchboard_client = self._mock_ok_client()
        r2 = await notify_fn(
            channel="telegram", intent="react", emoji="✅",
            request_context={"source_thread_identity": "123:456"},
        )
        assert r2["status"] == "ok"
        assert daemon.switchboard_client.call_tool.call_args[0][1]["notify_request"]["delivery"]["message"] == ""

        # Successful delivery: emoji and intent forwarded
        daemon.switchboard_client = self._mock_ok_client()
        r3 = await notify_fn(
            channel="telegram", message="", intent="react", emoji="🔥",
            request_context={"source_thread_identity": "123:456"},
        )
        assert r3["status"] == "ok"
        nr = daemon.switchboard_client.call_tool.call_args[0][1]["notify_request"]
        assert nr["delivery"]["emoji"] == "🔥"
        assert nr["delivery"]["intent"] == "react"


class TestNotifyReactContract:
    """Test suite for notify.v1 contract validation of react intent."""

    _BASE_CTX = {
        "request_id": "01916b9d-1234-7000-abcd-123456789abc",
        "source_channel": "telegram_bot",
        "source_endpoint_identity": "test",
        "source_sender_identity": "user123",
        "source_thread_identity": "123:456",
    }

    def test_react_contract_validation_and_valid_payload(self) -> None:
        """emoji required; request_context required; source_thread_identity required; valid payload accepted."""
        # Missing emoji
        with pytest.raises(ValidationError) as exc_info:
            parse_notify_request({
                "schema_version": "notify.v1", "origin_butler": "health",
                "delivery": {"intent": "react", "channel": "telegram", "message": ""},
                "request_context": self._BASE_CTX,
            })
        assert "emoji" in str(exc_info.value).lower()

        # Missing request_context
        with pytest.raises(ValidationError) as exc_info:
            parse_notify_request({
                "schema_version": "notify.v1", "origin_butler": "health",
                "delivery": {"intent": "react", "channel": "telegram", "message": "", "emoji": "👍"},
            })
        assert "context" in str(exc_info.value).lower()

        # Missing source_thread_identity
        ctx_no_thread = {k: v for k, v in self._BASE_CTX.items() if k != "source_thread_identity"}
        with pytest.raises(ValidationError) as exc_info:
            parse_notify_request({
                "schema_version": "notify.v1", "origin_butler": "health",
                "delivery": {"intent": "react", "channel": "telegram", "message": "", "emoji": "👍"},
                "request_context": ctx_no_thread,
            })
        assert "thread" in str(exc_info.value).lower()

        # Valid payload accepted
        result = parse_notify_request({
            "schema_version": "notify.v1", "origin_butler": "health",
            "delivery": {"intent": "react", "channel": "telegram", "message": "", "emoji": "🎉"},
            "request_context": self._BASE_CTX,
        })
        assert result.delivery.intent == "react"
        assert result.delivery.emoji == "🎉"
        assert result.request_context.source_thread_identity == "123:456"
