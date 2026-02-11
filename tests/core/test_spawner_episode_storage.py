"""Tests for episode storage after CC session (butlers-cfw.7.3, butlers-cfw.7.4).

Covers:
- store_session_episode returns True on success
- store_session_episode returns False on connection error
- store_session_episode returns False on timeout
- store_session_episode returns False on error result
- store_session_episode sends correct arguments
- store_session_episode passes session_id when provided
- store_session_episode omits session_id when None
- Episode stored after successful session in spawner
- Episode NOT stored after failed session
- Episode storage failure doesn't block spawner return
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import ButlerConfig
from butlers.core.spawner import store_session_episode

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


def _patch_mcp_client(mock_client):
    """Return a patch context for MCPClient that yields mock_client from async with."""
    return patch(
        "butlers.core.spawner.MCPClient",
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_client),
            __aexit__=AsyncMock(return_value=False),
        ),
    )


# ---------------------------------------------------------------------------
# Tests for store_session_episode (standalone function)
# ---------------------------------------------------------------------------


class TestStoreSessionEpisode:
    """Tests for the store_session_episode helper function."""

    async def test_returns_true_on_success(self):
        """When Memory Butler responds successfully, return True."""
        mock_result = _make_call_tool_result()
        mock_client = _mock_mcp_client(call_tool_return=mock_result)

        with _patch_mcp_client(mock_client):
            result = await store_session_episode("my-butler", "session output text")

        assert result is True

    async def test_returns_false_on_connection_error(self):
        """When Memory Butler is unreachable, return False."""
        with patch(
            "butlers.core.spawner.MCPClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(side_effect=ConnectionError("Connection refused")),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            result = await store_session_episode("my-butler", "session output")

        assert result is False

    async def test_returns_false_on_timeout(self):
        """When the request times out, return False."""
        mock_client = _mock_mcp_client(call_tool_side_effect=TimeoutError("timed out"))

        with _patch_mcp_client(mock_client):
            result = await store_session_episode("my-butler", "session output")

        assert result is False

    async def test_returns_false_on_error_result(self):
        """When Memory Butler returns an error result, return False."""
        mock_result = _make_call_tool_result(is_error=True, text="internal error")
        mock_client = _mock_mcp_client(call_tool_return=mock_result)

        with _patch_mcp_client(mock_client):
            result = await store_session_episode("my-butler", "session output")

        assert result is False

    async def test_sends_correct_arguments(self):
        """Verify the correct arguments are passed to call_tool."""
        mock_result = _make_call_tool_result()
        mock_client = _mock_mcp_client(call_tool_return=mock_result)

        with _patch_mcp_client(mock_client):
            await store_session_episode("my-butler", "task completed successfully")

        mock_client.call_tool.assert_called_once_with(
            "memory_store_episode",
            {
                "content": "task completed successfully",
                "butler": "my-butler",
            },
        )

    async def test_passes_session_id_when_provided(self):
        """When session_id is given, it appears in the arguments."""
        mock_result = _make_call_tool_result()
        mock_client = _mock_mcp_client(call_tool_return=mock_result)
        sid = uuid.UUID("12345678-1234-5678-1234-567812345678")

        with _patch_mcp_client(mock_client):
            await store_session_episode("my-butler", "output text", session_id=sid)

        mock_client.call_tool.assert_called_once_with(
            "memory_store_episode",
            {
                "content": "output text",
                "butler": "my-butler",
                "session_id": "12345678-1234-5678-1234-567812345678",
            },
        )

    async def test_omits_session_id_when_none(self):
        """When session_id is None, it is not included in arguments."""
        mock_result = _make_call_tool_result()
        mock_client = _mock_mcp_client(call_tool_return=mock_result)

        with _patch_mcp_client(mock_client):
            await store_session_episode("my-butler", "output text", session_id=None)

        call_args = mock_client.call_tool.call_args
        arguments = call_args[0][1]  # second positional arg
        assert "session_id" not in arguments


# ---------------------------------------------------------------------------
# Integration tests: episode storage in Spawner._run
# ---------------------------------------------------------------------------


class TestSpawnerEpisodeStorageIntegration:
    """Tests for episode storage being called after successful CC sessions."""

    async def test_episode_stored_after_successful_session(self, tmp_path: Path):
        """After a successful session with output, store_session_episode is called."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        async def fake_sdk(*, prompt: str, options: Any):
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
                result="Task completed",
            )

        from butlers.core.spawner import Spawner

        spawner = Spawner(config=config, config_dir=config_dir, sdk_query=fake_sdk)

        with (
            patch(
                "butlers.core.spawner.fetch_memory_context",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.spawner.store_session_episode",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_store,
        ):
            result = await spawner.trigger(prompt="do task", trigger_source="trigger_tool")

        assert result.success is True
        mock_store.assert_called_once_with(
            "test-butler",
            "Task completed",
            session_id=None,
        )

    async def test_episode_not_stored_after_failed_session(self, tmp_path: Path):
        """After a failed session, store_session_episode is NOT called."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        async def failing_sdk(*, prompt: str, options: Any):
            raise RuntimeError("SDK failure")
            # Make it an async generator
            yield  # noqa: F841  # pragma: no cover

        from butlers.core.spawner import Spawner

        spawner = Spawner(config=config, config_dir=config_dir, sdk_query=failing_sdk)

        with (
            patch(
                "butlers.core.spawner.fetch_memory_context",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.spawner.store_session_episode",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_store,
        ):
            result = await spawner.trigger(prompt="do task", trigger_source="trigger_tool")

        assert result.success is False
        mock_store.assert_not_called()

    async def test_episode_storage_failure_does_not_block_return(self, tmp_path: Path):
        """If store_session_episode returns False, the spawner still returns."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        async def fake_sdk(*, prompt: str, options: Any):
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
                result="Task completed",
            )

        from butlers.core.spawner import Spawner

        spawner = Spawner(config=config, config_dir=config_dir, sdk_query=fake_sdk)

        with (
            patch(
                "butlers.core.spawner.fetch_memory_context",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.spawner.store_session_episode",
                new_callable=AsyncMock,
                return_value=False,  # Simulate failure
            ),
        ):
            result = await spawner.trigger(prompt="do task", trigger_source="trigger_tool")

        # Spawner should still return successfully despite episode storage failure
        assert result.success is True
        assert result.output == "Task completed"
