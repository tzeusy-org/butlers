"""Test request_context injection into CC session context.

Verifies that:
- CC sessions spawned from route.execute receive request_context data
- request_context includes all routing metadata fields
- Non-route triggers (tick, schedule) are unaffected
- Both dict and string input.context values are preserved
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


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
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    return daemon, route_execute_fn


def _route_request_context(
    *,
    source_endpoint_identity: str = "switchboard",
    source_sender_identity: str = "health",
    source_channel: str = "telegram",
    source_thread_identity: str | None = "12345",
    request_id: str = "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "request_id": request_id,
        "received_at": "2026-02-14T00:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }
    if source_thread_identity is not None:
        ctx["source_thread_identity"] = source_thread_identity
    return ctx


class TestRouteExecuteRequestContextInjection:
    """Verify that request_context is injected into CC session context."""

    async def test_request_context_injected_into_spawner_context(self, tmp_path: Path) -> None:
        """Request context is prepended to spawner context for routes."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        mock_trigger_result = MagicMock()
        mock_trigger_result.output = "ok"
        mock_trigger_result.success = True
        mock_trigger_result.error = None
        mock_trigger_result.duration_ms = 10
        daemon.spawner.trigger = AsyncMock(return_value=mock_trigger_result)

        request_ctx = _route_request_context(
            source_channel="telegram",
            source_thread_identity="98765",
            source_sender_identity="user123",
            request_id="018f6f4e-5b3b-7b2d-9c2f-aaaaaabbbbbb",
        )

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=request_ctx,
            input={"prompt": "Run health check."},
        )

        assert result["status"] == "ok"
        daemon.spawner.trigger.assert_awaited_once()

        # Extract the context argument passed to spawner.trigger
        call_args = daemon.spawner.trigger.call_args
        assert call_args is not None
        context_arg = call_args.kwargs.get("context")

        # Verify request_context is in the context
        assert context_arg is not None
        assert "REQUEST CONTEXT" in context_arg
        assert "018f6f4e-5b3b-7b2d-9c2f-aaaaaabbbbbb" in context_arg
        assert "telegram" in context_arg
        assert "98765" in context_arg
        assert "user123" in context_arg

    async def test_request_context_with_dict_input_context(self, tmp_path: Path) -> None:
        """Both request_context and input.context (dict) are in context."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
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
            request_context=_route_request_context(),
            input={
                "prompt": "Check vital signs.",
                "context": {
                    "patient_id": "patient-456",
                    "visit_date": "2026-02-14",
                },
            },
        )

        assert result["status"] == "ok"
        call_args = daemon.spawner.trigger.call_args
        context_arg = call_args.kwargs.get("context")

        # Verify both REQUEST CONTEXT and INPUT CONTEXT are present
        assert "REQUEST CONTEXT" in context_arg
        assert "INPUT CONTEXT" in context_arg
        assert "patient-456" in context_arg
        assert "2026-02-14" in context_arg

    async def test_request_context_with_string_input_context(self, tmp_path: Path) -> None:
        """Both request_context and input.context (string) are in context."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
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
            request_context=_route_request_context(),
            input={
                "prompt": "Check vital signs.",
                "context": "Previous reading: BP 120/80, HR 72",
            },
        )

        assert result["status"] == "ok"
        call_args = daemon.spawner.trigger.call_args
        context_arg = call_args.kwargs.get("context")

        # Verify both REQUEST CONTEXT and INPUT CONTEXT are present
        assert "REQUEST CONTEXT" in context_arg
        assert "INPUT CONTEXT" in context_arg
        assert "Previous reading: BP 120/80, HR 72" in context_arg

    async def test_request_context_only_when_no_input_context(self, tmp_path: Path) -> None:
        """Request context is injected when input.context is absent."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
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
            request_context=_route_request_context(source_channel="email"),
            input={"prompt": "Check vital signs."},
        )

        assert result["status"] == "ok"
        call_args = daemon.spawner.trigger.call_args
        context_arg = call_args.kwargs.get("context")

        # Verify REQUEST CONTEXT is present but INPUT CONTEXT is not
        assert "REQUEST CONTEXT" in context_arg
        assert "email" in context_arg
        assert "INPUT CONTEXT" not in context_arg

    async def test_request_context_preserves_all_fields(self, tmp_path: Path) -> None:
        """All request_context fields are preserved in spawner context."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        mock_trigger_result = MagicMock()
        mock_trigger_result.output = "ok"
        mock_trigger_result.success = True
        mock_trigger_result.error = None
        mock_trigger_result.duration_ms = 10
        daemon.spawner.trigger = AsyncMock(return_value=mock_trigger_result)

        request_ctx = _route_request_context(
            source_channel="telegram",
            source_thread_identity="thread-999",
            source_sender_identity="sender-888",
            source_endpoint_identity="switchboard",
            request_id="018f6f4e-5b3b-7b2d-9c2f-ccccccdddddd",
        )

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=request_ctx,
            input={"prompt": "Check status."},
        )

        assert result["status"] == "ok"
        call_args = daemon.spawner.trigger.call_args
        context_arg = call_args.kwargs.get("context")

        # Parse the JSON from the context to verify structure
        assert "REQUEST CONTEXT" in context_arg
        lines = context_arg.split("\n")
        json_start = None
        for i, line in enumerate(lines):
            if line.strip().startswith("{"):
                json_start = i
                break

        assert json_start is not None
        # Find the end of the JSON block
        json_lines = []
        brace_count = 0
        for i in range(json_start, len(lines)):
            json_lines.append(lines[i])
            brace_count += lines[i].count("{") - lines[i].count("}")
            if brace_count == 0:
                break

        json_text = "\n".join(json_lines)
        parsed_ctx = json.loads(json_text)

        # Verify all fields are present
        assert parsed_ctx["request_id"] == "018f6f4e-5b3b-7b2d-9c2f-ccccccdddddd"
        assert parsed_ctx["source_channel"] == "telegram"
        assert parsed_ctx["source_thread_identity"] == "thread-999"
        assert parsed_ctx["source_sender_identity"] == "sender-888"
        assert parsed_ctx["source_endpoint_identity"] == "switchboard"
        assert "received_at" in parsed_ctx

    async def test_interactive_channel_injects_guidance_for_telegram(self, tmp_path: Path) -> None:
        """Telegram source_channel triggers INTERACTIVE DATA SOURCE block."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
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
            request_context=_route_request_context(source_channel="telegram"),
            input={"prompt": "Track medication."},
        )

        assert result["status"] == "ok"
        context_arg = daemon.spawner.trigger.call_args.kwargs.get("context")
        assert "INTERACTIVE DATA SOURCE" in context_arg
        assert 'channel="telegram"' in context_arg
        assert 'intent="reply"' in context_arg
        assert "notify()" in context_arg

    async def test_interactive_channel_injects_guidance_for_email(self, tmp_path: Path) -> None:
        """Email source_channel triggers INTERACTIVE DATA SOURCE block."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
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
            request_context=_route_request_context(source_channel="email"),
            input={"prompt": "Check inbox."},
        )

        assert result["status"] == "ok"
        context_arg = daemon.spawner.trigger.call_args.kwargs.get("context")
        assert "INTERACTIVE DATA SOURCE" in context_arg
        assert 'channel="email"' in context_arg

    async def test_non_interactive_channel_omits_guidance(self, tmp_path: Path) -> None:
        """MCP source_channel does NOT inject INTERACTIVE DATA SOURCE block."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
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
            request_context=_route_request_context(source_channel="mcp"),
            input={"prompt": "Internal check."},
        )

        assert result["status"] == "ok"
        context_arg = daemon.spawner.trigger.call_args.kwargs.get("context")
        assert "INTERACTIVE DATA SOURCE" not in context_arg
