"""Tests for CodexAdapter — Codex CLI runtime adapter.

Unique behaviors not in test_adapter_contract.py:
- Binary discovery (_find_codex_binary)
- parse_system_prompt_file reads AGENTS.md
- build_config_file writes TOML with transport inference
- _parse_codex_output: exec --json format, function_call, mcp_tool_call events
- _extract_tool_call: various container formats, command_execution
- invoke(): exec subcommand, HOME injection, error paths, transport diagnostics
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import CodexAdapter
from butlers.core.runtimes.codex import (
    _extract_tool_call,
    _find_codex_binary,
    _has_mcp_tool_calls,
    _infer_mcp_transport_from_url,
    _parse_codex_output,
    _prefer_ipv4_loopback,
    _resolve_canonical_home,
    _select_error_detail,
)

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"


def test_binary_and_system_prompt(tmp_path: Path):
    """Binary discovery raises FileNotFoundError when missing; AGENTS.md read for system prompt."""
    with patch("butlers.core.runtimes.codex.shutil.which", return_value="/usr/bin/codex"):
        assert _find_codex_binary() == "/usr/bin/codex"
    with patch("butlers.core.runtimes.codex.shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="Codex CLI binary not found"):
            _find_codex_binary()

    (tmp_path / "AGENTS.md").write_text("You are a specialized Codex butler.")
    assert (
        CodexAdapter().parse_system_prompt_file(config_dir=tmp_path)
        == "You are a specialized Codex butler."
    )
    with tempfile.TemporaryDirectory() as empty:
        assert CodexAdapter().parse_system_prompt_file(config_dir=Path(empty)) == ""


def test_build_config_file(tmp_path: Path):
    """build_config_file() writes TOML with correct transport; unsafe names are skipped."""
    # Basic structure
    config_path = CodexAdapter().build_config_file(
        mcp_servers={"my-butler": {"url": "http://localhost:9100/mcp"}}, tmp_dir=tmp_path
    )
    assert config_path == tmp_path / ".codex" / "config.toml"
    content = config_path.read_text()
    assert "[mcp_servers.my-butler]" in content and 'url = "http://127.0.0.1:9100/mcp"' in content
    assert 'transport = "streamable_http"' in content
    # Transport URL inference
    assert _infer_mcp_transport_from_url("http://localhost:41100/mcp") == "streamable_http"
    assert _infer_mcp_transport_from_url("http://localhost:41100/sse") == "sse"
    assert _infer_mcp_transport_from_url("http://localhost:41100/events") is None
    # Unsafe name injection protection
    result = CodexAdapter._write_mcp_config_toml(
        {
            "safe_name": {"url": "http://localhost:9100/mcp"},
            'unsafe".transport="sse': {"url": "http://localhost:9200/mcp"},
        },
        tmp_path,
    )
    assert result is not None
    content2 = result.read_text()
    assert (
        "[mcp_servers.safe_name]" in content2
        and "unsafe" not in content2
        and "9200" not in content2
    )


def test_prefer_ipv4_loopback_rewrites_only_bare_localhost():
    """Codex MCP config should rewrite only exact localhost loopback URLs."""
    assert _prefer_ipv4_loopback("http://localhost:9100/mcp") == "http://127.0.0.1:9100/mcp"
    assert (
        _prefer_ipv4_loopback("http://localhost:9100/mcp?runtime_session_id=sess-1")
        == "http://127.0.0.1:9100/mcp?runtime_session_id=sess-1"
    )
    assert _prefer_ipv4_loopback("http://127.0.0.1:9100/mcp") == "http://127.0.0.1:9100/mcp"
    assert _prefer_ipv4_loopback("http://[::1]:9100/mcp") == "http://[::1]:9100/mcp"
    assert _prefer_ipv4_loopback("https://example.com/mcp") == "https://example.com/mcp"


def test_parse_codex_output_and_extract_tool_call():
    """item.completed mcp_tool_call, JSON string arguments, and command_execution formats."""
    # item.completed mcp_tool_call
    lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "mcp_1",
                        "type": "mcp_tool_call",
                        "call": {"name": "route_to_butler", "arguments": {"butler": "rel"}},
                    },
                }
            ),
            json.dumps({"type": "result", "result": "Routed"}),
        ]
    )
    _, tool_calls, _ = _parse_codex_output(lines, "", 0)
    assert (
        len(tool_calls) == 1
        and tool_calls[0]["id"] == "mcp_1"
        and tool_calls[0]["name"] == "route_to_butler"
    )

    # stringified JSON arguments
    tc = _extract_tool_call(
        {
            "id": "fc2",
            "name": "route_to_butler",
            "arguments": '{"butler":"health","prompt":"Track meal"}',
        }
    )
    assert tc["input"] == {"butler": "health", "prompt": "Track meal"}

    # command_execution normalized
    tc = _extract_tool_call(
        {
            "id": "cmd1",
            "type": "command_execution",
            "command": "ls -1",
            "exit_code": 0,
            "aggregated_output": "file.txt\n",
        }
    )
    assert (
        tc["name"] == "command_execution"
        and tc["input"]["command"] == "ls -1"
        and tc["input"]["exit_code"] == 0
    )


def test_parse_item_started_does_not_duplicate_tool_calls():
    """item.started events for tool calls are skipped to avoid duplicating item.completed."""
    lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "id": "cmd1",
                        "type": "command_execution",
                        "command": "ls -1",
                        "status": "in_progress",
                        "exit_code": None,
                        "aggregated_output": "",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "cmd1",
                        "type": "command_execution",
                        "command": "ls -1",
                        "status": "completed",
                        "exit_code": 0,
                        "aggregated_output": "file.txt\n",
                    },
                }
            ),
        ]
    )
    _, tool_calls, _ = _parse_codex_output(lines, "", 0)
    assert len(tool_calls) == 1, f"Expected 1 tool call, got {len(tool_calls)}"
    assert tool_calls[0]["input"]["exit_code"] == 0
    assert tool_calls[0]["input"]["aggregated_output"] == "file.txt\n"


async def test_invoke_behaviors():
    """invoke() uses exec subcommand, injects HOME, raises on error, adds transport diagnostics."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    # exec subcommand
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})
    assert mock_sub.call_args[0][:2] == ("/usr/bin/codex", "exec")
    assert "--ephemeral" in mock_sub.call_args[0]
    assert mock_sub.call_args[0][-1] == "-"
    assert mock_sub.call_args[1]["stdin"] is asyncio.subprocess.PIPE
    mock_proc.communicate.assert_awaited_once_with(b"test")

    # HOME injection with mcp servers
    mock_proc.communicate = AsyncMock(return_value=(_make_mcp_stdout(), b""))
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={"s": {"url": "http://localhost/mcp"}},
            env={},
        )
    assert "HOME" in mock_sub.call_args[1].get("env", {})

    # Plain error → RuntimeError
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: rate limit"))
    mock_proc.returncode = 1
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError, match="Codex CLI exited with code 1: Error: rate limit"):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    # Structured stdout on non-zero exit should surface text, not raw JSON lines
    mock_proc.communicate = AsyncMock(
        return_value=(
            "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "thread-123"}),
                    json.dumps({"type": "turn.started"}),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "msg_1",
                                "type": "agent_message",
                                "text": "Authentication failed.",
                            },
                        }
                    ),
                ]
            ).encode(),
            b"",
        )
    )
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(
            RuntimeError,
            match="Codex CLI exited with code 1: Authentication failed\\.",
        ):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    # Benign stdin notice should not mask the real error
    mock_proc.communicate = AsyncMock(
        return_value=(b"", b"Reading additional input from stdin...\nError: rate limit")
    )
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError, match="Codex CLI exited with code 1: Error: rate limit"):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    # Transport failure includes diagnostics
    mock_proc.communicate = AsyncMock(
        return_value=(b"", b"rmcp startup failed: 405 Method Not Allowed")
    )
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError) as exc_info:
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={"switchboard": {"url": "http://localhost:41100/sse"}},
                env={},
            )
    assert "MCP transport diagnostics" in str(exc_info.value)


