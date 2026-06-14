"""Dynamic model routing — catalog-based model selection with per-butler overrides.

Provides:
- ``Complexity`` enum (canonical six: reasoning / workhorse / cheap / specialty / local / legacy)
- ``resolve_model(pool, butler_name, complexity_tier)`` — highest-priority enabled model
  in tier whose state ∈ {verified, untested}; falls through canonical tier order if none
  qualify in the requested tier.
- ``resolve_model_with_effective_tier(pool, butler_name, complexity_tier)`` — same as
  ``resolve_model`` but also returns the effective tier that produced the candidate (needed
  for same-tier failover to stay within the resolved tier).
- ``next_same_tier_candidate(pool, butler_name, effective_tier, attempted_ids)`` — returns
  the next eligible model in an exact effective complexity tier, excluding already-attempted
  catalog entry IDs.  Used by the spawner failover loop to iterate within the same tier.
- ``QuotaStatus`` dataclass — result of a pre-spawn token quota check.
- ``check_token_quota(pool, catalog_entry_id)`` — CTE-based single-query quota check.
- ``check_monthly_ceiling(pool)`` — pre-spawn monthly USD spend-ceiling check.
- ``record_token_usage(pool, ...)`` — best-effort ledger INSERT.

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
import uuid

import asyncpg

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
    """

    resolved: tuple[str, str, list[str], uuid.UUID, int]
    max_cost_per_call: float | None = None


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
    """

    allowed: bool
    mtd_usd: float
    ceiling_usd: float | None


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
# 3. winning:       Find the first tier (lowest ord) that has at least one qualifying
#                   model; also record its max priority so step 4 can filter to
#                   top-priority entries only.
# 4. candidates:    Narrow to top-priority models in the winning tier, decorated with
#                   a stable round-robin row number (created_at ASC, id ASC tie-break).
# 5. next_counter:  INSERT...SELECT from `winning` — fires ONLY when a winning tier
#                   exists, so empty-tier fallthrough attempts never increment any
#                   counter.  Atomically increments the per-(butler, tier) counter.
# 6. Final SELECT:  Picks the candidate at index (counter % total).
#
# Returns: (runtime_type, model_id, extra_args, id, session_timeout_s, effective_tier)
# Returns no rows when no qualifying model exists in any provided tier.
_RESOLVE_SQL = """
WITH
tier_order AS (
    SELECT t.tier, t.ord
    FROM unnest($2::text[]) WITH ORDINALITY AS t(tier, ord)
),
all_candidates AS (
    SELECT
        mc.runtime_type,
        mc.model_id,
        mc.extra_args,
        mc.id,
        mc.session_timeout_s,
        mc.created_at,
        COALESCE(bmo.complexity_tier, mc.complexity_tier) AS effective_tier,
        COALESCE(bmo.priority, mc.priority) AS effective_priority,
        t.ord AS tier_ord
    FROM public.model_catalog mc
    LEFT JOIN public.butler_model_overrides bmo
        ON bmo.catalog_entry_id = mc.id AND bmo.butler_name = $1
    JOIN tier_order t
        ON COALESCE(bmo.complexity_tier, mc.complexity_tier) = t.tier
    WHERE COALESCE(bmo.enabled, mc.enabled) = true
      AND mc.last_verified_ok IS DISTINCT FROM false
),
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
        ROW_NUMBER() OVER (ORDER BY ac.created_at ASC, ac.id ASC) - 1 AS rn,
        COUNT(*) OVER () AS total
    FROM all_candidates ac
    JOIN winning w
        ON ac.effective_tier = w.effective_tier
        AND ac.tier_ord = w.tier_ord
        AND ac.effective_priority = w.max_priority
),
next_counter AS (
    INSERT INTO public.model_round_robin_counters
        (butler_name, complexity_tier, counter, updated_at)
    SELECT $1, w.effective_tier, 0, now() FROM winning w
    ON CONFLICT (butler_name, complexity_tier)
    DO UPDATE SET
        counter = public.model_round_robin_counters.counter + 1,
        updated_at = now()
    RETURNING counter
)
SELECT c.runtime_type, c.model_id, c.extra_args, c.id, c.session_timeout_s, c.effective_tier
FROM candidates c, next_counter nc
WHERE c.rn = (nc.counter % c.total)
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
# Returns no rows when no qualifying candidate remains.
_NEXT_SAME_TIER_SQL = """
WITH
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
    (catalog_entry_id, butler_name, session_id, input_tokens, output_tokens)
VALUES ($1, $2, $3, $4, $5)
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
    COALESCE(SUM(tul.input_tokens), 0)  AS input_tokens,
    COALESCE(SUM(tul.output_tokens), 0) AS output_tokens
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
    - ``trigger`` — the dispatch trigger source available at the spawner call site
      (e.g. ``"route"``, ``"qa"``, ``"healing"``, ``"schedule:<task>"``, ``"extraction"``).
      Chosen because it is the richest dispatch-context dimension actually plumbed to
      ``apply_spend_routing_rules`` — no synthetic "feature" tag exists at that call
      site, so the rule never matches on data that isn't really present.  When the
      caller does not supply a trigger source, a ``trigger`` constraint cannot be
      evaluated and the rule does NOT match (fail-closed).

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
    }
    for key, expected in condition.items():
        if key == "trigger" and trigger_source is None:
            # Trigger constraint present but no trigger context to evaluate it against.
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
        selection rotates round-robin via an atomic counter.

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

    # Single query resolves across all candidate tiers, incrementing the counter
    # only for the tier actually used.  Empty tiers never touch their counters.
    row = await pool.fetchrow(_RESOLVE_SQL, butler_name, tiers_to_try)
    if row is None:
        return None

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

    Returns
    -------
    tuple[str, str, list[str], uuid.UUID, int, str] | None
        ``(runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s,
        effective_tier)`` or ``None`` if no enabled entries match.
        ``effective_tier`` is the canonical tier string that produced the candidate
        (may differ from ``complexity_tier`` when tier fallthrough occurred).
    """
    if isinstance(complexity_tier, Complexity):
        tier_value = complexity_tier.value
    else:
        tier_value = _check_deprecated_tier(str(complexity_tier))

    if allow_tier_fallthrough and tier_value in TIER_FALLTHROUGH_ORDER:
        start_idx = TIER_FALLTHROUGH_ORDER.index(tier_value)
        tiers_to_try = list(TIER_FALLTHROUGH_ORDER[start_idx:])
    else:
        tiers_to_try = [tier_value]

    row = await pool.fetchrow(_RESOLVE_SQL, butler_name, tiers_to_try)
    if row is None:
        return None

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
      ``trigger`` (the dispatch ``trigger_source``).  Values may be scalars (exact
      match) or lists (membership).  See ``_rule_condition_matches``.
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
        return SpendRoutingResult(
            resolved=(
                row["runtime_type"],
                row["model_id"],
                _parse_extra_args(row["extra_args"]),
                row["id"],
                row["session_timeout_s"],
            ),
            max_cost_per_call=max_cost_per_call,
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


async def check_monthly_ceiling(
    pool: asyncpg.Pool,
) -> CeilingStatus:
    """Check whether month-to-date spend is within the configured monthly ceiling.

    Reads the singleton ceiling from ``public.spend_ceiling`` (id=1) and estimates
    month-to-date spend by pricing the current-month ``public.token_usage_ledger``
    rows (joined to ``public.model_catalog`` for the priced ``model_id``).  Pricing
    reuses ``butlers.api.pricing.estimate_session_cost`` — the same pathway the
    spawner uses when emitting per-call spend events — via a lazy import to avoid a
    core→api import cycle.

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

        usage_rows = await pool.fetch(_MTD_USAGE_BY_MODEL_SQL)

        # Lazy import to avoid a core→api import cycle (mirrors spawner's
        # per-call spend-event emission).
        from butlers.api.pricing import estimate_session_cost, load_pricing

        pricing = load_pricing()
        mtd_usd = 0.0
        for row in usage_rows:
            mtd_usd += estimate_session_cost(
                pricing,
                row["model_id"] or "unknown",
                int(row["input_tokens"]),
                int(row["output_tokens"]),
            )

        return CeilingStatus(
            allowed=mtd_usd < ceiling_usd,
            mtd_usd=mtd_usd,
            ceiling_usd=ceiling_usd,
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
        Name of the butler that spawned the session (or ``"__discretion__"`` for
        discretion dispatcher calls).
    session_id:
        UUID of the spawner session, or ``None`` for discretion dispatcher calls.
    input_tokens:
        Number of input tokens reported by the adapter.
    output_tokens:
        Number of output tokens reported by the adapter.
    """
    try:
        await pool.execute(
            _LEDGER_INSERT_SQL,
            catalog_entry_id,
            butler_name,
            session_id,
            input_tokens,
            output_tokens,
        )
    except Exception:
        logger.warning(
            "record_token_usage failed for catalog_entry_id=%s butler=%s; "
            "usage not recorded (best-effort)",
            catalog_entry_id,
            butler_name,
            exc_info=True,
        )
