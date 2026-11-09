"""Tests for GeminiAdapter — Gemini CLI runtime adapter.

Unique behaviors not in test_adapter_contract.py:
- Binary discovery (_find_gemini_binary)
- _filter_env preserves runtime keys
- parse_system_prompt_file: GEMINI.md priority, AGENTS.md fallback
- build_config_file writes gemini_mcp.json
- _parse_gemini_output: functionCall formats
- _extract_tool_call: Gemini-specific functionCall container
- invoke(): CLI flags, error paths
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import GeminiAdapter
from butlers.core.runtimes.gemini import (
    _extract_tool_call,
    _filter_env,
    _find_gemini_binary,
    _parse_gemini_output,
)

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec"


def test_binary_filter_env_and_system_prompt(tmp_path: Path):
    """Binary discovery; _filter_env preserves runtime keys; system prompt: GEMINI.md > AGENTS.md."""
    with patch("butlers.core.runtimes.gemini.shutil.which", return_value="/usr/bin/gemini"):
        assert _find_gemini_binary() == "/usr/bin/gemini"
    with patch("butlers.core.runtimes.gemini.shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="Gemini CLI binary not found"):
            _find_gemini_binary()

    env = {"GOOGLE_API_KEY": "gk-test", "ANTHROPIC_API_KEY": "sk-ant", "PATH": "/usr/bin"}
    assert _filter_env(env) == env and _filter_env({}) == {}

    adapter = GeminiAdapter()
    (tmp_path / "GEMINI.md").write_text("Gemini instructions.")
    (tmp_path / "AGENTS.md").write_text("Agent fallback.")
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == "Gemini instructions."
    (tmp_path / "GEMINI.md").write_text("   \n  ")
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == "Agent fallback."
    (tmp_path / "GEMINI.md").unlink()
    assert adapter.parse_system_prompt_file(config_dir=tmp_path) == "Agent fallback."


def test_build_config_file_and_tool_call_formats(tmp_path: Path):
    """build_config_file() writes gemini_mcp.json; both args/arguments functionCall keys work;
    only ``mcp_``-prefixed names (the CLI's MCP naming) escape the ``builtin`` tag."""
    config_path = GeminiAdapter().build_config_file(
        mcp_servers={"my-butler": {"url": "http://localhost:9100/mcp"}}, tmp_dir=tmp_path
    )
    assert config_path == tmp_path / "gemini_mcp.json"
    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["my-butler"]["url"] == "http://localhost:9100/mcp"

    # functionCall extraction
    line = json.dumps(
        {
            "type": "functionCall",
            "id": "fc1",
            "functionCall": {"name": "mcp_my-butler_my_tool", "args": {"arg1": "val1"}},
        }
    )
    _, tool_calls = _parse_gemini_output(line, "")
    assert tool_calls == [{"id": "fc1", "name": "mcp_my-butler_my_tool", "input": {"arg1": "val1"}}]

    # args and arguments both supported
    tc1 = _extract_tool_call({"id": "fc1", "functionCall": {"name": "tool_a", "args": {"a": 1}}})
    assert tc1["name"] == "tool_a" and tc1["input"] == {"a": 1} and tc1["builtin"] is True
    tc2 = _extract_tool_call(
        {"id": "fc2", "functionCall": {"name": "tool_b", "arguments": {"b": 2}}}
    )
    assert tc2["name"] == "tool_b" and tc2["input"] == {"b": 2}


async def test_invoke():
    """REQ-core-spawner-017: required flags, text/tools and failed terminal outcome."""
    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(
        return_value=(json.dumps({"type": "result", "result": "Task done."}).encode(), b"")
    )
    staged: dict[str, Any] = {}

    def _spawn(*_cmd: str, **kwargs: Any) -> AsyncMock:
        env = kwargs["env"]
        staged["system_md"] = Path(env["GEMINI_SYSTEM_MD"]).read_text(encoding="utf-8")
        staged["settings"] = json.loads(Path(env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]).read_text())
        return mock_proc

    with patch(_EXEC, side_effect=_spawn) as mock_sub:
        result_text, _, _ = await adapter.invoke(
            prompt="do something",
            system_prompt="you are helpful — ${AvailableTools} ${state_get_ToolName} ${HOME}",
            mcp_servers={"test": {"url": "http://localhost:9100/mcp"}},
            env={"GOOGLE_API_KEY": "gk-test"},
            max_turns=7,
            model="gemini-2.5-pro",
        )
    assert result_text == "Task done."
    assert mock_sub.call_args.kwargs["env"]["GOOGLE_API_KEY"] == "gk-test"
    # gemini-cli 0.66.0 has a strict parser and no system-prompt flag; the prompt is staged
    # as a utf-8 GEMINI_SYSTEM_MD file. The CLI substitutes ${AvailableTools}-style tokens
    # in that file, so the adapter breaks them with a zero-width space; others pass intact.
    assert staged["system_md"] == (
        "you are helpful — $​{AvailableTools} $​{state_get_ToolName} ${HOME}"
    )
    # The CLI has no --max-turns flag; model.maxSessionTurns in the staged system
    # settings is its turn limit (exit 53 when reached).
    assert staged["settings"]["model"] == {"maxSessionTurns": 7}
    assert staged["settings"]["mcpServers"]["test"]["httpUrl"] == "http://localhost:9100/mcp"
    cmd = list(mock_sub.call_args[0])
    assert cmd[:2] == ["/usr/bin/gemini", "--sandbox=false"]
    # Headless runs exit 55 in an untrusted folder; stream-json carries usage and the
    # terminal result; -m makes the catalog model_id take effect.
    assert "--skip-trust" in cmd
    assert cmd[cmd.index("-o") + 1] == "stream-json"
    assert cmd[cmd.index("-m") + 1] == "gemini-2.5-pro"
    assert "--system-prompt" not in cmd and cmd[cmd.index("--prompt") + 1] == "do something"

    # functionCall output
    output_lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "functionCall",
                    "id": "fc1",
                    "functionCall": {"name": "state_get", "args": {"key": "bar"}},
                }
            ),
            json.dumps({"type": "result", "result": "Complete"}),
        ]
    )
    mock_proc.communicate = AsyncMock(return_value=(output_lines.encode(), b""))
    with patch(_EXEC, return_value=mock_proc) as empty_sub:
        result_text2, tool_calls, _ = await adapter.invoke(
            prompt="use tools", system_prompt="", mcp_servers={}, env={}
        )
    assert set(empty_sub.call_args.kwargs["env"]) == {"GEMINI_CLI_SYSTEM_SETTINGS_PATH"}
    assert (
        result_text2 == "Complete" and len(tool_calls) == 1 and tool_calls[0]["name"] == "state_get"
    )
    # Pinned stream metadata, synthetic conformance only. Prompt is inclusive;
    # the model label can be request fallback and is never promoted to Served.
    from butlers.core.runtimes.served_identity import TerminalResultError

    payload = {
        "type": "result",
        "status": "success",
        "stats": {
            "models": {
                "gemini-2.5-pro": {
                    "input_tokens": 105,
                    "cached": 100,
                    "input": 5,
                    "output_tokens": 3,
                }
            },
            "input_tokens": 105,
            "cached": 100,
            "input": 5,
            "output_tokens": 3,
        },
    }
    mock_proc.communicate = AsyncMock(return_value=(json.dumps(payload).encode(), b""))
    with patch(_EXEC, return_value=mock_proc):
        _, _, usage = await adapter.invoke(
            prompt="synthetic", system_prompt="", mcp_servers={}, env={}
        )
    assert usage["input_tokens"] == 5 and usage["cache_read_input_tokens"] == 100
    evidence = adapter.last_process_info["served"]["executions"][0]
    assert evidence["served_models"] == []
    assert evidence["reported_models"][0]["provenance"] == "provider_or_request_fallback"
    assert evidence["cli_version_reported"] is None
    payload["status"] = "error"
    mock_proc.communicate = AsyncMock(return_value=(json.dumps(payload).encode(), b""))
    with patch(_EXEC, return_value=mock_proc), pytest.raises(TerminalResultError):
        await adapter.invoke(prompt="synthetic", system_prompt="", mcp_servers={}, env={})

    # Pinned 0.66.0 emits these constructor names in terminal error.type.
    # Synthetic adapter conformance, not a provider recording or auth decision.
    for raw_type, expected in (
        ("FatalTurnLimitedError", "max_turns"),
        ("FatalAuthenticationError", "provider_auth"),
    ):
        payload["error"] = {"type": raw_type, "message": "synthetic-private-error-marker"}
        mock_proc.communicate = AsyncMock(return_value=(json.dumps(payload).encode(), b""))
        with patch(_EXEC, return_value=mock_proc), pytest.raises(TerminalResultError) as caught:
            await adapter.invoke(prompt="synthetic", system_prompt="", mcp_servers={}, env={})
        terminal = caught.value.served["executions"][0]
        assert terminal["error"]["code"] == expected
        assert terminal["completion_state"] == "error" and terminal["error"]["is_error"] is True
        assert terminal["aggregate_usage"]["input_tokens"] == 5
        assert terminal["aggregate_usage"]["cache_read_input_tokens"] == 100
        assert raw_type not in json.dumps(caught.value.served)
        assert "synthetic-private-error-marker" not in json.dumps(caught.value.served)

    # Absent, unknown and malformed types remain the existing generic category.
    # In particular, lists/dicts/bools never become dict keys or stored labels.
    for raw_error in (
        None,
        True,
        "synthetic-private-error-marker",
        ["synthetic-private-error-marker"],
        {},
        {"type": "UnknownSyntheticType", "message": "synthetic-private-error-marker"},
        {"type": None},
        {"type": True},
        {"type": 53},
        {"type": []},
        {"type": {}},
    ):
        payload["error"] = raw_error
        mock_proc.communicate = AsyncMock(return_value=(json.dumps(payload).encode(), b""))
        with patch(_EXEC, return_value=mock_proc), pytest.raises(TerminalResultError) as caught:
            await adapter.invoke(prompt="synthetic", system_prompt="", mcp_servers={}, env={})
        terminal = caught.value.served["executions"][0]
        assert terminal["completion_state"] == "error"
        assert terminal["error"]["code"] == "execution_error"
        assert "UnknownSyntheticType" not in json.dumps(caught.value.served)
        assert "synthetic-private-error-marker" not in json.dumps(caught.value.served)

    # Informational warnings and even a typed error object do not override an
    # explicit successful result. A later fatal terminal still dominates it.
    payload["status"] = "success"
    payload["error"] = {
        "type": "FatalAuthenticationError",
        "message": "synthetic-private-error-marker",
    }
    success = json.dumps(payload)
    warning = json.dumps(
        {"type": "error", "severity": "warning", "message": "synthetic-private-error-marker"}
    )
    mock_proc.communicate = AsyncMock(return_value=((warning + "\n" + success).encode(), b""))
    with patch(_EXEC, return_value=mock_proc):
        _, _, usage = await adapter.invoke(
            prompt="synthetic", system_prompt="", mcp_servers={}, env={}
        )
    evidence = adapter.last_process_info["served"]
    assert evidence["executions"][0]["completion_state"] == "success"
    assert evidence["executions"][0]["error"]["code"] is None
    assert usage["input_tokens"] == 5 and usage["cache_read_input_tokens"] == 100
    assert "synthetic-private-error-marker" not in json.dumps(evidence)
    payload["status"] = "error"
    mock_proc.communicate = AsyncMock(
        return_value=((success + "\n" + json.dumps(payload)).encode(), b"")
    )
    with patch(_EXEC, return_value=mock_proc), pytest.raises(TerminalResultError) as caught:
        await adapter.invoke(prompt="synthetic", system_prompt="", mcp_servers={}, env={})
    assert caught.value.served["executions"][0]["error"]["code"] == "provider_auth"
    assert caught.value.served["executions"][0]["completion_state"] == "error"


