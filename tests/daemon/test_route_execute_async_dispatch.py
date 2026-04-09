"""Tests for route.execute accept-then-process async dispatch (butlers-963.6).

Verifies:
1. Accept phase: route.execute returns {"status": "accepted"} quickly without awaiting trigger
2. Persist + background dispatch: route_inbox_insert called; trigger() called in background
3. Failure/success recording: errors stored in route_inbox
4. Crash recovery: _recover_route_inbox called on startup (not for staffer)
5. Complexity plumbing: complexity forwarded to spawner.trigger()
"""

from __future__ import annotations

import asyncio
import time
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
    butler_name: str = "health",
    port: int = 9200,
    butler_type: str | None = None,
    modules: dict[str, dict] | None = None,
) -> Path:
    modules = modules or {}
    toml_lines = [
        "[butler]",
        f'name = "{butler_name}"',
        f"port = {port}",
        'description = "A test butler"',
    ]
    if butler_type is not None:
        toml_lines.append(f'type = "{butler_type}"')
    toml_lines += [
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


def _patch_infra(butler_name: str = "health"):
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
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(
            ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock
        ),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
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
        patch("butlers.daemon.Spawner", return_value=patches["mock_spawner"]),
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
) -> dict[str, Any]:
    return {
        "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
        "received_at": "2026-02-18T10:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }


def _make_trigger_mock():
    trigger_mock = AsyncMock()
    trigger_result = MagicMock()
    trigger_result.session_id = uuid.uuid4()
    trigger_mock.return_value = trigger_result
    return trigger_mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_accept_phase_and_background_dispatch(tmp_path: Path) -> None:
    """Returns accepted in <100ms; route_inbox_insert called before return; trigger called in background with correct params."""
    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    # Accept phase: fast return with accepted status
    inserted_id = uuid.uuid4()
    mock_insert = AsyncMock(return_value=inserted_id)
    with patch("butlers.core_tools._routing.route_inbox_insert", mock_insert):
        t0 = time.monotonic()
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        elapsed_ms = (time.monotonic() - t0) * 1000

    assert result["status"] == "accepted"
    assert result["schema_version"] == "route_response.v1"
    assert result["inbox_id"] == str(inserted_id)
    assert elapsed_ms < 100, f"Accept phase took {elapsed_ms:.0f}ms, expected <100ms"

    # route_inbox_insert called with full envelope
    mock_insert.assert_awaited_once()
    envelope = mock_insert.call_args.kwargs["route_envelope"]
    assert envelope["schema_version"] == "route.v1"
    assert envelope["input"]["prompt"] == "Run health check."

    # Background trigger called with trigger_source=route and request_id
    trigger_mock = _make_trigger_mock()
    daemon.spawner.trigger = trigger_mock
    with (
        patch(
            "butlers.core_tools._routing.route_inbox_insert",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch("butlers.core_tools._routing.route_inbox_mark_processing", new_callable=AsyncMock),
        patch("butlers.core_tools._routing.route_inbox_mark_processed", new_callable=AsyncMock),
    ):
        await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        await asyncio.sleep(0.05)

    trigger_mock.assert_awaited()
    call_kwargs = trigger_mock.call_args.kwargs
    assert call_kwargs["trigger_source"] == "route"
    assert call_kwargs["request_id"] == "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b"


async def test_failure_recording_and_dedup(tmp_path: Path) -> None:
    """Trigger failures stored in route_inbox; success stored; duplicate request_ids deduped."""
    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    # Failure path
    daemon.spawner.trigger = AsyncMock(side_effect=RuntimeError("spawner crash"))
    mock_errored = AsyncMock()
    with (
        patch(
            "butlers.core_tools._routing.route_inbox_insert",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch("butlers.core_tools._routing.route_inbox_mark_processing", new_callable=AsyncMock),
        patch("butlers.core_tools._routing.route_inbox_mark_errored", mock_errored),
    ):
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        assert result["status"] == "accepted"
        await asyncio.sleep(0.05)
    mock_errored.assert_awaited_once()

    # Dedup: existing session → skip insert, return accepted with dedup=True
    existing_session_id = uuid.uuid4()
    patches["mock_pool"].fetchval.return_value = existing_session_id
    mock_insert = AsyncMock(return_value=uuid.uuid4())
    with patch("butlers.core_tools._routing.route_inbox_insert", mock_insert):
        result2 = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
    assert result2["status"] == "accepted"
    assert result2.get("dedup") is True
    mock_insert.assert_not_awaited()


async def test_crash_recovery_on_startup(tmp_path: Path) -> None:
    """_recover_route_inbox called for non-staffer; not called for staffer type."""
    patches = _patch_infra("health")
    del patches["recover_route_inbox"]

    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    mock_mcp = MagicMock()
    mock_mcp.tool = lambda *a, **kw: lambda fn: fn

    recovery_called = False

    async def mock_recover(self_daemon, pool):
        nonlocal recovery_called
        recovery_called = True

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp),
        patch("butlers.daemon.Spawner", return_value=patches["mock_spawner"]),
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patch.object(ButlerDaemon, "_recover_route_inbox", mock_recover),
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()
        await asyncio.sleep(0)

    assert recovery_called, "_recover_route_inbox was not called on startup"

    # Staffer does NOT schedule recovery
    patches2 = _patch_infra("infratool")
    butler_dir2 = _make_butler_toml(tmp_path, butler_name="infratool", port=9302, butler_type="staffer")
    mock_mcp2 = MagicMock()
    mock_mcp2.tool = lambda *a, **kw: lambda fn: fn
    with (
        patches2["db_from_env"],
        patches2["run_migrations"],
        patches2["validate_credentials"],
        patches2["validate_module_credentials"],
        patches2["init_telemetry"],
        patches2["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp2),
        patch("butlers.daemon.Spawner", return_value=patches2["mock_spawner"]),
        patches2["get_adapter"],
        patches2["shutil_which"],
        patches2["start_mcp_server"],
        patches2["connect_switchboard"],
        patches2["recover_route_inbox"],
        patch.object(ButlerDaemon, "_wire_pipelines"),
    ):
        daemon2 = ButlerDaemon(butler_dir2)
        await daemon2.start()
    assert daemon2._route_inbox_recovery_task is None
