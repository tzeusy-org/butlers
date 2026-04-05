"""Unit tests for RuntimeAdapter.last_process_info.

Verifies adapters populate last_process_info after invoke() completes
(success and timeout) and that the base class default is None.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import ClaudeCodeAdapter, CodexAdapter, GeminiAdapter, RuntimeAdapter

pytestmark = pytest.mark.unit

_SUBPROCESS_ADAPTERS = [
    pytest.param(ClaudeCodeAdapter, "claude_binary", "/usr/bin/claude",
                 "butlers.core.runtimes.claude_code.asyncio.create_subprocess_exec", "claude", id="claude"),
    pytest.param(CodexAdapter, "codex_binary", "/usr/bin/codex",
                 "butlers.core.runtimes.codex.asyncio.create_subprocess_exec", "codex", id="codex"),
    pytest.param(GeminiAdapter, "gemini_binary", "/usr/bin/gemini",
                 "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec", "gemini", id="gemini"),
]


def test_base_adapter_last_process_info_is_none():
    """RuntimeAdapter base class returns None for last_process_info by default."""

    class _MinimalAdapter(RuntimeAdapter):
        @property
        def binary_name(self) -> str:
            return "test-binary"

        async def invoke(self, prompt, system_prompt, mcp_servers, env, **kwargs):
            return ("ok", [], None)

        def build_config_file(self, mcp_servers, tmp_dir):
            return tmp_dir / "config.json"

        def parse_system_prompt_file(self, config_dir):
            return ""

    assert _MinimalAdapter().last_process_info is None


@pytest.mark.parametrize("adapter_class, binary_kwarg, binary, exec_patch, runtime_type",
                         _SUBPROCESS_ADAPTERS)
async def test_last_process_info_populated_after_successful_invoke(
    adapter_class, binary_kwarg, binary, exec_patch, runtime_type
):
    """last_process_info is populated with pid, exit_code, command, stderr, runtime_type."""
    adapter = adapter_class(**{binary_kwarg: binary})
    mock_proc = AsyncMock()
    mock_proc.pid = 12345
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b"warn"))

    with patch(exec_patch, return_value=mock_proc):
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    info = adapter.last_process_info
    assert info is not None
    assert info["pid"] == 12345
    assert info["exit_code"] == 0
    assert "warn" in info["stderr"]
    assert info["runtime_type"] == runtime_type


@pytest.mark.parametrize("adapter_class, binary_kwarg, binary, exec_patch, runtime_type",
                         _SUBPROCESS_ADAPTERS)
async def test_last_process_info_populated_after_timeout(
    adapter_class, binary_kwarg, binary, exec_patch, runtime_type
):
    """last_process_info is populated even when invoke() raises TimeoutError."""
    adapter = adapter_class(**{binary_kwarg: binary})
    mock_proc = AsyncMock()
    mock_proc.pid = 5678
    mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()

    with patch(exec_patch, return_value=mock_proc):
        with pytest.raises(TimeoutError):
            await adapter.invoke(prompt="slow", system_prompt="", mcp_servers={}, env={}, timeout=1)

    info = adapter.last_process_info
    assert info is not None
    assert info["pid"] == 5678
    assert info["exit_code"] == -1
    assert "timeout" in info["stderr"].lower()
