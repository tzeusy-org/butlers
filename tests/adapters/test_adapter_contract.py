"""Shared parser contract tests parametrized across CodexAdapter, GeminiAdapter, and
OpenCodeAdapter.

Each adapter has its own parser function and invoke() implementation, but they share a
common behavioral contract: plain text, JSON messages, tool calls, exit code handling.
Tests in this module verify that contract for all adapters without duplication.

Adapter-specific tests (unique output formats, env filtering, binary discovery errors,
system-prompt file resolution) remain in the native test files.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import (
    ClaudeCodeAdapter,
    CodexAdapter,
    GeminiAdapter,
    OpenCodeAdapter,
    RuntimeAdapter,
    get_adapter,
)
from butlers.core.runtimes.claude_code import _parse_claude_output
from butlers.core.runtimes.codex import _extract_tool_call as codex_extract_tool_call
from butlers.core.runtimes.codex import _parse_codex_output
from butlers.core.runtimes.gemini import _extract_tool_call as gemini_extract_tool_call
from butlers.core.runtimes.gemini import _parse_gemini_output
from butlers.core.runtimes.opencode import _extract_usage as opencode_extract_usage
from butlers.core.runtimes.opencode import _parse_opencode_output

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Adapter registry contract — all adapters register themselves
# ---------------------------------------------------------------------------


def test_adapter_registry_and_base_class() -> None:
    """All adapters register correctly, are RuntimeAdapter subclasses, and instantiate."""
    expected = {
        "codex": CodexAdapter,
        "gemini": GeminiAdapter,
        "opencode": OpenCodeAdapter,
        "claude": ClaudeCodeAdapter,
    }
    import butlers.core.runtimes as runtimes_module

    for name, cls in expected.items():
        assert get_adapter(name) is cls
        assert issubclass(cls, RuntimeAdapter)
        assert cls() is not None
        assert getattr(runtimes_module, cls.__name__) is cls


# ---------------------------------------------------------------------------
# build_config_file contract — shared mcpServers key structure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_class", [GeminiAdapter, ClaudeCodeAdapter])
def test_build_config_file_json_adapters(adapter_class: type, tmp_path: Path) -> None:
    """JSON adapters write mcpServers with empty and multi-server configs."""
    adapter = adapter_class()
    # empty servers
    empty_path = adapter.build_config_file(mcp_servers={}, tmp_dir=tmp_path)
    assert json.loads(empty_path.read_text())["mcpServers"] == {}
    # multi-server
    servers = {
        "butler-a": {"url": "http://localhost:9100/mcp"},
        "butler-b": {"url": "http://localhost:9200/mcp"},
    }
    multi_path = adapter.build_config_file(mcp_servers=servers, tmp_dir=tmp_path)
    data = json.loads(multi_path.read_text())
    assert len(data["mcpServers"]) == 2
    assert "butler-a" in data["mcpServers"] and "butler-b" in data["mcpServers"]


def test_codex_and_opencode_build_config_file(tmp_path: Path) -> None:
    """CodexAdapter writes TOML; OpenCodeAdapter writes JSONC mcp section."""
    servers = {
        "butler-a": {"url": "http://localhost:9100/mcp"},
        "butler-b": {"url": "http://localhost:9200/mcp"},
    }
    # Codex: TOML
    codex_path = CodexAdapter().build_config_file(mcp_servers=servers, tmp_dir=tmp_path)
    assert codex_path.name == "config.toml"
    content = codex_path.read_text()
    assert "[mcp_servers.butler-a]" in content and "[mcp_servers.butler-b]" in content
    # OpenCode: JSONC with mcp section
    oc_path = OpenCodeAdapter().build_config_file(mcp_servers=servers, tmp_dir=tmp_path)
    lines = [ln for ln in oc_path.read_text().splitlines() if not ln.strip().startswith("//")]
    data = json.loads("\n".join(lines))
    assert "butler-a" in data.get("mcp", {}) and "butler-b" in data.get("mcp", {})


# ---------------------------------------------------------------------------
# parse_system_prompt_file contract — non-Claude adapters ignore CLAUDE.md
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_class", [CodexAdapter, GeminiAdapter, OpenCodeAdapter])
def test_parse_system_prompt_ignores_claude_md(adapter_class: type, tmp_path: Path) -> None:
    """Non-Claude adapters do not read CLAUDE.md for their system prompt."""
    (tmp_path / "CLAUDE.md").write_text("This is Claude instructions.")
    assert adapter_class().parse_system_prompt_file(config_dir=tmp_path) == ""


# ---------------------------------------------------------------------------
# Helpers: normalize parser output to (result_text, tool_calls)
# ---------------------------------------------------------------------------


def _codex_parse(stdout: str, stderr: str, returncode: int) -> tuple[str | None, list]:
    result_text, tool_calls, _usage = _parse_codex_output(stdout, stderr, returncode)
    return result_text, tool_calls


def _gemini_parse(stdout: str, stderr: str, returncode: int) -> tuple[str | None, list]:
    return _parse_gemini_output(stdout, stderr)


def _opencode_parse(stdout: str, stderr: str, returncode: int) -> tuple[str | None, list]:
    result_text, tool_calls, _usage = _parse_opencode_output(stdout, stderr, returncode)
    return result_text, tool_calls


def _claude_parse(stdout: str, stderr: str, returncode: int) -> tuple[str | None, list]:
    result_text, tool_calls, _usage = _parse_claude_output(stdout, stderr, returncode)
    return result_text, tool_calls


_ALL_PARSERS = [_codex_parse, _gemini_parse, _opencode_parse, _claude_parse]

# ---------------------------------------------------------------------------
# Shared parser contract — plain text, empty, exit codes, JSON message types
# ---------------------------------------------------------------------------


def test_shared_parser_contract_text_and_error() -> None:
    """All parsers: plain text, empty output, and non-zero exit code handling.

    Note: Gemini parser no longer handles non-zero exit codes (invoke() raises
    before calling _parse_gemini_output). Test only non-zero cases for other parsers.
    """
    for parse in _ALL_PARSERS:
        # plain text
        text, calls = parse("Hello, world!", "", 0)
        assert text == "Hello, world!" and calls == []
        # empty
        text, calls = parse("", "", 0)
        assert text is None and calls == []
        # nonzero exit tests: skip for Gemini (handled by invoke() before parser)
        if parse is not _gemini_parse:
            # nonzero exit with stderr
            text, calls = parse("", "Something went wrong", 1)
            assert text is not None and "Something went wrong" in text and calls == []
            # nonzero with stdout
            text, calls = parse("stdout error", "", 1)
            assert text is not None and "stdout error" in text


def test_shared_parser_contract_json_messages() -> None:
    """All parsers: JSON message, content blocks, tool_use, result, mixed lines."""
    for parse in _ALL_PARSERS:
        # JSON message string
        line = json.dumps({"type": "message", "content": "Hello from adapter"})
        text, calls = parse(line, "", 0)
        assert text == "Hello from adapter" and calls == []

        # message with content blocks
        line = json.dumps(
            {
                "type": "message",
                "content": [{"type": "text", "text": "Part 1"}, {"type": "text", "text": "Part 2"}],
            }
        )
        text, calls = parse(line, "", 0)
        assert "Part 1" in text and "Part 2" in text

        # tool_use extraction
        line = json.dumps(
            {"type": "tool_use", "id": "t1", "name": "state_get", "input": {"key": "foo"}}
        )
        text, calls = parse(line, "", 0)
        assert len(calls) == 1
        assert calls[0]["id"] == "t1" and calls[0]["name"] == "state_get"
        assert calls[0]["input"] == {"key": "foo"}

        # result type
        line = json.dumps({"type": "result", "result": "Task completed."})
        text, calls = parse(line, "", 0)
        assert text == "Task completed."

        # mixed lines
        lines = "\n".join(
            [
                json.dumps({"type": "tool_use", "id": "t1", "name": "state_get", "input": {}}),
                json.dumps({"type": "message", "content": "Done!"}),
            ]
        )
        text, calls = parse(lines, "", 0)
        assert text == "Done!" and len(calls) == 1 and calls[0]["name"] == "state_get"

        # tool_use in content blocks
        line = json.dumps(
            {
                "type": "message",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "kv_set",
                        "input": {"key": "x", "value": 1},
                    }
                ],
            }
        )
        text, calls = parse(line, "", 0)
        assert len(calls) == 1 and calls[0]["name"] == "kv_set"

        # unknown type with text field
        line = json.dumps({"type": "unknown", "text": "some text"})
        text, calls = parse(line, "", 0)
        assert text == "some text"


# ---------------------------------------------------------------------------
# _extract_tool_call shared contract — codex and gemini share this helper
# ---------------------------------------------------------------------------


def test_extract_tool_call_shared_contract() -> None:
    """Both codex and gemini _extract_tool_call handle standard and missing fields."""
    for extract in [codex_extract_tool_call, gemini_extract_tool_call]:
        tc = extract({"id": "t1", "name": "my_tool", "input": {"key": "val"}})
        assert tc == {"id": "t1", "name": "my_tool", "input": {"key": "val"}}
        tc_empty = extract({})
        assert tc_empty["id"] == "" and tc_empty["name"] == ""


# ---------------------------------------------------------------------------
# invoke() behavioral contract — shared behaviors across subprocess adapters
# ---------------------------------------------------------------------------

_CLAUDE_EXEC = "butlers.core.runtimes.claude_code.asyncio.create_subprocess_exec"
_CODEX_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"
_GEMINI_EXEC = "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec"
_OPENCODE_EXEC = "butlers.core.runtimes.opencode.asyncio.create_subprocess_exec"

_INVOKE_PARAMS = [
    pytest.param(ClaudeCodeAdapter, "/usr/bin/claude", "claude_binary", _CLAUDE_EXEC, id="claude"),
    pytest.param(CodexAdapter, "/usr/bin/codex", "codex_binary", _CODEX_EXEC, id="codex"),
    pytest.param(GeminiAdapter, "/usr/bin/gemini", "gemini_binary", _GEMINI_EXEC, id="gemini"),
    pytest.param(
        OpenCodeAdapter, "/usr/bin/opencode", "opencode_binary", _OPENCODE_EXEC, id="opencode"
    ),
]


@pytest.mark.parametrize("adapter_class, binary, binary_kwarg, exec_patch", _INVOKE_PARAMS)
async def test_invoke_cwd_and_tool_calls(
    adapter_class: type,
    binary: str,
    binary_kwarg: str,
    exec_patch: str,
) -> None:
    """invoke() passes cwd to subprocess and captures tool_use tool calls."""
    adapter = adapter_class(**{binary_kwarg: binary})
    output_lines = "\n".join(
        [
            json.dumps(
                {"type": "tool_use", "id": "t1", "name": "state_get", "input": {"key": "foo"}}
            ),
            json.dumps({"type": "result", "result": "Done"}),
        ]
    )
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output_lines.encode(), b""))
    mock_proc.returncode = 0

    with patch(exec_patch, return_value=mock_proc) as mock_sub:
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="use tools",
            system_prompt="helpful",
            mcp_servers={},
            env={},
            cwd=Path("/tmp/workdir"),
        )

    assert mock_sub.call_args[1]["cwd"] == "/tmp/workdir"
    assert result_text == "Done"
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "state_get" and tool_calls[0]["input"] == {"key": "foo"}
    assert usage is None


@pytest.mark.parametrize("adapter_class, binary, binary_kwarg, exec_patch", _INVOKE_PARAMS)
async def test_invoke_timeout(
    adapter_class: type,
    binary: str,
    binary_kwarg: str,
    exec_patch: str,
) -> None:
    """invoke() raises TimeoutError when the subprocess times out."""
    adapter = adapter_class(**{binary_kwarg: binary})
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()

    with patch(exec_patch, return_value=mock_proc):
        with pytest.raises(TimeoutError, match="timed out"):
            await adapter.invoke(prompt="slow", system_prompt="", mcp_servers={}, env={}, timeout=1)


# ---------------------------------------------------------------------------
# Token reporting contract — usage dict must have int fields or be None
# ---------------------------------------------------------------------------


def _usage_satisfies_contract(usage: dict | None) -> bool:
    if usage is None:
        return True
    if not isinstance(usage, dict):
        return False
    return isinstance(usage.get("input_tokens"), int) and isinstance(
        usage.get("output_tokens"), int
    )


def test_codex_usage_contract() -> None:
    """Codex parser usage contract: int tokens, None without event, partial/non-int."""
    # int tokens
    line = json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}
    )
    _, _, usage = _parse_codex_output(line, "", 0)
    assert _usage_satisfies_contract(usage)
    assert usage == {"input_tokens": 100, "output_tokens": 50}
    # none without event
    no_event = json.dumps({"type": "message", "content": "hello"})
    _, _, usage = _parse_codex_output(no_event, "", 0)
    assert usage is None
    # partial defaults to 0
    line = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 42}})
    _, _, usage = _parse_codex_output(line, "", 0)
    assert _usage_satisfies_contract(usage) and isinstance(usage["output_tokens"], int)
    # non-int → None
    line = json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": "nan", "output_tokens": "nan"}}
    )
    assert _parse_codex_output(line, "", 0)[2] is None


def test_opencode_usage_contract() -> None:
    """OpenCode parser usage contract: int tokens, None without event, partial/non-int."""
    # int tokens from step_finish
    lines = json.dumps(
        {"type": "step_finish", "sessionID": "s1", "part": {"tokens": {"input": 200, "output": 80}}}
    )
    _, _, usage = _parse_opencode_output(lines, "", 0)
    assert _usage_satisfies_contract(usage)
    assert usage == {
        "input_tokens": 200,
        "output_tokens": 80,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    # none without event
    assert (
        _parse_opencode_output(json.dumps({"type": "message", "content": "hello"}), "", 0)[2]
        is None
    )
    # non-int → None
    assert opencode_extract_usage({"input_tokens": "nan", "output_tokens": "nan"}) is None
    # partial defaults to 0
    result = opencode_extract_usage({"input_tokens": 42})
    assert (
        _usage_satisfies_contract(result)
        and result["input_tokens"] == 42
        and result["output_tokens"] == 0
    )


async def test_claude_usage_contract() -> None:
    """ClaudeCodeAdapter.invoke() returns int-typed usage from stream-json result event."""
    output = json.dumps(
        {"type": "result", "result": "Done", "usage": {"input_tokens": 150, "output_tokens": 60}}
    )
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output.encode(), b""))
    mock_proc.returncode = 0
    mock_proc.pid = 1
    with patch(_CLAUDE_EXEC, return_value=mock_proc):
        _, _, usage = await ClaudeCodeAdapter(claude_binary="/usr/bin/claude").invoke(
            prompt="hello", system_prompt="", mcp_servers={}, env={}
        )
    assert _usage_satisfies_contract(usage) and isinstance(usage["input_tokens"], int)


def test_gemini_usage_is_none() -> None:
    """_parse_gemini_output returns 2-tuple; GeminiAdapter.invoke() reports usage=None."""
    result = _parse_gemini_output("hello world", "")
    assert len(result) == 2
    result_text, tool_calls = result
    assert result_text == "hello world" and tool_calls == []
