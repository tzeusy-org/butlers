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
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import CodexAdapter
from butlers.core.runtimes.codex import (
    _extract_tool_call,
    _find_codex_binary,
    _infer_mcp_transport_from_url,
    _parse_codex_output,
)

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


def test_find_codex_binary():
    """_find_codex_binary returns path when found, raises FileNotFoundError when missing."""
    with patch("butlers.core.runtimes.codex.shutil.which", return_value="/usr/bin/codex"):
        assert _find_codex_binary() == "/usr/bin/codex"
    with patch("butlers.core.runtimes.codex.shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="Codex CLI binary not found"):
            _find_codex_binary()


# ---------------------------------------------------------------------------
# parse_system_prompt_file — reads AGENTS.md
# ---------------------------------------------------------------------------


def test_parse_system_prompt(tmp_path: Path):
    """CodexAdapter reads AGENTS.md; returns empty string when missing."""
    (tmp_path / "AGENTS.md").write_text("You are a specialized Codex butler.")
    assert (
        CodexAdapter().parse_system_prompt_file(config_dir=tmp_path)
        == "You are a specialized Codex butler."
    )
    import tempfile
    with tempfile.TemporaryDirectory() as empty:
        assert CodexAdapter().parse_system_prompt_file(config_dir=Path(empty)) == ""


# ---------------------------------------------------------------------------
# build_config_file — TOML with transport inference
# ---------------------------------------------------------------------------


def test_build_config_file_writes_toml(tmp_path: Path):
    """build_config_file() writes TOML config to .codex/config.toml."""
    adapter = CodexAdapter()
    config_path = adapter.build_config_file(
        mcp_servers={"my-butler": {"url": "http://localhost:9100/mcp"}}, tmp_dir=tmp_path
    )
    assert config_path == tmp_path / ".codex" / "config.toml"
    content = config_path.read_text()
    assert "[mcp_servers.my-butler]" in content
    assert 'url = "http://localhost:9100/mcp"' in content
    assert 'transport = "streamable_http"' in content


def test_infer_mcp_transport_from_url():
    """URL conventions infer expected MCP transport."""
    assert _infer_mcp_transport_from_url("http://localhost:41100/mcp") == "streamable_http"
    assert _infer_mcp_transport_from_url("http://localhost:41100/sse") == "sse"
    assert _infer_mcp_transport_from_url("http://localhost:41100/events") is None


def test_write_mcp_config_toml_skips_unsafe_names(tmp_path: Path):
    """Unsafe MCP server names with injection characters are skipped."""
    result = CodexAdapter._write_mcp_config_toml(
        {
            "safe_name": {"url": "http://localhost:9100/mcp"},
            'unsafe".transport="sse': {"url": "http://localhost:9200/mcp"},
        },
        tmp_path,
    )
    assert result is not None
    content = result.read_text()
    assert "[mcp_servers.safe_name]" in content
    assert "unsafe" not in content
    assert "9200" not in content


# ---------------------------------------------------------------------------
# _parse_codex_output — exec --json event formats
# ---------------------------------------------------------------------------


def test_parse_item_completed_mcp_tool_call():
    """item.completed mcp_tool_call payloads normalize to name + input."""
    lines = "\n".join([
        json.dumps({
            "type": "item.completed",
            "item": {
                "id": "mcp_1",
                "type": "mcp_tool_call",
                "call": {"name": "route_to_butler", "arguments": {"butler": "rel"}},
            },
        }),
        json.dumps({"type": "result", "result": "Routed"}),
    ])
    _, tool_calls, _ = _parse_codex_output(lines, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "mcp_1"
    assert tool_calls[0]["name"] == "route_to_butler"


# ---------------------------------------------------------------------------
# _extract_tool_call — special formats
# ---------------------------------------------------------------------------


def test_extract_tool_call_parses_json_string_arguments():
    """Stringified JSON arguments are parsed into dict."""
    tc = _extract_tool_call({
        "id": "fc2",
        "name": "route_to_butler",
        "arguments": '{"butler":"health","prompt":"Track meal"}',
    })
    assert tc["input"] == {"butler": "health", "prompt": "Track meal"}


def test_extract_tool_call_command_execution():
    """command_execution events are normalized as tool calls."""
    tc = _extract_tool_call({
        "id": "cmd1",
        "type": "command_execution",
        "command": "ls -1",
        "exit_code": 0,
        "aggregated_output": "file.txt\n",
    })
    assert tc["name"] == "command_execution"
    assert tc["input"]["command"] == "ls -1"
    assert tc["input"]["exit_code"] == 0


# ---------------------------------------------------------------------------
# invoke() — key behaviors
# ---------------------------------------------------------------------------


async def test_invoke_uses_exec_subcommand():
    """invoke() uses codex exec subcommand."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    cmd = mock_sub.call_args[0]
    assert cmd[:2] == ("/usr/bin/codex", "exec")


async def test_invoke_injects_home_for_config_discovery():
    """invoke() sets HOME to a temp dir for Codex config discovery."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test", system_prompt="", mcp_servers={"s": {"url": "http://localhost/mcp"}},
            env={},
        )

    env_kwarg = mock_sub.call_args[1].get("env")
    assert "HOME" in env_kwarg


async def test_invoke_error_paths():
    """invoke() raises RuntimeError on non-zero exit; transport failures include diagnostics."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()

    # Plain error
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: rate limit"))
    mock_proc.returncode = 1
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError, match="Codex CLI exited with code 1: Error: rate limit"):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    # Transport diagnostics
    mock_proc.communicate = AsyncMock(
        return_value=(b"", b"rmcp startup failed: 405 Method Not Allowed")
    )
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError) as exc_info:
            await adapter.invoke(
                prompt="test", system_prompt="",
                mcp_servers={"switchboard": {"url": "http://localhost:41100/sse"}}, env={},
            )
    assert "MCP transport diagnostics" in str(exc_info.value)
