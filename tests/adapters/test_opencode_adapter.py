"""Tests for OpenCodeAdapter.

Covers unique OpenCode behaviors not in test_adapter_contract.py:
- parse_system_prompt_file: OPENCODE.md priority, AGENTS.md fallback
- build_config_file: remote entry structure, validation/skip logic
- _parse_opencode_output: unique event types (text variants, tool call formats, usage)
- _extract_opencode_tool_call: format normalization
- _looks_like_tool_call_event: heuristic detection
- _extract_usage: token extraction and format mapping
- _find_opencode_binary: PATH discovery
- invoke(): OPENCODE_CONFIG injection, model flag, error paths
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import get_adapter
from butlers.core.runtimes.opencode import (
    OpenCodeAdapter,
    _extract_envelope_tool_call,
    _extract_opencode_tool_call,
    _extract_usage,
    _find_opencode_binary,
    _looks_like_tool_call_event,
    _parse_opencode_output,
)

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.opencode.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# parse_system_prompt_file — unique behaviors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "files, expected",
    [
        ({"OPENCODE.md": "OpenCode instructions."}, "OpenCode instructions."),
        ({"AGENTS.md": "Agent instructions."}, "Agent instructions."),
        (
            {"OPENCODE.md": "OpenCode instructions.", "AGENTS.md": "Agent fallback."},
            "OpenCode instructions.",
        ),
        ({"OPENCODE.md": "   \n  ", "AGENTS.md": "Agent fallback."}, "Agent fallback."),
        ({}, ""),
        ({"CLAUDE.md": "Claude instructions."}, ""),  # CLAUDE.md is ignored
    ],
)
def test_parse_system_prompt(tmp_path: Path, files: dict, expected: str):
    """parse_system_prompt_file resolves OPENCODE.md → AGENTS.md → empty."""
    adapter = OpenCodeAdapter()
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == expected


# ---------------------------------------------------------------------------
# build_config_file — remote entry structure and validation
# ---------------------------------------------------------------------------


def test_build_config_file_structure(tmp_path: Path):
    """build_config_file() writes opencode.jsonc with mcp and permission keys."""
    adapter = OpenCodeAdapter()
    mcp_servers = {"my-butler": {"url": "http://localhost:9100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    assert config_path == tmp_path / "opencode.jsonc"
    data = json.loads(config_path.read_text())
    assert data["permission"] == {}
    entry = data["mcp"]["my-butler"]
    assert entry["type"] == "remote"
    assert entry["url"] == "http://localhost:9100/mcp"
    assert entry["enabled"] is True


@pytest.mark.parametrize(
    "bad_server",
    [
        {"bad": "not-a-dict"},
        {"no-url": {"transport": "remote"}},
        {"empty-url": {"url": "   "}},
    ],
)
def test_build_config_file_skips_invalid_servers(tmp_path: Path, bad_server: dict, caplog):
    """build_config_file() skips servers with invalid config and logs warning."""
    adapter = OpenCodeAdapter()
    mcp_servers = {"valid": {"url": "http://localhost:9100/mcp"}, **bad_server}
    with caplog.at_level(logging.WARNING):
        config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert "valid" in data["mcp"]
    for bad_name in bad_server:
        assert bad_name not in data["mcp"]


# ---------------------------------------------------------------------------
# _parse_opencode_output — unique OpenCode event types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event, expected_text",
    [
        # text event: various field names
        ({"type": "text", "text": "Hello"}, "Hello"),
        ({"type": "text", "content": "Content"}, "Content"),
        ({"type": "text", "delta": "Delta"}, "Delta"),
        # result event
        ({"type": "result", "result": "Task complete"}, "Task complete"),
        # nested item types
        ({"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}}, "hi"),
        ({"type": "assistant", "content": "Direct content"}, "Direct content"),
    ],
)
def test_parse_event_text(event: dict, expected_text: str):
    """Various event types yield correct result_text."""
    result_text, tool_calls, usage = _parse_opencode_output(json.dumps(event), "", 0)
    assert result_text == expected_text


@pytest.mark.parametrize("event_type", ["tool_use", "tool_call", "function_call", "mcp_tool_call"])
def test_parse_tool_call_event_types(event_type: str):
    """Common tool call event types all produce tool_calls."""
    event = {"type": event_type, "id": "t1", "name": "do_thing", "input": {"k": "v"}}
    _, tool_calls, _ = _parse_opencode_output(json.dumps(event), "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "do_thing"


# ---------------------------------------------------------------------------
# _extract_usage — token format normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event, expected",
    [
        ({"input_tokens": 100, "output_tokens": 50}, {"input_tokens": 100, "output_tokens": 50}),
        (
            {"usage": {"input_tokens": 200, "output_tokens": 80}},
            {"input_tokens": 200, "output_tokens": 80},
        ),
        ({"prompt_tokens": 150, "completion_tokens": 60}, {"input_tokens": 150, "output_tokens": 60}),
        ({"type": "text", "text": "hello"}, None),
        ({"input_tokens": "many", "output_tokens": None}, None),
        (None, None),  # non-dict returns None
        ("string", None),  # non-dict returns None
    ],
)
def test_extract_usage(event: dict | None, expected):
    """_extract_usage handles all token formats, invalid cases, and non-dict input."""
    assert _extract_usage(event) == expected  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _looks_like_tool_call_event
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj, expected",
    [
        ({"type": "tool_use"}, True),
        ({"type": "function_call"}, True),
        ({"type": "mcp_tool_call"}, True),
        ({"name": "my_tool", "input": {"a": 1}}, True),
        ({"name": "my_tool", "arguments": {"a": 1}}, True),
        ({"function": {"name": "my_fn", "arguments": {"x": 1}}}, True),
        ({"type": "text", "text": "hello"}, False),
        ({"name": "", "input": {"a": 1}}, False),
    ],
)
def test_looks_like_tool_call_event(obj: dict, expected: bool):
    """_looks_like_tool_call_event correctly identifies tool call objects."""
    assert _looks_like_tool_call_event(obj) is expected


# ---------------------------------------------------------------------------
# _extract_opencode_tool_call — format normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event, expected_name, expected_input",
    [
        # Standard tool_use
        ({"id": "t1", "name": "do_thing", "input": {"k": "v"}}, "do_thing", {"k": "v"}),
        # function container
        ({"id": "fc1", "function": {"name": "my_tool", "arguments": {"x": 1}}}, "my_tool", {"x": 1}),
        # call container (MCP style)
        ({"id": "mcp_1", "call": {"name": "router", "arguments": {"b": "g"}}}, "router", {"b": "g"}),
        # stringified JSON arguments
        ({"id": "t3", "name": "fn", "arguments": '{"k":"v"}'}, "fn", {"k": "v"}),
    ],
)
def test_extract_tool_call_formats(event, expected_name, expected_input):
    """_extract_opencode_tool_call normalizes all container formats."""
    tc = _extract_opencode_tool_call(event)
    assert tc["name"] == expected_name
    assert tc["input"] == expected_input


# ---------------------------------------------------------------------------
# _find_opencode_binary
# ---------------------------------------------------------------------------


def test_find_opencode_binary():
    """_find_opencode_binary returns path when found, raises FileNotFoundError when missing."""
    with patch("butlers.core.runtimes.opencode.shutil.which", return_value="/usr/bin/opencode"):
        assert _find_opencode_binary() == "/usr/bin/opencode"
    with patch("butlers.core.runtimes.opencode.shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="OpenCode CLI binary not found"):
            _find_opencode_binary()


# ---------------------------------------------------------------------------
# invoke() — key behavioral contracts
# ---------------------------------------------------------------------------


async def test_invoke_success():
    """invoke() calls subprocess with run subcommand and parses output."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    output_lines = "\n".join([
        json.dumps({"type": "text", "text": "Task done."}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 20}}),
    ])
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output_lines.encode(), b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="do something",
            system_prompt="you are helpful",
            mcp_servers={"test": {"url": "http://localhost:9100/mcp"}},
            env={"ANTHROPIC_API_KEY": "sk-test"},
        )

    assert result_text == "Task done."
    assert usage == {"input_tokens": 10, "output_tokens": 20}
    cmd = mock_sub.call_args[0]
    assert cmd[0] == "/usr/bin/opencode"
    assert cmd[1] == "run"
    assert "--format" in cmd


