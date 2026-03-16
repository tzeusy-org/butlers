"""Dynamic model routing — catalog-based model selection with per-butler overrides.

Provides:
- ``Complexity`` enum (trivial / medium / high / extra_high / discretion)
- ``resolve_model(pool, butler_name, complexity_tier)`` — single-query resolution
  that respects per-butler overrides and falls back to global catalog entries.

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
7. Return the first row as (runtime_type, model_id, extra_args), or None if
   no matching entries exist.
"""

from __future__ import annotations

import enum
import json

import asyncpg


class Complexity(enum.StrEnum):
    """Task complexity tiers used for model selection."""

    TRIVIAL = "trivial"
    MEDIUM = "medium"
    HIGH = "high"
    EXTRA_HIGH = "extra_high"
    DISCRETION = "discretion"


# SQL that performs the full resolution in a single round-trip.
# Uses a LEFT JOIN so global-only entries (no override row) are still returned.
# COALESCE applies the per-butler override for enabled/priority/complexity_tier
# when a matching override row exists, otherwise falls back to the catalog value.
_RESOLVE_SQL = """
SELECT
    mc.runtime_type,
    mc.model_id,
    mc.extra_args
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


async def resolve_model(
    pool: asyncpg.Pool,
    butler_name: str,
    complexity_tier: Complexity | str,
) -> tuple[str, str, list[str]] | None:
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
        (``"trivial"``, ``"medium"``, ``"high"``, ``"extra_high"``, ``"discretion"``).

    Returns
    -------
    tuple[str, str, list[str]] | None
        ``(runtime_type, model_id, extra_args)`` for the highest-priority
        matching entry, or ``None`` if no enabled entries match.
        ``extra_args`` is a list of CLI token strings (e.g. ``["--config", "k=v"]``).
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

    return (row["runtime_type"], row["model_id"], extra_args)
