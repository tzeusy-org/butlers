"""Tests for CodexAdapter — Codex CLI runtime adapter."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import CodexAdapter, get_adapter
from butlers.core.runtimes.codex import (
    _extract_tool_call,
    _find_codex_binary,
    _parse_codex_output,
)

pytestmark = pytest.mark.unit

# Long patch target as constant to keep lines within 100 chars
_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


def test_codex_adapter_registered():
    """get_adapter('codex') returns the real CodexAdapter (not the stub)."""
    cls = get_adapter("codex")
    assert cls is CodexAdapter


def test_codex_adapter_is_runtime_adapter():
    """CodexAdapter is a subclass of RuntimeAdapter."""
    from butlers.core.runtimes import RuntimeAdapter

    assert issubclass(CodexAdapter, RuntimeAdapter)


def test_codex_adapter_instantiates():
    """CodexAdapter can be instantiated."""
    adapter = CodexAdapter()
    assert adapter is not None


def test_codex_adapter_with_custom_binary():
    """CodexAdapter accepts a custom binary path."""
    adapter = CodexAdapter(codex_binary="/usr/local/bin/codex")
    assert adapter._codex_binary == "/usr/local/bin/codex"
    assert adapter._get_binary() == "/usr/local/bin/codex"


def test_codex_adapter_create_worker_preserves_binary():
    """create_worker() returns a distinct adapter with the same binary config."""
    adapter = CodexAdapter(codex_binary="/usr/local/bin/codex")
    worker = adapter.create_worker()

    assert worker is not adapter
    assert isinstance(worker, CodexAdapter)
    assert worker._codex_binary == "/usr/local/bin/codex"


# ---------------------------------------------------------------------------
# _find_codex_binary tests
# ---------------------------------------------------------------------------


def test_find_codex_binary_found():
    """_find_codex_binary returns path when codex is on PATH."""
    with patch(
        "butlers.core.runtimes.codex.shutil.which",
        return_value="/usr/bin/codex",
    ):
        assert _find_codex_binary() == "/usr/bin/codex"


def test_find_codex_binary_not_found():
    """_find_codex_binary raises FileNotFoundError when codex is missing."""
    with patch(
        "butlers.core.runtimes.codex.shutil.which",
        return_value=None,
    ):
        with pytest.raises(FileNotFoundError, match="Codex CLI binary not found"):
            _find_codex_binary()


# ---------------------------------------------------------------------------
# parse_system_prompt_file tests
# ---------------------------------------------------------------------------


def test_parse_system_prompt_reads_agents_md(tmp_path: Path):
    """CodexAdapter reads AGENTS.md (not CLAUDE.md) for system prompt."""
    adapter = CodexAdapter()
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("You are a specialized Codex butler.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "You are a specialized Codex butler."


def test_parse_system_prompt_ignores_claude_md(tmp_path: Path):
    """CodexAdapter does NOT read CLAUDE.md — only AGENTS.md."""
    adapter = CodexAdapter()
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("This is Claude instructions.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


def test_parse_system_prompt_missing_agents_md(tmp_path: Path):
    """Returns empty string when AGENTS.md is missing."""
    adapter = CodexAdapter()
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


def test_parse_system_prompt_empty_agents_md(tmp_path: Path):
    """Returns empty string when AGENTS.md is empty/whitespace."""
    adapter = CodexAdapter()
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("   \n  ")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


# ---------------------------------------------------------------------------
# build_config_file tests
# ---------------------------------------------------------------------------


def test_build_config_file_writes_codex_json(tmp_path: Path):
    """build_config_file() writes codex.json with mcpServers key."""
    adapter = CodexAdapter()
    mcp_servers = {"my-butler": {"url": "http://localhost:9100/sse"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    assert config_path == tmp_path / "codex.json"
    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["my-butler"]["url"] == "http://localhost:9100/sse"


def test_build_config_file_empty_servers(tmp_path: Path):
    """build_config_file() handles empty mcp_servers dict."""
    adapter = CodexAdapter()
    config_path = adapter.build_config_file(mcp_servers={}, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert data["mcpServers"] == {}


def test_build_config_file_multiple_servers(tmp_path: Path):
    """build_config_file() handles multiple MCP servers."""
    adapter = CodexAdapter()
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
# _parse_codex_output tests
# ---------------------------------------------------------------------------


def test_parse_plain_text_output():
    """Plain text stdout is returned as result_text."""
    result_text, tool_calls = _parse_codex_output("Hello, world!", "", 0)
    assert result_text == "Hello, world!"
    assert tool_calls == []


def test_parse_empty_output():
    """Empty stdout returns None result_text."""
    result_text, tool_calls = _parse_codex_output("", "", 0)
    assert result_text is None
    assert tool_calls == []


def test_parse_nonzero_exit_code():
    """Non-zero exit code returns error message."""
    result_text, tool_calls = _parse_codex_output("", "Something went wrong", 1)
    assert result_text is not None
    assert "Something went wrong" in result_text
    assert tool_calls == []


def test_parse_nonzero_exit_code_with_stdout():
    """Non-zero exit code with stdout in error detail."""
    result_text, tool_calls = _parse_codex_output("stdout error", "", 1)
    assert result_text is not None
    assert "stdout error" in result_text


def test_parse_json_message():
    """JSON message objects are parsed for text content."""
    line = json.dumps({"type": "message", "content": "Hello from Codex"})
    result_text, tool_calls = _parse_codex_output(line, "", 0)
    assert result_text == "Hello from Codex"
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
    result_text, tool_calls = _parse_codex_output(line, "", 0)
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
    result_text, tool_calls = _parse_codex_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "t1"
    assert tool_calls[0]["name"] == "state_get"
    assert tool_calls[0]["input"] == {"key": "foo"}


def test_parse_json_function_call():
    """JSON function_call objects are extracted as tool calls."""
    line = json.dumps(
        {
            "type": "function_call",
            "id": "fc1",
            "function": {
                "name": "my_tool",
                "arguments": {"arg1": "val1"},
            },
        }
    )
    result_text, tool_calls = _parse_codex_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "fc1"
    assert tool_calls[0]["name"] == "my_tool"
    assert tool_calls[0]["input"] == {"arg1": "val1"}


def test_parse_json_result():
    """JSON result objects extract the result field."""
    line = json.dumps({"type": "result", "result": "Task completed."})
    result_text, tool_calls = _parse_codex_output(line, "", 0)
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
    result_text, tool_calls = _parse_codex_output(lines, "", 0)
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
    result_text, tool_calls = _parse_codex_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "kv_set"


def test_parse_unknown_json_with_text_field():
    """Unknown JSON types with a 'text' field still yield text."""
    line = json.dumps({"type": "unknown", "text": "some text"})
    result_text, tool_calls = _parse_codex_output(line, "", 0)
    assert result_text == "some text"


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


def test_extract_tool_call_function_format():
    """OpenAI function_call format is extracted correctly."""
    tc = _extract_tool_call(
        {
            "id": "fc1",
            "function": {"name": "other_tool", "arguments": {"a": 1}},
        }
    )
    assert tc["id"] == "fc1"
    assert tc["name"] == "other_tool"
    assert tc["input"] == {"a": 1}


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
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

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
            env={"OPENAI_API_KEY": "sk-test"},
        )

    assert result_text == "Task done."
    assert tool_calls == []
    assert usage is None

    # Verify subprocess was called with correct args
    call_args = mock_sub.call_args
    cmd = call_args[0]
    assert cmd[0] == "/usr/bin/codex"
    assert "--full-auto" in cmd
    assert "--quiet" in cmd
    assert "--instructions" in cmd
    assert "do something" in cmd


async def test_invoke_passes_model_flag():
    """invoke() forwards model to Codex CLI when provided."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="run",
            system_prompt="",
            mcp_servers={},
            env={},
            model="gpt-5.3-codex-spark",
        )

    cmd = mock_sub.call_args[0]
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "gpt-5.3-codex-spark"


