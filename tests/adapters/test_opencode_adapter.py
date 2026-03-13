"""Tests for OpenCodeAdapter.

Covers:
- _parse_opencode_output(): JSON-lines parsing, text extraction, tool call normalization,
  usage tracking.
- parse_system_prompt_file(): reads OPENCODE.md, falls back to AGENTS.md,
  returns empty string when neither file exists.
- build_config_file(): writes valid JSONC with mcp key and remote server entries,
  skips invalid server configs with warnings.
- _find_opencode_binary(): PATH discovery, FileNotFoundError when missing.
- invoke(): mocked subprocess, OPENCODE_CONFIG env var, model flag, timeout, error paths.
- Adapter registration and create_worker().
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import get_adapter
from butlers.core.runtimes.opencode import (
    OpenCodeAdapter,
    _extract_envelope_tool_call,
    _extract_opencode_tool_call,
    _extract_usage,
    _find_opencode_binary,
    _looks_like_tool_call_event,
    _parse_opencode_output,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Registration and basic adapter tests
# ---------------------------------------------------------------------------


def test_opencode_adapter_registered():
    """get_adapter('opencode') returns OpenCodeAdapter."""
    assert get_adapter("opencode") is OpenCodeAdapter


def test_opencode_adapter_instantiates():
    """OpenCodeAdapter can be instantiated without arguments."""
    adapter = OpenCodeAdapter()
    assert adapter is not None


def test_opencode_adapter_with_custom_binary():
    """OpenCodeAdapter accepts a custom binary path."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/local/bin/opencode")
    assert adapter._opencode_binary == "/usr/local/bin/opencode"


def test_opencode_adapter_binary_name():
    """binary_name property returns 'opencode'."""
    adapter = OpenCodeAdapter()
    assert adapter.binary_name == "opencode"


