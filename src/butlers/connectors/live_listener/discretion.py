"""Discretion layer for the Live Listener connector.

An LLM-based filter that evaluates transcribed utterances in context and
decides whether they warrant butler attention (FORWARD) or should be
silently discarded (IGNORE).

Key design constraints (from spec §Discretion Layer, §Latency Budget):
- Sliding context window: last N utterances OR last T seconds, whichever is fewer.
- Fail-open: timeout and errors always default to FORWARD.
- Configurable LLM backend via LIVE_LISTENER_DISCRETION_LLM_URL and
  LIVE_LISTENER_DISCRETION_LLM_MODEL environment variables.
- Hard timeout: LIVE_LISTENER_DISCRETION_TIMEOUT_S (default 3 s).

Environment Variables:
    LIVE_LISTENER_DISCRETION_LLM_URL: LLM endpoint URL (default: empty = ecosystem default)
    LIVE_LISTENER_DISCRETION_LLM_MODEL: Model name (default: empty = fastest available)
    LIVE_LISTENER_DISCRETION_TIMEOUT_S: Per-call timeout in seconds (default: 3.0)
    LIVE_LISTENER_DISCRETION_WINDOW_SIZE: Max utterances to keep in context window (default: 10)
    LIVE_LISTENER_DISCRETION_WINDOW_SECONDS: Max age in seconds for window entries (default: 300)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT_S: float = 3.0
_DEFAULT_WINDOW_SIZE: int = 10
_DEFAULT_WINDOW_SECONDS: float = 300.0

# Prompt sent to the discretion LLM.
_SYSTEM_PROMPT = (
    "You are a voice-assistant discretion filter. "
    "Given a recent conversation context and a new utterance, decide whether "
    "the utterance warrants forwarding to a personal AI assistant. "
    "Reply with EXACTLY one of:\n"
    "  FORWARD: <one-line reason>\n"
    "  IGNORE\n"
    "Do not include any other text. "
    "FORWARD if the utterance is a question, request, command, or anything "
    "that sounds like it is directed at an assistant. "
    "IGNORE for background conversation, ambient noise transcriptions, TV/radio "
    "chatter, or utterances clearly not directed at an assistant.\n"
    "/no_think"
)

_USER_PROMPT_TEMPLATE = """\
## Recent context ({n} utterances)
{context}

## New utterance to evaluate
mic: {mic}
text: {text}

