"""Predicate coverage receipts — telling ``absent_proven`` apart from ``unknown``.

An empty fact list is ambiguous: it means either "no source has ever looked for
this predicate on this subject" or "sources looked and there genuinely is
nothing". Callers that cannot separate those two either over-trust silence or
re-ask forever.

``relationship.fact_coverage`` (rel_034) removes the ambiguity by recording, per
``(subject, predicate, src)``, the most recent outcome that source observed when
it looked:

- ``present``     — the source found at least one value.
- ``absent``      — the source looked and there was nothing to find.
- ``unavailable`` — the source could not be consulted at all (auth gone,
  connector down, target expired). Not evidence of absence.

Composition (:func:`compose_state`) folds the live fact rows and the receipts
into exactly one of:

- ``present``       — at least one active fact for the predicate.
- ``absent_proven`` — no active fact AND at least one ``absent`` receipt.
- ``unavailable``   — the subject itself is unavailable, or every receipt says
  the sources could not be consulted.
- ``unknown``       — anything else, and specifically the no-receipt case.

**Missing coverage always means ``unknown``.** There is no configuration, source
allowlist, or freshness heuristic that upgrades "we never looked" into
"it is not there"; only an explicit ``absent`` receipt can do that.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

#: Outcomes a source can record when it looks for a predicate.
COVERAGE_OUTCOMES: frozenset[str] = frozenset({"present", "absent", "unavailable"})

#: Composed states a read can report to a caller.
COVERAGE_STATES: frozenset[str] = frozenset({"present", "absent_proven", "unknown", "unavailable"})


def compose_state(
    *,
    target_available: bool,
    active_value_count: int,
    receipt_outcomes: list[str],
) -> str:
    """Fold target availability, live values, and receipts into one state.

    Pure and total: every input combination maps to exactly one member of
    :data:`COVERAGE_STATES`, so the ambiguity this module exists to remove cannot
    reappear as an unhandled branch.
    """
    if not target_available:
        return "unavailable"
    if active_value_count > 0:
        return "present"
    if not receipt_outcomes:
        # Nobody looked. This is the load-bearing case: silence is never proof.
        return "unknown"
    if "absent" in receipt_outcomes:
        return "absent_proven"
    if all(outcome == "unavailable" for outcome in receipt_outcomes):
        return "unavailable"
    # Receipts claim a value was seen but no active fact remains (retracted or
    # superseded away since the sweep). The receipts are stale, not proof.
    return "unknown"


async def record_coverage(
    conn: asyncpg.Connection,
    *,
    subject: uuid.UUID,
    predicate: str,
    src: str,
    outcome: str,
    observed_at: datetime | None = None,
) -> None:
    """Record what *src* observed for ``(subject, predicate)`` when it looked.

    Upserts the source's latest receipt. An older observation never overwrites a
    newer one, so an out-of-order replay (a backfill sweeping historical data
    after a live sweep already ran) cannot rewind coverage.

    Runs on the caller's connection so a receipt written next to a fact write
    commits or rolls back with it.
    """
    if outcome not in COVERAGE_OUTCOMES:
        raise ValueError(f"coverage outcome must be one of {sorted(COVERAGE_OUTCOMES)}.")
    await conn.execute(
        """
        INSERT INTO relationship.fact_coverage (subject, predicate, src, outcome, observed_at)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (subject, predicate, src) DO UPDATE
        SET outcome     = EXCLUDED.outcome,
            observed_at = EXCLUDED.observed_at,
            recorded_at = now()
        WHERE relationship.fact_coverage.observed_at <= EXCLUDED.observed_at
        """,
        subject,
        predicate,
        src,
        outcome,
        observed_at if observed_at is not None else datetime.now(UTC),
    )


async def _target_available(pool: asyncpg.Pool, subject: uuid.UUID) -> bool:
    """Is *subject* a live entity we can meaningfully answer questions about?

    A missing entity, or one tombstoned by a merge (``metadata->>'merged_into'``
    — the same tombstone test :mod:`relationship_lookup` uses for resolution),
    is unavailable: its predicate reads are not "absent", they are unanswerable.
    """
    row = await pool.fetchrow(
        """
        SELECT (e.metadata->>'merged_into') IS NOT NULL AS merged
        FROM public.entities e
        WHERE e.id = $1
        """,
        subject,
    )
    return row is not None and not row["merged"]


async def predicate_coverage(
    pool: asyncpg.Pool,
    subject: uuid.UUID,
    predicates: list[str],
) -> dict[str, Any]:
    """Report the coverage state of each predicate for *subject*.

    Returns ``{"subject", "target", "coverage": {predicate: {...}}}`` where
    ``target`` is ``available`` or ``unavailable`` and each coverage entry is
    ``{state, value_count, receipts}``. ``receipts`` lists the per-source
    observations backing the state so the caller can see *why* a read is proven
    absent rather than taking the verdict on faith.

    Read-only. Predicates are reported in the order requested, de-duplicated.
    """
    ordered: list[str] = []
    for predicate in predicates:
        if predicate not in ordered:
            ordered.append(predicate)

    available = await _target_available(pool, subject)

    value_counts: dict[str, int] = dict.fromkeys(ordered, 0)
    receipts: dict[str, list[dict[str, Any]]] = {predicate: [] for predicate in ordered}

    if ordered:
        fact_rows = await pool.fetch(
            """
            SELECT predicate, count(*) AS n
            FROM relationship.entity_facts
            WHERE subject = $1
              AND validity = 'active'
              AND predicate = ANY($2::text[])
            GROUP BY predicate
            """,
            subject,
            ordered,
        )
        for row in fact_rows:
            value_counts[row["predicate"]] = int(row["n"])

        receipt_rows = await pool.fetch(
            """
            SELECT predicate, src, outcome, observed_at
            FROM relationship.fact_coverage
            WHERE subject = $1
              AND predicate = ANY($2::text[])
            ORDER BY predicate, observed_at DESC, src
            """,
            subject,
            ordered,
        )
        for row in receipt_rows:
            receipts[row["predicate"]].append(
                {
                    "src": row["src"],
                    "outcome": row["outcome"],
                    "observed_at": row["observed_at"],
                }
            )

    return {
        "subject": str(subject),
        "target": "available" if available else "unavailable",
        "coverage": {
            predicate: {
                "state": compose_state(
                    target_available=available,
                    active_value_count=value_counts[predicate],
                    receipt_outcomes=[r["outcome"] for r in receipts[predicate]],
                ),
                "value_count": value_counts[predicate],
                "receipts": receipts[predicate],
            }
            for predicate in ordered
        },
    }
