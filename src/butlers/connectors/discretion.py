"""Shared discretion layer for connectors.

An LLM-based filter that evaluates messages in context and decides whether
they warrant butler attention (FORWARD) or should be silently discarded
(IGNORE).

Design constraints:
- Sliding context window: last N messages OR last T seconds, whichever is fewer.
- Fail-open: timeout and errors always default to FORWARD.
- Configurable LLM backend via environment variables.
- Hard timeout per call (default 3 s).
- Identity-based weight: sender relationship determines fail behaviour and
  bypass thresholds.  Owner messages skip the LLM entirely.

Environment variables use a per-connector prefix (e.g. ``LIVE_LISTENER_``,
``TELEGRAM_USER_``).  Each connector reads:

    {PREFIX}DISCRETION_LLM_URL
    {PREFIX}DISCRETION_LLM_MODEL
    {PREFIX}DISCRETION_TIMEOUT_S
    {PREFIX}DISCRETION_WINDOW_SIZE
    {PREFIX}DISCRETION_WINDOW_SECONDS
    {PREFIX}DISCRETION_WEIGHT_BYPASS      (default 1.0)
    {PREFIX}DISCRETION_WEIGHT_FAIL_OPEN   (default 0.5)

Falls back to ``CONNECTOR_DISCRETION_*`` if the prefixed var is not set,
then to built-in defaults.
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
_DEFAULT_LLM_MODEL: str = "gemma3:12b"
_DEFAULT_LLM_URL: str = "http://localhost:11434"
_DEFAULT_WEIGHT_BYPASS: float = 1.0
_DEFAULT_WEIGHT_FAIL_OPEN: float = 0.5

_INNER_CIRCLE_ROLES: frozenset[str] = frozenset({"family", "close-friends"})

_DEFAULT_SYSTEM_PROMPT = (
    "You are a personal-assistant discretion filter. "
    "Given a recent conversation context and a new message, decide whether "
    "the message warrants forwarding to a personal AI assistant. "
    "Reply with EXACTLY one of:\n"
    "  FORWARD: <one-line reason>\n"
    "  IGNORE\n"
    "Do not include any other text. "
    "FORWARD if the message is a question, request, command, or anything "
    "that sounds like it is directed at an assistant or its owner. "
    "IGNORE for background conversation, ambient noise transcriptions, "
    "media chatter, group banter, or messages clearly not directed at "
    "an assistant."
)

_USER_PROMPT_TEMPLATE = """\
## Recent context ({n} messages)
{context}

## New message to evaluate
source: {source}
text: {text}

