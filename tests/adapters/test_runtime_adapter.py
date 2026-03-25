"""Tests for RuntimeAdapter ABC and adapter registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from butlers.core.runtimes import RuntimeAdapter, get_adapter, register_adapter
from butlers.core.runtimes.claude_code import ClaudeCodeAdapter
from butlers.core.runtimes.codex import CodexAdapter
from butlers.core.runtimes.gemini import GeminiAdapter
from butlers.core.runtimes.opencode import OpenCodeAdapter

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


def test_runtime_adapter_default_create_worker_returns_self():
    """RuntimeAdapter.create_worker() defaults to returning self."""
    adapter = FullAdapter()
    assert adapter.create_worker() is adapter


async def test_runtime_adapter_default_reset_is_noop():
    """RuntimeAdapter.reset() default implementation is a no-op."""
    adapter = FullAdapter()
    await adapter.reset()


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
    """get_adapter('claude') returns ClaudeCodeAdapter."""
    cls = get_adapter("claude")
    assert cls is ClaudeCodeAdapter


def test_get_adapter_codex():
    """get_adapter('codex') returns the real CodexAdapter."""
    cls = get_adapter("codex")
    assert cls is CodexAdapter


def test_get_adapter_gemini():
    """get_adapter('gemini') returns GeminiAdapter."""
    cls = get_adapter("gemini")
    assert cls is GeminiAdapter


def test_get_adapter_opencode():
    """get_adapter('opencode') returns OpenCodeAdapter."""
    cls = get_adapter("opencode")
    assert cls is OpenCodeAdapter


def test_get_adapter_unknown_raises():
    """get_adapter() raises ValueError for unregistered type strings."""
    with pytest.raises(ValueError, match="Unknown runtime type 'unknown-runtime'"):
        get_adapter("unknown-runtime")


def test_get_adapter_error_lists_available():
    """The ValueError message includes the available adapter types."""
    with pytest.raises(ValueError, match="claude") as exc_info:
        get_adapter("nope")
    msg = str(exc_info.value)
    assert "codex" in msg
    assert "gemini" in msg
    assert "opencode" in msg


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
    assert issubclass(OpenCodeAdapter, RuntimeAdapter)


def test_all_adapters_instantiate():
    """All adapters can be instantiated."""
    assert ClaudeCodeAdapter()
    assert CodexAdapter()
    assert GeminiAdapter()
    assert OpenCodeAdapter()


@pytest.mark.parametrize(
    ("adapter_factory", "expected_filename"),
    [
        (ClaudeCodeAdapter, "mcp.json"),
        (CodexAdapter, "codex.json"),
        (GeminiAdapter, "gemini_mcp.json"),
    ],
)
def test_build_config_file_preserves_streamable_http_urls(
    tmp_path: Path, adapter_factory: Any, expected_filename: str
):
    """All runtime adapters preserve streamable HTTP MCP endpoint URLs."""
    adapter = adapter_factory()
    mcp_servers = {"switchboard": {"url": "http://localhost:41100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)

    assert config_path == tmp_path / expected_filename
    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["switchboard"]["url"] == "http://localhost:41100/mcp"


def test_opencode_adapter_binary_name():
    """OpenCodeAdapter.binary_name returns 'opencode'."""
    adapter = OpenCodeAdapter()
    assert adapter.binary_name == "opencode"


def test_opencode_adapter_build_config_file_preserves_url(tmp_path: Path):
    """OpenCodeAdapter.build_config_file() writes opencode.jsonc with correct URL."""
    adapter = OpenCodeAdapter()
    mcp_servers = {"switchboard": {"url": "http://localhost:41100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)

    assert config_path == tmp_path / "opencode.jsonc"
    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert data["mcp"]["switchboard"]["url"] == "http://localhost:41100/mcp"
    assert data["mcp"]["switchboard"]["type"] == "remote"


def test_opencode_adapter_importable_from_runtimes():
    """OpenCodeAdapter is importable from butlers.core.runtimes."""
    from butlers.core.runtimes import OpenCodeAdapter as OCA

    assert OCA is OpenCodeAdapter


# ---------------------------------------------------------------------------
# ClaudeCodeAdapter-specific tests
# ---------------------------------------------------------------------------


def test_claude_code_adapter_build_config_file(tmp_path: Path):
    """ClaudeCodeAdapter.build_config_file() writes mcp.json."""
    adapter = ClaudeCodeAdapter()
    mcp_servers = {"my-butler": {"url": "http://localhost:9100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    assert config_path == tmp_path / "mcp.json"
    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["my-butler"]["url"] == "http://localhost:9100/mcp"


def test_claude_code_adapter_create_worker_preserves_constructor_args(tmp_path: Path):
    """create_worker() returns a new adapter with identical configuration."""
    adapter = ClaudeCodeAdapter(
        claude_binary="/usr/bin/claude",
        butler_name="switchboard",
        log_root=tmp_path,
    )

    worker = adapter.create_worker()
    assert worker is not adapter
    assert isinstance(worker, ClaudeCodeAdapter)
    assert worker._claude_binary == "/usr/bin/claude"
    assert worker._butler_name == "switchboard"
    assert worker._log_root == tmp_path


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


_CLAUDE_EXEC = "butlers.core.runtimes.claude_code.asyncio.create_subprocess_exec"


async def test_claude_code_adapter_invoke_with_mock():
    """ClaudeCodeAdapter.invoke() parses stream-json result event."""
    import json as _json
    from unittest.mock import AsyncMock, patch

    output = _json.dumps({"type": "result", "result": "Hello!"})
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output.encode(), b""))
    mock_proc.returncode = 0

    with patch(_CLAUDE_EXEC, return_value=mock_proc):
        adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="hi",
            system_prompt="you are helpful",
            mcp_servers={},
            env={},
        )
    assert result_text == "Hello!"
    assert tool_calls == []
    assert usage is None  # no usage in result event


async def test_claude_code_adapter_invoke_with_tool_calls():
    """ClaudeCodeAdapter.invoke() captures tool_use content blocks."""
    import json as _json
    from unittest.mock import AsyncMock, patch

    output_lines = "\n".join(
        [
            _json.dumps(
                {
                    "type": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "state_get",
                            "input": {"key": "foo"},
                        }
                    ],
                }
            ),
            _json.dumps({"type": "result", "result": "Done"}),
        ]
    )
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output_lines.encode(), b""))
    mock_proc.returncode = 0

    with patch(_CLAUDE_EXEC, return_value=mock_proc):
        adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="use tools",
            system_prompt="you are helpful",
            mcp_servers={},
            env={},
        )
    assert result_text == "Done"
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "state_get"
    assert tool_calls[0]["input"] == {"key": "foo"}


async def test_claude_code_adapter_invoke_captures_usage():
    """ClaudeCodeAdapter.invoke() extracts token usage from result event."""
    import json as _json
    from unittest.mock import AsyncMock, patch

    output = _json.dumps(
        {
            "type": "result",
            "result": "With usage!",
            "usage": {"input_tokens": 150, "output_tokens": 300},
        }
    )
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output.encode(), b""))
    mock_proc.returncode = 0

    with patch(_CLAUDE_EXEC, return_value=mock_proc):
        adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="test",
            system_prompt="you are helpful",
            mcp_servers={},
            env={},
        )
    assert result_text == "With usage!"
    assert usage is not None
    assert usage["input_tokens"] == 150
    assert usage["output_tokens"] == 300


async def test_claude_code_adapter_invoke_none_usage():
    """ClaudeCodeAdapter.invoke() returns None usage when no usage in result."""
    import json as _json
    from unittest.mock import AsyncMock, patch

    output = _json.dumps({"type": "result", "result": "No usage"})
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output.encode(), b""))
    mock_proc.returncode = 0

    with patch(_CLAUDE_EXEC, return_value=mock_proc):
        adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="test",
            system_prompt="you are helpful",
            mcp_servers={},
            env={},
        )
    assert result_text == "No usage"
    assert usage is None


async def test_claude_code_adapter_command_includes_mcp_config(tmp_path: Path):
    """ClaudeCodeAdapter includes --mcp-config and --strict-mcp-config flags."""
    from unittest.mock import AsyncMock, patch

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    mock_proc.pid = 1

    captured_cmd: list[str] = []

    async def capturing_exec(*args, **kwargs):
        captured_cmd.extend(args)
        return mock_proc

    with patch(_CLAUDE_EXEC, side_effect=capturing_exec):
        adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")
        await adapter.invoke(
            prompt="test",
            system_prompt="sys",
            mcp_servers={"test": {"url": "http://localhost:9100/mcp"}},
            env={},
        )

    assert "--mcp-config" in captured_cmd
    assert "--strict-mcp-config" in captured_cmd
    assert "--bare" in captured_cmd
    assert "--no-session-persistence" in captured_cmd
    assert "--permission-mode" in captured_cmd
    assert "bypassPermissions" in captured_cmd


# ---------------------------------------------------------------------------
# ClaudeCodeAdapter stderr capture tests
# ---------------------------------------------------------------------------


async def test_claude_code_adapter_creates_stderr_log_when_butler_name_set(tmp_path: Path):
    """ClaudeCodeAdapter creates per-butler stderr log file when butler_name is set."""
    from unittest.mock import AsyncMock, patch

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"some stderr output"))
    mock_proc.returncode = 0
    mock_proc.pid = 42

    with patch(_CLAUDE_EXEC, return_value=mock_proc):
        adapter = ClaudeCodeAdapter(
            claude_binary="/usr/bin/claude",
            butler_name="test-butler",
            log_root=tmp_path,
        )
        await adapter.invoke(
            prompt="hi",
            system_prompt="sys",
            mcp_servers={},
            env={},
        )

    # Verify the log file was created with session start marker
    stderr_log = tmp_path / "butlers" / "test-butler_cc_stderr.log"
    assert stderr_log.exists()
    content = stderr_log.read_text()
    assert "runtime session start:" in content


async def test_claude_code_adapter_no_stderr_without_butler_name():
    """ClaudeCodeAdapter does not create stderr log when butler_name is not set."""
    from unittest.mock import AsyncMock, patch

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    mock_proc.pid = 1

    with patch(_CLAUDE_EXEC, return_value=mock_proc):
        adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")
        # Should not raise — no log file needed
        await adapter.invoke(
            prompt="hi",
            system_prompt="sys",
            mcp_servers={},
            env={},
        )


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
