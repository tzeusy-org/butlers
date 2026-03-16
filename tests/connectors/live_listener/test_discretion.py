"""Tests for the live-listener discretion layer.

Covers:
  - Sliding context window management (size cap, age cap, both simultaneously)
  - Discretion LLM verdict parsing (FORWARD with/without reason, IGNORE, malformed)
  - Fail-open behaviour: timeout → FORWARD, error → FORWARD, parse error → FORWARD
  - Full evaluator flow via DiscretionEvaluator (happy path + failure paths)
  - MockDispatcher replaces direct LLM calls for all evaluator tests
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.connectors.discretion import (
    ContextEntry,
    ContextWindow,
    DiscretionEvaluator,
    DiscretionLLMCaller,
    _build_user_prompt,
    _parse_verdict,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# MockDispatcher — implements DiscretionLLMCaller for tests
# ---------------------------------------------------------------------------


class MockDispatcher:
    """Lightweight mock implementing DiscretionLLMCaller.

    Wraps an ``AsyncMock`` so test code can control return values and
    side_effects while still satisfying the protocol.
    """

    def __init__(
        self,
        return_value: str = "FORWARD: ok",
        side_effect: Any = None,
    ) -> None:
        self._mock = AsyncMock(return_value=return_value, side_effect=side_effect)

    async def call(self, prompt: str, system_prompt: str = "") -> str:
        return await self._mock(prompt, system_prompt=system_prompt)

    @property
    def mock(self) -> AsyncMock:
        """Access the underlying AsyncMock for assertions."""
        return self._mock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(text: str, *, mic: str = "kitchen", age_s: float = 0.0) -> ContextEntry:
    """Create a ContextEntry with a timestamp offset by ``age_s`` seconds in the past."""
    return ContextEntry(text=text, timestamp=time.time() - age_s, source=mic)


def _make_evaluator(
    source_name: str = "kitchen",
    *,
    return_value: str = "IGNORE",
    side_effect: Any = None,
    window_size: int = 10,
    window_seconds: float = 300.0,
    weight_bypass: float = 1.0,
    weight_fail_open: float = 0.5,
) -> tuple[DiscretionEvaluator, MockDispatcher]:
    """Return an evaluator + its mock dispatcher."""
    dispatcher = MockDispatcher(return_value=return_value, side_effect=side_effect)
    evaluator = DiscretionEvaluator(
        source_name=source_name,
        dispatcher=dispatcher,
        window_size=window_size,
        window_seconds=window_seconds,
        weight_bypass=weight_bypass,
        weight_fail_open=weight_fail_open,
    )
    return evaluator, dispatcher


# ---------------------------------------------------------------------------
# ContextWindow — window management
# ---------------------------------------------------------------------------


class TestContextWindow:
    def test_empty_window_has_zero_length(self) -> None:
        w = ContextWindow()
        assert len(w) == 0
        assert w.entries == []

    def test_append_single_entry(self) -> None:
        w = ContextWindow()
        e = _make_entry("hello")
        w.append(e)
        assert len(w) == 1
        assert w.entries[0] is e

    def test_size_cap_enforced(self) -> None:
        w = ContextWindow(max_size=3)
        for i in range(5):
            w.append(_make_entry(f"utterance {i}"))
        assert len(w) == 3
        # Most recent three are kept.
        texts = [e.text for e in w.entries]
        assert texts == ["utterance 2", "utterance 3", "utterance 4"]

    def test_age_cap_removes_old_entries(self) -> None:
        w = ContextWindow(max_size=100, max_age_seconds=60.0)
        # Two old entries (70 s ago) and two fresh entries.
        w.append(_make_entry("old 1", age_s=70.0))
        w.append(_make_entry("old 2", age_s=70.0))
        w.append(_make_entry("fresh 1", age_s=1.0))
        w.append(_make_entry("fresh 2", age_s=1.0))
        entries = w.entries
        assert len(entries) == 2
        assert all("fresh" in e.text for e in entries)

    def test_both_caps_applied_simultaneously(self) -> None:
        """Size cap AND age cap should both trim simultaneously.

        Window: max_size=3, max_age=60 s.
        We add 5 entries: 3 within age window, 2 expired.
        After trimming: age removes 2 → 3 remain; size cap keeps 3 → still 3.
        """
        w = ContextWindow(max_size=3, max_age_seconds=60.0)
        w.append(_make_entry("expired 1", age_s=120.0))
        w.append(_make_entry("expired 2", age_s=90.0))
        w.append(_make_entry("keep 1", age_s=30.0))
        w.append(_make_entry("keep 2", age_s=20.0))
        w.append(_make_entry("keep 3", age_s=10.0))
        entries = w.entries
        assert len(entries) == 3
        assert all("keep" in e.text for e in entries)

    def test_age_cap_wins_over_size_cap(self) -> None:
        """When age removes more entries than size cap would, age wins.

        max_size=5, max_age=10s.
        We add 4 entries: 3 expired (> 10 s), 1 fresh.
        After age trim: 1 remains.  Size cap (5) doesn't further reduce.
        """
        w = ContextWindow(max_size=5, max_age_seconds=10.0)
        w.append(_make_entry("exp 1", age_s=30.0))
        w.append(_make_entry("exp 2", age_s=20.0))
        w.append(_make_entry("exp 3", age_s=15.0))
        w.append(_make_entry("fresh", age_s=1.0))
        assert len(w) == 1
        assert w.entries[0].text == "fresh"

    def test_size_cap_wins_over_age_cap(self) -> None:
        """When size cap removes more entries than age would, size cap wins.

        max_size=2, max_age=1000s (effectively infinite).
        We add 4 entries, all fresh.
        Size cap should keep only 2.
        """
        w = ContextWindow(max_size=2, max_age_seconds=1000.0)
        for i in range(4):
            w.append(_make_entry(f"u{i}", age_s=1.0))
        assert len(w) == 2
        texts = [e.text for e in w.entries]
        assert texts == ["u2", "u3"]

    def test_entries_returns_copy(self) -> None:
        """Mutating the returned list must not affect the internal state."""
        w = ContextWindow(max_size=5)
        w.append(_make_entry("a"))
        snapshot = w.entries
        snapshot.clear()
        assert len(w) == 1


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------


class TestParseVerdict:
    def test_forward_with_reason(self) -> None:
        verdict, reason = _parse_verdict("FORWARD: this sounds like a request")
        assert verdict == "FORWARD"
        assert reason == "this sounds like a request"

    def test_forward_without_reason(self) -> None:
        verdict, reason = _parse_verdict("FORWARD")
        assert verdict == "FORWARD"
        assert reason == ""

    def test_forward_with_extra_whitespace(self) -> None:
        verdict, reason = _parse_verdict("  FORWARD :  set a timer  ")
        assert verdict == "FORWARD"
        assert reason == "set a timer"

    def test_ignore(self) -> None:
        verdict, reason = _parse_verdict("IGNORE")
        assert verdict == "IGNORE"
        assert reason == ""

    def test_ignore_with_trailing_reason(self) -> None:
        """Spec says IGNORE has no reason; any trailing content is discarded."""
        verdict, reason = _parse_verdict("IGNORE: background chatter")
        assert verdict == "IGNORE"
        assert reason == ""

    def test_ignore_case_insensitive(self) -> None:
        verdict, _ = _parse_verdict("ignore")
        assert verdict == "IGNORE"

    def test_forward_case_insensitive(self) -> None:
        verdict, reason = _parse_verdict("forward: hello")
        assert verdict == "FORWARD"
        assert reason == "hello"

    def test_malformed_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unrecognisable"):
            _parse_verdict("MAYBE: not sure")

    def test_empty_response_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            _parse_verdict("")


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


class TestBuildUserPrompt:
    def test_empty_context_shows_none(self) -> None:
        utterance = _make_entry("what time is it?")
        prompt = _build_user_prompt([], utterance)
        assert "(none)" in prompt
        assert "what time is it?" in prompt

    def test_context_entries_are_numbered(self) -> None:
        context = [_make_entry("hello"), _make_entry("how are you")]
        utterance = _make_entry("set a timer")
        prompt = _build_user_prompt(context, utterance)
        assert "[1]" in prompt
        assert "[2]" in prompt
        assert "set a timer" in prompt

    def test_mic_name_appears_in_prompt(self) -> None:
        utterance = _make_entry("hey", mic="bedroom")
        prompt = _build_user_prompt([], utterance)
        assert "bedroom" in prompt


# ---------------------------------------------------------------------------
# DiscretionLLMCaller protocol
# ---------------------------------------------------------------------------


class TestDiscretionLLMCallerProtocol:
    def test_mock_dispatcher_satisfies_protocol(self) -> None:
        """MockDispatcher must satisfy the DiscretionLLMCaller runtime-checkable protocol."""
        dispatcher = MockDispatcher()
        assert isinstance(dispatcher, DiscretionLLMCaller)


# ---------------------------------------------------------------------------
# DiscretionEvaluator — happy path
# ---------------------------------------------------------------------------


class TestDiscretionEvaluatorHappyPath:
    async def test_forward_verdict_returned(self) -> None:
        evaluator, _ = _make_evaluator(
            return_value="FORWARD: sounds like a direct request",
        )
        result = await evaluator.evaluate("Hey, turn off the lights", weight=0.7)
        assert result.verdict == "FORWARD"
        assert "direct request" in result.reason
        assert result.is_fail_open is False

    async def test_ignore_verdict_returned(self) -> None:
        evaluator, _ = _make_evaluator(return_value="IGNORE")
        result = await evaluator.evaluate("Yeah I know right, it was crazy", weight=0.7)
        assert result.verdict == "IGNORE"
        assert result.reason == ""
        assert result.is_fail_open is False

    async def test_utterance_appended_to_window_before_next_call(self) -> None:
        """After evaluate(), the utterance should appear in the context window."""
        evaluator, _ = _make_evaluator(return_value="IGNORE")
        await evaluator.evaluate("first utterance", weight=0.7)
        await evaluator.evaluate("second utterance", weight=0.7)

        entries = evaluator.window.entries
        assert len(entries) == 2
        assert entries[0].text == "first utterance"
        assert entries[1].text == "second utterance"

    async def test_context_passed_to_llm_excludes_current_utterance(self) -> None:
        """The prompt context should contain only the *previous* window entries."""
        captured_prompts: list[str] = []

        async def capture_call(prompt: str, *, system_prompt: str = "") -> str:
            captured_prompts.append(prompt)
            return "IGNORE"

        dispatcher = MockDispatcher()
        dispatcher._mock.side_effect = capture_call
        evaluator = DiscretionEvaluator(
            source_name="kitchen",
            dispatcher=dispatcher,
            weight_bypass=1.0,
            weight_fail_open=0.5,
        )

        await evaluator.evaluate("first", weight=0.7)
        await evaluator.evaluate("second", weight=0.7)

        # First call: context window was empty.
        assert "(none)" in captured_prompts[0]
        assert "first" in captured_prompts[0]

        # Second call: context contains "first"; new utterance is "second".
        assert "first" in captured_prompts[1]
        assert "second" in captured_prompts[1]
        # "second" should appear as the *new utterance* line, not as a numbered context entry.
        # Verify "second" is mentioned after the context block.
        second_prompt = captured_prompts[1]
        context_end = second_prompt.index("## New message")
        # "first" should appear before the separator; "second" after.
        assert second_prompt.index("first") < context_end


# ---------------------------------------------------------------------------
# DiscretionEvaluator — fail-open behaviour
# ---------------------------------------------------------------------------


class TestDiscretionEvaluatorFailOpen:
    async def test_timeout_yields_forward(self) -> None:
        async def slow_call(prompt: str, *, system_prompt: str = "") -> str:
            await asyncio.sleep(10)  # Will never complete in test
            return "IGNORE"

        dispatcher = MockDispatcher()
        dispatcher._mock.side_effect = TimeoutError()
        evaluator = DiscretionEvaluator(
            source_name="kitchen",
            dispatcher=dispatcher,
            weight_bypass=1.0,
            weight_fail_open=0.5,
        )

        result = await evaluator.evaluate("any text", weight=0.7)

        assert result.verdict == "FORWARD"
        assert result.is_fail_open is True
        assert "timeout" in result.reason

    async def test_error_yields_forward(self) -> None:
        evaluator, _ = _make_evaluator(
            side_effect=RuntimeError("connection refused"),
        )
        result = await evaluator.evaluate("any text", weight=0.7)

        assert result.verdict == "FORWARD"
        assert result.is_fail_open is True
        assert "RuntimeError" in result.reason

    async def test_malformed_response_yields_forward(self) -> None:
        evaluator, _ = _make_evaluator(return_value="MAYBE: not sure")
        result = await evaluator.evaluate("any text", weight=0.7)

        assert result.verdict == "FORWARD"
        assert result.is_fail_open is True
        assert "parse_error" in result.reason

    async def test_unexpected_exception_yields_forward(self) -> None:
        evaluator, _ = _make_evaluator(
            side_effect=RuntimeError("something unexpected"),
        )
        result = await evaluator.evaluate("any text", weight=0.7)

        assert result.verdict == "FORWARD"
        assert result.is_fail_open is True
        assert "RuntimeError" in result.reason

    async def test_window_still_updated_on_failure(self) -> None:
        """Even when LLM fails, the utterance is added to the context window."""
        evaluator, _ = _make_evaluator(side_effect=RuntimeError("timeout"))
        await evaluator.evaluate("hello", weight=0.7)

        assert len(evaluator.window) == 1
        assert evaluator.window.entries[0].text == "hello"


# ---------------------------------------------------------------------------
# DiscretionEvaluator — window integration
# ---------------------------------------------------------------------------


class TestDiscretionEvaluatorWindowIntegration:
    async def test_window_respects_size_cap(self) -> None:
        evaluator, _ = _make_evaluator(
            return_value="IGNORE",
            window_size=3,
        )
        for i in range(5):
            await evaluator.evaluate(f"utterance {i}")

        assert len(evaluator.window) == 3
        texts = [e.text for e in evaluator.window.entries]
        assert texts == ["utterance 2", "utterance 3", "utterance 4"]

    async def test_mic_name_stored_in_window(self) -> None:
        evaluator, _ = _make_evaluator(source_name="bedroom", return_value="FORWARD: ok")
        await evaluator.evaluate("good morning")

        assert evaluator.window.entries[0].source == "bedroom"

    async def test_timestamp_stored_in_window(self) -> None:
        evaluator, _ = _make_evaluator(return_value="IGNORE")
        before = time.time()
        await evaluator.evaluate("any text", timestamp=before + 1.0)

        assert evaluator.window.entries[0].timestamp == pytest.approx(before + 1.0, abs=0.01)


# ---------------------------------------------------------------------------
# DiscretionEvaluator — weight-based behaviour
# ---------------------------------------------------------------------------


class TestDiscretionEvaluatorWeight:
    async def test_weight_bypass_skips_llm(self) -> None:
        """weight >= weight_bypass should return FORWARD without calling LLM."""
        evaluator, dispatcher = _make_evaluator(
            return_value="IGNORE",
            weight_bypass=1.0,
            weight_fail_open=0.5,
        )
        result = await evaluator.evaluate("any text", weight=1.0)

        assert result.verdict == "FORWARD"
        assert result.reason == "weight-bypass"
        assert result.is_fail_open is False
        dispatcher.mock.assert_not_called()

    async def test_weight_bypass_still_appends_to_window(self) -> None:
        """Bypassed messages should still appear in context window."""
        evaluator, _ = _make_evaluator(
            return_value="IGNORE",
            weight_bypass=1.0,
            weight_fail_open=0.5,
        )
        await evaluator.evaluate("owner says hello", weight=1.0)
        await evaluator.evaluate("stranger says hi", weight=0.3)

        assert len(evaluator.window) == 2
        assert evaluator.window.entries[0].text == "owner says hello"

    async def test_high_weight_fails_open(self) -> None:
        """weight >= weight_fail_open should fail-open (FORWARD) on errors."""
        evaluator, _ = _make_evaluator(
            side_effect=RuntimeError("connection refused"),
            weight_bypass=1.0,
            weight_fail_open=0.5,
        )
        result = await evaluator.evaluate("any text", weight=0.7)

        assert result.verdict == "FORWARD"
        assert result.is_fail_open is True
        assert "fail-open" in result.reason

    async def test_low_weight_fails_closed(self) -> None:
        """weight < weight_fail_open should fail-closed (IGNORE) on errors."""
        evaluator, _ = _make_evaluator(
            side_effect=RuntimeError("connection refused"),
            weight_bypass=1.0,
            weight_fail_open=0.5,
        )
        result = await evaluator.evaluate("any text", weight=0.3)

        assert result.verdict == "IGNORE"
        assert result.is_fail_open is False
        assert "fail-closed" in result.reason

    async def test_low_weight_timeout_fails_closed(self) -> None:
        """Timeout with low weight should fail-closed."""
        evaluator, _ = _make_evaluator(
            side_effect=TimeoutError(),
            weight_bypass=1.0,
            weight_fail_open=0.5,
        )
        result = await evaluator.evaluate("any text", weight=0.3)

        assert result.verdict == "IGNORE"
        assert "fail-closed" in result.reason
        assert "timeout" in result.reason

    async def test_low_weight_parse_error_fails_closed(self) -> None:
        """Parse error with low weight should fail-closed."""
        evaluator, _ = _make_evaluator(
            return_value="MAYBE: not sure",
            weight_bypass=1.0,
            weight_fail_open=0.5,
        )
        result = await evaluator.evaluate("any text", weight=0.3)

        assert result.verdict == "IGNORE"
        assert "fail-closed" in result.reason
        assert "parse_error" in result.reason

    async def test_low_weight_normal_verdict_still_honored(self) -> None:
        """When LLM succeeds, weight doesn't override the verdict."""
        evaluator, _ = _make_evaluator(return_value="FORWARD: urgent request")
        result = await evaluator.evaluate("help!", weight=0.3)

        assert result.verdict == "FORWARD"
        assert result.is_fail_open is False