Respond FORWARD or IGNORE."""

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

Verdict = Literal["FORWARD", "IGNORE"]


@dataclass(frozen=True)
class ContextEntry:
    """A single message in the sliding context window."""

    text: str
    timestamp: float  # Unix epoch seconds
    source: str  # mic name, chat id, sender — any identifier


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
    """Sliding context window for discretion evaluation.

    Bounded by ``max_size`` entries AND ``max_age_seconds`` age; whichever
    constraint produces **fewer** entries is applied — i.e. both limits are
    enforced simultaneously.
    """

    max_size: int = _DEFAULT_WINDOW_SIZE
    max_age_seconds: float = _DEFAULT_WINDOW_SECONDS
    _entries: list[ContextEntry] = field(default_factory=list)

    def append(self, entry: ContextEntry) -> None:
        """Add a new message and trim the window."""
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
# Identity-based weight resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeightTier:
    """Default weight per sender-relationship tier.

    Higher weight → more likely to be forwarded.
    Weight >= ``weight_bypass`` threshold skips the LLM entirely.
    Weight >= ``weight_fail_open`` threshold fails open on LLM errors.
    Weight below that threshold fails closed (errors → IGNORE).
    """

    owner: float = 1.0
    inner_circle: float = 0.9  # family, close-friends
    known: float = 0.7
    unknown: float = 0.3


class ContactWeightResolver:
    """Resolve sender identity to a discretion weight via ``shared`` tables.

    Queries ``shared.contact_info → shared.contacts → shared.entities`` and
    maps the entity's roles to a :class:`WeightTier` value.  Results are
    cached in-memory with a configurable TTL.

    Usage::

        resolver = ContactWeightResolver(db_pool)
        weight = await resolver.resolve("telegram", "123456789")
    """

    def __init__(
        self,
        db_pool: object,
        *,
        tiers: WeightTier | None = None,
        cache_ttl_s: float = 300.0,
    ) -> None:
        self._pool = db_pool
        self._tiers = tiers or WeightTier()
        self._cache_ttl = cache_ttl_s
        # (channel_type, channel_value) → (weight, expiry_epoch)
        self._cache: dict[tuple[str, str], tuple[float, float]] = {}

    async def resolve(self, channel_type: str, channel_value: str) -> float:
        """Return the discretion weight for a sender identity.

        Falls back to ``tiers.unknown`` on cache miss + DB error.
        """
        key = (channel_type, channel_value)
        cached = self._cache.get(key)
        if cached is not None:
            weight, expiry = cached
            if time.time() < expiry:
                return weight

        weight = await self._query(channel_type, channel_value)
        self._cache[key] = (weight, time.time() + self._cache_ttl)
        return weight

    async def _query(self, channel_type: str, channel_value: str) -> float:
        """Look up contact roles from the shared schema."""
        try:
            row = await self._pool.fetchrow(
                """
                SELECT COALESCE(e.roles, '{}') AS roles
                FROM   shared.contact_info ci
                JOIN   shared.contacts c  ON c.id = ci.contact_id
                LEFT JOIN shared.entities e ON e.id = c.entity_id
                WHERE  ci.type = $1
                  AND  ci.value = $2
                LIMIT  1
                """,
                channel_type,
                channel_value,
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "ContactWeightResolver DB error for %s:%s — defaulting unknown",
                channel_type,
                channel_value,
            )
            return self._tiers.unknown

        if row is None:
            return self._tiers.unknown

        roles = set(row["roles"])
        if "owner" in roles:
            return self._tiers.owner
        if roles & _INNER_CIRCLE_ROLES:
            return self._tiers.inner_circle
        return self._tiers.known


# ---------------------------------------------------------------------------
# LLM caller
# ---------------------------------------------------------------------------


def _build_user_prompt(context_entries: list[ContextEntry], entry: ContextEntry) -> str:
    """Construct the user-facing prompt for the discretion LLM."""
    if context_entries:
        context_lines = "\n".join(
            f"[{i + 1}] ({e.source}) {e.text}" for i, e in enumerate(context_entries)
        )
    else:
        context_lines = "(none)"

    return _USER_PROMPT_TEMPLATE.format(
        n=len(context_entries),
        context=context_lines,
        source=entry.source,
        text=entry.text,
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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class DiscretionConfig:
    """Reads discretion configuration from environment variables.

    Supports a per-connector prefix for env var resolution:

        DiscretionConfig(env_prefix="LIVE_LISTENER_")
        # reads LIVE_LISTENER_DISCRETION_LLM_URL, falls back to
        # CONNECTOR_DISCRETION_LLM_URL, then built-in default.

        DiscretionConfig(env_prefix="TELEGRAM_USER_")
        # reads TELEGRAM_USER_DISCRETION_LLM_URL, etc.

        DiscretionConfig()
        # reads CONNECTOR_DISCRETION_* only.
    """

    def __init__(
        self,
        *,
        env_prefix: str = "",
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self.system_prompt = system_prompt
        self.llm_url = self._env("DISCRETION_LLM_URL", "", env_prefix)
        self.llm_model = self._env("DISCRETION_LLM_MODEL", "", env_prefix)
        self.timeout_s = float(
            self._env("DISCRETION_TIMEOUT_S", str(_DEFAULT_TIMEOUT_S), env_prefix)
        )
        self.window_size = int(
            self._env("DISCRETION_WINDOW_SIZE", str(_DEFAULT_WINDOW_SIZE), env_prefix)
        )
        self.window_seconds = float(
            self._env("DISCRETION_WINDOW_SECONDS", str(_DEFAULT_WINDOW_SECONDS), env_prefix)
        )
        self.weight_bypass = float(
            self._env("DISCRETION_WEIGHT_BYPASS", str(_DEFAULT_WEIGHT_BYPASS), env_prefix)
        )
        self.weight_fail_open = float(
            self._env("DISCRETION_WEIGHT_FAIL_OPEN", str(_DEFAULT_WEIGHT_FAIL_OPEN), env_prefix)
        )

    @staticmethod
    def _env(suffix: str, default: str, prefix: str) -> str:
        """Resolve env var: {prefix}{suffix} → CONNECTOR_{suffix} → default."""
        if prefix:
            val = os.environ.get(f"{prefix}{suffix}")
            if val is not None:
                return val
        val = os.environ.get(f"CONNECTOR_{suffix}")
        if val is not None:
            return val
        return default


async def _call_llm(
    prompt: str,
    *,
    system_prompt: str,
    llm_url: str,
    llm_model: str,
    timeout_s: float,
) -> str:
    """Call the Ollama native ``/api/chat`` endpoint and return the raw response text.

    Uses ``think: false`` so reasoning models (qwen3, deepseek-r1, etc.) produce
    a direct answer without consuming tokens on chain-of-thought.
    """
    base_url = (llm_url.rstrip("/") if llm_url else _DEFAULT_LLM_URL).removesuffix("/v1")
    model = llm_model if llm_model else _DEFAULT_LLM_MODEL

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "think": False,
        "stream": False,
        "options": {
            "num_predict": 64,
            "temperature": 0.0,
        },
    }

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"]


# ---------------------------------------------------------------------------
# Main discretion evaluator
# ---------------------------------------------------------------------------


class DiscretionEvaluator:
    """Stateful per-source discretion evaluator.

    Maintains a :class:`ContextWindow` and calls the configured LLM to
    evaluate each new message.  All failures are fail-open (FORWARD).

    Typical usage::

        config = DiscretionConfig(env_prefix="LIVE_LISTENER_")
        evaluator = DiscretionEvaluator(source_name="kitchen", config=config)

        result = await evaluator.evaluate(text="Hey, what's the weather?")
        if result.verdict == "FORWARD":
            # proceed to ingest submission
            ...
    """

    def __init__(self, source_name: str, config: DiscretionConfig | None = None) -> None:
        self._source = source_name
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
        weight: float = 1.0,
    ) -> DiscretionResult:
        """Evaluate a new message against the sliding context window.

        The message is always appended to the context window (even when
        bypassed) so that future evaluations see the full conversation.

        Args:
            text: Message text.
            timestamp: Unix timestamp.  Defaults to now.
            weight: Sender-relationship weight (0.0–1.0).  Controls bypass
                and fail behaviour:

                - ``>= config.weight_bypass`` (default 1.0): skip LLM,
                  always FORWARD.
                - ``>= config.weight_fail_open`` (default 0.5): call LLM,
                  errors → FORWARD (fail-open).
                - ``< config.weight_fail_open``: call LLM, errors → IGNORE
                  (fail-closed).

        Returns:
            :class:`DiscretionResult` — always succeeds.
        """
        ts = timestamp if timestamp is not None else time.time()
        entry = ContextEntry(text=text, timestamp=ts, source=self._source)

        # Capture context *before* appending so the prompt separates "recent
        # history" from "new message" — matches the spec language.
        context_snapshot = self._window.entries

        # Always append so the window stays complete for future evaluations.
        self._window.append(entry)

        # Weight bypass: high-trust senders skip the LLM entirely.
        if weight >= self._config.weight_bypass:
            return DiscretionResult(
                verdict="FORWARD",
                reason="weight-bypass",
                is_fail_open=False,
            )

        fail_open = weight >= self._config.weight_fail_open
        fail_verdict: Verdict = "FORWARD" if fail_open else "IGNORE"
        fail_label = "fail-open" if fail_open else "fail-closed"

        prompt = _build_user_prompt(context_snapshot, entry)

        try:
            raw = await asyncio.wait_for(
                _call_llm(
                    prompt,
                    system_prompt=self._config.system_prompt,
                    llm_url=self._config.llm_url,
                    llm_model=self._config.llm_model,
                    timeout_s=self._config.timeout_s,
                ),
                timeout=self._config.timeout_s,
            )
        except (TimeoutError, httpx.TimeoutException):
            logger.warning(
                "Discretion LLM timed out after %.1fs for source=%s (weight=%.2f) — defaulting %s",
                self._config.timeout_s,
                self._source,
                weight,
                fail_verdict,
            )
            return DiscretionResult(
                verdict=fail_verdict,
                reason=f"{fail_label}: timeout",
                is_fail_open=fail_open,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Discretion LLM error for source=%s (weight=%.2f): %s — defaulting %s",
                self._source,
                weight,
                exc,
                fail_verdict,
            )
            return DiscretionResult(
                verdict=fail_verdict,
                reason=f"{fail_label}: {type(exc).__name__}",
                is_fail_open=fail_open,
            )

        try:
            verdict, reason = _parse_verdict(raw)
        except ValueError:
            logger.warning(
                "Discretion LLM unparseable response for source=%s: %r — defaulting %s",
                self._source,
                raw[:200],
                fail_verdict,
            )
            return DiscretionResult(
                verdict=fail_verdict,
                reason=f"{fail_label}: parse_error",
                is_fail_open=fail_open,
            )

        return DiscretionResult(verdict=verdict, reason=reason, is_fail_open=False)
