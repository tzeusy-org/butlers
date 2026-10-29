"""One model-call kernel: every attempt loop runs here (bu-m0vjeb.1).

``call_model`` owns, each in exactly one place:

- **Candidate resolution**: the catalog tier through
  :func:`~butlers.core.model_routing.resolve_model_with_effective_tier` (RFC 0038 D16 resolves
  roles onto tiers before calling in), then operator spend routing rules.
- **The spend gate**: the monthly ceiling before the first attempt and again before each
  relaunch, and each candidate's token quota as a pre-invocation skip. Gate errors behave as
  ``catalog-token-limits`` specifies, inside the check functions themselves.
- **Failover**: failures are classified by
  :func:`~butlers.core.failover_classifier.classify_failover_eligibility`. An eligible failure
  moves to the next same-tier candidate; an ineligible one re-raises. A captured tool call
  makes a failure ineligible, which is RFC 0038 D14's "no capability call" condition for a
  relaunch. ``CallSpec.max_relaunches`` bounds relaunches (D14 goal invocations pass 1), and
  ``MAX_ATTEMPTS`` bounds every call.
- **Accounting**: one ``dispatch_attempts`` row per attempt or skip, token usage per successful
  invocation, and the failover metrics.

The caller supplies only the attempt itself (``attempt_fn``): how a resolved adapter is invoked
for this kind of call. Adapters come from the shared
:class:`~butlers.core.runtimes.registry.AdapterRegistry` (see :func:`registry_adapters`).
"""

from __future__ import annotations

import dataclasses
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import asyncpg

from butlers.core.dispatch_intent import DispatchIntent
from butlers.core.dispatch_outcomes import project_resolution_receipt, record_dispatch_attempt
from butlers.core.failover_classifier import (
    FailoverContext,
    FailoverDecision,
    classify_failover_eligibility,
)
from butlers.core.metrics import ButlerMetrics
from butlers.core.model_routing import (
    CEILING_DENIAL_REASON_PREFIX,
    Complexity,
    apply_spend_routing_rules,
    check_monthly_ceiling,
    check_token_quota,
    next_same_tier_candidate,
    record_token_usage,
    resolve_model_with_effective_tier,
)
from butlers.core.purpose_lane import PURPOSE_LANE_STANDARD, PurposeLane
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.runtimes.registry import AdapterRegistry
from butlers.core.spawner_provider import resolve_provider_config

logger = logging.getLogger(__name__)

#: Attempt slots per call. Each invocation or quota skip consumes one; a defensive backstop
#: against a pathological catalog (many same-tier entries all failing or quota-denied).
MAX_ATTEMPTS: int = 5

#: Catalog-derived log fields are bounded so a malformed row cannot create an unbounded event.
_LOG_FIELD_MAX_CHARS: int = 256

#: Failure reason recorded instead of provider text when the call carries private content.
REDACTED_RUNTIME_FAILURE = "private_content_runtime_failure"


class NoModelConfigured(RuntimeError):
    """The catalog has no enabled entry for the requested tier."""


class SpendCeilingReached(RuntimeError):
    """The owner's monthly spend ceiling refuses the call."""


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One dispatchable catalog entry."""

    runtime_type: str
    model_id: str
    extra_args: list[str]
    catalog_entry_id: uuid.UUID
    session_timeout_s: int

    @classmethod
    def from_row(cls, row: tuple[str, str, list[str], uuid.UUID, int]) -> Candidate:
        return cls(*row)

    def as_row(self) -> tuple[str, str, list[str], uuid.UUID, int]:
        return (
            self.runtime_type,
            self.model_id,
            self.extra_args,
            self.catalog_entry_id,
            self.session_timeout_s,
        )


@dataclasses.dataclass(frozen=True)
class AttemptOutcome:
    """What a successful attempt returns: the response text and the provider's token usage."""

    text: str
    usage: Mapping[str, Any] | None = None


AttemptFn = Callable[[RuntimeAdapter, Candidate], Awaitable[AttemptOutcome]]
AdapterFor = Callable[[Candidate], Awaitable[RuntimeAdapter]]


def registry_adapters(pool: asyncpg.Pool, registry: AdapterRegistry) -> AdapterFor:
    """Adapters from ``registry``, configured with each candidate's provider configuration."""

    async def adapter_for(candidate: Candidate) -> RuntimeAdapter:
        provider_config = await resolve_provider_config(pool, candidate.model_id)
        return registry.for_runtime(candidate.runtime_type, provider_config)

    return adapter_for


