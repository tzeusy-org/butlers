"""Tests for the channel-level discretion bypass (bu-mm5vr).

DISCRETION_BYPASS_CHANNELS lists channels whose messages are always
operator-intentional (e.g. the dashboard, submitted directly by the owner).
Messages on those channels must skip the LLM discretion filter entirely and
always FORWARD; every other channel must still go through full discretion
evaluation unchanged.

Covers:
- Dashboard-channel message bypasses discretion (LLM never called).
- A non-bypass channel (telegram) still calls the LLM and honours its verdict.
- Omitting the channel preserves prior behaviour (full evaluation).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.connectors.discretion import (
    DISCRETION_BYPASS_CHANNELS,
    DiscretionEvaluator,
)

pytestmark = pytest.mark.unit


def _make_dispatcher(response: str = "IGNORE") -> AsyncMock:
    """A discretion LLM caller mock whose ``call`` returns *response*."""
    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(return_value=response)
    return dispatcher


def test_dashboard_channel_is_in_bypass_set() -> None:
    assert "dashboard" in DISCRETION_BYPASS_CHANNELS


async def test_dashboard_channel_bypasses_discretion() -> None:
    """A dashboard-channel message FORWARDs without ever calling the LLM."""
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(source_name="dash", dispatcher=dispatcher)

    # weight below fail_open would normally fail-closed (IGNORE) on the LLM
    # verdict; the channel bypass must override that and FORWARD.
    result = await evaluator.evaluate(text="please pay rent", weight=0.1, channel="dashboard")

    assert result.verdict == "FORWARD"
    assert result.reason == "channel-bypass"
    assert result.is_fail_open is False
    dispatcher.call.assert_not_called()


async def test_non_bypass_channel_runs_full_discretion() -> None:
    """A telegram-channel message still calls the LLM and honours its IGNORE."""
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(source_name="tg", dispatcher=dispatcher)

    # weight >= weight_fail_open (0.5) but < weight_bypass (1.0) → LLM is called.
    result = await evaluator.evaluate(text="ambient chatter", weight=0.7, channel="telegram")

    assert result.verdict == "IGNORE"
    dispatcher.call.assert_awaited_once()


async def test_omitted_channel_preserves_full_discretion() -> None:
    """No channel supplied → unchanged behaviour: the LLM is consulted."""
    dispatcher = _make_dispatcher(response="FORWARD: looks like a request")
    evaluator = DiscretionEvaluator(source_name="tg", dispatcher=dispatcher)

    result = await evaluator.evaluate(text="what's the weather?", weight=0.7)

    assert result.verdict == "FORWARD"
    assert result.reason == "looks like a request"
    dispatcher.call.assert_awaited_once()


async def test_non_bypass_channel_with_low_weight_still_fails_closed() -> None:
    """A non-bypass channel with a low-trust sender keeps fail-closed semantics."""
    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(side_effect=TimeoutError())
    evaluator = DiscretionEvaluator(source_name="tg", dispatcher=dispatcher)

    result = await evaluator.evaluate(text="spam", weight=0.1, channel="telegram")

    assert result.verdict == "IGNORE"
    assert result.is_fail_open is False
    dispatcher.call.assert_awaited_once()
