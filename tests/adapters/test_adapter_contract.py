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


@pytest.mark.parametrize(
    "adapter_name, adapter_class",
    [
        ("codex", CodexAdapter),
        ("gemini", GeminiAdapter),
        ("opencode", OpenCodeAdapter),
        ("claude", ClaudeCodeAdapter),
    ],
)
def test_adapter_registered(adapter_name: str, adapter_class: type) -> None:
    """get_adapter() returns the correct registered adapter class."""
    assert get_adapter(adapter_name) is adapter_class


@pytest.mark.parametrize(
    "adapter_class", [CodexAdapter, GeminiAdapter, OpenCodeAdapter, ClaudeCodeAdapter]
)
def test_adapter_is_runtime_adapter(adapter_class: type) -> None:
    """All adapters are subclasses of RuntimeAdapter."""
    assert issubclass(adapter_class, RuntimeAdapter)


@pytest.mark.parametrize(
    "adapter_class", [CodexAdapter, GeminiAdapter, OpenCodeAdapter, ClaudeCodeAdapter]
)
def test_adapter_instantiates(adapter_class: type) -> None:
    """All adapters can be instantiated without arguments."""
    assert adapter_class() is not None


@pytest.mark.parametrize(
    "adapter_class, import_name",
    [
        (CodexAdapter, "CodexAdapter"),
        (GeminiAdapter, "GeminiAdapter"),
        (OpenCodeAdapter, "OpenCodeAdapter"),
        (ClaudeCodeAdapter, "ClaudeCodeAdapter"),
    ],
)
def test_adapter_importable_from_runtimes(adapter_class: type, import_name: str) -> None:
    """All adapters are importable from butlers.core.runtimes."""
    import butlers.core.runtimes as runtimes_module

    assert getattr(runtimes_module, import_name) is adapter_class


# ---------------------------------------------------------------------------
# build_config_file contract — shared mcpServers key structure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_class", [CodexAdapter, GeminiAdapter])
def test_build_config_file_empty_servers(adapter_class: type, tmp_path: Path) -> None:
    """build_config_file() writes a config with an empty mcpServers dict (JSON adapters)."""
    adapter = adapter_class()
    config_path = adapter.build_config_file(mcp_servers={}, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert data["mcpServers"] == {}


@pytest.mark.parametrize("adapter_class", [CodexAdapter, GeminiAdapter])
def test_build_config_file_multiple_servers(adapter_class: type, tmp_path: Path) -> None:
    """build_config_file() writes all provided MCP servers (JSON adapters)."""
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


def test_opencode_build_config_file_writes_mcp_servers(tmp_path: Path) -> None:
    """OpenCodeAdapter.build_config_file() includes all provided MCP servers in mcp section."""
    adapter = OpenCodeAdapter()
    mcp_servers = {
        "butler-a": {"url": "http://localhost:9100/mcp"},
        "butler-b": {"url": "http://localhost:9200/mcp"},
    }
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    # OpenCode uses JSONC format with 'mcp' section, not 'mcpServers'
    # Strip comments before parsing
    content = config_path.read_text()
    lines = [line for line in content.splitlines() if not line.strip().startswith("//")]
    data = json.loads("\n".join(lines))
    mcp_section = data.get("mcp", {})
    assert "butler-a" in mcp_section, f"butler-a not in mcp section: {mcp_section}"
    assert "butler-b" in mcp_section, f"butler-b not in mcp section: {mcp_section}"


# ---------------------------------------------------------------------------
# parse_system_prompt_file contract — non-Claude adapters ignore CLAUDE.md
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_class", [CodexAdapter, GeminiAdapter, OpenCodeAdapter])
def test_parse_system_prompt_ignores_claude_md(adapter_class: type, tmp_path: Path) -> None:
    """Non-Claude adapters do not read CLAUDE.md for their system prompt."""
    adapter = adapter_class()
    (tmp_path / "CLAUDE.md").write_text("This is Claude instructions.")
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == ""


# ---------------------------------------------------------------------------
# Helpers: normalize parser output to (result_text, tool_calls)
#
# _parse_codex_output returns (result_text, tool_calls, usage)
# _parse_opencode_output returns (result_text, tool_calls, usage)
# _parse_gemini_output returns (result_text, tool_calls)
# The shared contract only asserts on result_text and tool_calls.
# ---------------------------------------------------------------------------


def _codex_parse(stdout: str, stderr: str, returncode: int) -> tuple[str | None, list]:
    result_text, tool_calls, _usage = _parse_codex_output(stdout, stderr, returncode)
    return result_text, tool_calls


def _gemini_parse(stdout: str, stderr: str, returncode: int) -> tuple[str | None, list]:
    return _parse_gemini_output(stdout, stderr, returncode)


def _opencode_parse(stdout: str, stderr: str, returncode: int) -> tuple[str | None, list]:
    result_text, tool_calls, _usage = _parse_opencode_output(stdout, stderr, returncode)
    return result_text, tool_calls


_PARSE_PARAMS = [
    pytest.param(_codex_parse, id="codex"),
    pytest.param(_gemini_parse, id="gemini"),
    pytest.param(_opencode_parse, id="opencode"),
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
# invoke() behavioral contract — shared behaviors across subprocess adapters
# ---------------------------------------------------------------------------

_CODEX_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"
_GEMINI_EXEC = "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec"
_OPENCODE_EXEC = "butlers.core.runtimes.opencode.asyncio.create_subprocess_exec"

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
    pytest.param(
        OpenCodeAdapter,
        "/usr/bin/opencode",
        "opencode_binary",
        _OPENCODE_EXEC,
        id="opencode",
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


# ---------------------------------------------------------------------------
# Token reporting contract — usage dict must have int fields or be None
# ---------------------------------------------------------------------------


def _usage_satisfies_contract(usage: dict | None) -> bool:
    """Return True when the usage value satisfies the adapter token reporting contract.

    Contract: usage is either None (adapter cannot report) or a dict
    with ``input_tokens: int`` and ``output_tokens: int``.
    """
    if usage is None:
        return True
    if not isinstance(usage, dict):
        return False
    return isinstance(usage.get("input_tokens"), int) and isinstance(
        usage.get("output_tokens"), int
    )


def test_codex_usage_contract_int_tokens():
    """Codex parser returns int-typed usage fields when turn.completed provides ints."""
    line = json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}
    )
    _, _, usage = _parse_codex_output(line, "", 0)
    assert _usage_satisfies_contract(usage), f"Usage violates contract: {usage}"
    assert usage == {"input_tokens": 100, "output_tokens": 50}


def test_codex_usage_contract_none_when_no_token_event():
    """Codex parser returns usage=None when no turn.completed event is present."""
    line = json.dumps({"type": "message", "content": "hello"})
    _, _, usage = _parse_codex_output(line, "", 0)
    assert usage is None


def test_codex_usage_contract_partial_tokens_defaults_to_zero():
    """Codex parser normalises partial token data: missing field defaults to 0."""
    line = json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": 42}}  # no output_tokens
    )
    _, _, usage = _parse_codex_output(line, "", 0)
    assert _usage_satisfies_contract(usage), f"Usage violates contract: {usage}"
    assert usage is not None
    assert isinstance(usage["input_tokens"], int)
    assert isinstance(usage["output_tokens"], int)


