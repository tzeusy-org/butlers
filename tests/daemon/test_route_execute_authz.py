"""Regression tests for route.execute authn/authz guardrails.

Verifies that:
- Unauthenticated callers (unknown source_endpoint_identity) are rejected.
- Only trusted control-plane callers (default: Switchboard) can invoke
  route.execute on both messenger and non-messenger butlers.
- Custom trusted_route_callers config is respected.
- Authorized callers pass through normally.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _toml_value(v: Any) -> str:
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        items = ", ".join(f'"{i}"' if isinstance(i, str) else str(i) for i in v)
        return f"[{items}]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _make_butler_toml(
    tmp_path: Path,
    *,
    butler_name: str = "test-butler",
    port: int = 9100,
    modules: dict[str, dict] | None = None,
    trusted_route_callers: list[str] | None = None,
) -> Path:
    modules = modules or {}
    toml_lines = [
        "[butler]",
        f'name = "{butler_name}"',
        f"port = {port}",
        'description = "A test butler"',
        "",
        "[butler.db]",
        f'name = "butler_{butler_name}"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    if trusted_route_callers is not None:
        toml_lines.append("")
        toml_lines.append("[butler.security]")
        items = ", ".join(f'"{c}"' for c in trusted_route_callers)
        toml_lines.append(f"trusted_route_callers = [{items}]")
    for mod_name, mod_cfg in modules.items():
        toml_lines.append(f"\n[modules.{mod_name}]")
        for k, v in mod_cfg.items():
            toml_lines.append(f"{k} = {_toml_value(v)}")
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _patch_infra():
    mock_pool = AsyncMock()
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
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


async def _start_daemon_with_route_execute(butler_dir: Path, patches: dict):
    """Boot a daemon and capture the route.execute handler function."""
    route_execute_fn = None
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            nonlocal route_execute_fn
            resolved_name = declared_name or fn.__name__
            if resolved_name == "route.execute":
                route_execute_fn = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp),
        patches["Spawner"],
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    return daemon, route_execute_fn


def _route_request_context(
    *,
    source_endpoint_identity: str = "switchboard",
    source_sender_identity: str = "health",
) -> dict[str, Any]:
    return {
        "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
        "received_at": "2026-02-14T00:00:00Z",
        "source_channel": "mcp",
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }


def _valid_notify_request(*, origin_butler: str = "health") -> dict[str, Any]:
    return {
        "schema_version": "notify.v1",
        "origin_butler": origin_butler,
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": "Take your medication.",
            "recipient": "12345",
        },
    }


@pytest.fixture(autouse=True)
def _mock_route_inbox(monkeypatch):
    """Patch route_inbox DB calls so tests don't need a real DB pool."""
    fake_inbox_id = uuid.uuid4()
    monkeypatch.setattr(
        "butlers.daemon.route_inbox_insert",
        AsyncMock(return_value=fake_inbox_id),
    )
    monkeypatch.setattr(
        "butlers.daemon.route_inbox_mark_processing",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "butlers.daemon.route_inbox_mark_processed",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "butlers.daemon.route_inbox_mark_errored",
        AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Tests: unauthenticated/unauthorized callers are rejected
# ---------------------------------------------------------------------------


class TestRouteExecuteAuthzRejectsUntrustedCallers:
    """Verify that route.execute rejects callers not in trusted_route_callers."""

    async def test_unauthenticated_caller_rejected_on_messenger(self, tmp_path: Path) -> None:
        """Messenger butler rejects route.execute from unknown endpoint identity."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(
            tmp_path, butler_name="messenger", modules={"telegram": {}, "email": {}}
        )
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        telegram_module = next(m for m in daemon._modules if m.name == "telegram")
        telegram_module._send_message = AsyncMock(return_value={"result": {"message_id": 999}})

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(
                source_endpoint_identity="rogue-caller",
            ),
            input={
                "prompt": "Deliver.",
                "context": {
                    "notify_request": _valid_notify_request(),
                },
            },
        )

        # Must reject before any delivery side effect
        telegram_module._send_message.assert_not_awaited()
        assert result["schema_version"] == "route_response.v1"
        assert result["status"] == "error"
        assert result["error"]["class"] == "validation_error"
        assert result["error"]["retryable"] is False
        assert "rogue-caller" in result["error"]["message"]
        assert "trusted_route_callers" in result["error"]["message"]

    async def test_unauthenticated_caller_rejected_on_non_messenger(self, tmp_path: Path) -> None:
        """Non-messenger butler also rejects untrusted callers."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        _, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(
                source_endpoint_identity="unknown-origin",
            ),
            input={"prompt": "Run health check."},
        )

        assert result["status"] == "error"
        assert result["error"]["class"] == "validation_error"
        assert "unknown-origin" in result["error"]["message"]

    async def test_empty_string_endpoint_identity_rejected(self, tmp_path: Path) -> None:
        """Empty endpoint identity fails envelope validation before authz."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        _, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        # Empty string should fail the NonEmptyStr validation in the
        # route envelope before reaching authz; either way it's rejected.
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(
                source_endpoint_identity=" ",
            ),
            input={"prompt": "Run health check."},
        )

        assert result["status"] == "error"
        assert result["error"]["class"] == "validation_error"


# ---------------------------------------------------------------------------
# Tests: authorized callers pass through
# ---------------------------------------------------------------------------


class TestRouteExecuteAuthzAllowsTrustedCallers:
    """Verify that trusted callers are allowed through."""

    async def test_switchboard_caller_allowed_by_default(self, tmp_path: Path) -> None:
        """Default trusted_route_callers includes switchboard."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(
            tmp_path, butler_name="messenger", modules={"telegram": {}, "email": {}}
        )
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        telegram_module = next(m for m in daemon._modules if m.name == "telegram")
        telegram_module._send_message = AsyncMock(return_value={"result": {"message_id": 321}})

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(
                source_endpoint_identity="switchboard",
            ),
            input={
                "prompt": "Deliver.",
                "context": {
                    "notify_request": _valid_notify_request(),
                },
            },
        )

        telegram_module._send_message.assert_awaited_once()
        assert result["status"] == "ok"

    async def test_non_messenger_trigger_with_switchboard_succeeds(self, tmp_path: Path) -> None:
        """Non-messenger butler routes via trigger when switchboard calls."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        mock_trigger_result = MagicMock()
        mock_trigger_result.output = "done"
        mock_trigger_result.success = True
        mock_trigger_result.error = None
        mock_trigger_result.duration_ms = 42
        daemon.spawner.trigger = AsyncMock(return_value=mock_trigger_result)

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(
                source_endpoint_identity="switchboard",
            ),
            input={"prompt": "Run health check."},
        )

        assert result["status"] == "accepted"
        assert "inbox_id" in result


# ---------------------------------------------------------------------------
# Tests: custom trusted_route_callers config
# ---------------------------------------------------------------------------


class TestRouteExecuteCustomTrustedCallers:
    """Verify custom trusted_route_callers from butler.toml is respected."""

    async def test_custom_trusted_caller_allowed(self, tmp_path: Path) -> None:
        """A caller listed in custom trusted_route_callers is accepted."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(
            tmp_path,
            butler_name="health",
            trusted_route_callers=["switchboard", "heartbeat"],
        )
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        mock_trigger_result = MagicMock()
        mock_trigger_result.output = "ok"
        mock_trigger_result.success = True
        mock_trigger_result.error = None
        mock_trigger_result.duration_ms = 10
        daemon.spawner.trigger = AsyncMock(return_value=mock_trigger_result)

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(
                source_endpoint_identity="heartbeat",
            ),
            input={"prompt": "Tick check."},
        )

        assert result["status"] == "accepted"

    async def test_switchboard_rejected_when_not_in_custom_list(self, tmp_path: Path) -> None:
        """Even switchboard is rejected if excluded from custom config."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(
            tmp_path,
            butler_name="health",
            trusted_route_callers=["internal-only"],
        )
        _, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(
                source_endpoint_identity="switchboard",
            ),
            input={"prompt": "Run health check."},
        )

        assert result["status"] == "error"
        assert result["error"]["class"] == "validation_error"
        assert "switchboard" in result["error"]["message"]

    async def test_empty_trusted_callers_rejects_everyone(self, tmp_path: Path) -> None:
        """An empty trusted_route_callers list rejects all callers."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(
            tmp_path,
            butler_name="health",
            trusted_route_callers=[],
        )
        _, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(
                source_endpoint_identity="switchboard",
            ),
            input={"prompt": "Run health check."},
        )

        assert result["status"] == "error"
        assert result["error"]["class"] == "validation_error"