async def test_invoke_with_tool_calls():
    """invoke() captures tool calls from Codex output."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

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
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: rate limit"))
    mock_proc.returncode = 1

    with patch(_EXEC, return_value=mock_proc):
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    assert result_text is not None
    assert "rate limit" in result_text
    assert tool_calls == []
    assert usage is None


async def test_invoke_no_system_prompt():
    """invoke() works without system prompt (omits --instructions)."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

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
    assert "--instructions" not in cmd


async def test_invoke_passes_env():
    """invoke() passes filtered env vars to subprocess."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    env = {"OPENAI_API_KEY": "sk-test", "PATH": "/usr/bin"}

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env=env,
        )

    call_kwargs = mock_sub.call_args[1]
    assert call_kwargs["env"] == env


async def test_invoke_passes_cwd():
    """invoke() passes working directory to subprocess."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

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

    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

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
    """invoke() raises FileNotFoundError if codex not on PATH."""
    adapter = CodexAdapter()  # No binary specified, auto-detect

    with patch(
        "butlers.core.runtimes.codex.shutil.which",
        return_value=None,
    ):
        with pytest.raises(FileNotFoundError, match="Codex CLI binary not found"):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
            )


# ---------------------------------------------------------------------------
# Import path tests
# ---------------------------------------------------------------------------


def test_codex_adapter_importable_from_runtimes():
    """CodexAdapter is importable from butlers.core.runtimes."""
    from butlers.core.runtimes import CodexAdapter as CA

    assert CA is CodexAdapter
