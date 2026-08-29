"""Dynamic model routing — catalog-based model selection with per-butler overrides.

Provides:
- ``Complexity`` enum (canonical six: reasoning / workhorse / cheap / specialty / local / legacy)
- ``coerce_complexity_tier(value, strict=True)`` — normalizes a caller-supplied complexity
  string (LLM tool argument, structured-classification output, etc.) to a canonical
  ``Complexity``, gracefully remapping retired vocabulary and either raising a clear
  ``ValueError`` (``strict=True``) or fail-open defaulting to ``workhorse``
  (``strict=False``) on genuinely unrecognized values.
- ``resolve_model(pool, butler_name, complexity_tier)`` — highest-priority enabled model
  in tier whose state ∈ {verified, untested}; falls through canonical tier order if none
  qualify in the requested tier.
- ``resolve_model_with_effective_tier(pool, butler_name, complexity_tier)`` — same as
  ``resolve_model`` but also returns the effective tier that produced the candidate (needed
  for same-tier failover to stay within the resolved tier). ``quota_aware=True`` (bu-k9te9)
  folds the pre-spawn token-quota gate into this same round trip; see
  ``TierQuotaExhausted`` and the function's own docstring for the exact contract.
- ``next_same_tier_candidate(pool, butler_name, effective_tier, attempted_ids)`` — returns
  the next eligible model in an exact effective complexity tier, excluding already-attempted
  catalog entry IDs.  Used by the spawner failover loop to iterate within the same tier.
- ``QuotaStatus`` dataclass — result of a pre-spawn token quota check.
- ``check_token_quota(pool, catalog_entry_id)`` — CTE-based single-query quota check.
- ``price_mtd_from_ledger(pool)`` — prices month-to-date spend from
  ``public.token_usage_ledger``; the single source of truth shared by
  ``check_monthly_ceiling`` (spawn gate) and the dashboard's
  ``GET /api/spend/forecast`` (bu-7o89u.1) so the two can never diverge.
- ``check_monthly_ceiling(pool)`` — pre-spawn monthly USD spend-ceiling check.
- ``record_token_usage(pool, ...)`` — best-effort ledger INSERT.
- Bounded routing-decision cache (bu-k9te9) — quota-unaware ``resolve_model`` /
  ``resolve_model_with_effective_tier`` calls serve the ``_RESOLVE_SQL`` tier/breaker/
  priority resolution from a short-TTL, size-bounded in-process cache;
  ``clear_routing_decision_cache()`` drops it (test use). Never applies to the
  quota-aware path, ``next_same_tier_candidate``, or any invoked session.

Resolution strategy (§3.2 routing contract)
--------------------------------------------
For a given ``butler_name`` and ``complexity_tier``:

1. Join ``public.model_catalog mc`` with ``public.butler_model_overrides bmo``
   on ``bmo.butler_name = $butler_name AND bmo.catalog_entry_id = mc.id``.
2. Effective enabled:  ``COALESCE(bmo.enabled, mc.enabled)``
3. Effective priority: ``COALESCE(bmo.priority, mc.priority)``
4. Effective tier:     ``COALESCE(bmo.complexity_tier, mc.complexity_tier)``
5. Filter: effective enabled = true AND effective tier = $complexity_tier
   AND state ∈ {verified, untested} (where state column does not yet exist,
   state is treated as always untested/verified — all enabled entries qualify).
6. Select the highest-priority enabled entry.  Among ties at the same priority,
   use a round-robin counter in ``public.model_round_robin_counters``.
7. If no entry qualifies in the requested tier, fall through to the next tier
   in canonical order: reasoning → workhorse → cheap → specialty → local → legacy.
8. Return the selected row as (runtime_type, model_id, extra_args,
   catalog_entry_id, session_timeout_s), or None if no matching entries exist
   in any tier at or below the requested tier.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import asyncpg

from butlers.core.dispatch_intent import (
    DISPATCH_POLICY_VERSION,
    DispatchIntent,
    FitCode,
    FitFinding,
    FitVerdict,
    evaluate_fit,
)
from butlers.core.model_capabilities import (
    CapabilityDescriptor,
    CapabilityDescriptorError,
    effective_capabilities,
)

if TYPE_CHECKING:
    from butlers.core.pricing import PricingConfig

logger = logging.getLogger(__name__)


class Complexity(enum.StrEnum):
    """Canonical complexity tiers used for model selection.

    Canonical order (highest to lowest capability):
        reasoning → workhorse → cheap → specialty → local → legacy

    Old vocabulary (trivial/medium/high/extra_high/discretion/self_healing) was
    retired in migration core_092.  Any code still emitting the old values will
    trigger a loud deprecation warning via ``_check_deprecated_tier()``.
    """

    REASONING = "reasoning"
    WORKHORSE = "workhorse"
    CHEAP = "cheap"
    SPECIALTY = "specialty"
    LOCAL = "local"
    LEGACY = "legacy"


# Canonical fallthrough order for §3.2 routing contract.
TIER_FALLTHROUGH_ORDER: tuple[str, ...] = (
    "reasoning",
    "workhorse",
    "cheap",
    "specialty",
    "local",
    "legacy",
)

# Mapping from old vocabulary to new (for deprecation shim).
_DEPRECATED_TIER_MAP: dict[str, str] = {
    "trivial": "cheap",
    "medium": "workhorse",
    "high": "reasoning",
    "extra_high": "reasoning",
    "discretion": "specialty",
    "self_healing": "specialty",
}


def _check_deprecated_tier(tier_value: str) -> str:
    """Fail-loud on legacy tier vocabulary; remap and log a deprecation warning.

    Callers that have not been updated to the new canonical tier names will
    see a loud WARNING in the application logs.  The call is NOT silently
    accepted — this function remaps the value but always logs so the caller
    is visible and can be fixed.

    Parameters
    ----------
    tier_value:
        The raw tier string provided by the caller.

    Returns
    -------
    str
        The canonical tier value (possibly remapped from deprecated vocabulary).
    """
    if tier_value in _DEPRECATED_TIER_MAP:
        canonical = _DEPRECATED_TIER_MAP[tier_value]
        logger.warning(
            "DEPRECATED complexity_tier value %r received — caller must be updated. "
            "Remapping to canonical value %r. "
            "Old vocabulary (trivial/medium/high/extra_high/discretion/self_healing) "
            "was retired in migration core_092.",
            tier_value,
            canonical,
        )
        return canonical
    return tier_value


def coerce_complexity_tier(value: str | None, *, strict: bool = True) -> Complexity:
    """Normalize a caller-supplied complexity tier string to a canonical ``Complexity``.

    Shared entry point for LLM-facing tool parameters (``route_to_butler``'s
    and ``trigger``'s ``complexity`` args, and any structured-classification
    schema output) where the caller may be a stale LLM prompt, cached memory,
    or an old session transcript that still uses the pre-core_092 vocabulary
    (trivial/medium/high/extra_high/discretion/self_healing).

    Handles three cases:
    - Missing/empty ``value`` -> ``Complexity.WORKHORSE`` (the canonical
      default used across the routing/spawn/schedule surfaces).
    - A canonical or retired tier value -> returned as the matching
      ``Complexity`` member. Retired values are remapped via
      ``_DEPRECATED_TIER_MAP`` (through ``_check_deprecated_tier``, which
      always logs a deprecation warning) so a stale caller degrades
      gracefully instead of crashing the tool call.
    - Any other unrecognized value:
      - ``strict=True`` (default): raises ``ValueError`` naming the valid
        tiers — appropriate for tool entry points where a clear, actionable
        tool-call error lets the caller retry with a valid value, rather than
        opaquely surfacing ``Complexity(value)``'s own ``ValueError``.
      - ``strict=False``: logs a warning and falls back to
        ``Complexity.WORKHORSE`` instead of raising — appropriate for
        fail-open routing hot paths (e.g. ``route_to_butler``) that must
        never crash an entire classification session over one cosmetic
        parameter.
    """
    normalized = value.strip().lower() if isinstance(value, str) else ""
    if not normalized:
        return Complexity.WORKHORSE
    try:
        return Complexity(normalized)
    except ValueError:
        pass
    if normalized in _DEPRECATED_TIER_MAP:
        return Complexity(_check_deprecated_tier(normalized))
    if strict:
        valid_tiers = ", ".join(tier.value for tier in Complexity)
        legacy_tiers = ", ".join(_DEPRECATED_TIER_MAP)
        raise ValueError(
            f"Invalid complexity tier {value!r}. Must be one of: {valid_tiers} "
            f"(legacy aliases still accepted: {legacy_tiers})."
        )
    logger.warning(
        "Unrecognized complexity tier %r — defaulting to %r. "
        "Valid tiers: reasoning/workhorse/cheap/specialty/local/legacy.",
        value,
        Complexity.WORKHORSE.value,
    )
    return Complexity.WORKHORSE


@dataclasses.dataclass
class QuotaStatus:
    """Result of a pre-spawn token quota check.

    Attributes
    ----------
    allowed:
        True when the spawn is permitted (usage is within limits or entry is unlimited).
    usage_24h:
        Total tokens consumed in the 24-hour rolling window.
    limit_24h:
        Configured 24h token budget, or ``None`` if unlimited.
    usage_30d:
        Total tokens consumed in the 30-day rolling window.
    limit_30d:
        Configured 30d token budget, or ``None`` if unlimited.
    """

    allowed: bool
    usage_24h: int
    limit_24h: int | None
    usage_30d: int
    limit_30d: int | None


class TierQuotaExhausted(Exception):
    """Raised by ``resolve_model_with_effective_tier(..., quota_aware=True)``.

    Signals that the winning tier had at least one breaker-ok candidate (so
    this is NOT the "no candidates at all" condition that triggers
    static-fallback), but the SQL-embedded quota fold (bu-ep4ks.13 follow-up,
    "fold the quota/ceiling pre-spawn gates into the resolve CTE") could not
    prove every top-priority candidate has quota headroom. The caller must
    fall back to the pre-existing sequential ``check_token_quota`` +
    ``next_same_tier_candidate`` gate loop starting from ``representative``
    (the same tie-break winner a quota-unaware resolve would have produced)
    rather than treating this as "resolution found nothing".

    Deliberately conservative: raised whenever ANY top-priority candidate is
    quota-blocked, even if a DIFFERENT tied peer is quota-ok — see
    ``resolve_model_with_effective_tier``'s docstring for why the fold does
    not attempt to pick among a mixed quota-ok/quota-blocked tie itself.
    """

    def __init__(
        self,
        *,
        effective_tier: str,
        representative: tuple[str, str, list[str], uuid.UUID, int, str],
        resolution: DispatchResolution | None = None,
    ) -> None:
        self.effective_tier = effective_tier
        self.representative = representative
        # bu-6jv4m.7: the intent-aware path (``resolve_dispatch``) has already built a
        # full resolution receipt by the time it discovers the quota block. Carrying it
        # on the exception keeps the receipt available on the failure path too -- a
        # quota-exhausted dispatch is exactly when "which candidates were considered,
        # and why was each one out?" is most worth having. ``None`` on the legacy path.
        self.resolution = resolution
        super().__init__(
            f"Tier {effective_tier!r} has a quota-blocked top-priority candidate; "
            "caller must run the sequential quota/failover gate starting from "
            f"{representative[1]!r}"
        )


@dataclasses.dataclass
class SpendRoutingResult:
    """Result of evaluating operator spend-routing rules against a dispatch.

    Attributes
    ----------
    resolved:
        The (possibly rule-overridden) model tuple
        ``(runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s)``.
        Equal to the input ``resolved`` when no rule re-routed the model.
    max_cost_per_call:
        The per-call USD cap from the first matching rule's ``action.max_cost_per_call``
        effect, or ``None`` when the matching rule sets no cap (or no rule matched).
        The cap is a hard per-dispatch budget the spawner enforces as a DENY gate —
        distinct from the global monthly ceiling.
    breaker_open:
        The live dispatch-outcome circuit-breaker state (bu-hmdqz.2) of the
        rule-selected model *at rule-resolution time*, but ONLY when that model's
        breaker is open. ``None`` when no rule re-routed the model, when the
        rule-selected model's breaker is closed, or when the breaker probe
        failed (fail-open). Operator spend rules are an explicit human override,
        so a breaker-open target is honored — NOT silently excluded (bu-14j0m,
        decision (b)). This field lets the spawner record the breaker-open fact
        on the dispatch-attempt trail so the "why did this session fail" trail
        shows the breaker was open when the operator rule selected the model;
        the existing same-tier failover machinery still handles any real
        dispatch failure (``next_same_tier_candidate`` excludes breaker-open
        entries).
    """

    resolved: tuple[str, str, list[str], uuid.UUID, int]
    max_cost_per_call: float | None = None
    breaker_open: BreakerState | None = None


# Shared with the ceiling-deny message the spawner builds below and the
# validated runtime-attention fleet-halt producer, which reads this exact
# prefix from public.model_dispatch_attempts.failure_reason. Also mirrored
# (as a plain string literal, cross-language) by the frontend's
# CEILING_DENIAL_REASON_PREFIX in frontend/src/hooks/use-fleet-halt.ts — keep
# both in sync if this text ever changes.
CEILING_DENIAL_REASON_PREFIX = "Monthly spend ceiling reached"

# Recorded on a ``public.model_dispatch_attempts`` row (outcome
# ``breaker_open_override``) when an operator spend rule routes to a model whose
# dispatch-outcome circuit breaker is open (bu-14j0m, decision (b): honor the
# rule, warn visibly, do NOT silently exclude). Mirrors the ``failure_reason``
# prefix idiom used by ``CEILING_DENIAL_REASON_PREFIX`` so the fact is
# greppable on the dispatch-attempt trail. The row is informational only — its
# outcome is deliberately NOT ``runtime_failure``/``success`` so the breaker
# CTE (which counts only those two) neither trips nor resets on it.
BREAKER_OPEN_RULE_OVERRIDE_REASON_PREFIX = "Spend rule routed to breaker-open model"

# The ``outcome`` value for the informational breaker-open-override attempt row
# above. Distinct from the failover outcome vocabulary; ignored by the breaker
# derivation (see ``_BREAKER_OPEN_CTE``).
BREAKER_OPEN_RULE_OVERRIDE_OUTCOME = "breaker_open_override"


@dataclasses.dataclass
class CeilingStatus:
    """Result of a pre-spawn monthly spend-ceiling check.

    Attributes
    ----------
    allowed:
        True when the spawn is permitted (current-month spend is below the
        configured ceiling, or no ceiling is configured).
    mtd_usd:
        Estimated month-to-date spend in USD, computed from the token-usage
        ledger priced via the pricing catalog.
    ceiling_usd:
        Configured monthly USD ceiling, or ``None`` when no ceiling is set.
    unpriced_models:
        Executed models with ledger usage but no configured price. Their usage
        is deliberately excluded from ``mtd_usd`` rather than treated as free.
    """

    allowed: bool
    mtd_usd: float
    ceiling_usd: float | None
    unpriced_models: tuple[UnpricedModelUsage, ...] = ()


@dataclasses.dataclass(frozen=True)
class UnpricedModelUsage:
    """Observed ledger usage whose model has no configured price."""

    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_creation_tokens: int

    @property
    def total_tokens(self) -> int:
        """Return all four token buckets for compact operator summaries."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cached_input_tokens
            + self.cache_creation_tokens
        )


