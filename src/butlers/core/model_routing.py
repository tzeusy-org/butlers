"""Dynamic model routing — catalog-based model selection with per-butler overrides.

Provides:
- ``Complexity`` enum (canonical six: reasoning / workhorse / cheap / specialty / local / legacy)
- ``resolve_model(pool, butler_name, complexity_tier)`` — highest-priority enabled model
  in tier whose state ∈ {verified, untested}; falls through canonical tier order if none
  qualify in the requested tier.
- ``QuotaStatus`` dataclass — result of a pre-spawn token quota check.
- ``check_token_quota(pool, catalog_entry_id)`` — CTE-based single-query quota check.
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
