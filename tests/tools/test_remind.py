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


def _make_runtime_config_row(butler_name: str = "test-butler") -> dict[str, Any]:
    """Return a dict-like runtime_config row for mocked asyncpg fetchrow calls."""
    return {
        "butler_name": butler_name,
        "core_groups": None,
        "max_concurrent": 3,
        "max_queued": 10,
        "seeded_at": None,
        "updated_at": None,
    }


def _make_fetchrow_side_effect(butler_name: str = "test-butler"):
    """Return runtime_config rows for runtime-config queries; None otherwise."""

    async def _fetchrow(query: str, *args, **kwargs):  # noqa: ARG001
        if "runtime_config" in query:
            return _make_runtime_config_row(butler_name)
        return None

    return _fetchrow


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
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_pool = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_pool.fetchrow = AsyncMock(side_effect=_make_fetchrow_side_effect())
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
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=mock_spawner),
        "get_adapter": patch(
            "butlers.lifecycle.get_adapter",
            return_value=type(
                "MockAdapter",
                (),
                {"binary_name": "claude", "__init__": lambda self, **kwargs: None},
            ),
        ),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
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
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
        patches["Spawner"],
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    return daemon, tool_fns


async def test_remind_scheduling(tmp_path):
    """remind registered; delay_minutes creates correct cron + until_at; remind_at works."""
    butler_dir = _make_butler_toml(tmp_path)
    patches = _patch_infra()
    _, tools = await _start_daemon_capture_tools(butler_dir, patches)
    assert "remind" in tools

    task_id = uuid4()
    with patch(
        "butlers.core_tools._notifications._schedule_create",
        new_callable=AsyncMock,
        return_value=task_id,
    ) as mock_create:
        result = await tools["remind"](
            message="Take medication", channel="telegram", delay_minutes=30
        )

    assert result["status"] == "scheduled"
    assert result["id"] == str(task_id)
    cron = mock_create.call_args[0][2]
    assert len(cron.split()) == 5
    until_at = mock_create.call_args[1]["until_at"]
    remind_at = datetime.fromisoformat(result["remind_at"])
    assert abs((until_at - (remind_at + timedelta(minutes=1))).total_seconds()) < 2

    # remind_at variant
    future_time = datetime.now(UTC) + timedelta(hours=2)
    with patch(
        "butlers.core_tools._notifications._schedule_create",
        new_callable=AsyncMock,
        return_value=uuid4(),
    ) as mc:
        result2 = await tools["remind"](message="Meeting", channel="email", remind_at=future_time)
    assert result2["status"] == "scheduled"
    parts = mc.call_args[0][2].split()
    assert parts[0] == str(future_time.minute)
    assert parts[1] == str(future_time.hour)


async def test_remind_validation_errors(tmp_path):
    """remind returns error status for invalid timing combinations."""
    butler_dir = _make_butler_toml(tmp_path)
    _, tools = await _start_daemon_capture_tools(butler_dir)

    future = datetime.now(UTC) + timedelta(hours=1)
    past = datetime.now(UTC) - timedelta(hours=1)
    # Each invalid timing combination must be rejected (wording is not the contract).
    cases = [
        {"delay_minutes": 10, "remind_at": future},  # both supplied
        {},  # neither supplied
        {"delay_minutes": 0},  # non-positive delay
        {"remind_at": past},  # past time
    ]
    for kwargs in cases:
        result = await tools["remind"](message="Test", channel="telegram", **kwargs)
        assert result["status"] == "error", f"Expected error status for {kwargs}"