@dataclasses.dataclass(frozen=True)
class LedgerSpend:
    """A priced ledger subtotal plus the models excluded for missing prices."""

    cost_usd: float
    unpriced_models: tuple[UnpricedModelUsage, ...] = ()


# ---------------------------------------------------------------------------
# Dispatch-outcome circuit breaker (bu-hmdqz.2)
# ---------------------------------------------------------------------------
#
# Fully derived from public.model_dispatch_attempts — no new columns, no new
# table, no migration. A catalog entry's breaker is "open" (excluded from
# resolution) when its most recent _BREAKER_FAILURE_THRESHOLD attempts that
# reached an outcome of 'runtime_failure' or 'success' are ALL
# 'runtime_failure', AND the most recent such attempt is within
# _BREAKER_HALF_OPEN_COOLDOWN_MINUTES. 'suppressed'/'quota_skip'/'exhausted'
# rows are ignored for this count — they are not systemic pre-invocation
# failure signals about the *model*, so they neither trip nor reset the
# breaker.
#
# Half-open probe: once the cooldown elapses since the last failure, the CTE
# below stops excluding the entry — the very next resolution is a live probe.
# If that probe fails, a fresh 'runtime_failure' row extends the window and
# the breaker re-opens for another cooldown period. If it succeeds, the
# trailing-window bool_and() flips to false and the breaker closes. No
# explicit "probe token"/state column is needed: the resolver's own selection
# frequency IS the probe cadence, since an open breaker means the entry is
# never selected (and thus never re-attempted) until the cooldown passes.
_BREAKER_FAILURE_THRESHOLD = 5
_BREAKER_HALF_OPEN_COOLDOWN_MINUTES = 15

# Inlined as a CTE into every resolver query that picks a live dispatch
# candidate from public.model_catalog. References only fixed module
# constants (not caller input), so it is safe to inline as a literal.
#
# Bounded per catalog entry (bu-ep4ks.13), not a window over the whole table:
# the original form ran ROW_NUMBER() OVER (PARTITION BY catalog_entry_id ...)
# across every row public.model_dispatch_attempts has EVER held, for every
# butler and every model, on every single dispatch resolution -- a full-table
# scan that only grows as the fleet dispatches. public.model_catalog is small
# (order of dozens of rows) and bounded, so this instead runs one correlated
# subquery per catalog entry ("for THIS mc.id, are its most recent
# _BREAKER_FAILURE_THRESHOLD qualifying attempts all runtime_failure") which
# Postgres can satisfy with an index-range scan on
# idx_model_dispatch_attempts_catalog_ts_id (catalog_entry_id, ts DESC, id
# DESC) LIMIT
# _BREAKER_FAILURE_THRESHOLD, instead of touching the rest of the table.
# Same trigger condition, same result set -- see
# tests/core/test_model_routing.py's breaker scenarios, unchanged by this
# rewrite.
_BREAKER_OPEN_CTE = f"""
breaker_open AS (
    SELECT mc.id AS catalog_entry_id
    FROM public.model_catalog mc
    WHERE EXISTS (
        SELECT 1
        FROM (
            SELECT outcome, ts
            FROM public.model_dispatch_attempts
            WHERE catalog_entry_id = mc.id
              AND outcome IN ('runtime_failure', 'success')
            ORDER BY ts DESC, id DESC
            LIMIT {_BREAKER_FAILURE_THRESHOLD}
        ) br
        HAVING
            COUNT(*) >= {_BREAKER_FAILURE_THRESHOLD}
            AND bool_and(br.outcome = 'runtime_failure')
            AND now() - MAX(br.ts) < interval '{_BREAKER_HALF_OPEN_COOLDOWN_MINUTES} minutes'
    )
)
"""


# ---------------------------------------------------------------------------
# Quota fold for the resolve CTE (bu-ep4ks.13 follow-up / bu-k9te9)
# ---------------------------------------------------------------------------
#
# Mirrors ``_QUOTA_CHECK_SQL``'s exact boundary semantics (``>=`` blocks,
# GREATEST(reset_at, window_start) exclusion window, "no token_limits row ==
# unlimited" fast path) as a per-catalog-entry correlated computation, the
# same shape as ``_BREAKER_OPEN_CTE``: one row per ``public.model_catalog``
# entry (bounded, small, catalog-sized -- not a scan of
# ``token_usage_ledger``), each deciding its own quota_ok via two correlated
# subqueries scoped by ``catalog_entry_id`` (index-bound via
# ``idx_ledger_entry_time (catalog_entry_id, recorded_at)``, created in
# core_004 alongside the table).
#
# This CTE answers "does this entry currently have quota headroom" -- it does
# NOT decide selection. ``_RESOLVE_SQL`` only uses it to annotate rows; the
# quota-aware callers (``resolve_model_with_effective_tier(quota_aware=True)``)
# decide what to do with the annotation. A LEFT JOIN means an entry with no
# ``token_limits`` row gets ``quota_ok = true`` (both limit checks are NULL,
# so neither can trip), identical to ``check_token_quota``'s fast path.
_QUOTA_OK_CTE = """
quota_ok_candidates AS (
    SELECT
        mc.id AS catalog_entry_id,
        NOT (
            (
                tl.limit_24h IS NOT NULL
                AND (
                    SELECT COALESCE(SUM(tul.input_tokens + tul.output_tokens), 0)
                    FROM public.token_usage_ledger tul
                    WHERE tul.catalog_entry_id = mc.id
                      AND tul.recorded_at > GREATEST(
                          COALESCE(tl.reset_24h_at, '-infinity'::timestamptz),
                          now() - interval '24 hours'
                      )
                ) >= tl.limit_24h
            )
            OR (
                tl.limit_30d IS NOT NULL
                AND (
                    SELECT COALESCE(SUM(tul.input_tokens + tul.output_tokens), 0)
                    FROM public.token_usage_ledger tul
                    WHERE tul.catalog_entry_id = mc.id
                      AND tul.recorded_at > GREATEST(
                          COALESCE(tl.reset_30d_at, '-infinity'::timestamptz),
                          now() - interval '30 days'
                      )
                ) >= tl.limit_30d
            )
        ) AS quota_ok
    FROM public.model_catalog mc
    LEFT JOIN public.token_limits tl ON tl.catalog_entry_id = mc.id
)
"""


def _breaker_recent_cte(*, filter_by_ids: bool) -> str:
    """Build the ``breaker_recent``/``breaker_open`` CTE pair.

    ``filter_by_ids`` pushes a ``catalog_entry_id`` predicate into
    ``breaker_recent`` itself so Postgres can use the
    ``idx_model_dispatch_attempts_catalog_ts_id (catalog_entry_id, ts DESC,
    id DESC)``
    index instead of windowing the entire ``model_dispatch_attempts`` table
    on every call. Only safe when the caller supplies concrete entry ids
    (single-entry and batch-with-ids callers); ``get_breaker_states(pool)``
    with no ids still needs every entry, so that one call stays unfiltered.
    (The resolver's own ``_BREAKER_OPEN_CTE`` is a separate, always
    catalog-bounded query -- see its docstring, bu-ep4ks.13.)
    """
    id_filter = "AND catalog_entry_id = ANY($1)" if filter_by_ids else ""
    return f"""
    breaker_recent AS (
        SELECT
            catalog_entry_id,
            outcome,
            ts,
            ROW_NUMBER() OVER (
                PARTITION BY catalog_entry_id ORDER BY ts DESC, id DESC
            ) AS rn
        FROM public.model_dispatch_attempts
        WHERE outcome IN ('runtime_failure', 'success')
        {id_filter}
    ),
    breaker_open AS (
        SELECT catalog_entry_id
        FROM breaker_recent
        WHERE rn <= {_BREAKER_FAILURE_THRESHOLD}
        GROUP BY catalog_entry_id
        HAVING
            COUNT(*) >= {_BREAKER_FAILURE_THRESHOLD}
            AND bool_and(outcome = 'runtime_failure')
            AND now() - MAX(ts) < interval '{_BREAKER_HALF_OPEN_COOLDOWN_MINUTES} minutes'
    )
    """


async def get_breaker_state(
    pool: asyncpg.Pool | asyncpg.Connection,
    catalog_entry_id: uuid.UUID,
) -> BreakerState:
    """Return the live derived breaker state for one catalog entry.

    Used transaction-locally by the atomic dispatch-outcome recorder and by
    the Models tab list endpoint to surface the routing consequence
    ("excluded by breaker") without duplicating the threshold/cooldown logic
    baked into ``_BREAKER_OPEN_CTE``. Filters ``breaker_recent`` to this one
    entry so the query stays index-bound
    (``idx_model_dispatch_attempts_catalog_ts_id``) regardless of dispatch
    history size.
    """
    cte = _breaker_recent_cte(filter_by_ids=True)
    row = await pool.fetchrow(
        f"""
        WITH {cte}
        SELECT
            (SELECT COUNT(*) FROM breaker_recent
             WHERE rn <= {_BREAKER_FAILURE_THRESHOLD}
               AND outcome = 'runtime_failure') AS consecutive_failures,
            (SELECT MAX(ts) FROM breaker_recent) AS last_attempt_at,
            EXISTS (SELECT 1 FROM breaker_open) AS is_open
        """,
        [catalog_entry_id],
    )
    if row is None:
        return BreakerState(open=False, consecutive_failures=0, last_attempt_at=None)
    return BreakerState(
        open=bool(row["is_open"]),
        consecutive_failures=int(row["consecutive_failures"] or 0),
        last_attempt_at=row["last_attempt_at"],
    )


async def get_breaker_states(
    pool: asyncpg.Pool, catalog_entry_ids: list[uuid.UUID] | None = None
) -> dict[uuid.UUID, BreakerState]:
    """Batch variant of ``get_breaker_state`` — one round trip for the whole catalog.

    Used by ``GET /api/settings/models`` to annotate every row without an
    N+1 query. When ``catalog_entry_ids`` is ``None``, returns state for every
    entry that has any recent (runtime_failure|success) dispatch history
    (unfiltered scan — no concrete id set to push down). When
    ``catalog_entry_ids`` is provided, filters ``breaker_recent`` to those ids
    so Postgres can use ``idx_model_dispatch_attempts_catalog_ts_id`` instead of
    windowing the whole table on every Models tab page load.
    """
    cte = _breaker_recent_cte(filter_by_ids=catalog_entry_ids is not None)
    query = f"""
        WITH {cte}
        SELECT
            br.catalog_entry_id,
            COUNT(*) FILTER (WHERE br.outcome = 'runtime_failure') AS consecutive_failures,
            MAX(br.ts) AS last_attempt_at,
            bo.catalog_entry_id IS NOT NULL AS is_open
        FROM breaker_recent br
        LEFT JOIN breaker_open bo ON bo.catalog_entry_id = br.catalog_entry_id
        WHERE br.rn <= {_BREAKER_FAILURE_THRESHOLD}
        GROUP BY br.catalog_entry_id, bo.catalog_entry_id
        """
    rows = (
        await pool.fetch(query, catalog_entry_ids)
        if catalog_entry_ids is not None
        else await pool.fetch(query)
    )
    states = {
        row["catalog_entry_id"]: BreakerState(
            open=bool(row["is_open"]),
            consecutive_failures=int(row["consecutive_failures"] or 0),
            last_attempt_at=row["last_attempt_at"],
        )
        for row in rows
    }
    if catalog_entry_ids is not None:
        return {cid: states.get(cid, BreakerState(False, 0, None)) for cid in catalog_entry_ids}
    return states


@dataclasses.dataclass(frozen=True)
class BreakerState:
    """Derived dispatch-outcome circuit-breaker state for one catalog entry.

    Attributes
    ----------
    open:
        True when the entry is currently excluded from resolution (the last
        ``_BREAKER_FAILURE_THRESHOLD`` runtime_failure/success attempts were
        all failures, most recently within the half-open cooldown window).
    consecutive_failures:
        Count of trailing-window attempts that were 'runtime_failure' (capped
        at ``_BREAKER_FAILURE_THRESHOLD`` by construction).
    last_attempt_at:
        Timestamp of the most recent runtime_failure/success attempt, or
        ``None`` when the entry has no such history.
    """

    open: bool
    consecutive_failures: int
    last_attempt_at: datetime | None


# ---------------------------------------------------------------------------
# Evidence-based routing score (bu-ep4ks.13)
# ---------------------------------------------------------------------------
#
# The dispatch-outcome breaker (above) answers "is this entry systemically
# broken" with a hard exclude. This answers a softer question the breaker
# does not: among several enabled, non-broken, same-priority candidates,
# which one has actually been fast, cheap, and reliable recently? Before this,
# ``_RESOLVE_SQL`` broke priority ties with a blind round-robin counter --
# duration_ms was computed by the spawner for the audit log and ledger, then
# discarded before it ever reached ``public.model_dispatch_attempts``, so a
# working-but-slow model (cf. the 436s opencode incident) was rotated into
# exactly as often as a fast one.
#
# Evidence-gated, not evidence-mandatory: a candidate needs
# ``_EVIDENCE_MIN_SAMPLES`` recent success/runtime_failure attempts before its
# score counts for anything. Below that, ``compute_routing_score`` returns
# ``score=None`` and the tie-break degrades to the original round-robin
# counter -- a brand-new or rarely-used model is never penalized for lacking
# history, and the routing decision never fabricates confidence it doesn't
# have (the same doctrine the Models-tab degraded-source fields follow).
_EVIDENCE_WINDOW_DAYS = 7
_EVIDENCE_MIN_SAMPLES = 5

