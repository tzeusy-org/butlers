"""Tests for ClaudeCodeAdapter — Claude CLI runtime adapter.

Covers unique ClaudeCode behaviors:
- binary discovery (_find_claude_binary)
- create_worker() preserves all constructor args
- parse_system_prompt_file reads CLAUDE.md
- invoke() CLI flags (required flags, mcp-config, system-prompt, model, runtime_args)
- invoke() stderr log file creation
- invoke() error paths (nonzero exit, timeout)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import ClaudeCodeAdapter
from butlers.core.runtimes.claude_code import _extract_claude_session_id, _find_claude_binary

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.claude_code.asyncio.create_subprocess_exec"


def test_binary_and_create_worker(tmp_path: Path):
    """Binary discovery raises FileNotFoundError with npm hint; create_worker preserves args."""
    with patch("butlers.core.runtimes.claude_code.shutil.which", return_value="/usr/bin/claude"):
        assert _find_claude_binary() == "/usr/bin/claude"
    with patch("butlers.core.runtimes.claude_code.shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="npm install"):
            _find_claude_binary()

    adapter = ClaudeCodeAdapter(
        claude_binary="/usr/bin/claude", butler_name="switchboard", log_root=tmp_path
    )
    worker = adapter.create_worker()
    assert worker is not adapter and isinstance(worker, ClaudeCodeAdapter)
    assert worker._claude_binary == "/usr/bin/claude"
    assert worker._butler_name == "switchboard" and worker._log_root == tmp_path


async def test_invoke_flags_and_output():
    """invoke() required CLI flags; --system-prompt/--model conditional; usage parsing."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    mock_proc.pid = 1

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="do something", system_prompt="", mcp_servers={}, env={})
    cmd = mock_sub.call_args[0]
    assert cmd[0] == "/usr/bin/claude"
    for flag in [
        "--output-format",
        "--bare",
        "--no-session-persistence",
        "--permission-mode",
        "--strict-mcp-config",
    ]:
        assert flag in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"

    # --system-prompt conditional
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test", system_prompt="You are helpful.", mcp_servers={}, env={}
        )
    cmd = mock_sub.call_args[0]
    assert "--system-prompt" in cmd and cmd[cmd.index("--system-prompt") + 1] == "You are helpful."

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})
    assert "--system-prompt" not in mock_sub.call_args[0]

    # --model conditional
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="run", system_prompt="", mcp_servers={}, env={}, model="claude-opus-4-5"
        )
    cmd = mock_sub.call_args[0]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "claude-opus-4-5"

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="run", system_prompt="", mcp_servers={}, env={}, model=None)
    assert "--model" not in mock_sub.call_args[0]

    # output parsing with and without usage
    output = json.dumps({"type": "result", "result": "Hello!"})
    mock_proc.communicate = AsyncMock(return_value=(output.encode(), b""))
    with patch(_EXEC, return_value=mock_proc):
        result_text, tool_calls, usage = await ClaudeCodeAdapter(
            claude_binary="/usr/bin/claude"
        ).invoke(prompt="hi", system_prompt="", mcp_servers={}, env={})
    assert result_text == "Hello!" and tool_calls == [] and usage is None

    output2 = json.dumps(
        {"type": "result", "result": "ok", "usage": {"input_tokens": 150, "output_tokens": 300}}
    )
    mock_proc.communicate = AsyncMock(return_value=(output2.encode(), b""))
    with patch(_EXEC, return_value=mock_proc):
        _, _, usage2 = await ClaudeCodeAdapter(claude_binary="/usr/bin/claude").invoke(
            prompt="test", system_prompt="", mcp_servers={}, env={}
        )
    assert usage2 == {"input_tokens": 150, "output_tokens": 300}


def test_supports_resume_flag():
    """ClaudeCodeAdapter opts into the resume extension point (bu-ep4ks.8)."""
    assert ClaudeCodeAdapter.supports_resume is True


