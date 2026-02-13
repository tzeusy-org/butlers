"""Tests for memory context injection in Spawner (butlers-cfw.7.1, butlers-cfw.7.2).

Covers:
- fetch_memory_context returns context on success
- fetch_memory_context returns None on connection error
- fetch_memory_context returns None on timeout
- fetch_memory_context returns None on error result
- fetch_memory_context returns None on empty content
- Memory context is appended to system prompt when available
- Spawner works without memory context when Memory Butler is unreachable
- Timeout on memory context fetch doesn't block spawner
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import ButlerConfig
from butlers.core.spawner import fetch_memory_context

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    name: str = "test-butler",
    port: int = 9100,
) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        env_required=[],
        env_optional=[],
    )


def _make_call_tool_result(*, is_error: bool = False, text: str | None = None):
    """Create a mock CallToolResult."""
    result = MagicMock()
    result.is_error = is_error
    if text is not None:
        block = MagicMock()
        block.text = text
        result.content = [block]
    else:
        result.content = []
    return result


def _mock_mcp_client(*, call_tool_return=None, call_tool_side_effect=None):
    """Create a mock MCPClient preconfigured for async context manager use."""
    mock_client = AsyncMock()
    if call_tool_side_effect is not None:
        mock_client.call_tool.side_effect = call_tool_side_effect
    else:
        mock_client.call_tool.return_value = call_tool_return
    return mock_client


# ---------------------------------------------------------------------------
# Tests for fetch_memory_context (standalone function)
# ---------------------------------------------------------------------------


class TestFetchMemoryContext:
    """Tests for the fetch_memory_context helper function."""

    @pytest.fixture(autouse=True)
    async def _reset_memory_client_cache(self):
        from butlers.core.spawner import _reset_memory_client_cache_for_tests

        await _reset_memory_client_cache_for_tests()
        yield
        await _reset_memory_client_cache_for_tests()

    async def test_returns_context_on_success(self):
        """When Memory Butler responds with valid content, return it."""
        mock_result = _make_call_tool_result(text="You previously discussed project deadlines.")
        mock_client = _mock_mcp_client(call_tool_return=mock_result)

        with patch(
            "butlers.core.spawner.MCPClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_client),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            result = await fetch_memory_context("my-butler", "hello world")

        assert result == "You previously discussed project deadlines."
        mock_client.call_tool.assert_called_once_with(
            "memory_context",
            {"trigger_prompt": "hello world", "butler": "my-butler"},
        )

    async def test_reuses_cached_client_across_consecutive_calls(self):
        """Consecutive context lookups should reuse a healthy cached client."""
        first_result = _make_call_tool_result(text="ctx-1")
        second_result = _make_call_tool_result(text="ctx-2")
        mock_client = _mock_mcp_client(call_tool_side_effect=[first_result, second_result])

        mock_ctor = MagicMock()
        mock_ctx = mock_ctor.return_value
        mock_ctx.is_connected = MagicMock(return_value=True)
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("butlers.core.spawner.MCPClient", mock_ctor):
            first = await fetch_memory_context("my-butler", "hello 1")
            second = await fetch_memory_context("my-butler", "hello 2")

        assert first == "ctx-1"
        assert second == "ctx-2"
        mock_ctor.assert_called_once_with("http://localhost:8150/sse", name="spawner-memory")
        assert mock_client.call_tool.await_count == 2

    async def test_reconnects_when_cached_client_is_disconnected(self):
        """Disconnected cached clients should be replaced before the next call."""
        first_client = _mock_mcp_client(call_tool_return=_make_call_tool_result(text="ctx-1"))
        second_client = _mock_mcp_client(call_tool_return=_make_call_tool_result(text="ctx-2"))

        first_ctx = MagicMock()
        first_ctx.is_connected = MagicMock(return_value=True)
        first_ctx.__aenter__ = AsyncMock(return_value=first_client)
        first_ctx.__aexit__ = AsyncMock(return_value=False)
        second_ctx = MagicMock()
        second_ctx.is_connected = MagicMock(return_value=True)
        second_ctx.__aenter__ = AsyncMock(return_value=second_client)
        second_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "butlers.core.spawner.MCPClient",
            MagicMock(side_effect=[first_ctx, second_ctx]),
        ):
            first = await fetch_memory_context("my-butler", "hello 1")
            first_ctx.is_connected.return_value = False
            second = await fetch_memory_context("my-butler", "hello 2")

        assert first == "ctx-1"
        assert second == "ctx-2"
        first_ctx.__aexit__.assert_awaited_once()

    async def test_returns_none_on_connection_error(self):
        """When Memory Butler is unreachable, return None."""
        with patch(
            "butlers.core.spawner.MCPClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(side_effect=ConnectionError("Connection refused")),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            result = await fetch_memory_context("my-butler", "hello")

        assert result is None

    async def test_returns_none_on_timeout(self):
        """When the request times out, return None."""
        mock_client = _mock_mcp_client(call_tool_side_effect=TimeoutError("timed out"))

        with patch(
            "butlers.core.spawner.MCPClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_client),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            result = await fetch_memory_context("my-butler", "hello")

        assert result is None

    async def test_returns_none_on_error_result(self):
        """When Memory Butler returns an error result, return None."""
        mock_result = _make_call_tool_result(is_error=True, text="internal error")
        mock_client = _mock_mcp_client(call_tool_return=mock_result)

        with patch(
            "butlers.core.spawner.MCPClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_client),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            result = await fetch_memory_context("my-butler", "hello")

        assert result is None

    async def test_returns_none_on_empty_content(self):
        """When content is empty, return None."""
        mock_result = _make_call_tool_result(text="")
        mock_client = _mock_mcp_client(call_tool_return=mock_result)

        with patch(
            "butlers.core.spawner.MCPClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_client),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            result = await fetch_memory_context("my-butler", "hello")

        assert result is None

    async def test_returns_none_on_no_content_blocks(self):
        """When result has no content blocks, return None."""
        mock_result = _make_call_tool_result()  # empty content list
        mock_client = _mock_mcp_client(call_tool_return=mock_result)

        with patch(
            "butlers.core.spawner.MCPClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_client),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            result = await fetch_memory_context("my-butler", "hello")

        assert result is None

    async def test_custom_port(self):
        """Verify the custom port parameter is used in the SSE URL."""
        mock_result = _make_call_tool_result(text="context from port 9999")
        mock_client = _mock_mcp_client(call_tool_return=mock_result)

        mock_constructor = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_client),
                __aexit__=AsyncMock(return_value=False),
            ),
        )

        with patch("butlers.core.spawner.MCPClient", mock_constructor):
            result = await fetch_memory_context("my-butler", "hello", memory_butler_port=9999)

        assert result == "context from port 9999"
        mock_constructor.assert_called_once_with("http://localhost:9999/sse", name="spawner-memory")


# ---------------------------------------------------------------------------
# Integration tests: memory context injection in Spawner._run
# ---------------------------------------------------------------------------


class TestSpawnerMemoryContextInjection:
    """Tests for memory context being injected into the system prompt."""

    async def test_memory_context_injected_into_system_prompt(self, tmp_path: Path):
        """When memory context is available, it is appended to system prompt."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        captured_system_prompt: str | None = None

        async def capturing_sdk(*, prompt: str, options: Any):
            nonlocal captured_system_prompt
            captured_system_prompt = getattr(options, "system_prompt", None)
            from claude_code_sdk import ResultMessage

            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.005,
                usage={},
                result="Done",
            )

        from butlers.core.spawner import Spawner

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            sdk_query=capturing_sdk,
        )

        memory_text = "Remembered: user prefers concise answers."
        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value=memory_text,
        ):
            await spawner.trigger(prompt="do task", trigger_source="trigger_tool")

        # The system prompt should end with the memory context
        assert captured_system_prompt is not None
        assert captured_system_prompt.endswith(memory_text)
        # And the memory context should be separated by double newline
        assert f"\n\n{memory_text}" in captured_system_prompt

    async def test_spawner_works_without_memory_context(self, tmp_path: Path):
        """When memory context is None, spawner works normally."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        captured_system_prompt: str | None = None

        async def capturing_sdk(*, prompt: str, options: Any):
            nonlocal captured_system_prompt
            captured_system_prompt = getattr(options, "system_prompt", None)
            from claude_code_sdk import ResultMessage

            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.005,
                usage={},
                result="Done",
            )

        from butlers.core.spawner import Spawner

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            sdk_query=capturing_sdk,
        )

        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await spawner.trigger(prompt="do task", trigger_source="trigger_tool")

        assert result.success is True
        # System prompt should not contain memory context separator pattern
        # (it should just be the base system prompt)
        assert captured_system_prompt is not None

    async def test_memory_context_appended_after_system_prompt(self, tmp_path: Path):
        """Memory context comes after the original system prompt content."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # Write a CLAUDE.md so we know the base system prompt content
        (config_dir / "CLAUDE.md").write_text("Base system instructions.")
        config = _make_config()

        captured_system_prompt: str | None = None

        async def capturing_sdk(*, prompt: str, options: Any):
            nonlocal captured_system_prompt
            captured_system_prompt = getattr(options, "system_prompt", None)
            from claude_code_sdk import ResultMessage

            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.005,
                usage={},
                result="Done",
            )

        from butlers.core.spawner import Spawner

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            sdk_query=capturing_sdk,
        )

        memory_text = "Memory: user is working on project X."
        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value=memory_text,
        ):
            await spawner.trigger(prompt="do task", trigger_source="trigger_tool")

        assert captured_system_prompt is not None
        # Base system prompt comes first
        assert captured_system_prompt.startswith("Base system instructions.")
        # Memory context comes after
        assert captured_system_prompt.endswith(memory_text)
        # Separated by double newline
        parts = captured_system_prompt.split("\n\n")
        assert len(parts) >= 2
        assert parts[-1] == memory_text

    async def test_timeout_does_not_block_spawner(self, tmp_path: Path):
        """Spawner proceeds normally even when memory fetch times out."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        invoked = False

        async def capturing_sdk(*, prompt: str, options: Any):
            nonlocal invoked
            invoked = True
            from claude_code_sdk import ResultMessage

            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.005,
                usage={},
                result="Done",
            )

        from butlers.core.spawner import Spawner

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            sdk_query=capturing_sdk,
        )

        # Simulate a timeout in fetch_memory_context (returns None)
        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await spawner.trigger(prompt="do task", trigger_source="trigger_tool")

        assert result.success is True
        assert invoked is True
