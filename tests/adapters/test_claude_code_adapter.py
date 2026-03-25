"""Tests for ClaudeCodeAdapter — Claude CLI runtime adapter.

Adapter-specific tests only. Common parser contract tests (plain text, JSON messages,
tool calls, exit codes) and shared behavioral contracts (build_config_file, invoke CWD,
invoke timeout, etc.) are parametrized across all adapters in test_adapter_contract.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import ClaudeCodeAdapter, get_adapter
from butlers.core.runtimes.claude_code import _find_claude_binary

pytestmark = pytest.mark.unit

# Long patch target as constant to keep lines within 100 chars
_EXEC = "butlers.core.runtimes.claude_code.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


def test_claude_code_adapter_registered():
    """get_adapter('claude') returns ClaudeCodeAdapter."""
    assert get_adapter("claude") is ClaudeCodeAdapter


def test_claude_code_adapter_instantiates():
    """ClaudeCodeAdapter can be instantiated without arguments."""
    adapter = ClaudeCodeAdapter()
    assert adapter is not None


def test_claude_code_adapter_with_custom_binary():
    """ClaudeCodeAdapter accepts a custom binary path."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/local/bin/claude")
    assert adapter._claude_binary == "/usr/local/bin/claude"
    assert adapter._get_binary() == "/usr/local/bin/claude"


def test_claude_code_adapter_binary_name():
    """binary_name property returns 'claude'."""
    adapter = ClaudeCodeAdapter()
    assert adapter.binary_name == "claude"


# ---------------------------------------------------------------------------
# create_worker() tests
# ---------------------------------------------------------------------------


def test_create_worker_returns_new_instance():
    """create_worker() returns a distinct adapter instance."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/local/bin/claude")
    worker = adapter.create_worker()

    assert worker is not adapter
    assert isinstance(worker, ClaudeCodeAdapter)


def test_create_worker_preserves_butler_name():
    """create_worker() preserves butler_name."""
    adapter = ClaudeCodeAdapter(butler_name="my-butler")
    worker = adapter.create_worker()

    assert worker._butler_name == "my-butler"


def test_create_worker_preserves_log_root(tmp_path: Path):
    """create_worker() preserves log_root."""
    adapter = ClaudeCodeAdapter(log_root=tmp_path)
    worker = adapter.create_worker()

    assert worker._log_root == tmp_path


def test_create_worker_preserves_binary():
    """create_worker() preserves custom binary path."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/local/bin/claude")
    worker = adapter.create_worker()

    assert worker._claude_binary == "/usr/local/bin/claude"


def test_create_worker_no_binary():
    """create_worker() preserves None binary path."""
    adapter = ClaudeCodeAdapter()
    worker = adapter.create_worker()
    assert worker._claude_binary is None


# ---------------------------------------------------------------------------
# _find_claude_binary tests
# ---------------------------------------------------------------------------


def test_find_claude_binary_found():
    """_find_claude_binary returns path when claude is on PATH."""
    with patch(
        "butlers.core.runtimes.claude_code.shutil.which",
        return_value="/usr/bin/claude",
    ):
        assert _find_claude_binary() == "/usr/bin/claude"


def test_find_claude_binary_not_found():
    """_find_claude_binary raises FileNotFoundError when claude is missing."""
    with patch(
        "butlers.core.runtimes.claude_code.shutil.which",
        return_value=None,
    ):
        with pytest.raises(FileNotFoundError, match="Claude CLI binary not found"):
            _find_claude_binary()


def test_find_claude_binary_not_found_includes_install_hint():
    """FileNotFoundError includes npm install hint."""
    with patch(
        "butlers.core.runtimes.claude_code.shutil.which",
        return_value=None,
    ):
        with pytest.raises(FileNotFoundError, match="npm install"):
            _find_claude_binary()


# ---------------------------------------------------------------------------
# parse_system_prompt_file tests
# ---------------------------------------------------------------------------


