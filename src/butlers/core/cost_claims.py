"""Typed shared cost-claim ledger operations.

The database, not the caller, derives assertion and resolution authority from
the active runtime role. The asserting butler's own store remains canonical;
this ledger is a cross-butler projection for deterministic Finance matching.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg

CLAIM_COLUMNS = """
    c.id, c.claim_key, c.asserted_by, c.asserted_by_role, c.kind, c.direction,
    c.amount, c.currency, c.counterparty_entity_id, c.counterparty_label,
    c.expected_on, c.description, c.evidence_kind, c.evidence_ref,
    c.asserted_at, c.superseded_at, c.retracted_at, c.retraction_reason
"""


def _claim_args(
    *,
    claim_key: str,
    asserted_by: str,
    kind: str,
    direction: str,
    amount: Decimal,
    currency: str,
    counterparty_entity_id: uuid.UUID | None,
    counterparty_label: str | None,
    expected_on: date | None,
    description: str,
    evidence_kind: str | None,
    evidence_ref: str | None,
) -> tuple[Any, ...]:
    normalized_currency = currency.strip().upper()
    if len(normalized_currency) != 3 or not normalized_currency.isalpha():
        raise ValueError("currency must be an ISO-4217 uppercase three-letter code")
    normalized_amount = Decimal(amount).quantize(Decimal("0.01"))
    if normalized_amount <= 0:
        raise ValueError("amount must be greater than zero")
    return (
        claim_key,
        asserted_by,
        kind,
        direction,
        normalized_amount,
        normalized_currency,
        counterparty_entity_id,
        counterparty_label,
        expected_on,
        description,
        evidence_kind,
        evidence_ref,
    )


async def assert_claim(pool: asyncpg.Pool, **values: Any) -> dict[str, Any]:
    """Assert a claim, returning the live row for an identical concurrent retry."""
    args = _claim_args(**values)
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO public.cost_claims
                        (claim_key, asserted_by, kind, direction, amount, currency,
                         counterparty_entity_id, counterparty_label, expected_on,
                         description, evidence_kind, evidence_ref)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    RETURNING {CLAIM_COLUMNS.replace("c.", "")}
                    """,
                    *args,
                )
        except asyncpg.UniqueViolationError:
            row = await conn.fetchrow(
                f"SELECT {CLAIM_COLUMNS} FROM public.cost_claims c "
                "WHERE c.asserted_by = $1 AND c.claim_key = $2 "
                "AND c.superseded_at IS NULL AND c.retracted_at IS NULL",
                values["asserted_by"],
                values["claim_key"],
            )
            if row is None:
                raise
            comparable = _claim_args(**values)[2:]
            stored = (
                row["kind"],
                row["direction"],
                row["amount"],
                row["currency"],
                row["counterparty_entity_id"],
                row["counterparty_label"],
                row["expected_on"],
                row["description"],
                row["evidence_kind"],
                row["evidence_ref"],
            )
            if stored != comparable:
                async with conn.transaction():
                    locked = await conn.fetchrow(
                        "SELECT id FROM public.cost_claims WHERE id = $1 "
                        "AND superseded_at IS NULL AND retracted_at IS NULL FOR UPDATE",
                        row["id"],
                    )
                    if locked is None:
                        raise ValueError("live claim changed during amendment") from None
                    await conn.execute(
                        "UPDATE public.cost_claims SET superseded_at = now() WHERE id = $1",
                        row["id"],
                    )
                    row = await conn.fetchrow(
                        f"""
                        INSERT INTO public.cost_claims
                            (claim_key, asserted_by, kind, direction, amount, currency,
                             counterparty_entity_id, counterparty_label, expected_on,
                             description, evidence_kind, evidence_ref)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                        RETURNING {CLAIM_COLUMNS.replace("c.", "")}
                        """,
                        *args,
                    )
    assert row is not None
    return dict(row)


