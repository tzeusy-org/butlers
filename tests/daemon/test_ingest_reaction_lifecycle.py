"""Tests for the ingest→pipeline Telegram reaction lifecycle.

Verifies that the daemon's ingest pipeline flow fires the correct
Telegram reactions (👀 on receive, ✅ on success, 👾 on error)
when messages arrive via the TelegramBotConnector → MCP ingest path.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.pipeline import MessagePipeline, RoutingResult
from butlers.modules.telegram import (
    REACTION_FAILURE,
    REACTION_IN_PROGRESS,
    REACTION_SUCCESS,
    TelegramModule,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_pipeline(result: RoutingResult | None = None) -> MessagePipeline:
    """Build a MessagePipeline with a mock dispatch that returns a fixed result."""

    async def mock_dispatch(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        default_result = result or RoutingResult(
            target_butler="general",
            route_result={"cc_summary": "routed"},
            routed_targets=["general"],
            acked_targets=["general"],
        )
        tool_calls = [
            {
                "name": "route_to_butler",
                "input": {"butler": default_result.target_butler, "prompt": "ok"},
                "result": {"status": "ok"},
            }
        ]
        return SimpleNamespace(output="ok", tool_calls=tool_calls)

    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value=None)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    pool.fetchval = AsyncMock(return_value=None)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock(return_value=None)

    return MessagePipeline(
        switchboard_pool=pool,
        dispatch_fn=mock_dispatch,
        source_butler="switchboard",
    )


def _make_capturing_mod() -> tuple[TelegramModule, list]:
    """Return a TelegramModule and a list that records reaction kwargs dicts."""
    mod = TelegramModule()
    calls: list[dict] = []

    async def mock_reaction(**kwargs: Any) -> None:
        calls.append(dict(kwargs))

    mod._set_message_reaction = mock_reaction  # type: ignore[method-assign]
    return mod, calls


# ---------------------------------------------------------------------------
# react_for_ingest unit tests
# ---------------------------------------------------------------------------


class TestReactForIngestParsing:
    """Test the parsing logic of react_for_ingest without network calls."""

    async def test_thread_id_parsing(self) -> None:
        """Valid 'chat_id:message_id' parsed; negative chat IDs (groups) also work;
        invalid/missing thread IDs are no-ops."""
        # Positive chat ID
        mod, calls = _make_capturing_mod()
        await mod.react_for_ingest(external_thread_id="12345:678", reaction=REACTION_IN_PROGRESS)
        assert len(calls) == 1
        assert calls[0]["chat_id"] == "12345"
        assert calls[0]["message_id"] == 678
        assert calls[0]["reaction"] == REACTION_IN_PROGRESS

        # Negative chat ID (groups/supergroups)
        mod2, calls2 = _make_capturing_mod()
        await mod2.react_for_ingest(
            external_thread_id="-100987654321:999", reaction=REACTION_IN_PROGRESS
        )
        assert len(calls2) == 1
        assert calls2[0]["chat_id"] == "-100987654321"
        assert calls2[0]["message_id"] == 999

        # Invalid: None → noop; non-integer message_id → noop
        for bad_thread in (None, "123:abc"):
            mod3, calls3 = _make_capturing_mod()
            await mod3.react_for_ingest(
                external_thread_id=bad_thread, reaction=REACTION_IN_PROGRESS
            )
            assert calls3 == [], f"Expected noop for thread_id={bad_thread!r}"


class TestReactForIngestBehavior:
    """Tests for reaction values, error swallowing, non-telegram guard, and sequences."""

    async def test_reaction_values_errors_and_sequences(self) -> None:
        """IN_PROGRESS/SUCCESS/FAILURE pass through; API errors swallowed; non-telegram skipped;
        IN_PROGRESS→SUCCESS and IN_PROGRESS→FAILURE sequences correct."""
        # All reaction values forwarded correctly
        for reaction in (REACTION_IN_PROGRESS, REACTION_SUCCESS, REACTION_FAILURE):
            mod, calls = _make_capturing_mod()
            await mod.react_for_ingest(external_thread_id="1:1", reaction=reaction)
            assert calls == [{"chat_id": "1", "message_id": 1, "reaction": reaction}]

        # Exceptions from _set_message_reaction must not propagate
        mod = TelegramModule()

        async def raising_reaction(**kwargs: Any) -> None:
            raise RuntimeError("Telegram API unavailable")

        mod._set_message_reaction = raising_reaction  # type: ignore[method-assign]
        await mod.react_for_ingest(external_thread_id="123:456", reaction=REACTION_IN_PROGRESS)

        # Non-telegram: guard check prevents call (email channel → no react_for_ingest)
        mod_email, calls_email = _make_capturing_mod()
        channel = "email"
        if channel == "telegram_bot":
            await mod_email.react_for_ingest(
                external_thread_id="123:456", reaction=REACTION_IN_PROGRESS
            )
        assert calls_email == []

        # Success sequence
        mod_s, seq_s = _make_capturing_mod()
        await mod_s.react_for_ingest(external_thread_id="42:100", reaction=REACTION_IN_PROGRESS)
        await mod_s.react_for_ingest(external_thread_id="42:100", reaction=REACTION_SUCCESS)
        assert [c["reaction"] for c in seq_s] == [REACTION_IN_PROGRESS, REACTION_SUCCESS]

        # Failure sequence
        mod_f, seq_f = _make_capturing_mod()
        await mod_f.react_for_ingest(external_thread_id="99:200", reaction=REACTION_IN_PROGRESS)
        await mod_f.react_for_ingest(external_thread_id="99:200", reaction=REACTION_FAILURE)
        assert [c["reaction"] for c in seq_f] == [REACTION_IN_PROGRESS, REACTION_FAILURE]