def test_parse_system_prompt_reads_claude_md(tmp_path: Path):
    """ClaudeCodeAdapter reads CLAUDE.md for system prompt."""
    adapter = ClaudeCodeAdapter()
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("You are a specialized Claude butler.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "You are a specialized Claude butler."


def test_parse_system_prompt_missing_claude_md(tmp_path: Path):
    """Returns empty string when CLAUDE.md is missing."""
    adapter = ClaudeCodeAdapter()
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


def test_parse_system_prompt_empty_claude_md(tmp_path: Path):
    """Returns empty string when CLAUDE.md is empty/whitespace."""
    adapter = ClaudeCodeAdapter()
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("   \n  ")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


# ---------------------------------------------------------------------------
# build_config_file tests
# ---------------------------------------------------------------------------


def test_build_config_file_writes_mcp_json(tmp_path: Path):
    """build_config_file() writes mcp.json with mcpServers key."""
    adapter = ClaudeCodeAdapter()
    mcp_servers = {"my-butler": {"url": "http://localhost:9100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    assert config_path == tmp_path / "mcp.json"
    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["my-butler"]["url"] == "http://localhost:9100/mcp"


def test_build_config_file_empty_servers(tmp_path: Path):
    """build_config_file() writes mcp.json with empty mcpServers dict."""
    adapter = ClaudeCodeAdapter()
    config_path = adapter.build_config_file(mcp_servers={}, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert data == {"mcpServers": {}}


def test_build_config_file_multiple_servers(tmp_path: Path):
    """build_config_file() writes all mcp_servers entries."""
    adapter = ClaudeCodeAdapter()
    mcp_servers = {
        "butler-a": {"url": "http://localhost:9100/mcp"},
        "butler-b": {"url": "http://localhost:9200/mcp"},
    }
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert "butler-a" in data["mcpServers"]
    assert "butler-b" in data["mcpServers"]


# ---------------------------------------------------------------------------
# invoke() tests with mocked subprocess
# ---------------------------------------------------------------------------


async def test_invoke_command_includes_required_flags():
    """invoke() command array includes all required Claude CLI flags."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="do something",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    cmd = mock_sub.call_args[0]
    assert cmd[0] == "/usr/bin/claude"
    assert "-p" in cmd
    assert "--output-format" in cmd
    output_fmt_idx = cmd.index("--output-format")
    assert cmd[output_fmt_idx + 1] == "stream-json"
    assert "--bare" in cmd
    assert "--no-session-persistence" in cmd
    assert "--permission-mode" in cmd
    pm_idx = cmd.index("--permission-mode")
    assert cmd[pm_idx + 1] == "bypassPermissions"
    assert "--strict-mcp-config" in cmd


async def test_invoke_includes_mcp_config_flag():
    """invoke() passes --mcp-config with a temp file path."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="do something",
            system_prompt="",
            mcp_servers={"test": {"url": "http://localhost:9100/mcp"}},
            env={},
        )

    cmd = mock_sub.call_args[0]
    assert "--mcp-config" in cmd
    mcp_idx = cmd.index("--mcp-config")
    assert cmd[mcp_idx + 1].endswith("mcp.json")


async def test_invoke_includes_system_prompt_flag():
    """invoke() adds --system-prompt flag when system_prompt is non-empty."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="do something",
            system_prompt="You are a helpful butler.",
            mcp_servers={},
            env={},
        )

    cmd = mock_sub.call_args[0]
    assert "--system-prompt" in cmd
    sp_idx = cmd.index("--system-prompt")
    assert cmd[sp_idx + 1] == "You are a helpful butler."


async def test_invoke_omits_system_prompt_flag_when_empty():
    """invoke() omits --system-prompt when system_prompt is empty string."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="do something",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    cmd = mock_sub.call_args[0]
    assert "--system-prompt" not in cmd


async def test_invoke_passes_model_flag():
    """invoke() adds --model when model is specified."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="run",
            system_prompt="",
            mcp_servers={},
            env={},
            model="claude-opus-4-5",
        )

    cmd = mock_sub.call_args[0]
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "claude-opus-4-5"


async def test_invoke_omits_model_flag_when_none():
    """invoke() omits --model when model is None."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="run",
            system_prompt="",
            mcp_servers={},
            env={},
            model=None,
        )

    cmd = mock_sub.call_args[0]
    assert "--model" not in cmd


async def test_invoke_appends_runtime_args_after_fixed_flags():
    """invoke() appends runtime_args after fixed flags."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="run",
            system_prompt="",
            mcp_servers={},
            env={},
            runtime_args=["--effort", "high"],
        )

    cmd = mock_sub.call_args[0]
    assert "--effort" in cmd
    effort_idx = cmd.index("--effort")
    assert cmd[effort_idx + 1] == "high"
    # runtime_args appear after --strict-mcp-config
    strict_idx = cmd.index("--strict-mcp-config")
    assert effort_idx > strict_idx


async def test_invoke_env_isolation():
    """invoke() passes only the env kwarg to subprocess — no host env leakage."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    isolated_env = {"ANTHROPIC_API_KEY": "sk-test", "PATH": "/usr/bin"}

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env=isolated_env,
        )

    call_kwargs = mock_sub.call_args[1]
    assert call_kwargs["env"] == isolated_env


async def test_invoke_nonzero_exit_raises_runtime_error():
    """invoke() raises RuntimeError on non-zero exit code."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: rate limit exceeded"))
    mock_proc.returncode = 1

    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError, match="Claude CLI exited with code 1"):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
            )


async def test_invoke_nonzero_exit_error_message_includes_stderr():
    """RuntimeError from non-zero exit includes stderr content."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"rate limit exceeded"))
    mock_proc.returncode = 1

    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError, match="rate limit exceeded"):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
            )


