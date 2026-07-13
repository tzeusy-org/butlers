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

import asyncpg
from prometheus_client import Counter

from butlers.core.attention_ledger import record_attention_event
from butlers.core.failover_classifier import FailoverContext, classify_failover_eligibility
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

# ``origin_butler`` stamped on the attention-ledger row written when a discretion
# evaluation is suppressed by same-tier failover exhaustion (bu-5go3y). Discretion
# runs inside connectors, not a butler daemon, so there is no butler identity to
# attribute; this stable marker mirrors ``DiscretionDispatcher``'s default
# ``butler_name`` ("__discretion__") already used for token-usage attribution, so
# the ledger's per-``origin_butler`` Trust Console summary groups every inbound
# discretion suppression under one honest, low-cardinality name. The per-source
# identity (chat/mic/etc.) is preserved in the row's ``metadata.source_identity``.
_DISCRETION_LEDGER_ORIGIN = "__discretion__"

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

# Per-CHANNEL discretion-drop counter (bu-cicgb). ``discretion_evaluations_total``
# is labelled by per-source evaluator name (per-chat → high cardinality) and its
# ``outcome`` does not distinguish a genuine LLM IGNORE from an infra fail-closed
# default (an ``llm_verdict`` IGNORE has ``outcome="ok"``). This counter is
# low-cardinality (channel × ignore-kind) so an over-filtering channel — and
# specifically the split between genuine noise (``llm_verdict``) and fabricated
# infra drops (``failover_exhausted`` / ``*_default``) — is visible per channel
# in Prometheus/Grafana without re-sampling private payloads. The audit that
# motivated it found 9/9 recent WhatsApp drops were ``failover_exhausted`` and
# zero were ``llm_verdict``.
discretion_ignore_total = Counter(
    "discretion_ignore_total",
    "Discretion IGNORE (dropped message) outcomes by channel and ignore-kind",
    labelnames=["channel", "kind"],
)
"""Labels:

- channel: originating connector channel (e.g. ``whatsapp``, ``live_listener``)
- kind: :func:`classify_ignore_kind` result — ``llm_verdict`` (genuine noise) vs
  a fail-closed default (``failover_exhausted``, ``auth_failure_default``,
  ``provider_unavailable_default``, ``timeout_default``, ``parse_error_default``,
  ``error_default``).
"""


def record_discretion_ignore(*, channel: str, kind: str) -> None:
    """Increment the per-channel discretion-drop counter for one IGNORE.

    Call this at the connector site that records a discretion IGNORE to
    ``connectors.filtered_events`` (it already computes ``kind`` via
    :func:`classify_ignore_kind`), so the per-channel drop-rate and its
    genuine-vs-infra split are exported without a schema change.
    """
    discretion_ignore_total.labels(channel=channel, kind=kind).inc()


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

    async def call(
        self, prompt: str, system_prompt: str = "", *, identity: str | None = None
    ) -> str:
        """Invoke the LLM with *prompt* and return the raw response text.

        ``identity`` is an optional per-connector spend-attribution label
        (see :meth:`~butlers.connectors.discretion_dispatcher.DiscretionDispatcher.call`);
        implementations that don't care about spend attribution may ignore it.
        """
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


def _classify_default_error(exc: Exception) -> str:
    """Classify a discretion-call exception for the fail-open/fail-closed reason string.

    Distinguishes three systemic failure modes that would otherwise collapse
    into an opaque exception class name (bu-n0336, from bu-ofo3i's diagnosis
    that weight<0.5 senders silently fail-closed on a never-provisioned CLI
    auth token with zero discrimination from any other error):

    - ``"failover_exhausted"``: every same-tier model candidate failed (the
      ``RuntimeError`` :class:`~butlers.connectors.discretion_dispatcher.DiscretionDispatcher`
      raises when its own same-tier failover loop is exhausted).
    - ``"auth_failure"``: a genuine provider/auth-classified failure (e.g. a
      missing or revoked CLI auth token). Reuses
      :func:`~butlers.core.failover_classifier.classify_failover_eligibility` —
      the same default-closed marker list ``DiscretionDispatcher``'s same-tier
      failover and connector ``/status`` auth health already key off — so the
      marker patterns live in exactly one place.
    - ``"provider_unavailable"``: a provider/backend availability failure
      (connection refused, service unavailable, bad gateway, etc.) — distinct
      from ``"auth_failure"`` since bu-ujm9d split the classifier's marker
      buckets: a connectivity blip is not an identity/credential rejection and
      must not be attributed to auth in this audit trail.

    Falls back to the raw exception class name for anything else. Timeouts and
    unparseable responses are classified by their own call sites, not here.
    """
    if str(exc).startswith("same_tier_failover_exhausted"):
        return "failover_exhausted"
    decision = classify_failover_eligibility(FailoverContext(exception=exc))
    if decision.reason.startswith("provider_auth_error"):
        return "auth_failure"
    if decision.reason.startswith("provider_unavailable"):
        return "provider_unavailable"
    return type(exc).__name__