def test_opencode_adapter_create_worker_preserves_binary():
    """create_worker() returns a distinct adapter with the same binary config."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/local/bin/opencode")
    worker = adapter.create_worker()

    assert worker is not adapter
    assert isinstance(worker, OpenCodeAdapter)
    assert worker._opencode_binary == "/usr/local/bin/opencode"


def test_opencode_adapter_create_worker_no_binary():
    """create_worker() preserves None binary path."""
    adapter = OpenCodeAdapter()
    worker = adapter.create_worker()
    assert worker._opencode_binary is None


# ---------------------------------------------------------------------------
# parse_system_prompt_file tests
# ---------------------------------------------------------------------------


def test_parse_system_prompt_reads_opencode_md(tmp_path: Path):
    """OpenCodeAdapter prefers OPENCODE.md for system prompt."""
    adapter = OpenCodeAdapter()
    (tmp_path / "OPENCODE.md").write_text("You are an OpenCode butler.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "You are an OpenCode butler."


def test_parse_system_prompt_falls_back_to_agents_md(tmp_path: Path):
    """Falls back to AGENTS.md when OPENCODE.md is missing."""
    adapter = OpenCodeAdapter()
    (tmp_path / "AGENTS.md").write_text("You are an agent butler.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "You are an agent butler."


def test_parse_system_prompt_prefers_opencode_over_agents(tmp_path: Path):
    """OPENCODE.md takes priority over AGENTS.md."""
    adapter = OpenCodeAdapter()
    (tmp_path / "OPENCODE.md").write_text("OpenCode instructions.")
    (tmp_path / "AGENTS.md").write_text("Agent instructions.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "OpenCode instructions."


def test_parse_system_prompt_missing_all(tmp_path: Path):
    """Returns empty string when no prompt files exist."""
    adapter = OpenCodeAdapter()
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


def test_parse_system_prompt_empty_opencode_md_falls_back(tmp_path: Path):
    """Falls back to AGENTS.md when OPENCODE.md is empty (whitespace only)."""
    adapter = OpenCodeAdapter()
    (tmp_path / "OPENCODE.md").write_text("   \n  ")
    (tmp_path / "AGENTS.md").write_text("Agent fallback.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "Agent fallback."


def test_parse_system_prompt_both_empty(tmp_path: Path):
    """Returns empty string when both OPENCODE.md and AGENTS.md are empty."""
    adapter = OpenCodeAdapter()
    (tmp_path / "OPENCODE.md").write_text("   \n  ")
    (tmp_path / "AGENTS.md").write_text("  ")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


def test_parse_system_prompt_ignores_claude_md(tmp_path: Path):
    """CLAUDE.md is not used by OpenCodeAdapter."""
    adapter = OpenCodeAdapter()
    (tmp_path / "CLAUDE.md").write_text("This is Claude instructions.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


def test_parse_system_prompt_opencode_md_with_leading_trailing_whitespace(tmp_path: Path):
    """OPENCODE.md content is stripped of surrounding whitespace."""
    adapter = OpenCodeAdapter()
    (tmp_path / "OPENCODE.md").write_text("  Instructions here.  \n")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "Instructions here."


def test_parse_system_prompt_agents_md_with_leading_trailing_whitespace(tmp_path: Path):
    """AGENTS.md content is stripped of surrounding whitespace."""
    adapter = OpenCodeAdapter()
    (tmp_path / "AGENTS.md").write_text("\n  Agent instructions.  \n")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "Agent instructions."


# ---------------------------------------------------------------------------
# build_config_file tests
# ---------------------------------------------------------------------------


def test_build_config_file_writes_opencode_jsonc(tmp_path: Path):
    """build_config_file() writes opencode.jsonc with mcp key."""
    adapter = OpenCodeAdapter()
    mcp_servers = {"my-butler": {"url": "http://localhost:9100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    assert config_path == tmp_path / "opencode.jsonc"
    assert config_path.exists()


def test_build_config_file_remote_server_entry(tmp_path: Path):
    """build_config_file() maps servers to remote type entries."""
    adapter = OpenCodeAdapter()
    mcp_servers = {"my-butler": {"url": "http://localhost:9100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert "mcp" in data
    entry = data["mcp"]["my-butler"]
    assert entry["type"] == "remote"
    assert entry["url"] == "http://localhost:9100/mcp"
    assert entry["enabled"] is True


def test_build_config_file_includes_permission_key(tmp_path: Path):
    """build_config_file() includes empty permission object for auto-mode."""
    adapter = OpenCodeAdapter()
    config_path = adapter.build_config_file(mcp_servers={}, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert "permission" in data
    assert data["permission"] == {}


def test_build_config_file_empty_servers(tmp_path: Path):
    """build_config_file() writes an empty mcp section when no servers provided."""
    adapter = OpenCodeAdapter()
    config_path = adapter.build_config_file(mcp_servers={}, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert data["mcp"] == {}


def test_build_config_file_multiple_servers(tmp_path: Path):
    """build_config_file() writes all valid MCP servers."""
    adapter = OpenCodeAdapter()
    mcp_servers = {
        "butler-a": {"url": "http://localhost:9100/mcp"},
        "butler-b": {"url": "http://localhost:9200/mcp"},
    }
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert len(data["mcp"]) == 2
    assert "butler-a" in data["mcp"]
    assert "butler-b" in data["mcp"]
    assert data["mcp"]["butler-a"]["url"] == "http://localhost:9100/mcp"
    assert data["mcp"]["butler-b"]["url"] == "http://localhost:9200/mcp"


def test_build_config_file_skips_non_dict_server(tmp_path: Path, caplog):
    """build_config_file() skips servers with non-dict config and logs warning."""
    adapter = OpenCodeAdapter()
    mcp_servers = {
        "valid-server": {"url": "http://localhost:9100/mcp"},
        "bad-server": "not-a-dict",  # type: ignore[dict-item]
    }
    with caplog.at_level(logging.WARNING):
        config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)

    data = json.loads(config_path.read_text())
    assert "valid-server" in data["mcp"]
    assert "bad-server" not in data["mcp"]
    assert "bad-server" in caplog.text


def test_build_config_file_skips_server_without_url(tmp_path: Path, caplog):
    """build_config_file() skips servers missing a url key and logs warning."""
    adapter = OpenCodeAdapter()
    mcp_servers = {
        "valid-server": {"url": "http://localhost:9100/mcp"},
        "no-url-server": {"transport": "remote"},
    }
    with caplog.at_level(logging.WARNING):
        config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)

    data = json.loads(config_path.read_text())
    assert "valid-server" in data["mcp"]
    assert "no-url-server" not in data["mcp"]
    assert "no-url-server" in caplog.text


def test_build_config_file_skips_server_with_empty_url(tmp_path: Path, caplog):
    """build_config_file() skips servers with empty url string."""
    adapter = OpenCodeAdapter()
    mcp_servers = {
        "empty-url-server": {"url": "   "},
    }
    with caplog.at_level(logging.WARNING):
        config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)

    data = json.loads(config_path.read_text())
    assert "empty-url-server" not in data["mcp"]


def test_build_config_file_url_is_stripped(tmp_path: Path):
    """build_config_file() strips whitespace from server URLs."""
    adapter = OpenCodeAdapter()
    mcp_servers = {"my-butler": {"url": "  http://localhost:9100/mcp  "}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert data["mcp"]["my-butler"]["url"] == "http://localhost:9100/mcp"


def test_build_config_file_is_valid_json(tmp_path: Path):
    """build_config_file() writes valid JSON (JSONC with no comments for now)."""
    adapter = OpenCodeAdapter()
    mcp_servers = {"butler": {"url": "http://localhost:9100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    # Must parse as valid JSON
    data = json.loads(config_path.read_text())
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# _parse_opencode_output — text extraction
# ---------------------------------------------------------------------------


def test_parse_plain_text_fallback():
    """Plain text stdout is returned as result when no JSON found."""
    result_text, tool_calls, usage = _parse_opencode_output("Hello, world!", "", 0)
    assert result_text == "Hello, world!"
    assert tool_calls == []
    assert usage is None


def test_parse_empty_stdout_returns_none():
    """Empty stdout yields None result_text."""
    result_text, tool_calls, usage = _parse_opencode_output("", "", 0)
    assert result_text is None
    assert tool_calls == []
    assert usage is None


def test_parse_text_event_single():
    """Single text event yields its text as result."""
    line = json.dumps({"type": "text", "text": "Hello from OpenCode"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Hello from OpenCode"
    assert tool_calls == []
    assert usage is None


def test_parse_text_event_content_field():
    """text event with 'content' field (not 'text') is extracted."""
    line = json.dumps({"type": "text", "content": "Content field text"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Content field text"


def test_parse_text_event_value_field():
    """text event with 'value' field is extracted."""
    line = json.dumps({"type": "text", "value": "Value field text"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Value field text"


def test_parse_text_event_delta_field():
    """text event with 'delta' field is extracted."""
    line = json.dumps({"type": "text", "delta": "Delta text"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Delta text"


def test_parse_multipart_text_events_concatenated():
    """Multiple text events are concatenated with newlines."""
    lines = "\n".join(
        [
            json.dumps({"type": "text", "text": "Part one"}),
            json.dumps({"type": "text", "text": "Part two"}),
            json.dumps({"type": "text", "text": "Part three"}),
        ]
    )
    result_text, tool_calls, usage = _parse_opencode_output(lines, "", 0)
    assert result_text == "Part one\nPart two\nPart three"
    assert tool_calls == []


def test_parse_message_event_with_string_content():
    """Message event with string content is extracted."""
    line = json.dumps({"type": "message", "content": "Assistant reply"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Assistant reply"
    assert tool_calls == []


def test_parse_message_event_with_content_blocks():
    """Message event with content block list extracts text blocks."""
    line = json.dumps(
        {
            "type": "message",
            "content": [
                {"type": "text", "text": "First block"},
                {"type": "text", "text": "Second block"},
            ],
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "First block\nSecond block"
    assert tool_calls == []


def test_parse_message_event_tool_use_in_content():
    """Message event with tool_use block in content yields tool call."""
    line = json.dumps(
        {
            "type": "message",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "state_get",
                    "input": {"key": "foo"},
                }
            ],
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text is None
    assert len(tool_calls) == 1
    assert tool_calls[0] == {"id": "tu_1", "name": "state_get", "input": {"key": "foo"}}


def test_parse_result_event():
    """Result event with 'result' key is extracted as text."""
    line = json.dumps({"type": "result", "result": "Task complete"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Task complete"
    assert tool_calls == []


def test_parse_result_event_text_field():
    """Result event with 'text' key (not 'result') is extracted."""
    line = json.dumps({"type": "result", "text": "Result via text field"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Result via text field"


def test_parse_assistant_event_with_message():
    """Assistant event with nested message dict extracts content."""
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Done!"},
                ]
            },
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Done!"
    assert tool_calls == []


def test_parse_assistant_event_with_string_content():
    """Assistant event with direct string content is extracted."""
    line = json.dumps({"type": "assistant", "content": "Direct content"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Direct content"


def test_parse_item_completed_agent_message():
    """item.completed event with agent_message item type is extracted."""
    line = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Agent says hello"},
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Agent says hello"
    assert tool_calls == []


def test_parse_response_output_item_done_text():
    """response.output_item.done with text item type extracts content."""
    line = json.dumps(
        {
            "type": "response.output_item.done",
            "item": {"type": "text", "content": "Item text"},
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Item text"


def test_parse_ignores_non_json_lines_when_json_present():
    """Non-JSON diagnostics mixed with JSON lines are ignored."""
    lines = "\n".join(
        [
            "2026-01-01T00:00:00Z INFO opencode starting",
            json.dumps({"type": "text", "text": "Hello"}),
        ]
    )
    result_text, tool_calls, usage = _parse_opencode_output(lines, "", 0)
    assert result_text == "Hello"


def test_parse_pure_non_json_fallback():
    """When all lines are non-JSON, they are treated as plain text."""
    stdout = "line one\nline two\nline three"
    result_text, tool_calls, usage = _parse_opencode_output(stdout, "", 0)
    assert result_text == "line one\nline two\nline three"


def test_parse_unknown_event_type_skipped_gracefully():
    """Unknown event types log and skip without crashing."""
    lines = "\n".join(
        [
            json.dumps({"type": "some_future_event", "data": "value"}),
            json.dumps({"type": "text", "text": "After unknown"}),
        ]
    )
    result_text, tool_calls, usage = _parse_opencode_output(lines, "", 0)
    assert result_text == "After unknown"
    assert tool_calls == []


def test_parse_malformed_json_line_skipped():
    """Malformed JSON lines are skipped without crashing."""
    lines = "\n".join(
        [
            "{invalid json",
            json.dumps({"type": "text", "text": "Valid line"}),
        ]
    )
    result_text, tool_calls, usage = _parse_opencode_output(lines, "", 0)
    assert result_text == "Valid line"


# ---------------------------------------------------------------------------
# _parse_opencode_output — tool call extraction
# ---------------------------------------------------------------------------


def test_parse_tool_use_event():
    """Standard tool_use event is normalized to {id, name, input}."""
    line = json.dumps(
        {
            "type": "tool_use",
            "id": "tu_abc",
            "name": "state_set",
            "input": {"key": "x", "value": 42},
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0] == {
        "id": "tu_abc",
        "name": "state_set",
        "input": {"key": "x", "value": 42},
    }
    assert result_text is None


def test_parse_tool_call_event():
    """tool_call event (alternative shape) is normalized."""
    line = json.dumps(
        {
            "type": "tool_call",
            "id": "tc_1",
            "name": "notify",
            "input": {"message": "hello"},
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "notify"
    assert tool_calls[0]["id"] == "tc_1"


def test_parse_function_call_event():
    """function_call event with nested function container is normalized."""
    line = json.dumps(
        {
            "type": "function_call",
            "id": "fc_1",
            "function": {
                "name": "route_to_butler",
                "arguments": {"butler": "general", "prompt": "Do something"},
            },
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "fc_1"
    assert tool_calls[0]["name"] == "route_to_butler"
    assert tool_calls[0]["input"] == {"butler": "general", "prompt": "Do something"}


def test_parse_mcp_tool_call_event_with_nested_call():
    """mcp_tool_call event with nested call container is normalized."""
    line = json.dumps(
        {
            "type": "mcp_tool_call",
            "id": "mcp_1",
            "call": {
                "name": "schedule_task",
                "arguments": {"cron": "0 9 * * *", "task": "morning_digest"},
            },
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "mcp_1"
    assert tool_calls[0]["name"] == "schedule_task"
    assert tool_calls[0]["input"] == {"cron": "0 9 * * *", "task": "morning_digest"}


def test_parse_multiple_tool_calls():
    """Multiple tool call events are all collected."""
    lines = "\n".join(
        [
            json.dumps({"type": "tool_use", "id": "t1", "name": "tool_a", "input": {"a": 1}}),
            json.dumps({"type": "tool_use", "id": "t2", "name": "tool_b", "input": {"b": 2}}),
            json.dumps({"type": "text", "text": "Done"}),
        ]
    )
    result_text, tool_calls, usage = _parse_opencode_output(lines, "", 0)
    assert len(tool_calls) == 2
    assert tool_calls[0]["name"] == "tool_a"
    assert tool_calls[1]["name"] == "tool_b"
    assert result_text == "Done"


def test_parse_item_completed_with_tool_call():
    """item.completed with nested tool call item is extracted."""
    line = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "tool_use",
                "id": "nested_t1",
                "name": "send_notification",
                "input": {"to": "user", "msg": "hi"},
            },
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "nested_t1"
    assert tool_calls[0]["name"] == "send_notification"


def test_parse_response_output_item_done_with_function_call():
    """response.output_item.done with function_call item is extracted."""
    line = json.dumps(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "id": "fc_nested",
                "function": {
                    "name": "route_to_butler",
                    "arguments": {"butler": "health", "prompt": "Log meal"},
                },
            },
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "route_to_butler"
    assert tool_calls[0]["input"] == {"butler": "health", "prompt": "Log meal"}


# ---------------------------------------------------------------------------
# _parse_opencode_output — token usage extraction
# ---------------------------------------------------------------------------


def test_parse_usage_event():
    """Standalone usage event extracts input_tokens and output_tokens."""
    line = json.dumps({"type": "usage", "input_tokens": 100, "output_tokens": 50})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert usage == {"input_tokens": 100, "output_tokens": 50}
    assert result_text is None
    assert tool_calls == []


def test_parse_turn_completed_with_usage():
    """turn.completed event extracts usage from top-level."""
    line = json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": 200, "output_tokens": 80}}
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert usage == {"input_tokens": 200, "output_tokens": 80}


def test_parse_response_completed_with_nested_usage():
    """response.completed with response.usage is extracted."""
    line = json.dumps(
        {
            "type": "response.completed",
            "response": {"usage": {"input_tokens": 300, "output_tokens": 120}},
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert usage == {"input_tokens": 300, "output_tokens": 120}


def test_parse_usage_openai_token_format():
    """prompt_tokens/completion_tokens (OpenAI format) are accepted."""
    line = json.dumps({"type": "usage", "prompt_tokens": 150, "completion_tokens": 60})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert usage == {"input_tokens": 150, "output_tokens": 60}


def test_parse_no_usage_returns_none():
    """When no usage events appear, usage is None."""
    line = json.dumps({"type": "text", "text": "hello"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert usage is None


def test_parse_usage_non_int_tokens_none():
    """Non-integer token counts are stored as None."""
    line = json.dumps({"type": "usage", "input_tokens": "many", "output_tokens": None})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert usage == {"input_tokens": None, "output_tokens": None}


# ---------------------------------------------------------------------------
# _parse_opencode_output — non-zero exit code
# ---------------------------------------------------------------------------


def test_parse_nonzero_exit_returns_error_from_stderr():
    """Non-zero exit code returns error detail from stderr."""
    result_text, tool_calls, usage = _parse_opencode_output("", "rate limit exceeded", 1)
    assert result_text == "Error: rate limit exceeded"
    assert tool_calls == []
    assert usage is None


def test_parse_nonzero_exit_falls_back_to_stdout():
    """Non-zero exit uses stdout when stderr is empty."""
    result_text, tool_calls, usage = _parse_opencode_output("some stdout error", "", 2)
    assert result_text == "Error: some stdout error"


def test_parse_nonzero_exit_generic_message():
    """Non-zero exit with no output returns generic exit code message."""
    result_text, tool_calls, usage = _parse_opencode_output("", "", 127)
    assert result_text == "Error: exit code 127"


# ---------------------------------------------------------------------------
# _parse_opencode_output — combined scenarios
# ---------------------------------------------------------------------------


def test_parse_full_conversation_flow():
    """Full event stream with text, tool calls, and usage is parsed correctly."""
    lines = "\n".join(
        [
            json.dumps({"type": "text", "text": "I'll help you with that."}),
            json.dumps(
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "state_get",
                    "input": {"key": "user_pref"},
                }
            ),
            json.dumps({"type": "text", "text": "Task complete."}),
            json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 512, "output_tokens": 256}}
            ),
        ]
    )
    result_text, tool_calls, usage = _parse_opencode_output(lines, "", 0)
    assert result_text == "I'll help you with that.\nTask complete."
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "state_get"
    assert usage == {"input_tokens": 512, "output_tokens": 256}


# ---------------------------------------------------------------------------
# _extract_opencode_tool_call — unit tests
# ---------------------------------------------------------------------------


def test_extract_tool_call_standard_tool_use():
    """Standard tool_use format with id, name, input is normalized."""
    tc = _extract_opencode_tool_call(
        {"id": "t1", "name": "schedule_task", "input": {"cron": "0 9 * * *"}}
    )
    assert tc == {"id": "t1", "name": "schedule_task", "input": {"cron": "0 9 * * *"}}


def test_extract_tool_call_function_container():
    """function container with arguments is normalized."""
    tc = _extract_opencode_tool_call(
        {
            "id": "fc1",
            "function": {"name": "my_tool", "arguments": {"x": 1}},
        }
    )
    assert tc["id"] == "fc1"
    assert tc["name"] == "my_tool"
    assert tc["input"] == {"x": 1}


def test_extract_tool_call_call_container():
    """call container (MCP style) with arguments is normalized."""
    tc = _extract_opencode_tool_call(
        {
            "id": "mcp_1",
            "call": {"name": "route_to_butler", "arguments": {"butler": "general"}},
        }
    )
    assert tc["id"] == "mcp_1"
    assert tc["name"] == "route_to_butler"
    assert tc["input"] == {"butler": "general"}


def test_extract_tool_call_nested_call_id():
    """call_id at top level is used as tool id when 'id' is absent."""
    tc = _extract_opencode_tool_call(
        {
            "type": "mcp_tool_call",
            "call_id": "call_xyz",
            "call": {"name": "do_thing", "arguments": {}},
        }
    )
    assert tc["id"] == "call_xyz"
    assert tc["name"] == "do_thing"


def test_extract_tool_call_args_field():
    """args field (not input or arguments) is used as input."""
    tc = _extract_opencode_tool_call({"id": "t2", "name": "my_fn", "args": {"a": "b"}})
    assert tc["input"] == {"a": "b"}


def test_extract_tool_call_stringified_json_arguments():
    """Stringified JSON arguments are parsed into dict."""
    tc = _extract_opencode_tool_call(
        {
            "id": "t3",
            "name": "route_to_butler",
            "arguments": '{"butler":"health","prompt":"Track meal"}',
        }
    )
    assert tc["input"] == {"butler": "health", "prompt": "Track meal"}


def test_extract_tool_call_non_json_string_arguments_preserved():
    """Non-JSON string arguments are kept as-is (not parsed)."""
    tc = _extract_opencode_tool_call({"id": "t4", "name": "cmd", "arguments": "not json"})
    assert tc["input"] == "not json"


def test_extract_tool_call_missing_id_defaults_empty():
    """Missing id defaults to empty string."""
    tc = _extract_opencode_tool_call({"name": "some_tool", "input": {}})
    assert tc["id"] == ""


def test_extract_tool_call_missing_name_defaults_empty():
    """Missing name defaults to empty string."""
    tc = _extract_opencode_tool_call({"id": "t5", "input": {"k": "v"}})
    assert tc["name"] == ""


def test_extract_tool_call_no_input_defaults_empty_dict():
    """Missing input/args/arguments defaults to empty dict."""
    tc = _extract_opencode_tool_call({"id": "t6", "name": "empty_tool"})
    assert tc["input"] == {}


def test_extract_tool_call_tool_container():
    """tool container with arguments is normalized."""
    tc = _extract_opencode_tool_call(
        {
            "id": "t7",
            "tool": {"name": "another_tool", "arguments": {"key": "val"}},
        }
    )
    assert tc["name"] == "another_tool"
    assert tc["input"] == {"key": "val"}


# ---------------------------------------------------------------------------
# _looks_like_tool_call_event — unit tests
# ---------------------------------------------------------------------------


def test_looks_like_tool_call_known_type():
    """Known tool call type strings are detected."""
    for type_str in (
        "tool_use",
        "tool_call",
        "function_call",
        "mcp_tool_call",
        "mcp_tool_use",
        "custom_tool_call",
        "command_execution",
    ):
        assert _looks_like_tool_call_event({"type": type_str}) is True


def test_looks_like_tool_call_name_and_input():
    """Object with name + input is detected heuristically."""
    assert _looks_like_tool_call_event({"name": "my_tool", "input": {"a": 1}}) is True


def test_looks_like_tool_call_name_and_arguments():
    """Object with name + arguments is detected heuristically."""
    assert _looks_like_tool_call_event({"name": "my_tool", "arguments": {"a": 1}}) is True


def test_looks_like_tool_call_false_for_text_event():
    """Text events are not detected as tool calls."""
    assert _looks_like_tool_call_event({"type": "text", "text": "hello"}) is False


def test_looks_like_tool_call_false_for_empty_name():
    """Object with empty name string is not detected as tool call."""
    assert _looks_like_tool_call_event({"name": "", "input": {"a": 1}}) is False


def test_looks_like_tool_call_nested_function_container():
    """Nested function container with name + arguments is detected."""
    obj = {"function": {"name": "my_fn", "arguments": {"x": 1}}}
    assert _looks_like_tool_call_event(obj) is True


# ---------------------------------------------------------------------------
# _extract_usage — unit tests
# ---------------------------------------------------------------------------


def test_extract_usage_from_direct_fields():
    """input_tokens/output_tokens extracted from direct fields."""
    result = _extract_usage({"input_tokens": 100, "output_tokens": 50})
    assert result == {"input_tokens": 100, "output_tokens": 50}


def test_extract_usage_from_nested_usage_key():
    """usage sub-key is checked when direct fields are absent."""
    result = _extract_usage({"usage": {"input_tokens": 200, "output_tokens": 80}})
    assert result == {"input_tokens": 200, "output_tokens": 80}


def test_extract_usage_openai_format():
    """prompt_tokens/completion_tokens (OpenAI format) are mapped."""
    result = _extract_usage({"prompt_tokens": 150, "completion_tokens": 60})
    assert result == {"input_tokens": 150, "output_tokens": 60}


def test_extract_usage_returns_none_when_no_usage_fields():
    """None returned when no recognizable usage fields exist."""
    result = _extract_usage({"type": "text", "text": "hello"})
    assert result is None


def test_extract_usage_returns_none_for_non_dict():
    """None returned for non-dict input."""
    assert _extract_usage(None) is None  # type: ignore[arg-type]
    assert _extract_usage("string") is None  # type: ignore[arg-type]


def test_extract_usage_non_int_stored_as_none():
    """Non-integer token counts are stored as None."""
    result = _extract_usage({"input_tokens": "lots", "output_tokens": None})
    assert result == {"input_tokens": None, "output_tokens": None}


# ---------------------------------------------------------------------------
# _find_opencode_binary tests
# ---------------------------------------------------------------------------

_EXEC = "butlers.core.runtimes.opencode.asyncio.create_subprocess_exec"


def test_find_opencode_binary_found():
    """_find_opencode_binary returns path when opencode is on PATH."""
    with patch(
        "butlers.core.runtimes.opencode.shutil.which",
        return_value="/usr/local/bin/opencode",
    ):
        assert _find_opencode_binary() == "/usr/local/bin/opencode"


def test_find_opencode_binary_not_found():
    """_find_opencode_binary raises FileNotFoundError when opencode is missing."""
    with patch(
        "butlers.core.runtimes.opencode.shutil.which",
        return_value=None,
    ):
        with pytest.raises(FileNotFoundError, match="OpenCode CLI binary not found"):
            _find_opencode_binary()


def test_opencode_adapter_get_binary_uses_custom_path():
    """_get_binary() returns custom binary path without calling shutil.which."""
    adapter = OpenCodeAdapter(opencode_binary="/opt/opencode/bin/opencode")
    with patch("butlers.core.runtimes.opencode.shutil.which") as mock_which:
        result = adapter._get_binary()
    assert result == "/opt/opencode/bin/opencode"
    mock_which.assert_not_called()


def test_opencode_adapter_get_binary_auto_detects():
    """_get_binary() calls _find_opencode_binary when no custom binary is set."""
    adapter = OpenCodeAdapter()
    with patch(
        "butlers.core.runtimes.opencode.shutil.which",
        return_value="/usr/bin/opencode",
    ):
        result = adapter._get_binary()
    assert result == "/usr/bin/opencode"


# ---------------------------------------------------------------------------
# invoke() tests with mocked subprocess
# ---------------------------------------------------------------------------


async def test_invoke_success():
    """invoke() calls subprocess and parses JSON output."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    output_lines = "\n".join(
        [
            json.dumps({"type": "text", "text": "Task done."}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                }
            ),
        ]
    )

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output_lines.encode(), b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="do something",
            system_prompt="you are helpful",
            mcp_servers={"test": {"url": "http://localhost:9100/mcp"}},
            env={"ANTHROPIC_API_KEY": "sk-test"},
        )

    assert result_text == "Task done."
    assert tool_calls == []
    assert usage == {"input_tokens": 10, "output_tokens": 20}

    call_args = mock_sub.call_args
    cmd = call_args[0]
    assert cmd[0] == "/usr/bin/opencode"
    assert cmd[1] == "run"
    assert "--format" in cmd
    assert "json" in cmd
    assert "do something" in cmd


