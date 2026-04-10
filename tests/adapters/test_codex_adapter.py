"""Tests for CodexAdapter — Codex CLI runtime adapter.

Unique behaviors not in test_adapter_contract.py:
- Binary discovery (_find_codex_binary)
- parse_system_prompt_file reads AGENTS.md
- build_config_file writes TOML with transport inference
- _parse_codex_output: exec --json format, function_call, mcp_tool_call events
- _extract_tool_call: various container formats, command_execution
- invoke(): exec subcommand, HOME injection, error paths, transport diagnostics
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import CodexAdapter
from butlers.core.runtimes.codex import (
    _extract_tool_call,
    _find_codex_binary,
    _has_mcp_tool_calls,
    _infer_mcp_transport_from_url,
    _parse_codex_output,
)

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"


def test_binary_and_system_prompt(tmp_path: Path):
    """Binary discovery raises FileNotFoundError when missing; AGENTS.md read for system prompt."""
    with patch("butlers.core.runtimes.codex.shutil.which", return_value="/usr/bin/codex"):
        assert _find_codex_binary() == "/usr/bin/codex"
    with patch("butlers.core.runtimes.codex.shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="Codex CLI binary not found"):
            _find_codex_binary()

    (tmp_path / "AGENTS.md").write_text("You are a specialized Codex butler.")
    assert (
        CodexAdapter().parse_system_prompt_file(config_dir=tmp_path)
        == "You are a specialized Codex butler."
    )
    with tempfile.TemporaryDirectory() as empty:
        assert CodexAdapter().parse_system_prompt_file(config_dir=Path(empty)) == ""


def test_build_config_file(tmp_path: Path):
    """build_config_file() writes TOML with correct transport; unsafe names are skipped."""
    # Basic structure
    config_path = CodexAdapter().build_config_file(
        mcp_servers={"my-butler": {"url": "http://localhost:9100/mcp"}}, tmp_dir=tmp_path
    )
    assert config_path == tmp_path / ".codex" / "config.toml"
    content = config_path.read_text()
    assert "[mcp_servers.my-butler]" in content and 'url = "http://localhost:9100/mcp"' in content
    assert 'transport = "streamable_http"' in content
    # Transport URL inference
    assert _infer_mcp_transport_from_url("http://localhost:41100/mcp") == "streamable_http"
    assert _infer_mcp_transport_from_url("http://localhost:41100/sse") == "sse"
    assert _infer_mcp_transport_from_url("http://localhost:41100/events") is None
    # Unsafe name injection protection
    result = CodexAdapter._write_mcp_config_toml(
        {
            "safe_name": {"url": "http://localhost:9100/mcp"},
            'unsafe".transport="sse': {"url": "http://localhost:9200/mcp"},
        },
        tmp_path,
    )
    assert result is not None
    content2 = result.read_text()
    assert (
        "[mcp_servers.safe_name]" in content2
        and "unsafe" not in content2
        and "9200" not in content2
    )


def test_parse_codex_output_and_extract_tool_call():
    """item.completed mcp_tool_call, JSON string arguments, and command_execution formats."""
    # item.completed mcp_tool_call
    lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "mcp_1",
                        "type": "mcp_tool_call",
                        "call": {"name": "route_to_butler", "arguments": {"butler": "rel"}},
                    },
                }
            ),
            json.dumps({"type": "result", "result": "Routed"}),
        ]
    )
    _, tool_calls, _ = _parse_codex_output(lines, "", 0)
    assert (
        len(tool_calls) == 1
        and tool_calls[0]["id"] == "mcp_1"
        and tool_calls[0]["name"] == "route_to_butler"
    )

    # stringified JSON arguments
    tc = _extract_tool_call(
        {
            "id": "fc2",
            "name": "route_to_butler",
            "arguments": '{"butler":"health","prompt":"Track meal"}',
        }
    )
    assert tc["input"] == {"butler": "health", "prompt": "Track meal"}

    # command_execution normalized
    tc = _extract_tool_call(
        {
            "id": "cmd1",
            "type": "command_execution",
            "command": "ls -1",
            "exit_code": 0,
            "aggregated_output": "file.txt\n",
        }
    )
    assert (
        tc["name"] == "command_execution"
        and tc["input"]["command"] == "ls -1"
        and tc["input"]["exit_code"] == 0
    )


def test_parse_item_started_does_not_duplicate_tool_calls():
    """item.started events for tool calls are skipped to avoid duplicating item.completed."""
    lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "id": "cmd1",
                        "type": "command_execution",
                        "command": "ls -1",
                        "status": "in_progress",
                        "exit_code": None,
                        "aggregated_output": "",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "cmd1",
                        "type": "command_execution",
                        "command": "ls -1",
                        "status": "completed",
                        "exit_code": 0,
                        "aggregated_output": "file.txt\n",
                    },
                }
            ),
        ]
    )
    _, tool_calls, _ = _parse_codex_output(lines, "", 0)
    assert len(tool_calls) == 1, f"Expected 1 tool call, got {len(tool_calls)}"
    assert tool_calls[0]["input"]["exit_code"] == 0
    assert tool_calls[0]["input"]["aggregated_output"] == "file.txt\n"


async def test_invoke_behaviors():
    """invoke() uses exec subcommand, injects HOME, raises on error, adds transport diagnostics."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    # exec subcommand
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})
    assert mock_sub.call_args[0][:2] == ("/usr/bin/codex", "exec")

    # HOME injection with mcp servers
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={"s": {"url": "http://localhost/mcp"}},
            env={},
        )
    assert "HOME" in mock_sub.call_args[1].get("env", {})

    # Plain error → RuntimeError
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: rate limit"))
    mock_proc.returncode = 1
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError, match="Codex CLI exited with code 1: Error: rate limit"):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    # Transport failure includes diagnostics
    mock_proc.communicate = AsyncMock(
        return_value=(b"", b"rmcp startup failed: 405 Method Not Allowed")
    )
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError) as exc_info:
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={"switchboard": {"url": "http://localhost:41100/sse"}},
                env={},
            )
    assert "MCP transport diagnostics" in str(exc_info.value)