@pytest.mark.parametrize(
    "returncode,stderr,stdout,expected_fragment",
    [
        (1, "authentication failed", "", "authentication failed"),
        (401, "unauthorized", "", "unauthorized"),
        (429, "too many requests", "", "too many requests"),
        (1, "", "Error: connection refused", "connection refused"),
        (1, "", "", "exit code 1"),
    ],
)
async def test_invoke_nonzero_exit_raises_runtime_error(
    returncode: int, stderr: str, stdout: str, expected_fragment: str
) -> None:
    """Non-zero Gemini exit codes raise RuntimeError so the failover classifier can act.

    Before the fix, non-zero exits were absorbed as result text.  The classifier
    was never invoked, so provider/auth/rate-limit failures silently produced
    'Error: ...' output instead of triggering same-tier failover.
    """
    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")
    mock_proc = AsyncMock()
    mock_proc.returncode = returncode
    mock_proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError) as exc_info:
            await adapter.invoke(
                prompt="do something",
                system_prompt="",
                mcp_servers={},
                env={},
            )
    assert expected_fragment in str(exc_info.value)
    assert str(returncode) in str(exc_info.value)


async def test_invoke_nonzero_exit_records_process_info() -> None:
    """Non-zero exit populates last_process_info before raising RuntimeError."""
    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")
    mock_proc = AsyncMock()
    mock_proc.pid = 12345
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"auth error"))

    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    info = adapter.last_process_info
    assert info is not None
    assert info["exit_code"] == 1
    assert info["runtime_type"] == "gemini"
    assert "auth error" in info["stderr"]


