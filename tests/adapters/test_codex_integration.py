"""Integration tests for Codex adapter — runs the real codex binary.

These tests invoke the actual ``codex exec --json --full-auto`` CLI and verify
that the adapter's parser correctly extracts text, tool calls, and token usage
from real Codex output.

Marked ``nightly`` so they are excluded from default CI runs (addopts includes
``-m 'not nightly'``). Run explicitly with::

    uv run pytest tests/adapters/test_codex_integration.py -m nightly -v

Requirements:
- ``codex`` binary on PATH (``npm install -g @openai/codex``)
- Valid LLM API credentials in environment (e.g. OPENAI_API_KEY)
"""

from __future__ import annotations

import os
import shutil

import pytest

from butlers.core.runtimes.codex import (
    CodexAdapter,
    _parse_codex_output,
)

from .conftest import parse_jsonl_events, run_cli

_codex_available = shutil.which("codex") is not None

pytestmark = [
    pytest.mark.nightly,
    pytest.mark.skipif(not _codex_available, reason="codex binary not on PATH"),
]

_CODEX_ARGS = ["exec", "--json", "--full-auto", "--skip-git-repo-check"]


def _run_codex(prompt: str, timeout: int = 120) -> tuple[str, str, int]:
    """Run ``codex exec --json --full-auto`` via shared helper."""
    return run_cli("codex", _CODEX_ARGS, prompt, timeout=timeout)


# ---------------------------------------------------------------------------
# Raw output format verification — do events match expected shapes?
# ---------------------------------------------------------------------------


class TestCodexOutputFormat:
    """Verify Codex CLI JSON output matches the event format we parse."""

    def test_simple_text_produces_expected_events(self):
        """Simple prompt produces thread.started, turn.started, item.completed, turn.completed."""
        stdout, stderr, rc = _run_codex("hello")
        assert rc == 0, f"codex failed: {stderr}"

        events = parse_jsonl_events(stdout)
        assert len(events) >= 2, f"Expected at least 2 events, got {len(events)}"

        event_types = [e.get("type") for e in events]
        assert "turn.completed" in event_types, (
            f"Missing turn.completed event. Types: {event_types}"
        )

    def test_item_completed_agent_message_has_text(self):
        """item.completed events with agent_message type contain text field."""
        stdout, stderr, rc = _run_codex("hello")
        assert rc == 0, f"codex failed: {stderr}"

        events = parse_jsonl_events(stdout)
        agent_msgs = [
            e for e in events
            if e.get("type") == "item.completed"
            and isinstance(e.get("item"), dict)
            and e["item"].get("type") == "agent_message"
        ]
        assert len(agent_msgs) >= 1, (
            f"No agent_message item.completed events. Types: "
            f"{[e.get('type') for e in events]}"
        )

        for msg in agent_msgs:
            item = msg["item"]
            assert "text" in item, f"agent_message item missing 'text': {item}"
            assert isinstance(item["text"], str)
            assert len(item["text"]) > 0

    def test_turn_completed_has_usage(self):
        """turn.completed events contain usage with input_tokens and output_tokens."""
        stdout, stderr, rc = _run_codex("hello")
        assert rc == 0, f"codex failed: {stderr}"

        events = parse_jsonl_events(stdout)
        turn_events = [e for e in events if e.get("type") == "turn.completed"]
        assert len(turn_events) >= 1, f"No turn.completed events: {events}"

        for te in turn_events:
            usage = te.get("usage")
            assert isinstance(usage, dict), f"turn.completed missing usage: {te}"
            assert "input_tokens" in usage, f"usage missing input_tokens: {usage}"
            assert "output_tokens" in usage, f"usage missing output_tokens: {usage}"
            assert isinstance(usage["input_tokens"], int)
            assert isinstance(usage["output_tokens"], int)
            assert usage["input_tokens"] > 0
            assert usage["output_tokens"] > 0

    def test_command_execution_event_has_command_and_exit_code(self):
        """command_execution events contain command, exit_code, and aggregated_output."""
        stdout, stderr, rc = _run_codex(
            "Run echo codex-format-test in the shell"
        )
        assert rc == 0, f"codex failed: {stderr}"

        events = parse_jsonl_events(stdout)
        cmd_events = [
            e for e in events
            if e.get("type") == "item.completed"
            and isinstance(e.get("item"), dict)
            and e["item"].get("type") == "command_execution"
        ]
        assert len(cmd_events) >= 1, (
            f"No command_execution events. Types: "
            f"{[(e.get('type'), e.get('item', {}).get('type')) for e in events]}"
        )

        item = cmd_events[0]["item"]
        assert "command" in item, f"command_execution missing 'command': {item}"
        assert isinstance(item["command"], str)
        assert len(item["command"]) > 0
        assert "exit_code" in item, f"command_execution missing 'exit_code': {item}"
        assert "aggregated_output" in item, (
            f"command_execution missing 'aggregated_output': {item}"
        )


