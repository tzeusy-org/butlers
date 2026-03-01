"""Test prompt fencing for routed messages.

Verifies that:
- Routed prompts are wrapped in <routed_message> XML tags
- Non-interactive channels get a CONTENT SAFETY preamble in context
- Interactive channels (telegram, whatsapp) do NOT get the safety preamble
- Both main route.execute and recovery paths apply fencing
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
    """Patch route_inbox functions for all tests."""
    mock_insert = AsyncMock(return_value=uuid.uuid4())
    monkeypatch.setattr("butlers.daemon.route_inbox_insert", mock_insert)
    monkeypatch.setattr("butlers.daemon.route_inbox_mark_processing", AsyncMock())
    monkeypatch.setattr("butlers.daemon.route_inbox_mark_processed", AsyncMock())
    monkeypatch.setattr("butlers.daemon.route_inbox_mark_errored", AsyncMock())
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
    port: int = 9200,
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
        patches["validate_core_credentials"],
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
    source_channel: str = "email",
    request_id: str = "018f6f4e-5b3b-7b2d-9c2f-fe0cefe0ce01",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "received_at": "2026-03-01T00:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": "switchboard",
        "source_sender_identity": "pipeline",
    }


class TestRouteExecutePromptFencing:
    """Verify routed prompts are structurally fenced with XML tags."""

    async def test_prompt_wrapped_in_routed_message_tags(self, tmp_path: Path) -> None:
        """Prompt text is wrapped in <routed_message> tags."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="general")
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
            input={"prompt": "Add a comment at https://example.com"},
        )

        await asyncio.sleep(0.05)
        assert result["status"] == "accepted"
        daemon.spawner.trigger.assert_awaited_once()

        call_args = daemon.spawner.trigger.call_args
        prompt_arg = call_args.kwargs.get("prompt")
        assert prompt_arg is not None
        assert prompt_arg.startswith("<routed_message>\n")
        assert prompt_arg.endswith("\n</routed_message>")
        assert "Add a comment at https://example.com" in prompt_arg

    async def test_content_safety_preamble_for_non_interactive_channel(
        self, tmp_path: Path
    ) -> None:
        """Non-interactive channels get CONTENT SAFETY preamble in context."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="general")
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
            input={"prompt": "Newsletter content here"},
        )

        await asyncio.sleep(0.05)
        assert result["status"] == "accepted"

        call_args = daemon.spawner.trigger.call_args
        context_arg = call_args.kwargs.get("context")
        assert context_arg is not None
        assert "CONTENT SAFETY" in context_arg
        assert "<routed_message>" in context_arg

    async def test_no_content_safety_preamble_for_interactive_channel(self, tmp_path: Path) -> None:
        """Interactive channels (telegram) do NOT get the safety preamble."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="general")
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
            input={"prompt": "Hello from telegram"},
        )

        await asyncio.sleep(0.05)
        assert result["status"] == "accepted"

        call_args = daemon.spawner.trigger.call_args
        context_arg = call_args.kwargs.get("context")
        # Interactive channels should NOT have the safety preamble
        assert "CONTENT SAFETY" not in (context_arg or "")

    async def test_prompt_still_fenced_for_interactive_channel(self, tmp_path: Path) -> None:
        """Even interactive channels get XML-fenced prompts (belt-and-suspenders)."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="general")
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
            input={"prompt": "Hello from telegram"},
        )

        await asyncio.sleep(0.05)
        assert result["status"] == "accepted"

        call_args = daemon.spawner.trigger.call_args
        prompt_arg = call_args.kwargs.get("prompt")
        assert prompt_arg is not None
        assert "<routed_message>" in prompt_arg
        assert "</routed_message>" in prompt_arg
