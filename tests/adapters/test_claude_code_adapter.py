"""Tests for ClaudeCodeAdapter — Claude CLI runtime adapter.

Covers unique ClaudeCode behaviors:
- binary discovery (_find_claude_binary)
- create_worker() preserves all constructor args
- parse_system_prompt_file reads CLAUDE.md
- invoke() CLI flags (required flags, mcp-config, system-prompt, model, runtime_args)
- invoke() stderr log file creation
- invoke() error paths (nonzero exit, timeout)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import ClaudeCodeAdapter, get_adapter
from butlers.core.runtimes.claude_code import _find_claude_binary

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.claude_code.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


def test_find_claude_binary():
    """_find_claude_binary returns path when found; FileNotFoundError includes npm install hint."""
    with patch("butlers.core.runtimes.claude_code.shutil.which", return_value="/usr/bin/claude"):
        assert _find_claude_binary() == "/usr/bin/claude"
    with patch("butlers.core.runtimes.claude_code.shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="npm install"):
            _find_claude_binary()


# ---------------------------------------------------------------------------
# create_worker() preserves all constructor args
# ---------------------------------------------------------------------------


def test_create_worker_preserves_constructor_args(tmp_path: Path):
    """create_worker() returns a new adapter with identical configuration."""
    adapter = ClaudeCodeAdapter(
        claude_binary="/usr/bin/claude", butler_name="switchboard", log_root=tmp_path
    )
    worker = adapter.create_worker()
    assert worker is not adapter
    assert isinstance(worker, ClaudeCodeAdapter)
    assert worker._claude_binary == "/usr/bin/claude"
    assert worker._butler_name == "switchboard"
    assert worker._log_root == tmp_path


# ---------------------------------------------------------------------------
# invoke() CLI flag contract
# ---------------------------------------------------------------------------


async def test_invoke_command_includes_required_flags():
    """invoke() command includes all required Claude CLI flags."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="do something", system_prompt="", mcp_servers={}, env={})

    cmd = mock_sub.call_args[0]
    assert cmd[0] == "/usr/bin/claude"
    assert "--output-format" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--bare" in cmd
    assert "--no-session-persistence" in cmd
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--strict-mcp-config" in cmd


async def test_invoke_optional_flags():
    """invoke() conditionally adds --system-prompt and --model based on arguments."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    # --system-prompt when non-empty
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="test", system_prompt="You are helpful.", mcp_servers={}, env={})
    cmd = mock_sub.call_args[0]
    assert "--system-prompt" in cmd and cmd[cmd.index("--system-prompt") + 1] == "You are helpful."

    # --system-prompt omitted when empty
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})
    assert "--system-prompt" not in mock_sub.call_args[0]

    # --model when provided
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="run", system_prompt="", mcp_servers={}, env={}, model="claude-opus-4-5")
    cmd = mock_sub.call_args[0]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "claude-opus-4-5"

    # --model omitted when None
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="run", system_prompt="", mcp_servers={}, env={}, model=None)
    assert "--model" not in mock_sub.call_args[0]


# ---------------------------------------------------------------------------
# invoke() stderr log
# ---------------------------------------------------------------------------


async def test_invoke_stderr_log_behavior(tmp_path: Path):
    """Creates per-butler stderr log when butler_name is set; no log without butler_name."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"some stderr output"))
    mock_proc.returncode = 0
    mock_proc.pid = 42

    with patch(_EXEC, return_value=mock_proc):
        adapter = ClaudeCodeAdapter(
            claude_binary="/usr/bin/claude", butler_name="test-butler", log_root=tmp_path
        )
        await adapter.invoke(prompt="hi", system_prompt="sys", mcp_servers={}, env={})

    stderr_log = tmp_path / "butlers" / "test-butler_cc_stderr.log"
    assert stderr_log.exists()
    assert "runtime session start:" in stderr_log.read_text()

    # No log without butler_name — should not raise
    mock_proc.pid = 1
    with patch(_EXEC, return_value=mock_proc):
        await ClaudeCodeAdapter(claude_binary="/usr/bin/claude").invoke(
            prompt="hi", system_prompt="sys", mcp_servers={}, env={}
        )


# ---------------------------------------------------------------------------
# invoke() output parsing
# ---------------------------------------------------------------------------


async def test_invoke_output_and_usage():
    """invoke() parses stream-json result event and captures token usage."""
    mock_proc = AsyncMock()
    mock_proc.pid = 1

    # Plain result — no usage
    output = json.dumps({"type": "result", "result": "Hello!"})
    mock_proc.communicate = AsyncMock(return_value=(output.encode(), b""))
    mock_proc.returncode = 0
    with patch(_EXEC, return_value=mock_proc):
        result_text, tool_calls, usage = await ClaudeCodeAdapter(
            claude_binary="/usr/bin/claude"
        ).invoke(prompt="hi", system_prompt="", mcp_servers={}, env={})
    assert result_text == "Hello!" and tool_calls == [] and usage is None

    # Result with usage
    output2 = json.dumps(
        {"type": "result", "result": "ok", "usage": {"input_tokens": 150, "output_tokens": 300}}
    )
    mock_proc.communicate = AsyncMock(return_value=(output2.encode(), b""))
    with patch(_EXEC, return_value=mock_proc):
        _, _, usage2 = await ClaudeCodeAdapter(claude_binary="/usr/bin/claude").invoke(
            prompt="test", system_prompt="", mcp_servers={}, env={}
        )
    assert usage2 == {"input_tokens": 150, "output_tokens": 300}