# ---------------------------------------------------------------------------
# Parser integration — does _parse_codex_output handle real output?
# ---------------------------------------------------------------------------


class TestParserWithRealOutput:
    """Feed real codex output through the adapter parser and verify results."""

    def test_simple_text_parsed_correctly(self):
        """Parser extracts text from a simple text-only response."""
        stdout, stderr, rc = _run_codex("hello")
        assert rc == 0, f"codex failed: {stderr}"

        result_text, tool_calls, usage = _parse_codex_output(stdout, stderr, rc)
        assert result_text is not None, (
            f"Parser returned None result_text. stdout: {stdout[:500]}"
        )
        assert len(result_text) > 0, "Parser returned empty result_text"

    def test_token_usage_extracted(self):
        """Parser extracts non-None, positive token usage from real output."""
        stdout, stderr, rc = _run_codex("hello")
        assert rc == 0, f"codex failed: {stderr}"

        result_text, tool_calls, usage = _parse_codex_output(stdout, stderr, rc)
        assert usage is not None, (
            f"Parser returned None usage. stdout: {stdout[:500]}"
        )
        assert isinstance(usage["input_tokens"], int)
        assert isinstance(usage["output_tokens"], int)
        assert usage["input_tokens"] > 0, "input_tokens should be positive"
        assert usage["output_tokens"] > 0, "output_tokens should be positive"

    def test_tool_call_parsed_with_name_and_input(self):
        """Parser extracts tool calls with non-empty name and input."""
        stdout, stderr, rc = _run_codex(
            "Run echo parser-codex-test in the shell"
        )
        assert rc == 0, f"codex failed: {stderr}"

        result_text, tool_calls, usage = _parse_codex_output(stdout, stderr, rc)
        assert len(tool_calls) >= 1, (
            f"Expected at least 1 tool call. stdout: {stdout[:500]}"
        )
        tc = tool_calls[0]
        assert tc["name"], f"Tool call has empty name: {tc}"
        assert tc["id"], f"Tool call has empty id: {tc}"
        # command_execution tool calls have command in input
        assert tc["input"], f"Tool call has empty input: {tc}"


# ---------------------------------------------------------------------------
# Full adapter invoke() integration — end-to-end with real binary
# ---------------------------------------------------------------------------


class TestAdapterInvoke:
    """End-to-end test of CodexAdapter.invoke() with the real binary."""

    async def test_invoke_returns_text_and_usage(self):
        """Full invoke() returns non-empty text and positive token counts."""
        adapter = CodexAdapter()
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="hello",
            system_prompt="",
            mcp_servers={},
            env=dict(os.environ),
            timeout=120,
        )

        assert result_text is not None, "invoke() returned None result_text"
        assert len(result_text) > 0
        assert usage is not None, "invoke() returned None usage"
        assert usage["input_tokens"] > 0
        assert usage["output_tokens"] > 0

        # Process info should be populated
        info = adapter.last_process_info
        assert info is not None
        assert info["runtime_type"] == "codex"
        assert info["exit_code"] == 0
        assert info["pid"] is not None

    async def test_invoke_with_tool_use(self):
        """invoke() captures tool calls from real tool-using session."""
        adapter = CodexAdapter()
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="Run echo invoke-codex-test in the shell",
            system_prompt="",
            mcp_servers={},
            env=dict(os.environ),
            timeout=120,
        )

        assert len(tool_calls) >= 1, f"Expected tool calls, got: {tool_calls}"
        tc = tool_calls[0]
        assert tc["name"], f"Tool call missing name: {tc}"
        assert tc["id"], f"Tool call missing id: {tc}"
        assert tc["input"], f"Tool call missing input: {tc}"