# ---------------------------------------------------------------------------
# ContactWeightResolver
# ---------------------------------------------------------------------------


class TestContactWeightResolver:
    @pytest.fixture()
    def mock_pool(self) -> AsyncMock:
        return AsyncMock()

    async def test_owner_gets_weight_1(self, mock_pool: AsyncMock) -> None:
        from butlers.connectors.discretion import ContactWeightResolver

        mock_pool.fetchrow.return_value = {"roles": ["owner"]}
        resolver = ContactWeightResolver(mock_pool)
        weight = await resolver.resolve("telegram", "123")
        assert weight == 1.0

    async def test_family_gets_inner_circle_weight(self, mock_pool: AsyncMock) -> None:
        from butlers.connectors.discretion import ContactWeightResolver

        mock_pool.fetchrow.return_value = {"roles": ["family"]}
        resolver = ContactWeightResolver(mock_pool)
        weight = await resolver.resolve("telegram", "456")
        assert weight == 0.9

    async def test_close_friends_gets_inner_circle_weight(self, mock_pool: AsyncMock) -> None:
        from butlers.connectors.discretion import ContactWeightResolver

        mock_pool.fetchrow.return_value = {"roles": ["close-friends"]}
        resolver = ContactWeightResolver(mock_pool)
        weight = await resolver.resolve("telegram", "789")
        assert weight == 0.9

    async def test_known_contact_gets_known_weight(self, mock_pool: AsyncMock) -> None:
        from butlers.connectors.discretion import ContactWeightResolver

        mock_pool.fetchrow.return_value = {"roles": []}
        resolver = ContactWeightResolver(mock_pool)
        weight = await resolver.resolve("telegram", "111")
        assert weight == 0.7

    async def test_unknown_sender_gets_unknown_weight(self, mock_pool: AsyncMock) -> None:
        from butlers.connectors.discretion import ContactWeightResolver

        mock_pool.fetchrow.return_value = None
        resolver = ContactWeightResolver(mock_pool)
        weight = await resolver.resolve("telegram", "999")
        assert weight == 0.3

    async def test_db_error_returns_unknown_weight(self, mock_pool: AsyncMock) -> None:
        from butlers.connectors.discretion import ContactWeightResolver

        mock_pool.fetchrow.side_effect = RuntimeError("connection lost")
        resolver = ContactWeightResolver(mock_pool)
        weight = await resolver.resolve("telegram", "123")
        assert weight == 0.3

    async def test_cache_prevents_repeated_queries(self, mock_pool: AsyncMock) -> None:
        from butlers.connectors.discretion import ContactWeightResolver

        mock_pool.fetchrow.return_value = {"roles": ["owner"]}
        resolver = ContactWeightResolver(mock_pool, cache_ttl_s=300.0)

        w1 = await resolver.resolve("telegram", "123")
        w2 = await resolver.resolve("telegram", "123")
        assert w1 == w2 == 1.0
        assert mock_pool.fetchrow.call_count == 1

    async def test_custom_tiers(self, mock_pool: AsyncMock) -> None:
        from butlers.connectors.discretion import ContactWeightResolver, WeightTier

        tiers = WeightTier(owner=1.0, inner_circle=0.8, known=0.5, unknown=0.1)
        mock_pool.fetchrow.return_value = {"roles": ["family"]}
        resolver = ContactWeightResolver(mock_pool, tiers=tiers)
        weight = await resolver.resolve("telegram", "456")
        assert weight == 0.8
