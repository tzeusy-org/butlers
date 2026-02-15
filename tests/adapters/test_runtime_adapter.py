"""Tests for RuntimeAdapter ABC and adapter registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from butlers.core.runtimes import RuntimeAdapter, get_adapter, register_adapter
from butlers.core.runtimes.claude_code import ClaudeCodeAdapter
from butlers.core.runtimes.codex import CodexAdapter
from butlers.core.runtimes.gemini import GeminiAdapter

pytestmark = pytest.mark.unit
# ---------------------------------------------------------------------------
# Test fixtures — concrete and partial subclasses
# ---------------------------------------------------------------------------


class FullAdapter(RuntimeAdapter):
    """Fully concrete adapter implementation for testing."""

    @property
    def binary_name(self) -> str:
        return "full-test-binary"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        return ("ok", [], None)

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        return tmp_dir / "config.json"

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return "system prompt"


class MissingInvokeAdapter(RuntimeAdapter):
    """Missing the invoke() method."""

    @property
    def binary_name(self) -> str:
        return "test"

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        return tmp_dir / "config.json"

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


class MissingBuildConfigAdapter(RuntimeAdapter):
    """Missing the build_config_file() method."""

    @property
    def binary_name(self) -> str:
        return "test"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        return (None, [], None)

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


class MissingParsePromptAdapter(RuntimeAdapter):
    """Missing the parse_system_prompt_file() method."""

    @property
    def binary_name(self) -> str:
        return "test"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        return (None, [], None)

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        return tmp_dir / "config.json"


# ---------------------------------------------------------------------------
# ABC enforcement tests
# ---------------------------------------------------------------------------


def test_cannot_instantiate_runtime_adapter_abc():
    """RuntimeAdapter is abstract and cannot be instantiated directly."""
    with pytest.raises(TypeError):
        RuntimeAdapter()  # type: ignore[abstract]


def test_missing_invoke_raises():
    """A subclass missing invoke() cannot be instantiated."""
    with pytest.raises(TypeError):
        MissingInvokeAdapter()  # type: ignore[abstract]


def test_missing_build_config_file_raises():
    """A subclass missing build_config_file() cannot be instantiated."""
    with pytest.raises(TypeError):
        MissingBuildConfigAdapter()  # type: ignore[abstract]


def test_missing_parse_system_prompt_file_raises():
    """A subclass missing parse_system_prompt_file() cannot be instantiated."""
    with pytest.raises(TypeError):
        MissingParsePromptAdapter()  # type: ignore[abstract]


def test_full_adapter_instantiates():
    """A fully concrete subclass can be instantiated."""
    adapter = FullAdapter()
    assert isinstance(adapter, RuntimeAdapter)


async def test_full_adapter_invoke():
    """invoke() returns a (result_text, tool_calls, usage) tuple."""
    adapter = FullAdapter()
    result_text, tool_calls, usage = await adapter.invoke(
        prompt="hello",
        system_prompt="you are helpful",
        mcp_servers={},
        env={},
    )
    assert result_text == "ok"
    assert tool_calls == []
    assert usage is None


def test_full_adapter_build_config_file(tmp_path: Path):
    """build_config_file() returns a Path."""
    adapter = FullAdapter()
    config_path = adapter.build_config_file(mcp_servers={}, tmp_dir=tmp_path)
    assert isinstance(config_path, Path)
    assert config_path == tmp_path / "config.json"


def test_full_adapter_parse_system_prompt_file(tmp_path: Path):
    """parse_system_prompt_file() returns a string."""
    adapter = FullAdapter()
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "system prompt"


# ---------------------------------------------------------------------------
# Registry / get_adapter tests
# ---------------------------------------------------------------------------


def test_get_adapter_claude_code():
    """get_adapter('claude-code') returns ClaudeCodeAdapter."""
    cls = get_adapter("claude-code")
    assert cls is ClaudeCodeAdapter


def test_get_adapter_codex():
    """get_adapter('codex') returns the real CodexAdapter."""
    cls = get_adapter("codex")
    assert cls is CodexAdapter


def test_get_adapter_gemini():
    """get_adapter('gemini') returns GeminiAdapter."""
    cls = get_adapter("gemini")
    assert cls is GeminiAdapter


def test_get_adapter_unknown_raises():
    """get_adapter() raises ValueError for unregistered type strings."""
    with pytest.raises(ValueError, match="Unknown runtime type 'unknown-runtime'"):
        get_adapter("unknown-runtime")


def test_get_adapter_error_lists_available():
    """The ValueError message includes the available adapter types."""
    with pytest.raises(ValueError, match="claude-code") as exc_info:
        get_adapter("nope")
    msg = str(exc_info.value)
    assert "codex" in msg
    assert "gemini" in msg


def test_register_custom_adapter():
    """register_adapter() allows adding new runtime types at runtime."""
    register_adapter("custom", FullAdapter)
    assert get_adapter("custom") is FullAdapter


# ---------------------------------------------------------------------------
# Adapter subclass tests
# ---------------------------------------------------------------------------


def test_all_adapters_are_runtime_adapters():
    """All adapters are subclasses of RuntimeAdapter."""
    assert issubclass(ClaudeCodeAdapter, RuntimeAdapter)
    assert issubclass(CodexAdapter, RuntimeAdapter)
    assert issubclass(GeminiAdapter, RuntimeAdapter)


def test_all_adapters_instantiate():
    """All adapters can be instantiated."""
    assert ClaudeCodeAdapter()
    assert CodexAdapter()
    assert GeminiAdapter()


# ---------------------------------------------------------------------------
# ClaudeCodeAdapter-specific tests
# ---------------------------------------------------------------------------


def test_claude_code_adapter_build_config_file(tmp_path: Path):
    """ClaudeCodeAdapter.build_config_file() writes mcp.json."""
    import json

    adapter = ClaudeCodeAdapter()
    mcp_servers = {"my-butler": {"url": "http://localhost:9100/sse"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    assert config_path == tmp_path / "mcp.json"
    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["my-butler"]["url"] == "http://localhost:9100/sse"


def test_claude_code_adapter_parse_system_prompt_file(tmp_path: Path):
    """ClaudeCodeAdapter reads CLAUDE.md for system prompt."""
    adapter = ClaudeCodeAdapter()
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("You are a specialized butler.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "You are a specialized butler."


def test_claude_code_adapter_parse_missing_prompt(tmp_path: Path):
    """ClaudeCodeAdapter returns empty string for missing CLAUDE.md."""
    adapter = ClaudeCodeAdapter()
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


def test_claude_code_adapter_parse_empty_prompt(tmp_path: Path):
    """ClaudeCodeAdapter returns empty string for empty CLAUDE.md."""
    adapter = ClaudeCodeAdapter()
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("   \n  ")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


async def test_claude_code_adapter_invoke_with_mock():
    """ClaudeCodeAdapter.invoke() calls sdk_query and parses results."""
    from claude_code_sdk import ResultMessage

    async def mock_query(*, prompt, options):
        yield ResultMessage(
            subtype="result",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=1,
            session_id="test",
            total_cost_usd=0.0,
            usage={},
            result="Hello!",
        )

    adapter = ClaudeCodeAdapter(sdk_query=mock_query)
    result_text, tool_calls, usage = await adapter.invoke(
        prompt="hi",
        system_prompt="you are helpful",
        mcp_servers={"test": {"url": "http://localhost:9100/sse"}},
        env={"ANTHROPIC_API_KEY": "sk-test"},
    )
    assert result_text == "Hello!"
    assert tool_calls == []
    assert usage is None  # empty dict becomes None


async def test_claude_code_adapter_invoke_with_tool_calls():
    """ClaudeCodeAdapter.invoke() captures ToolUseBlock tool calls."""
    from claude_code_sdk import AssistantMessage, ResultMessage, ToolUseBlock

    async def mock_query(*, prompt, options):
        yield AssistantMessage(
            content=[ToolUseBlock(id="t1", name="state_get", input={"key": "foo"})],
            model="claude-test",
        )
        yield ResultMessage(
            subtype="result",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=1,
            session_id="test",
            total_cost_usd=0.0,
            usage={},
            result="Done",
        )

    adapter = ClaudeCodeAdapter(sdk_query=mock_query)
    result_text, tool_calls, usage = await adapter.invoke(
        prompt="use tools",
        system_prompt="you are helpful",
        mcp_servers={"test": {"url": "http://localhost:9100/sse"}},
        env={},
    )
    assert result_text == "Done"
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "state_get"
    assert tool_calls[0]["input"] == {"key": "foo"}


async def test_claude_code_adapter_invoke_captures_usage():
    """ClaudeCodeAdapter.invoke() extracts token usage from ResultMessage."""
    from claude_code_sdk import ResultMessage

    async def mock_query(*, prompt, options):
        yield ResultMessage(
            subtype="result",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=1,
            session_id="test",
            total_cost_usd=0.01,
            usage={"input_tokens": 150, "output_tokens": 300},
            result="With usage!",
        )

    adapter = ClaudeCodeAdapter(sdk_query=mock_query)
    result_text, tool_calls, usage = await adapter.invoke(
        prompt="test",
        system_prompt="you are helpful",
        mcp_servers={"test": {"url": "http://localhost:9100/sse"}},
        env={},
    )
    assert result_text == "With usage!"
    assert usage is not None
    assert usage["input_tokens"] == 150
    assert usage["output_tokens"] == 300


async def test_claude_code_adapter_invoke_none_usage():
    """ClaudeCodeAdapter.invoke() returns None usage when SDK usage is None."""
    from claude_code_sdk import ResultMessage

    async def mock_query(*, prompt, options):
        yield ResultMessage(
            subtype="result",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=1,
            session_id="test",
            total_cost_usd=0.0,
            usage=None,
            result="No usage",
        )

    adapter = ClaudeCodeAdapter(sdk_query=mock_query)
    result_text, tool_calls, usage = await adapter.invoke(
        prompt="test",
        system_prompt="you are helpful",
        mcp_servers={"test": {"url": "http://localhost:9100/sse"}},
        env={},
    )
    assert result_text == "No usage"
    assert usage is None


# ---------------------------------------------------------------------------
# ClaudeCodeAdapter stderr capture tests
# ---------------------------------------------------------------------------


async def test_claude_code_adapter_passes_stderr_options_when_butler_name_set(tmp_path: Path):
    """ClaudeCodeAdapter passes debug_stderr and extra_args when butler_name is set."""
    from claude_code_sdk import ResultMessage

    captured_options = {}

    async def mock_query(*, prompt, options):
        captured_options["debug_stderr"] = options.debug_stderr
        captured_options["extra_args"] = options.extra_args
        yield ResultMessage(
            subtype="result",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=1,
            session_id="test",
            total_cost_usd=0.0,
            usage={},
            result="ok",
        )

    adapter = ClaudeCodeAdapter(
        sdk_query=mock_query, butler_name="test-butler", log_root=tmp_path
    )
    await adapter.invoke(
        prompt="hi",
        system_prompt="sys",
        mcp_servers={"test": {"url": "http://localhost:9100/sse"}},
        env={},
    )

    # Verify the options were set correctly
    assert captured_options["extra_args"] == {"debug-to-stderr": None}
    assert captured_options["debug_stderr"] is not None
    # File should have been closed after invoke returns
    assert captured_options["debug_stderr"].closed

    # Verify the log file was created
    stderr_log = tmp_path / "butlers" / "test-butler_cc_stderr.log"
    assert stderr_log.exists()
    content = stderr_log.read_text()
    assert "CC session start:" in content


async def test_claude_code_adapter_no_stderr_without_butler_name():
    """ClaudeCodeAdapter does not set debug_stderr when butler_name is not set."""
    from claude_code_sdk import ResultMessage

    captured_options = {}

    async def mock_query(*, prompt, options):
        captured_options["debug_stderr"] = options.debug_stderr
        captured_options["extra_args"] = options.extra_args
        yield ResultMessage(
            subtype="result",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=1,
            session_id="test",
            total_cost_usd=0.0,
            usage={},
            result="ok",
        )

    adapter = ClaudeCodeAdapter(sdk_query=mock_query)
    await adapter.invoke(
        prompt="hi",
        system_prompt="sys",
        mcp_servers={"test": {"url": "http://localhost:9100/sse"}},
        env={},
    )

    # Without butler_name, extra_args should be default empty dict
    assert captured_options["extra_args"] == {}


async def test_claude_code_adapter_stderr_closed_on_error(tmp_path: Path):
    """ClaudeCodeAdapter closes stderr file even when SDK raises an error."""
    captured_options = {}

    async def mock_query(*, prompt, options):
        captured_options["debug_stderr"] = options.debug_stderr
        raise RuntimeError("SDK error")
        yield  # make it a generator  # pragma: no cover

    adapter = ClaudeCodeAdapter(
        sdk_query=mock_query, butler_name="test-butler", log_root=tmp_path
    )
    with pytest.raises(RuntimeError, match="SDK error"):
        await adapter.invoke(
            prompt="hi",
            system_prompt="sys",
            mcp_servers={"test": {"url": "http://localhost:9100/sse"}},
            env={},
        )

    # File should be closed even after error
    assert captured_options["debug_stderr"].closed


# ---------------------------------------------------------------------------
# Import path tests
# ---------------------------------------------------------------------------


def test_importable_from_butlers_core_runtimes():
    """RuntimeAdapter and get_adapter are importable from butlers.core.runtimes."""
    from butlers.core.runtimes import RuntimeAdapter as RA
    from butlers.core.runtimes import get_adapter as ga

    assert RA is RuntimeAdapter
    assert ga is get_adapter


def test_claude_code_adapter_importable_from_runtimes():
    """ClaudeCodeAdapter is importable from butlers.core.runtimes."""
    from butlers.core.runtimes import ClaudeCodeAdapter as CCA

    assert CCA is ClaudeCodeAdapter


# ---------------------------------------------------------------------------
# binary_name property tests
# ---------------------------------------------------------------------------


def test_claude_code_adapter_binary_name():
    """ClaudeCodeAdapter.binary_name returns 'claude'."""
    adapter = ClaudeCodeAdapter()
    assert adapter.binary_name == "claude"


def test_codex_adapter_binary_name():
    """CodexAdapter.binary_name returns 'codex'."""
    adapter = CodexAdapter()
    assert adapter.binary_name == "codex"


def test_gemini_adapter_binary_name():
    """GeminiAdapter.binary_name returns 'gemini'."""
    adapter = GeminiAdapter()
    assert adapter.binary_name == "gemini"


def test_full_adapter_binary_name():
    """FullAdapter.binary_name returns the test binary name."""
    adapter = FullAdapter()
    assert adapter.binary_name == "full-test-binary"


def test_adapter_must_implement_binary_name():
    """A subclass missing binary_name cannot be instantiated."""

    class AdapterWithoutBinary(RuntimeAdapter):
        async def invoke(self, prompt, system_prompt, mcp_servers, env, cwd=None, timeout=None):
            return ("ok", [])

        def build_config_file(self, mcp_servers, tmp_dir):
            return tmp_dir / "config.json"

        def parse_system_prompt_file(self, config_dir):
            return ""

    with pytest.raises(TypeError):
        AdapterWithoutBinary()  # type: ignore[abstract]
