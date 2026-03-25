"""Integration tests for Claude Code adapter — invokes the real claude binary.

These tests call ClaudeCodeAdapter.invoke() against the live Anthropic API and
verify that the subprocess invocation, tool call extraction, and token usage
reporting all work correctly end-to-end.

Marked ``nightly`` so they are excluded from default CI runs (addopts includes
``-m 'not nightly'``). Run explicitly with::

    uv run pytest tests/adapters/test_claude_code_integration.py -m nightly -v

Requirements:
- ``ANTHROPIC_API_KEY`` environment variable set with a valid API key
- ``claude`` binary on PATH (claude-code CLI)
"""

from __future__ import annotations

import os

import pytest

from butlers.core.runtimes.claude_code import ClaudeCodeAdapter

_api_key_available = bool(os.environ.get("ANTHROPIC_API_KEY"))

pytestmark = [
    pytest.mark.nightly,
    pytest.mark.skipif(
        not _api_key_available,
        reason="ANTHROPIC_API_KEY not set in environment",
    ),
]


# ---------------------------------------------------------------------------
# Full subprocess invoke() integration — end-to-end with real Anthropic API
# ---------------------------------------------------------------------------


class TestAdapterInvoke:
    """End-to-end test of ClaudeCodeAdapter.invoke() via subprocess."""

    async def test_invoke_returns_text_and_usage(self):
        """Full invoke() returns non-empty text and positive token counts."""
        adapter = ClaudeCodeAdapter()
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="What is 5+5? Answer in one word.",
            system_prompt="",
            mcp_servers={},
            env=dict(os.environ),
            timeout=120,
        )

        assert result_text is not None, "invoke() returned None result_text"
        assert len(result_text) > 0, "invoke() returned empty result_text"
        assert usage is not None, "invoke() returned None usage"
        assert isinstance(usage.get("input_tokens"), int), (
            f"usage['input_tokens'] is not int: {usage}"
        )
        assert isinstance(usage.get("output_tokens"), int), (
            f"usage['output_tokens'] is not int: {usage}"
        )
        assert usage["input_tokens"] > 0, "input_tokens should be positive"
        assert usage["output_tokens"] > 0, "output_tokens should be positive"

    async def test_invoke_with_tool_call_extraction(self):
        """invoke() extracts tool_use records from tool-using sessions.

        Triggers a tool-using session and verifies that the tool_use block
        parsing produces tool call dicts with non-empty name, id, and input.
        """
        adapter = ClaudeCodeAdapter()
        result_text, tool_calls, usage = await adapter.invoke(
            prompt=(
                "Use the Bash tool to run the shell command: "
                "echo 'claude-code-invoke-integration-test'"
            ),
            system_prompt="",
            mcp_servers={},
            env=dict(os.environ),
            timeout=120,
        )

        assert len(tool_calls) >= 1, f"Expected at least 1 tool call, got: {tool_calls}"
        tc = tool_calls[0]
        assert tc["name"], f"Tool call has empty name: {tc}"
        assert tc["id"], f"Tool call has empty id: {tc}"
        assert isinstance(tc["input"], dict), f"Tool call input is not dict: {tc}"
        assert len(tc["input"]) > 0, f"Tool call input is empty: {tc}"

    async def test_invoke_token_usage_is_populated(self):
        """Token usage is populated with positive integers after invoke()."""
        adapter = ClaudeCodeAdapter()
        _, _, usage = await adapter.invoke(
            prompt="Say 'yes'.",
            system_prompt="",
            mcp_servers={},
            env=dict(os.environ),
            timeout=120,
        )

        assert usage is not None, "ClaudeCodeAdapter.invoke() returned None usage"
        assert usage["input_tokens"] > 0, "input_tokens should be positive"
        assert usage["output_tokens"] > 0, "output_tokens should be positive"

    async def test_invoke_no_system_prompt(self):
        """invoke() works correctly when system_prompt is empty."""
        adapter = ClaudeCodeAdapter()
        result_text, tool_calls, usage = await adapter.invoke(
            prompt="What is 2+2? Answer in one word.",
            system_prompt="",
            mcp_servers={},
            env=dict(os.environ),
            timeout=120,
        )

        assert result_text is not None, "invoke() returned None result_text"
        assert len(result_text) > 0, "invoke() returned empty result_text"

    async def test_invoke_populates_last_process_info(self):
        """last_process_info is populated with subprocess metadata after invoke()."""
        adapter = ClaudeCodeAdapter()
        await adapter.invoke(
            prompt="Say 'hello'.",
            system_prompt="",
            mcp_servers={},
            env=dict(os.environ),
            timeout=120,
        )

        info = adapter.last_process_info
        assert info is not None, "last_process_info is None after invoke()"
        assert isinstance(info.get("pid"), int), f"pid is not int: {info}"
        assert info.get("exit_code") == 0, f"exit_code is not 0: {info}"
        assert info.get("runtime_type") == "claude", f"runtime_type mismatch: {info}"
