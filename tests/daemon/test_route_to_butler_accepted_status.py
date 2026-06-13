"""Tests for route_to_butler 'accepted' status passthrough — condensed.

Verifies that when a target butler's route.execute returns {status: 'accepted'},
the switchboard's route_to_butler tool passes through {status: 'accepted', butler: ...}
instead of the generic {status: 'ok', butler: ...}.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.tool_call_capture import (
    clear_runtime_session_routing_context,
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
    set_runtime_session_routing_context,
)
from butlers.daemon import ButlerDaemon
from butlers.tools.switchboard.routing.contracts import parse_route_envelope

pytestmark = pytest.mark.unit


def _make_switchboard_dir(tmp_path: Path) -> Path:
    toml_lines = [
        "[butler]",
        'name = "switchboard"',
        "port = 9100",
        'description = "Routes messages"',
        "",
        "[butler.db]",
        'name = "butlers"',
        'schema = "switchboard"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _make_runtime_config_row(butler_name: str = "switchboard") -> dict:
    """Return a dict-like row for the runtime_config table, as returned by asyncpg.fetchrow."""
    return {
        "butler_name": butler_name,
        "core_groups": None,
        "max_concurrent": 3,
        "max_queued": 10,
        "seeded_at": None,
        "updated_at": None,
    }


def _make_fetchrow_side_effect(butler_name: str = "switchboard"):
    """Return an async side_effect for pool.fetchrow that returns runtime_config rows
    for runtime_config queries and None for all other queries."""

    async def _fetchrow(query: str, *args, **kwargs):
        if "runtime_config" in query:
            return _make_runtime_config_row(butler_name)
        return None

    return _fetchrow


def _patch_infra():
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
        "get_adapter": patch("butlers.lifecycle.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


async def _start_switchboard_and_capture_route_to_butler(
    butler_dir: Path,
    patches: dict,
    mock_route: AsyncMock | None = None,
) -> tuple[ButlerDaemon, Any]:
    route_to_butler_fn = None
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            nonlocal route_to_butler_fn
            resolved_name = declared_name or fn.__name__
            if resolved_name == "route_to_butler":
                route_to_butler_fn = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator

    route_patch = (
        patch("butlers.tools.switchboard.routing.route.route", new=mock_route)
        if mock_route is not None
        else patch("butlers.tools.switchboard.routing.route.route")
    )

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
        route_patch,
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    return daemon, route_to_butler_fn


# ---------------------------------------------------------------------------
# Status passthrough
# ---------------------------------------------------------------------------


async def test_route_to_butler_status_mapping(tmp_path: Path) -> None:
    """route_to_butler maps inner results to correct outer status codes."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)

    # accepted → passed through
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})
    _, fn = await _start_switchboard_and_capture_route_to_butler(
        butler_dir, patches, mock_route=mock_route
    )
    assert fn is not None
    result = await fn(butler="health", prompt="test")
    assert result["status"] == "accepted"
    assert result["butler"] == "health"

    # error → error with message
    patches2 = _patch_infra()
    mock_route2 = AsyncMock(return_value={"error": "Butler 'health' not found in registry"})
    (tmp_path / "d2").mkdir()
    _, fn2 = await _start_switchboard_and_capture_route_to_butler(
        _make_switchboard_dir(tmp_path / "d2"),
        patches2,
        mock_route=mock_route2,
    )
    assert fn2 is not None
    result2 = await fn2(butler="health", prompt="test")
    assert result2["status"] == "error"
    assert "not found" in result2.get("error", "")


# ---------------------------------------------------------------------------
# Request context normalization
# ---------------------------------------------------------------------------


