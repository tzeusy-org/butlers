"""Tests for OpenCodeAdapter.

Covers unique OpenCode behaviors not in test_adapter_contract.py:
- parse_system_prompt_file: OPENCODE.md priority, AGENTS.md fallback
- build_config_file: remote entry structure, validation/skip logic
- _parse_opencode_output: unique event types (text variants, tool call formats, usage)
- _extract_opencode_tool_call: format normalization
- _looks_like_tool_call_event: heuristic detection
- _extract_usage: token extraction and format mapping
- _find_opencode_binary: PATH discovery
- invoke(): OPENCODE_CONFIG injection, model flag, error paths
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes.opencode import (
    _MAX_PROMPT_ARG_BYTES,
    _PROMPT_ATTACHMENT_MESSAGE,
    OpenCodeAdapter,
    _extract_opencode_tool_call,
    _extract_usage,
    _find_opencode_binary,
    _looks_like_tool_call_event,
    _parse_opencode_output,
    _select_error_detail,
)

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.opencode.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# parse_system_prompt_file — OPENCODE.md priority, AGENTS.md fallback
# ---------------------------------------------------------------------------


def test_parse_system_prompt(tmp_path: Path):
    """parse_system_prompt_file: OPENCODE.md > AGENTS.md; empty OPENCODE.md falls to AGENTS.md."""
    adapter = OpenCodeAdapter()
    # OPENCODE.md takes priority
    (tmp_path / "OPENCODE.md").write_text("OpenCode instructions.")
    (tmp_path / "AGENTS.md").write_text("Agent fallback.")
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == "OpenCode instructions."
    # blank OPENCODE.md falls back to AGENTS.md
    (tmp_path / "OPENCODE.md").write_text("   \n  ")
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == "Agent fallback."
    # AGENTS.md only
    (tmp_path / "OPENCODE.md").unlink()
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == "Agent fallback."
    # nothing → empty
    (tmp_path / "AGENTS.md").unlink()
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == ""
    # CLAUDE.md ignored
    (tmp_path / "CLAUDE.md").write_text("Claude only.")
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == ""


# ---------------------------------------------------------------------------
# build_config_file — remote entry structure and validation
# ---------------------------------------------------------------------------


def test_build_config_file(tmp_path: Path, caplog):
    """build_config_file(): valid server written; invalid/missing-url servers skipped."""
    adapter = OpenCodeAdapter()
    bad_servers = [
        {"bad": "not-a-dict"},
        {"no-url": {"transport": "remote"}},
        {"empty-url": {"url": "   "}},
    ]
    for bad in bad_servers:
        mcp_servers = {"valid": {"url": "http://localhost:9100/mcp"}, **bad}
        with caplog.at_level(logging.WARNING):
            config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
        data = json.loads(config_path.read_text())
        assert "valid" in data["mcp"]
        for bad_name in bad:
            assert bad_name not in data["mcp"]
    # Check structure for a valid server
    mcp_servers = {"my-butler": {"url": "http://localhost:9100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    assert config_path == tmp_path / "opencode.jsonc"
    data = json.loads(config_path.read_text())
    assert data["permission"] == {}
    entry = data["mcp"]["my-butler"]
    assert entry["type"] == "remote" and entry["url"] == "http://localhost:9100/mcp"
    assert entry["enabled"] is True


# ---------------------------------------------------------------------------
# _parse_opencode_output — unique event types and tool call formats
# ---------------------------------------------------------------------------


def test_parse_opencode_unique_events():
    """OpenCode-specific event types: text variants, result, item.completed, tool call types."""
    # text event variants
    for event, expected in [
        ({"type": "text", "text": "Hello"}, "Hello"),
        ({"type": "text", "content": "Content"}, "Content"),
        ({"type": "text", "delta": "Delta"}, "Delta"),
        ({"type": "result", "result": "Task complete"}, "Task complete"),
        ({"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}}, "hi"),
        ({"type": "assistant", "content": "Direct content"}, "Direct content"),
    ]:
        text, _, _ = _parse_opencode_output(json.dumps(event), "", 0)
        assert text == expected, f"Failed for event {event}"

    # tool call event types
    for event_type in ["tool_use", "tool_call", "function_call", "mcp_tool_call"]:
        event = {"type": event_type, "id": "t1", "name": "do_thing", "input": {"k": "v"}}
        _, tool_calls, _ = _parse_opencode_output(json.dumps(event), "", 0)
        assert len(tool_calls) == 1 and tool_calls[0]["name"] == "do_thing"


# ---------------------------------------------------------------------------
# _looks_like_tool_call_event and _extract_opencode_tool_call
# ---------------------------------------------------------------------------


def test_looks_like_tool_call_event():
    """_looks_like_tool_call_event correctly identifies tool call objects."""
    positives = [
        {"type": "tool_use"},
        {"type": "function_call"},
        {"type": "mcp_tool_call"},
        {"name": "my_tool", "input": {"a": 1}},
        {"name": "my_tool", "arguments": {"a": 1}},
        {"function": {"name": "my_fn", "arguments": {"x": 1}}},
    ]
    negatives = [{"type": "text", "text": "hello"}, {"name": "", "input": {"a": 1}}]
    for obj in positives:
        assert _looks_like_tool_call_event(obj) is True, f"Expected True for {obj}"
    for obj in negatives:
        assert _looks_like_tool_call_event(obj) is False, f"Expected False for {obj}"


def test_extract_opencode_tool_call_formats():
    """_extract_opencode_tool_call normalizes standard, function, call, and string-arg formats."""
    cases = [
        ({"id": "t1", "name": "do_thing", "input": {"k": "v"}}, "do_thing", {"k": "v"}),
        (
            {"id": "fc1", "function": {"name": "my_tool", "arguments": {"x": 1}}},
            "my_tool",
            {"x": 1},
        ),
        (
            {"id": "mcp_1", "call": {"name": "router", "arguments": {"b": "g"}}},
            "router",
            {"b": "g"},
        ),
        ({"id": "t3", "name": "fn", "arguments": '{"k":"v"}'}, "fn", {"k": "v"}),
    ]
    for event, expected_name, expected_input in cases:
        tc = _extract_opencode_tool_call(event)
        assert tc["name"] == expected_name and tc["input"] == expected_input


# ---------------------------------------------------------------------------
# _extract_usage, _find_opencode_binary, invoke()
# ---------------------------------------------------------------------------


def test_extract_usage_and_find_binary():
    """_extract_usage handles all token formats and non-dict input; binary raises when missing."""
    cases = [
        ({"input_tokens": 100, "output_tokens": 50}, {"input_tokens": 100, "output_tokens": 50}),
        (
            {"usage": {"input_tokens": 200, "output_tokens": 80}},
            {"input_tokens": 200, "output_tokens": 80},
        ),
        (
            {"prompt_tokens": 150, "completion_tokens": 60},
            {"input_tokens": 150, "output_tokens": 60},
        ),
        ({"type": "text", "text": "hello"}, None),
        ({"input_tokens": "many", "output_tokens": None}, None),
        (None, None),
        ("string", None),
    ]
    for event, expected in cases:
        assert _extract_usage(event) == expected, f"Failed for {event}"  # type: ignore[arg-type]

    with patch("butlers.core.runtimes.opencode.shutil.which", return_value="/usr/bin/opencode"):
        assert _find_opencode_binary() == "/usr/bin/opencode"
    with patch("butlers.core.runtimes.opencode.shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="OpenCode CLI binary not found"):
            _find_opencode_binary()


_MIGRATION_NOISE = (
    "Performing one time database migration, may take a few minutes...\n"
    "sqlite-migration:done\n"
    "Database migration complete.\n"
)


@pytest.mark.parametrize(
    "stderr,stdout,exit_code,expected",
    [
        # Structured stdout error beats benign first-run migration banner on stderr.
        pytest.param(
            _MIGRATION_NOISE,
            "\n".join(
                [
                    json.dumps({"type": "session.started", "id": "session-123"}),
                    json.dumps(
                        {
                            "type": "error",
                            "message": "AuthenticationError: provider rejected the request",
                        }
                    ),
                ]
            ),
            1,
            "AuthenticationError: provider rejected the request",
            id="stdout-error-over-migration-noise",
        ),
        # Code-only structured stdout error → numeric code.
        pytest.param(
            "plain stderr fallback",
            json.dumps({"type": "result", "code": 429}),
            1,
            "429",
            id="code-only-stdout-error",
        ),
        # Nested numeric code is still surfaced.
        pytest.param(
            "plain stderr fallback",
            json.dumps({"type": "error", "error": {"code": 401}}),
            1,
            "401",
            id="nested-numeric-code",
        ),
        # OpenCode provider failures wrap the useful message under error.data.
        pytest.param(
            "",
            json.dumps(
                {
                    "type": "error",
                    "timestamp": 123456,
                    "sessionID": "session-123",
                    "error": {
                        "name": "APIError",
                        "data": {"message": "provider overloaded, retry after 30s"},
                    },
                }
            ),
            1,
            "APIError: provider overloaded, retry after 30s",
            id="nested-apierror-data-message",
        ),
        # Balance errors use the same nested provider shape and retain the provider name.
        pytest.param(
            "",
            json.dumps(
                {
                    "type": "error",
                    "timestamp": "2026-06-21T00:00:00Z",
                    "sessionID": "session-123",
                    "error": {
                        "name": "APIError",
                        "data": {"message": "Insufficient balance"},
                    },
                }
            ),
            1,
            "APIError: Insufficient balance",
            id="nested-apierror-balance-message",
        ),
        # Avoid duplicate prefixes when the nested payload is already named.
        pytest.param(
            "",
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "name": "APIError",
                        "data": {"message": "APIError: provider rejected the request"},
                    },
                }
            ),
            1,
            "APIError: provider rejected the request",
            id="nested-apierror-data-message-with-prefix",
        ),
        pytest.param(
            "",
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "name": "APIError",
                        "message": "APIError",
                        "data": {"message": "provider request failed upstream"},
                    },
                }
            ),
            1,
            "APIError: provider request failed upstream",
            id="nested-apierror-skips-generic-message",
        ),
        pytest.param(
            "plain stderr fallback",
            json.dumps(
                {
                    "type": "result",
                    "data": {"message": "provider request failed upstream"},
                }
            ),
            1,
            "provider request failed upstream",
            id="data-only-stdout-error",
        ),
        # Scalar diagnostics under structured payloads are preserved.
        pytest.param(
            "",
            json.dumps({"type": "error", "error": {"name": "APIError", "data": {"status": 503}}}),
            1,
            "APIError: 503",
            id="nested-apierror-scalar-status",
        ),
        pytest.param(
            "",
            json.dumps({"type": "error", "name": 402, "detail": "payment required"}),
            1,
            "402: payment required",
            id="scalar-name-detail",
        ),
        # OpenCode APIError payloads can put the useful message beside data.
        pytest.param(
            "",
            json.dumps({"type": "error", "error": {"name": "APIError", "message": "quota hit"}}),
            1,
            "APIError: quota hit",
            id="nested-apierror-message-fallback",
        ),
        # Billing rejection messages under APIError.data are preserved for failover matching.
        pytest.param(
            "",
            json.dumps(
                {
                    "type": "error",
                    "timestamp": "2026-06-21T10:42:00.000Z",
                    "sessionID": "session-123",
                    "error": {
                        "name": "APIError",
                        "data": {"message": "Insufficient balance. Manage your balance settings."},
                    },
                }
            ),
            1,
            "APIError: Insufficient balance. Manage your balance settings.",
            id="nested-apierror-insufficient-balance-data-message",
        ),
        # Migration banner alone is not a useful diagnostic → fall back to exit code.
        pytest.param(_MIGRATION_NOISE, "", 1, "exit code 1", id="migration-noise-only"),
    ],
)
def test_select_error_detail_branches(stderr, stdout, exit_code, expected):
    """_select_error_detail picks the actionable headline and ignores migration noise."""
    assert _select_error_detail(stderr, stdout, exit_code) == expected


async def test_invoke_success_and_config():
    """invoke() calls subprocess with run subcommand; injects OPENCODE_CONFIG; forwards --model."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    output_lines = "\n".join(
        [
            json.dumps({"type": "text", "text": "Task done."}),
            json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 20}}
            ),
        ]
    )
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output_lines.encode(), b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        result_text, _, usage = await adapter.invoke(
            prompt="do something",
            system_prompt="you are helpful",
            mcp_servers={"test": {"url": "http://localhost:9100/mcp"}},
            env={"ANTHROPIC_API_KEY": "sk-test"},
        )
    assert result_text == "Task done." and usage == {"input_tokens": 10, "output_tokens": 20}
    cmd = mock_sub.call_args[0]
    assert cmd[0] == "/usr/bin/opencode" and cmd[1] == "run" and "--format" in cmd
    assert "do something" in cmd
    assert "--file" not in cmd
    env = mock_sub.call_args[1]["env"]
    assert "OPENCODE_CONFIG" in env and env["OPENCODE_CONFIG"].endswith("opencode.jsonc")

    # --model flag present/absent
    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="run",
            system_prompt="",
            mcp_servers={},
            env={},
            model="anthropic/claude-sonnet-4-5",
        )
    cmd = mock_sub.call_args[0]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "anthropic/claude-sonnet-4-5"

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(prompt="run", system_prompt="", mcp_servers={}, env={}, model=None)
    assert "--model" not in mock_sub.call_args[0]