async def test_invoke_prefers_home_scoped_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """invoke() should avoid /tmp-backed HOME when a real home directory is available."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path))

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    isolated_home = Path(mock_sub.call_args[1]["env"]["HOME"])
    assert isolated_home.parent == codex_dir / ".tmp"


async def test_invoke_stdin_prompt_wraps_system_prompt():
    """invoke() writes the composed system+user prompt to stdin when using "-"."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc):
        await adapter.invoke(
            prompt="Investigate",
            system_prompt="You are the runtime.",
            mcp_servers={},
            env={},
        )

    mock_proc.communicate.assert_awaited_once_with(
        b"<system_instructions>\n"
        b"You are the runtime.\n"
        b"</system_instructions>\n\n"
        b"<user_prompt>\n"
        b"Investigate\n"
        b"</user_prompt>"
    )


async def test_invoke_uses_passwd_home_when_home_is_nested_codex_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """invoke() should ignore transient ``~/.codex/.tmp/<session>`` HOME values."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    real_home = tmp_path / "real-home"
    (real_home / ".codex").mkdir(parents=True)
    session_home = real_home / ".codex" / ".tmp" / "session-123"
    session_home.mkdir(parents=True)
    (real_home / ".codex" / "auth.json").write_text("{}")

    class _PwRecord:
        pw_dir = str(real_home)

    monkeypatch.setenv("HOME", str(session_home))
    monkeypatch.setattr("butlers.core.runtimes.codex.pwd.getpwuid", lambda _uid: _PwRecord())

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    isolated_home = Path(mock_sub.call_args[1]["env"]["HOME"])
    assert isolated_home.parent == real_home / ".codex" / ".tmp"


def test_has_mcp_tool_calls():
    """_has_mcp_tool_calls distinguishes MCP tools from bash-only sessions."""
    assert not _has_mcp_tool_calls([])
    assert not _has_mcp_tool_calls([{"name": "command_execution"}])
    assert _has_mcp_tool_calls([{"name": "mcp__switchboard__route_to_butler"}])
    assert _has_mcp_tool_calls([{"name": "command_execution"}, {"name": "route_to_butler"}])


def test_select_error_detail_filters_benign_stdin_notice():
    """Benign stdin notices should not become the reported failure headline."""
    detail = _select_error_detail(
        "Reading additional input from stdin...\nError: rate limit",
        "",
        1,
    )
    assert detail == "Error: rate limit"


def test_select_error_detail_prefers_stdout_json_error_payload():
    """Structured stdout errors should surface their message instead of JSON lines."""
    detail = _select_error_detail(
        "",
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-123"}),
                json.dumps(
                    {
                        "type": "error",
                        "error": {"message": "transport connection failed"},
                    }
                ),
            ]
        ),
        7,
    )
    assert detail == "transport connection failed"


def test_select_error_detail_uses_stdout_agent_message_fallback():
    """When stdout has only progress events plus an assistant message, use the text."""
    detail = _select_error_detail(
        "",
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-123"}),
                json.dumps({"type": "turn.started"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "msg_1",
                            "type": "agent_message",
                            "text": "Authentication failed.",
                        },
                    }
                ),
            ]
        ),
        7,
    )
    assert detail == "Authentication failed."


def test_select_error_detail_does_not_dump_json_progress_without_text():
    """JSON progress-only stdout should collapse to the exit code."""
    detail = _select_error_detail(
        "",
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-123"}),
                json.dumps({"type": "turn.started"}),
            ]
        ),
        7,
    )
    assert detail == "exit code 7"


def test_resolve_canonical_home_collapses_nested_codex_tmp_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Nested ``~/.codex/.tmp/<session>`` homes resolve back to the real home."""
    real_home = tmp_path / "real-home"
    session_home = real_home / ".codex" / ".tmp" / "session-123"
    session_home.mkdir(parents=True)

    class _PwRecord:
        pw_dir = str(real_home)

    monkeypatch.setattr("butlers.core.runtimes.codex.pwd.getpwuid", lambda _uid: _PwRecord())
    assert _resolve_canonical_home(str(session_home)) == real_home