async def test_invoke_sets_opencode_config_env_var():
    """invoke() injects OPENCODE_CONFIG env var pointing to temp config file.

    OPENCODE_CONFIG is only set when there are MCP servers or a system prompt
    to inject — otherwise OpenCode uses its own config for provider auth.
    """
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="do something",
            system_prompt="",
            mcp_servers={"my-butler": {"url": "http://localhost:9100/mcp"}},
            env={"PATH": "/usr/bin"},
        )

    call_kwargs = mock_sub.call_args[1]
    env = call_kwargs["env"]
    assert "OPENCODE_CONFIG" in env
    # The value should be a path ending in opencode.jsonc
    assert env["OPENCODE_CONFIG"].endswith("opencode.jsonc")


async def test_invoke_skips_opencode_config_when_no_servers_or_prompt():
    """invoke() does NOT set OPENCODE_CONFIG when there are no MCP servers and no prompt.

    This lets OpenCode use its own config so provider auth/keys are preserved.
    """
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="do something",
            system_prompt="",
            mcp_servers={},
            env={"PATH": "/usr/bin"},
        )

    call_kwargs = mock_sub.call_args[1]
    env = call_kwargs["env"]
    assert "OPENCODE_CONFIG" not in env


async def test_invoke_config_contains_mcp_servers():
    """invoke() config file written to OPENCODE_CONFIG contains MCP servers.

    We read the config content while subprocess is running (inside the TemporaryDirectory
    context) by capturing it from the env passed to create_subprocess_exec and reading
    the file before the context manager cleans up.
    """
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    captured_config: list[dict] = []

    mock_proc = AsyncMock()
    mock_proc.returncode = 0

    async def communicate_and_capture() -> tuple[bytes, bytes]:
        return b"ok", b""

    mock_proc.communicate = communicate_and_capture

    def capture_env(*args: object, **kwargs: object) -> AsyncMock:
        env = kwargs.get("env", {})
        if "OPENCODE_CONFIG" in env:
            config_path = env["OPENCODE_CONFIG"]
            # Read immediately while temp dir still exists
            try:
                data = json.loads(Path(config_path).read_text())
                captured_config.append(data)
            except Exception:
                pass
        return mock_proc

    with patch(_EXEC, side_effect=capture_env):
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={"my-butler": {"url": "http://localhost:9100/mcp"}},
            env={},
        )

    assert len(captured_config) == 1
    config_data = captured_config[0]
    assert "mcp" in config_data
    assert "my-butler" in config_data["mcp"]
    assert config_data["mcp"]["my-butler"]["type"] == "remote"
    assert config_data["mcp"]["my-butler"]["url"] == "http://localhost:9100/mcp"
    assert config_data["mcp"]["my-butler"]["enabled"] is True
    assert "permission" in config_data