async def test_invoke_nonzero_terminal_result_keeps_usage_and_tool_evidence() -> None:
    """Pinned 0.66.0 stream-json emits ``result``/``status: error`` then exits non-zero.

    Its ``handleError``/``handleMaxTurnsExceededError`` always ``process.exit`` with the
    error's exit code (``FatalTurnLimitedError`` is 53) after emitting the envelope, so
    the exit-zero path never sees it. The non-zero raise keeps the attempt's usage and
    tool calls; the classified message stays the plain exit text. Shapes are synthetic,
    mirroring that CLI's ``tool_use`` (tool_name/tool_id/parameters) and result events.
    """
    from butlers.core.runtimes.served_identity import TerminalResultError

    stats = {"input_tokens": 105, "cached": 100, "input": 5, "output_tokens": 3}
    result = {
        "type": "result",
        "status": "error",
        "error": {"type": "FatalTurnLimitedError", "message": "synthetic"},
        "stats": stats,
    }
    tool_use = {
        "type": "tool_use",
        "tool_name": "mcp_general_state_get",
        "tool_id": "c1",
        "parameters": {"key": "bar"},
    }

    async def run(lines: list[dict]) -> tuple[Exception, dict]:
        adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")
        proc = AsyncMock(returncode=53, pid=7)
        stdout = "\n".join(json.dumps(line) for line in lines)
        proc.communicate = AsyncMock(return_value=(stdout.encode(), b""))
        with patch(_EXEC, return_value=proc), pytest.raises(RuntimeError) as caught:
            await adapter.invoke(prompt="x", system_prompt="", mcp_servers={}, env={})
        return caught.value, adapter.last_process_info

    # A tool ran before the turn limit: usage and the tool call survive, not pre-tool-call.
    error, info = await run([tool_use, result])
    assert isinstance(error, TerminalResultError)
    assert str(error).startswith("Gemini CLI exited with code 53: ")
    assert error.usage["input_tokens"] == 5 and error.usage["cache_read_input_tokens"] == 100
    assert error.tool_calls == [
        {"id": "c1", "name": "mcp_general_state_get", "input": {"key": "bar"}}
    ]
    assert error.served["executions"][0]["error"]["code"] == "max_turns"
    assert info["is_pre_tool_call"] is False and info["exit_code"] == 53

    # No tool evidence: still a terminal result with usage, but pre-tool-call for failover.
    error, info = await run([result])
    assert isinstance(error, TerminalResultError) and error.tool_calls == []
    assert error.usage["input_tokens"] == 5
    assert info["is_pre_tool_call"] is True

    # ``stats.tool_calls`` is the CLI's telemetry total, which also counts tool calls
    # with no top-level ``tool_use`` event (a subagent's): it alone proves work ran.
    counted = {**result, "stats": {**stats, "tool_calls": 2}}
    error, info = await run([counted])
    assert isinstance(error, TerminalResultError) and error.tool_calls == []
    assert info["is_pre_tool_call"] is False