@dataclasses.dataclass(frozen=True)
class CallSpec:
    """What one logical model call asks for and how its spend is attributed."""

    butler_name: str
    """Identity for catalog overrides and attempt rows."""
    tier: Complexity | str
    receipt_intent: DispatchIntent | None = None
    spend_rule_trigger: str | None = None
    """``trigger_source`` that spend routing rules match against (``purpose`` conditions)."""
    purpose_lane: PurposeLane = PURPOSE_LANE_STANDARD
    usage_butler_name: str | None = None
    """Ledger attribution for token usage; defaults to ``butler_name``."""
    usage_purpose: str | None = None
    redact_failures: bool = False
    """Record failures without provider text (private-content lane)."""
    enforce_ceiling: bool = True
    max_relaunches: int | None = None
    """Runtime-failure relaunches allowed; ``None`` leaves only ``MAX_ATTEMPTS``."""
    caller: str = "model_call"
    """Label that prefixes this call's log lines."""


@dataclasses.dataclass(frozen=True)
class CallResult:
    text: str
    candidate: Candidate
    attempts: int


class AttemptSink:
    """Where the kernel writes attempt rows, usage and failover metrics.

    Callers subclass it to observe attempts (for example connector auth health); the hooks run
    for every attempt whatever the caller's privacy lane.
    """

    def __init__(self, pool: asyncpg.Pool, *, metrics: ButlerMetrics | None = None) -> None:
        self.pool = pool
        self.metrics = metrics

    async def attempt(self, **fields: Any) -> None:
        await record_dispatch_attempt(self.pool, **fields)

    async def usage(self, **fields: Any) -> None:
        await record_token_usage(self.pool, **fields)

    def on_attempt_start(self, candidate: Candidate) -> None:
        """Called before each candidate's quota check."""

    def on_runtime_failure(self, candidate: Candidate, decision: FailoverDecision) -> None:
        """Called with the unredacted classification of every failed invocation."""


def _bounded(value: str) -> str:
    return value[:_LOG_FIELD_MAX_CHARS]


async def _resolve(
    pool: asyncpg.Pool, spec: CallSpec
) -> tuple[Candidate, str, dict | None, str | None]:
    """The first candidate, its effective tier, the base receipt and any selection reason."""
    receipts: list[Any] = []
    resolved = await resolve_model_with_effective_tier(
        pool,
        spec.butler_name,
        spec.tier,
        receipt_intent=spec.receipt_intent,
        receipt_sink=receipts,
    )
    if resolved is None:
        tier = getattr(spec.tier, "value", spec.tier)
        raise NoModelConfigured(
            f"No {spec.tier} model configured in public.model_catalog. "
            f"Add an enabled entry with complexity_tier={tier!r}."
        )
    *row, effective_tier = resolved
    candidate = Candidate.from_row(tuple(row))
    base_receipt = receipts[-1].describe() if receipts else None

    selection_reason: str | None = None
    if candidate.catalog_entry_id is not None:
        try:
            routed = await apply_spend_routing_rules(
                pool,
                spec.butler_name,
                effective_tier,
                candidate.as_row(),
                trigger_source=spec.spend_rule_trigger,
            )
            rerouted = Candidate.from_row(routed.resolved)
            if routed.max_cost_per_call is not None:
                logger.info(
                    "%s: spend rule set per-call cap $%.4f for model=%s (not enforced pre-call: "
                    "no fixed token budget to estimate worst-case cost against)",
                    spec.caller,
                    routed.max_cost_per_call,
                    rerouted.model_id,
                )
            if rerouted.catalog_entry_id != candidate.catalog_entry_id:
                selection_reason = "spend_rule_override"
            candidate = rerouted
        except Exception:
            logger.warning(
                "%s: spend routing-rule evaluation failed for butler=%s; keeping tier-resolved "
                "model=%s",
                spec.caller,
                spec.butler_name,
                candidate.model_id,
                exc_info=True,
            )
    return candidate, effective_tier, base_receipt, selection_reason