async def test_invoke_config_includes_instructions_when_system_prompt():
    """invoke() config includes instructions array when system_prompt is provided."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    captured_config: list[dict] = []

    mock_proc = AsyncMock()
    mock_proc.returncode = 0

    async def communicate_and_capture() -> tuple[bytes, bytes]:
        return b"ok", b""

    mock_proc.communicate = communicate_and_capture

    def capture_env(*args: object, **kwargs: object) -> AsyncMock:
        env = kwargs.get("env", {})
        if "OPENCODE_CONFIG" in env:
            config_path = env["OPENCODE_CONFIG"]
            try:
                data = json.loads(Path(config_path).read_text())
                captured_config.append(data)
            except Exception:
                pass
        return mock_proc

    with patch(_EXEC, side_effect=capture_env):
        await adapter.invoke(
            prompt="test",
            system_prompt="You are a helpful butler.",
            mcp_servers={},
            env={},
        )

    assert len(captured_config) == 1
    config_data = captured_config[0]
    assert "instructions" in config_data
    assert len(config_data["instructions"]) == 1
    # The instructions path should point to a _system_prompt.md file
    assert "_system_prompt.md" in config_data["instructions"][0]


async def test_invoke_no_instructions_when_no_system_prompt():
    """invoke() config has no instructions key when system_prompt is empty.

    We must provide MCP servers so that OPENCODE_CONFIG is actually set
    (the adapter skips config injection when there are no servers and no prompt).
    """
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    captured_config: list[dict] = []

    mock_proc = AsyncMock()
    mock_proc.returncode = 0

    async def communicate_and_capture() -> tuple[bytes, bytes]:
        return b"ok", b""

    mock_proc.communicate = communicate_and_capture

    def capture_env(*args: object, **kwargs: object) -> AsyncMock:
        env = kwargs.get("env", {})
        if "OPENCODE_CONFIG" in env:
            config_path = env["OPENCODE_CONFIG"]
            try:
                data = json.loads(Path(config_path).read_text())
                captured_config.append(data)
            except Exception:
                pass
        return mock_proc

    with patch(_EXEC, side_effect=capture_env):
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={"my-butler": {"url": "http://localhost:9100/mcp"}},
            env={},
        )

    assert len(captured_config) == 1
    config_data = captured_config[0]
    assert "instructions" not in config_data


async def test_invoke_passes_model_flag():
    """invoke() forwards --model flag to OpenCode CLI when provided."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="run",
            system_prompt="",
            mcp_servers={},
            env={},
            model="anthropic/claude-sonnet-4-5",
        )

    cmd = mock_sub.call_args[0]
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "anthropic/claude-sonnet-4-5"


