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


# ---------------------------------------------------------------------------
# Helpers (adapted from test_daemon_spans.py)
# ---------------------------------------------------------------------------


def _make_butler_toml(tmp_path: Path) -> Path:
    """Write a minimal butler.toml and return the directory."""
    toml_lines = [
        "[butler]",
        'name = "test-butler"',
        "port = 9100",
        'description = "A test butler"',
        "",
        "[butler.db]",
        'name = "butler_test"',
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
    mock_db.db_name = "butler_test"

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
        "validate_core_credentials": patch(
            "butlers.daemon.validate_core_credentials_async",
            new_callable=AsyncMock,
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
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
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
        patches["validate_core_credentials"],
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRemindTool:
    """Tests for the remind() MCP tool."""

    async def test_remind_registered(self, tmp_path):
        """remind is registered as an MCP tool."""
        butler_dir = _make_butler_toml(tmp_path)
        _, tools = await _start_daemon_capture_tools(butler_dir)
        assert "remind" in tools

    async def test_remind_with_delay_minutes(self, tmp_path):
        """remind with delay_minutes creates a one-shot schedule."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()
        _, tools = await _start_daemon_capture_tools(butler_dir, patches)

        task_id = uuid4()
        with patch(
            "butlers.daemon._schedule_create",
            new_callable=AsyncMock,
            return_value=task_id,
        ) as mock_create:
            result = await tools["remind"](
                message="Take medication",
                channel="telegram",
                delay_minutes=30,
            )

        assert result["status"] == "scheduled"
        assert result["id"] == str(task_id)
        assert result["channel"] == "telegram"
        assert result["message"] == "Take medication"
        assert "remind_at" in result

        # Verify _schedule_create was called correctly
        mock_create.assert_awaited_once()
        call_args = mock_create.call_args
        assert call_args[0][0] == patches["mock_pool"]  # pool
        assert call_args[0][1].startswith("remind-")  # name
        # cron is the 3rd positional arg
        cron = call_args[0][2]
        assert len(cron.split()) == 5  # valid 5-field cron
        # prompt is 4th positional arg
        prompt = call_args[0][3]
        assert "notify" in prompt.lower()
        assert "Take medication" in prompt
        assert "telegram" in prompt
        # until_at kwarg
        assert "until_at" in call_args[1]

    async def test_remind_with_remind_at(self, tmp_path):
        """remind with remind_at creates a one-shot schedule at the specified time."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()
        _, tools = await _start_daemon_capture_tools(butler_dir, patches)

        task_id = uuid4()
        future_time = datetime.now(UTC) + timedelta(hours=2)

        with patch(
            "butlers.daemon._schedule_create",
            new_callable=AsyncMock,
            return_value=task_id,
        ) as mock_create:
            result = await tools["remind"](
                message="Meeting in 5 min",
                channel="email",
                remind_at=future_time,
            )

        assert result["status"] == "scheduled"
        assert result["id"] == str(task_id)
        assert result["channel"] == "email"
        assert result["message"] == "Meeting in 5 min"

        # Verify cron matches the target time
        cron = mock_create.call_args[0][2]
        parts = cron.split()
        assert parts[0] == str(future_time.minute)
        assert parts[1] == str(future_time.hour)
        assert parts[2] == str(future_time.day)
        assert parts[3] == str(future_time.month)
        assert parts[4] == "*"

    async def test_remind_error_both_delay_and_remind_at(self, tmp_path):
        """remind errors when both delay_minutes and remind_at are provided."""
        butler_dir = _make_butler_toml(tmp_path)
        _, tools = await _start_daemon_capture_tools(butler_dir)

        result = await tools["remind"](
            message="Test",
            channel="telegram",
            delay_minutes=10,
            remind_at=datetime.now(UTC) + timedelta(hours=1),
        )

        assert result["status"] == "error"
        assert "exactly one" in result["error"].lower()

    async def test_remind_error_neither_delay_nor_remind_at(self, tmp_path):
        """remind errors when neither delay_minutes nor remind_at are provided."""
        butler_dir = _make_butler_toml(tmp_path)
        _, tools = await _start_daemon_capture_tools(butler_dir)

        result = await tools["remind"](
            message="Test",
            channel="telegram",
        )

        assert result["status"] == "error"
        assert "exactly one" in result["error"].lower()

    async def test_remind_error_delay_minutes_too_small(self, tmp_path):
        """remind errors when delay_minutes is less than 1."""
        butler_dir = _make_butler_toml(tmp_path)
        _, tools = await _start_daemon_capture_tools(butler_dir)

        result = await tools["remind"](
            message="Test",
            channel="telegram",
            delay_minutes=0,
        )

        assert result["status"] == "error"
        assert "at least 1" in result["error"]

    async def test_remind_error_remind_at_in_past(self, tmp_path):
        """remind errors when remind_at is in the past."""
        butler_dir = _make_butler_toml(tmp_path)
        _, tools = await _start_daemon_capture_tools(butler_dir)

        past_time = datetime.now(UTC) - timedelta(hours=1)
        result = await tools["remind"](
            message="Test",
            channel="telegram",
            remind_at=past_time,
        )

        assert result["status"] == "error"
        assert "future" in result["error"].lower()

    async def test_remind_with_request_context(self, tmp_path):
        """remind passes request_context through to the prompt."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()
        _, tools = await _start_daemon_capture_tools(butler_dir, patches)

        task_id = uuid4()
        ctx = {
            "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
            "source_channel": "telegram",
            "source_endpoint_identity": "switchboard",
            "source_sender_identity": "health",
            "source_thread_identity": "12345",
        }

        with patch(
            "butlers.daemon._schedule_create",
            new_callable=AsyncMock,
            return_value=task_id,
        ) as mock_create:
            result = await tools["remind"](
                message="Check blood pressure",
                channel="telegram",
                delay_minutes=60,
                request_context=ctx,
            )

        assert result["status"] == "scheduled"
        # Verify request_context is in the prompt
        prompt = mock_create.call_args[0][3]
        assert "request_context" in prompt
        assert "018f6f4e" in prompt

    async def test_remind_until_at_is_target_plus_one_minute(self, tmp_path):
        """until_at is set to target + 1 minute to ensure one-shot behavior."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()
        _, tools = await _start_daemon_capture_tools(butler_dir, patches)

        task_id = uuid4()
        with patch(
            "butlers.daemon._schedule_create",
            new_callable=AsyncMock,
            return_value=task_id,
        ) as mock_create:
            result = await tools["remind"](
                message="Test",
                channel="telegram",
                delay_minutes=30,
            )

        until_at = mock_create.call_args[1]["until_at"]
        # Parse the remind_at from result
        remind_at = datetime.fromisoformat(result["remind_at"])
        expected_until = remind_at + timedelta(minutes=1)
        # until_at should be target + 1 minute
        assert abs((until_at - expected_until).total_seconds()) < 2

    async def test_remind_naive_remind_at_treated_as_utc(self, tmp_path):
        """A naive (no timezone) remind_at is treated as UTC."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()
        _, tools = await _start_daemon_capture_tools(butler_dir, patches)

        task_id = uuid4()
        naive_future = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

        with patch(
            "butlers.daemon._schedule_create",
            new_callable=AsyncMock,
            return_value=task_id,
        ):
            result = await tools["remind"](
                message="Test",
                channel="telegram",
                remind_at=naive_future,
            )

        assert result["status"] == "scheduled"

    async def test_remind_name_includes_timestamp(self, tmp_path):
        """The schedule name includes a timestamp for uniqueness."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()
        _, tools = await _start_daemon_capture_tools(butler_dir, patches)

        task_id = uuid4()
        with patch(
            "butlers.daemon._schedule_create",
            new_callable=AsyncMock,
            return_value=task_id,
        ) as mock_create:
            await tools["remind"](
                message="Test",
                channel="telegram",
                delay_minutes=15,
            )

        name = mock_create.call_args[0][1]
        assert name.startswith("remind-")
        # Format is remind-YYYYMMDDTHHM
        assert len(name) > len("remind-")
