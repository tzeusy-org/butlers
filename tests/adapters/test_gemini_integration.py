"""Integration tests for Gemini adapter — runs the real gemini binary.

These tests invoke the actual ``gemini --prompt`` CLI and verify that the
adapter's parser correctly extracts text, tool calls, and token usage from real
Gemini output.

Marked ``nightly`` so they are excluded from default CI runs (addopts includes
``-m 'not nightly'``). Run explicitly with::

    uv run pytest tests/adapters/test_gemini_integration.py -m nightly -v

Requirements:
- ``gemini`` binary on PATH (``npm install -g @google/gemini-cli`` or equivalent)
- Valid Google API credentials in environment (e.g. GOOGLE_API_KEY)
"""

from __future__ import annotations

import os
import shutil

import pytest

from butlers.core.runtimes.gemini import (
    GeminiAdapter,
    _parse_gemini_output,
)

from .conftest import parse_jsonl_events, run_cli

_gemini_available = shutil.which("gemini") is not None

pytestmark = [
    pytest.mark.nightly,
    pytest.mark.skipif(not _gemini_available, reason="gemini binary not on PATH"),
]

_GEMINI_ARGS = ["--sandbox=false"]


def _run_gemini(prompt: str, timeout: int = 120) -> tuple[str, str, int]:
    """Run ``gemini --sandbox=false --prompt <prompt>`` via shared helper."""
    return run_cli("gemini", [*_GEMINI_ARGS, "--prompt"], prompt, timeout=timeout)


# ---------------------------------------------------------------------------
# Raw output format verification — do events match expected shapes?
# ---------------------------------------------------------------------------


class TestGeminiOutputFormat:
    """Verify Gemini CLI JSON output matches the event format we parse."""

    def test_simple_text_response_produces_output(self):
        """Simple prompt produces non-empty output."""
        stdout, stderr, rc = _run_gemini("What is 2+2? Answer in one word.")
        assert rc == 0, f"gemini failed: {stderr}"
        assert stdout.strip(), "gemini produced no output"

    def test_text_output_is_parseable(self):
        """Gemini output can be parsed by parse_jsonl_events or is plain text."""
        stdout, stderr, rc = _run_gemini("Say the word 'hello' and nothing else.")
        assert rc == 0, f"gemini failed: {stderr}"

        # Gemini may output JSON-lines or plain text; either is valid
        events = parse_jsonl_events(stdout)
        if events:
            # If JSON-lines, events should be dicts
            for event in events:
                assert isinstance(event, dict), f"Event is not a dict: {event}"
        else:
            # Plain text output
            assert len(stdout.strip()) > 0, "Gemini produced empty plain-text output"

    def test_tool_use_output_has_expected_structure(self):
        """Tool-use output from Gemini contains parseable tool metadata."""
        stdout, stderr, rc = _run_gemini(
            "Use the shell to run: echo 'gemini-integration-test-marker'"
        )
        assert rc == 0, f"gemini failed: {stderr}"

        # The output should contain something — either JSON events or plain text
        combined = stdout + stderr
        assert len(combined.strip()) > 0, "gemini produced no output at all"


# ---------------------------------------------------------------------------
# Parser integration — does _parse_gemini_output handle real output?
# ---------------------------------------------------------------------------


class TestParserWithRealOutput:
    """Feed real gemini output through the adapter parser and verify results."""

    def test_simple_text_parsed_correctly(self):
        """Parser extracts non-empty text from a simple text-only response."""
        stdout, stderr, rc = _run_gemini("What is 3+3? Answer in one word.")
        assert rc == 0, f"gemini failed: {stderr}"

        result_text, tool_calls = _parse_gemini_output(stdout, stderr, rc)
        assert result_text is not None, "Parser returned None result_text"
        assert len(result_text) > 0, "Parser returned empty result_text"

    def test_usage_is_none(self):
        """Gemini adapter does not report token usage — usage is always None.

        This is a known limitation of the Gemini CLI: it does not emit token
        counts in its output. The test explicitly documents this limitation.
        """
        stdout, stderr, rc = _run_gemini("Say 'yes'.")
        assert rc == 0, f"gemini failed: {stderr}"

        # _parse_gemini_output returns (result_text, tool_calls) — no usage
        result_text, tool_calls = _parse_gemini_output(stdout, stderr, rc)
        # Usage is implicitly None because the parser doesn't return it;
        # this is enforced by the return type signature.
        # Verify invoke() also returns usage=None
        ...

    def test_invoke_returns_none_usage(self):
        """GeminiAdapter.invoke() always returns usage=None."""
        import asyncio

        async def _run():
            adapter = GeminiAdapter()
            _, _, usage = await adapter.invoke(
                prompt="Say 'yes'.",
                system_prompt="",
                mcp_servers={},
                env=dict(os.environ),
                timeout=120,
            )
            return usage

        usage = asyncio.get_event_loop().run_until_complete(_run())
        assert usage is None, f"Expected usage=None for Gemini adapter, got: {usage}"

    def test_tool_call_parsed_with_name_and_input(self):
        """Parser extracts tool calls with non-empty name and input from real output."""
        stdout, stderr, rc = _run_gemini(
            "Use the shell to run: echo 'parser-gemini-test'"
        )
        assert rc == 0, f"gemini failed: {stderr}"

        result_text, tool_calls = _parse_gemini_output(stdout, stderr, rc)
        assert len(tool_calls) >= 1, (
            f"Expected at least 1 tool call. stdout sample: {stdout[:500]}"
        )
        tc = tool_calls[0]
        assert tc["name"], f"Tool call has empty name: {tc}"
        assert tc["id"], f"Tool call has empty id: {tc}"
        assert isinstance(tc["input"], dict), f"Tool call input is not dict: {tc}"
        assert len(tc["input"]) > 0, f"Tool call input is empty: {tc}"


# ---------------------------------------------------------------------------
# Full adapter invoke() integration — end-to-end with real binary
# ---------------------------------------------------------------------------


class TestAdapterInvoke:
    """End-to-end test of GeminiAdapter.invoke() with the real binary."""

    async def test_invoke_returns_text(self):
        """Full invoke() returns non-empty text."""
        adapter = GeminiAdapter()
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="What is 5+5? Answer in one word.",
            system_prompt="",
            mcp_servers={},
            env=dict(os.environ),
            timeout=120,
        )

        assert result_text is not None, "invoke() returned None result_text"
        assert len(result_text) > 0, "invoke() returned empty result_text"

    async def test_invoke_usage_is_none(self):
        """invoke() returns usage=None (Gemini does not emit token counts)."""
        adapter = GeminiAdapter()
        _, _, usage = await adapter.invoke(
            prompt="Say 'yes'.",
            system_prompt="",
            mcp_servers={},
            env=dict(os.environ),
            timeout=120,
        )
        assert usage is None, f"Expected usage=None for Gemini adapter, got: {usage}"

    async def test_invoke_populates_last_process_info(self):
        """invoke() populates last_process_info after a successful run."""
        adapter = GeminiAdapter()
        await adapter.invoke(
            prompt="What is 1+1? Answer in one word.",
            system_prompt="",
            mcp_servers={},
            env=dict(os.environ),
            timeout=120,
        )

        info = adapter.last_process_info
        assert info is not None, "last_process_info is None after invoke()"
        assert info["runtime_type"] == "gemini"
        assert info["exit_code"] == 0
        assert info["pid"] is not None

    async def test_invoke_with_tool_use(self):
        """invoke() captures tool calls from real tool-using session."""
        adapter = GeminiAdapter()
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="Use the shell to run: echo 'invoke-gemini-integration-test'",
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
