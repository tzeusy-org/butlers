"""Dynamic model routing — catalog-based model selection with per-butler overrides.

Provides:
- ``Complexity`` enum (trivial / medium / high / extra_high / discretion)
- ``resolve_model(pool, butler_name, complexity_tier)`` — single-query resolution
  that respects per-butler overrides and falls back to global catalog entries.
- ``QuotaStatus`` dataclass — result of a pre-spawn token quota check.
- ``check_token_quota(pool, catalog_entry_id)`` — CTE-based single-query quota check.
- ``record_token_usage(pool, ...)`` — best-effort ledger INSERT.

Resolution strategy
-------------------
For a given ``butler_name`` and ``complexity_tier``:

1. Join ``shared.model_catalog mc`` with ``shared.butler_model_overrides bmo``
   on ``bmo.butler_name = $butler_name AND bmo.catalog_entry_id = mc.id``.
2. Effective enabled:  ``COALESCE(bmo.enabled, mc.enabled)``
3. Effective priority: ``COALESCE(bmo.priority, mc.priority)``
4. Effective tier:     ``COALESCE(bmo.complexity_tier, mc.complexity_tier)``
5. Filter: effective enabled = true AND effective tier = $complexity_tier.
6. Order by effective priority DESC, then mc.created_at ASC (stable tie-break).
7. Return the first row as (runtime_type, model_id, extra_args, catalog_entry_id),
   or None if no matching entries exist.
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
    """Task complexity tiers used for model selection."""

    TRIVIAL = "trivial"
    MEDIUM = "medium"
    HIGH = "high"
    EXTRA_HIGH = "extra_high"
    DISCRETION = "discretion"
    SELF_HEALING = "self_healing"


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


# SQL that performs the full resolution in a single round-trip.
# Uses a LEFT JOIN so global-only entries (no override row) are still returned.
# COALESCE applies the per-butler override for enabled/priority/complexity_tier
# when a matching override row exists, otherwise falls back to the catalog value.
_RESOLVE_SQL = """
SELECT
    mc.runtime_type,
    mc.model_id,
    mc.extra_args,
    mc.id
FROM shared.model_catalog mc
LEFT JOIN shared.butler_model_overrides bmo
    ON bmo.catalog_entry_id = mc.id
    AND bmo.butler_name = $1
WHERE
    COALESCE(bmo.enabled, mc.enabled) = true
    AND COALESCE(bmo.complexity_tier, mc.complexity_tier) = $2
ORDER BY
    COALESCE(bmo.priority, mc.priority) DESC,
    mc.created_at ASC
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
    FROM shared.token_limits
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
    FROM shared.token_usage_ledger
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
SELECT 1 FROM shared.token_limits WHERE catalog_entry_id = $1 LIMIT 1
"""

_LEDGER_INSERT_SQL = """
INSERT INTO shared.token_usage_ledger
    (catalog_entry_id, butler_name, session_id, input_tokens, output_tokens)
VALUES ($1, $2, $3, $4, $5)
"""


async def resolve_model(
    pool: asyncpg.Pool,
    butler_name: str,
    complexity_tier: Complexity | str,
) -> tuple[str, str, list[str], uuid.UUID] | None:
    """Resolve the best model for a butler and complexity tier.

    Queries ``shared.model_catalog`` with an optional ``shared.butler_model_overrides``
    LEFT JOIN.  Per-butler overrides can remap enabled state, priority, and
    complexity tier without duplicating the catalog row.

    Parameters
    ----------
    pool:
        An asyncpg connection pool connected to the butlers database.
    butler_name:
        The butler identity name (e.g. ``"general"``).  Used to look up any
        per-butler overrides; if none exist the global catalog is used directly.
    complexity_tier:
        A ``Complexity`` enum value or its string equivalent
        (``"trivial"``, ``"medium"``, ``"high"``, ``"extra_high"``, ``"discretion"``,
        ``"self_healing"``).

    Returns
    -------
    tuple[str, str, list[str], uuid.UUID] | None
        ``(runtime_type, model_id, extra_args, catalog_entry_id)`` for the
        highest-priority matching entry, or ``None`` if no enabled entries match.
        ``extra_args`` is a list of CLI token strings (e.g. ``["--config", "k=v"]``).
        ``catalog_entry_id`` is the UUID primary key of the matched catalog row.
    """
    if isinstance(complexity_tier, Complexity):
        tier_value = complexity_tier.value
    else:
        tier_value = str(complexity_tier)

    row = await pool.fetchrow(_RESOLVE_SQL, butler_name, tier_value)
    if row is None:
        return None

    # asyncpg returns JSONB columns as strings; parse them explicitly.
    raw_extra = row["extra_args"]
    if raw_extra is None:
        extra_args: list[str] = []
    elif isinstance(raw_extra, str):
        parsed = json.loads(raw_extra)
        extra_args = parsed if isinstance(parsed, list) else []
    elif isinstance(raw_extra, list):
        extra_args = raw_extra
    else:
        extra_args = []

    return (row["runtime_type"], row["model_id"], extra_args, row["id"])


async def check_token_quota(
    pool: asyncpg.Pool,
    catalog_entry_id: uuid.UUID,
) -> QuotaStatus:
    """Check whether a catalog entry's token usage is within its configured limits.

    Uses a CTE-based single round-trip query that computes both 24h and 30d
    window usages, respecting independent reset markers.

    Fast path: if no ``shared.token_limits`` row exists for the entry, returns
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
        UUID of the ``shared.model_catalog`` row to check.

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
            # No limits row found (CTE returned empty); treat as unlimited.
            return _unlimited

        limit_24h: int | None = row["limit_24h"]
        limit_30d: int | None = row["limit_30d"]
        used_24h: int = int(row["used_24h"])
        used_30d: int = int(row["used_30d"])

        allowed = True
        if limit_24h is not None and used_24h >= limit_24h:
            allowed = False
        if limit_30d is not None and used_30d >= limit_30d:
            allowed = False

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
    """Record token usage to ``shared.token_usage_ledger``.

    Best-effort: errors are logged as warnings and never propagate to the caller.
    A ledger write failure must never block a session result from being returned.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    catalog_entry_id:
        UUID of the resolved ``shared.model_catalog`` row.
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
