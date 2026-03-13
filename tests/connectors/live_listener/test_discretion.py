"""Tests for the live-listener discretion layer.

Covers:
  - Sliding context window management (size cap, age cap, both simultaneously)
  - Discretion LLM verdict parsing (FORWARD with/without reason, IGNORE, malformed)
  - Fail-open behaviour: timeout → FORWARD, HTTP error → FORWARD, parse error → FORWARD
  - Full evaluator flow via DiscretionEvaluator (happy path + failure paths)
  - Config reads from environment variables
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from butlers.connectors.live_listener.discretion import (
    ContextEntry,
    ContextWindow,
    DiscretionConfig,
    DiscretionEvaluator,
    _build_user_prompt,
    _parse_verdict,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(text: str, *, mic: str = "kitchen", age_s: float = 0.0) -> ContextEntry:
    """Create a ContextEntry with a timestamp offset by ``age_s`` seconds in the past."""
    return ContextEntry(text=text, timestamp=time.time() - age_s, mic=mic)


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
# DiscretionConfig — environment variables
# ---------------------------------------------------------------------------


class TestDiscretionConfig:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LIVE_LISTENER_DISCRETION_LLM_URL", raising=False)
        monkeypatch.delenv("LIVE_LISTENER_DISCRETION_LLM_MODEL", raising=False)
        monkeypatch.delenv("LIVE_LISTENER_DISCRETION_TIMEOUT_S", raising=False)
        monkeypatch.delenv("LIVE_LISTENER_DISCRETION_WINDOW_SIZE", raising=False)
        monkeypatch.delenv("LIVE_LISTENER_DISCRETION_WINDOW_SECONDS", raising=False)

        cfg = DiscretionConfig()
        assert cfg.llm_url == ""
        assert cfg.llm_model == ""
        assert cfg.timeout_s == 3.0
        assert cfg.window_size == 10
        assert cfg.window_seconds == 300.0

    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LIVE_LISTENER_DISCRETION_LLM_URL", "http://gpu-box:11434/v1")
        monkeypatch.setenv("LIVE_LISTENER_DISCRETION_LLM_MODEL", "phi3-mini")
        monkeypatch.setenv("LIVE_LISTENER_DISCRETION_TIMEOUT_S", "5")
        monkeypatch.setenv("LIVE_LISTENER_DISCRETION_WINDOW_SIZE", "20")
        monkeypatch.setenv("LIVE_LISTENER_DISCRETION_WINDOW_SECONDS", "600")

        cfg = DiscretionConfig()
        assert cfg.llm_url == "http://gpu-box:11434/v1"
        assert cfg.llm_model == "phi3-mini"
        assert cfg.timeout_s == 5.0
        assert cfg.window_size == 20
        assert cfg.window_seconds == 600.0


# ---------------------------------------------------------------------------
# DiscretionEvaluator — happy path
# ---------------------------------------------------------------------------


class TestDiscretionEvaluatorHappyPath:
    @pytest.fixture()
    def config(self) -> DiscretionConfig:
        cfg = DiscretionConfig.__new__(DiscretionConfig)
        cfg.llm_url = "http://localhost:11434/v1"
        cfg.llm_model = "haiku"
        cfg.timeout_s = 3.0
        cfg.window_size = 10
        cfg.window_seconds = 300.0
        return cfg

    async def test_forward_verdict_returned(self, config: DiscretionConfig) -> None:
        evaluator = DiscretionEvaluator(mic_name="kitchen", config=config)
        with patch(
            "butlers.connectors.live_listener.discretion._call_llm",
            new=AsyncMock(return_value="FORWARD: sounds like a direct request"),
        ):
            result = await evaluator.evaluate("Hey, turn off the lights")
        assert result.verdict == "FORWARD"
        assert "direct request" in result.reason
        assert result.is_fail_open is False

    async def test_ignore_verdict_returned(self, config: DiscretionConfig) -> None:
        evaluator = DiscretionEvaluator(mic_name="kitchen", config=config)
        with patch(
            "butlers.connectors.live_listener.discretion._call_llm",
            new=AsyncMock(return_value="IGNORE"),
        ):
            result = await evaluator.evaluate("Yeah I know right, it was crazy")
        assert result.verdict == "IGNORE"
        assert result.reason == ""
        assert result.is_fail_open is False

    async def test_utterance_appended_to_window_before_next_call(
        self, config: DiscretionConfig
    ) -> None:
        """After evaluate(), the utterance should appear in the context window."""
        evaluator = DiscretionEvaluator(mic_name="kitchen", config=config)
        with patch(
            "butlers.connectors.live_listener.discretion._call_llm",
            new=AsyncMock(return_value="IGNORE"),
        ):
            await evaluator.evaluate("first utterance")
            await evaluator.evaluate("second utterance")

        entries = evaluator.window.entries
        assert len(entries) == 2
        assert entries[0].text == "first utterance"
        assert entries[1].text == "second utterance"

    async def test_context_passed_to_llm_excludes_current_utterance(
        self, config: DiscretionConfig
    ) -> None:
        """The prompt context should contain only the *previous* window entries."""
        evaluator = DiscretionEvaluator(mic_name="kitchen", config=config)
        captured_prompts: list[str] = []

        async def capture_call(prompt: str, **_kwargs: object) -> str:
            captured_prompts.append(prompt)
            return "IGNORE"

        with patch(
            "butlers.connectors.live_listener.discretion._call_llm",
            new=AsyncMock(side_effect=capture_call),
        ):
            await evaluator.evaluate("first")
            await evaluator.evaluate("second")

        # First call: context window was empty.
        assert "(none)" in captured_prompts[0]
        assert "first" in captured_prompts[0]

        # Second call: context contains "first"; new utterance is "second".
        assert "first" in captured_prompts[1]
        assert "second" in captured_prompts[1]
        # "second" should appear as the *new utterance* line, not as a numbered context entry.
        # Verify "second" is mentioned after the context block.
        second_prompt = captured_prompts[1]
        context_end = second_prompt.index("## New utterance")
        # "first" should appear before the separator; "second" after.
        assert second_prompt.index("first") < context_end


# ---------------------------------------------------------------------------
# DiscretionEvaluator — fail-open behaviour
# ---------------------------------------------------------------------------


class TestDiscretionEvaluatorFailOpen:
    @pytest.fixture()
    def config(self) -> DiscretionConfig:
        cfg = DiscretionConfig.__new__(DiscretionConfig)
        cfg.llm_url = "http://localhost:11434/v1"
        cfg.llm_model = "haiku"
        cfg.timeout_s = 0.1  # Very short for testing
        cfg.window_size = 10
        cfg.window_seconds = 300.0
        return cfg

    async def test_timeout_yields_forward(self, config: DiscretionConfig) -> None:
        import asyncio

        evaluator = DiscretionEvaluator(mic_name="kitchen", config=config)

        async def slow_call(prompt: str, **_kwargs: object) -> str:
            await asyncio.sleep(10)  # Will be cancelled by wait_for timeout
            return "IGNORE"

        with patch(
            "butlers.connectors.live_listener.discretion._call_llm",
            new=AsyncMock(side_effect=slow_call),
        ):
            result = await evaluator.evaluate("any text")

        assert result.verdict == "FORWARD"
        assert result.is_fail_open is True
        assert "timeout" in result.reason

    async def test_http_error_yields_forward(self, config: DiscretionConfig) -> None:
        evaluator = DiscretionEvaluator(mic_name="kitchen", config=config)
        with patch(
            "butlers.connectors.live_listener.discretion._call_llm",
            new=AsyncMock(side_effect=httpx.ConnectError("refused")),
        ):
            result = await evaluator.evaluate("any text")

        assert result.verdict == "FORWARD"
        assert result.is_fail_open is True
        assert "ConnectError" in result.reason

    async def test_malformed_response_yields_forward(self, config: DiscretionConfig) -> None:
        evaluator = DiscretionEvaluator(mic_name="kitchen", config=config)
        with patch(
            "butlers.connectors.live_listener.discretion._call_llm",
            new=AsyncMock(return_value="MAYBE: not sure"),
        ):
            result = await evaluator.evaluate("any text")

        assert result.verdict == "FORWARD"
        assert result.is_fail_open is True
        assert "parse_error" in result.reason

    async def test_unexpected_exception_yields_forward(self, config: DiscretionConfig) -> None:
        evaluator = DiscretionEvaluator(mic_name="kitchen", config=config)
        with patch(
            "butlers.connectors.live_listener.discretion._call_llm",
            new=AsyncMock(side_effect=RuntimeError("something unexpected")),
        ):
            result = await evaluator.evaluate("any text")

        assert result.verdict == "FORWARD"
        assert result.is_fail_open is True
        assert "RuntimeError" in result.reason

    async def test_window_still_updated_on_failure(self, config: DiscretionConfig) -> None:
        """Even when LLM fails, the utterance is added to the context window."""
        evaluator = DiscretionEvaluator(mic_name="kitchen", config=config)
        with patch(
            "butlers.connectors.live_listener.discretion._call_llm",
            new=AsyncMock(side_effect=httpx.TimeoutException("timeout")),
        ):
            await evaluator.evaluate("hello")

        assert len(evaluator.window) == 1
        assert evaluator.window.entries[0].text == "hello"


# ---------------------------------------------------------------------------
# DiscretionEvaluator — window integration
# ---------------------------------------------------------------------------


class TestDiscretionEvaluatorWindowIntegration:
    @pytest.fixture()
    def config(self) -> DiscretionConfig:
        cfg = DiscretionConfig.__new__(DiscretionConfig)
        cfg.llm_url = ""
        cfg.llm_model = ""
        cfg.timeout_s = 3.0
        cfg.window_size = 3
        cfg.window_seconds = 300.0
        return cfg

    async def test_window_respects_size_cap(self, config: DiscretionConfig) -> None:
        evaluator = DiscretionEvaluator(mic_name="kitchen", config=config)
        with patch(
            "butlers.connectors.live_listener.discretion._call_llm",
            new=AsyncMock(return_value="IGNORE"),
        ):
            for i in range(5):
                await evaluator.evaluate(f"utterance {i}")

        assert len(evaluator.window) == 3
        texts = [e.text for e in evaluator.window.entries]
        assert texts == ["utterance 2", "utterance 3", "utterance 4"]

    async def test_mic_name_stored_in_window(self, config: DiscretionConfig) -> None:
        evaluator = DiscretionEvaluator(mic_name="bedroom", config=config)
        with patch(
            "butlers.connectors.live_listener.discretion._call_llm",
            new=AsyncMock(return_value="FORWARD: ok"),
        ):
            await evaluator.evaluate("good morning")

        assert evaluator.window.entries[0].mic == "bedroom"

    async def test_timestamp_stored_in_window(self, config: DiscretionConfig) -> None:
        evaluator = DiscretionEvaluator(mic_name="kitchen", config=config)
        before = time.time()
        with patch(
            "butlers.connectors.live_listener.discretion._call_llm",
            new=AsyncMock(return_value="IGNORE"),
        ):
            await evaluator.evaluate("any text", timestamp=before + 1.0)

        assert evaluator.window.entries[0].timestamp == pytest.approx(before + 1.0, abs=0.01)
