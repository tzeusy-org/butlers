"""Tests for GeminiAdapter — Gemini CLI runtime adapter."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import GeminiAdapter, get_adapter
from butlers.core.runtimes.gemini import (
    _extract_tool_call,
    _filter_env,
    _find_gemini_binary,
    _parse_gemini_output,
)

pytestmark = pytest.mark.unit

# Long patch target as constant to keep lines within 100 chars
_EXEC = "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


def test_gemini_adapter_registered():
    """get_adapter('gemini') returns the real GeminiAdapter (not the stub)."""
    cls = get_adapter("gemini")
    assert cls is GeminiAdapter


def test_gemini_adapter_is_runtime_adapter():
    """GeminiAdapter is a subclass of RuntimeAdapter."""
    from butlers.core.runtimes import RuntimeAdapter

    assert issubclass(GeminiAdapter, RuntimeAdapter)


def test_gemini_adapter_instantiates():
    """GeminiAdapter can be instantiated."""
    adapter = GeminiAdapter()
    assert adapter is not None


def test_gemini_adapter_with_custom_binary():
    """GeminiAdapter accepts a custom binary path."""
    adapter = GeminiAdapter(gemini_binary="/usr/local/bin/gemini")
    assert adapter._gemini_binary == "/usr/local/bin/gemini"
    assert adapter._get_binary() == "/usr/local/bin/gemini"


def test_gemini_adapter_create_worker_preserves_binary():
    """create_worker() returns a distinct adapter with the same binary config."""
    adapter = GeminiAdapter(gemini_binary="/usr/local/bin/gemini")
    worker = adapter.create_worker()

    assert worker is not adapter
    assert isinstance(worker, GeminiAdapter)
    assert worker._gemini_binary == "/usr/local/bin/gemini"


# ---------------------------------------------------------------------------
# _find_gemini_binary tests
# ---------------------------------------------------------------------------


def test_find_gemini_binary_found():
    """_find_gemini_binary returns path when gemini is on PATH."""
    with patch(
        "butlers.core.runtimes.gemini.shutil.which",
        return_value="/usr/bin/gemini",
    ):
        assert _find_gemini_binary() == "/usr/bin/gemini"


def test_find_gemini_binary_not_found():
    """_find_gemini_binary raises FileNotFoundError when gemini is missing."""
    with patch(
        "butlers.core.runtimes.gemini.shutil.which",
        return_value=None,
    ):
        with pytest.raises(FileNotFoundError, match="Gemini CLI binary not found"):
            _find_gemini_binary()


# ---------------------------------------------------------------------------
# _filter_env tests
# ---------------------------------------------------------------------------


def test_filter_env_passes_google_api_key():
    """GOOGLE_API_KEY is passed through to Gemini."""
    env = {"GOOGLE_API_KEY": "gk-test", "PATH": "/usr/bin"}
    filtered = _filter_env(env)
    assert "GOOGLE_API_KEY" in filtered
    assert filtered["GOOGLE_API_KEY"] == "gk-test"


def test_filter_env_excludes_anthropic_api_key():
    """ANTHROPIC_API_KEY is excluded from Gemini env."""
    env = {
        "GOOGLE_API_KEY": "gk-test",
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "PATH": "/usr/bin",
    }
    filtered = _filter_env(env)
    assert "ANTHROPIC_API_KEY" not in filtered
    assert "GOOGLE_API_KEY" in filtered
    assert "PATH" in filtered


def test_filter_env_empty():
    """Empty env dict returns empty dict."""
    assert _filter_env({}) == {}


def test_filter_env_only_anthropic_key():
    """Env with only ANTHROPIC_API_KEY returns empty dict."""
    env = {"ANTHROPIC_API_KEY": "sk-ant-secret"}
    filtered = _filter_env(env)
    assert filtered == {}


# ---------------------------------------------------------------------------
# parse_system_prompt_file tests
# ---------------------------------------------------------------------------


def test_parse_system_prompt_reads_gemini_md(tmp_path: Path):
    """GeminiAdapter prefers GEMINI.md for system prompt."""
    adapter = GeminiAdapter()
    gemini_md = tmp_path / "GEMINI.md"
    gemini_md.write_text("You are a Gemini butler.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "You are a Gemini butler."


def test_parse_system_prompt_falls_back_to_agents_md(tmp_path: Path):
    """GeminiAdapter falls back to AGENTS.md when GEMINI.md is missing."""
    adapter = GeminiAdapter()
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("You are an agent butler.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "You are an agent butler."


def test_parse_system_prompt_prefers_gemini_over_agents(tmp_path: Path):
    """GEMINI.md takes priority over AGENTS.md."""
    adapter = GeminiAdapter()
    gemini_md = tmp_path / "GEMINI.md"
    gemini_md.write_text("Gemini instructions.")
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("Agent instructions.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "Gemini instructions."


def test_parse_system_prompt_ignores_claude_md(tmp_path: Path):
    """GeminiAdapter does NOT read CLAUDE.md."""
    adapter = GeminiAdapter()
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("This is Claude instructions.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


def test_parse_system_prompt_missing_all(tmp_path: Path):
    """Returns empty string when no prompt files exist."""
    adapter = GeminiAdapter()
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


def test_parse_system_prompt_empty_gemini_md_falls_back(tmp_path: Path):
    """Falls back to AGENTS.md when GEMINI.md is empty."""
    adapter = GeminiAdapter()
    gemini_md = tmp_path / "GEMINI.md"
    gemini_md.write_text("   \n  ")
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("Agent fallback.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "Agent fallback."


def test_parse_system_prompt_both_empty(tmp_path: Path):
    """Returns empty string when both GEMINI.md and AGENTS.md are empty."""
    adapter = GeminiAdapter()
    gemini_md = tmp_path / "GEMINI.md"
    gemini_md.write_text("   \n  ")
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("  ")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


# ---------------------------------------------------------------------------
# build_config_file tests
# ---------------------------------------------------------------------------


def test_build_config_file_writes_gemini_mcp_json(tmp_path: Path):
    """build_config_file() writes gemini_mcp.json with mcpServers key."""
    adapter = GeminiAdapter()
    mcp_servers = {"my-butler": {"url": "http://localhost:9100/sse"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    assert config_path == tmp_path / "gemini_mcp.json"
    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["my-butler"]["url"] == "http://localhost:9100/sse"


def test_build_config_file_empty_servers(tmp_path: Path):
    """build_config_file() handles empty mcp_servers dict."""
    adapter = GeminiAdapter()
    config_path = adapter.build_config_file(mcp_servers={}, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert data["mcpServers"] == {}


def test_build_config_file_multiple_servers(tmp_path: Path):
    """build_config_file() handles multiple MCP servers."""
    adapter = GeminiAdapter()
    mcp_servers = {
        "butler-a": {"url": "http://localhost:9100/sse"},
        "butler-b": {"url": "http://localhost:9200/sse"},
    }
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert len(data["mcpServers"]) == 2
    assert "butler-a" in data["mcpServers"]
    assert "butler-b" in data["mcpServers"]


# ---------------------------------------------------------------------------
# _parse_gemini_output tests
# ---------------------------------------------------------------------------


def test_parse_plain_text_output():
    """Plain text stdout is returned as result_text."""
    result_text, tool_calls = _parse_gemini_output("Hello, world!", "", 0)
    assert result_text == "Hello, world!"
    assert tool_calls == []


def test_parse_empty_output():
    """Empty stdout returns None result_text."""
    result_text, tool_calls = _parse_gemini_output("", "", 0)
    assert result_text is None
    assert tool_calls == []


def test_parse_nonzero_exit_code():
    """Non-zero exit code returns error message."""
    result_text, tool_calls = _parse_gemini_output("", "Something went wrong", 1)
    assert result_text is not None
    assert "Something went wrong" in result_text
    assert tool_calls == []


def test_parse_nonzero_exit_code_with_stdout():
    """Non-zero exit code with stdout in error detail."""
    result_text, tool_calls = _parse_gemini_output("stdout error", "", 1)
    assert result_text is not None
    assert "stdout error" in result_text


def test_parse_json_message():
    """JSON message objects are parsed for text content."""
    line = json.dumps({"type": "message", "content": "Hello from Gemini"})
    result_text, tool_calls = _parse_gemini_output(line, "", 0)
    assert result_text == "Hello from Gemini"
    assert tool_calls == []


def test_parse_json_message_with_content_blocks():
    """JSON message with content blocks extracts text."""
    line = json.dumps(
        {
            "type": "message",
            "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ],
        }
    )
    result_text, tool_calls = _parse_gemini_output(line, "", 0)
    assert "Part 1" in result_text
    assert "Part 2" in result_text


def test_parse_json_tool_use():
    """JSON tool_use objects are extracted as tool calls."""
    line = json.dumps(
        {
            "type": "tool_use",
            "id": "t1",
            "name": "state_get",
            "input": {"key": "foo"},
        }
    )
    result_text, tool_calls = _parse_gemini_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "t1"
    assert tool_calls[0]["name"] == "state_get"
    assert tool_calls[0]["input"] == {"key": "foo"}


def test_parse_json_function_call():
    """JSON functionCall objects are extracted as tool calls."""
    line = json.dumps(
        {
            "type": "functionCall",
            "id": "fc1",
            "functionCall": {
                "name": "my_tool",
                "args": {"arg1": "val1"},
            },
        }
    )
    result_text, tool_calls = _parse_gemini_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "fc1"
    assert tool_calls[0]["name"] == "my_tool"
    assert tool_calls[0]["input"] == {"arg1": "val1"}


def test_parse_json_result():
    """JSON result objects extract the result field."""
    line = json.dumps({"type": "result", "result": "Task completed."})
    result_text, tool_calls = _parse_gemini_output(line, "", 0)
    assert result_text == "Task completed."


def test_parse_mixed_json_lines():
    """Multiple JSON lines with messages and tool calls."""
    lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "state_get",
                    "input": {},
                }
            ),
            json.dumps({"type": "message", "content": "Done!"}),
        ]
    )
    result_text, tool_calls = _parse_gemini_output(lines, "", 0)
    assert result_text == "Done!"
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "state_get"


def test_parse_tool_call_in_content_block():
    """Tool calls embedded in message content blocks are extracted."""
    line = json.dumps(
        {
            "type": "message",
            "content": [
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "kv_set",
                    "input": {"key": "x", "value": 1},
                },
            ],
        }
    )
    result_text, tool_calls = _parse_gemini_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "kv_set"


def test_parse_unknown_json_with_text_field():
    """Unknown JSON types with a 'text' field still yield text."""
    line = json.dumps({"type": "unknown", "text": "some text"})
    result_text, tool_calls = _parse_gemini_output(line, "", 0)
    assert result_text == "some text"


def test_parse_function_call_in_content_block():
    """Gemini functionCall in message content blocks extracted."""
    line = json.dumps(
        {
            "type": "message",
            "content": [
                {
                    "type": "functionCall",
                    "id": "fc2",
                    "functionCall": {
                        "name": "search",
                        "args": {"query": "test"},
                    },
                },
            ],
        }
    )
    result_text, tool_calls = _parse_gemini_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "search"
    assert tool_calls[0]["input"] == {"query": "test"}


# ---------------------------------------------------------------------------
# _extract_tool_call tests
# ---------------------------------------------------------------------------


def test_extract_tool_call_standard():
    """Standard tool_use format is extracted correctly."""
    tc = _extract_tool_call(
        {
            "id": "t1",
            "name": "my_tool",
            "input": {"key": "val"},
        }
    )
    assert tc == {"id": "t1", "name": "my_tool", "input": {"key": "val"}}


def test_extract_tool_call_gemini_function_format():
    """Gemini functionCall format is extracted correctly."""
    tc = _extract_tool_call(
        {
            "id": "fc1",
            "functionCall": {"name": "other_tool", "args": {"a": 1}},
        }
    )
    assert tc["id"] == "fc1"
    assert tc["name"] == "other_tool"
    assert tc["input"] == {"a": 1}


def test_extract_tool_call_gemini_function_with_arguments():
    """Gemini functionCall with 'arguments' key instead of 'args'."""
    tc = _extract_tool_call(
        {
            "id": "fc2",
            "functionCall": {
                "name": "another_tool",
                "arguments": {"b": 2},
            },
        }
    )
    assert tc["name"] == "another_tool"
    assert tc["input"] == {"b": 2}


def test_extract_tool_call_missing_fields():
    """Missing fields default to empty string/dict."""
    tc = _extract_tool_call({})
    assert tc["id"] == ""
    assert tc["name"] == ""


# ---------------------------------------------------------------------------
# invoke() tests with mocked subprocess
# ---------------------------------------------------------------------------


async def test_invoke_success():
    """invoke() calls subprocess and parses JSON output."""
    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(
        return_value=(
            json.dumps({"type": "result", "result": "Task done."}).encode(),
            b"",
        )
    )
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="do something",
            system_prompt="you are helpful",
            mcp_servers={"test": {"url": "http://localhost:9100/sse"}},
            env={"GOOGLE_API_KEY": "gk-test"},
        )

    assert result_text == "Task done."
    assert tool_calls == []
    assert usage is None

    # Verify subprocess was called with correct args
    call_args = mock_sub.call_args
    cmd = call_args[0]
    assert cmd[0] == "/usr/bin/gemini"
    assert "--sandbox=false" in cmd
    assert "--system-prompt" in cmd
    assert "--prompt" in cmd
    assert "do something" in cmd


async def test_invoke_with_tool_calls():
    """invoke() captures tool calls from Gemini output."""
    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")

    output_lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "state_get",
                    "input": {"key": "foo"},
                }
            ),
            json.dumps({"type": "result", "result": "Done"}),
        ]
    )

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output_lines.encode(), b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc):
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="use tools",
            system_prompt="helpful",
            mcp_servers={},
            env={},
        )

    assert result_text == "Done"
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "state_get"
    assert tool_calls[0]["input"] == {"key": "foo"}
    assert usage is None


async def test_invoke_nonzero_exit():
    """invoke() handles non-zero exit code."""
    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: quota exceeded"))
    mock_proc.returncode = 1

    with patch(_EXEC, return_value=mock_proc):
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    assert result_text is not None
    assert "quota exceeded" in result_text
    assert tool_calls == []
    assert usage is None


async def test_invoke_no_system_prompt():
    """invoke() works without system prompt (omits --system-prompt)."""
    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    cmd = mock_sub.call_args[0]
    assert "--system-prompt" not in cmd


async def test_invoke_filters_env():
    """invoke() filters env to exclude ANTHROPIC_API_KEY."""
    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    env = {
        "GOOGLE_API_KEY": "gk-test",
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "PATH": "/usr/bin",
    }

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env=env,
        )

    call_kwargs = mock_sub.call_args[1]
    passed_env = call_kwargs["env"]
    assert "GOOGLE_API_KEY" in passed_env
    assert "ANTHROPIC_API_KEY" not in passed_env
    assert "PATH" in passed_env


async def test_invoke_passes_cwd():
    """invoke() passes working directory to subprocess."""
    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
            cwd=Path("/tmp/workdir"),
        )

    call_kwargs = mock_sub.call_args[1]
    assert call_kwargs["cwd"] == "/tmp/workdir"


async def test_invoke_timeout():
    """invoke() raises TimeoutError on subprocess timeout."""

    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()

    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(TimeoutError, match="timed out"):
            await adapter.invoke(
                prompt="slow task",
                system_prompt="",
                mcp_servers={},
                env={},
                timeout=1,
            )


async def test_invoke_binary_not_found():
    """invoke() raises FileNotFoundError if gemini not on PATH."""
    adapter = GeminiAdapter()  # No binary specified, auto-detect

    with patch(
        "butlers.core.runtimes.gemini.shutil.which",
        return_value=None,
    ):
        with pytest.raises(FileNotFoundError, match="Gemini CLI binary not found"):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
            )


async def test_invoke_with_gemini_function_call_output():
    """invoke() correctly parses Gemini-style functionCall output."""
    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")

    output_lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "functionCall",
                    "id": "fc1",
                    "functionCall": {
                        "name": "state_get",
                        "args": {"key": "bar"},
                    },
                }
            ),
            json.dumps({"type": "result", "result": "Complete"}),
        ]
    )

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output_lines.encode(), b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc):
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="use gemini tools",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    assert result_text == "Complete"
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "state_get"
    assert tool_calls[0]["input"] == {"key": "bar"}
    assert usage is None


# ---------------------------------------------------------------------------
# Import path tests
# ---------------------------------------------------------------------------


def test_gemini_adapter_importable_from_runtimes():
    """GeminiAdapter is importable from butlers.core.runtimes."""
    from butlers.core.runtimes import GeminiAdapter as GA

    assert GA is GeminiAdapter
