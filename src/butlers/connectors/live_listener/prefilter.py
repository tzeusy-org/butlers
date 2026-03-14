"""Heuristic pre-filter for the live-listener connector.

Cheap, deterministic filter that runs between the source filter gate and the
LLM-based discretion layer.  Rejects obvious noise (fragments, burst media
playback, near-duplicate transcriptions) so the discretion LLM is only called
for plausible conversational utterances.

Pipeline position:
  transcription → filter_gate → [PRE-FILTER] → discretion → envelope → ingest

Design spec:
  openspec/changes/live-listener-prefilter/design.md
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_MIN_WORDS = 3
_DEFAULT_BURST_WINDOW_S = 60.0
_DEFAULT_BURST_MAX_RATE = 15
_DEFAULT_BURST_RESUME_PCT = 0.5
_DEFAULT_DEDUP_WINDOW_S = 120.0
_DEFAULT_DEDUP_THRESHOLD = 0.85

# Short utterances that should pass despite being < min_words.
_DEFAULT_FRAGMENT_ALLOWLIST = frozenset(
    {
        "stop",
        "help",
        "pause",
        "thanks",
        "cancel",
        "yes",
        "no",
        "resume",
        "repeat",
        "louder",
        "quieter",
    }
)

# Regex: ends with a question mark (after stripping whitespace).
_QUESTION_RE = re.compile(r"\?\s*$")

# Normalisation: collapse whitespace, strip punctuation, lowercase.
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_MULTI_SPACE_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreFilterResult:
    """Outcome of a single pre-filter evaluation."""

    allowed: bool
    reason: str  # "passed", "fragment", "burst", "duplicate"


@dataclass
class PreFilterConfig:
    """Pre-filter configuration loaded from environment variables."""

    enabled: bool = True
    min_words: int = _DEFAULT_MIN_WORDS
    fragment_allowlist: frozenset[str] = _DEFAULT_FRAGMENT_ALLOWLIST
    burst_window_s: float = _DEFAULT_BURST_WINDOW_S
    burst_max_rate: int = _DEFAULT_BURST_MAX_RATE
    burst_resume_pct: float = _DEFAULT_BURST_RESUME_PCT
    dedup_window_s: float = _DEFAULT_DEDUP_WINDOW_S
    dedup_threshold: float = _DEFAULT_DEDUP_THRESHOLD

    @classmethod
    def from_env(cls) -> PreFilterConfig:
        """Load configuration from ``LIVE_LISTENER_PREFILTER_*`` env vars."""

        def _bool(key: str, default: bool) -> bool:
            v = os.environ.get(key, "").strip().lower()
            if not v:
                return default
            return v in ("1", "true", "yes")

        def _int(key: str, default: int) -> int:
            v = os.environ.get(key, "").strip()
            return int(v) if v else default

        def _float(key: str, default: float) -> float:
            v = os.environ.get(key, "").strip()
            return float(v) if v else default

        return cls(
            enabled=_bool("LIVE_LISTENER_PREFILTER_ENABLED", True),
            min_words=_int("LIVE_LISTENER_PREFILTER_MIN_WORDS", _DEFAULT_MIN_WORDS),
            burst_window_s=_float(
                "LIVE_LISTENER_PREFILTER_BURST_WINDOW_S", _DEFAULT_BURST_WINDOW_S
            ),
            burst_max_rate=_int("LIVE_LISTENER_PREFILTER_BURST_MAX_RATE", _DEFAULT_BURST_MAX_RATE),
            burst_resume_pct=_float(
                "LIVE_LISTENER_PREFILTER_BURST_RESUME_PCT", _DEFAULT_BURST_RESUME_PCT
            ),
            dedup_window_s=_float(
                "LIVE_LISTENER_PREFILTER_DEDUP_WINDOW_S", _DEFAULT_DEDUP_WINDOW_S
            ),
            dedup_threshold=_float(
                "LIVE_LISTENER_PREFILTER_DEDUP_THRESHOLD", _DEFAULT_DEDUP_THRESHOLD
            ),
        )


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    t = text.lower()
    t = _PUNCT_RE.sub("", t)
    t = _MULTI_SPACE_RE.sub(" ", t).strip()
    return t


def _lcs_length(a: str, b: str) -> int:
    """Length of the longest common subsequence of *a* and *b*.

    Uses the standard DP approach with O(min(len(a), len(b))) space.
    """
    if len(a) < len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    for ch_a in a:
        curr = [0] * (len(b) + 1)
        for j, ch_b in enumerate(b, 1):
            if ch_a == ch_b:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    return prev[len(b)]


def _lcs_similarity(a: str, b: str) -> float:
    """LCS-based similarity ratio in [0.0, 1.0]."""
    if not a or not b:
        return 0.0
    max_len = max(len(a), len(b))
    return _lcs_length(a, b) / max_len


def _word_count(text: str) -> int:
    """Count whitespace-delimited tokens."""
    return len(text.split())


# ---------------------------------------------------------------------------
# Pre-filter
# ---------------------------------------------------------------------------

# Sentinel results (avoid per-call allocation for the common paths).
_PASS = PreFilterResult(allowed=True, reason="passed")
_DISABLED = PreFilterResult(allowed=True, reason="disabled")
_REJECT_FRAGMENT = PreFilterResult(allowed=False, reason="fragment")
_REJECT_BURST = PreFilterResult(allowed=False, reason="burst")
_REJECT_DUPLICATE = PreFilterResult(allowed=False, reason="duplicate")


class PreFilter:
    """Per-mic stateful heuristic pre-filter.

    Evaluates transcribed utterances through three heuristic stages:
    1. Fragment rejection (too few words, not a known short command)
    2. Burst rate suppression (too many utterances in a time window)
    3. Near-duplicate suppression (similar text seen recently)

    All state is in-memory and per-mic.  No I/O, no async, sub-millisecond.
    """

    def __init__(self, mic_name: str, config: PreFilterConfig | None = None) -> None:
        self._mic = mic_name
        self._config = config or PreFilterConfig()

        # Burst state: deque of timestamps (monotonic-ish, but we use wall time
        # for consistency with the rest of the connector pipeline).
        self._burst_timestamps: deque[float] = deque()
        self._burst_active: bool = False

        # Dedup state: deque of (timestamp, normalized_text).
        self._dedup_window: deque[tuple[float, str]] = deque()

    @property
    def burst_active(self) -> bool:
        """Whether burst suppression is currently engaged."""
        return self._burst_active

    def evaluate(self, text: str, *, timestamp: float | None = None) -> PreFilterResult:
        """Evaluate a transcribed utterance.

        Args:
            text: Raw transcript text from the transcription client.
            timestamp: Unix epoch seconds.  Defaults to ``time.time()``.

        Returns:
            :class:`PreFilterResult` — ``allowed=True`` means proceed to
            discretion; ``allowed=False`` means drop silently.
        """
        if not self._config.enabled:
            return _DISABLED

        ts = timestamp if timestamp is not None else time.time()
        norm = _normalize(text)

        # Always record for burst/dedup state regardless of outcome.
        self._burst_timestamps.append(ts)
        self._dedup_window.append((ts, norm))
        self._trim_windows(ts)

        # --- 1. Fragment rejection ---
        if self._is_fragment(text, norm):
            return _REJECT_FRAGMENT

        # --- 2. Burst suppression ---
        if self._is_burst(ts):
            return _REJECT_BURST

        # --- 3. Near-duplicate suppression ---
        if self._is_duplicate(ts, norm):
            return _REJECT_DUPLICATE

        return _PASS

    # ------------------------------------------------------------------
    # Heuristic implementations
    # ------------------------------------------------------------------

    def _is_fragment(self, raw_text: str, normalized: str) -> bool:
        """Reject utterances shorter than ``min_words`` unless allowlisted."""
        if _word_count(raw_text) >= self._config.min_words:
            return False

        # Allow short questions ("what?" "why?" "how?")
        if _QUESTION_RE.search(raw_text):
            return False

        # Allow known short commands
        if normalized in self._config.fragment_allowlist:
            return False

        return True

    def _is_burst(self, now: float) -> bool:
        """Detect and suppress media-playback-level utterance rates."""
        count = len(self._burst_timestamps)
        resume_threshold = int(self._config.burst_max_rate * self._config.burst_resume_pct)

        if self._burst_active:
            # Exit burst mode only when rate drops below resume threshold
            if count <= resume_threshold:
                self._burst_active = False
                logger.debug(
                    "live-listener: mic=%s exiting burst suppression (rate=%d <= %d)",
                    self._mic,
                    count,
                    resume_threshold,
                )
                return False
            return True
        else:
            # Enter burst mode when rate exceeds max
            if count > self._config.burst_max_rate:
                self._burst_active = True
                logger.info(
                    "live-listener: mic=%s entering burst suppression "
                    "(rate=%d > %d in %.0fs window)",
                    self._mic,
                    count,
                    self._config.burst_max_rate,
                    self._config.burst_window_s,
                )
                return True
            return False

    def _is_duplicate(self, now: float, normalized: str) -> bool:
        """Reject near-duplicate transcriptions within the dedup window."""
        if not normalized:
            return False

        # Compare against all entries except the last one (which is the current
        # utterance we just appended).
        for i in range(len(self._dedup_window) - 1):
            _, prev_text = self._dedup_window[i]
            if not prev_text:
                continue
            sim = _lcs_similarity(normalized, prev_text)
            if sim >= self._config.dedup_threshold:
                return True

        return False

    # ------------------------------------------------------------------
    # Window maintenance
    # ------------------------------------------------------------------

    def _trim_windows(self, now: float) -> None:
        """Remove entries older than their respective window durations."""
        burst_cutoff = now - self._config.burst_window_s
        while self._burst_timestamps and self._burst_timestamps[0] < burst_cutoff:
            self._burst_timestamps.popleft()

        dedup_cutoff = now - self._config.dedup_window_s
        while self._dedup_window and self._dedup_window[0][0] < dedup_cutoff:
            self._dedup_window.popleft()