async def _ceiling_gate(
    pool: asyncpg.Pool,
    spec: CallSpec,
    sink: AttemptSink,
    candidate: Candidate,
    *,
    attempt_index: int,
    receipt: dict | None,
) -> None:
    """Refuse the call when the owner's monthly spend ceiling is reached.

    Like the Spawner's gate, it applies only to catalog-resolved candidates.
    """
    if candidate.catalog_entry_id is None:
        return
    ceiling = await check_monthly_ceiling(pool)
    if ceiling.allowed:
        return
    message = (
        f"{CEILING_DENIAL_REASON_PREFIX}: month-to-date ${ceiling.mtd_usd:.2f} >= ceiling "
        f"${ceiling.ceiling_usd:.2f}"
    )
    logger.warning("%s blocked by monthly spend ceiling: %s", spec.caller, message)
    await sink.attempt(
        catalog_entry_id=candidate.catalog_entry_id,
        butler=spec.butler_name,
        outcome="quota_skip",
        attempt_index=attempt_index,
        failure_reason=message,
        purpose_lane=spec.purpose_lane,
        produce_fleet_halt=True,
        resolution_receipt=receipt,
    )
    raise SpendCeilingReached(message)


def _quota_message(candidate: Candidate, quota: Any) -> tuple[str, str]:
    windows: list[str] = []
    if quota.limit_24h is not None and quota.usage_24h >= quota.limit_24h:
        windows.append(f"24h (used={quota.usage_24h}, limit={quota.limit_24h})")
    if quota.limit_30d is not None and quota.usage_30d >= quota.limit_30d:
        windows.append(f"30d (used={quota.usage_30d}, limit={quota.limit_30d})")
    bounded_windows = _bounded("; ".join(windows))
    return (
        f"Token quota exhausted for catalog entry '{_bounded(candidate.model_id)}': "
        f"{bounded_windows}",
        bounded_windows,
    )


async def _record_usage(
    spec: CallSpec, sink: AttemptSink, candidate: Candidate, usage: Mapping[str, Any] | None
) -> None:
    """Record a successful invocation's tokens; the provider consumed them either way."""
    if not usage:
        logger.debug("%s: adapter returned no usage for model=%s", spec.caller, candidate.model_id)
        return
    input_tokens = usage.get("input_tokens")
    if input_tokens is None:
        logger.debug("%s: adapter usage without input_tokens: %s", spec.caller, usage)
        return
    await sink.usage(
        catalog_entry_id=candidate.catalog_entry_id,
        butler_name=spec.usage_butler_name or spec.butler_name,
        session_id=None,
        input_tokens=input_tokens,
        output_tokens=usage.get("output_tokens") or 0,
        purpose=spec.usage_purpose,
        purpose_lane=spec.purpose_lane,
    )