async def test_route_to_butler_envelope_behavior(tmp_path: Path) -> None:
    """Missing context generates uuid7; runtime session fallback honored; complexity defaults/validation."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    captured: dict[str, Any] = {}

    async def _capture(*_args, **kwargs):
        captured.update(kwargs["args"])
        return {"result": {"status": "accepted"}}

    _, fn = await _start_switchboard_and_capture_route_to_butler(
        butler_dir, patches, mock_route=AsyncMock(side_effect=_capture)
    )

    # Missing context: generates uuid7
    result = await fn(butler="general", prompt="hello")
    assert result["status"] == "accepted"
    payload = {k: v for k, v in captured.items() if k != "__switchboard_route_context"}
    assert parse_route_envelope(payload).request_context.request_id.version == 7

    # Runtime session context fallback
    runtime_session_id = "sess-route-to-butler-fallback"
    set_runtime_session_routing_context(
        runtime_session_id,
        {
            "source_metadata": {
                "channel": "telegram_bot",
                "identity": "telegram:bot-main",
                "tool_name": "ingest",
            },
            "request_context": {
                "request_id": "019c8812-fb0f-77f3-88b9-5763c1336b27",
                "source_channel": "telegram_bot",
                "source_sender_identity": "123456789",
                "source_thread_identity": "123456789:999",
            },
            "request_id": "019c8812-fb0f-77f3-88b9-5763c1336b27",
        },
    )
    token = set_current_runtime_session_id(runtime_session_id)
    captured.clear()
    try:
        result2 = await fn(butler="health", prompt="track breakfast")
    finally:
        reset_current_runtime_session_id(token)
        clear_runtime_session_routing_context(runtime_session_id)
    assert result2["status"] == "accepted"
    assert captured["request_context"]["source_channel"] == "telegram_bot"

    # Complexity: reasoning explicit; invalid → workhorse; missing → workhorse
    captured.clear()
    await fn(butler="health", prompt="test", complexity="reasoning")
    assert captured["input"]["complexity"] == "reasoning"

    captured.clear()
    await fn(butler="health", prompt="test", complexity="extreme")
    assert captured["input"]["complexity"] == "workhorse"

    captured.clear()
    await fn(butler="health", prompt="test")
    assert captured["input"]["complexity"] == "workhorse"


# ---------------------------------------------------------------------------
# Permissions-matrix enforcement (public.permissions: cross_butler) [bu-tzlq6]
# ---------------------------------------------------------------------------


async def test_route_to_butler_blocked_when_cross_butler_revoked(tmp_path: Path) -> None:
    """Revoked cross_butler blocks the dispatch before the switchboard route runs.

    Mirrors the spawn gate: a granted=false cell denies the cross-butler call
    outright (observable error), and the underlying route is never invoked.
    Pre-fix this fails: the matrix was ignored, so the route proceeded.
    """
    from butlers.core.permissions import PermissionStatus

    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})
    _, fn = await _start_switchboard_and_capture_route_to_butler(
        butler_dir, patches, mock_route=mock_route
    )
    assert fn is not None

    with patch(
        "butlers.core_tools._switchboard.check_permission",
        new_callable=AsyncMock,
        return_value=PermissionStatus(allowed=False, explicit=True, reason="revoked by owner"),
    ):
        result = await fn(butler="health", prompt="test")

    assert result["status"] == "error"
    assert "permission denied" in result.get("error", "").lower()
    assert result["butler"] == "health"
    mock_route.assert_not_awaited()


async def test_route_to_butler_allowed_when_cross_butler_granted(tmp_path: Path) -> None:
    """Granted/default cross_butler lets the dispatch proceed to the route."""
    from butlers.core.permissions import PermissionStatus

    patches = _patch_infra()
    (tmp_path / "granted").mkdir(exist_ok=True)
    butler_dir = _make_switchboard_dir(tmp_path / "granted")
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})
    _, fn = await _start_switchboard_and_capture_route_to_butler(
        butler_dir, patches, mock_route=mock_route
    )
    assert fn is not None

    with patch(
        "butlers.core_tools._switchboard.check_permission",
        new_callable=AsyncMock,
        return_value=PermissionStatus(allowed=True, explicit=True, reason="default"),
    ):
        result = await fn(butler="health", prompt="test")

    assert result["status"] == "accepted"
    mock_route.assert_awaited_once()