# A fixed token profile used only to rank candidates against each other on a
# comparable per-call USD basis -- not a prediction of any real call's cost.
_SCORE_REFERENCE_INPUT_TOKENS = 2000
_SCORE_REFERENCE_OUTPUT_TOKENS = 500


@dataclasses.dataclass(frozen=True)
class RoutingEvidence:
    """Recent dispatch-outcome evidence for one catalog entry.

    Derived from ``public.model_dispatch_attempts`` rows within the trailing
    ``_EVIDENCE_WINDOW_DAYS`` window. ``success_count``/``failure_count`` only
    count ``success``/``runtime_failure`` outcomes -- ``quota_skip``,
    ``suppressed``, and ``exhausted`` are not systemic-failure or -success
    signals about the model itself (same exclusion the circuit breaker uses).
    """

    success_count: int
    failure_count: int
    p50_duration_ms: float | None
    p95_duration_ms: float | None

    @property
    def sample_count(self) -> int:
        return self.success_count + self.failure_count


@dataclasses.dataclass(frozen=True)
class RoutingScore:
    """A composite evidence-based score for one catalog entry, or why it has none.

    ``score`` is ``None`` (never a fabricated number) whenever the entry has
    fewer than ``_EVIDENCE_MIN_SAMPLES`` qualifying attempts in the evidence
    window -- ``insufficient_data`` is then ``True`` and ``reason`` explains
    why, for honest display on the Models tab (never render an absent score
    as a 0 or an all-clear).
    """

    score: float | None
    success_rate: float | None
    latency_p95_ms: float | None
    cost_usd_per_call: float | None
    sample_count: int
    insufficient_data: bool
    reason: str | None


def compute_routing_score(
    evidence: RoutingEvidence,
    cost_usd_per_call: float | None,
    *,
    min_samples: int = _EVIDENCE_MIN_SAMPLES,
) -> RoutingScore:
    """Combine recent success rate, tail latency, and per-call cost into one score.

    Higher is better. ``score = success_rate / ((1 + p95_seconds/60) * (1 + cost*100))``:
    success rate is the dominant term (a flaky model is never worth routing to
    regardless of speed or price); latency and cost are then applied as
    diminishing-return penalties so neither alone can zero out an otherwise
    reliable model. Unpriced models (``cost_usd_per_call=None``, e.g. no
    ``pricing.toml`` entry) are treated as cost-neutral (0), not excluded --
    an operator may run a genuinely free/local/subscription model.

    Returns ``score=None`` (``insufficient_data=True``) when the entry has
    fewer than ``min_samples`` qualifying attempts -- callers MUST treat that
    as "no opinion", never as a low score.
    """
    total = evidence.sample_count
    if total < min_samples:
        return RoutingScore(
            score=None,
            success_rate=None,
            latency_p95_ms=evidence.p95_duration_ms,
            cost_usd_per_call=cost_usd_per_call,
            sample_count=total,
            insufficient_data=True,
            reason=f"insufficient dispatch history (n={total}, need >= {min_samples})",
        )
    success_rate = evidence.success_count / total
    latency_ms = (
        evidence.p95_duration_ms
        if evidence.p95_duration_ms is not None
        else evidence.p50_duration_ms
    )
    latency_s = (latency_ms or 0.0) / 1000.0
    cost = cost_usd_per_call if cost_usd_per_call is not None else 0.0
    score = success_rate / ((1.0 + latency_s / 60.0) * (1.0 + cost * 100.0))
    return RoutingScore(
        score=round(score, 6),
        success_rate=success_rate,
        latency_p95_ms=evidence.p95_duration_ms,
        cost_usd_per_call=cost_usd_per_call,
        sample_count=total,
        insufficient_data=False,
        reason=None,
    )


def _reference_cost_usd(pricing: PricingConfig | None, model_id: str) -> float | None:
    """Estimate a comparable per-call USD cost for *model_id* under a fixed token profile.

    Returns ``None`` when no pricing config is available or the model has no
    configured price -- callers treat that as cost-neutral (0), not excluded.
    """
    if pricing is None:
        return None
    try:
        return pricing.estimate_cost(
            model_id, _SCORE_REFERENCE_INPUT_TOKENS, _SCORE_REFERENCE_OUTPUT_TOKENS
        )
    except Exception:
        logger.debug("Reference cost estimate failed for model_id=%s", model_id, exc_info=True)
        return None


def _evidence_cte(*, filter_by_ids: bool, window_days: int = _EVIDENCE_WINDOW_DAYS) -> str:
    """Build the ``evidence`` CTE: per-entry success/failure counts and duration percentiles.

    Returns the CTE body text (no leading ``WITH``) named ``evidence``.

    ``window_days`` is always a trusted internal int (module constant or an
    explicit caller value coerced with ``int()``), never raw user input --
    interpolated directly since asyncpg has no parameterized ``interval``.
    """
    id_filter = "AND catalog_entry_id = ANY($1)" if filter_by_ids else ""
    return f"""
    evidence AS (
        SELECT
            catalog_entry_id,
            COUNT(*) FILTER (WHERE outcome = 'success') AS success_count,
            COUNT(*) FILTER (WHERE outcome = 'runtime_failure') AS failure_count,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms)
                FILTER (WHERE outcome = 'success' AND duration_ms IS NOT NULL) AS p50_duration_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)
                FILTER (WHERE outcome = 'success' AND duration_ms IS NOT NULL) AS p95_duration_ms
        FROM public.model_dispatch_attempts
        WHERE outcome IN ('success', 'runtime_failure')
          AND ts > now() - interval '{int(window_days)} days'
          {id_filter}
        GROUP BY catalog_entry_id
    )
    """


async def get_routing_evidence(
    pool: asyncpg.Pool,
    catalog_entry_ids: list[uuid.UUID] | None = None,
    *,
    window_days: int = _EVIDENCE_WINDOW_DAYS,
) -> dict[uuid.UUID, RoutingEvidence]:
    """Batch-fetch recent dispatch evidence for one round trip (no N+1).

    Mirrors ``get_breaker_states``: when ``catalog_entry_ids`` is provided,
    every id is present in the result (defaulting to zero-sample evidence),
    and the query is bound by the id list so Postgres can use
    ``idx_model_dispatch_attempts_catalog_ts_id`` instead of scanning the whole
    table. Used by the Models tab (``GET /api/settings/models``) to annotate
    every row's routing score without an N+1 query.
    """
    cte = _evidence_cte(filter_by_ids=catalog_entry_ids is not None, window_days=window_days)
    query = f"""
        WITH {cte}
        SELECT catalog_entry_id, success_count, failure_count, p50_duration_ms, p95_duration_ms
        FROM evidence
        """
    rows = (
        await pool.fetch(query, catalog_entry_ids)
        if catalog_entry_ids is not None
        else await pool.fetch(query)
    )
    evidence = {
        row["catalog_entry_id"]: RoutingEvidence(
            success_count=int(row["success_count"] or 0),
            failure_count=int(row["failure_count"] or 0),
            p50_duration_ms=(
                float(row["p50_duration_ms"]) if row["p50_duration_ms"] is not None else None
            ),
            p95_duration_ms=(
                float(row["p95_duration_ms"]) if row["p95_duration_ms"] is not None else None
            ),
        )
        for row in rows
    }
    if catalog_entry_ids is not None:
        return {
            cid: evidence.get(cid, RoutingEvidence(0, 0, None, None)) for cid in catalog_entry_ids
        }
    return evidence


async def get_routing_scores(
    pool: asyncpg.Pool,
    entries: list[tuple[uuid.UUID, str]],
    *,
    pricing: PricingConfig | None = None,
) -> dict[uuid.UUID, RoutingScore]:
    """Batch-compute routing scores for the Models tab (one round trip, no N+1).

    ``entries`` is ``[(catalog_entry_id, model_id), ...]`` — the model_id is
    needed alongside the id to look up its reference cost. Mirrors
    ``get_breaker_states``/``get_routing_evidence``: every id in ``entries``
    is present in the result.

    ``pricing`` defaults to the same process-cached ``PricingConfig`` the
    resolver's tie-break uses (see ``_get_cached_pricing``), so the score
    shown on the Models tab matches what routing actually saw. Pass an
    explicit ``PricingConfig`` (e.g. the FastAPI ``get_pricing`` dependency)
    to avoid a redundant load from the caller's own request context.
    """
    ids = [cid for cid, _ in entries]
    evidence_by_id = await get_routing_evidence(pool, ids)
    effective_pricing = pricing if pricing is not None else _get_cached_pricing()
    return {
        cid: compute_routing_score(
            evidence_by_id[cid], _reference_cost_usd(effective_pricing, model_id)
        )
        for cid, model_id in entries
    }


# SQL that resolves the best model across an ordered tier list in a single round-trip.
#
# Accepts:
#   $1 — butler_name (text)
#   $2 — ordered tiers to try (text[]), e.g. ['reasoning','workhorse','cheap']
#
# Strategy (§3.2 routing contract):
# 1. tier_order:    Enumerate provided tiers with their fallthrough position (ord).
# 2. all_candidates: Join catalog + overrides for all qualifying models across every
#                   provided tier, carrying effective_tier, effective_priority, and ord.
#                   Also excludes any catalog entry whose dispatch-outcome circuit
#                   breaker is open (see ``_BREAKER_OPEN_CTE``, bu-hmdqz.2).
# 3. winning:       Find the first tier (lowest ord) that has at least one qualifying
#                   model; also record its max priority so step 4 can filter to
#                   top-priority entries only.
# 4. candidates:    Narrow to top-priority models in the winning tier, decorated with
#                   a stable round-robin row number (created_at ASC, id ASC tie-break).
# 5. evidence:      Recent success/failure counts + duration percentiles for exactly
#                   these candidates (id-bound, index-friendly — see ``_evidence_cte``).
# 6. Final SELECT:  Returns ALL tied top-priority candidates (not a single winner) —
#                   the caller (``resolve_model``) picks by evidence-based score
#                   (bu-ep4ks.13) when at least two candidates have sufficient recent
#                   evidence, else falls back to the original ``rn == counter % total``
#                   round-robin index. The common case (exactly one top-priority
#                   candidate) returns exactly one row either way.
#
# Returns rows of: (runtime_type, model_id, extra_args, id, session_timeout_s,
# effective_tier, rn, total, success_count, failure_count, p50_duration_ms,
# p95_duration_ms, quota_ok). Returns no rows when no qualifying model exists in
# any provided tier.
#
# ``quota_ok`` (bu-ep4ks.13 follow-up / bu-k9te9): per-row annotation from
# ``_QUOTA_OK_CTE`` -- does NOT affect ``winning``/``candidates`` narrowing
# (tier-fallthrough and priority selection stay governed by breaker/enabled
# state only, exactly as before this column existed). Quota-aware callers
# (``resolve_model_with_effective_tier(quota_aware=True)``) read this column
# themselves to fold the pre-spawn quota gate into this same round trip
# instead of a separate ``check_token_quota`` call; quota-unaware callers
# (``resolve_model``, and ``resolve_model_with_effective_tier`` by default)
# ignore it, so their behavior is provably unchanged (see each function's
# docstring for the equivalence argument).
#
# The round-robin counter increment (formerly an inline ``next_counter`` CTE here)
# moved to a standalone query, ``_ROUTING_COUNTER_INCREMENT_SQL`` (bu-k9te9, slice 5):
# the bounded routing-decision cache below can skip re-running THIS query on a cache
# hit, but the counter must still increment on every logical resolution call (several
# existing tests pin the counter's raw value, e.g. incrementing 0/1/2 across three
# calls even with a single, uncontested candidate) -- so callers always issue the
# counter increment separately, unconditional of cache hit/miss. See
# ``resolve_model``/``resolve_model_with_effective_tier`` for the call sequence.
# Shared ``all_candidates`` CTE: every enabled, verified, breaker-closed catalog entry
# in any of the requested tiers, decorated with effective override values, its tier's
# fallthrough position, its quota headroom, and its capability envelope (core_204).
#
# Inlined by BOTH ``_RESOLVE_SQL`` (which then narrows to one winning tier and its
# top-priority set in SQL) and ``_RESOLVE_CANDIDATES_SQL`` (which returns the whole set
# so ``resolve_dispatch`` can apply hard fit BEFORE narrowing). Sharing the text is the
# point: the two paths must agree exactly on what "eligible at all" means, and a
# divergence there would make an intent-aware dispatch silently consider a different
# universe than a plain one.
#
# ``capabilities`` / ``max_context_tokens`` / ``max_output_tokens`` are inert for
# ``_RESOLVE_SQL`` -- its downstream CTEs select explicit columns and never reference
# them -- so carrying them here costs that path nothing.
_ALL_CANDIDATES_CTE = """
all_candidates AS (
    SELECT
        mc.runtime_type,
        mc.model_id,
        mc.extra_args,
        mc.id,
        mc.session_timeout_s,
        mc.created_at,
        mc.capabilities,
        mc.max_context_tokens,
        mc.max_output_tokens,
        COALESCE(bmo.complexity_tier, mc.complexity_tier) AS effective_tier,
        COALESCE(bmo.priority, mc.priority) AS effective_priority,
        t.ord AS tier_ord,
        COALESCE(qoc.quota_ok, true) AS quota_ok
    FROM public.model_catalog mc
    LEFT JOIN public.butler_model_overrides bmo
        ON bmo.catalog_entry_id = mc.id AND bmo.butler_name = $1
    LEFT JOIN quota_ok_candidates qoc
        ON qoc.catalog_entry_id = mc.id
    JOIN tier_order t
        ON COALESCE(bmo.complexity_tier, mc.complexity_tier) = t.tier
    WHERE COALESCE(bmo.enabled, mc.enabled) = true
      AND mc.last_verified_ok IS DISTINCT FROM false
      AND mc.id NOT IN (SELECT catalog_entry_id FROM breaker_open)
)
"""

