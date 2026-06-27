"""Shared discretion layer for connectors.

An LLM-based filter that evaluates messages in context and decides whether
they warrant butler attention (FORWARD) or should be silently discarded
(IGNORE).

Design constraints:
- Sliding context window: last N messages OR last T seconds, whichever is fewer.
- Fail-open: timeout and errors always default to FORWARD.
- Hard timeout per call is enforced by the injected dispatcher.
- Identity-based weight: sender relationship determines fail behaviour and
  bypass thresholds.  Owner messages skip the LLM entirely.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from prometheus_client import Counter

from butlers.identity import _CHANNEL_TYPE_TO_PREDICATE, _resolve_entity_by_triple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

_DEFAULT_WINDOW_SIZE: int = 10
_DEFAULT_WINDOW_SECONDS: float = 300.0
_DEFAULT_WEIGHT_BYPASS: float = 1.0
_DEFAULT_WEIGHT_FAIL_OPEN: float = 0.5

_INNER_CIRCLE_ROLES: frozenset[str] = frozenset({"family", "close-friends"})

# Channels whose messages are always operator-intentional and therefore
# bypass discretion evaluation entirely.  Dashboard messages are submitted
# directly by the owner via the web interface; they must never be filtered.
DISCRETION_BYPASS_CHANNELS: frozenset[str] = frozenset({"dashboard"})

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

discretion_evaluations_total = Counter(
    "discretion_evaluations_total",
    "Total discretion evaluations by outcome",
    labelnames=["source", "verdict", "outcome"],
)
"""Labels:

- source: evaluator source name (e.g. Telegram chat ID)
- verdict: FORWARD or IGNORE
- outcome: ok, bypass, timeout, error, parse_error, fail_open, fail_closed
"""

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

    Queries ``relationship.entity_facts`` (migration bead 7) joined to
    ``public.entities`` and maps the entity's roles to a :class:`WeightTier`
    value.  Results are cached in-memory with a configurable TTL.

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
        """Look up contact roles from relationship.entity_facts (bu-hjo3i).

        Resolves the sender's entity via the triple store using the canonical
        predicate for the given channel type, then reads roles from
        public.entities.  Falls back to ``tiers.unknown`` on DB error or when
        no matching triple is found.
        """
        predicate = _CHANNEL_TYPE_TO_PREDICATE.get(channel_type)
        if predicate is None:
            return self._tiers.unknown

        try:
            row = await _resolve_entity_by_triple(self._pool, predicate, channel_value)
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
# LLM caller protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DiscretionLLMCaller(Protocol):
    """Protocol for dispatching a single-turn discretion LLM call.

    Implementations include :class:`~butlers.connectors.discretion_dispatcher.DiscretionDispatcher`
    for production use and lightweight mock objects for testing.
    """

    async def call(self, prompt: str, system_prompt: str = "") -> str:
        """Invoke the LLM with *prompt* and return the raw response text."""
        ...


# ---------------------------------------------------------------------------
# Prompt helpers
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
# Main discretion evaluator
# ---------------------------------------------------------------------------


class DiscretionEvaluator:
    """Stateful per-source discretion evaluator.

    Maintains a :class:`ContextWindow` and calls the injected dispatcher to
    evaluate each new message.  All failures are handled per the weight tier:
    high-weight senders fail-open (FORWARD), low-weight senders fail-closed
    (IGNORE).

    Typical usage::

        dispatcher = DiscretionDispatcher(pool=db_pool)
        evaluator = DiscretionEvaluator(
            source_name="kitchen",
            dispatcher=dispatcher,
        )

        result = await evaluator.evaluate(text="Hey, what's the weather?")
        if result.verdict == "FORWARD":
            # proceed to ingest submission
            ...
    """

    def __init__(
        self,
        source_name: str,
        dispatcher: DiscretionLLMCaller,
        *,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        weight_bypass: float = _DEFAULT_WEIGHT_BYPASS,
        weight_fail_open: float = _DEFAULT_WEIGHT_FAIL_OPEN,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self._source = source_name
        self._dispatcher = dispatcher
        self._weight_bypass = weight_bypass
        self._weight_fail_open = weight_fail_open
        self._system_prompt = system_prompt
        self._window = ContextWindow(
            max_size=window_size,
            max_age_seconds=window_seconds,
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
        channel: str | None = None,
    ) -> DiscretionResult:
        """Evaluate a new message against the sliding context window.

        The message is always appended to the context window (even when
        bypassed) so that future evaluations see the full conversation.

        Args:
            text: Message text.
            timestamp: Unix timestamp.  Defaults to now.
            weight: Sender-relationship weight (0.0–1.0).  Controls bypass
                and fail behaviour:

                - ``>= weight_bypass`` (default 1.0): skip LLM,
                  always FORWARD.
                - ``>= weight_fail_open`` (default 0.5): call LLM,
                  errors → FORWARD (fail-open).
                - ``< weight_fail_open``: call LLM, errors → IGNORE
                  (fail-closed).
            channel: Originating channel name (e.g. ``"telegram"``,
                ``"dashboard"``).  Messages from a channel in
                :data:`DISCRETION_BYPASS_CHANNELS` skip the LLM entirely and
                always FORWARD — they are operator-intentional by definition.
                ``None`` (the default) means "no channel-level bypass" and
                preserves full discretion evaluation for all callers that do
                not supply a channel.

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

        # Channel bypass: messages from trusted operator-only surfaces (e.g. the
        # dashboard, submitted directly by the owner) skip the LLM entirely and
        # must never be filtered.  This must stay strictly limited to channels
        # in DISCRETION_BYPASS_CHANNELS so the security gate remains intact for
        # every other channel (telegram, email, etc.).
        if channel is not None and channel in DISCRETION_BYPASS_CHANNELS:
            discretion_evaluations_total.labels(
                source=self._source,
                verdict="FORWARD",
                outcome="bypass",
            ).inc()
            return DiscretionResult(
                verdict="FORWARD",
                reason="channel-bypass",
                is_fail_open=False,
            )

        # Weight bypass: high-trust senders skip the LLM entirely.
        if weight >= self._weight_bypass:
            discretion_evaluations_total.labels(
                source=self._source,
                verdict="FORWARD",
                outcome="bypass",
            ).inc()
            return DiscretionResult(
                verdict="FORWARD",
                reason="weight-bypass",
                is_fail_open=False,
            )

        fail_open = weight >= self._weight_fail_open
        fail_verdict: Verdict = "FORWARD" if fail_open else "IGNORE"
        fail_label = "fail-open" if fail_open else "fail-closed"

        prompt = _build_user_prompt(context_snapshot, entry)

        _MAX_PROMPT_LOG = 500
        _MAX_RESPONSE_LOG = 200
        logger.info(
            "Discretion LLM input for source=%s (weight=%.2f):\n%s",
            self._source,
            weight,
            prompt[:_MAX_PROMPT_LOG] + ("…" if len(prompt) > _MAX_PROMPT_LOG else ""),
        )

        try:
            raw = await self._dispatcher.call(prompt, system_prompt=self._system_prompt)
        except TimeoutError:
            logger.warning(
                "Discretion LLM timed out for source=%s (weight=%.2f) — defaulting %s",
                self._source,
                weight,
                fail_verdict,
            )
            discretion_evaluations_total.labels(
                source=self._source,
                verdict=fail_verdict,
                outcome="timeout",
            ).inc()
            return DiscretionResult(
                verdict=fail_verdict,
                reason=f"{fail_label}: timeout",
                is_fail_open=fail_open,
            )
        except Exception as exc:  # noqa: BLE001
            # Log at ERROR with traceback — these are silent killers that
            # cause the model to show 0 usage while messages flow through
            # on the fail-open/closed default.
            logger.error(
                "Discretion LLM error for source=%s (weight=%.2f): %s — defaulting %s",
                self._source,
                weight,
                exc,
                fail_verdict,
                exc_info=True,
            )
            discretion_evaluations_total.labels(
                source=self._source,
                verdict=fail_verdict,
                outcome="error",
            ).inc()
            return DiscretionResult(
                verdict=fail_verdict,
                reason=f"{fail_label}: {type(exc).__name__}",
                is_fail_open=fail_open,
            )

        logger.info(
            "Discretion LLM result for source=%s (weight=%.2f): %s",
            self._source,
            weight,
            raw[:_MAX_RESPONSE_LOG] + ("…" if len(raw) > _MAX_RESPONSE_LOG else ""),
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
            discretion_evaluations_total.labels(
                source=self._source,
                verdict=fail_verdict,
                outcome="parse_error",
            ).inc()
            return DiscretionResult(
                verdict=fail_verdict,
                reason=f"{fail_label}: parse_error",
                is_fail_open=fail_open,
            )

        discretion_evaluations_total.labels(
            source=self._source,
            verdict=verdict,
            outcome="ok",
        ).inc()
        return DiscretionResult(verdict=verdict, reason=reason, is_fail_open=False)