def _make_mcp_stdout() -> bytes:
    """Build Codex JSON-lines output containing an MCP tool call."""
    return (
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "mcp_1",
                    "type": "mcp_tool_call",
                    "call": {"name": "route_to_butler", "arguments": {"butler": "finance"}},
                },
            }
        )
        + "\n"
        + json.dumps({"type": "result", "result": "Routed to finance"})
    ).encode()


def _make_bash_only_stdout() -> bytes:
    """Build Codex JSON-lines output with only bash command_execution events."""
    return (
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "cmd1",
                    "type": "command_execution",
                    "command": "/bin/bash -lc true",
                    "status": "completed",
                    "exit_code": 0,
                    "aggregated_output": "",
                },
            }
        )
        + "\n"
        + json.dumps({"type": "result", "result": "MCP tools called: none."})
    ).encode()


def _make_text_only_stdout() -> bytes:
    """Build Codex JSON-lines output with a valid plain-text-only answer."""
    return (
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "msg1",
                    "type": "agent_message",
                    "text": "Here is a direct answer that does not need tools.",
                },
            }
        )
        + "\n"
        + json.dumps(
            {"type": "result", "result": "Here is a direct answer that does not need tools."}
        )
    ).encode()


_MCP_SERVERS = {"switchboard": {"url": "http://localhost:41100/mcp"}}