async def test_invoke_spills_large_prompt_to_attachment():
    """Large prompts are attached by file so execve argv limits cannot reject the spawn."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    large_prompt = "x" * (_MAX_PROMPT_ARG_BYTES + 1)
    output = json.dumps({"type": "text", "text": "Task done."})
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output.encode(), b""))
    mock_proc.returncode = 0

    captured: dict[str, object] = {}

    async def fake_exec(*cmd, **kwargs):
        prompt_file = Path(cmd[cmd.index("--file") + 1])
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        captured["prompt_file_text"] = prompt_file.read_text(encoding="utf-8")
        return mock_proc

    with patch(_EXEC, side_effect=fake_exec):
        result_text, _, _ = await adapter.invoke(
            prompt=large_prompt,
            system_prompt="",
            mcp_servers={},
            env={},
        )

    cmd = captured["cmd"]
    assert result_text == "Task done."
    assert isinstance(cmd, tuple)
    assert large_prompt not in cmd
    assert "--file" in cmd
    assert _PROMPT_ATTACHMENT_MESSAGE in cmd
    assert cmd.index(_PROMPT_ATTACHMENT_MESSAGE) < cmd.index("--file")
    assert captured["prompt_file_text"] == large_prompt


async def test_invoke_marks_retry_failed_when_second_attempt_exits_zero_with_error_stderr():
    """invoke() preserves retry provenance when the retry hits an exit-0 stderr failure."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    migration_proc = AsyncMock()
    migration_proc.pid = 100
    migration_proc.communicate = AsyncMock(
        return_value=(
            b"",
            b"\n".join(
                [
                    b"Performing one time database migration, may take a few minutes...",
                    b"sqlite-migration:done",
                    b"Database migration complete.",
                ]
            ),
        )
    )
    migration_proc.returncode = 1

    error_proc = AsyncMock()
    error_proc.pid = 101
    error_proc.communicate = AsyncMock(
        return_value=(b"", b"ProviderModelNotFoundError: model unavailable")
    )
    error_proc.returncode = 0

    with patch(_EXEC, side_effect=[migration_proc, error_proc]):
        with pytest.raises(RuntimeError, match="ProviderModelNotFoundError"):
            await adapter.invoke(
                prompt="do something",
                system_prompt="",
                mcp_servers={},
                env={},
            )

    assert adapter.last_process_info is not None
    assert adapter.last_process_info["retry_attempted"] is True
    assert adapter.last_process_info["retry_succeeded"] is False
    assert adapter.last_process_info["attempt_index"] == 1