_RESOLVE_SQL = f"""
WITH
{_BREAKER_OPEN_CTE},
{_QUOTA_OK_CTE},
tier_order AS (
    SELECT t.tier, t.ord
    FROM unnest($2::text[]) WITH ORDINALITY AS t(tier, ord)
),
{_ALL_CANDIDATES_CTE},
winning AS (
    SELECT effective_tier, tier_ord, MAX(effective_priority) AS max_priority
    FROM all_candidates
    GROUP BY effective_tier, tier_ord
    ORDER BY tier_ord ASC
    LIMIT 1
),
candidates AS (
    SELECT
        ac.runtime_type,
        ac.model_id,
        ac.extra_args,
        ac.id,
        ac.session_timeout_s,
        ac.effective_tier,
        ac.quota_ok,
        ROW_NUMBER() OVER (ORDER BY ac.created_at ASC, ac.id ASC) - 1 AS rn,
        COUNT(*) OVER () AS total
    FROM all_candidates ac
    JOIN winning w
        ON ac.effective_tier = w.effective_tier
        AND ac.tier_ord = w.tier_ord
        AND ac.effective_priority = w.max_priority
),
evidence AS (
    SELECT
        catalog_entry_id,
        COUNT(*) FILTER (WHERE outcome = 'success') AS success_count,
        COUNT(*) FILTER (WHERE outcome = 'runtime_failure') AS failure_count,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms)
            FILTER (WHERE outcome = 'success' AND duration_ms IS NOT NULL) AS p50_duration_ms,
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)
            FILTER (WHERE outcome = 'success' AND duration_ms IS NOT NULL) AS p95_duration_ms
    FROM public.model_dispatch_attempts
    WHERE catalog_entry_id IN (SELECT id FROM candidates)
      AND outcome IN ('success', 'runtime_failure')
      AND ts > now() - interval '{_EVIDENCE_WINDOW_DAYS} days'
    GROUP BY catalog_entry_id
)
SELECT
    c.runtime_type, c.model_id, c.extra_args, c.id, c.session_timeout_s, c.effective_tier,
    c.rn, c.total,
    e.success_count, e.failure_count, e.p50_duration_ms, e.p95_duration_ms,
    c.quota_ok
FROM candidates c
LEFT JOIN evidence e ON e.catalog_entry_id = c.id
"""

# Intent-aware candidate query (bu-6jv4m.7). Same eligibility universe as
# ``_RESOLVE_SQL`` (it inlines the identical ``_ALL_CANDIDATES_CTE``), but it does NOT
# narrow to a winning tier or a top-priority set: it returns every eligible candidate
# across the requested tier list so ``resolve_dispatch`` can apply hard capability/
# context/deadline/budget fit BEFORE any ranking narrows the field.
#
# The order matters: narrowing first, as ``_RESOLVE_SQL`` does, means a tier whose only
# top-priority entry cannot do the job produces no dispatchable candidate at all, even
# though a lower-priority entry in the same tier could have. Fit is a precondition, not
# a preference, so it has to run before priority and before the evidence tie-break.
#
# Accepts the same $1 (butler_name) / $2 (ordered tiers) as ``_RESOLVE_SQL``.
#
# ``evidence`` additionally carries ``last_attempt_at``: the resolution receipt records
# how OLD the evidence behind a score is, so a score computed from a week-old sample is
# not read as a fresh observation.
_RESOLVE_CANDIDATES_SQL = f"""
WITH
{_BREAKER_OPEN_CTE},
{_QUOTA_OK_CTE},
tier_order AS (
    SELECT t.tier, t.ord
    FROM unnest($2::text[]) WITH ORDINALITY AS t(tier, ord)
),
{_ALL_CANDIDATES_CTE},
evidence AS (
    SELECT
        catalog_entry_id,
        COUNT(*) FILTER (WHERE outcome = 'success') AS success_count,
        COUNT(*) FILTER (WHERE outcome = 'runtime_failure') AS failure_count,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms)
            FILTER (WHERE outcome = 'success' AND duration_ms IS NOT NULL) AS p50_duration_ms,
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)
            FILTER (WHERE outcome = 'success' AND duration_ms IS NOT NULL) AS p95_duration_ms,
        MAX(ts) AS last_attempt_at
    FROM public.model_dispatch_attempts
    WHERE catalog_entry_id IN (SELECT id FROM all_candidates)
      AND outcome IN ('success', 'runtime_failure')
      AND ts > now() - interval '{_EVIDENCE_WINDOW_DAYS} days'
    GROUP BY catalog_entry_id
)
SELECT
    ac.runtime_type, ac.model_id, ac.extra_args, ac.id, ac.session_timeout_s,
    ac.capabilities, ac.max_context_tokens, ac.max_output_tokens,
    ac.effective_tier, ac.effective_priority, ac.tier_ord, ac.quota_ok,
    e.success_count, e.failure_count, e.p50_duration_ms, e.p95_duration_ms,
    e.last_attempt_at
FROM all_candidates ac
LEFT JOIN evidence e ON e.catalog_entry_id = ac.id
ORDER BY ac.tier_ord ASC, ac.effective_priority DESC, ac.created_at ASC, ac.id ASC
"""


# Standalone round-robin counter increment (bu-k9te9, slice 5 — split out of the former
# ``next_counter`` CTE above so the bounded routing-decision cache can skip re-running
# the expensive candidate-resolution query on a cache hit while this cheap single-row
# upsert still fires on every logical resolution call, exactly preserving the
# increment-every-call contract the counter table has always had. Accepts:
#   $1 — butler_name (text)
#   $2 — effective_tier that won resolution (text)
# Always increments (or seeds at 0) — callers only run this after confirming at least
# one candidate row exists for the winning tier, matching the old CTE's implicit gating
# (it only ever ran once ``winning`` was non-empty).
_ROUTING_COUNTER_INCREMENT_SQL = """
INSERT INTO public.model_round_robin_counters
    (butler_name, complexity_tier, counter, updated_at)
VALUES ($1, $2, 0, now())
ON CONFLICT (butler_name, complexity_tier)
DO UPDATE SET
    counter = public.model_round_robin_counters.counter + 1,
    updated_at = now()
RETURNING counter
"""

# SQL for same-tier failover candidate resolution.
#
# Accepts:
#   $1 — butler_name (text)
#   $2 — exact effective tier (text), e.g. 'workhorse'
#   $3 — already-attempted catalog entry IDs (uuid[]) — excluded from results
#
# Strategy (same-tier failover — §model-catalog/next-eligible-same-tier-candidate):
# 1. all_candidates: Join catalog + overrides; apply COALESCE semantics for enabled,
#    priority, and complexity_tier; filter to the exact effective tier; exclude attempted
#    IDs; filter disabled and failed-verification entries.
# 2. best_priority:  Find the maximum effective_priority across all remaining candidates.
# 3. top_candidates: Narrow to entries at best_priority, ordered deterministically:
#    effective_priority DESC, created_at ASC, id ASC.  Round-robin is NOT used here —
#    deterministic ordering ensures predictable failover progression.
# 4. Return the first row.
#
# Returns: (runtime_type, model_id, extra_args, id, session_timeout_s)
# Returns no rows when no qualifying candidate remains. Also excludes any
# catalog entry whose dispatch-outcome circuit breaker is open (see
# ``_BREAKER_OPEN_CTE``, bu-hmdqz.2) — the exact reason failover needs this:
# a same-tier candidate that has itself been failing repeatedly should not be
# re-offered as the "next" candidate mid-loop.
_NEXT_SAME_TIER_SQL = f"""
WITH
{_BREAKER_OPEN_CTE},
all_candidates AS (
    SELECT
        mc.runtime_type,
        mc.model_id,
        mc.extra_args,
        mc.id,
        mc.session_timeout_s,
        mc.created_at,
        COALESCE(bmo.complexity_tier, mc.complexity_tier) AS effective_tier,
        COALESCE(bmo.priority, mc.priority) AS effective_priority
    FROM public.model_catalog mc
    LEFT JOIN public.butler_model_overrides bmo
        ON bmo.catalog_entry_id = mc.id AND bmo.butler_name = $1
    WHERE COALESCE(bmo.enabled, mc.enabled) = true
      AND mc.last_verified_ok IS DISTINCT FROM false
      AND COALESCE(bmo.complexity_tier, mc.complexity_tier) = $2
      AND mc.id != ALL($3::uuid[])
      AND mc.id NOT IN (SELECT catalog_entry_id FROM breaker_open)
)
SELECT
    runtime_type,
    model_id,
    extra_args,
    id,
    session_timeout_s
FROM all_candidates
ORDER BY effective_priority DESC, created_at ASC, id ASC
LIMIT 1
"""

# CTE-based single-query for both 24h and 30d windows.
# Fast path (no limits row) is handled in Python before executing this query.
_QUOTA_CHECK_SQL = """
WITH limits AS (
    SELECT
        limit_24h,
        limit_30d,
        COALESCE(reset_24h_at, '-infinity'::timestamptz) AS reset_24h_at,
        COALESCE(reset_30d_at, '-infinity'::timestamptz) AS reset_30d_at
    FROM public.token_limits
    WHERE catalog_entry_id = $1
),
usage AS (
    SELECT
        COALESCE(SUM(input_tokens + output_tokens)
            FILTER (WHERE recorded_at > GREATEST(
                (SELECT reset_24h_at FROM limits),
                now() - interval '24 hours'
            )), 0) AS used_24h,
        COALESCE(SUM(input_tokens + output_tokens)
            FILTER (WHERE recorded_at > GREATEST(
                (SELECT reset_30d_at FROM limits),
                now() - interval '30 days'
            )), 0) AS used_30d
    FROM public.token_usage_ledger
    WHERE catalog_entry_id = $1
      AND recorded_at > GREATEST(
          LEAST(
              (SELECT reset_24h_at FROM limits),
              (SELECT reset_30d_at FROM limits)
          ),
          now() - interval '30 days'
      )
)
SELECT l.limit_24h, l.limit_30d, u.used_24h, u.used_30d
FROM usage u, limits l
"""

# Check whether a limits row exists for the given catalog entry (fast path).
_LIMITS_EXISTS_SQL = """
SELECT 1 FROM public.token_limits WHERE catalog_entry_id = $1 LIMIT 1
"""

_LEDGER_INSERT_SQL = """
INSERT INTO public.token_usage_ledger
    (catalog_entry_id, butler_name, session_id, input_tokens, output_tokens,
     cached_input_tokens, cache_creation_tokens, purpose)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""

# Read the configured monthly spend ceiling (singleton row id=1).
_CEILING_SELECT_SQL = """
SELECT monthly_usd FROM public.spend_ceiling WHERE id = 1
"""

# Aggregate current-month token usage per model_id from the ledger.  Grouped by
# model_id so the caller can apply per-model pricing in Python (pricing config is
# not represented in the DB).  Scoped to ledger rows recorded since the start of
# the current UTC month (date_trunc('month', now())).
_MTD_USAGE_BY_MODEL_SQL = """
SELECT
    mc.model_id AS model_id,
    COUNT(*) AS calls,
    COALESCE(SUM(tul.input_tokens), 0)  AS input_tokens,
    COALESCE(SUM(tul.output_tokens), 0) AS output_tokens,
    COALESCE(SUM(tul.cached_input_tokens), 0)   AS cached_input_tokens,
    COALESCE(SUM(tul.cache_creation_tokens), 0) AS cache_creation_tokens
FROM public.token_usage_ledger tul
JOIN public.model_catalog mc ON mc.id = tul.catalog_entry_id
WHERE tul.recorded_at >= date_trunc('month', now() AT TIME ZONE 'UTC')
GROUP BY mc.model_id
"""

# Load all spend routing rules in evaluation order (top-to-bottom = position ASC).
# Each rule is a (condition JSONB, action JSONB) pair; evaluation is first-match-wins.
_SPEND_RULES_SELECT_SQL = """
SELECT id, condition, action
FROM public.spend_rules
ORDER BY position ASC
"""

# Resolve a single catalog entry by its priced model_id (the target a routing rule
# routes TO).  Picks the highest-priority enabled, non-failed entry for the model so
# the rule override lands on a real dispatchable row (and its real session_timeout /
# extra_args / runtime_type).  Per-butler overrides are honored via COALESCE so a rule
# routing to a model the butler has disabled yields no row (caller keeps the original).
_RESOLVE_BY_MODEL_ID_SQL = """
WITH candidates AS (
    SELECT
        mc.runtime_type,
        mc.model_id,
        mc.extra_args,
        mc.id,
        mc.session_timeout_s,
        mc.created_at,
        COALESCE(bmo.priority, mc.priority) AS effective_priority
    FROM public.model_catalog mc
    LEFT JOIN public.butler_model_overrides bmo
        ON bmo.catalog_entry_id = mc.id AND bmo.butler_name = $1
    WHERE mc.model_id = $2
      AND COALESCE(bmo.enabled, mc.enabled) = true
      AND mc.last_verified_ok IS DISTINCT FROM false
)
SELECT runtime_type, model_id, extra_args, id, session_timeout_s
FROM candidates
ORDER BY effective_priority DESC, created_at ASC, id ASC
LIMIT 1
"""


def _coerce_rule_dict(raw: object) -> dict:
    """Coerce an asyncpg JSONB column (dict or JSON string) to a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _rule_condition_matches(
    condition: dict,
    *,
    butler_name: str,
    complexity_tier: str,
    trigger_source: str | None = None,
) -> bool:
    """Return True when a routing-rule condition matches the dispatch context.

    A condition is a JSONB object of constraints; ALL constraints must hold for the
    rule to match (logical AND).  An empty condition ``{}`` is a catch-all and matches
    every dispatch.  Supported constraint keys:

    - ``butler`` — the butler identity name (e.g. ``"general"``).
    - ``complexity`` / ``tier`` — the canonical complexity tier (e.g. ``"workhorse"``).
    - ``trigger`` / ``purpose`` — the dispatch trigger source available at the spawner
      call site (e.g. ``"route"``, ``"qa"``, ``"healing"``, ``"schedule:<task>"``,
      ``"extraction"``).  Chosen because it is the richest dispatch-context dimension
      actually plumbed to ``apply_spend_routing_rules`` — no synthetic "feature" tag
      exists at that call site, so the rule never matches on data that isn't really
      present.  ``purpose`` is an alias for the same value: it is the same
      ``trigger_source`` string that ``core.spawner._run()`` stamps onto
      ``public.token_usage_ledger.purpose`` (bu-qvnce.12/core_156), so a rule written
      against either key evaluates identically at dispatch time — ``purpose`` just
      matches the vocabulary spend-analysis surfaces (``/spend`` breakdown) use for the
      same dimension.  When the caller does not supply a trigger source, a
      ``trigger``/``purpose`` constraint cannot be evaluated and the rule does NOT
      match (fail-closed).

    Each constraint value may be a scalar (exact match) or a list (membership match).
    Matching is case-insensitive on string values.  Unknown constraint keys cause the
    rule NOT to match (fail-closed on unrecognized constraints), so a malformed or
    forward-dated rule never silently routes every dispatch.
    """
    context: dict[str, str | None] = {
        "butler": butler_name,
        "complexity": complexity_tier,
        "tier": complexity_tier,
        "trigger": trigger_source,
        "purpose": trigger_source,
    }
    for key, expected in condition.items():
        if key in ("trigger", "purpose") and trigger_source is None:
            # Trigger/purpose constraint present but no trigger context to evaluate it against.
            return False
        if key not in context:
            # Unknown constraint dimension — cannot evaluate; do not match.
            return False
        actual = context[key]
        if isinstance(expected, list):
            allowed = {str(v).lower() for v in expected}
            if str(actual).lower() not in allowed:
                return False
        else:
            if str(actual).lower() != str(expected).lower():
                return False
    return True