async def test_invoke_prefers_home_scoped_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """invoke() should avoid /tmp-backed HOME when a real home directory is available."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path))

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    isolated_home = Path(mock_sub.call_args[1]["env"]["HOME"])
    assert isolated_home.parent == codex_dir / ".tmp"


def test_has_mcp_tool_calls():
    """_has_mcp_tool_calls distinguishes MCP tools from bash-only sessions."""
    assert not _has_mcp_tool_calls([])
    assert not _has_mcp_tool_calls([{"name": "command_execution"}])
    assert _has_mcp_tool_calls([{"name": "mcp__switchboard__route_to_butler"}])
    assert _has_mcp_tool_calls([{"name": "command_execution"}, {"name": "route_to_butler"}])


def _make_mcp_stdout() -> bytes:
    """Build Codex JSON-lines output containing an MCP tool call."""
    return (
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "mcp_1",
                    "type": "mcp_tool_call",
                    "call": {"name": "route_to_butler", "arguments": {"butler": "finance"}},
                },
            }
        )
        + "\n"
        + json.dumps({"type": "result", "result": "Routed to finance"})
    ).encode()


def _make_bash_only_stdout() -> bytes:
    """Build Codex JSON-lines output with only bash command_execution events."""
    return (
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "cmd1",
                    "type": "command_execution",
                    "command": "/bin/bash -lc true",
                    "status": "completed",
                    "exit_code": 0,
                    "aggregated_output": "",
                },
            }
        )
        + "\n"
        + json.dumps({"type": "result", "result": "MCP tools called: none."})
    ).encode()


_MCP_SERVERS = {"switchboard": {"url": "http://localhost:41100/mcp"}}


async def test_retry_on_mcp_connection_failure():
    """invoke() retries once when MCP tools not discovered, succeeds on second attempt."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    call_count = 0

    async def _mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 100 + call_count
        # First call: bash only (MCP failure). Second call: MCP tools present.
        if call_count == 1:
            proc.communicate = AsyncMock(return_value=(_make_bash_only_stdout(), b""))
        else:
            proc.communicate = AsyncMock(return_value=(_make_mcp_stdout(), b""))
        return proc

    with (
        patch(_EXEC, side_effect=_mock_exec),
        patch("butlers.core.runtimes.codex._MCP_RETRY_DELAY_SECONDS", 0),
    ):
        result_text, tool_calls, _ = await adapter.invoke(
            prompt="route this",
            system_prompt="",
            mcp_servers=_MCP_SERVERS,
            env={},
        )

    assert call_count == 2, "Should have retried once"
    assert any(tc.get("name") == "route_to_butler" for tc in tool_calls)
    info = adapter.last_process_info
    assert info["mcp_connection_failed"] is True
    assert info["retry_attempted"] is True
    assert info["retry_succeeded"] is True


async def test_retry_both_fail_returns_first_result():
    """When retry also fails, returns first result for text-based fallback."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    async def _mock_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 42
        proc.communicate = AsyncMock(return_value=(_make_bash_only_stdout(), b""))
        return proc

    with (
        patch(_EXEC, side_effect=_mock_exec),
        patch("butlers.core.runtimes.codex._MCP_RETRY_DELAY_SECONDS", 0),
    ):
        result_text, tool_calls, _ = await adapter.invoke(
            prompt="route this",
            system_prompt="",
            mcp_servers=_MCP_SERVERS,
            env={},
        )

    # Should return result (for text fallback), not raise
    assert result_text is not None
    info = adapter.last_process_info
    assert info["mcp_connection_failed"] is True
    assert info["retry_attempted"] is True
    assert info["retry_succeeded"] is False


async def test_no_retry_without_mcp_servers():
    """No retry when mcp_servers is empty (no MCP configured)."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    call_count = 0

    async def _mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 42
        proc.communicate = AsyncMock(return_value=(b"ok", b""))
        return proc

    with patch(_EXEC, side_effect=_mock_exec):
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    assert call_count == 1, "Should NOT retry when no MCP servers configured"


async def test_no_retry_on_nonzero_exit():
    """No retry when CLI exits with non-zero code (real error, not MCP flake)."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    async def _mock_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.returncode = 1
        proc.pid = 42
        proc.communicate = AsyncMock(return_value=(b"", b"Auth error"))
        return proc

    with patch(_EXEC, side_effect=_mock_exec):
        with pytest.raises(RuntimeError, match="Codex CLI exited with code 1"):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers=_MCP_SERVERS,
                env={},
            )