async def test_retry_on_mcp_connection_failure():
    """invoke() retries when MCP tools not discovered, succeeds on second attempt."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    call_count = 0

    async def _mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 100 + call_count
        # First call: bash only (MCP failure). Second call: MCP tools present.
        if call_count == 1:
            proc.communicate = AsyncMock(return_value=(_make_bash_only_stdout(), b""))
        else:
            proc.communicate = AsyncMock(return_value=(_make_mcp_stdout(), b""))
        return proc

    with (
        patch(_EXEC, side_effect=_mock_exec),
        patch("butlers.core.runtimes.codex._MCP_RETRY_DELAYS", (0,)),
    ):
        result_text, tool_calls, _ = await adapter.invoke(
            prompt="route this",
            system_prompt="",
            mcp_servers=_MCP_SERVERS,
            env={},
        )

    assert call_count == 2, "Should have retried once"
    assert any(tc.get("name") == "route_to_butler" for tc in tool_calls)
    info = adapter.last_process_info
    assert info["mcp_connection_failed"] is True
    assert info["retry_attempted"] is True
    assert info["retry_succeeded"] is True


async def test_retry_all_fail_raises_runtime_error():
    """When all retries fail, invoke() raises so the spawner records a failed session."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    call_count = 0

    async def _mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 42
        proc.communicate = AsyncMock(return_value=(_make_bash_only_stdout(), b""))
        return proc

    with (
        patch(_EXEC, side_effect=_mock_exec),
        patch("butlers.core.runtimes.codex._MCP_RETRY_DELAYS", (0, 0)),
    ):
        with pytest.raises(RuntimeError, match=r"MCP tool discovery failed after 3 attempts"):
            await adapter.invoke(
                prompt="route this",
                system_prompt="",
                mcp_servers=_MCP_SERVERS,
                env={},
            )

    assert call_count == 3, "Should have tried 3 times (initial + 2 retries)"
    info = adapter.last_process_info
    assert info["mcp_connection_failed"] is True
    assert info["retry_attempted"] is True
    assert info["retry_succeeded"] is False
    assert info["attempt_count"] == 3