def _parse_extra_args(raw_extra: object) -> list[str]:
    """Coerce asyncpg JSONB result for extra_args to list[str]."""
    if raw_extra is None:
        return []
    if isinstance(raw_extra, list):
        return raw_extra
    if isinstance(raw_extra, str):
        parsed = json.loads(raw_extra)
        return parsed if isinstance(parsed, list) else []
    return []


# Process-global pricing cache, mirroring ``spawner._cached_pricing``. Pricing is
# only used here to rank same-priority candidates against each other (never to
# gate resolution), so a load failure fails open to cost-neutral (see
# ``_reference_cost_usd``), not to a resolution error.
_cached_pricing: PricingConfig | None = None


def _get_cached_pricing() -> PricingConfig | None:
    global _cached_pricing
    if _cached_pricing is None:
        try:
            from butlers.core.pricing import load_pricing

            _cached_pricing = load_pricing()
        except Exception:
            logger.debug("Pricing config load failed for routing score (non-fatal)", exc_info=True)
            return None
    return _cached_pricing


def _score_row(row: asyncpg.Record, pricing: PricingConfig | None) -> RoutingScore:
    """Score one candidate row from its evidence columns.

    Shared by ``_select_resolved_row`` (the ``_RESOLVE_SQL`` path) and
    ``resolve_dispatch`` (the intent-aware path) so the two can never drift into
    scoring the same evidence differently -- the receipt would otherwise be able to
    claim a winner reason the legacy path would not have reached.
    """
    evidence = RoutingEvidence(
        success_count=int(row["success_count"] or 0),
        failure_count=int(row["failure_count"] or 0),
        p50_duration_ms=(
            float(row["p50_duration_ms"]) if row["p50_duration_ms"] is not None else None
        ),
        p95_duration_ms=(
            float(row["p95_duration_ms"]) if row["p95_duration_ms"] is not None else None
        ),
    )
    return compute_routing_score(evidence, _reference_cost_usd(pricing, row["model_id"]))


def _select_resolved_row(
    rows: list[asyncpg.Record], rr_counter: int | None = None
) -> asyncpg.Record:
    """Pick the winning candidate row from ``_RESOLVE_SQL``'s tied top-priority set.

    Every row shares the same ``total`` (one winning tier per call) but carries its own
    ``rn`` and evidence columns. Score-based selection (bu-ep4ks.13) only engages when at
    least two candidates have sufficient recent evidence (``compute_routing_score``
    returns a non-None score) -- otherwise this falls back to the legacy ``rn == counter
    % total`` round-robin index, so a fleet with no dispatch history yet, or a single
    top-priority candidate, behaves exactly as before.

    ``rr_counter`` is the freshly-fetched value from ``_ROUTING_COUNTER_INCREMENT_SQL``
    (bu-k9te9, slice 5 -- split out of ``_RESOLVE_SQL`` so the routing-decision cache can
    skip re-running the expensive candidate query on a hit while the counter still
    increments every call). Only read when ``len(rows) > 1`` and evidence is
    insufficient; every single-row and evidence-decided call path ignores it, so ``None``
    is a safe default for those callers.
    """
    if len(rows) == 1:
        return rows[0]

    pricing = _get_cached_pricing()
    scored = [(row, _score_row(row, pricing)) for row in rows]

    eligible = [(row, s) for row, s in scored if s.score is not None]
    if len(eligible) >= 2:
        best_row, _ = max(eligible, key=lambda pair: pair[1].score)
        return best_row

    # Insufficient evidence to trust a score-based tie-break — legacy round robin.
    assert rr_counter is not None, (
        "_select_resolved_row: rr_counter is required once evidence-based scoring "
        "cannot decide a multi-row tie (see docstring)"
    )
    counter = rr_counter
    total = rows[0]["total"]
    target_rn = counter % total
    for row in rows:
        if row["rn"] == target_rn:
            return row
    return rows[0]  # defensive: rn/total are always consistent with len(rows)


# ---------------------------------------------------------------------------
# Bounded routing-decision cache (bu-ep4ks.13 follow-up / bu-k9te9, slice 5)
# ---------------------------------------------------------------------------
#
# Caches ONLY the ``_RESOLVE_SQL`` tier/breaker/priority/evidence resolution rows --
# i.e. "which catalog entries would qualify for this (butler, tier-fallthrough-list)" --
# never an invoked session, and never anything from the quota-aware fast path the
# spawner's dispatch-critical gate chain uses (``resolve_model_with_effective_tier(...,
# quota_aware=True)``, bu-k9te9 slice 3). Caching a quota-aware answer would be actively
# wrong: token usage changes with every dispatch, so a cached "quota is fine" answer
# could go stale within milliseconds -- exactly the "serving a cached answer as fresh
# content" failure mode this slice must avoid. ``next_same_tier_candidate`` (post-failure
# / quota-skip failover) is likewise never cached -- a failover decision exists
# specifically to react to a JUST-discovered problem and must always see current state.
#
# Bounded size (LRU eviction, ``_ROUTING_CACHE_MAX_ENTRIES``) + a short TTL
# (``_ROUTING_CACHE_TTL_SECONDS``). Deliberately TTL-based rather than event-driven
# invalidation on model_catalog / butler_model_overrides / model_round_robin_counters
# writes: those mutations are scattered across many surfaces (Models tab CRUD, override
# edits, the breaker/evidence machinery's own writes on every dispatch attempt), and
# wiring a reliable invalidation signal to all of them is a substantially larger, riskier
# change than this follow-up's scope. A short TTL keeps staleness provably harmless
# instead: it is small relative to the breaker's own half-open cooldown
# (``_BREAKER_HALF_OPEN_COOLDOWN_MINUTES`` = 15 minutes) and to how rarely operators
# actually edit the catalog, so a few seconds of a stale "which model(s) qualify" answer
# costs at most a handful of dispatches briefly seeing a not-yet-updated (but still valid
# at cache-write-time) candidate set -- never a safety violation, since no DENY decision
# (quota, ceiling, permission) is ever served from this cache.
#
# One side effect worth naming: ``next_counter``'s round-robin increment is part of the
# cached SQL round-trip, so a cache HIT does not advance the counter -- round-robin
# fairness becomes "per TTL window" instead of "per call" for cache-hit traffic. This is
# self-consistent (a cache hit is not a new resolution) and immaterial in practice, since
# round-robin is only the tie-break used below the evidence-based scoring threshold.
_ROUTING_CACHE_TTL_SECONDS = 5.0
_ROUTING_CACHE_MAX_ENTRIES = 256

# key: (butler_name, tiers_to_try) -> (rows, expires_at monotonic seconds)
_routing_rows_cache: OrderedDict[
    tuple[str, tuple[str, ...]], tuple[list[asyncpg.Record], float]
] = OrderedDict()


def clear_routing_decision_cache() -> None:
    """Drop every cached routing-decision entry.

    Not needed by production callers (the TTL alone keeps the cache correct) -- exists so
    tests that assert on fresh-DB-state resolution results (integration tests against a
    per-test truncated database) can guarantee they never observe another test's cached
    rows for the same (butler_name, tier) key within the TTL window.
    """
    _routing_rows_cache.clear()


async def _fetch_resolve_rows(
    pool: asyncpg.Pool,
    butler_name: str,
    tiers_to_try: list[str],
    *,
    cache: bool = True,
) -> list[asyncpg.Record]:
    """Execute (or serve from the bounded TTL cache) the ``_RESOLVE_SQL`` round trip.

    ``cache=False`` (used by the spawner's quota-aware pre-spawn resolve) always hits the
    database and never reads or writes the cache -- see the module-level cache docstring
    above for why quota-aware results must never be cached.

    An empty result (no qualifying candidate in any tier) is deliberately never cached
    either, even when ``cache=True``: "no candidates yet" is exactly the state most
    likely to change soon (an operator enabling/adding the first entry for a butler+tier),
    and unlike a stale "these N candidates qualify" answer -- which just means a few
    dispatches within the TTL window pick from a not-yet-updated but still-valid set --
    a stale empty answer would incorrectly keep forcing the static-fallback path for up to
    the full TTL after a real fix landed.
    """
    if not cache:
        return await pool.fetch(_RESOLVE_SQL, butler_name, tiers_to_try)

    key = (butler_name, tuple(tiers_to_try))
    now = time.monotonic()
    cached = _routing_rows_cache.get(key)
    if cached is not None:
        rows, expires_at = cached
        if expires_at > now:
            _routing_rows_cache.move_to_end(key)
            return rows
        del _routing_rows_cache[key]

    rows = await pool.fetch(_RESOLVE_SQL, butler_name, tiers_to_try)
    if not rows:
        return rows
    _routing_rows_cache[key] = (rows, now + _ROUTING_CACHE_TTL_SECONDS)
    _routing_rows_cache.move_to_end(key)
    while len(_routing_rows_cache) > _ROUTING_CACHE_MAX_ENTRIES:
        _routing_rows_cache.popitem(last=False)
    return rows


async def resolve_model(
    pool: asyncpg.Pool,
    butler_name: str,
    complexity_tier: Complexity | str,
    *,
    allow_tier_fallthrough: bool = True,
) -> tuple[str, str, list[str], uuid.UUID, int] | None:
    """Resolve the best model for a butler and complexity tier.

    Implements the §3.2 routing contract:
      - Selects the highest-priority enabled model in ``complexity_tier`` whose
        state ∈ {verified, untested}.  (State column not yet in schema; all
        enabled entries are treated as untested/qualifying.)
      - When ``allow_tier_fallthrough=True`` (default) and no model qualifies
        in the requested tier, falls through to the next tier in canonical order:
        reasoning → workhorse → cheap → specialty → local → legacy.
      - When multiple entries share the highest effective priority for a tier,
        selection prefers the entry with the best recent evidence-based score
        (success rate × latency × cost, see ``compute_routing_score``) once at
        least two candidates have sufficient dispatch history; otherwise (new
        catalog, sparse history, or all-tied scores) selection falls back to
        round-robin via an atomic counter (bu-ep4ks.13).

    Deprecation shim: if the caller passes a legacy tier string
    (trivial/medium/high/extra_high/discretion/self_healing), a LOUD WARNING is
    logged and the value is remapped to the canonical equivalent.  The call is
    never silently accepted without the warning.

    Parameters
    ----------
    pool:
        An asyncpg connection pool connected to the butlers database.
    butler_name:
        The butler identity name (e.g. ``"general"``).  Used to look up any
        per-butler overrides; if none exist the global catalog is used directly.
    complexity_tier:
        A ``Complexity`` enum value or its string equivalent using the canonical
        vocabulary (``"reasoning"``, ``"workhorse"``, ``"cheap"``, ``"specialty"``,
        ``"local"``, ``"legacy"``).
    allow_tier_fallthrough:
        When True (default), fall through to the next tier in canonical order if
        no entry qualifies in the requested tier.  Set to False to restrict
        resolution to the exact requested tier only.

    Returns
    -------
    tuple[str, str, list[str], uuid.UUID, int] | None
        ``(runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s)``
        for the selected entry, or ``None`` if no enabled entries match in any
        qualifying tier.
        ``extra_args`` is a list of CLI token strings (e.g. ``["--config", "k=v"]``).
        ``catalog_entry_id`` is the UUID primary key of the matched catalog row.
        ``session_timeout_s`` is the per-session runtime timeout from the catalog row.
    """
    if isinstance(complexity_tier, Complexity):
        tier_value = complexity_tier.value
    else:
        tier_value = _check_deprecated_tier(str(complexity_tier))

    # Build the ordered tier list for the single-query resolver.
    if allow_tier_fallthrough and tier_value in TIER_FALLTHROUGH_ORDER:
        start_idx = TIER_FALLTHROUGH_ORDER.index(tier_value)
        tiers_to_try = list(TIER_FALLTHROUGH_ORDER[start_idx:])
    else:
        tiers_to_try = [tier_value]

    # Candidate resolution: possibly served from the bounded TTL cache (bu-ep4ks.13
    # follow-up / bu-k9te9, slice 5 -- see _fetch_resolve_rows's docstring). The
    # round-robin counter increment always runs fresh regardless of cache hit/miss, only
    # for the tier actually used -- empty tiers never touch their counters (mirrors the
    # gating the old inline next_counter CTE had via `winning`).
    rows = await _fetch_resolve_rows(pool, butler_name, tiers_to_try)
    if not rows:
        return None
    rr_counter = await pool.fetchval(
        _ROUTING_COUNTER_INCREMENT_SQL, butler_name, rows[0]["effective_tier"]
    )
    row = _select_resolved_row(rows, rr_counter)

    effective_tier = row["effective_tier"]
    if effective_tier != tier_value:
        logger.debug(
            "resolve_model: no entry in tier %r for butler %r; fell through to %r",
            tier_value,
            butler_name,
            effective_tier,
        )
    return (
        row["runtime_type"],
        row["model_id"],
        _parse_extra_args(row["extra_args"]),
        row["id"],
        row["session_timeout_s"],
    )