async def test_invoke_config_injection_and_model_flag():
    """invoke() injects OPENCODE_CONFIG when MCP servers provided; forwards --model flag."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    # OPENCODE_CONFIG injection
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test", system_prompt="", mcp_servers={"s": {"url": "http://localhost:9100/mcp"}},
            env={},
        )
    env = mock_sub.call_args[1]["env"]
    assert "OPENCODE_CONFIG" in env and env["OPENCODE_CONFIG"].endswith("opencode.jsonc")

    # --model flag
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="run", system_prompt="", mcp_servers={}, env={},
            model="anthropic/claude-sonnet-4-5",
        )
    cmd = mock_sub.call_args[0]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "anthropic/claude-sonnet-4-5"

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="run", system_prompt="", mcp_servers={}, env={}, model=None)
    assert "--model" not in mock_sub.call_args[0]


async def test_invoke_error_paths():
    """invoke() raises RuntimeError on non-zero exit; TimeoutError and kill on timeout."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    mock_proc = AsyncMock()

    mock_proc.communicate = AsyncMock(return_value=(b"", b"rate limit exceeded"))
    mock_proc.returncode = 1
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError, match="rate limit exceeded"):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(TimeoutError, match="OpenCode CLI timed out"):
            await adapter.invoke(prompt="slow", system_prompt="", mcp_servers={}, env={}, timeout=1)
    mock_proc.kill.assert_called_once()