async def test_invoke_no_model_flag_when_none():
    """invoke() omits --model flag when model is None."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
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


async def test_invoke_passes_runtime_args():
    """invoke() forwards configured runtime args to OpenCode CLI."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="do the task",
            system_prompt="",
            mcp_servers={},
            env={},
            runtime_args=["--verbose", "--debug"],
        )

    cmd = mock_sub.call_args[0]
    assert "--verbose" in cmd
    assert "--debug" in cmd
    # runtime_args come before the prompt
    verbose_idx = cmd.index("--verbose")
    prompt_idx = cmd.index("do the task")
    assert verbose_idx < prompt_idx


async def test_invoke_passes_cwd():
    """invoke() passes working directory to the subprocess."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
            cwd=Path("/tmp/workdir"),
        )

    call_kwargs = mock_sub.call_args[1]
    assert call_kwargs["cwd"] == "/tmp/workdir"


async def test_invoke_nonzero_exit_raises_runtime_error():
    """invoke() raises RuntimeError on non-zero exit code."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"rate limit exceeded"))
    mock_proc.returncode = 1

    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(
            RuntimeError,
            match="OpenCode CLI exited with code 1: rate limit exceeded",
        ):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
            )


async def test_invoke_nonzero_exit_falls_back_to_stdout_for_error():
    """invoke() includes stdout in RuntimeError when stderr is empty."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"stdout error detail", b""))
    mock_proc.returncode = 2

    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(RuntimeError, match="stdout error detail"):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
            )


async def test_invoke_timeout_kills_process():
    """invoke() raises TimeoutError and kills process when subprocess times out."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()

    with patch(_EXEC, return_value=mock_proc):
        with pytest.raises(TimeoutError, match="OpenCode CLI timed out"):
            await adapter.invoke(
                prompt="slow task",
                system_prompt="",
                mcp_servers={},
                env={},
                timeout=1,
            )

    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_called_once()