async def resolve_model_with_effective_tier(
    pool: asyncpg.Pool,
    butler_name: str,
    complexity_tier: Complexity | str,
    *,
    allow_tier_fallthrough: bool = True,
    quota_aware: bool = False,
    intent: DispatchIntent | None = None,
) -> tuple[str, str, list[str], uuid.UUID, int, str] | None:
    """Resolve the best model for a butler and return the effective tier alongside.

    Identical to ``resolve_model`` except the returned tuple includes the effective
    complexity tier that actually produced the candidate.  Callers that implement
    same-tier failover need this to restrict subsequent ``next_same_tier_candidate``
    calls to the resolved tier.

    Parameters
    ----------
    pool:
        An asyncpg connection pool connected to the butlers database.
    butler_name:
        The butler identity name (e.g. ``"general"``).
    complexity_tier:
        A ``Complexity`` enum value or its string equivalent.
    allow_tier_fallthrough:
        When True (default), fall through to the next canonical tier if no entry
        qualifies in the requested tier.
    quota_aware:
        When ``True`` (bu-ep4ks.13 follow-up / bu-k9te9 -- "fold the quota/
        ceiling pre-spawn gates into the resolve CTE"), fold the pre-spawn
        token-quota gate into this same round trip using the ``quota_ok``
        column ``_RESOLVE_SQL`` now carries per candidate. Default ``False``
        (unchanged quota-unaware resolution -- every existing caller other
        than the spawner's pre-spawn gate chain keeps this default and is
        provably unaffected, since the underlying SQL narrowing/tie-break is
        identical either way and this parameter only changes what happens
        with the new ``quota_ok`` column in Python).

        Semantics when ``True``:

        - If every top-priority candidate in the winning tier has quota
          headroom (``quota_ok`` true for all of them), this is a pure
          optimization: the winner is picked via the exact same tie-break as
          ``quota_aware=False`` and returned normally. Since every candidate
          in that tie-break's input set would have passed a
          ``check_token_quota`` call anyway, the caller can safely skip that
          now-redundant round trip and the resulting selection is identical.
        - If ANY top-priority candidate is quota-blocked, this function
          raises :class:`TierQuotaExhausted` instead of silently picking
          among the quota-ok subset or returning ``None``. Deliberately
          conservative: a mixed quota-ok/quota-blocked tie could pick a
          *different* candidate than the deterministic same-tier failover
          the caller already runs for a real quota exhaustion (``priority
          DESC, created_at ASC, id ASC`` via ``next_same_tier_candidate``,
          not round-robin/evidence), so this function does not try to
          reproduce that search itself -- it hands back the quota-unaware
          tie-break winner as ``TierQuotaExhausted.representative`` and lets
          the caller fall back to the pre-existing, already-tested
          sequential quota/failover loop. Returning ``None`` here would be
          WRONG: ``None`` means "no breaker-ok candidate exists in the tier
          at all" to every caller (triggering static-fallback to the
          hard-coded default model) — quota exhaustion is a hard DENY, not a
          static-fallback condition, so it must never be signaled the same
          way.

    intent:
        When supplied (bu-6jv4m.7), delegate to :func:`resolve_dispatch`, which
        disqualifies candidates that cannot satisfy the intent's required
        capabilities, context floor, deadline, or per-call budget *before*
        priority narrowing and the tie-break. ``complexity_tier`` stays
        authoritative for the tier -- the intent's own tier is overridden with
        it -- so callers cannot accidentally route to two different tiers by
        passing an intent built from a stale complexity. Ranking is unchanged,
        and an intent that requires nothing selects exactly what ``None``
        selects. The resolution receipt is dropped here (this signature returns
        the same 6-tuple as before); callers that want it call
        ``resolve_dispatch`` directly.

    Returns
    -------
    tuple[str, str, list[str], uuid.UUID, int, str] | None
        ``(runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s,
        effective_tier)`` or ``None`` if no enabled entries match.
        ``effective_tier`` is the canonical tier string that produced the candidate
        (may differ from ``complexity_tier`` when tier fallthrough occurred).

    Raises
    ------
    TierQuotaExhausted
        Only when ``quota_aware=True`` and the winning tier has a
        quota-blocked top-priority candidate. See ``quota_aware`` above.
    """
    if isinstance(complexity_tier, Complexity):
        tier_value = complexity_tier.value
    else:
        tier_value = _check_deprecated_tier(str(complexity_tier))

    if intent is not None:
        resolution = await resolve_dispatch(
            pool,
            butler_name,
            dataclasses.replace(intent, complexity_tier=tier_value),
            allow_tier_fallthrough=allow_tier_fallthrough,
            quota_aware=quota_aware,
        )
        return resolution.selection

    if allow_tier_fallthrough and tier_value in TIER_FALLTHROUGH_ORDER:
        start_idx = TIER_FALLTHROUGH_ORDER.index(tier_value)
        tiers_to_try = list(TIER_FALLTHROUGH_ORDER[start_idx:])
    else:
        tiers_to_try = [tier_value]

    # Candidate resolution: bounded TTL cache (bu-ep4ks.13 follow-up / bu-k9te9, slice 5),
    # but NEVER for the quota-aware path -- see _fetch_resolve_rows's docstring for why.
    # The round-robin counter increment always runs fresh regardless of cache hit/miss or
    # quota_aware, exactly once whenever a winning tier was found (matches the old inline
    # next_counter CTE's implicit gating via `winning`, before quota was ever consulted).
    rows = await _fetch_resolve_rows(pool, butler_name, tiers_to_try, cache=not quota_aware)
    if not rows:
        return None
    rr_counter = await pool.fetchval(
        _ROUTING_COUNTER_INCREMENT_SQL, butler_name, rows[0]["effective_tier"]
    )

    if quota_aware and not all(r["quota_ok"] for r in rows):
        naive_row = _select_resolved_row(rows, rr_counter)
        naive_effective_tier = naive_row["effective_tier"]
        raise TierQuotaExhausted(
            effective_tier=naive_effective_tier,
            representative=(
                naive_row["runtime_type"],
                naive_row["model_id"],
                _parse_extra_args(naive_row["extra_args"]),
                naive_row["id"],
                naive_row["session_timeout_s"],
                naive_effective_tier,
            ),
        )

    row = _select_resolved_row(rows, rr_counter)

    effective_tier = row["effective_tier"]
    if effective_tier != tier_value:
        logger.debug(
            "resolve_model_with_effective_tier: no entry in tier %r for butler %r; "
            "fell through to %r",
            tier_value,
            butler_name,
            effective_tier,
        )
    return (
        row["runtime_type"],
        row["model_id"],
        _parse_extra_args(row["extra_args"]),
        row["id"],
        row["session_timeout_s"],
        effective_tier,
    )


# ---------------------------------------------------------------------------
# Intent-aware resolution (bu-6jv4m.7)
# ---------------------------------------------------------------------------


class CandidateOutcome(enum.StrEnum):
    """What happened to one candidate during an intent-aware resolution."""

    SELECTED = "selected"
    ELIGIBLE = "eligible"
    """Fit the intent and reached the ranking step, but another candidate won."""

    EXCLUDED_HARD_FIT = "excluded_hard_fit"
    """Disqualified by capability / context / deadline / budget fit, before ranking."""

    EXCLUDED_QUOTA = "excluded_quota"
    NOT_TOP_PRIORITY = "not_top_priority"
    """Fit the intent, but a higher effective priority existed in the same tier."""

    TIER_NOT_REACHED = "tier_not_reached"
    """In a lower fallthrough tier than the one that produced a survivor."""


WINNER_REASON_SOLE_CANDIDATE = "sole_candidate"
WINNER_REASON_EVIDENCE_SCORE = "evidence_score"
WINNER_REASON_ROUND_ROBIN = "round_robin"


@dataclasses.dataclass(frozen=True)
class CandidateRecord:
    """One catalog entry's fate in a resolution, as recorded on the receipt.

    Deliberately prompt-free: identifiers, tiers, outcomes, and fit findings only.
    Nothing here carries session content, so the receipt can be stored and shown
    without dragging prompt text into a new place.
    """

    catalog_entry_id: uuid.UUID
    runtime_type: str
    model_id: str
    effective_tier: str
    effective_priority: int
    outcome: CandidateOutcome
    exclusions: tuple[FitFinding, ...] = ()
    advisories: tuple[FitFinding, ...] = ()
    evidence_samples: int = 0
    evidence_age_s: float | None = None
    score: float | None = None

    def describe(self) -> dict[str, Any]:
        """JSON-safe projection for the resolution receipt."""
        return {
            "catalog_entry_id": str(self.catalog_entry_id),
            "runtime_type": self.runtime_type,
            "model_id": self.model_id,
            "effective_tier": self.effective_tier,
            "effective_priority": self.effective_priority,
            "outcome": self.outcome.value,
            "exclusions": [f.describe() for f in self.exclusions],
            "advisories": [f.describe() for f in self.advisories],
            "evidence_samples": self.evidence_samples,
            "evidence_age_s": self.evidence_age_s,
            "score": self.score,
        }


@dataclasses.dataclass(frozen=True)
class DispatchResolution:
    """The full, replayable account of one intent-aware model resolution.

    ``selection`` is the same 6-tuple ``resolve_model_with_effective_tier`` returns, so
    a caller can use this object and ignore the receipt entirely. ``requested_intent``
    and ``effective_intent`` differ only in ``complexity_tier``, and only when tier
    fallthrough occurred -- recording both is what makes "why did a cheap-tier butler
    end up on a local model?" answerable after the fact instead of re-derived.
    """

    policy_version: str
    requested_intent: DispatchIntent
    effective_intent: DispatchIntent
    candidates: tuple[CandidateRecord, ...]
    selection: tuple[str, str, list[str], uuid.UUID, int, str] | None = None
    winner_reason: str | None = None

    @property
    def effective_tier(self) -> str | None:
        """Tier that produced the winner, or ``None`` when nothing was selected."""
        return self.selection[5] if self.selection is not None else None

    def describe(self) -> dict[str, Any]:
        """JSON-safe, prompt-free receipt."""
        return {
            "policy_version": self.policy_version,
            "requested_intent": self.requested_intent.describe(),
            "effective_intent": self.effective_intent.describe(),
            "winner": (
                None
                if self.selection is None
                else {
                    "catalog_entry_id": str(self.selection[3]),
                    "runtime_type": self.selection[0],
                    "model_id": self.selection[1],
                    "effective_tier": self.selection[5],
                    "reason": self.winner_reason,
                }
            ),
            "candidates": [c.describe() for c in self.candidates],
        }


def _evidence_age_s(row: asyncpg.Record, *, now: datetime) -> float | None:
    """Seconds since this candidate's most recent counted dispatch attempt."""
    last = row["last_attempt_at"]
    if last is None:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return max(0.0, (now - last).total_seconds())


def _row_capabilities(row: asyncpg.Record) -> CapabilityDescriptor | CapabilityDescriptorError:
    """Layer this row's stored envelope over its adapter baseline, or report the error."""
    try:
        return effective_capabilities(
            row["runtime_type"],
            row["capabilities"],
            max_context_tokens=row["max_context_tokens"],
            max_output_tokens=row["max_output_tokens"],
        )
    except CapabilityDescriptorError as exc:
        return exc