Respond FORWARD or IGNORE."""

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

Verdict = Literal["FORWARD", "IGNORE"]


@dataclass(frozen=True)
class ContextEntry:
    """A single utterance in the sliding context window."""

    text: str
    timestamp: float  # Unix epoch seconds
    mic: str


@dataclass
class DiscretionResult:
    """Outcome of a single discretion evaluation."""

    verdict: Verdict
    reason: str  # One-line rationale (may be empty for IGNORE or fail-open)
    is_fail_open: bool = False  # True when verdict was forced by a failure


# ---------------------------------------------------------------------------
# Sliding context window
# ---------------------------------------------------------------------------


@dataclass
class ContextWindow:
    """Per-mic sliding context window for discretion evaluation.

    Bounded by ``max_size`` entries AND ``max_age_seconds`` age; whichever
    constraint produces **fewer** entries is applied — i.e. both limits are
    enforced simultaneously.
    """

    max_size: int = _DEFAULT_WINDOW_SIZE
    max_age_seconds: float = _DEFAULT_WINDOW_SECONDS
    _entries: list[ContextEntry] = field(default_factory=list)

    def append(self, entry: ContextEntry) -> None:
        """Add a new utterance and trim the window."""
        self._entries.append(entry)
        self._trim()

    def _trim(self) -> None:
        """Enforce both the size cap and the age cap simultaneously."""
        now = time.time()
        age_cutoff = now - self.max_age_seconds

        # Drop entries that are older than the time window.
        self._entries = [e for e in self._entries if e.timestamp >= age_cutoff]

        # Drop the oldest entries beyond the size cap.
        if len(self._entries) > self.max_size:
            self._entries = self._entries[-self.max_size :]

    @property
    def entries(self) -> list[ContextEntry]:
        """Return a snapshot of the current (trimmed) window."""
        self._trim()
        return list(self._entries)

    def __len__(self) -> int:
        self._trim()
        return len(self._entries)


# ---------------------------------------------------------------------------
# LLM caller
# ---------------------------------------------------------------------------


def _build_user_prompt(context_entries: list[ContextEntry], utterance: ContextEntry) -> str:
    """Construct the user-facing prompt for the discretion LLM."""
    if context_entries:
        context_lines = "\n".join(
            f"[{i + 1}] ({e.mic}) {e.text}" for i, e in enumerate(context_entries)
        )
    else:
        context_lines = "(none)"

    return _USER_PROMPT_TEMPLATE.format(
        n=len(context_entries),
        context=context_lines,
        mic=utterance.mic,
        text=utterance.text,
    )


def _parse_verdict(raw_response: str) -> tuple[Verdict, str]:
    """Parse the LLM response into a (verdict, reason) tuple.

    Accepts:
        "FORWARD: <reason>"
        "FORWARD"           (no reason — treated as empty reason)
        "IGNORE"
        "IGNORE: <reason>"  (reason ignored)

    Returns:
        (verdict, reason) — reason is empty string for IGNORE verdicts.

    Raises:
        ValueError: if the response cannot be parsed as FORWARD or IGNORE.
    """
    stripped = raw_response.strip()
    upper = stripped.upper()

    if upper.startswith("FORWARD"):
        # Everything after the optional ": " separator is the reason.
        rest = stripped[len("FORWARD") :].lstrip(": ").strip()
        return "FORWARD", rest

    if upper.startswith("IGNORE"):
        return "IGNORE", ""

    raise ValueError(f"Unrecognisable discretion verdict: {stripped!r}")


class DiscretionConfig:
    """Reads discretion configuration from environment variables."""

    def __init__(self) -> None:
        self.llm_url: str = os.environ.get("LIVE_LISTENER_DISCRETION_LLM_URL", "")
        self.llm_model: str = os.environ.get("LIVE_LISTENER_DISCRETION_LLM_MODEL", "")
        self.timeout_s: float = float(
            os.environ.get("LIVE_LISTENER_DISCRETION_TIMEOUT_S", _DEFAULT_TIMEOUT_S)
        )
        self.window_size: int = int(
            os.environ.get("LIVE_LISTENER_DISCRETION_WINDOW_SIZE", _DEFAULT_WINDOW_SIZE)
        )
        self.window_seconds: float = float(
            os.environ.get("LIVE_LISTENER_DISCRETION_WINDOW_SECONDS", _DEFAULT_WINDOW_SECONDS)
        )


async def _call_llm(
    prompt: str,
    *,
    llm_url: str,
    llm_model: str,
    timeout_s: float,
) -> str:
    """Call an OpenAI-compatible LLM endpoint and return the raw response text.

    Args:
        prompt: User-turn message to send.
        llm_url: Base URL of an OpenAI-compatible endpoint
                 (e.g. ``http://localhost:11434/v1`` for Ollama).
                 If empty, falls back to ``http://localhost:11434/v1``.
        llm_model: Model name to request.  If empty, ``"haiku"`` is used
                   as a latency-oriented fallback.
        timeout_s: Hard timeout for the entire HTTP round-trip.

    Returns:
        The content of the first choice's message from the completion response.

    Raises:
        httpx.TimeoutException: on network timeout.
        httpx.HTTPStatusError: on non-2xx HTTP responses.
        KeyError / ValueError: on unexpected response shape.
    """
    base_url = llm_url.rstrip("/") if llm_url else "http://localhost:11434/v1"
    model = llm_model if llm_model else "haiku"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 64,
        "temperature": 0.0,
    }

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(f"{base_url}/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Main discretion evaluator
# ---------------------------------------------------------------------------


class DiscretionEvaluator:
    """Stateful per-mic discretion evaluator.

    Maintains a :class:`ContextWindow` and calls the configured LLM to
    evaluate each new utterance.  All failures are fail-open (FORWARD).

    Typical usage::

        config = DiscretionConfig()
        evaluator = DiscretionEvaluator(mic_name="kitchen", config=config)

        result = await evaluator.evaluate(text="Hey, what's the weather?")
        if result.verdict == "FORWARD":
            # proceed to ingest submission
            ...
    """

    def __init__(self, mic_name: str, config: DiscretionConfig | None = None) -> None:
        self._mic = mic_name
        self._config = config or DiscretionConfig()
        self._window = ContextWindow(
            max_size=self._config.window_size,
            max_age_seconds=self._config.window_seconds,
        )

    @property
    def window(self) -> ContextWindow:
        """Read-only access to the context window (for testing / observability)."""
        return self._window

    async def evaluate(
        self,
        text: str,
        *,
        timestamp: float | None = None,
    ) -> DiscretionResult:
        """Evaluate a new utterance against the sliding context window.

        The utterance is appended to the window **before** evaluation so that
        the LLM sees the full picture including the current utterance.

        Args:
            text: Transcribed utterance text.
            timestamp: Unix timestamp of utterance offset.  Defaults to now.

        Returns:
            :class:`DiscretionResult` — always succeeds; errors produce
            ``verdict="FORWARD", is_fail_open=True``.
        """
        ts = timestamp if timestamp is not None else time.time()
        entry = ContextEntry(text=text, timestamp=ts, mic=self._mic)

        # Capture context *before* appending so the prompt separates "recent
        # history" from "new utterance" — matches the spec language.
        context_snapshot = self._window.entries

        # Now append so that the window is up-to-date for future evaluations.
        self._window.append(entry)

        prompt = _build_user_prompt(context_snapshot, entry)

        try:
            raw = await asyncio.wait_for(
                _call_llm(
                    prompt,
                    llm_url=self._config.llm_url,
                    llm_model=self._config.llm_model,
                    timeout_s=self._config.timeout_s,
                ),
                timeout=self._config.timeout_s,
            )
        except (TimeoutError, httpx.TimeoutException):
            logger.warning(
                "Discretion LLM timed out after %.1fs for mic=%s utterance=%r — defaulting FORWARD",
                self._config.timeout_s,
                self._mic,
                text[:80],
            )
            return DiscretionResult(
                verdict="FORWARD",
                reason="fail-open: timeout",
                is_fail_open=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Discretion LLM error for mic=%s: %s — defaulting FORWARD",
                self._mic,
                exc,
            )
            return DiscretionResult(
                verdict="FORWARD",
                reason=f"fail-open: {type(exc).__name__}",
                is_fail_open=True,
            )

        try:
            verdict, reason = _parse_verdict(raw)
        except ValueError:
            logger.warning(
                "Discretion LLM returned unparseable response for mic=%s: %r — defaulting FORWARD",
                self._mic,
                raw[:200],
            )
            return DiscretionResult(
                verdict="FORWARD",
                reason="fail-open: parse_error",
                is_fail_open=True,
            )

        return DiscretionResult(verdict=verdict, reason=reason, is_fail_open=False)