_RECORDED = Path(__file__).parent.parent / "fixtures" / "gemini_stream_json"


async def test_recorded_streams_parse_to_text_tools_usage_and_terminal_error() -> None:
    """Real 0.66.0 stream-json (see the fixture README): the echoed user prompt is not
    result text, assistant ``delta`` chunks join, and the turn limit keeps tool evidence."""
    from butlers.core.runtimes.served_identity import TerminalResultError

    async def run(name: str, returncode: int) -> tuple[Any, dict]:
        adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")
        proc = AsyncMock(returncode=returncode, pid=7)
        proc.communicate = AsyncMock(return_value=((_RECORDED / name).read_bytes(), b""))
        with patch(_EXEC, return_value=proc):
            try:
                outcome: Any = await adapter.invoke(
                    prompt="list files", system_prompt="", mcp_servers={}, env={}
                )
            except TerminalResultError as error:
                outcome = error
        return outcome, adapter.last_process_info

    (text, tool_calls, usage), _ = await run("tool_run_success.jsonl", 0)
    assert text == "Checking.\nListed the directory."
    assert tool_calls == [
        {
            "id": "list_directory__list_directory_1793569777410_0",
            "name": "list_directory",
            "input": {"dir_path": "."},
            "builtin": True,
        }
    ]
    assert usage == {
        "input_tokens": 120,
        "output_tokens": 11,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": None,
    }

    error, info = await run("turn_limit_error.jsonl", 53)
    assert isinstance(error, TerminalResultError)
    assert error.served["executions"][0]["error"]["code"] == "max_turns"
    assert [call["name"] for call in error.tool_calls] == ["list_directory"]
    assert error.usage["input_tokens"] == 100
    assert info["is_pre_tool_call"] is False and info["exit_code"] == 53
    assert info["terminal_error_text"].startswith("Reached max session turns for this session.")