async def test_invoke_marks_retry_failed_when_second_attempt_exits_zero_empty():
    """invoke() does not report retry success until retry output validates."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    migration_proc = AsyncMock()
    migration_proc.pid = 100
    migration_proc.communicate = AsyncMock(
        return_value=(
            b"",
            b"\n".join(
                [
                    b"Performing one time database migration, may take a few minutes...",
                    b"sqlite-migration:done",
                    b"Database migration complete.",
                ]
            ),
        )
    )
    migration_proc.returncode = 1

    empty_proc = AsyncMock()
    empty_proc.pid = 101
    empty_proc.communicate = AsyncMock(return_value=(b"", b""))
    empty_proc.returncode = 0

    with patch(_EXEC, side_effect=[migration_proc, empty_proc]):
        with pytest.raises(RuntimeError, match="OpenCode CLI returned no response"):
            await adapter.invoke(
                prompt="do something",
                system_prompt="",
                mcp_servers={},
                env={},
            )

    assert adapter.last_process_info is not None
    assert adapter.last_process_info["retry_attempted"] is True
    assert adapter.last_process_info["retry_succeeded"] is False
    assert adapter.last_process_info["attempt_index"] == 1
    assert adapter.last_process_info.get("result_source") != "retry"


async def test_invoke_error_paths(caplog):
    """invoke() raises RuntimeError on non-zero exit; TimeoutError and kill on timeout."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    mock_proc = AsyncMock()

    mock_proc.communicate = AsyncMock(return_value=(b"", b"rate limit exceeded"))
    mock_proc.returncode = 1
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError, match="rate limit exceeded"):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    mock_proc.communicate = AsyncMock(
        return_value=(
            json.dumps(
                {"type": "error", "message": "AuthenticationError: login required"}
            ).encode(),
            b"Performing one time database migration, may take a few minutes...\n"
            b"sqlite-migration:done\n"
            b"Database migration complete.\n",
        )
    )
    mock_proc.returncode = 1
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError) as exc_info:
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})
    assert "AuthenticationError: login required" in str(exc_info.value)
    assert "sqlite-migration:done" not in str(exc_info.value)

    mock_proc.communicate = AsyncMock(
        return_value=(
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "name": "APIError",
                        "data": {"message": "Insufficient balance"},
                    },
                }
            ).encode(),
            b"",
        )
    )
    mock_proc.returncode = 1
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError, match="Insufficient balance"):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})
    assert adapter.last_process_info is not None
    assert adapter.last_process_info["error_detail"] == "APIError: Insufficient balance"
    assert adapter.last_process_info["is_pre_tool_call"] is True

    mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()
    with patch(_EXEC, return_value=mock_proc), caplog.at_level(logging.WARNING):
        with pytest.raises(TimeoutError, match="OpenCode CLI timed out"):
            await adapter.invoke(prompt="slow", system_prompt="", mcp_servers={}, env={}, timeout=1)
    mock_proc.kill.assert_called_once()
    timeout_records = [r for r in caplog.records if "OpenCode CLI timed out after 1s" in r.message]
    assert timeout_records and all(r.levelno == logging.WARNING for r in timeout_records)


