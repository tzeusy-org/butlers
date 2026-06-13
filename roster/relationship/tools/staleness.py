"""Read-time staleness derivation for entity facts.

Staleness is a **read-time** computation — it is NEVER stored on a fact row (per
``relationship-facts`` §"Read-time staleness derivation" and
``relationship-entity-lifecycle`` §"Age — read-time staleness derivation").  A
row's stored ``conf``, ``validity``, and timestamps are unchanged by the passage
of time; only its derived ``staleness_band`` reflects age.

Two stores feed entity-knowledge reads, each with its own timestamp fallback
chain (see the lifecycle spec):

- **identity store** (``relationship.entity_facts``):
  ``COALESCE(observed_at, last_seen, created_at)``
- **narrative store** (the memory-module ``facts`` table, which has
  ``last_confirmed_at`` but NO ``last_seen`` column):
  ``COALESCE(observed_at, last_confirmed_at, created_at)``

The age of the resolved timestamp maps to a band:

- ``fresh``  — age ≤ 30 days
- ``aging``  — 30 days < age ≤ 180 days
- ``stale``  — age > 180 days

This module provides both a Python function (:func:`staleness_band`) for
application-code derivation and SQL-expression builders
(:func:`identity_staleness_band_sql`, :func:`narrative_staleness_band_sql`) for
deriving the band directly in a query's SELECT list.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

# ---------------------------------------------------------------------------
# Band thresholds (binding, per relationship-entity-lifecycle §"Age")
# ---------------------------------------------------------------------------

#: Upper bound (inclusive) of the ``fresh`` band, in days.
FRESH_MAX_DAYS = 30
#: Upper bound (inclusive) of the ``aging`` band, in days. Above this is ``stale``.
AGING_MAX_DAYS = 180


class StalenessBand(StrEnum):
    """Read-time staleness classification of a fact row."""

    fresh = "fresh"  # age ≤ 30 days
    aging = "aging"  # 30 days < age ≤ 180 days
    stale = "stale"  # age > 180 days


Store = Literal["identity", "narrative"]


def _band_for_age_days(age_days: float) -> StalenessBand:
    """Map an age in days to a :class:`StalenessBand` using the spec thresholds."""
    if age_days <= FRESH_MAX_DAYS:
        return StalenessBand.fresh
    if age_days <= AGING_MAX_DAYS:
        return StalenessBand.aging
    return StalenessBand.stale


def staleness_band(
    *,
    store: Store,
    observed_at: datetime | None,
    created_at: datetime,
    last_seen: datetime | None = None,
    last_confirmed_at: datetime | None = None,
    now: datetime | None = None,
) -> StalenessBand:
    """Derive the read-time :class:`StalenessBand` for a single fact row.

    The reference timestamp is resolved by the store's fallback chain:

    - ``identity``:  ``COALESCE(observed_at, last_seen, created_at)``
    - ``narrative``: ``COALESCE(observed_at, last_confirmed_at, created_at)``

    ``created_at`` is the final fallback for both stores and is therefore
    required (a fact row always has a creation time).

    Parameters
    ----------
    store:
        ``'identity'`` (``relationship.entity_facts``) or ``'narrative'``
        (memory-module ``facts`` table).
    observed_at:
        When the fact was actually observed (highest-priority signal; nullable).
    created_at:
        Assertion/creation time — the final fallback. Required.
    last_seen:
        Identity-store most-recent-observation timestamp. Ignored for the
        narrative store (which has no ``last_seen`` column).
    last_confirmed_at:
        Narrative-store last-confirmed timestamp. Ignored for the identity store.
    now:
        Reference "current time" for the age computation. Defaults to
        :func:`datetime.now` in UTC. Supplying it makes derivation deterministic
        under a frozen clock.

    Returns
    -------
    StalenessBand
        ``fresh`` (≤30d), ``aging`` (≤180d), or ``stale`` (>180d).

    Raises
    ------
    ValueError
        If ``store`` is not ``'identity'`` or ``'narrative'``.
    """
    if store == "identity":
        reference = observed_at or last_seen or created_at
    elif store == "narrative":
        reference = observed_at or last_confirmed_at or created_at
    else:  # pragma: no cover - guarded by the Literal type at call sites
        raise ValueError(f"Unknown store {store!r}: must be 'identity' or 'narrative'.")

    current = now if now is not None else datetime.now(UTC)
    age_days = (current - reference).total_seconds() / 86_400.0
    return _band_for_age_days(age_days)


# ---------------------------------------------------------------------------
# SQL-expression builders
# ---------------------------------------------------------------------------
#
# These emit a CASE expression that classifies a row's age into the staleness
# band, deriving the reference timestamp via the store's COALESCE chain. They
# take a table alias so callers can splice them into an existing SELECT without
# ambiguous-column errors. The bands MUST match :func:`staleness_band`.


def _band_case_sql(reference_sql: str) -> str:
    """Build the band CASE expression over a reference-timestamp SQL fragment."""
    return (
        "CASE "
        f"WHEN {reference_sql} > now() - INTERVAL '{FRESH_MAX_DAYS} days' THEN 'fresh' "
        f"WHEN {reference_sql} > now() - INTERVAL '{AGING_MAX_DAYS} days' THEN 'aging' "
        "ELSE 'stale' "
        "END"
    )


def identity_staleness_band_sql(alias: str = "f") -> str:
    """SQL CASE expression for the identity store (``relationship.entity_facts``).

    Reference timestamp: ``COALESCE(observed_at, last_seen, created_at)``.

    Parameters
    ----------
    alias:
        Table alias the columns are qualified with (default ``'f'``).

    Returns
    -------
    str
        A SQL expression evaluating to ``'fresh'`` / ``'aging'`` / ``'stale'``.
        Splice it into a SELECT list, e.g.
        ``f"SELECT {identity_staleness_band_sql('f')} AS staleness_band ..."``.
    """
    reference = f"COALESCE({alias}.observed_at, {alias}.last_seen, {alias}.created_at)"
    return _band_case_sql(reference)


def narrative_staleness_band_sql(alias: str = "f") -> str:
    """SQL CASE expression for the narrative store (memory-module ``facts``).

    Reference timestamp: ``COALESCE(observed_at, last_confirmed_at, created_at)``.
    The narrative store has no ``last_seen`` column.

    Parameters
    ----------
    alias:
        Table alias the columns are qualified with (default ``'f'``).

    Returns
    -------
    str
        A SQL expression evaluating to ``'fresh'`` / ``'aging'`` / ``'stale'``.
    """
    reference = f"COALESCE({alias}.observed_at, {alias}.last_confirmed_at, {alias}.created_at)"
    return _band_case_sql(reference)


# ---------------------------------------------------------------------------
# Narrative-store scope predicate (binding, project-wide)
# ---------------------------------------------------------------------------
#
# Every entity-anchored read of the memory-module ``facts`` table (narrative
# store) MUST scope-filter identically, or the same fact set appears in some
# surfaces and vanishes in others. This was bu-3jrq3: the dashboard drill,
# delta banner, and compare blocks hardcoded ``scope = 'relationship'`` while
# the ``relationship_lookup`` MCP tool applied NO scope filter at all. Because
# ``memory_store_fact`` defaults ``scope = 'global'`` and relationship runtime
# guidance routes edge-facts through it, narrative facts routinely land with
# ``scope = 'global'`` — visible via the lookup tool but invisible everywhere
# in the dashboard.
#
# Canonical rule: ``scope IN ('relationship', 'global')``. This mirrors the
# memory module's own search semantics (``src/butlers/modules/memory/search.py``
# filters facts/rules with ``scope IN ('global', <butler_scope>)``): ``global``
# facts are visible to every butler, plus this butler's own scoped facts.
# Foreign butler scopes (e.g. ``'health'``) stay hidden. Reading-side widening
# (this predicate) is the least-invasive fix — it leaves the global-memory
# write model untouched.

#: Scopes whose narrative facts are visible to relationship entity reads.
NARRATIVE_READ_SCOPES: tuple[str, ...] = ("relationship", "global")


def narrative_scope_sql(alias: str = "f") -> str:
    """SQL predicate restricting narrative ``facts`` rows to readable scopes.

    Emits ``<alias>.scope IN ('relationship', 'global')`` — the single
    project-wide scope rule for entity-anchored narrative reads (bu-3jrq3).
    Every narrative read surface (dashboard facts drill, delta banner, compare
    blocks, and the ``relationship_lookup`` MCP tool) MUST use this so the same
    fact set is visible (or hidden) consistently.

    Parameters
    ----------
    alias:
        Table alias the ``scope`` column is qualified with (default ``'f'``).

    Returns
    -------
    str
        A SQL boolean expression, e.g.
        ``"f.scope IN ('relationship', 'global')"``. Splice it into a WHERE
        clause as a standalone conjunct (no parameters — the scope set is a
        fixed literal).
    """
    literals = ", ".join(f"'{scope}'" for scope in NARRATIVE_READ_SCOPES)
    return f"{alias}.scope IN ({literals})"