async def amend_claim(pool: asyncpg.Pool, claim_id: uuid.UUID, **values: Any) -> dict[str, Any]:
    """Supersede one owned live claim and restart reconciliation on its successor."""
    args = _claim_args(**values)
    async with pool.acquire() as conn, conn.transaction():
        old = await conn.fetchrow(
            "SELECT asserted_by FROM public.cost_claims WHERE id = $1 "
            "AND superseded_at IS NULL AND retracted_at IS NULL FOR UPDATE",
            claim_id,
        )
        if old is None:
            raise ValueError(f"Live cost claim {claim_id} not found")
        if old["asserted_by"] != values["asserted_by"]:
            raise ValueError("asserted_by cannot change when amending a claim")
        await conn.execute(
            "UPDATE public.cost_claims SET superseded_at = now() WHERE id = $1", claim_id
        )
        row = await conn.fetchrow(
            f"""
            INSERT INTO public.cost_claims
                (claim_key, asserted_by, kind, direction, amount, currency,
                 counterparty_entity_id, counterparty_label, expected_on,
                 description, evidence_kind, evidence_ref)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING {CLAIM_COLUMNS.replace("c.", "")}
            """,
            *args,
        )
    return dict(row)


async def retract_claim(pool: asyncpg.Pool, claim_id: uuid.UUID, reason: str) -> dict[str, Any]:
    """Retract one owned live claim without deleting its last Finance verdict."""
    if not reason.strip():
        raise ValueError("retraction reason is required")
    row = await pool.fetchrow(
        f"UPDATE public.cost_claims c SET retracted_at = now(), retraction_reason = $2 "
        f"WHERE c.id = $1 AND c.superseded_at IS NULL AND c.retracted_at IS NULL "
        f"RETURNING {CLAIM_COLUMNS}",
        claim_id,
        reason.strip(),
    )
    if row is None:
        raise ValueError(f"Live cost claim {claim_id} not found")
    return dict(row)


async def list_claims(
    pool: asyncpg.Pool, *, asserted_by: str | None = None
) -> list[dict[str, Any]]:
    where = "WHERE c.asserted_by = $1" if asserted_by else ""
    args = (asserted_by,) if asserted_by else ()
    rows = await pool.fetch(
        f"""
        SELECT {CLAIM_COLUMNS}, r.state, r.matched_amount, r.matched_currency,
               r.match_refs, r.unmatched_reason, r.unverifiable_reason,
               r.evidence_horizon_at, r.decided_at, r.decided_by
        FROM public.cost_claims c
        LEFT JOIN public.cost_claim_resolutions r ON r.claim_id = c.id
        {where}
        ORDER BY c.asserted_at DESC, c.id DESC
        """,
        *args,
    )
    return [dict(row) for row in rows]


async def resolve_claim(
    pool: asyncpg.Pool,
    claim_id: uuid.UUID,
    *,
    state: str,
    matched_amount: Decimal | None = None,
    matched_currency: str | None = None,
    match_refs: list[str] | None = None,
    unmatched_reason: str | None = None,
    unverifiable_reason: str | None = None,
    evidence_horizon_at: Any = None,
) -> dict[str, Any]:
    """Write a Finance-owned resolution. RLS rejects every non-Finance caller."""
    row = await pool.fetchrow(
        """
        INSERT INTO public.cost_claim_resolutions
            (claim_id, state, matched_amount, matched_currency, match_refs,
             unmatched_reason, unverifiable_reason, evidence_horizon_at, decided_at, decided_by)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, now(), current_user)
        ON CONFLICT (claim_id) DO UPDATE SET
            state = EXCLUDED.state, matched_amount = EXCLUDED.matched_amount,
            matched_currency = EXCLUDED.matched_currency, match_refs = EXCLUDED.match_refs,
            unmatched_reason = EXCLUDED.unmatched_reason,
            unverifiable_reason = EXCLUDED.unverifiable_reason,
            evidence_horizon_at = EXCLUDED.evidence_horizon_at,
            decided_at = EXCLUDED.decided_at, decided_by = EXCLUDED.decided_by
        RETURNING *
        """,
        claim_id,
        state,
        matched_amount,
        matched_currency,
        json.dumps(match_refs or []),
        unmatched_reason,
        unverifiable_reason,
        evidence_horizon_at,
    )
    return dict(row)


__all__ = ["amend_claim", "assert_claim", "list_claims", "resolve_claim", "retract_claim"]