async def test_invoke_empty_exit_zero_raises_pre_tool_call_error(caplog):
    """invoke() rejects exit-0 runs that produce no parseable response or diagnostics."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc), caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="OpenCode CLI returned no response"):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    no_response_records = [
        r for r in caplog.records if "OpenCode CLI returned no response" in r.message
    ]
    assert no_response_records
    assert all(r.levelno == logging.WARNING for r in no_response_records)

    info = adapter.last_process_info
    assert info is not None
    assert info.get("exit_code") == 0
    assert info.get("stderr") == ""
    assert info.get("is_pre_tool_call") is True
    assert "returned no response" in info.get("error_detail", "")


async def test_invoke_empty_exit_zero_with_stderr_noise_raises_pre_tool_call_error():
    """invoke() rejects empty parse results even when stderr contains non-fatal noise."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"warning: newer version available"))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError, match="OpenCode CLI returned no response"):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    info = adapter.last_process_info
    assert info is not None
    assert info.get("exit_code") == 0
    assert info.get("stderr") == "warning: newer version available"
    assert info.get("is_pre_tool_call") is True
    assert "warning: newer version available" in info.get("error_detail", "")


async def test_invoke_retries_once_after_sqlite_migration_banner():
    """A first-run OpenCode SQLite migration-only exit should retry once."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    migration_stderr = "\n".join(
        [
            "Performing one time database migration, may take a few minutes...",
            "sqlite-migration:done",
            "Database migration complete.",
        ]
    )
    success_stdout = json.dumps({"type": "text", "text": "Recovered."})

    first_proc = AsyncMock()
    first_proc.communicate = AsyncMock(return_value=(b"", migration_stderr.encode()))
    first_proc.returncode = 1
    first_proc.pid = 101

    second_proc = AsyncMock()
    second_proc.communicate = AsyncMock(return_value=(success_stdout.encode(), b""))
    second_proc.returncode = 0
    second_proc.pid = 102

    procs = [first_proc, second_proc]

    async def _mock_exec(*_args, **_kwargs):
        return procs.pop(0)

    with patch(_EXEC, side_effect=_mock_exec) as mock_sub:
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="run", system_prompt="", mcp_servers={}, env={}
        )

    assert mock_sub.call_count == 2
    assert result_text == "Recovered."
    assert tool_calls == []
    assert usage is None
    info = adapter.last_process_info
    assert info is not None
    assert info["exit_code"] == 0
    assert info["attempt_count"] == 2
    assert info["retry_attempted"] is True
    assert info["retry_succeeded"] is True
    assert info["retry_reason"] == "opencode_sqlite_migration"
    assert info["result_source"] == "retry"


async def test_invoke_does_not_retry_migration_banner_with_actionable_error():
    """Migration chatter plus another error line is not treated as benign bootstrap."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    stderr = "\n".join(
        [
            "Performing one time database migration, may take a few minutes...",
            "sqlite-migration:done",
            "Database migration complete.",
            "AuthenticationError: missing credentials",
        ]
    )
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", stderr.encode()))
    mock_proc.returncode = 1
    mock_proc.pid = 103

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        with pytest.raises(RuntimeError, match="AuthenticationError"):
            await adapter.invoke(prompt="run", system_prompt="", mcp_servers={}, env={})

    assert mock_sub.call_count == 1


