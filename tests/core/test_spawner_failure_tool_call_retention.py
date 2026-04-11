"""Tests for spawner failure-path tool-call retention.

Verifies that when a runtime invocation fails after capturing some tool calls,
the spawner persists the captured calls to session_complete() rather than
always writing tool_calls=[].  Also verifies that the new retry-provenance
fields are forwarded from proc_info to session_process_log_write().
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner
from butlers.core.tool_call_capture import _captured_tool_calls

pytestmark = pytest.mark.unit


def _make_config(name: str = "test-butler", port: int = 9100) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        modules={},
        env_required=[],
        env_optional=[],
    )


class _FailAfterToolCallAdapter(RuntimeAdapter):
    """Adapter that simulates a session where MCP tools ran then a crash occurred.

    In production the tool-call buffer is populated by MCP tool handlers
    (guards.py sets the context var per-request).  Here we simulate that by
    directly inserting a record into the shared capture dict keyed by the
    session_id the spawner pre-allocated, then raising so the failure path
    is exercised.
    """

    def __init__(self, tool_name: str = "some_tool") -> None:
        self._tool_name = tool_name

    @property
    def binary_name(self) -> str:
        return "mock"

    @property
    def last_process_info(self) -> dict[str, Any] | None:
        return {
            "pid": 999,
            "exit_code": 1,
            "command": "mock",
            "stderr": "crash",
            "runtime_type": "mock",
        }

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        **kwargs: Any,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        # Simulate MCP tool call captured by the daemon guard layer before crash.
        # The spawner pre-allocates the buffer via ensure_runtime_session_capture().
        # We find the buffer by inspecting what keys exist (only one active session).
        from butlers.core.tool_call_capture import _capture_lock

        with _capture_lock:
            for session_id_key in list(_captured_tool_calls):
                _captured_tool_calls[session_id_key].append(
                    {"name": self._tool_name, "input": {"key": "val"}}
                )
        raise RuntimeError("mid-session crash")

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        config_path = tmp_dir / "cfg.json"
        config_path.write_text(json.dumps({"mcpServers": mcp_servers}))
        return config_path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


class _FailNoToolCallAdapter(RuntimeAdapter):
    """Adapter that raises before any tool calls are captured."""

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        **kwargs: Any,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        raise RuntimeError("early crash")

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        config_path = tmp_dir / "cfg.json"
        config_path.write_text("{}")
        return config_path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


async def test_failure_path_retains_captured_tool_calls(tmp_path: Path) -> None:
    """session_complete() receives captured tool calls on failure, not []."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    pool = AsyncMock()
    session_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    spawner = Spawner(
        config=_make_config(),
        config_dir=config_dir,
        pool=pool,
        runtime=_FailAfterToolCallAdapter(tool_name="important_tool"),
    )

    complete_calls: list[dict] = []

    async def _capture_complete(_pool, _sid, *, tool_calls, **kwargs):
        complete_calls.append({"tool_calls": list(tool_calls), **kwargs})

    with (
        patch(
            "butlers.core.spawner.session_create", new_callable=AsyncMock, return_value=session_id
        ),
        patch("butlers.core.spawner.session_complete", side_effect=_capture_complete),
        patch("butlers.core.spawner.session_process_log_write", new_callable=AsyncMock),
        patch("butlers.core.spawner.write_audit_entry", new_callable=AsyncMock),
    ):
        result = await spawner.trigger("test prompt", "tick")

    assert result.success is False
    assert "mid-session crash" in result.error

    # session_complete() must have been called once with the captured tool call
    assert len(complete_calls) == 1
    completed = complete_calls[0]
    assert completed["success"] is False
    assert any(tc.get("name") == "important_tool" for tc in completed["tool_calls"]), (
        f"Expected 'important_tool' in tool_calls, got {completed['tool_calls']}"
    )


async def test_failure_path_empty_tool_calls_when_none_captured(tmp_path: Path) -> None:
    """session_complete() receives [] when no tool calls were captured before failure."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    pool = AsyncMock()
    session_id = uuid.UUID("00000000-0000-0000-0000-000000000002")

    spawner = Spawner(
        config=_make_config(),
        config_dir=config_dir,
        pool=pool,
        runtime=_FailNoToolCallAdapter(),
    )

    complete_calls: list[dict] = []

    async def _capture_complete(_pool, _sid, *, tool_calls, **kwargs):
        complete_calls.append({"tool_calls": list(tool_calls), **kwargs})

    with (
        patch(
            "butlers.core.spawner.session_create", new_callable=AsyncMock, return_value=session_id
        ),
        patch("butlers.core.spawner.session_complete", side_effect=_capture_complete),
        patch("butlers.core.spawner.session_process_log_write", new_callable=AsyncMock),
        patch("butlers.core.spawner.write_audit_entry", new_callable=AsyncMock),
    ):
        result = await spawner.trigger("test prompt", "tick")

    assert result.success is False
    assert len(complete_calls) == 1
    assert complete_calls[0]["tool_calls"] == []


class _FailWithRetryInfoAdapter(RuntimeAdapter):
    """Adapter with retry provenance set in last_process_info that then raises."""

    @property
    def binary_name(self) -> str:
        return "codex"

    @property
    def last_process_info(self) -> dict[str, Any] | None:
        return {
            "pid": 42,
            "exit_code": 0,
            "command": "codex exec ...",
            "stderr": "",
            "runtime_type": "codex",
            "retry_attempted": True,
            "retry_succeeded": False,
            "result_source": "first",
            "attempt_count": 2,
        }

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        **kwargs: Any,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        raise RuntimeError("post-retry crash")

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        config_path = tmp_dir / "cfg.json"
        config_path.write_text("{}")
        return config_path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


async def test_failure_path_forwards_retry_provenance_to_process_log(tmp_path: Path) -> None:
    """session_process_log_write() receives retry provenance fields on failure path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    pool = AsyncMock()
    session_id = uuid.UUID("00000000-0000-0000-0000-000000000003")

    spawner = Spawner(
        config=_make_config(),
        config_dir=config_dir,
        pool=pool,
        runtime=_FailWithRetryInfoAdapter(),
    )

    log_write_calls: list[dict] = []

    async def _capture_log_write(_pool, _sid, **kwargs):
        log_write_calls.append(dict(kwargs))

    with (
        patch(
            "butlers.core.spawner.session_create", new_callable=AsyncMock, return_value=session_id
        ),
        patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        patch("butlers.core.spawner.session_process_log_write", side_effect=_capture_log_write),
        patch("butlers.core.spawner.write_audit_entry", new_callable=AsyncMock),
    ):
        result = await spawner.trigger("test prompt", "tick")

    assert result.success is False
    assert len(log_write_calls) == 1
    log_call = log_write_calls[0]
    assert log_call["retry_attempted"] is True
    assert log_call["retry_succeeded"] is False
    assert log_call["result_source"] == "first"
    assert log_call["attempt_count"] == 2
