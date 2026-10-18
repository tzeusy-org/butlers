"""Read side of the memorial-counterparty claim question (bu-q7vx1q.45).

A loan to or from a person the owner marked ``memorial`` stays a live
``public.cost_claims`` row. This module only *reads* which live, unsettled loan
claims have such a counterparty; it never changes a claim or its resolution.
Deciding whether to retract or keep stays with the owner.
"""

from __future__ import annotations

from typing import Any

import asyncpg

#: Loan claims only: Relationship's loan tools assert these two kinds.
_LOAN_KINDS = ("receivable", "payable")
#: Resolution states in which the claim is no longer open.
_CLOSED_STATES = ("settled", "written_off")


async def list_memorial_open_claims(
    conn: asyncpg.Connection | asyncpg.Pool,
) -> list[dict[str, Any]]:
    """Live, unsettled loan claims whose counterparty entity has posture ``memorial``.

    Raises on any read failure; the caller treats that as unknown and changes nothing.
    """
    rows = await conn.fetch(
        """
        SELECT c.id, c.asserted_by, c.claim_key, c.kind, c.direction,
               e.canonical_name AS counterparty_name
        FROM public.cost_claims c
        JOIN public.entities e ON e.id = c.counterparty_entity_id
        LEFT JOIN public.cost_claim_resolutions r ON r.claim_id = c.id
        WHERE c.superseded_at IS NULL
          AND c.retracted_at IS NULL
          AND c.kind = ANY($1::text[])
          AND COALESCE(r.state, 'unreconciled') <> ALL($2::text[])
          AND e.posture = 'memorial'
        ORDER BY c.asserted_at, c.id
        """,
        list(_LOAN_KINDS),
        list(_CLOSED_STATES),
    )
    return [dict(row) for row in rows]
