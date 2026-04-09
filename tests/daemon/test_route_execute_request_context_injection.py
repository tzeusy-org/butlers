"""Test request_context injection into runtime session context.

Verifies that:
- Runtime sessions spawned from route.execute receive request_context data
- request_context includes all routing metadata fields
- Non-route triggers (tick, schedule) are unaffected
- Both dict and string input.context values are preserved

Note: route.execute now uses accept-then-process async dispatch (butlers-963.6).
Tests mock route_inbox_insert/mark_processing/mark_processed and await asyncio.sleep(0.05)
before checking trigger call_args to allow the background task to complete.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _mock_route_inbox(monkeypatch):
    """Patch route_inbox functions for all tests in this module.

    Prevents tests from hitting the mock DB pool via route_inbox_insert and
    allows the background trigger task to complete cleanly.
    """
    mock_insert = AsyncMock(return_value=uuid.uuid4())
    mock_mark_processing = AsyncMock()
    mock_mark_processed = AsyncMock()
    mock_mark_errored = AsyncMock()
    monkeypatch.setattr("butlers.core_tools._routing.route_inbox_insert", mock_insert)
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_mark_processing", mock_mark_processing
    )
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_mark_processed", mock_mark_processed
    )
    monkeypatch.setattr("butlers.core_tools._routing.route_inbox_mark_errored", mock_mark_errored)
    return mock_insert


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
        'name = "butlers"',
        f'schema = "{butler_name}"',
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
        "db_from_env": patch("butlers.daemon.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
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
        patches["validate_module_credentials"],
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
    source_channel: str = "telegram_bot",
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


def _mock_trigger_result() -> MagicMock:
    """Return a mock trigger result indicating success."""
    r = MagicMock()
    r.output = "ok"
    r.success = True
    r.error = None
    r.duration_ms = 10
    return r


async def _call_route_execute(daemon, route_execute_fn, *, request_context, input_data) -> str:
    """Call route_execute_fn, wait for background task, return context_arg."""
    daemon.spawner.trigger = AsyncMock(return_value=_mock_trigger_result())
    result = await route_execute_fn(
        schema_version="route.v1",
        request_context=request_context,
        input=input_data,
    )
    await asyncio.sleep(0.05)
    assert result["status"] == "accepted"
    return daemon.spawner.trigger.call_args.kwargs.get("context")


class TestRouteExecuteRequestContextInjection:
    """Verify that request_context is injected into runtime session context."""

    async def test_request_context_and_input_context(self, tmp_path: Path) -> None:
        """REQUEST CONTEXT injected with all fields; INPUT CONTEXT section for both dict and string;
        telegram channel injects INTERACTIVE DATA SOURCE; MCP does not."""
        patches = _patch_infra()
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            _make_butler_toml(tmp_path, butler_name="health"), patches
        )
        assert route_execute_fn is not None

        # Basic injection: REQUEST CONTEXT present with key fields
        ctx_arg = await _call_route_execute(
            daemon,
            route_execute_fn,
            request_context=_route_request_context(
                source_channel="telegram_bot",
                source_thread_identity="98765",
                source_sender_identity="user123",
                request_id="018f6f4e-5b3b-7b2d-9c2f-aaaaaabbbbbb",
            ),
            input_data={"prompt": "Run health check."},
        )
        assert "REQUEST CONTEXT" in ctx_arg
        assert "018f6f4e-5b3b-7b2d-9c2f-aaaaaabbbbbb" in ctx_arg
        assert "telegram" in ctx_arg
        assert "user123" in ctx_arg
        assert "INPUT CONTEXT" not in ctx_arg

        # Dict input.context → INPUT CONTEXT section
        ctx_arg2 = await _call_route_execute(
            daemon,
            route_execute_fn,
            request_context=_route_request_context(),
            input_data={"prompt": "Check.", "context": {"patient_id": "patient-456"}},
        )
        assert "INPUT CONTEXT" in ctx_arg2
        assert "patient-456" in ctx_arg2

        # String input.context → INPUT CONTEXT section
        ctx_arg3 = await _call_route_execute(
            daemon,
            route_execute_fn,
            request_context=_route_request_context(),
            input_data={"prompt": "Check.", "context": "Previous reading: BP 120/80"},
        )
        assert "INPUT CONTEXT" in ctx_arg3
        assert "Previous reading: BP 120/80" in ctx_arg3

        # Telegram: INTERACTIVE DATA SOURCE injected
        ctx_tg = await _call_route_execute(
            daemon,
            route_execute_fn,
            request_context=_route_request_context(source_channel="telegram_bot"),
            input_data={"prompt": "Track medication."},
        )
        assert "INTERACTIVE DATA SOURCE" in ctx_tg
        assert "notify()" in ctx_tg

        # MCP: no INTERACTIVE DATA SOURCE
        ctx_mcp = await _call_route_execute(
            daemon,
            route_execute_fn,
            request_context=_route_request_context(source_channel="mcp"),
            input_data={"prompt": "Internal check."},
        )
        assert "INTERACTIVE DATA SOURCE" not in ctx_mcp

    async def test_conversation_history(self, tmp_path: Path) -> None:
        """Conversation history injected under CONVERSATION HISTORY before INPUT CONTEXT;
        absent/empty/None → no CONVERSATION HISTORY section."""
        patches = _patch_infra()
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            _make_butler_toml(tmp_path, butler_name="health"), patches
        )
        assert route_execute_fn is not None

        history_text = "**user123** (2026-02-16T10:00:00Z):\nTrack my metformin 500mg twice daily"

        ctx_arg = await _call_route_execute(
            daemon,
            route_execute_fn,
            request_context=_route_request_context(source_channel="telegram_bot"),
            input_data={
                "prompt": "When?",
                "context": "Extra context",
                "conversation_history": history_text,
            },
        )
        assert "CONVERSATION HISTORY" in ctx_arg
        assert "metformin 500mg" in ctx_arg
        history_pos = ctx_arg.find("CONVERSATION HISTORY")
        input_ctx_pos = ctx_arg.find("INPUT CONTEXT")
        assert history_pos < input_ctx_pos

        # None → no CONVERSATION HISTORY
        ctx_no = await _call_route_execute(
            daemon,
            route_execute_fn,
            request_context=_route_request_context(source_channel="telegram_bot"),
            input_data={"prompt": "Do something.", "conversation_history": None},
        )
        assert "CONVERSATION HISTORY" not in ctx_no