async def resolve_dispatch(
    pool: asyncpg.Pool,
    butler_name: str,
    intent: DispatchIntent,
    *,
    allow_tier_fallthrough: bool = True,
    quota_aware: bool = False,
) -> DispatchResolution:
    """Resolve a model for a ``DispatchIntent``, filtering hard fit before ranking.

    The one behavioural difference from ``resolve_model_with_effective_tier``: a
    candidate that cannot satisfy the intent's *required* features, context floor,
    deadline, or per-call budget is removed from consideration BEFORE priority
    narrowing and before the evidence/round-robin tie-break. Order matters. Today's
    resolver narrows to the winning tier's top-priority set first, so if the single
    top-priority entry cannot do the job, the dispatch fails (or silently runs on a
    model that will raise at invoke time) even when a perfectly capable lower-priority
    entry sits in the same tier. ``public.model_catalog``'s seeded ``api-haiku-cheap``
    is exactly that shape: priority 30, top of the ``cheap`` tier, and
    ``ApiAdapter.invoke`` raises for any non-empty ``mcp_servers`` -- which every
    trigger source except ``healing``/``qa`` supplies.

    An intent that requires nothing (``required_features`` empty, no context floor, no
    deadline, no budget) excludes nobody, so it resolves to exactly what the legacy
    path resolves to. That is the intended migration property, and it is asserted
    directly in the tests rather than assumed.

    Ranking itself is unchanged: same effective-priority narrowing, same
    ``compute_routing_score`` evidence tie-break above ``_EVIDENCE_MIN_SAMPLES``, same
    round-robin counter below it. ``preferred_features`` are recorded on the receipt
    and never influence the winner in this policy version -- preferring, say,
    resume-capable models for interactive triggers is a cost/quality trade-off that
    belongs to the owner, not to this function.

    Never served from the routing-decision cache: a resolution is per-intent, and the
    cache is keyed only by ``(butler_name, tiers)``.

    Raises
    ------
    TierQuotaExhausted
        Only when ``quota_aware=True`` and the fit-surviving top-priority set in the
        winning tier contains a quota-blocked entry -- same conservative contract as
        ``resolve_model_with_effective_tier``, with the receipt attached to the
        exception's ``resolution`` attribute.
    """
    tier_value = _check_deprecated_tier(str(intent.complexity_tier))
    if allow_tier_fallthrough and tier_value in TIER_FALLTHROUGH_ORDER:
        start_idx = TIER_FALLTHROUGH_ORDER.index(tier_value)
        tiers_to_try = list(TIER_FALLTHROUGH_ORDER[start_idx:])
    else:
        tiers_to_try = [tier_value]

    requested_intent = dataclasses.replace(intent, complexity_tier=tier_value)
    rows = await pool.fetch(_RESOLVE_CANDIDATES_SQL, butler_name, tiers_to_try)
    if not rows:
        return DispatchResolution(
            policy_version=DISPATCH_POLICY_VERSION,
            requested_intent=requested_intent,
            effective_intent=requested_intent,
            candidates=(),
        )

    pricing = _get_cached_pricing()
    now = datetime.now(tz=UTC)

    # Fit first, across every tier, before anything narrows.
    verdicts: dict[uuid.UUID, FitVerdict] = {}
    scores: dict[uuid.UUID, RoutingScore] = {}
    for row in rows:
        capabilities = _row_capabilities(row)
        if isinstance(capabilities, CapabilityDescriptorError):
            logger.warning(
                "resolve_dispatch: catalog entry %s has an unusable capability envelope "
                "and cannot be dispatched to: %s",
                row["id"],
                capabilities,
            )
            verdicts[row["id"]] = FitVerdict(
                eligible=False,
                exclusions=(FitFinding(FitCode.CAPABILITY_DESCRIPTOR_INVALID, str(capabilities)),),
            )
        else:
            score = _score_row(row, pricing)
            scores[row["id"]] = score
            verdicts[row["id"]] = evaluate_fit(
                requested_intent,
                capabilities,
                observed_p95_ms=(
                    float(row["p95_duration_ms"]) if row["p95_duration_ms"] is not None else None
                ),
                reference_cost_usd=_reference_cost_usd(pricing, row["model_id"]),
            )

    # First tier (in fallthrough order) with at least one candidate that fits.
    winning_tier: str | None = None
    for row in rows:
        if verdicts[row["id"]].eligible:
            winning_tier = row["effective_tier"]
            break

    def _record(row: asyncpg.Record, outcome: CandidateOutcome) -> CandidateRecord:
        verdict = verdicts[row["id"]]
        score = scores.get(row["id"])
        return CandidateRecord(
            catalog_entry_id=row["id"],
            runtime_type=row["runtime_type"],
            model_id=row["model_id"],
            effective_tier=row["effective_tier"],
            effective_priority=int(row["effective_priority"]),
            outcome=outcome,
            exclusions=verdict.exclusions,
            advisories=verdict.advisories,
            evidence_samples=int(row["success_count"] or 0) + int(row["failure_count"] or 0),
            evidence_age_s=_evidence_age_s(row, now=now),
            score=None if score is None else score.score,
        )

    if winning_tier is None:
        # Every candidate in every tier failed hard fit. This is NOT the same as "no
        # catalog entries exist": the caller's static fallback is still the right
        # recovery, but the receipt says why, which "returned None" never could.
        candidates = tuple(_record(row, CandidateOutcome.EXCLUDED_HARD_FIT) for row in rows)
        logger.warning(
            "resolve_dispatch: butler %r has %d eligible catalog entries but none fit "
            "intent (trigger_class=%s, tier=%s)",
            butler_name,
            len(rows),
            requested_intent.trigger_class,
            tier_value,
        )
        return DispatchResolution(
            policy_version=DISPATCH_POLICY_VERSION,
            requested_intent=requested_intent,
            effective_intent=requested_intent,
            candidates=candidates,
        )

    effective_intent = (
        requested_intent
        if winning_tier == tier_value
        else dataclasses.replace(requested_intent, complexity_tier=winning_tier)
    )
    if winning_tier != tier_value:
        logger.debug(
            "resolve_dispatch: no fitting entry in tier %r for butler %r; fell through to %r",
            tier_value,
            butler_name,
            winning_tier,
        )

    winning_tier_ord = next(r["tier_ord"] for r in rows if r["effective_tier"] == winning_tier)
    in_tier = [r for r in rows if r["effective_tier"] == winning_tier]
    survivors = [r for r in in_tier if verdicts[r["id"]].eligible]
    best_priority = max(int(r["effective_priority"]) for r in survivors)
    top = [r for r in survivors if int(r["effective_priority"]) == best_priority]

    # Counter always advances once a winning tier exists, before quota is consulted --
    # matching the legacy path's gating so round-robin fairness does not drift between
    # the two resolvers.
    rr_counter = await pool.fetchval(_ROUTING_COUNTER_INCREMENT_SQL, butler_name, winning_tier)

    def _build(
        winner: asyncpg.Record | None, reason: str | None, *, quota_blocked: bool
    ) -> DispatchResolution:
        top_ids = {r["id"] for r in top}
        records = []
        for row in rows:
            rid = row["id"]
            if row["tier_ord"] > winning_tier_ord:
                # Below the winning tier in fallthrough order: never evaluated against
                # a winner, so "did not fit" would be a claim the resolver never made.
                outcome = CandidateOutcome.TIER_NOT_REACHED
            elif not verdicts[rid].eligible:
                # Includes every candidate in a HIGHER tier: that tier lost only
                # because none of its entries fit, and the receipt must say so.
                outcome = CandidateOutcome.EXCLUDED_HARD_FIT
            elif rid not in top_ids:
                outcome = CandidateOutcome.NOT_TOP_PRIORITY
            elif quota_blocked and not row["quota_ok"]:
                outcome = CandidateOutcome.EXCLUDED_QUOTA
            elif winner is not None and rid == winner["id"]:
                outcome = CandidateOutcome.SELECTED
            else:
                outcome = CandidateOutcome.ELIGIBLE
            records.append(_record(row, outcome))
        return DispatchResolution(
            policy_version=DISPATCH_POLICY_VERSION,
            requested_intent=requested_intent,
            effective_intent=effective_intent,
            candidates=tuple(records),
            selection=(
                None
                if winner is None
                else (
                    winner["runtime_type"],
                    winner["model_id"],
                    _parse_extra_args(winner["extra_args"]),
                    winner["id"],
                    winner["session_timeout_s"],
                    winning_tier,
                )
            ),
            winner_reason=reason,
        )

    winner, reason = _rank_candidates(top, scores, rr_counter)

    if quota_aware and not all(r["quota_ok"] for r in top):
        resolution = _build(winner, reason, quota_blocked=True)
        assert resolution.selection is not None
        raise TierQuotaExhausted(
            effective_tier=winning_tier,
            representative=resolution.selection,
            resolution=resolution,
        )

    return _build(winner, reason, quota_blocked=False)


def _rank_candidates(
    top: list[asyncpg.Record],
    scores: Mapping[uuid.UUID, RoutingScore],
    rr_counter: int | None,
) -> tuple[asyncpg.Record, str]:
    """Pick the winner among a fit-surviving, equal-priority set, and say why.

    Same policy as ``_select_resolved_row``: evidence-based scoring once at least two
    candidates carry enough recent samples to be scored, otherwise the round-robin
    counter over the deterministic ``priority DESC, created_at ASC, id ASC`` order the
    query already produced. The only addition is the reason string, which the receipt
    needs so "why this model?" does not have to be re-derived from the tie-break rules.
    """
    if len(top) == 1:
        return top[0], WINNER_REASON_SOLE_CANDIDATE

    scored = [(row, scores[row["id"]]) for row in top if scores.get(row["id"]) is not None]
    eligible = [(row, s) for row, s in scored if s.score is not None]
    if len(eligible) >= 2:
        best_row, _ = max(eligible, key=lambda pair: pair[1].score)
        return best_row, WINNER_REASON_EVIDENCE_SCORE

    assert rr_counter is not None, "_rank_candidates: rr_counter required for round-robin"
    return top[rr_counter % len(top)], WINNER_REASON_ROUND_ROBIN


async def next_same_tier_candidate(
    pool: asyncpg.Pool,
    butler_name: str,
    effective_tier: str,
    attempted_ids: list[uuid.UUID],
) -> tuple[str, str, list[str], uuid.UUID, int] | None:
    """Return the next eligible model in the exact effective tier, excluding attempted IDs.

    Used by the spawner failover loop to iterate over same-tier candidates without
    repeating entries that have already been attempted or explicitly skipped.

    Resolution applies the same COALESCE override semantics as ``resolve_model``
    (per-butler ``enabled``, ``priority``, and ``complexity_tier`` overrides take
    precedence over catalog defaults).  State filtering mirrors the primary resolver:
    entries with ``last_verified_ok = false`` are excluded.

    Ordering is deterministic — NOT round-robin — so failover progression is
    predictable: ``effective_priority DESC``, then ``created_at ASC``, then ``id ASC``.

    Parameters
    ----------
    pool:
        An asyncpg connection pool connected to the butlers database.
    butler_name:
        The butler identity name.  Used to look up per-butler overrides.
    effective_tier:
        The exact effective complexity tier to search (canonical string, e.g.
        ``"workhorse"``).  Must match the effective tier returned by the initial
        ``resolve_model`` or ``resolve_model_with_effective_tier`` call so that
        failover stays within the same resolved tier.
    attempted_ids:
        Catalog entry IDs that have already been attempted or explicitly skipped
        for this logical session.  All of these are excluded from the result.

    Returns
    -------
    tuple[str, str, list[str], uuid.UUID, int] | None
        ``(runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s)``
        for the next eligible candidate, or ``None`` when all same-tier candidates
        are exhausted.
    """
    row = await pool.fetchrow(_NEXT_SAME_TIER_SQL, butler_name, effective_tier, attempted_ids)
    if row is None:
        return None
    return (
        row["runtime_type"],
        row["model_id"],
        _parse_extra_args(row["extra_args"]),
        row["id"],
        row["session_timeout_s"],
    )


def _parse_max_cost_per_call(action: dict, rule_id: object) -> float | None:
    """Extract and validate the ``action.max_cost_per_call`` per-call USD cap.

    Returns the cap as a positive float, or ``None`` when the action sets no cap or
    the configured value is malformed (non-numeric or non-positive).  A malformed cap
    is ignored with a warning rather than failing the dispatch — a routing rule must
    never wedge a spawn because of bad effect data.
    """
    raw = action.get("max_cost_per_call")
    if raw is None:
        return None
    try:
        cap = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "apply_spend_routing_rules: rule %s has non-numeric action.max_cost_per_call=%r; "
            "ignoring the cap",
            rule_id,
            raw,
        )
        return None
    if cap <= 0:
        logger.warning(
            "apply_spend_routing_rules: rule %s has non-positive action.max_cost_per_call=%r; "
            "ignoring the cap",
            rule_id,
            raw,
        )
        return None
    return cap


async def apply_spend_routing_rules(
    pool: asyncpg.Pool,
    butler_name: str,
    complexity_tier: Complexity | str,
    resolved: tuple[str, str, list[str], uuid.UUID, int],
    *,
    trigger_source: str | None = None,
) -> SpendRoutingResult:
    """Apply operator-configured spend routing rules to a tier-resolved model.

    The Settings → Spend page stores ordered routing rules in ``public.spend_rules``
    (``condition`` JSONB → ``action`` JSONB, ``position``-sorted).  The UI promises
    "rules evaluate top-to-bottom and the first match wins".  This function makes that
    promise real: it runs AFTER tier-based resolution (``resolve_model*``) and BEFORE
    the spawn-time DENY gates (token quota, spend ceiling, permissions), evaluating the
    rules top-to-bottom and applying the first rule whose ``condition`` matches the
    dispatch context.

    Rule semantics
    --------------
    - ``condition`` (JSONB object): constraints ANDed together; an empty ``{}`` is a
      catch-all.  Supported keys: ``butler``, ``complexity`` (alias ``tier``), and
      ``trigger`` (alias ``purpose``) — both evaluate against the dispatch
      ``trigger_source``.  Values may be scalars (exact match) or lists (membership).
      See ``_rule_condition_matches``.
    - ``action.model`` (str, optional): the priced ``model_id`` to route TO.  The
      matched model is re-resolved against ``public.model_catalog`` (honoring per-butler
      overrides) to the highest-priority enabled, non-failed entry for that ``model_id``
      — so the override lands on a real dispatchable catalog row with its own
      ``runtime_type``, ``extra_args``, ``catalog_entry_id``, and ``session_timeout_s``.
      Downstream quota / ledger / failover therefore operate on the rule-selected model.
    - ``action.max_cost_per_call`` (float, optional): a hard per-dispatch USD cap.  This
      function does NOT enforce it — enforcement is a spawn-time DENY decision made by
      the caller (the spawner), which knows the call's token budget and pricing.  The
      cap is surfaced on ``SpendRoutingResult.max_cost_per_call`` so the spawner can
      enforce/deny.  A rule may set the model effect, the cap effect, or both.

    First match wins: once a rule's condition matches, evaluation stops — later rules
    are not considered, exactly as the UI copy states.  The single matching rule
    supplies BOTH effects (model re-route and/or per-call cap).

    Robustness
    ----------
    - A matched rule whose ``action.model`` routes to a model that resolves to no
      dispatchable catalog row (e.g. disabled for this butler, or failed verification)
      leaves the originally-resolved model UNCHANGED and logs a warning — but any
      ``max_cost_per_call`` from the SAME matching rule still applies.
    - A matched rule with NO ``action.model`` is valid when it carries a cap (cap-only
      rule); the tier-resolved model is kept and the cap is surfaced.  A matched rule
      with neither effect keeps the model and logs a warning.
    - This helper is purely a model-SELECTION + effect-surfacing step.  It never blocks
      a dispatch — the authorization (permissions) and budget (quota / ceiling /
      per-call cap) DENY gates are the caller's responsibility.
    - Fail-open: any DB error or malformed rule data leaves ``resolved`` unchanged with
      no cap and logs a warning, so a routing-rules failure never wedges spawns.

    Parameters
    ----------
    pool:
        asyncpg connection pool connected to the butlers database.
    butler_name:
        The butler identity name driving the dispatch.
    complexity_tier:
        The requested complexity tier (``Complexity`` or canonical string).  Matched
        against rule ``complexity``/``tier`` constraints.
    resolved:
        The tier-resolved model tuple
        ``(runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s)``
        produced by ``resolve_model`` / ``resolve_model_with_effective_tier``.
    trigger_source:
        The dispatch trigger source (e.g. ``"route"``, ``"qa"``, ``"healing"``) used to
        evaluate ``condition.trigger``.  ``None`` (the default) means a ``trigger``
        constraint cannot match.

    Returns
    -------
    SpendRoutingResult
        ``.resolved`` is the (possibly rule-overridden) model tuple; ``.max_cost_per_call``
        is the per-call USD cap from the matching rule (or ``None``).
    """
    if isinstance(complexity_tier, Complexity):
        tier_value = complexity_tier.value
    else:
        tier_value = _check_deprecated_tier(str(complexity_tier))

    try:
        rule_rows = await pool.fetch(_SPEND_RULES_SELECT_SQL)
    except Exception:
        logger.warning(
            "apply_spend_routing_rules: failed to load spend_rules for butler=%s; "
            "keeping tier-resolved model (fail-open)",
            butler_name,
            exc_info=True,
        )
        return SpendRoutingResult(resolved=resolved)

    for rule_row in rule_rows:
        condition = _coerce_rule_dict(rule_row["condition"])
        if not _rule_condition_matches(
            condition,
            butler_name=butler_name,
            complexity_tier=tier_value,
            trigger_source=trigger_source,
        ):
            continue

        # First match wins — stop evaluating further rules regardless of outcome.
        rule_id = rule_row["id"]
        action = _coerce_rule_dict(rule_row["action"])
        max_cost_per_call = _parse_max_cost_per_call(action, rule_id)
        target_model = action.get("model")

        if not target_model or not isinstance(target_model, str):
            if max_cost_per_call is None:
                logger.warning(
                    "apply_spend_routing_rules: rule %s matched (butler=%s tier=%s) but has no "
                    "action.model or action.max_cost_per_call; keeping tier-resolved model %s",
                    rule_id,
                    butler_name,
                    tier_value,
                    resolved[1],
                )
            else:
                logger.info(
                    "apply_spend_routing_rules: rule %s matched (butler=%s tier=%s); "
                    "cap-only rule, per-call cap=$%.4f, model %s unchanged",
                    rule_id,
                    butler_name,
                    tier_value,
                    max_cost_per_call,
                    resolved[1],
                )
            return SpendRoutingResult(resolved=resolved, max_cost_per_call=max_cost_per_call)

        try:
            row = await pool.fetchrow(_RESOLVE_BY_MODEL_ID_SQL, butler_name, target_model)
        except Exception:
            logger.warning(
                "apply_spend_routing_rules: rule %s matched but resolving target model %r "
                "failed for butler=%s; keeping tier-resolved model %s (fail-open)",
                rule_id,
                target_model,
                butler_name,
                resolved[1],
                exc_info=True,
            )
            return SpendRoutingResult(resolved=resolved, max_cost_per_call=max_cost_per_call)

        if row is None:
            logger.warning(
                "apply_spend_routing_rules: rule %s matched (butler=%s tier=%s) routing to "
                "model %r, but no dispatchable catalog entry resolves for it "
                "(disabled/failed-verification?); keeping tier-resolved model %s",
                rule_id,
                butler_name,
                tier_value,
                target_model,
                resolved[1],
            )
            return SpendRoutingResult(resolved=resolved, max_cost_per_call=max_cost_per_call)

        logger.info(
            "apply_spend_routing_rules: rule %s matched (butler=%s tier=%s); routed model %s -> %s"
            "%s",
            rule_id,
            butler_name,
            tier_value,
            resolved[1],
            row["model_id"],
            f" (per-call cap=${max_cost_per_call:.4f})" if max_cost_per_call is not None else "",
        )

        # Circuit-breaker awareness (bu-14j0m, decision (b)): an operator spend
        # rule is explicit human intent, so a breaker-open target is HONORED,
        # not silently excluded — silently vetoing operator config would turn an
        # availability signal into a hidden override (the same fabricated-calm
        # failure mode inverted). Instead we warn visibly and surface the
        # breaker state so the spawner records it on the dispatch-attempt trail;
        # the existing same-tier failover (``next_same_tier_candidate`` excludes
        # breaker-open entries) still handles any real dispatch failure.
        # Fail-open: a breaker probe error must never wedge the spawn.
        breaker_open_state: BreakerState | None = None
        try:
            breaker_state = await get_breaker_state(pool, row["id"])
            if breaker_state.open:
                breaker_open_state = breaker_state
                logger.warning(
                    "apply_spend_routing_rules: rule %s routed butler=%s tier=%s to model %s "
                    "(catalog_entry=%s) whose dispatch-outcome circuit breaker is OPEN "
                    "(%d consecutive runtime failures, last attempt %s); honoring the operator "
                    "rule (not excluding) — same-tier failover remains available if dispatch fails",
                    rule_id,
                    butler_name,
                    tier_value,
                    row["model_id"],
                    row["id"],
                    breaker_state.consecutive_failures,
                    breaker_state.last_attempt_at,
                )
        except Exception:
            logger.debug(
                "apply_spend_routing_rules: breaker probe failed for rule-selected model %s "
                "(catalog_entry=%s); proceeding without breaker annotation (fail-open)",
                row["model_id"],
                row["id"],
                exc_info=True,
            )

        return SpendRoutingResult(
            resolved=(
                row["runtime_type"],
                row["model_id"],
                _parse_extra_args(row["extra_args"]),
                row["id"],
                row["session_timeout_s"],
            ),
            max_cost_per_call=max_cost_per_call,
            breaker_open=breaker_open_state,
        )

    # No rule matched — tier-based resolution stands.
    return SpendRoutingResult(resolved=resolved)


