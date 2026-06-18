"""Insights delivery-state read-model v1 — versioned read boundary for the insights pipeline.

Centralises the SQL projection and single-pool query function for the
``/api/system/insights/delivery-state`` endpoint, which aggregates delivery
counts from ``public.insight_candidates`` in the switchboard schema.

A breaking schema change (new column, renamed status value, type change) should
produce a new ``insights_v2`` module rather than silently altering this one.

Public surface
--------------
Column constant:
    INSIGHT_DELIVERY_COLUMNS

Row DTO:
    InsightDeliveryStateRow

Query function (async):
    query_insight_delivery_state(pool) -> InsightDeliveryStateRow | None
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------

#: Stability contract — bump to ``insights_v2`` for breaking changes.
READ_MODEL_VERSION = "insights_v1"

# ---------------------------------------------------------------------------
# Column projections / SQL projection constant (v1 schema contract)
# ---------------------------------------------------------------------------

#: Aggregate projection from ``public.insight_candidates``.
#: This is a computed projection (COUNT … FILTER) rather than a plain column list,
#: but it is versioned here so the exact SQL contract is explicit and testable.
#: Changing this expression is a breaking change — create ``insights_v2`` instead.
INSIGHT_DELIVERY_COLUMNS: str = (
    "COUNT(*) FILTER (WHERE status = 'pending') AS queued, "
    "COUNT(*) FILTER (WHERE status = 'delivered') AS delivered, "
    "COUNT(*) FILTER ("
    "WHERE status = 'filtered' AND delivery_attempt_count >= 3"
    ") AS failed, "
    "MAX(delivered_at) FILTER (WHERE status = 'delivered') AS last_delivery_at"
)

# ---------------------------------------------------------------------------
# Typed row DTO
# ---------------------------------------------------------------------------


@dataclass
class InsightDeliveryStateRow:
    """Typed DTO for the aggregate delivery-state query result (v1).

    All counts are non-negative integers.  ``last_delivery_at`` is ``None``
    when no candidates have been delivered yet.
    """

    queued: int
    delivered: int
    failed: int
    last_delivery_at: datetime | None


# ---------------------------------------------------------------------------
# Row converter
# ---------------------------------------------------------------------------


def row_to_delivery_state(row: asyncpg.Record) -> InsightDeliveryStateRow:
    """Convert an asyncpg aggregate Record to an :class:`InsightDeliveryStateRow`.

    This is the single place that knows the column aliases from
    :data:`INSIGHT_DELIVERY_COLUMNS`.
    """
    return InsightDeliveryStateRow(
        queued=int(row["queued"] or 0),
        delivered=int(row["delivered"] or 0),
        failed=int(row["failed"] or 0),
        last_delivery_at=row["last_delivery_at"],
    )


# ---------------------------------------------------------------------------
# Query function
# ---------------------------------------------------------------------------


async def query_insight_delivery_state(
    pool: asyncpg.Pool,
) -> InsightDeliveryStateRow | None:
    """Fetch the aggregated insight delivery state from the switchboard pool.

    Queries ``public.insight_candidates`` for pending / delivered / failed
    counts and the most recent delivery timestamp.

    This function performs **no error handling** — the caller is responsible for
    catching exceptions and returning a degraded zero-count response as needed.
    This separation keeps the read-model testable in isolation and the exception
    handling logic explicit in the router.

    Parameters
    ----------
    pool:
        The asyncpg pool for the switchboard butler (which owns ``public.insight_candidates``).

    Returns
    -------
    InsightDeliveryStateRow | None
        Typed delivery-state DTO, or ``None`` if the aggregate query returns no row
        (should not happen for a bare aggregate with no GROUP BY, but guarded for safety).
    """
    row = await pool.fetchrow(f"SELECT {INSIGHT_DELIVERY_COLUMNS} FROM public.insight_candidates")

    if row is None:
        return None

    return row_to_delivery_state(row)