async def call_model(
    pool: asyncpg.Pool,
    spec: CallSpec,
    attempt_fn: AttemptFn,
    *,
    adapter_for: AdapterFor,
    sink: AttemptSink,
) -> CallResult:
    """Run one logical model call through resolution, the spend gate and failover.

    Raises :class:`NoModelConfigured`, :class:`SpendCeilingReached`, the original exception of
    an ineligible failure, or a ``RuntimeError`` whose message starts with
    ``same_tier_failover_exhausted`` once candidates, relaunches or attempt slots run out.
    """
    candidate, effective_tier, base_receipt, selection_reason = await _resolve(pool, spec)
    receipt = project_resolution_receipt(
        base_receipt,
        catalog_entry_id=candidate.catalog_entry_id,
        runtime_type=candidate.runtime_type,
        model_id=candidate.model_id,
        effective_tier=effective_tier,
        attempt_index=0,
        selection_reason=selection_reason,
    )
    if spec.enforce_ceiling:
        await _ceiling_gate(pool, spec, sink, candidate, attempt_index=0, receipt=receipt)

    metrics = sink.metrics
    tier = _bounded(effective_tier)
    attempted_ids: list[uuid.UUID] = []
    attempt_count = 0
    relaunches = 0

    def exhausted(detail: str) -> RuntimeError:
        logger.warning(
            "%s same-tier failover exhausted for tier=%s after %d attempt(s): %s",
            spec.caller,
            tier,
            attempt_count,
            detail,
        )
        if metrics is not None:
            metrics.record_failover_exhausted(tier=tier)
        return RuntimeError(
            f"same_tier_failover_exhausted: tier={tier} after {attempt_count} attempt(s){detail}"
        )

    async def advance(previous_failure: str, reason_label: str) -> Candidate | None:
        nonlocal receipt
        row = await next_same_tier_candidate(pool, spec.butler_name, effective_tier, attempted_ids)
        if row is None:
            return None
        nxt = Candidate.from_row(row)
        logger.info(
            "%s same-tier failover: %s -> %s (tier=%s, reason=%s)",
            spec.caller,
            _bounded(candidate.model_id),
            _bounded(nxt.model_id),
            tier,
            previous_failure,
        )
        if metrics is not None:
            metrics.record_failover_attempt(
                from_model=_bounded(candidate.model_id),
                to_model=_bounded(nxt.model_id),
                reason=reason_label,
            )
        receipt = project_resolution_receipt(
            base_receipt,
            catalog_entry_id=nxt.catalog_entry_id,
            runtime_type=nxt.runtime_type,
            model_id=nxt.model_id,
            effective_tier=effective_tier,
            attempt_index=attempt_count,
            previous_failure_class=previous_failure,
            selection_reason=selection_reason,
        )
        return nxt

    while True:
        attempt_count += 1
        sink.on_attempt_start(candidate)

        quota = await check_token_quota(pool, candidate.catalog_entry_id)
        if not quota.allowed:
            quota_msg, windows = _quota_message(candidate, quota)
            await sink.attempt(
                catalog_entry_id=candidate.catalog_entry_id,
                butler=spec.butler_name,
                outcome="quota_skip",
                attempt_index=attempt_count - 1,
                failure_reason=quota_msg,
                purpose_lane=spec.purpose_lane,
                resolution_receipt=receipt,
            )
            logger.info(
                "%s quota skip: model=%s tier=%s attempt=%d reason=quota_exhausted windows=%s",
                spec.caller,
                _bounded(candidate.model_id),
                tier,
                attempt_count,
                windows,
            )
            attempted_ids.append(candidate.catalog_entry_id)
            if attempt_count >= MAX_ATTEMPTS:
                raise exhausted(f" (safety cap); last quota skip: {quota_msg}")
            nxt = await advance("quota_exhausted", "quota_exhausted")
            if nxt is None:
                raise exhausted(f"; last quota skip: {quota_msg}")
            candidate = nxt
            continue

        if relaunches and spec.enforce_ceiling:
            await _ceiling_gate(
                pool, spec, sink, candidate, attempt_index=attempt_count - 1, receipt=receipt
            )

        adapter = await adapter_for(candidate)
        started = time.monotonic()
        try:
            outcome = await attempt_fn(adapter, candidate)
        except Exception as exc:  # noqa: BLE001 -- classified below
            failure: Exception = exc
        else:
            await _record_usage(spec, sink, candidate, outcome.usage)
            await sink.attempt(
                catalog_entry_id=candidate.catalog_entry_id,
                butler=spec.butler_name,
                outcome="success",
                attempt_index=attempt_count - 1,
                duration_ms=int((time.monotonic() - started) * 1000),
                purpose_lane=spec.purpose_lane,
                resolution_receipt=receipt,
            )
            return CallResult(text=outcome.text, candidate=candidate, attempts=attempt_count)

        decision = classify_failover_eligibility(
            FailoverContext(
                exception=failure,
                tool_calls=list(getattr(failure, "tool_calls", None) or []),
                process_info=adapter.last_process_info,
            )
        )
        await sink.attempt(
            catalog_entry_id=candidate.catalog_entry_id,
            butler=spec.butler_name,
            outcome="runtime_failure",
            attempt_index=attempt_count - 1,
            failure_reason=REDACTED_RUNTIME_FAILURE if spec.redact_failures else decision.reason,
            error_code=None if spec.redact_failures else type(failure).__name__,
            error_message=None if spec.redact_failures else str(failure),
            duration_ms=int((time.monotonic() - started) * 1000),
            purpose_lane=spec.purpose_lane,
            resolution_receipt=receipt,
        )
        sink.on_runtime_failure(candidate, decision)

        if not decision.eligible:
            logger.debug(
                "%s failover suppressed for model=%s: %s",
                spec.caller,
                candidate.model_id,
                decision.reason,
            )
            if metrics is not None:
                metrics.record_failover_suppressed(reason=decision.reason)
            raise failure

        attempted_ids.append(candidate.catalog_entry_id)
        relaunches += 1
        last_error = f"last error: {type(failure).__name__}: {failure}"
        if spec.max_relaunches is not None and relaunches > spec.max_relaunches:
            raise exhausted(f" (relaunch limit); {last_error}") from failure
        if attempt_count >= MAX_ATTEMPTS:
            raise exhausted(f" (safety cap); {last_error}") from failure
        nxt = await advance(decision.reason, decision.reason.split(":")[0])
        if nxt is None:
            raise exhausted(f"; {last_error}") from failure
        candidate = nxt