def test_catalog_gemini_model_ids_were_probed_against_the_pinned_cli() -> None:
    """The CLI rewrites some ``-m`` values (0.66.0 sends gemini-3.5-flash as
    gemini-3.8-flash), and its usage labels never raise ``requested_not_served``. So
    every Gemini catalog id needs a recorded probe for the pinned CLI (see the fixture
    README), and an entry may be enabled only if the CLI serves its id unchanged."""
    import tomllib

    root = Path(__file__).parents[2]
    # The image installs the closed component declared here, rather than an
    # online npm version literal in Dockerfile.base.
    runtime_inputs = json.loads((root / "deploy/runtime-clis/runtime-inputs.json").read_text())
    pinned_version = runtime_inputs["versions"]["gemini"]
    probes = json.loads((_RECORDED / "model_resolution.json").read_text())
    served = probes.get(pinned_version)
    assert served is not None, f"record a model probe for gemini-cli {pinned_version}"
    catalog = tomllib.loads((root / "model_catalog_defaults.toml").read_text())["models"]
    for entry in (e for e in catalog if e["runtime_type"] == "gemini"):
        assert entry["model_id"] in served, f"probe {entry['model_id']} on the pinned CLI"
        if entry["enabled"]:
            assert served[entry["model_id"]] == entry["model_id"], entry["alias"]