# ---------------------------------------------------------------------------
# Tests: config parsing for trusted_route_callers
# ---------------------------------------------------------------------------


class TestTrustedRouteCallersConfig:
    """Verify config parsing of [butler.security].trusted_route_callers."""

    def test_default_trusted_callers_is_switchboard(self, tmp_path: Path) -> None:
        """Without explicit config, trusted_route_callers defaults to ('switchboard',)."""
        from butlers.config import load_config

        butler_dir = _make_butler_toml(tmp_path, butler_name="test-butler")
        config = load_config(butler_dir)
        assert config.trusted_route_callers == ("switchboard",)

    def test_custom_trusted_callers_parsed(self, tmp_path: Path) -> None:
        """Explicit trusted_route_callers in config is parsed correctly."""
        from butlers.config import load_config

        butler_dir = _make_butler_toml(
            tmp_path,
            butler_name="test-butler",
            trusted_route_callers=["switchboard", "heartbeat", "admin"],
        )
        config = load_config(butler_dir)
        assert config.trusted_route_callers == ("switchboard", "heartbeat", "admin")

    def test_empty_trusted_callers_list(self, tmp_path: Path) -> None:
        """Empty list produces an empty tuple."""
        from butlers.config import load_config

        butler_dir = _make_butler_toml(
            tmp_path,
            butler_name="test-butler",
            trusted_route_callers=[],
        )
        config = load_config(butler_dir)
        assert config.trusted_route_callers == ()

    def test_invalid_trusted_callers_type_raises(self, tmp_path: Path) -> None:
        """Non-list value for trusted_route_callers raises ConfigError."""
        from butlers.config import ConfigError, load_config

        toml_content = """
[butler]
name = "test-butler"
port = 9100
description = "A test butler"

[butler.db]
name = "butler_test"

[butler.security]
trusted_route_callers = "switchboard"

[[butler.schedule]]
name = "daily-check"
cron = "0 9 * * *"
prompt = "Do the daily check"
"""
        (tmp_path / "butler.toml").write_text(toml_content)
        with pytest.raises(
            ConfigError,
            match=r"butler\.security\.trusted_route_callers must be a list of strings",
        ):
            load_config(tmp_path)
