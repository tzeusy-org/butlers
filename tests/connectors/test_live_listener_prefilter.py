"""Tests for live-listener heuristic pre-filter.

Covers:
- Fragment rejection (short utterances, allowlist bypass, question bypass)
- Burst rate suppression (threshold, hysteresis)
- Near-duplicate suppression (exact match, near match, below threshold)
- Master enable/disable toggle
- Window trimming (old entries expire)
- Edge cases (empty text, single word)
"""

from __future__ import annotations

import pytest

from butlers.connectors.live_listener.prefilter import (
    PreFilter,
    PreFilterConfig,
    _lcs_similarity,
    _normalize,
)

pytestmark = pytest.mark.unit

_MIC = "kitchen"


def _cfg(**overrides: object) -> PreFilterConfig:
    """Build a PreFilterConfig with defaults suitable for testing."""
    defaults = {
        "enabled": True,
        "min_words": 3,
        "burst_window_s": 60.0,
        "burst_max_rate": 5,  # low threshold for easier testing
        "burst_resume_pct": 0.5,
        "dedup_window_s": 120.0,
        "dedup_threshold": 0.85,
    }
    defaults.update(overrides)
    return PreFilterConfig(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_lowercase_and_strip_punctuation(self) -> None:
        assert _normalize("Hello, World!") == "hello world"

    def test_collapse_whitespace(self) -> None:
        assert _normalize("  foo   bar  ") == "foo bar"

    def test_empty(self) -> None:
        assert _normalize("") == ""


class TestLcsSimilarity:
    def test_identical(self) -> None:
        assert _lcs_similarity("hello world", "hello world") == 1.0

    def test_empty_strings(self) -> None:
        assert _lcs_similarity("", "") == 0.0
        assert _lcs_similarity("hello", "") == 0.0

    def test_one_word_diff(self) -> None:
        a = "subscribe and hit the bell"
        b = "subscribe and hit that bell"
        sim = _lcs_similarity(a, b)
        assert sim > 0.85  # should be caught as near-duplicate

    def test_completely_different(self) -> None:
        sim = _lcs_similarity("abc", "xyz")
        assert sim == 0.0


# ---------------------------------------------------------------------------
# Fragment rejection
# ---------------------------------------------------------------------------


class TestFragmentRejection:
    def test_short_utterance_rejected(self) -> None:
        pf = PreFilter(_MIC, _cfg())
        result = pf.evaluate("uh huh", timestamp=100.0)
        assert not result.allowed
        assert result.reason == "fragment"

    def test_single_word_rejected(self) -> None:
        pf = PreFilter(_MIC, _cfg())
        result = pf.evaluate("yeah", timestamp=100.0)
        assert not result.allowed
        assert result.reason == "fragment"

    def test_three_words_passes(self) -> None:
        pf = PreFilter(_MIC, _cfg())
        result = pf.evaluate("what is happening", timestamp=100.0)
        assert result.allowed

    def test_allowlisted_short_command_passes(self) -> None:
        pf = PreFilter(_MIC, _cfg())
        for cmd in ("stop", "help", "cancel", "yes", "no"):
            result = pf.evaluate(cmd, timestamp=100.0)
            assert result.allowed, f"'{cmd}' should be allowlisted"

    def test_question_mark_passes(self) -> None:
        pf = PreFilter(_MIC, _cfg())
        result = pf.evaluate("what?", timestamp=100.0)
        assert result.allowed

    def test_two_word_question_passes(self) -> None:
        pf = PreFilter(_MIC, _cfg())
        result = pf.evaluate("how much?", timestamp=100.0)
        assert result.allowed

    def test_empty_string_rejected(self) -> None:
        pf = PreFilter(_MIC, _cfg())
        result = pf.evaluate("", timestamp=100.0)
        assert not result.allowed
        assert result.reason == "fragment"


# ---------------------------------------------------------------------------
# Burst rate suppression
# ---------------------------------------------------------------------------


class TestBurstSuppression:
    def test_below_threshold_passes(self) -> None:
        # Disable dedup so only burst logic is tested
        pf = PreFilter(_MIC, _cfg(burst_max_rate=5, dedup_threshold=1.0))
        # 5 utterances within window — at threshold, not above
        texts = [
            "the weather is nice today",
            "please turn off the lights",
            "what time does the store close",
            "remind me to call the dentist",
            "can you play some jazz music",
        ]
        for i, text in enumerate(texts):
            result = pf.evaluate(text, timestamp=100.0 + i)
        assert result.allowed

    def test_above_threshold_triggers_burst(self) -> None:
        pf = PreFilter(_MIC, _cfg(burst_max_rate=5, dedup_threshold=1.0))
        texts = [
            "the weather is nice today",
            "please turn off the lights",
            "what time does the store close",
            "remind me to call the dentist",
            "can you play some jazz music",
        ]
        for i, text in enumerate(texts):
            pf.evaluate(text, timestamp=100.0 + i)
        result = pf.evaluate("one more utterance here", timestamp=105.0)
        assert not result.allowed
        assert result.reason == "burst"
        assert pf.burst_active

    def test_hysteresis_stays_active(self) -> None:
        pf = PreFilter(_MIC, _cfg(burst_max_rate=5, burst_resume_pct=0.5, dedup_threshold=1.0))
        # Trigger burst mode with distinct utterances
        burst_texts = [
            "the weather is nice today",
            "please turn off the lights",
            "what time does the store close",
            "remind me to call the dentist",
            "can you play some jazz music",
            "tell me about the latest news",
        ]
        for i, text in enumerate(burst_texts):
            pf.evaluate(text, timestamp=100.0 + i)
        assert pf.burst_active

        # Still many entries in window (> resume threshold of 2) — stays active
        result = pf.evaluate("still in burst mode here", timestamp=103.0)
        assert not result.allowed
        assert pf.burst_active

    def test_hysteresis_exits_when_rate_drops(self) -> None:
        pf = PreFilter(
            _MIC,
            _cfg(burst_max_rate=5, burst_resume_pct=0.5, burst_window_s=10.0, dedup_threshold=1.0),
        )
        # Trigger burst: 6 utterances at t=100..105
        burst_texts = [
            "the weather is nice today",
            "please turn off the lights",
            "what time does the store close",
            "remind me to call the dentist",
            "can you play some jazz music",
            "tell me about the latest news",
        ]
        for i, text in enumerate(burst_texts):
            pf.evaluate(text, timestamp=100.0 + i)
        assert pf.burst_active

        # Jump forward so all old timestamps expire (window is 10s)
        # Only 1 entry in window (the current one) → below resume threshold (2)
        result = pf.evaluate("should pass now right here", timestamp=200.0)
        assert result.allowed
        assert not pf.burst_active

    def test_burst_rejects_long_utterances_too(self) -> None:
        """Even long (non-fragment) utterances are rejected during burst."""
        pf = PreFilter(_MIC, _cfg(burst_max_rate=3, dedup_threshold=1.0))
        burst_texts = [
            "the weather is nice today indeed",
            "please turn off the lights now",
            "what time does the store close tonight",
            "remind me to call the dentist tomorrow",
        ]
        for i, text in enumerate(burst_texts):
            pf.evaluate(text, timestamp=100.0 + i)
        result = pf.evaluate("this is a perfectly good sentence", timestamp=104.0)
        assert not result.allowed
        assert result.reason == "burst"


# ---------------------------------------------------------------------------
# Near-duplicate suppression
# ---------------------------------------------------------------------------


class TestDuplicateSuppression:
    def test_exact_duplicate_rejected(self) -> None:
        pf = PreFilter(_MIC, _cfg())
        pf.evaluate("subscribe and hit the bell", timestamp=100.0)
        result = pf.evaluate("subscribe and hit the bell", timestamp=101.0)
        assert not result.allowed
        assert result.reason == "duplicate"

    def test_near_duplicate_rejected(self) -> None:
        pf = PreFilter(_MIC, _cfg())
        pf.evaluate("subscribe and hit the bell", timestamp=100.0)
        result = pf.evaluate("subscribe and hit that bell", timestamp=101.0)
        assert not result.allowed
        assert result.reason == "duplicate"

    def test_different_text_passes(self) -> None:
        pf = PreFilter(_MIC, _cfg())
        pf.evaluate("subscribe and hit the bell", timestamp=100.0)
        result = pf.evaluate("what is the weather today", timestamp=101.0)
        assert result.allowed

    def test_duplicate_outside_window_passes(self) -> None:
        pf = PreFilter(_MIC, _cfg(dedup_window_s=10.0))
        pf.evaluate("subscribe and hit the bell", timestamp=100.0)
        # 15 seconds later — outside dedup window
        result = pf.evaluate("subscribe and hit the bell", timestamp=115.0)
        assert result.allowed

    def test_below_similarity_threshold_passes(self) -> None:
        pf = PreFilter(_MIC, _cfg(dedup_threshold=0.95))
        pf.evaluate("the quick brown fox jumps", timestamp=100.0)
        # Change enough words to drop below 0.95
        result = pf.evaluate("a slow red cat leaps high", timestamp=101.0)
        assert result.allowed

    def test_case_insensitive_dedup(self) -> None:
        pf = PreFilter(_MIC, _cfg())
        pf.evaluate("Subscribe And Hit The Bell", timestamp=100.0)
        result = pf.evaluate("subscribe and hit the bell", timestamp=101.0)
        assert not result.allowed
        assert result.reason == "duplicate"


# ---------------------------------------------------------------------------
# Master toggle
# ---------------------------------------------------------------------------


class TestDisabledPreFilter:
    def test_disabled_passes_everything(self) -> None:
        pf = PreFilter(_MIC, _cfg(enabled=False))
        # Fragment that would normally be rejected
        result = pf.evaluate("uh", timestamp=100.0)
        assert result.allowed
        assert result.reason == "disabled"


# ---------------------------------------------------------------------------
# Heuristic evaluation order
# ---------------------------------------------------------------------------


class TestEvaluationOrder:
    def test_fragment_checked_before_burst(self) -> None:
        """Fragment rejection fires first, even during burst."""
        pf = PreFilter(_MIC, _cfg(burst_max_rate=3, dedup_threshold=1.0))
        # Trigger burst with distinct short utterances (all are fragments)
        short_words = ["right", "okay", "yeah", "sure"]
        for i, w in enumerate(short_words):
            pf.evaluate(w, timestamp=100.0 + i)
        # Next short utterance: fragment fires first (it's checked before burst)
        result = pf.evaluate("ok", timestamp=104.0)
        assert result.reason == "fragment"

    def test_burst_checked_before_duplicate(self) -> None:
        """During burst, burst rejection fires before duplicate check."""
        pf = PreFilter(_MIC, _cfg(burst_max_rate=3, dedup_threshold=1.0))
        # Trigger burst with distinct long utterances
        burst_texts = [
            "the weather is nice today indeed",
            "please turn off the lights now",
            "what time does the store close tonight",
            "remind me to call the dentist tomorrow",
        ]
        for i, text in enumerate(burst_texts):
            pf.evaluate(text, timestamp=100.0 + i)
        # Repeat an earlier utterance — burst should fire first
        result = pf.evaluate("the weather is nice today indeed", timestamp=104.0)
        assert result.reason == "burst"


# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------


class TestPreFilterConfig:
    def test_defaults(self) -> None:
        cfg = PreFilterConfig()
        assert cfg.enabled is True
        assert cfg.min_words == 3
        assert cfg.burst_max_rate == 15
        assert cfg.dedup_threshold == 0.85

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LIVE_LISTENER_PREFILTER_ENABLED", "false")
        monkeypatch.setenv("LIVE_LISTENER_PREFILTER_MIN_WORDS", "5")
        monkeypatch.setenv("LIVE_LISTENER_PREFILTER_BURST_MAX_RATE", "20")
        monkeypatch.setenv("LIVE_LISTENER_PREFILTER_DEDUP_THRESHOLD", "0.9")
        cfg = PreFilterConfig.from_env()
        assert cfg.enabled is False
        assert cfg.min_words == 5
        assert cfg.burst_max_rate == 20
        assert cfg.dedup_threshold == 0.9

    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset env vars use defaults."""
        # Clear any that might be set
        for key in (
            "LIVE_LISTENER_PREFILTER_ENABLED",
            "LIVE_LISTENER_PREFILTER_MIN_WORDS",
            "LIVE_LISTENER_PREFILTER_BURST_MAX_RATE",
        ):
            monkeypatch.delenv(key, raising=False)
        cfg = PreFilterConfig.from_env()
        assert cfg.enabled is True
        assert cfg.min_words == 3
