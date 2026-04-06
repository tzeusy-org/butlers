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
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes.opencode import (
    OpenCodeAdapter,
    _extract_opencode_tool_call,
    _extract_usage,
    _find_opencode_binary,
    _looks_like_tool_call_event,
    _parse_opencode_output,
)

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.opencode.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# parse_system_prompt_file — OPENCODE.md priority, AGENTS.md fallback
# ---------------------------------------------------------------------------


def test_parse_system_prompt(tmp_path: Path):
    """parse_system_prompt_file: OPENCODE.md > AGENTS.md; empty OPENCODE.md falls to AGENTS.md."""
    adapter = OpenCodeAdapter()
    # OPENCODE.md takes priority
    (tmp_path / "OPENCODE.md").write_text("OpenCode instructions.")
    (tmp_path / "AGENTS.md").write_text("Agent fallback.")
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == "OpenCode instructions."
    # blank OPENCODE.md falls back to AGENTS.md
    (tmp_path / "OPENCODE.md").write_text("   \n  ")
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == "Agent fallback."
    # AGENTS.md only
    (tmp_path / "OPENCODE.md").unlink()
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == "Agent fallback."
    # nothing → empty
    (tmp_path / "AGENTS.md").unlink()
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == ""
    # CLAUDE.md ignored
    (tmp_path / "CLAUDE.md").write_text("Claude only.")
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == ""


# ---------------------------------------------------------------------------
# build_config_file — remote entry structure and validation
# ---------------------------------------------------------------------------