async def test_invoke_binary_not_found():
    """invoke() raises FileNotFoundError if opencode not on PATH."""
    adapter = OpenCodeAdapter()  # No binary specified, auto-detect

    with patch(
        "butlers.core.runtimes.opencode.shutil.which",
        return_value=None,
    ):
        with pytest.raises(FileNotFoundError, match="OpenCode CLI binary not found"):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
            )


async def test_invoke_passes_env_to_subprocess():
    """invoke() passes caller env vars (plus OPENCODE_CONFIG) to subprocess.

    OPENCODE_CONFIG is injected only when MCP servers or a system prompt are
    provided, so we pass a server entry to trigger the injection.
    """
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    caller_env = {"ANTHROPIC_API_KEY": "sk-test", "PATH": "/usr/bin"}

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={"my-butler": {"url": "http://localhost:9100/mcp"}},
            env=caller_env,
        )

    call_kwargs = mock_sub.call_args[1]
    subprocess_env = call_kwargs["env"]
    assert subprocess_env["ANTHROPIC_API_KEY"] == "sk-test"
    assert subprocess_env["PATH"] == "/usr/bin"
    assert "OPENCODE_CONFIG" in subprocess_env


async def test_invoke_with_tool_calls():
    """invoke() captures tool_use tool calls from adapter output."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

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

    with patch(_EXEC, return_value=mock_proc):
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


async def test_invoke_uses_run_subcommand():
    """invoke() uses 'opencode run' subcommand (not exec or other)."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    cmd = mock_sub.call_args[0]
    assert cmd[0] == "/usr/bin/opencode"
    assert cmd[1] == "run"


# ---------------------------------------------------------------------------
# Gap coverage: unknown event type text/content harvesting
# ---------------------------------------------------------------------------