def test_codex_usage_contract_non_int_tokens_returns_none():
    """Codex parser returns usage=None when token fields are non-int strings."""
    line = json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": "nan", "output_tokens": "nan"}}
    )
    _, _, usage = _parse_codex_output(line, "", 0)
    assert usage is None, f"Expected None usage for non-int tokens, got: {usage}"


def test_opencode_usage_contract_int_tokens():
    """OpenCode parser returns int-typed usage fields from step_finish events."""
    lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "step_finish",
                    "sessionID": "s1",
                    "part": {"tokens": {"input": 200, "output": 80}},
                }
            ),
        ]
    )
    _, _, usage = _parse_opencode_output(lines, "", 0)
    assert _usage_satisfies_contract(usage), f"Usage violates contract: {usage}"
    assert usage == {"input_tokens": 200, "output_tokens": 80}


def test_opencode_usage_contract_none_when_no_usage_event():
    """OpenCode parser returns usage=None when no usage event is present."""
    line = json.dumps({"type": "message", "content": "hello"})
    _, _, usage = _parse_opencode_output(line, "", 0)
    assert usage is None


def test_opencode_usage_contract_non_int_tokens_returns_none():
    """OpenCode _extract_usage returns None when both token fields are non-int."""
    result = opencode_extract_usage({"input_tokens": "nan", "output_tokens": "nan"})
    assert result is None, f"Expected None for non-int tokens, got: {result}"


def test_opencode_usage_contract_partial_tokens_defaults_to_zero():
    """OpenCode _extract_usage normalises partial token data: missing field defaults to 0."""
    result = opencode_extract_usage({"input_tokens": 42})  # no output_tokens
    assert _usage_satisfies_contract(result), f"Usage violates contract: {result}"
    assert result is not None
    assert isinstance(result["input_tokens"], int)
    assert result["input_tokens"] == 42
    assert isinstance(result["output_tokens"], int)
    assert result["output_tokens"] == 0


async def test_claude_usage_contract_int_tokens():
    """ClaudeCodeAdapter.invoke() returns int-typed usage fields from the SDK ResultMessage."""
    from unittest.mock import MagicMock

    from claude_agent_sdk import ResultMessage

    mock_usage = MagicMock()
    mock_usage.__iter__ = MagicMock(
        return_value=iter([("input_tokens", 150), ("output_tokens", 60)])
    )
    mock_result = MagicMock(spec=ResultMessage)
    mock_result.result = "Done"
    mock_result.usage = mock_usage

    async def mock_sdk_query(**kwargs):
        yield mock_result

    adapter = ClaudeCodeAdapter(sdk_query=mock_sdk_query)
    result_text, tool_calls, usage = await adapter.invoke(
        prompt="hello",
        system_prompt="",
        mcp_servers={},
        env={},
    )

    assert _usage_satisfies_contract(usage), f"Usage violates contract: {usage}"
    assert usage is not None
    assert isinstance(usage["input_tokens"], int)
    assert isinstance(usage["output_tokens"], int)


def test_gemini_usage_is_none():
    """GeminiAdapter.invoke() returns usage=None (Gemini CLI does not expose token counts).

    _parse_gemini_output returns a 2-tuple (result_text, tool_calls) without a
    usage field because the Gemini CLI does not emit token counts. GeminiAdapter.invoke()
    always passes None as the third tuple element to satisfy the adapter contract.
    """
    result = _parse_gemini_output("hello world", "", 0)
    # _parse_gemini_output returns (result_text, tool_calls) — a 2-tuple.
    # Usage=None is returned by GeminiAdapter.invoke() itself, not by the parser.
    assert len(result) == 2, f"_parse_gemini_output should return a 2-tuple, got: {result}"
    result_text, tool_calls = result
    assert result_text == "hello world"
    assert tool_calls == []