def classify_ignore_kind(result: DiscretionResult) -> str:
    """Classify an IGNORE :class:`DiscretionResult` into a stable filter-reason kind.

    Distinguishes a genuine LLM-judged IGNORE from the various fail-closed
    default outcomes so ``connectors.filtered_events`` rows are queryable
    without re-sampling raw payloads (bu-n0336, from bu-ofo3i/bu-cicgb: the
    WhatsApp 90%-drop audit could not tell "the LLM judged this noise" apart
    from "Codex CLI 401'd and the low-trust default kicked in").

    Only meaningful when ``result.verdict == "IGNORE"``; callers should check
    the verdict first. Returns one of:

    - ``"llm_verdict"`` — the LLM actually ran and returned IGNORE.
    - ``"auth_failure_default"`` — fail-closed default from a genuine
      provider/auth failure (e.g. a missing or revoked CLI auth token).
    - ``"provider_unavailable_default"`` — fail-closed default from a
      provider/backend availability failure (connection refused, service
      unavailable, bad gateway, etc.). Split from ``"auth_failure_default"``
      (bu-ujm9d) — a connectivity blip is not an identity/credential
      rejection and must not masquerade as one in the audit taxonomy.
    - ``"failover_exhausted"`` — fail-closed default after every same-tier
      model candidate failed.
    - ``"timeout_default"`` — fail-closed default from an LLM call timeout.
    - ``"parse_error_default"`` — fail-closed default from an unparseable LLM
      response.
    - ``"error_default"`` — fail-closed default from any other exception.
    """
    reason = result.reason
    if not reason:
        return "llm_verdict"
    if reason.startswith("fail-closed: auth_failure"):
        return "auth_failure_default"
    if reason.startswith("fail-closed: provider_unavailable"):
        return "provider_unavailable_default"
    if reason.startswith("fail-closed: failover_exhausted"):
        return "failover_exhausted"
    if reason.startswith("fail-closed: timeout"):
        return "timeout_default"
    if reason.startswith("fail-closed: parse_error"):
        return "parse_error_default"
    return "error_default"


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
        ledger_pool: asyncpg.Pool | None = None,
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
        # DB pool for the best-effort failover-exhausted suppression ledger write
        # (bu-5go3y). Defaults to the dispatcher's own pool so the six existing
        # connector construction sites are wired automatically — the evaluator is
        # always injected with a DiscretionDispatcher, which owns the pool. Falls
        # back to None (write no-ops) when the dispatcher exposes no ``pool`` (e.g.
        # a mock caller in tests); an explicit ``ledger_pool`` overrides both.
        self._ledger_pool: asyncpg.Pool | None = (
            ledger_pool if ledger_pool is not None else getattr(dispatcher, "pool", None)
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
            raw = await self._dispatcher.call(
                prompt, system_prompt=self._system_prompt, identity=self._source
            )
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
            default_reason = _classify_default_error(exc)
            # Durably record ONLY the failover-exhausted weight-default IGNORE —
            # a degraded, fabricated suppression that silently drops a message
            # the owner would otherwise have seen (bu-5go3y). A fail-OPEN default
            # (FORWARD) still reaches the pipeline, so it is not an honesty gap;
            # and any non-failover error class (auth_failure, provider_unavailable,
            # timeout, parse_error, opaque exception) is out of this bead's scope —
            # classify-before-flagging. Best-effort/fail-open: the ledger write
            # never alters the discretion verdict returned below.
            if not fail_open and default_reason == "failover_exhausted":
                await self._record_failover_suppression(exc, weight=weight, channel=channel)
            return DiscretionResult(
                verdict=fail_verdict,
                reason=f"{fail_label}: {default_reason}",
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

    async def _record_failover_suppression(
        self,
        exc: Exception,
        *,
        weight: float,
        channel: str | None,
    ) -> None:
        """Best-effort attention-ledger row for a failover-exhausted IGNORE (bu-5go3y).

        Records the one inbound honesty gap the ledger tracks: a message the
        owner would have received had the discretion pipeline not degraded, but
        which was suppressed because same-tier model failover exhausted and the
        weight-default IGNORE verdict (a fabricated suppression, not a
        model-judged decision) kicked in.

        Fail-open, mirroring ``_notifications._record_failed_attention``:
        :func:`~butlers.core.attention_ledger.record_attention_event` never
        raises and no-ops when the pool is absent, and the whole call is wrapped
        defensively so a ledger hiccup can never alter or break the discretion
        verdict the caller is about to return.
        """
        try:
            await record_attention_event(
                self._ledger_pool,
                origin_butler=_DISCRETION_LEDGER_ORIGIN,
                source="discretion",
                outcome="suppressed",
                channel=channel,
                intent="discretion",
                reason="failover_exhausted",
                metadata={
                    "reason": "failover_exhausted",
                    "weight_default": True,
                    "verdict": "IGNORE",
                    "source_identity": self._source,
                    "weight": weight,
                    # str(exc) carries the dispatcher's terminal message —
                    # ``same_tier_failover_exhausted: tier=<tier> after N
                    # attempt(s); ...`` — so the tier and attempt count are
                    # preserved for querying without brittle parsing here.
                    "detail": str(exc)[:500],
                },
            )
        except Exception:  # noqa: BLE001 — never let ledger trouble touch discretion
            logger.warning(
                "Discretion failover-exhausted ledger write failed for source=%s",
                self._source,
                exc_info=True,
            )
