"""Shared parser contract tests parametrized across CodexAdapter and GeminiAdapter.

Each adapter has its own parser function and invoke() implementation, but they share a
common behavioral contract: plain text, JSON messages, tool calls, exit code handling.
Tests in this module verify that contract for both adapters without duplication.

Adapter-specific tests (unique output formats, env filtering, binary discovery errors,
system-prompt file resolution) remain in the native test files.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import CodexAdapter, GeminiAdapter, RuntimeAdapter, get_adapter
from butlers.core.runtimes.codex import _extract_tool_call as codex_extract_tool_call
from butlers.core.runtimes.codex import _parse_codex_output
from butlers.core.runtimes.gemini import _extract_tool_call as gemini_extract_tool_call
from butlers.core.runtimes.gemini import _parse_gemini_output

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Adapter registry contract — both adapters register themselves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "adapter_name, adapter_class",
    [
        ("codex", CodexAdapter),
        ("gemini", GeminiAdapter),
    ],
)
def test_adapter_registered(adapter_name: str, adapter_class: type) -> None:
    """get_adapter() returns the correct registered adapter class."""
    assert get_adapter(adapter_name) is adapter_class


@pytest.mark.parametrize("adapter_class", [CodexAdapter, GeminiAdapter])
def test_adapter_is_runtime_adapter(adapter_class: type) -> None:
    """Both adapters are subclasses of RuntimeAdapter."""
    assert issubclass(adapter_class, RuntimeAdapter)


@pytest.mark.parametrize("adapter_class", [CodexAdapter, GeminiAdapter])
def test_adapter_instantiates(adapter_class: type) -> None:
    """Both adapters can be instantiated without arguments."""
    assert adapter_class() is not None


@pytest.mark.parametrize(
    "adapter_class, import_name",
    [
        (CodexAdapter, "CodexAdapter"),
        (GeminiAdapter, "GeminiAdapter"),
    ],
)
def test_adapter_importable_from_runtimes(adapter_class: type, import_name: str) -> None:
    """Both adapters are importable from butlers.core.runtimes."""
    import butlers.core.runtimes as runtimes_module

    assert getattr(runtimes_module, import_name) is adapter_class


# ---------------------------------------------------------------------------
# build_config_file contract — shared mcpServers key structure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_class", [CodexAdapter, GeminiAdapter])
def test_build_config_file_empty_servers(adapter_class: type, tmp_path: Path) -> None:
    """build_config_file() writes a config with an empty mcpServers dict."""
    adapter = adapter_class()
    config_path = adapter.build_config_file(mcp_servers={}, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert data["mcpServers"] == {}


@pytest.mark.parametrize("adapter_class", [CodexAdapter, GeminiAdapter])
def test_build_config_file_multiple_servers(adapter_class: type, tmp_path: Path) -> None:
    """build_config_file() writes all provided MCP servers."""
    adapter = adapter_class()
    mcp_servers = {
        "butler-a": {"url": "http://localhost:9100/mcp"},
        "butler-b": {"url": "http://localhost:9200/mcp"},
    }
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert len(data["mcpServers"]) == 2
    assert "butler-a" in data["mcpServers"]
    assert "butler-b" in data["mcpServers"]


# ---------------------------------------------------------------------------
# parse_system_prompt_file contract — both adapters ignore CLAUDE.md
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_class", [CodexAdapter, GeminiAdapter])
def test_parse_system_prompt_ignores_claude_md(adapter_class: type, tmp_path: Path) -> None:
    """Neither adapter reads CLAUDE.md for its system prompt."""
    adapter = adapter_class()
    (tmp_path / "CLAUDE.md").write_text("This is Claude instructions.")
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == ""


# ---------------------------------------------------------------------------
# Helpers: normalize parser output to (result_text, tool_calls)
#
# _parse_codex_output returns (result_text, tool_calls, usage)
# _parse_gemini_output returns (result_text, tool_calls)
# The shared contract only asserts on result_text and tool_calls.
# ---------------------------------------------------------------------------


def _codex_parse(stdout: str, stderr: str, returncode: int) -> tuple[str | None, list]:
    result_text, tool_calls, _usage = _parse_codex_output(stdout, stderr, returncode)
    return result_text, tool_calls


def _gemini_parse(stdout: str, stderr: str, returncode: int) -> tuple[str | None, list]:
    return _parse_gemini_output(stdout, stderr, returncode)


_PARSE_PARAMS = [
    pytest.param(_codex_parse, id="codex"),
    pytest.param(_gemini_parse, id="gemini"),
]

# ---------------------------------------------------------------------------
# Shared parser contract — plain text, empty, exit codes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parse", _PARSE_PARAMS)
def test_parse_plain_text_output(parse) -> None:
    """Plain text stdout is returned as result_text."""
    result_text, tool_calls = parse("Hello, world!", "", 0)
    assert result_text == "Hello, world!"
    assert tool_calls == []


@pytest.mark.parametrize("parse", _PARSE_PARAMS)
def test_parse_empty_output(parse) -> None:
    """Empty stdout returns None result_text."""
    result_text, tool_calls = parse("", "", 0)
    assert result_text is None
    assert tool_calls == []


@pytest.mark.parametrize("parse", _PARSE_PARAMS)
def test_parse_nonzero_exit_code(parse) -> None:
    """Non-zero exit code returns an error message containing stderr."""
    result_text, tool_calls = parse("", "Something went wrong", 1)
    assert result_text is not None
    assert "Something went wrong" in result_text
    assert tool_calls == []


@pytest.mark.parametrize("parse", _PARSE_PARAMS)
def test_parse_nonzero_exit_code_with_stdout(parse) -> None:
    """Non-zero exit code with stdout includes that text in the error detail."""
    result_text, tool_calls = parse("stdout error", "", 1)
    assert result_text is not None
    assert "stdout error" in result_text


# ---------------------------------------------------------------------------
# Shared parser contract — JSON message types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parse", _PARSE_PARAMS)
def test_parse_json_message(parse) -> None:
    """JSON message objects are parsed for text content."""
    line = json.dumps({"type": "message", "content": "Hello from adapter"})
    result_text, tool_calls = parse(line, "", 0)
    assert result_text == "Hello from adapter"
    assert tool_calls == []


@pytest.mark.parametrize("parse", _PARSE_PARAMS)
def test_parse_json_message_with_content_blocks(parse) -> None:
    """JSON message with list content blocks extracts all text parts."""
    line = json.dumps(
        {
            "type": "message",
            "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ],
        }
    )
    result_text, tool_calls = parse(line, "", 0)
    assert "Part 1" in result_text
    assert "Part 2" in result_text


@pytest.mark.parametrize("parse", _PARSE_PARAMS)
def test_parse_json_tool_use(parse) -> None:
    """JSON tool_use objects are extracted as tool calls."""
    line = json.dumps(
        {
            "type": "tool_use",
            "id": "t1",
            "name": "state_get",
            "input": {"key": "foo"},
        }
    )
    result_text, tool_calls = parse(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "t1"
    assert tool_calls[0]["name"] == "state_get"
    assert tool_calls[0]["input"] == {"key": "foo"}


@pytest.mark.parametrize("parse", _PARSE_PARAMS)
def test_parse_json_result(parse) -> None:
    """JSON result objects extract the result field as result_text."""
    line = json.dumps({"type": "result", "result": "Task completed."})
    result_text, tool_calls = parse(line, "", 0)
    assert result_text == "Task completed."


@pytest.mark.parametrize("parse", _PARSE_PARAMS)
def test_parse_mixed_json_lines(parse) -> None:
    """Multiple JSONL lines with tool calls and a message are all parsed."""
    lines = "\n".join(
        [
            json.dumps({"type": "tool_use", "id": "t1", "name": "state_get", "input": {}}),
            json.dumps({"type": "message", "content": "Done!"}),
        ]
    )
    result_text, tool_calls = parse(lines, "", 0)
    assert result_text == "Done!"
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "state_get"


@pytest.mark.parametrize("parse", _PARSE_PARAMS)
def test_parse_tool_call_in_content_block(parse) -> None:
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
    result_text, tool_calls = parse(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "kv_set"


@pytest.mark.parametrize("parse", _PARSE_PARAMS)
def test_parse_unknown_json_with_text_field(parse) -> None:
    """Unknown JSON types with a 'text' field still yield result_text."""
    line = json.dumps({"type": "unknown", "text": "some text"})
    result_text, tool_calls = parse(line, "", 0)
    assert result_text == "some text"


# ---------------------------------------------------------------------------
# _extract_tool_call shared contract — both adapters share this helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extract_tool_call",
    [
        pytest.param(codex_extract_tool_call, id="codex"),
        pytest.param(gemini_extract_tool_call, id="gemini"),
    ],
)
def test_extract_tool_call_standard(extract_tool_call) -> None:
    """Standard tool_use format is extracted correctly by both adapters."""
    tc = extract_tool_call({"id": "t1", "name": "my_tool", "input": {"key": "val"}})
    assert tc == {"id": "t1", "name": "my_tool", "input": {"key": "val"}}


@pytest.mark.parametrize(
    "extract_tool_call",
    [
        pytest.param(codex_extract_tool_call, id="codex"),
        pytest.param(gemini_extract_tool_call, id="gemini"),
    ],
)
def test_extract_tool_call_missing_fields(extract_tool_call) -> None:
    """Missing fields default to empty string/dict for both adapters."""
    tc = extract_tool_call({})
    assert tc["id"] == ""
    assert tc["name"] == ""


# ---------------------------------------------------------------------------
# invoke() behavioral contract — shared behaviors across both adapters
# ---------------------------------------------------------------------------

_CODEX_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"
_GEMINI_EXEC = "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec"

_INVOKE_PARAMS = [
    pytest.param(
        CodexAdapter,
        "/usr/bin/codex",
        "codex_binary",
        _CODEX_EXEC,
        id="codex",
    ),
    pytest.param(
        GeminiAdapter,
        "/usr/bin/gemini",
        "gemini_binary",
        _GEMINI_EXEC,
        id="gemini",
    ),
]


@pytest.mark.parametrize("adapter_class, binary, binary_kwarg, exec_patch", _INVOKE_PARAMS)
async def test_invoke_passes_cwd(
    adapter_class: type,
    binary: str,
    binary_kwarg: str,
    exec_patch: str,
) -> None:
    """invoke() passes working directory to the subprocess."""
    adapter = adapter_class(**{binary_kwarg: binary})

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(exec_patch, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
            cwd=Path("/tmp/workdir"),
        )

    call_kwargs = mock_sub.call_args[1]
    assert call_kwargs["cwd"] == "/tmp/workdir"


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
            await adapter.invoke(
                prompt="slow task",
                system_prompt="",
                mcp_servers={},
                env={},
                timeout=1,
            )


@pytest.mark.parametrize("adapter_class, binary, binary_kwarg, exec_patch", _INVOKE_PARAMS)
async def test_invoke_with_tool_calls(
    adapter_class: type,
    binary: str,
    binary_kwarg: str,
    exec_patch: str,
) -> None:
    """invoke() captures tool_use tool calls from adapter output."""
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

    with patch(exec_patch, return_value=mock_proc):
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