async def test_invoke_does_not_retry_migration_banner_with_stdout():
    """Migration-only stderr is not retried when stdout contains process output."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    stderr = "\n".join(
        [
            "Performing one time database migration, may take a few minutes...",
            "sqlite-migration:done",
            "Database migration complete.",
        ]
    )
    stdout = json.dumps({"type": "error", "message": "Provider rejected the request"})
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    mock_proc.returncode = 1
    mock_proc.pid = 104

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        with pytest.raises(RuntimeError, match="Provider rejected the request"):
            await adapter.invoke(prompt="run", system_prompt="", mcp_servers={}, env={})

    assert mock_sub.call_count == 1


async def test_invoke_does_not_retry_partial_migration_banner():
    """Partial migration chatter is not enough to classify a failed exit as benign."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    stderr = "\n".join(
        [
            "Performing one time database migration, may take a few minutes...",
            "sqlite-migration:done",
        ]
    )
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", stderr.encode()))
    mock_proc.returncode = 1
    mock_proc.pid = 105

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        with pytest.raises(RuntimeError, match="exit code 1") as exc_info:
            await adapter.invoke(prompt="run", system_prompt="", mcp_servers={}, env={})

    assert mock_sub.call_count == 1
    assert "sqlite-migration:done" not in str(exc_info.value)