async def test_settings_are_staged_only_where_the_cli_will_read_them(
    tmp_path: Path, gemini_settings_staging_root: Any
) -> None:
    """gemini-cli 0.66.0 skips a system settings file, with only a stderr warning, unless
    the file and every ancestor directory are owned by uid 0 and not group/other-writable;
    the session then runs without its MCP servers and turn limit. So staging refuses a
    directory chain that fails that check instead of spawning. Only an ancestor is made
    insecure, so a check of the staging directory alone would not catch it."""
    from butlers.core.runtimes import gemini

    real_stat = os.stat
    # Every path reads as root-owned and not group/other-writable, except ``insecure``.
    insecure: dict[str, Any] = {"path": None, "uid": 0, "extra_mode": 0}

    def fake_stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        st = real_stat(path, *args, **kwargs)
        uid, extra_mode = 0, 0
        if insecure["path"] and os.path.realpath(path) == os.path.realpath(insecure["path"]):
            uid, extra_mode = insecure["uid"], insecure["extra_mode"]
        mode = (st.st_mode & ~0o022) | extra_mode
        return os.stat_result((mode, *st[1:4], uid, *st[5:]))

    staging_root = gemini_settings_staging_root
    root = tmp_path / "staging"
    with (
        patch.object(gemini, "_SETTINGS_ROOT", root),
        patch.object(gemini, "_settings_staging_root", staging_root),
        patch.object(gemini.os, "stat", fake_stat),
    ):
        assert staging_root() == root
        assert real_stat(root).st_mode & 0o777 == 0o700
        insecure.update(path=tmp_path, extra_mode=0o020)
        writable = f"/{re.escape(tmp_path.name)} is group- or world-writable"
        with pytest.raises(RuntimeError, match=writable):
            staging_root()
        insecure.update(path=tmp_path.parent, uid=1000, extra_mode=0)
        adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")
        with patch(_EXEC) as spawn, pytest.raises(RuntimeError, match="not owned by root"):
            await adapter.invoke(prompt="p", system_prompt="", mcp_servers={}, env={})
    spawn.assert_not_called()

    # The settings file itself must not be group/other-writable under a loose umask.
    config_path = adapter.build_config_file({}, tmp_path)
    assert real_stat(config_path).st_mode & 0o777 == 0o600


async def test_invoke_staging_failure_removes_the_temp_dir(tmp_path: Path) -> None:
    """A failure while staging system.md or settings never leaks the per-invocation dir."""
    adapter = GeminiAdapter(gemini_binary="/usr/bin/gemini")
    with (
        patch.object(GeminiAdapter, "build_config_file", side_effect=OSError("disk full")),
        patch(_EXEC) as spawn,
        pytest.raises(OSError, match="disk full"),
    ):
        await adapter.invoke(prompt="p", system_prompt="sys", mcp_servers={}, env={})
    spawn.assert_not_called()
    assert os.listdir(tmp_path) == []


def test_build_config_file_carries_the_invocation_header(tmp_path: Path):
    """The Spawner's per-invocation binding header survives into gemini_mcp.json (bu-m2zcah)."""
    from butlers.core.fact_authority import INVOCATION_HEADER

    config_path = GeminiAdapter().build_config_file(
        mcp_servers={
            "my-butler": {
                "url": "http://localhost:9100/mcp",
                "headers": {INVOCATION_HEADER: "synthetic-binding"},
            }
        },
        tmp_dir=tmp_path,
    )

    entry = json.loads(config_path.read_text())["mcpServers"]["my-butler"]
    assert entry["headers"] == {"X-Butlers-Fact-Invocation": "synthetic-binding"}