def test_parse_unknown_event_type_with_text_field_harvested():
    """Unknown event type with a 'text' field has its text harvested."""
    line = json.dumps({"type": "some_future_event", "text": "Harvested text"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Harvested text"
    assert tool_calls == []


def test_parse_unknown_event_type_with_content_field_harvested():
    """Unknown event type with a 'content' string field has its content harvested."""
    line = json.dumps({"type": "exotic_event", "content": "Content from unknown event"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Content from unknown event"
    assert tool_calls == []


def test_parse_unknown_event_type_text_takes_priority_over_content():
    """For unknown event types, 'text' field takes priority over 'content'."""
    line = json.dumps({"type": "dual_field_event", "text": "From text", "content": "From content"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "From text"


# ---------------------------------------------------------------------------
# Gap coverage: item.started and response.output_item.added event types
# ---------------------------------------------------------------------------


def test_parse_item_started_with_agent_message():
    """item.started event with agent_message item type extracts text."""
    line = json.dumps(
        {
            "type": "item.started",
            "item": {"type": "agent_message", "text": "Starting agent message"},
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Starting agent message"
    assert tool_calls == []


def test_parse_response_output_item_added_with_text():
    """response.output_item.added event with text item type extracts content."""
    line = json.dumps(
        {
            "type": "response.output_item.added",
            "item": {"type": "text", "content": "Added item text"},
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Added item text"


def test_parse_item_started_with_tool_call():
    """item.started with a nested tool call item is extracted."""
    line = json.dumps(
        {
            "type": "item.started",
            "item": {
                "type": "tool_use",
                "id": "ts_1",
                "name": "state_set",
                "input": {"key": "x", "value": 1},
            },
        }
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "state_set"
    assert tool_calls[0]["id"] == "ts_1"


def test_parse_item_started_no_item_key():
    """item.started with no 'item' key is skipped gracefully."""
    line = json.dumps({"type": "item.started"})
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text is None
    assert tool_calls == []


# ---------------------------------------------------------------------------
# Gap coverage: _extract_opencode_tool_call — camelCase and extra fields
# ---------------------------------------------------------------------------


def test_extract_tool_call_toolcall_camelcase_container():
    """toolCall (camelCase) container is normalized."""
    tc = _extract_opencode_tool_call(
        {
            "id": "cc_1",
            "toolCall": {"name": "camel_tool", "arguments": {"k": "v"}},
        }
    )
    assert tc["id"] == "cc_1"
    assert tc["name"] == "camel_tool"
    assert tc["input"] == {"k": "v"}


def test_extract_tool_call_tool_call_snake_case_container():
    """tool_call (snake_case) nested container is normalized."""
    tc = _extract_opencode_tool_call(
        {
            "id": "sc_1",
            "tool_call": {"name": "snake_tool", "arguments": {"x": 42}},
        }
    )
    assert tc["id"] == "sc_1"
    assert tc["name"] == "snake_tool"
    assert tc["input"] == {"x": 42}


def test_extract_tool_call_parameters_field():
    """parameters field (not input/args/arguments) is used as input."""
    tc = _extract_opencode_tool_call({"id": "p1", "name": "param_tool", "parameters": {"a": 1}})
    assert tc["input"] == {"a": 1}


def test_extract_tool_call_tool_name_field():
    """tool_name field is used when 'name' is absent."""
    tc = _extract_opencode_tool_call({"id": "tn_1", "tool_name": "alt_name", "input": {"x": 1}})
    assert tc["name"] == "alt_name"


def test_extract_tool_call_toolname_camelcase_field():
    """toolName (camelCase) field is used when 'name' and 'tool_name' are absent."""
    tc = _extract_opencode_tool_call({"id": "tN_1", "toolName": "CamelTool", "input": {}})
    assert tc["name"] == "CamelTool"


def test_extract_tool_call_nested_call_id_from_container():
    """call_id inside a nested container is used as id when top-level id/call_id absent."""
    tc = _extract_opencode_tool_call(
        {
            "call": {"name": "nested_id_tool", "call_id": "inner_id", "arguments": {}},
        }
    )
    assert tc["id"] == "inner_id"
    assert tc["name"] == "nested_id_tool"


# ---------------------------------------------------------------------------
# Gap coverage: _looks_like_tool_call_event — camelCase and additional containers
# ---------------------------------------------------------------------------


def test_looks_like_tool_call_toolcall_camelcase_container():
    """toolCall (camelCase) nested container with name is detected."""
    obj = {"toolCall": {"name": "my_tool", "arguments": {"x": 1}}}
    assert _looks_like_tool_call_event(obj) is True


def test_looks_like_tool_call_tool_call_snake_container():
    """tool_call (snake_case) nested container with name is detected."""
    obj = {"tool_call": {"name": "my_tool", "input": {"x": 1}}}
    assert _looks_like_tool_call_event(obj) is True


def test_looks_like_tool_call_false_for_no_args():
    """Object with name but no args/input is not detected as tool call."""
    assert _looks_like_tool_call_event({"name": "lonely_tool"}) is False


# ---------------------------------------------------------------------------
# Gap coverage: invoke() temp directory cleanup on RuntimeError
# ---------------------------------------------------------------------------


async def test_invoke_temp_dir_cleaned_up_after_runtime_error():
    """invoke() cleans up the temp directory even when subprocess exits non-zero."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
    captured_tmp_dirs: list[str] = []
    original_temp_dir = tempfile.TemporaryDirectory

    class CapturingTempDir:
        def __init__(self):
            self._inner = original_temp_dir()
            captured_tmp_dirs.append(self._inner.name)

        def __enter__(self):
            return self._inner.__enter__()

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"some error"))
    mock_proc.returncode = 1

    with patch(_EXEC, return_value=mock_proc):
        with patch("butlers.core.runtimes.opencode.tempfile.TemporaryDirectory", CapturingTempDir):
            with pytest.raises(RuntimeError):
                await adapter.invoke(
                    prompt="test",
                    system_prompt="",
                    mcp_servers={},
                    env={},
                )

    # The temp dir should have been created and then cleaned up
    assert len(captured_tmp_dirs) == 1
    assert not Path(captured_tmp_dirs[0]).exists(), "Temp dir should be cleaned up on failure"


# ---------------------------------------------------------------------------
# OpenCode v1.2+ envelope format — _parse_opencode_output
# ---------------------------------------------------------------------------


def _envelope(event_type: str, part: dict, session_id: str = "ses_test") -> str:
    """Build a JSON-serialized OpenCode v1.2+ envelope event."""
    return json.dumps(
        {
            "type": event_type,
            "timestamp": 1700000000000,
            "sessionID": session_id,
            "part": part,
        }
    )


def test_parse_envelope_text_event():
    """Envelope text event extracts part.text as result text."""
    line = _envelope(
        "text",
        {
            "type": "text",
            "text": "Hello from envelope",
            "id": "prt_1",
            "sessionID": "ses_test",
            "messageID": "msg_1",
        },
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Hello from envelope"
    assert tool_calls == []
    assert usage is None


def test_parse_envelope_multiple_text_events():
    """Multiple envelope text events are concatenated."""
    lines = "\n".join(
        [
            _envelope("text", {"type": "text", "text": "Part one"}),
            _envelope("text", {"type": "text", "text": "Part two"}),
        ]
    )
    result_text, tool_calls, usage = _parse_opencode_output(lines, "", 0)
    assert result_text == "Part one\nPart two"


def test_parse_envelope_tool_use_event():
    """Envelope tool_use event extracts part.tool, part.callID, part.state.input."""
    line = _envelope(
        "tool_use",
        {
            "type": "tool",
            "callID": "call_ABC123",
            "tool": "bash",
            "state": {
                "status": "completed",
                "input": {"command": "ls -la", "workdir": "/tmp"},
                "output": "total 0\n",
            },
        },
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text is None
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call_ABC123"
    assert tool_calls[0]["name"] == "bash"
    assert tool_calls[0]["input"] == {"command": "ls -la", "workdir": "/tmp"}


def test_parse_envelope_tool_use_read_tool():
    """Envelope tool_use event for file read tool extracts correctly."""
    line = _envelope(
        "tool_use",
        {
            "type": "tool",
            "callID": "call_READ1",
            "tool": "read",
            "state": {
                "status": "completed",
                "input": {"filePath": "/tmp/test.txt"},
                "output": "file contents here",
            },
        },
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "read"
    assert tool_calls[0]["input"] == {"filePath": "/tmp/test.txt"}


def test_parse_envelope_step_finish_extracts_tokens():
    """Envelope step_finish event extracts part.tokens as usage."""
    line = _envelope(
        "step_finish",
        {
            "type": "step-finish",
            "reason": "stop",
            "cost": 0,
            "tokens": {
                "total": 15711,
                "input": 15485,
                "output": 226,
                "reasoning": 219,
                "cache": {"read": 0, "write": 0},
            },
        },
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert usage == {"input_tokens": 15485, "output_tokens": 226}
    assert result_text is None
    assert tool_calls == []


def test_parse_envelope_multi_step_tokens_accumulated():
    """Token usage from multiple step_finish events is accumulated."""
    lines = "\n".join(
        [
            _envelope("step_start", {"type": "step-start"}),
            _envelope(
                "tool_use",
                {
                    "type": "tool",
                    "callID": "call_1",
                    "tool": "bash",
                    "state": {"status": "completed", "input": {"command": "ls"}},
                },
            ),
            _envelope(
                "step_finish",
                {
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "tokens": {"total": 15686, "input": 15472, "output": 214},
                },
            ),
            _envelope("step_start", {"type": "step-start"}),
            _envelope("text", {"type": "text", "text": "Done"}),
            _envelope(
                "step_finish",
                {
                    "type": "step-finish",
                    "reason": "stop",
                    "tokens": {"total": 16166, "input": 346, "output": 460},
                },
            ),
        ]
    )
    result_text, tool_calls, usage = _parse_opencode_output(lines, "", 0)
    assert result_text == "Done"
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "bash"
    assert usage == {
        "input_tokens": 15472 + 346,
        "output_tokens": 214 + 460,
    }


def test_parse_envelope_step_start_skipped():
    """Envelope step_start events are silently skipped."""
    lines = "\n".join(
        [
            _envelope("step_start", {"type": "step-start", "id": "prt_1"}),
            _envelope("text", {"type": "text", "text": "After start"}),
        ]
    )
    result_text, tool_calls, usage = _parse_opencode_output(lines, "", 0)
    assert result_text == "After start"
    assert tool_calls == []


def test_parse_envelope_unknown_type_harvests_part_text():
    """Unknown envelope event type harvests text from part if present."""
    line = _envelope(
        "some_new_event",
        {
            "type": "new-thing",
            "text": "Harvested from part",
        },
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert result_text == "Harvested from part"


def test_parse_envelope_full_conversation_flow():
    """Full OpenCode v1.2+ conversation: tool call → text → token accumulation."""
    lines = "\n".join(
        [
            _envelope("step_start", {"type": "step-start"}),
            _envelope(
                "tool_use",
                {
                    "type": "tool",
                    "callID": "call_VFLEFkFe",
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "/tmp/test.txt"},
                        "output": "file content",
                    },
                },
            ),
            _envelope(
                "step_finish",
                {
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "cost": 0,
                    "tokens": {"total": 15639, "input": 9087, "output": 152},
                },
            ),
            _envelope("step_start", {"type": "step-start"}),
            _envelope(
                "text",
                {
                    "type": "text",
                    "text": "The file contains: file content",
                },
            ),
            _envelope(
                "step_finish",
                {
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0,
                    "tokens": {"total": 16417, "input": 748, "output": 309},
                },
            ),
        ]
    )
    result_text, tool_calls, usage = _parse_opencode_output(lines, "", 0)
    assert result_text == "The file contains: file content"
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call_VFLEFkFe"
    assert tool_calls[0]["name"] == "read"
    assert tool_calls[0]["input"] == {"filePath": "/tmp/test.txt"}
    assert usage == {
        "input_tokens": 9087 + 748,
        "output_tokens": 152 + 309,
    }


def test_parse_envelope_step_finish_no_tokens_key():
    """step_finish without tokens key does not set usage."""
    line = _envelope(
        "step_finish",
        {
            "type": "step-finish",
            "reason": "stop",
        },
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert usage is None


def test_parse_envelope_tool_use_missing_state():
    """Envelope tool_use with missing state still extracts name and id."""
    line = _envelope(
        "tool_use",
        {
            "type": "tool",
            "callID": "call_NO_STATE",
            "tool": "write",
        },
    )
    result_text, tool_calls, usage = _parse_opencode_output(line, "", 0)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call_NO_STATE"
    assert tool_calls[0]["name"] == "write"
    assert tool_calls[0]["input"] == {}


# ---------------------------------------------------------------------------
# _extract_envelope_tool_call — unit tests
# ---------------------------------------------------------------------------


def test_extract_envelope_tool_call_full():
    """Full envelope part with callID, tool, state.input is extracted."""
    tc = _extract_envelope_tool_call(
        {
            "type": "tool",
            "callID": "call_ABC",
            "tool": "bash",
            "state": {
                "status": "completed",
                "input": {"command": "echo hello", "workdir": "/tmp"},
                "output": "hello\n",
            },
        }
    )
    assert tc == {
        "id": "call_ABC",
        "name": "bash",
        "input": {"command": "echo hello", "workdir": "/tmp"},
    }


def test_extract_envelope_tool_call_no_state():
    """Part without state defaults input to empty dict."""
    tc = _extract_envelope_tool_call(
        {
            "type": "tool",
            "callID": "call_1",
            "tool": "read",
        }
    )
    assert tc == {"id": "call_1", "name": "read", "input": {}}


def test_extract_envelope_tool_call_fallback_to_id():
    """Falls back to 'id' when 'callID' is absent."""
    tc = _extract_envelope_tool_call(
        {
            "type": "tool",
            "id": "alt_id",
            "tool": "write",
            "state": {"input": {"path": "/tmp/x"}},
        }
    )
    assert tc["id"] == "alt_id"


def test_extract_envelope_tool_call_fallback_to_name():
    """Falls back to 'name' when 'tool' is absent."""
    tc = _extract_envelope_tool_call(
        {
            "type": "tool",
            "callID": "call_2",
            "name": "custom_tool",
            "state": {"input": {"k": "v"}},
        }
    )
    assert tc["name"] == "custom_tool"


def test_extract_envelope_tool_call_stringified_input():
    """Stringified JSON in state.input is parsed into dict."""
    tc = _extract_envelope_tool_call(
        {
            "callID": "call_3",
            "tool": "bash",
            "state": {"input": '{"command": "ls"}'},
        }
    )
    assert tc["input"] == {"command": "ls"}


def test_extract_envelope_tool_call_non_json_string_input():
    """Non-JSON string in state.input is kept as-is."""
    tc = _extract_envelope_tool_call(
        {
            "callID": "call_4",
            "tool": "bash",
            "state": {"input": "not json"},
        }
    )
    assert tc["input"] == "not json"


def test_extract_envelope_tool_call_empty_defaults():
    """Empty part defaults to empty id, name, and input."""
    tc = _extract_envelope_tool_call({})
    assert tc == {"id": "", "name": "", "input": {}}


# ---------------------------------------------------------------------------
# invoke() with envelope format (mocked subprocess)
# ---------------------------------------------------------------------------


async def test_invoke_with_envelope_output():
    """invoke() correctly parses OpenCode v1.2+ envelope format output."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")

    output_lines = "\n".join(
        [
            _envelope("step_start", {"type": "step-start"}),
            _envelope(
                "tool_use",
                {
                    "type": "tool",
                    "callID": "call_123",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "echo hi"},
                        "output": "hi\n",
                    },
                },
            ),
            _envelope(
                "step_finish",
                {
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "tokens": {"total": 500, "input": 400, "output": 100},
                },
            ),
            _envelope("step_start", {"type": "step-start"}),
            _envelope("text", {"type": "text", "text": "Done!"}),
            _envelope(
                "step_finish",
                {
                    "type": "step-finish",
                    "reason": "stop",
                    "tokens": {"total": 200, "input": 150, "output": 50},
                },
            ),
        ]
    )

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(output_lines.encode(), b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc):
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="do something",
            system_prompt="you are helpful",
            mcp_servers={},
            env={},
        )

    assert result_text == "Done!"
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "bash"
    assert tool_calls[0]["id"] == "call_123"
    assert tool_calls[0]["input"] == {"command": "echo hi"}
    assert usage == {"input_tokens": 550, "output_tokens": 150}
