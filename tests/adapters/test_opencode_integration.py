"""Integration tests for OpenCode adapter — runs the real opencode binary.

These tests invoke the actual ``opencode run --format json`` CLI and verify that
the adapter's parser correctly extracts text, tool calls, and token usage from
real OpenCode output.

Marked ``nightly`` so they are excluded from default CI runs (addopts includes
``-m 'not nightly'``). Run explicitly with::

    uv run pytest tests/adapters/test_opencode_integration.py -m nightly -v

Requirements:
- ``opencode`` binary on PATH (``npm install -g opencode-ai``)
- Valid LLM API credentials in environment (e.g. OPENAI_API_KEY or ANTHROPIC_API_KEY)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from butlers.core.runtimes.opencode import (
    OpenCodeAdapter,
    _parse_opencode_output,
)

_opencode_available = shutil.which("opencode") is not None

pytestmark = [
    pytest.mark.nightly,
    pytest.mark.skipif(not _opencode_available, reason="opencode binary not on PATH"),
]


def _run_opencode(prompt: str, timeout: int = 120) -> tuple[str, str, int]:
    """Run ``opencode run --format json`` and return (stdout, stderr, returncode)."""
    result = subprocess.run(
        ["opencode", "run", "--format", "json", prompt],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd="/tmp",
    )
    return result.stdout, result.stderr, result.returncode


def _parse_raw_events(stdout: str) -> list[dict]:
    """Parse raw JSON-lines from opencode stdout into event dicts."""
    events = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            pass
    return events


# ---------------------------------------------------------------------------
# Raw output format verification — do events match expected envelope shape?
# ---------------------------------------------------------------------------


class TestOpenCodeOutputFormat:
    """Verify OpenCode v1.2+ JSON output matches the envelope format we parse."""

    def test_simple_text_response_produces_envelope_events(self):
        """Simple prompt produces envelope events with sessionID and part."""
        stdout, stderr, rc = _run_opencode("What is 2+2? Answer in one word.")
        assert rc == 0, f"opencode failed: {stderr}"

        events = _parse_raw_events(stdout)
        assert len(events) >= 2, f"Expected at least 2 events, got {len(events)}"

        # All events should have envelope structure
        for event in events:
            assert "type" in event, f"Event missing 'type': {event}"
            assert "sessionID" in event, f"Event missing 'sessionID': {event}"
            assert "part" in event, f"Event missing 'part': {event}"
            assert isinstance(event["part"], dict), f"'part' is not a dict: {event}"

    def test_text_event_has_part_text(self):
        """Text events store response text in part.text, not top-level."""
        stdout, stderr, rc = _run_opencode("Say the word 'hello' and nothing else.")
        assert rc == 0, f"opencode failed: {stderr}"

        events = _parse_raw_events(stdout)
        text_events = [e for e in events if e.get("type") == "text"]
        assert len(text_events) >= 1, f"No text events found in: {events}"

        for te in text_events:
            part = te["part"]
            assert "text" in part, f"text event part missing 'text': {part}"
            assert isinstance(part["text"], str)
            assert len(part["text"]) > 0
            # Verify text is NOT at top level (the bug we fixed)
            assert "text" not in te or te["text"] != part["text"] or "sessionID" in te

    def test_step_finish_has_tokens(self):
        """step_finish events contain token usage in part.tokens."""
        stdout, stderr, rc = _run_opencode("What is 1+1? One word answer.")
        assert rc == 0, f"opencode failed: {stderr}"

        events = _parse_raw_events(stdout)
        finish_events = [e for e in events if e.get("type") == "step_finish"]
        assert len(finish_events) >= 1, f"No step_finish events: {events}"

        for fe in finish_events:
            part = fe["part"]
            assert "tokens" in part, f"step_finish part missing 'tokens': {part}"
            tokens = part["tokens"]
            assert isinstance(tokens, dict)
            assert "input" in tokens, f"tokens missing 'input': {tokens}"
            assert "output" in tokens, f"tokens missing 'output': {tokens}"
            assert isinstance(tokens["input"], int)
            assert isinstance(tokens["output"], int)
            assert tokens["input"] > 0
            assert tokens["output"] > 0

    def test_tool_use_event_has_part_tool_and_state(self):
        """tool_use events store tool info in part.tool, part.callID, part.state."""
        stdout, stderr, rc = _run_opencode("Use the shell to run: echo 'integration-test-marker'")
        assert rc == 0, f"opencode failed: {stderr}"

        events = _parse_raw_events(stdout)
        tool_events = [e for e in events if e.get("type") == "tool_use"]
        assert len(tool_events) >= 1, (
            f"No tool_use events found. Events: {[e.get('type') for e in events]}"
        )

        te = tool_events[0]
        part = te["part"]
        assert "tool" in part, f"tool_use part missing 'tool': {part}"
        assert isinstance(part["tool"], str)
        assert len(part["tool"]) > 0
        assert "callID" in part, f"tool_use part missing 'callID': {part}"
        assert isinstance(part["callID"], str)
        assert "state" in part, f"tool_use part missing 'state': {part}"
        state = part["state"]
        assert isinstance(state, dict)
        assert "input" in state, f"tool_use state missing 'input': {state}"


# ---------------------------------------------------------------------------
# Parser integration — does _parse_opencode_output handle real output?
# ---------------------------------------------------------------------------


class TestParserWithRealOutput:
    """Feed real opencode output through the adapter parser and verify results."""

    def test_simple_text_parsed_correctly(self):
        """Parser extracts text from a simple text-only response."""
        stdout, stderr, rc = _run_opencode("What is 3+3? Answer in one word.")
        assert rc == 0, f"opencode failed: {stderr}"

        result_text, tool_calls, usage = _parse_opencode_output(stdout, stderr, rc)
        assert result_text is not None, "Parser returned None result_text"
        assert len(result_text) > 0, "Parser returned empty result_text"
        # The answer should contain "six" or "6"
        lower = result_text.lower()
        assert "six" in lower or "6" in lower, (
            f"Expected 'six' or '6' in result, got: {result_text}"
        )

    def test_token_usage_extracted(self):
        """Parser extracts non-None, positive token usage from real output."""
        stdout, stderr, rc = _run_opencode("Say 'yes'.")
        assert rc == 0, f"opencode failed: {stderr}"

        result_text, tool_calls, usage = _parse_opencode_output(stdout, stderr, rc)
        assert usage is not None, f"Parser returned None usage. stdout sample: {stdout[:500]}"
        assert isinstance(usage["input_tokens"], int)
        assert isinstance(usage["output_tokens"], int)
        assert usage["input_tokens"] > 0, "input_tokens should be positive"
        assert usage["output_tokens"] > 0, "output_tokens should be positive"

    def test_tool_call_parsed_with_name_and_input(self):
        """Parser extracts tool calls with non-empty name and input from real output."""
        stdout, stderr, rc = _run_opencode("Use the shell to run: echo 'parser-test-marker'")
        assert rc == 0, f"opencode failed: {stderr}"

        result_text, tool_calls, usage = _parse_opencode_output(stdout, stderr, rc)
        assert len(tool_calls) >= 1, f"Expected at least 1 tool call. stdout sample: {stdout[:500]}"
        tc = tool_calls[0]
        assert tc["name"], f"Tool call has empty name: {tc}"
        assert tc["id"], f"Tool call has empty id: {tc}"
        assert isinstance(tc["input"], dict), f"Tool call input is not dict: {tc}"
        assert len(tc["input"]) > 0, f"Tool call input is empty: {tc}"

    def test_multi_step_token_accumulation(self):
        """When multiple step_finish events exist, their tokens accumulate."""
        # Build synthetic multi-step output that mirrors real OpenCode format
        # to test accumulation without depending on LLM tool-use behavior.
        # (Real multi-step is tested via TestAdapterInvoke.test_invoke_with_tool_use)
        session_id = "ses_integration_test"
        ts = 1700000000000

        def env(etype: str, part: dict) -> str:
            return json.dumps(
                {
                    "type": etype,
                    "timestamp": ts,
                    "sessionID": session_id,
                    "part": part,
                }
            )

        stdout = "\n".join(
            [
                env("step_start", {"type": "step-start"}),
                env(
                    "tool_use",
                    {
                        "type": "tool",
                        "callID": "call_1",
                        "tool": "bash",
                        "state": {"status": "completed", "input": {"command": "ls"}},
                    },
                ),
                env(
                    "step_finish",
                    {
                        "type": "step-finish",
                        "reason": "tool-calls",
                        "tokens": {"total": 1000, "input": 800, "output": 200},
                    },
                ),
                env("step_start", {"type": "step-start"}),
                env("text", {"type": "text", "text": "Here are the files."}),
                env(
                    "step_finish",
                    {
                        "type": "step-finish",
                        "reason": "stop",
                        "tokens": {"total": 500, "input": 300, "output": 200},
                    },
                ),
            ]
        )

        result_text, tool_calls, usage = _parse_opencode_output(stdout, "", 0)

        assert result_text == "Here are the files."
        assert len(tool_calls) == 1
        assert tool_calls[0]["name"] == "bash"
        assert usage is not None
        assert usage["input_tokens"] == 800 + 300
        assert usage["output_tokens"] == 200 + 200


# ---------------------------------------------------------------------------
# Full adapter invoke() integration — end-to-end with real binary
# ---------------------------------------------------------------------------


class TestAdapterInvoke:
    """End-to-end test of OpenCodeAdapter.invoke() with the real binary."""

    async def test_invoke_returns_text_and_usage(self):
        """Full invoke() returns non-empty text and positive token counts."""
        adapter = OpenCodeAdapter()
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="What is 5+5? Answer in one word.",
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
        assert info["runtime_type"] == "opencode"
        assert info["exit_code"] == 0
        assert info["pid"] is not None

    async def test_invoke_with_tool_use(self):
        """invoke() captures tool calls from real tool-using session."""
        adapter = OpenCodeAdapter()
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="Use the shell to run: echo 'invoke-integration-test'",
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
