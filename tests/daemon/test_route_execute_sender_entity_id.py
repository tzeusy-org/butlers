"""Tests for sender entity_id injection via route.execute.

Verifies that when route.execute receives a request_context with
source_sender_entity_id, the value is captured and injected into
_routing_ctx_var before spawner.trigger() is called, so that
memory_store_fact can use it as the default entity_id.
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


# ---------------------------------------------------------------------------
# Helpers (shared with other route_execute test modules)
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
    butler_name: str = "health",
    port: int = 9700,
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


@pytest.fixture(autouse=True)
def _mock_route_inbox(monkeypatch):
    """Patch route_inbox functions to avoid DB calls in all tests here."""
    mock_insert = AsyncMock(return_value=uuid.uuid4())
    mock_mark_processing = AsyncMock()
    mock_mark_processed = AsyncMock()
    mock_mark_errored = AsyncMock()
    monkeypatch.setattr("butlers.daemon.route_inbox_insert", mock_insert)
    monkeypatch.setattr("butlers.daemon.route_inbox_mark_processing", mock_mark_processing)
    monkeypatch.setattr("butlers.daemon.route_inbox_mark_processed", mock_mark_processed)
    monkeypatch.setattr("butlers.daemon.route_inbox_mark_errored", mock_mark_errored)
    return mock_insert


def _base_request_context(
    *,
    source_sender_entity_id: str | None = None,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "request_id": "018f6f4e-5b3b-7b2d-9c2f-aabbccddee00",
        "received_at": "2026-03-10T00:00:00Z",
        "source_channel": "telegram_bot",
        "source_endpoint_identity": "switchboard",
        "source_sender_identity": "owner",
        "source_thread_identity": "12345",
    }
    if source_sender_entity_id is not None:
        ctx["source_sender_entity_id"] = source_sender_entity_id
    return ctx


class TestRouteExecuteSenderEntityIdInjection:
    """Verify that source_sender_entity_id is injected into _routing_ctx_var."""

    async def test_sender_entity_id_injected_into_routing_ctx_var(self, tmp_path: Path) -> None:
        """_routing_ctx_var is set with source_entity_id when source_sender_entity_id present."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        sender_entity_id = "550e8400-e29b-41d4-a716-446655440000"
        captured_routing_ctx: list[Any] = []

        mock_trigger_result = MagicMock()
        mock_trigger_result.output = "ok"
        mock_trigger_result.success = True
        mock_trigger_result.error = None
        mock_trigger_result.duration_ms = 10

        async def _capture_and_trigger(*args, **kwargs):
            from butlers.modules.pipeline import _routing_ctx_var

            captured_routing_ctx.append(_routing_ctx_var.get())
            return mock_trigger_result

        daemon.spawner.trigger = _capture_and_trigger

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_base_request_context(source_sender_entity_id=sender_entity_id),
            input={"prompt": "Store some info about me."},
        )
        await asyncio.sleep(0.05)

        assert result["status"] == "accepted"
        assert len(captured_routing_ctx) == 1
        ctx = captured_routing_ctx[0]
        assert ctx is not None
        assert isinstance(ctx, dict)
        assert ctx.get("source_entity_id") == sender_entity_id

    async def test_no_sender_entity_id_leaves_routing_ctx_unset(self, tmp_path: Path) -> None:
        """When source_sender_entity_id is absent, _routing_ctx_var is not set by route."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        captured_routing_ctx: list[Any] = []

        mock_trigger_result = MagicMock()
        mock_trigger_result.output = "ok"
        mock_trigger_result.success = True
        mock_trigger_result.error = None
        mock_trigger_result.duration_ms = 10

        async def _capture_and_trigger(*args, **kwargs):
            from butlers.modules.pipeline import _routing_ctx_var

            captured_routing_ctx.append(_routing_ctx_var.get())
            return mock_trigger_result

        daemon.spawner.trigger = _capture_and_trigger

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_base_request_context(),  # no entity_id
            input={"prompt": "Just a message."},
        )
        await asyncio.sleep(0.05)

        assert result["status"] == "accepted"
        assert len(captured_routing_ctx) == 1
        # When no entity_id, routing context should be None (not set by route.execute)
        assert captured_routing_ctx[0] is None