async def check_token_quota(
    pool: asyncpg.Pool,
    catalog_entry_id: uuid.UUID,
) -> QuotaStatus:
    """Check whether a catalog entry's token usage is within its configured limits.

    Uses a CTE-based single round-trip query that computes both 24h and 30d
    window usages, respecting independent reset markers.

    Fast path: if no ``public.token_limits`` row exists for the entry, returns
    ``QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)``
    without querying the ledger.

    Fail-open: if the DB query fails for any reason (timeout, missing partition,
    connection error), returns ``allowed=True`` and logs a warning.  The quota
    guardrail must never become a single point of failure.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    catalog_entry_id:
        UUID of the ``public.model_catalog`` row to check.

    Returns
    -------
    QuotaStatus
        Quota check result with usage and limit figures for both windows.
    """
    _unlimited = QuotaStatus(
        allowed=True,
        usage_24h=0,
        limit_24h=None,
        usage_30d=0,
        limit_30d=None,
    )

    try:
        # Fast path: no limits row → entry is unlimited, skip ledger query.
        limits_row = await pool.fetchrow(_LIMITS_EXISTS_SQL, catalog_entry_id)
        if limits_row is None:
            return _unlimited

        row = await pool.fetchrow(_QUOTA_CHECK_SQL, catalog_entry_id)
        if row is None:
            # Race condition: limits row was deleted between the existence check and
            # the CTE query. Treat as unlimited for safety.
            return _unlimited

        limit_24h: int | None = row["limit_24h"]
        limit_30d: int | None = row["limit_30d"]
        used_24h: int = int(row["used_24h"])
        used_30d: int = int(row["used_30d"])

        allowed = not (
            (limit_24h is not None and used_24h >= limit_24h)
            or (limit_30d is not None and used_30d >= limit_30d)
        )

        return QuotaStatus(
            allowed=allowed,
            usage_24h=used_24h,
            limit_24h=limit_24h,
            usage_30d=used_30d,
            limit_30d=limit_30d,
        )

    except Exception:
        logger.warning(
            "check_token_quota failed for catalog_entry_id=%s; failing open (allowed=True)",
            catalog_entry_id,
            exc_info=True,
        )
        return _unlimited


def price_ledger_usage_rows(
    usage_rows: Iterable[Mapping[str, object]],
    pricing: PricingConfig | None = None,
) -> LedgerSpend:
    """Price grouped ledger rows without erasing models missing from pricing.

    ``usage_rows`` must expose an executed ``model_id`` and the four ledger
    token buckets. ``calls`` is optional for backwards-compatible callers and
    defaults to one. The function is deliberately shared by the monthly
    ceiling and dashboard aggregates: every consumer gets the same priced
    subtotal and the same omission envelope.
    """
    # Lazy import keeps optional pricing policy out of routing module
    # initialization. It also mirrors the existing pricing path used by the
    # spawner's live event emission.
    from butlers.core.pricing import estimate_session_cost, load_pricing

    effective_pricing = pricing or load_pricing()
    cost_usd = 0.0
    unpriced_by_model: dict[str, dict[str, int]] = {}

    for row in usage_rows:
        model_id = str(row.get("model_id") or "unknown")
        calls = int(row.get("calls") or 1)
        input_tokens = int(row.get("input_tokens") or 0)
        output_tokens = int(row.get("output_tokens") or 0)
        cached_input_tokens = int(row.get("cached_input_tokens") or 0)
        cache_creation_tokens = int(row.get("cache_creation_tokens") or 0)
        cost = estimate_session_cost(
            effective_pricing,
            model_id,
            input_tokens,
            output_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )
        if cost is not None:
            cost_usd += cost
            continue

        usage = unpriced_by_model.setdefault(
            model_id,
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
                "cache_creation_tokens": 0,
            },
        )
        usage["calls"] += calls
        usage["input_tokens"] += input_tokens
        usage["output_tokens"] += output_tokens
        usage["cached_input_tokens"] += cached_input_tokens
        usage["cache_creation_tokens"] += cache_creation_tokens

    return LedgerSpend(
        cost_usd=cost_usd,
        unpriced_models=tuple(
            UnpricedModelUsage(model=model_id, **usage)
            for model_id, usage in sorted(unpriced_by_model.items())
        ),
    )


async def price_mtd_from_ledger(
    pool: asyncpg.Pool,
    pricing: PricingConfig | None = None,
) -> LedgerSpend:
    """Price month-to-date spend directly from ``public.token_usage_ledger``.

    Aggregates the current-month ledger rows by ``model_id`` (joined to
    ``public.model_catalog``) and prices each bucket via
    ``butlers.core.pricing.estimate_session_cost`` — the same pathway the
    spawner uses when emitting per-call spend events — through a lazy import
    to keep optional pricing policy out of module initialization.

    This is the single source of truth for "how much has been spent this
    month", shared by :func:`check_monthly_ceiling` (the spawn-deny gate) and
    the dashboard's ``GET /api/spend/forecast`` MTD/projected-EOM figures
    (bu-7o89u.1) — before this, the dashboard priced MTD from a per-butler
    sessions fan-out while the gate priced the ledger, so the dashboard could
    show e.g. 92% of ceiling while spawns were already being denied.

    Unlike ``check_monthly_ceiling``, this raises on any DB or pricing
    failure instead of failing open — each caller applies its own semantics
    on top (the spawn gate fails open so a ledger outage never wedges spawns;
    the dashboard reports a degraded envelope so a ledger outage never
    renders as a fabricated $0 MTD).

    Parameters
    ----------
    pool:
        asyncpg connection pool with visibility into ``public.token_usage_ledger``
        and ``public.model_catalog`` (the "switchboard" pool in the dashboard API).

    Returns
    -------
    LedgerSpend
        Estimated month-to-date priced subtotal plus any unpriced model usage.
    """
    usage_rows = await pool.fetch(_MTD_USAGE_BY_MODEL_SQL)

    return price_ledger_usage_rows(usage_rows, pricing)


async def check_monthly_ceiling(
    pool: asyncpg.Pool,
) -> CeilingStatus:
    """Check whether month-to-date spend is within the configured monthly ceiling.

    Reads the singleton ceiling from ``public.spend_ceiling`` (id=1) and prices
    month-to-date spend via :func:`price_mtd_from_ledger` — the same helper the
    dashboard's ``GET /api/spend/forecast`` uses, so the two can never diverge.

    Fast path: when no ceiling row exists (or it is non-positive), the spawn is
    unconditionally allowed and the ledger is not queried.

    Fail-open: if any DB query or pricing computation fails, returns
    ``allowed=True`` and logs a warning.  Like the token-quota guardrail, the
    ceiling check must never become a single point of failure that wedges spawns.

    Parameters
    ----------
    pool:
        asyncpg connection pool connected to the butlers database.

    Returns
    -------
    CeilingStatus
        Ceiling check result with the estimated MTD spend and configured ceiling.
    """
    _unlimited = CeilingStatus(allowed=True, mtd_usd=0.0, ceiling_usd=None)

    try:
        ceiling_row = await pool.fetchrow(_CEILING_SELECT_SQL)
        if ceiling_row is None:
            return _unlimited
        ceiling_usd = float(ceiling_row["monthly_usd"])
        if ceiling_usd <= 0:
            # Non-positive ceiling is treated as "no ceiling configured".
            return _unlimited

        spend = await price_mtd_from_ledger(pool)

        return CeilingStatus(
            allowed=spend.cost_usd < ceiling_usd,
            mtd_usd=spend.cost_usd,
            ceiling_usd=ceiling_usd,
            unpriced_models=spend.unpriced_models,
        )

    except Exception:
        logger.warning(
            "check_monthly_ceiling failed; failing open (allowed=True)",
            exc_info=True,
        )
        return _unlimited


async def record_token_usage(
    pool: asyncpg.Pool,
    *,
    catalog_entry_id: uuid.UUID,
    butler_name: str,
    session_id: uuid.UUID | None,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    cache_creation_tokens: int = 0,
    purpose: str | None = None,
) -> None:
    """Record token usage to ``public.token_usage_ledger``.

    Best-effort: errors are logged as warnings and never propagate to the caller.
    A ledger write failure must never block a session result from being returned.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    catalog_entry_id:
        UUID of the resolved ``public.model_catalog`` row.
    butler_name:
        Name of the butler that spawned the session, or the per-connector
        identity for discretion dispatcher calls (e.g. ``"tg:<chat_id>"``);
        falls back to ``"__discretion__"`` when no identity is available.
    session_id:
        UUID of the spawner session, or ``None`` for discretion dispatcher calls.
    input_tokens:
        Number of UNCACHED input tokens reported by the adapter (see the
        runtime usage contract in ``butlers.core.runtimes.base``).
    output_tokens:
        Number of output tokens reported by the adapter.
    cached_input_tokens:
        Prompt-cache READ tokens reported by the adapter.
    cache_creation_tokens:
        Prompt-cache WRITE tokens reported by the adapter.
    purpose:
        Coarse "why" dimension for spend attribution (bu-qvnce.12), e.g. the
        spawner's ``trigger_source`` (``route``/``schedule``/``classification``/
        ``healing``/...) or ``"discretion"`` for connector discretion screening.
        ``None`` when the caller has no meaningful purpose to report (kept
        nullable rather than defaulted so honestly-unknown rows stay
        distinguishable from a real, named purpose).
    """
    try:
        await pool.execute(
            _LEDGER_INSERT_SQL,
            catalog_entry_id,
            butler_name,
            session_id,
            input_tokens,
            output_tokens,
            cached_input_tokens,
            cache_creation_tokens,
            purpose,
        )
    except Exception:
        logger.warning(
            "record_token_usage failed for catalog_entry_id=%s butler=%s; "
            "usage not recorded (best-effort)",
            catalog_entry_id,
            butler_name,
            exc_info=True,
        )