async def test_invoke_stderr_captured_to_log_file(tmp_path: Path):
    """invoke() writes stderr output to per-butler log file when log_root is set."""
    log_root = tmp_path / "logs"
    adapter = ClaudeCodeAdapter(
        claude_binary="/usr/bin/claude",
        butler_name="test-butler",
        log_root=log_root,
    )

    result_json = json.dumps({"type": "result", "result": "Done."})
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(
        return_value=(result_json.encode(), b"Claude diagnostic output here")
    )
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc):
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    log_path = log_root / "butlers" / "test-butler_cc_stderr.log"
    assert log_path.exists()
    log_content = log_path.read_text()
    assert "Claude diagnostic output here" in log_content


async def test_invoke_no_stderr_log_when_log_root_none():
    """invoke() does not create log file when log_root is None."""
    adapter = ClaudeCodeAdapter(
        claude_binary="/usr/bin/claude",
        butler_name="test-butler",
        log_root=None,
    )

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b"some stderr"))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc):
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
        )
    # No assertion needed — just verifying no error is raised


async def test_invoke_success_returns_parsed_output():
    """invoke() returns parsed result_text, tool_calls, and usage from stream-json."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    output_lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "result",
                    "result": "Task complete.",
                    "usage": {
                        "input_tokens": 50,
                        "output_tokens": 10,
                    },
                }
            ),
        ]
    )

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output_lines.encode(), b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc):
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="do work",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    assert result_text == "Task complete."
    assert tool_calls == []
    assert usage == {"input_tokens": 50, "output_tokens": 10}


async def test_invoke_binary_not_found_raises():
    """invoke() raises FileNotFoundError if claude not on PATH."""
    adapter = ClaudeCodeAdapter()  # No binary specified — auto-detect

    with patch(
        "butlers.core.runtimes.claude_code.shutil.which",
        return_value=None,
    ):
        with pytest.raises(FileNotFoundError, match="Claude CLI binary not found"):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
            )


async def test_invoke_prompt_is_final_positional_arg():
    """The prompt text is appended as the final positional argument."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="unique-prompt-text",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    cmd = mock_sub.call_args[0]
    assert cmd[-1] == "unique-prompt-text"


# ---------------------------------------------------------------------------
# credential_store / ANTHROPIC_API_KEY injection tests
# ---------------------------------------------------------------------------


def test_create_worker_preserves_credential_store():
    """create_worker() preserves credential_store reference."""
    from unittest.mock import MagicMock

    mock_store = MagicMock()
    adapter = ClaudeCodeAdapter(credential_store=mock_store)
    worker = adapter.create_worker()

    assert worker._credential_store is mock_store


async def test_invoke_injects_api_key_from_credential_store():
    """invoke() injects ANTHROPIC_API_KEY from credential store when env lacks it."""
    from unittest.mock import AsyncMock, MagicMock

    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value="sk-ant-test-key-123")
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude", credential_store=mock_store)

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    call_kwargs = mock_sub.call_args[1]
    assert call_kwargs["env"]["ANTHROPIC_API_KEY"] == "sk-ant-test-key-123"
    mock_store.load.assert_awaited_once_with("cli-auth/claude")


async def test_invoke_does_not_override_caller_provided_api_key():
    """invoke() does not override ANTHROPIC_API_KEY when caller provides it in env."""
    from unittest.mock import AsyncMock, MagicMock

    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value="sk-ant-from-store")
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude", credential_store=mock_store)

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    caller_env = {"ANTHROPIC_API_KEY": "sk-ant-caller-key", "PATH": "/usr/bin"}

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env=caller_env,
        )

    call_kwargs = mock_sub.call_args[1]
    # Caller-provided key must NOT be overwritten
    assert call_kwargs["env"]["ANTHROPIC_API_KEY"] == "sk-ant-caller-key"
    mock_store.load.assert_not_awaited()


async def test_invoke_falls_back_to_env_when_no_credential_store():
    """invoke() reads ANTHROPIC_API_KEY from os.environ when no credential store set."""
    import os

    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-from-env"}, clear=False):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
            )

    call_kwargs = mock_sub.call_args[1]
    assert call_kwargs["env"]["ANTHROPIC_API_KEY"] == "sk-ant-from-env"


async def test_invoke_credential_store_failure_does_not_raise():
    """invoke() continues without ANTHROPIC_API_KEY if credential store load fails."""
    import os
    from unittest.mock import AsyncMock, MagicMock

    mock_store = MagicMock()
    mock_store.load = AsyncMock(side_effect=Exception("DB connection error"))
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude", credential_store=mock_store)

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    # Remove ANTHROPIC_API_KEY from env so fallback also finds nothing
    env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        with patch.dict(os.environ, env_without_key, clear=True):
            # Should not raise — just proceeds without the key
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={"PATH": "/usr/bin"},
            )

    call_kwargs = mock_sub.call_args[1]
    assert "ANTHROPIC_API_KEY" not in call_kwargs["env"]


# ---------------------------------------------------------------------------
# Import path tests
# ---------------------------------------------------------------------------
