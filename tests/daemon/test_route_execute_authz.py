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
    butler_name: str = "test_butler",
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
        'name = "butlers"',
        f'schema = "{butler_name}"',
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
    mock_pool.fetchval.return_value = None
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
        "db_from_env": patch("butlers.lifecycle.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.lifecycle.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.lifecycle.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.lifecycle.init_telemetry"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.lifecycle.FastMCP"),
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "get_adapter": patch("butlers.lifecycle.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
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
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
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
        "butlers.core_tools._routing.route_inbox_insert",
        AsyncMock(return_value=fake_inbox_id),
    )
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_mark_processing",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_mark_processed",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_mark_errored",
        AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Tests: unauthenticated/unauthorized callers are rejected
# ---------------------------------------------------------------------------


class TestRouteExecuteAuthz:
    """Verify route.execute authz: untrusted callers rejected; trusted callers allowed."""

    async def test_authz_reject_and_allow(self, tmp_path: Path) -> None:
        """Untrusted callers rejected with validation_error; trusted switchboard allowed."""
        # Messenger rejects rogue-caller
        patches = _patch_infra()
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            _make_butler_toml(tmp_path, butler_name="messenger", modules={"telegram": {}, "email": {}}),
            patches,
        )
        assert route_execute_fn is not None
        telegram_module = next(m for m in daemon._modules if m.name == "telegram")
        telegram_module._send_message = AsyncMock(return_value={"result": {"message_id": 999}})

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="rogue-caller"),
            input={"prompt": "Deliver.", "context": {"notify_request": _valid_notify_request()}},
        )
        telegram_module._send_message.assert_not_awaited()
        assert result["schema_version"] == "route_response.v1"
        assert result["status"] == "error"
        assert result["error"]["class"] == "validation_error"
        assert result["error"]["retryable"] is False
        assert "rogue-caller" in result["error"]["message"]

        # Empty/blank endpoint identity also rejected
        patches3 = _patch_infra()
        subdir3 = tmp_path / "h3"
        subdir3.mkdir()
        _, route_execute_fn3 = await _start_daemon_with_route_execute(
            _make_butler_toml(subdir3, butler_name="health"), patches3
        )
        result3 = await route_execute_fn3(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity=" "),
            input={"prompt": "Run health check."},
        )
        assert result3["status"] == "error"
        assert result3["error"]["class"] == "validation_error"

        # Switchboard caller allowed: messenger delivery proceeds; non-messenger trigger accepted
        patches_ok = _patch_infra()
        (tmp_path / "mess2").mkdir(exist_ok=True)
        daemon_ok, fn_ok = await _start_daemon_with_route_execute(
            _make_butler_toml(tmp_path / "mess2", butler_name="messenger", modules={"telegram": {}, "email": {}}),
            patches_ok,
        )
        tg_mod = next(m for m in daemon_ok._modules if m.name == "telegram")
        tg_mod._send_message = AsyncMock(return_value={"result": {"message_id": 321}})
        result_ok = await fn_ok(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="switchboard"),
            input={"prompt": "Deliver.", "context": {"notify_request": _valid_notify_request()}},
        )
        tg_mod._send_message.assert_awaited_once()
        assert result_ok["status"] == "ok"


# ---------------------------------------------------------------------------
# Tests: custom trusted_route_callers config
# ---------------------------------------------------------------------------


class TestRouteExecuteCustomTrustedCallers:
    """Verify custom trusted_route_callers from butler.toml is respected."""

    async def test_custom_trusted_callers(self, tmp_path: Path) -> None:
        """Custom list caller allowed; switchboard rejected when not in list; empty list rejects all."""
        # Custom caller in list → accepted
        patches = _patch_infra()
        d1 = tmp_path / "c1"
        d1.mkdir()
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            _make_butler_toml(d1, butler_name="health", trusted_route_callers=["switchboard", "heartbeat"]),
            patches,
        )
        mock_tr = MagicMock(output="ok", success=True, error=None, duration_ms=10)
        daemon.spawner.trigger = AsyncMock(return_value=mock_tr)
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="heartbeat"),
            input={"prompt": "Tick check."},
        )
        assert result["status"] == "accepted"

        # Switchboard excluded from custom list → rejected
        patches2 = _patch_infra()
        d2 = tmp_path / "c2"
        d2.mkdir()
        _, route_execute_fn2 = await _start_daemon_with_route_execute(
            _make_butler_toml(d2, butler_name="health", trusted_route_callers=["internal-only"]),
            patches2,
        )
        result2 = await route_execute_fn2(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="switchboard"),
            input={"prompt": "Run health check."},
        )
        assert result2["status"] == "error"
        assert result2["error"]["class"] == "validation_error"
        assert "switchboard" in result2["error"]["message"]

        # Empty list → everyone rejected
        patches3 = _patch_infra()
        d3 = tmp_path / "c3"
        d3.mkdir()
        _, route_execute_fn3 = await _start_daemon_with_route_execute(
            _make_butler_toml(d3, butler_name="health", trusted_route_callers=[]),
            patches3,
        )
        result3 = await route_execute_fn3(
            schema_version="route.v1",
            request_context=_route_request_context(source_endpoint_identity="switchboard"),
            input={"prompt": "Run health check."},
        )
        assert result3["status"] == "error"
        assert result3["error"]["class"] == "validation_error"


# ---------------------------------------------------------------------------
# Tests: config parsing for trusted_route_callers
# ---------------------------------------------------------------------------


class TestTrustedRouteCallersConfig:
    """Verify config parsing of [butler.security].trusted_route_callers."""

    def test_trusted_callers_config_parsing(self, tmp_path: Path) -> None:
        """Default is ('switchboard',); custom list parsed; empty gives ();
        non-list value raises ConfigError."""
        from butlers.config import ConfigError, load_config

        # Default
        assert load_config(_make_butler_toml(tmp_path, butler_name="test_butler")).trusted_route_callers == ("switchboard",)

        # Custom list
        d2 = tmp_path / "c2"
        d2.mkdir()
        cfg2 = load_config(_make_butler_toml(d2, butler_name="test_butler", trusted_route_callers=["switchboard", "heartbeat", "admin"]))
        assert cfg2.trusted_route_callers == ("switchboard", "heartbeat", "admin")

        # Empty list
        d3 = tmp_path / "c3"
        d3.mkdir()
        cfg3 = load_config(_make_butler_toml(d3, butler_name="test_butler", trusted_route_callers=[]))
        assert cfg3.trusted_route_callers == ()

        # Non-list raises ConfigError
        (tmp_path / "bad.toml").write_text("")
        d4 = tmp_path / "c4"
        d4.mkdir()
        (d4 / "butler.toml").write_text("""
[butler]
name = "test_butler"
port = 9100
description = "A test butler"

[butler.db]
name = "butlers"
schema = "test_butler"

[butler.security]
trusted_route_callers = "switchboard"

[[butler.schedule]]
name = "daily-check"
cron = "0 9 * * *"
prompt = "Do the daily check"
""")
        with pytest.raises(ConfigError, match=r"butler\.security\.trusted_route_callers must be a list of strings"):
            load_config(d4)