def test_build_config_file(tmp_path: Path, caplog):
    """build_config_file(): valid server written; invalid/missing-url servers skipped."""
    adapter = OpenCodeAdapter()
    bad_servers = [
        {"bad": "not-a-dict"},
        {"no-url": {"transport": "remote"}},
        {"empty-url": {"url": "   "}},
    ]
    for bad in bad_servers:
        mcp_servers = {"valid": {"url": "http://localhost:9100/mcp"}, **bad}
        with caplog.at_level(logging.WARNING):
            config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
        data = json.loads(config_path.read_text())
        assert "valid" in data["mcp"]
        for bad_name in bad:
            assert bad_name not in data["mcp"]
    # Check structure for a valid server
    mcp_servers = {"my-butler": {"url": "http://localhost:9100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    assert config_path == tmp_path / "opencode.jsonc"
    data = json.loads(config_path.read_text())
    assert data["permission"] == {}
    entry = data["mcp"]["my-butler"]
    assert entry["type"] == "remote" and entry["url"] == "http://localhost:9100/mcp"
    assert entry["enabled"] is True


# ---------------------------------------------------------------------------
# _parse_opencode_output — unique event types and tool call formats
# ---------------------------------------------------------------------------


def test_parse_opencode_unique_events():
    """OpenCode-specific event types: text variants, result, item.completed, tool call types."""
    # text event variants
    for event, expected in [
        ({"type": "text", "text": "Hello"}, "Hello"),
        ({"type": "text", "content": "Content"}, "Content"),
        ({"type": "text", "delta": "Delta"}, "Delta"),
        ({"type": "result", "result": "Task complete"}, "Task complete"),
        ({"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}}, "hi"),
        ({"type": "assistant", "content": "Direct content"}, "Direct content"),
    ]:
        text, _, _ = _parse_opencode_output(json.dumps(event), "", 0)
        assert text == expected, f"Failed for event {event}"

    # tool call event types
    for event_type in ["tool_use", "tool_call", "function_call", "mcp_tool_call"]:
        event = {"type": event_type, "id": "t1", "name": "do_thing", "input": {"k": "v"}}
        _, tool_calls, _ = _parse_opencode_output(json.dumps(event), "", 0)
        assert len(tool_calls) == 1 and tool_calls[0]["name"] == "do_thing"


# ---------------------------------------------------------------------------
# _looks_like_tool_call_event and _extract_opencode_tool_call
# ---------------------------------------------------------------------------


def test_looks_like_tool_call_event():
    """_looks_like_tool_call_event correctly identifies tool call objects."""
    positives = [
        {"type": "tool_use"},
        {"type": "function_call"},
        {"type": "mcp_tool_call"},
        {"name": "my_tool", "input": {"a": 1}},
        {"name": "my_tool", "arguments": {"a": 1}},
        {"function": {"name": "my_fn", "arguments": {"x": 1}}},
    ]
    negatives = [{"type": "text", "text": "hello"}, {"name": "", "input": {"a": 1}}]
    for obj in positives:
        assert _looks_like_tool_call_event(obj) is True, f"Expected True for {obj}"
    for obj in negatives:
        assert _looks_like_tool_call_event(obj) is False, f"Expected False for {obj}"


def test_extract_opencode_tool_call_formats():
    """_extract_opencode_tool_call normalizes standard, function, call, and string-arg formats."""
    cases = [
        ({"id": "t1", "name": "do_thing", "input": {"k": "v"}}, "do_thing", {"k": "v"}),
        (
            {"id": "fc1", "function": {"name": "my_tool", "arguments": {"x": 1}}},
            "my_tool",
            {"x": 1},
        ),
        (
            {"id": "mcp_1", "call": {"name": "router", "arguments": {"b": "g"}}},
            "router",
            {"b": "g"},
        ),
        ({"id": "t3", "name": "fn", "arguments": '{"k":"v"}'}, "fn", {"k": "v"}),
    ]
    for event, expected_name, expected_input in cases:
        tc = _extract_opencode_tool_call(event)
        assert tc["name"] == expected_name and tc["input"] == expected_input


# ---------------------------------------------------------------------------
# _extract_usage, _find_opencode_binary, invoke()
# ---------------------------------------------------------------------------


def test_extract_usage_and_find_binary():
    """_extract_usage handles all token formats and non-dict input; binary raises when missing."""
    cases = [
        ({"input_tokens": 100, "output_tokens": 50}, {"input_tokens": 100, "output_tokens": 50}),
        (
            {"usage": {"input_tokens": 200, "output_tokens": 80}},
            {"input_tokens": 200, "output_tokens": 80},
        ),
        (
            {"prompt_tokens": 150, "completion_tokens": 60},
            {"input_tokens": 150, "output_tokens": 60},
        ),
        ({"type": "text", "text": "hello"}, None),
        ({"input_tokens": "many", "output_tokens": None}, None),
        (None, None),
        ("string", None),
    ]
    for event, expected in cases:
        assert _extract_usage(event) == expected, f"Failed for {event}"  # type: ignore[arg-type]

    with patch("butlers.core.runtimes.opencode.shutil.which", return_value="/usr/bin/opencode"):
        assert _find_opencode_binary() == "/usr/bin/opencode"
    with patch("butlers.core.runtimes.opencode.shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="OpenCode CLI binary not found"):
            _find_opencode_binary()


async def test_invoke_success_and_config():
    """invoke() calls subprocess with run subcommand; injects OPENCODE_CONFIG; forwards --model."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    output_lines = "\n".join(
        [
            json.dumps({"type": "text", "text": "Task done."}),
            json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 20}}
            ),
        ]
    )
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output_lines.encode(), b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        result_text, _, usage = await adapter.invoke(
            prompt="do something",
            system_prompt="you are helpful",
            mcp_servers={"test": {"url": "http://localhost:9100/mcp"}},
            env={"ANTHROPIC_API_KEY": "sk-test"},
        )
    assert result_text == "Task done." and usage == {"input_tokens": 10, "output_tokens": 20}
    cmd = mock_sub.call_args[0]
    assert cmd[0] == "/usr/bin/opencode" and cmd[1] == "run" and "--format" in cmd
    env = mock_sub.call_args[1]["env"]
    assert "OPENCODE_CONFIG" in env and env["OPENCODE_CONFIG"].endswith("opencode.jsonc")

    # --model flag present/absent
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="run",
            system_prompt="",
            mcp_servers={},
            env={},
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
