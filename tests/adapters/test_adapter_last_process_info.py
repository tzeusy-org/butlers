"""Unit tests for RuntimeAdapter.last_process_info — no Docker required.

Verifies that concrete adapters populate last_process_info after invoke()
completes (success and timeout) and that the base class default is None.

Issue: bu-gjb1.2 (openspec/changes/session-process-logs task 6.4)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import CodexAdapter, GeminiAdapter, RuntimeAdapter

pytestmark = pytest.mark.unit

_CODEX_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"
_GEMINI_EXEC = "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec"

_SUBPROCESS_ADAPTERS = [
    pytest.param(CodexAdapter, "codex_binary", "/usr/bin/codex", _CODEX_EXEC, "codex", id="codex"),
    pytest.param(
        GeminiAdapter, "gemini_binary", "/usr/bin/gemini", _GEMINI_EXEC, "gemini", id="gemini"
    ),
]


# ---------------------------------------------------------------------------
# Base class default: last_process_info returns None
# ---------------------------------------------------------------------------


def test_base_adapter_last_process_info_is_none():
    """RuntimeAdapter base class returns None for last_process_info by default."""

    class _MinimalAdapter(RuntimeAdapter):
        @property
        def binary_name(self) -> str:
            return "test-binary"

        async def invoke(
            self,
            prompt: str,
            system_prompt: str,
            mcp_servers: dict[str, Any],
            env: dict[str, str],
            **kwargs: Any,
        ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
            return ("ok", [], None)

        def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
            return tmp_dir / "config.json"

        def parse_system_prompt_file(self, config_dir: Path) -> str:
            return ""

    adapter = _MinimalAdapter()
    assert adapter.last_process_info is None


# ---------------------------------------------------------------------------
# Before first invoke(): last_process_info is None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "adapter_class, binary_kwarg, binary, exec_patch, runtime_type",
    _SUBPROCESS_ADAPTERS,
)
def test_last_process_info_none_before_first_invoke(
    adapter_class: type,
    binary_kwarg: str,
    binary: str,
    exec_patch: str,
    runtime_type: str,
) -> None:
    """last_process_info is None before any invoke() has been called."""
    adapter = adapter_class(**{binary_kwarg: binary})
    assert adapter.last_process_info is None


# ---------------------------------------------------------------------------
# After successful invoke(): last_process_info is populated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "adapter_class, binary_kwarg, binary, exec_patch, runtime_type",
    _SUBPROCESS_ADAPTERS,
)
async def test_last_process_info_populated_after_successful_invoke(
    adapter_class: type,
    binary_kwarg: str,
    binary: str,
    exec_patch: str,
    runtime_type: str,
) -> None:
    """last_process_info is populated with process metadata after a successful invoke()."""
    adapter = adapter_class(**{binary_kwarg: binary})

    mock_proc = AsyncMock()
    mock_proc.pid = 12345
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

    with patch(exec_patch, return_value=mock_proc):
        await adapter.invoke(
            prompt="test prompt",
            system_prompt="sys",
            mcp_servers={},
            env={},
        )

    info = adapter.last_process_info
    assert info is not None
    assert "pid" in info
    assert "exit_code" in info
    assert "command" in info
    assert "stderr" in info
    assert "runtime_type" in info


@pytest.mark.parametrize(
    "adapter_class, binary_kwarg, binary, exec_patch, runtime_type",
    _SUBPROCESS_ADAPTERS,
)
async def test_last_process_info_pid_matches_process(
    adapter_class: type,
    binary_kwarg: str,
    binary: str,
    exec_patch: str,
    runtime_type: str,
) -> None:
    """last_process_info['pid'] matches the subprocess PID after invoke()."""
    adapter = adapter_class(**{binary_kwarg: binary})

    mock_proc = AsyncMock()
    mock_proc.pid = 99999
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

    with patch(exec_patch, return_value=mock_proc):
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    assert adapter.last_process_info is not None
    assert adapter.last_process_info["pid"] == 99999


@pytest.mark.parametrize(
    "adapter_class, binary_kwarg, binary, exec_patch, runtime_type",
    _SUBPROCESS_ADAPTERS,
)
async def test_last_process_info_exit_code_on_success(
    adapter_class: type,
    binary_kwarg: str,
    binary: str,
    exec_patch: str,
    runtime_type: str,
) -> None:
    """last_process_info['exit_code'] is 0 after a successful invocation."""
    adapter = adapter_class(**{binary_kwarg: binary})

    mock_proc = AsyncMock()
    mock_proc.pid = 1
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

    with patch(exec_patch, return_value=mock_proc):
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    info = adapter.last_process_info
    assert info is not None
    assert info["exit_code"] == 0


@pytest.mark.parametrize(
    "adapter_class, binary_kwarg, binary, exec_patch, runtime_type",
    _SUBPROCESS_ADAPTERS,
)
async def test_last_process_info_runtime_type_matches_adapter(
    adapter_class: type,
    binary_kwarg: str,
    binary: str,
    exec_patch: str,
    runtime_type: str,
) -> None:
    """last_process_info['runtime_type'] matches the expected adapter type string."""
    adapter = adapter_class(**{binary_kwarg: binary})

    mock_proc = AsyncMock()
    mock_proc.pid = 1
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

    with patch(exec_patch, return_value=mock_proc):
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    info = adapter.last_process_info
    assert info is not None
    assert info["runtime_type"] == runtime_type


@pytest.mark.parametrize(
    "adapter_class, binary_kwarg, binary, exec_patch, runtime_type",
    _SUBPROCESS_ADAPTERS,
)
async def test_last_process_info_includes_stderr(
    adapter_class: type,
    binary_kwarg: str,
    binary: str,
    exec_patch: str,
    runtime_type: str,
) -> None:
    """last_process_info['stderr'] captures subprocess stderr output."""
    adapter = adapter_class(**{binary_kwarg: binary})

    mock_proc = AsyncMock()
    mock_proc.pid = 1
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b"some warning output"))

    with patch(exec_patch, return_value=mock_proc):
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    info = adapter.last_process_info
    assert info is not None
    assert "some warning output" in info["stderr"]


# ---------------------------------------------------------------------------
# After timeout: last_process_info is still populated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "adapter_class, binary_kwarg, binary, exec_patch, runtime_type",
    _SUBPROCESS_ADAPTERS,
)
async def test_last_process_info_populated_after_timeout(
    adapter_class: type,
    binary_kwarg: str,
    binary: str,
    exec_patch: str,
    runtime_type: str,
) -> None:
    """last_process_info is populated even when invoke() raises TimeoutError."""
    adapter = adapter_class(**{binary_kwarg: binary})

    mock_proc = AsyncMock()
    mock_proc.pid = 5678
    mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()

    with patch(exec_patch, return_value=mock_proc):
        with pytest.raises(TimeoutError):
            await adapter.invoke(
                prompt="slow task",
                system_prompt="",
                mcp_servers={},
                env={},
                timeout=1,
            )

    info = adapter.last_process_info
    assert info is not None
    assert info["pid"] == 5678
    assert info["exit_code"] == -1  # timeout uses -1 as sentinel
    assert "timeout" in info["stderr"].lower()
    assert info["runtime_type"] == runtime_type


@pytest.mark.parametrize(
    "adapter_class, binary_kwarg, binary, exec_patch, runtime_type",
    _SUBPROCESS_ADAPTERS,
)
async def test_last_process_info_updated_on_repeated_invoke(
    adapter_class: type,
    binary_kwarg: str,
    binary: str,
    exec_patch: str,
    runtime_type: str,
) -> None:
    """last_process_info is updated on each successive invoke() call."""
    adapter = adapter_class(**{binary_kwarg: binary})

    # First invocation — PID 1111
    mock_proc1 = AsyncMock()
    mock_proc1.pid = 1111
    mock_proc1.returncode = 0
    mock_proc1.communicate = AsyncMock(return_value=(b"first", b""))

    with patch(exec_patch, return_value=mock_proc1):
        await adapter.invoke(
            prompt="first",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    assert adapter.last_process_info is not None
    assert adapter.last_process_info["pid"] == 1111

    # Second invocation — PID 2222
    mock_proc2 = AsyncMock()
    mock_proc2.pid = 2222
    mock_proc2.returncode = 0
    mock_proc2.communicate = AsyncMock(return_value=(b"second", b""))

    with patch(exec_patch, return_value=mock_proc2):
        await adapter.invoke(
            prompt="second",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    assert adapter.last_process_info is not None
    assert adapter.last_process_info["pid"] == 2222


# ---------------------------------------------------------------------------
# ClaudeCodeAdapter: last_process_info inherits base default (None)
# ---------------------------------------------------------------------------


def test_claude_code_adapter_last_process_info_is_none():
    """ClaudeCodeAdapter inherits base default: last_process_info is always None."""
    from butlers.core.runtimes.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    assert adapter.last_process_info is None


async def test_claude_code_adapter_last_process_info_none_after_invoke():
    """ClaudeCodeAdapter returns None for last_process_info even after invoke()."""
    from claude_agent_sdk import ResultMessage

    from butlers.core.runtimes.claude_code import ClaudeCodeAdapter

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
            result="Done",
        )

    adapter = ClaudeCodeAdapter(sdk_query=mock_query)
    await adapter.invoke(
        prompt="test",
        system_prompt="sys",
        mcp_servers={},
        env={},
    )

    # SDK-based adapter — no subprocess, so last_process_info stays None
    assert adapter.last_process_info is None
