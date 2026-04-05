"""Tests for GeminiAdapter — Gemini CLI runtime adapter.

Unique behaviors not in test_adapter_contract.py:
- Binary discovery (_find_gemini_binary)
- _filter_env passes all keys through
- parse_system_prompt_file: GEMINI.md priority, AGENTS.md fallback
- build_config_file writes gemini_mcp.json
- _parse_gemini_output: functionCall formats
- _extract_tool_call: Gemini-specific functionCall container
- invoke(): CLI flags, error paths
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import GeminiAdapter
from butlers.core.runtimes.gemini import (
    _extract_tool_call,
    _filter_env,
    _find_gemini_binary,
    _parse_gemini_output,
)

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


def test_find_gemini_binary():
    """_find_gemini_binary returns path when found; raises FileNotFoundError when missing."""
    with patch("butlers.core.runtimes.gemini.shutil.which", return_value="/usr/bin/gemini"):
        assert _find_gemini_binary() == "/usr/bin/gemini"
    with patch("butlers.core.runtimes.gemini.shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="Gemini CLI binary not found"):
            _find_gemini_binary()


# ---------------------------------------------------------------------------
# _filter_env — passes all keys through
# ---------------------------------------------------------------------------


def test_filter_env():
    """_filter_env passes all env vars through to Gemini (no filtering)."""
    env = {"GOOGLE_API_KEY": "gk-test", "ANTHROPIC_API_KEY": "sk-ant", "PATH": "/usr/bin"}
    assert _filter_env(env) == env
    assert _filter_env({}) == {}


# ---------------------------------------------------------------------------
# parse_system_prompt_file — GEMINI.md priority, AGENTS.md fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "files, expected",
    [
        ({"GEMINI.md": "Gemini instructions."}, "Gemini instructions."),
        ({"AGENTS.md": "Agent instructions."}, "Agent instructions."),
        (
            {"GEMINI.md": "Gemini instructions.", "AGENTS.md": "Agent fallback."},
            "Gemini instructions.",
        ),
        ({"GEMINI.md": "   \n  ", "AGENTS.md": "Agent fallback."}, "Agent fallback."),
        ({}, ""),
    ],
)
def test_parse_system_prompt(tmp_path: Path, files: dict, expected: str):
    """parse_system_prompt_file resolves GEMINI.md → AGENTS.md → empty."""
    adapter = GeminiAdapter()
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == expected


# ---------------------------------------------------------------------------
# build_config_file — gemini_mcp.json
# ---------------------------------------------------------------------------


def test_build_config_file_writes_gemini_mcp_json(tmp_path: Path):
    """build_config_file() writes gemini_mcp.json with mcpServers key."""
    adapter = GeminiAdapter()
    config_path = adapter.build_config_file(
        mcp_servers={"my-butler": {"url": "http://localhost:9100/mcp"}}, tmp_dir=tmp_path
    )
    assert config_path == tmp_path / "gemini_mcp.json"
    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["my-butler"]["url"] == "http://localhost:9100/mcp"


# ---------------------------------------------------------------------------
# _parse_gemini_output / _extract_tool_call — Gemini-specific formats
# ---------------------------------------------------------------------------


def test_parse_json_function_call():
    """Top-level functionCall event is extracted as a tool call."""
    line = json.dumps({
        "type": "functionCall",
        "id": "fc1",
        "functionCall": {"name": "my_tool", "args": {"arg1": "val1"}},
    })
    result_text, tool_calls = _parse_gemini_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "fc1"
    assert tool_calls[0]["name"] == "my_tool"
    assert tool_calls[0]["input"] == {"arg1": "val1"}


def test_extract_tool_call_gemini_function_formats():
    """Gemini functionCall with args and arguments keys are both supported."""
    tc1 = _extract_tool_call({"id": "fc1", "functionCall": {"name": "tool_a", "args": {"a": 1}}})
    assert tc1["name"] == "tool_a"
    assert tc1["input"] == {"a": 1}

    tc2 = _extract_tool_call({"id": "fc2", "functionCall": {"name": "tool_b", "arguments": {"b": 2}}})
    assert tc2["name"] == "tool_b"
    assert tc2["input"] == {"b": 2}


# ---------------------------------------------------------------------------
# invoke() — key behaviors
# ---------------------------------------------------------------------------


async def test_invoke():
    """invoke() calls subprocess with correct flags; parses text and functionCall output."""
    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")
    mock_proc = AsyncMock()
    mock_proc.returncode = 0

    # Basic success with required flags
    mock_proc.communicate = AsyncMock(
        return_value=(json.dumps({"type": "result", "result": "Task done."}).encode(), b"")
    )
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        result_text, _, _ = await adapter.invoke(
            prompt="do something", system_prompt="you are helpful",
            mcp_servers={"test": {"url": "http://localhost:9100/mcp"}},
            env={"GOOGLE_API_KEY": "gk-test"},
        )
    assert result_text == "Task done."
    cmd = mock_sub.call_args[0]
    assert cmd[0] == "/usr/bin/gemini" and "--sandbox=false" in cmd and "--system-prompt" in cmd

    # functionCall output
    output_lines = "\n".join([
        json.dumps({"type": "functionCall", "id": "fc1", "functionCall": {"name": "state_get", "args": {"key": "bar"}}}),
        json.dumps({"type": "result", "result": "Complete"}),
    ])
    mock_proc.communicate = AsyncMock(return_value=(output_lines.encode(), b""))
    with patch(_EXEC, return_value=mock_proc):
        result_text2, tool_calls, _ = await adapter.invoke(
            prompt="use tools", system_prompt="", mcp_servers={}, env={}
        )
    assert result_text2 == "Complete"
    assert len(tool_calls) == 1 and tool_calls[0]["name"] == "state_get"