async def test_invoke_records_retry_failure_when_second_attempt_has_fatal_stderr():
    """A retry that exits 0 with fatal stderr still records failed retry provenance."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    migration_stderr = "\n".join(
        [
            "Performing one time database migration, may take a few minutes...",
            "sqlite-migration:done",
            "Database migration complete.",
        ]
    )

    first_proc = AsyncMock()
    first_proc.communicate = AsyncMock(return_value=(b"", migration_stderr.encode()))
    first_proc.returncode = 1
    first_proc.pid = 105

    second_proc = AsyncMock()
    second_proc.communicate = AsyncMock(return_value=(b"", b"AuthenticationError: missing key"))
    second_proc.returncode = 0
    second_proc.pid = 106

    procs = [first_proc, second_proc]

    async def _mock_exec(*_args, **_kwargs):
        return procs.pop(0)

    with patch(_EXEC, side_effect=_mock_exec) as mock_sub:
        with pytest.raises(RuntimeError, match="AuthenticationError"):
            await adapter.invoke(prompt="run", system_prompt="", mcp_servers={}, env={})

    assert mock_sub.call_count == 2
    info = adapter.last_process_info
    assert info is not None
    assert info["exit_code"] == 0
    assert info["attempt_count"] == 2
    assert info["retry_attempted"] is True
    assert info["retry_succeeded"] is False
    assert info["retry_reason"] == "opencode_sqlite_migration"
    assert info["result_source"] == "retry"