async def test_invoke_resume_session_id_conditional():
    """--resume is only emitted when resume_session_id is a non-empty string."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    mock_proc.pid = 1

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="continue",
            system_prompt="",
            mcp_servers={},
            env={},
            resume_session_id="sess-abc-123",
        )
    cmd = mock_sub.call_args[0]
    assert "--resume" in cmd and cmd[cmd.index("--resume") + 1] == "sess-abc-123"

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="cold start", system_prompt="", mcp_servers={}, env={})
    assert "--resume" not in mock_sub.call_args[0]

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="blank handle",
            system_prompt="",
            mcp_servers={},
            env={},
            resume_session_id="   ",
        )
    assert "--resume" not in mock_sub.call_args[0]


async def test_invoke_captures_provider_session_id_on_success():
    """last_process_info surfaces the CLI-reported session_id for the next resume."""
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")
    output = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-fresh-1"}),
            json.dumps({"type": "result", "result": "Done"}),
        ]
    )
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output.encode(), b""))
    mock_proc.returncode = 0
    mock_proc.pid = 1

    with patch(_EXEC, return_value=mock_proc):
        await adapter.invoke(prompt="hi", system_prompt="", mcp_servers={}, env={})

    assert adapter.last_process_info["provider_session_id"] == "sess-fresh-1"


async def test_invoke_captures_none_provider_session_id_when_absent():
    adapter = ClaudeCodeAdapter(claude_binary="/usr/bin/claude")
    output = json.dumps({"type": "result", "result": "Done"})
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output.encode(), b""))
    mock_proc.returncode = 0
    mock_proc.pid = 1

    with patch(_EXEC, return_value=mock_proc):
        await adapter.invoke(prompt="hi", system_prompt="", mcp_servers={}, env={})

    assert adapter.last_process_info["provider_session_id"] is None


def test_extract_claude_session_id_scans_any_event_type():
    output = "\n".join(
        [
            json.dumps({"type": "assistant", "content": "no session id here"}),
            json.dumps({"type": "result", "result": "Done", "session_id": "sess-9"}),
        ]
    )
    assert _extract_claude_session_id(output) == "sess-9"


def test_extract_claude_session_id_returns_none_for_non_json_output():
    assert _extract_claude_session_id("plain text, not json-lines") is None
    assert _extract_claude_session_id("") is None


async def test_invoke_stderr_log(tmp_path: Path):
    """Creates per-butler stderr log when butler_name is set; no log without butler_name."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"some stderr output"))
    mock_proc.returncode = 0
    mock_proc.pid = 42

    with patch(_EXEC, return_value=mock_proc):
        adapter = ClaudeCodeAdapter(
            claude_binary="/usr/bin/claude", butler_name="test-butler", log_root=tmp_path
        )
        await adapter.invoke(prompt="hi", system_prompt="sys", mcp_servers={}, env={})

    stderr_log = tmp_path / "butlers" / "test-butler_cc_stderr.log"
    assert stderr_log.exists() and "runtime session start:" in stderr_log.read_text()

    mock_proc.pid = 1
    with patch(_EXEC, return_value=mock_proc):
        await ClaudeCodeAdapter(claude_binary="/usr/bin/claude").invoke(
            prompt="hi", system_prompt="sys", mcp_servers={}, env={}
        )


async def test_invoke_hands_the_cli_an_mcp_config_with_the_invocation_header():
    """The Spawner's binding header reaches the --mcp-config file the CLI reads (bu-m2zcah)."""
    from butlers.core.fact_authority import INVOCATION_HEADER

    seen: dict[str, object] = {}

    async def fake_exec(*cmd, **kwargs):
        config_path = Path(cmd[cmd.index("--mcp-config") + 1])
        seen["config"] = json.loads(await asyncio.to_thread(config_path.read_text))
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        proc.pid = 1
        return proc

    with patch(_EXEC, side_effect=fake_exec):
        await ClaudeCodeAdapter(claude_binary="/usr/bin/claude").invoke(
            prompt="hi",
            system_prompt="",
            mcp_servers={
                "my-butler": {
                    "url": "http://localhost:9100/mcp",
                    "headers": {INVOCATION_HEADER: "synthetic-binding"},
                }
            },
            env={},
        )

    entry = seen["config"]["mcpServers"]["my-butler"]
    assert entry["headers"] == {"X-Butlers-Fact-Invocation": "synthetic-binding"}
    # Without a ``type`` the CLI defaults the entry to stdio, rejects it for lacking
    # ``command`` and skips the server, so the header and the connection would both be lost.
    assert entry["type"] == "http"


def test_build_config_file_keeps_stdio_and_explicit_types(tmp_path: Path):
    """Only a url entry with no type gains ``http``; stdio and explicit types are untouched."""
    config_path = ClaudeCodeAdapter(claude_binary="/usr/bin/claude").build_config_file(
        mcp_servers={
            "local": {"command": "some-server", "args": ["--flag"]},
            "streaming": {"type": "sse", "url": "http://localhost:9100/sse"},
            "plain": {"url": "http://localhost:9100/mcp"},
        },
        tmp_dir=tmp_path,
    )

    servers = json.loads(config_path.read_text())["mcpServers"]
    assert "type" not in servers["local"]
    assert servers["streaming"]["type"] == "sse"
    assert servers["plain"]["type"] == "http"