async def test_no_retry_without_mcp_servers():
    """No retry when mcp_servers is empty (no MCP configured)."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    call_count = 0

    async def _mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 42
        proc.communicate = AsyncMock(return_value=(b"ok", b""))
        return proc

    with patch(_EXEC, side_effect=_mock_exec):
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    assert call_count == 1, "Should NOT retry when no MCP servers configured"


async def test_no_retry_when_mcp_servers_present_but_response_is_text_only():
    """Plain text replies remain valid even when MCP servers are configured."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    call_count = 0

    async def _mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 42
        proc.communicate = AsyncMock(return_value=(_make_text_only_stdout(), b""))
        return proc

    with patch(_EXEC, side_effect=_mock_exec):
        result_text, tool_calls, _ = await adapter.invoke(
            prompt="say hello directly",
            system_prompt="",
            mcp_servers=_MCP_SERVERS,
            env={},
        )

    assert call_count == 1, "Plain text sessions must not be retried as MCP failures"
    assert result_text is not None
    assert "Here is a direct answer that does not need tools." in result_text
    assert tool_calls == []
    info = adapter.last_process_info
    assert info["mcp_connection_failed"] is False
    assert info["attempt_count"] == 1


async def test_qa_context_single_execution_bash_only():
    """QA sessions (empty mcp_servers) execute exactly one subprocess even with bash-only output.

    This guards against the regression where QA investigation/review-follow-up sessions
    were retried because the adapter saw zero non-bash MCP tool calls.  When the spawner
    correctly passes mcp_servers={} for trigger_source='qa', the adapter must not retry.
    """
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    call_count = 0

    async def _mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 42
        # Bash-only output (no MCP tool calls) — the typical QA session output
        proc.communicate = AsyncMock(return_value=(_make_bash_only_stdout(), b""))
        return proc

    # Pass empty mcp_servers — simulates the qa-gated spawner path
    with patch(_EXEC, side_effect=_mock_exec):
        result_text, tool_calls, _ = await adapter.invoke(
            prompt="investigate the failure in the worktree",
            system_prompt="",
            mcp_servers={},  # empty — as spawner sets for trigger_source='qa'
            env={},
        )

    assert call_count == 1, (
        "QA sessions must execute exactly one subprocess — "
        "MCP-discovery retry must not trigger when mcp_servers is empty"
    )
    # Only bash tool calls, no MCP tool calls
    assert all(tc.get("name") == "command_execution" for tc in tool_calls)


async def test_no_retry_on_nonzero_exit():
    """No retry when CLI exits with non-zero code (real error, not MCP flake)."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    async def _mock_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.returncode = 1
        proc.pid = 42
        proc.communicate = AsyncMock(return_value=(b"", b"Auth error"))
        return proc

    with patch(_EXEC, side_effect=_mock_exec):
        with pytest.raises(RuntimeError, match="Codex CLI exited with code 1"):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers=_MCP_SERVERS,
                env={},
            )


async def test_retry_on_transient_remote_compaction_failure():
    """Transient Codex backend compaction failures should retry and recover."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    call_count = 0

    async def _mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        proc = AsyncMock()
        proc.returncode = 1 if call_count == 1 else 0
        proc.pid = 100 + call_count
        proc.communicate = AsyncMock(
            return_value=(
                b"" if call_count == 1 else b"ok",
                (
                    b"2026-04-27T00:09:42Z ERROR codex_core::compact_remote: "
                    b"remote compaction failed turn_id=abc\n"
                    if call_count == 1
                    else b""
                ),
            )
        )
        return proc

    with (
        patch(_EXEC, side_effect=_mock_exec),
        patch("butlers.core.runtimes.codex._TRANSIENT_CLI_RETRY_DELAYS", (0,)),
    ):
        result_text, _, _ = await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers=_MCP_SERVERS,
            env={},
        )

    assert call_count == 2
    assert result_text == "ok"
    info = adapter.last_process_info
    assert info["result_source"] == "retry"
    assert info["attempt_count"] == 2
    assert info["retry_attempted"] is True
    assert info["retry_succeeded"] is True


