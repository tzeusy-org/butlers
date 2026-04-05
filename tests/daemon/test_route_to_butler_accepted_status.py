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
from butlers.modules.pipeline import _routing_ctx_var
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
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
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
        patch("butlers.daemon.FastMCP", return_value=mock_mcp),
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


@pytest.mark.parametrize(
    "inner_result,expected_status,check_field",
    [
        ({"result": {"status": "ok", "message": "processed"}}, "ok", None),
        ({"result": {"status": "accepted"}}, "accepted", None),
        ({"error": "Butler 'health' not found in registry"}, "error", "not found"),
        ({"result": {"message_id": "abc123"}}, "ok", None),
        ({"result": {"status": "error", "error": "something went wrong"}}, "error", "something"),
    ],
)
async def test_route_to_butler_status_mapping(
    tmp_path, inner_result, expected_status, check_field
) -> None:
    """route_to_butler maps inner results to correct outer status codes."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    mock_route = AsyncMock(return_value=inner_result)
    _, fn = await _start_switchboard_and_capture_route_to_butler(
        butler_dir, patches, mock_route=mock_route
    )
    assert fn is not None

    result = await fn(butler="health", prompt="test")
    assert result["status"] == expected_status
    assert result["butler"] == "health"
    if check_field:
        assert check_field in result.get("error", "")


# ---------------------------------------------------------------------------
# Request context / UUID7 normalization
# ---------------------------------------------------------------------------


async def test_route_to_butler_uuid7_context_normalization(tmp_path: Path) -> None:
    """Missing context generates uuid7; invalid context request_id is rewritten to uuid7."""
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

    # Invalid request_id: rewritten to uuid7
    captured.clear()
    token = _routing_ctx_var.set(
        {
            "source_metadata": {"channel": "telegram_bot", "identity": "user-123"},
            "request_context": {"source_thread_identity": "chat-456"},
            "request_id": "unknown",
        }
    )
    try:
        result2 = await fn(butler="general", prompt="hello")
    finally:
        _routing_ctx_var.reset(token)

    assert result2["status"] == "accepted"
    payload2 = {k: v for k, v in captured.items() if k != "__switchboard_route_context"}
    parsed = parse_route_envelope(payload2)
    assert parsed.request_context.request_id.version == 7
    assert captured["request_context"]["request_id"] != "unknown"


async def test_route_to_butler_runtime_session_routing_context_fallback(tmp_path: Path) -> None:
    """When task-local routing context is missing, fall back to runtime session lineage."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    captured: dict[str, Any] = {}

    async def _capture(*_args, **kwargs):
        captured.update(kwargs["args"])
        return {"result": {"status": "accepted"}}

    _, fn = await _start_switchboard_and_capture_route_to_butler(
        butler_dir, patches, mock_route=AsyncMock(side_effect=_capture)
    )

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
    try:
        result = await fn(butler="health", prompt="track breakfast")
    finally:
        reset_current_runtime_session_id(token)
        clear_runtime_session_routing_context(runtime_session_id)

    assert result["status"] == "accepted"
    assert captured["request_context"]["source_channel"] == "telegram_bot"
    assert captured["request_context"]["source_sender_identity"] == "123456789"
    assert captured["request_context"]["source_thread_identity"] == "123456789:999"


# ---------------------------------------------------------------------------
# Complexity envelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "complexity,expected",
    [
        (None, "medium"),       # default
        ("high", "high"),
        ("extra_high", "extra_high"),
        ("extreme", "medium"),  # invalid → fallback
        ("trivial", "trivial"),
    ],
)
async def test_route_to_butler_complexity_in_envelope(
    tmp_path, complexity, expected
) -> None:
    """route_to_butler embeds complexity in route.v1 input section with fallback to 'medium'."""
    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    captured: dict[str, Any] = {}

    async def _capture(*_args, **kwargs):
        captured.update(kwargs["args"])
        return {"result": {"status": "accepted"}}

    _, fn = await _start_switchboard_and_capture_route_to_butler(
        butler_dir, patches, mock_route=AsyncMock(side_effect=_capture)
    )
    kwargs = {"butler": "health", "prompt": "test"}
    if complexity is not None:
        kwargs["complexity"] = complexity
    await fn(**kwargs)
    assert captured["input"]["complexity"] == expected
