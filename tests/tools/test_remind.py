"""Tests for the remind() MCP tool in ButlerDaemon."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


def _make_butler_toml(tmp_path: Path) -> Path:
    """Write a minimal butler.toml and return the directory."""
    toml_lines = [
        "[butler]",
        'name = "test-butler"',
        "port = 9100",
        'description = "A test butler"',
        "",
        "[butler.db]",
        'name = "butlers"',
        'schema = "test_butler"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _patch_infra():
    """Return a dict of patches for all infrastructure dependencies."""
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
    mock_trigger_result = MagicMock()
    mock_trigger_result.result = "ok"
    mock_trigger_result.error = None
    mock_trigger_result.duration_ms = 100
    mock_spawner.trigger = AsyncMock(return_value=mock_trigger_result)
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
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "get_adapter": patch(
            "butlers.daemon.get_adapter",
            return_value=type(
                "MockAdapter",
                (),
                {"binary_name": "claude", "__init__": lambda self, **kwargs: None},
            ),
        ),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "mock_pool": mock_pool,
    }


async def _start_daemon_capture_tools(
    butler_dir: Path, patches: dict | None = None
) -> tuple[ButlerDaemon, dict[str, Any]]:
    """Start a daemon and capture all registered tool functions."""
    if patches is None:
        patches = _patch_infra()
    tool_fns: dict[str, Any] = {}
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            tool_fns[declared_name or fn.__name__] = fn
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
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    return daemon, tool_fns


async def test_remind_registered(tmp_path):
    """remind is registered as an MCP tool."""
    butler_dir = _make_butler_toml(tmp_path)
    _, tools = await _start_daemon_capture_tools(butler_dir)
    assert "remind" in tools


async def test_remind_with_delay_minutes(tmp_path):
    """remind with delay_minutes creates a one-shot schedule with correct cron."""
    butler_dir = _make_butler_toml(tmp_path)
    patches = _patch_infra()
    _, tools = await _start_daemon_capture_tools(butler_dir, patches)

    task_id = uuid4()
    with patch(
        "butlers.daemon._schedule_create", new_callable=AsyncMock, return_value=task_id
    ) as mock_create:
        result = await tools["remind"](
            message="Take medication", channel="telegram", delay_minutes=30
        )

    assert result["status"] == "scheduled"
    assert result["id"] == str(task_id)
    assert result["channel"] == "telegram"
    assert "remind_at" in result
    cron = mock_create.call_args[0][2]
    assert len(cron.split()) == 5  # valid 5-field cron
    prompt = mock_create.call_args[0][3]
    assert "Take medication" in prompt and "telegram" in prompt
    assert "until_at" in mock_create.call_args[1]
    # until_at = remind_at + 1 minute (one-shot)
    until_at = mock_create.call_args[1]["until_at"]
    remind_at = datetime.fromisoformat(result["remind_at"])
    assert abs((until_at - (remind_at + timedelta(minutes=1))).total_seconds()) < 2


async def test_remind_with_remind_at(tmp_path):
    """remind with remind_at creates a one-shot schedule at the specified time."""
    butler_dir = _make_butler_toml(tmp_path)
    patches = _patch_infra()
    _, tools = await _start_daemon_capture_tools(butler_dir, patches)

    task_id = uuid4()
    future_time = datetime.now(UTC) + timedelta(hours=2)

    with patch(
        "butlers.daemon._schedule_create", new_callable=AsyncMock, return_value=task_id
    ) as mock_create:
        result = await tools["remind"](
            message="Meeting in 5 min", channel="email", remind_at=future_time
        )

    assert result["status"] == "scheduled"
    parts = mock_create.call_args[0][2].split()
    assert parts[0] == str(future_time.minute)
    assert parts[1] == str(future_time.hour)


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (
            {"delay_minutes": 10, "remind_at": datetime.now(UTC) + timedelta(hours=1)},
            "exactly one",
        ),
        ({}, "exactly one"),
        ({"delay_minutes": 0}, "at least 1"),
        ({"remind_at": datetime.now(UTC) - timedelta(hours=1)}, "future"),
    ],
)
async def test_remind_validation_errors(tmp_path, kwargs, match):
    """remind returns error status for invalid timing combinations."""
    butler_dir = _make_butler_toml(tmp_path)
    _, tools = await _start_daemon_capture_tools(butler_dir)
    result = await tools["remind"](message="Test", channel="telegram", **kwargs)
    assert result["status"] == "error"
    assert match in result["error"].lower()