async def test_retry_on_transient_remote_compaction_failure_exhausted():
    """Persistent remote-compaction failures should surface after bounded retries."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    call_count = 0

    async def _mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        proc = AsyncMock()
        proc.returncode = 1
        proc.pid = 100 + call_count
        proc.communicate = AsyncMock(
            return_value=(
                b"",
                (
                    b"2026-04-27T00:09:42Z ERROR codex_core::compact_remote: "
                    b"remote compaction failed turn_id=abc\n"
                ),
            )
        )
        return proc

    with (
        patch(_EXEC, side_effect=_mock_exec),
        patch("butlers.core.runtimes.codex._TRANSIENT_CLI_RETRY_DELAYS", (0, 0)),
    ):
        with pytest.raises(RuntimeError, match="remote compaction failed"):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers=_MCP_SERVERS,
                env={},
            )

    assert call_count == 3
    info = adapter.last_process_info
    assert info["result_source"] == "first"
    assert info["attempt_count"] == 3
    assert info["retry_attempted"] is True
    assert info["retry_succeeded"] is False


async def test_retry_provenance_result_source_retry():
    """retry succeeded: result_source='retry', attempt_count=2."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    call_count = 0

    async def _mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 100 + call_count
        proc.communicate = AsyncMock(
            return_value=(
                _make_bash_only_stdout() if call_count == 1 else _make_mcp_stdout(),
                b"",
            )
        )
        return proc

    with (
        patch(_EXEC, side_effect=_mock_exec),
        patch("butlers.core.runtimes.codex._MCP_RETRY_DELAYS", (0,)),
    ):
        await adapter.invoke(
            prompt="route this",
            system_prompt="",
            mcp_servers=_MCP_SERVERS,
            env={},
        )

    info = adapter.last_process_info
    assert info["result_source"] == "retry"
    assert info["attempt_count"] == 2
    assert info["retry_attempted"] is True
    assert info["retry_succeeded"] is True


async def test_retry_provenance_result_source_first():
    """All attempts fail MCP discovery: result_source='first', attempt_count=3."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    async def _mock_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 42
        proc.communicate = AsyncMock(return_value=(_make_bash_only_stdout(), b""))
        return proc

    with (
        patch(_EXEC, side_effect=_mock_exec),
        patch("butlers.core.runtimes.codex._MCP_RETRY_DELAYS", (0, 0)),
    ):
        with pytest.raises(RuntimeError, match=r"MCP tool discovery failed after 3 attempts"):
            await adapter.invoke(
                prompt="route this",
                system_prompt="",
                mcp_servers=_MCP_SERVERS,
                env={},
            )

    info = adapter.last_process_info
    assert info["result_source"] == "first"
    assert info["attempt_count"] == 3
    assert info["retry_attempted"] is True
    assert info["retry_succeeded"] is False


async def test_no_retry_sets_attempt_count_one():
    """Single-attempt execution (no MCP flake): attempt_count=1, no retry fields."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    async def _mock_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 42
        proc.communicate = AsyncMock(return_value=(_make_mcp_stdout(), b""))
        return proc

    with patch(_EXEC, side_effect=_mock_exec):
        await adapter.invoke(
            prompt="route this",
            system_prompt="",
            mcp_servers=_MCP_SERVERS,
            env={},
        )

    info = adapter.last_process_info
    assert info["attempt_count"] == 1
    assert info.get("retry_attempted") is None
    assert info.get("result_source") is None
