"""Deterministic reconciliation of shared cost claims against Finance evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.tools.finance.reconciliation import _amount_compatible, _payee_match

_LOCK_NAME = "finance:cost-claim-reconciliation"
_LOOKBACK_DAYS = 45
_GRACE_DAYS = 7
_FRESH_HOURS = 24


def _refs(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row["id"]) for row in rows]


async def _write_resolution(
    conn: asyncpg.Connection,
    claim_id: Any,
    *,
    state: str,
    matches: list[dict[str, Any]] | None = None,
    unmatched_reason: str | None = None,
    unverifiable_reason: str | None = None,
    evidence_horizon_at: datetime | None = None,
    matched_amount: Decimal | None = None,
    matched_currency: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO public.cost_claim_resolutions
            (claim_id, state, matched_amount, matched_currency, match_refs,
             unmatched_reason, unverifiable_reason, evidence_horizon_at,
             decided_at, decided_by)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, now(), current_user)
        ON CONFLICT (claim_id) DO UPDATE SET
            state = EXCLUDED.state, matched_amount = EXCLUDED.matched_amount,
            matched_currency = EXCLUDED.matched_currency, match_refs = EXCLUDED.match_refs,
            unmatched_reason = EXCLUDED.unmatched_reason,
            unverifiable_reason = EXCLUDED.unverifiable_reason,
            evidence_horizon_at = EXCLUDED.evidence_horizon_at,
            decided_at = EXCLUDED.decided_at, decided_by = EXCLUDED.decided_by
        """,
        claim_id,
        state,
        matched_amount,
        matched_currency,
        json.dumps(_refs(matches or [])),
        unmatched_reason,
        unverifiable_reason,
        evidence_horizon_at,
    )


async def reconcile_cost_claims(pool: asyncpg.Pool) -> dict[str, Any]:
    """Sweep active claims once; a concurrent caller exits without writes."""
    async with pool.acquire() as conn:
        acquired = await conn.fetchval(
            "SELECT pg_try_advisory_lock(hashtextextended($1, 0))", _LOCK_NAME
        )
        if not acquired:
            return {"acquired": False, "processed": 0, "outcomes": {}}
        try:
            outcomes: dict[str, int] = {}
            async with conn.transaction():
                # Bindings describe only the current sweep's evidence. Rebuild
                # them atomically so changed evidence cannot leave a stale
                # transaction reserved while another claim is evaluated.
                await conn.execute("DELETE FROM claim_match_bindings")
                claims = await conn.fetch(
                    """
                    SELECT c.*,
                           COALESCE(NULLIF(btrim(c.counterparty_label), ''), e.canonical_name)
                               AS effective_counterparty_label
                    FROM public.cost_claims c
                    LEFT JOIN public.entities e ON e.id = c.counterparty_entity_id
                    WHERE c.superseded_at IS NULL AND c.retracted_at IS NULL
                    ORDER BY c.asserted_at, c.id
                    """
                )
                for record in claims:
                    claim = dict(record)
                    outcome = await _reconcile_one(conn, claim)
                    outcomes[outcome] = outcomes.get(outcome, 0) + 1
            return {"acquired": True, "processed": len(claims), "outcomes": outcomes}
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", _LOCK_NAME)


async def _reconcile_one(conn: asyncpg.Connection, claim: dict[str, Any]) -> str:
    currency = claim["currency"]
    accounts = [
        dict(row)
        for row in await conn.fetch(
            "SELECT id, last_synced_at FROM accounts WHERE is_active = true AND currency = $1",
            currency,
        )
    ]
    horizon = await conn.fetchval(
        "SELECT max(posted_at) FROM transactions WHERE deleted_at IS NULL AND currency = $1",
        currency,
    )
    if not accounts:
        await _write_resolution(
            conn,
            claim["id"],
            state="unverifiable",
            unverifiable_reason="no_account",
            evidence_horizon_at=horizon,
        )
        return "unverifiable"
    synced = [row["last_synced_at"] for row in accounts if row["last_synced_at"] is not None]
    if not synced:
        await _write_resolution(
            conn,
            claim["id"],
            state="unverifiable",
            unverifiable_reason="never_synced",
            evidence_horizon_at=horizon,
        )
        return "unverifiable"
    now = datetime.now(UTC)
    if max(synced) < now - timedelta(hours=_FRESH_HOURS):
        await _write_resolution(
            conn,
            claim["id"],
            state="unverifiable",
            unverifiable_reason="feed_stale",
            evidence_horizon_at=horizon,
        )
        return "unverifiable"

    anchor = claim["expected_on"] or claim["asserted_at"].date()
    start = anchor - timedelta(days=_LOOKBACK_DAYS)
    end = anchor + timedelta(days=_GRACE_DAYS + 1)
    transaction_direction = "credit" if claim["direction"] == "inbound" else "debit"
    rows = [
        dict(row)
        for row in await conn.fetch(
            """
            SELECT id, amount, currency, merchant, posted_at
            FROM transactions
            WHERE deleted_at IS NULL AND direction = $1 AND category <> 'transfer'
              AND posted_at >= $2::date AND posted_at < $3::date
            ORDER BY posted_at, id
            """,
            transaction_direction,
            start,
            end,
        )
    ]
    label = claim["effective_counterparty_label"] or ""
    plausible = []
    for row in rows:
        payee_match, _ = _payee_match(label, row["merchant"])
        amount_match, _ = _amount_compatible(Decimal(claim["amount"]), abs(Decimal(row["amount"])))
        if payee_match and amount_match:
            plausible.append(row)
    same_currency = [row for row in plausible if row["currency"] == currency]
    if any(row["currency"] != currency for row in plausible):
        await _write_resolution(
            conn,
            claim["id"],
            state="ambiguous",
            matches=plausible,
            unmatched_reason="currency_mismatch",
            evidence_horizon_at=horizon,
        )
        return "ambiguous"
    if not same_currency:
        await _write_resolution(
            conn,
            claim["id"],
            state="unreconciled",
            unmatched_reason="no_candidate_in_window",
            evidence_horizon_at=horizon,
        )
        return "unreconciled"
    if len(same_currency) > 1:
        await _write_resolution(
            conn,
            claim["id"],
            state="ambiguous",
            matches=same_currency,
            unmatched_reason="multiple_candidates",
            evidence_horizon_at=horizon,
        )
        return "ambiguous"

    transaction = same_currency[0]
    existing_claim = await conn.fetchval(
        "SELECT claim_id FROM claim_match_bindings WHERE transaction_id = $1",
        transaction["id"],
    )
    if existing_claim is not None and existing_claim != claim["id"]:
        await _write_resolution(
            conn,
            claim["id"],
            state="ambiguous",
            matches=same_currency,
            unmatched_reason="transaction_already_bound",
            evidence_horizon_at=horizon,
        )
        return "ambiguous"
    await conn.execute(
        "INSERT INTO claim_match_bindings (claim_id, transaction_id) VALUES ($1, $2) "
        "ON CONFLICT (claim_id) DO UPDATE SET "
        "transaction_id = EXCLUDED.transaction_id, bound_at = now()",
        claim["id"],
        transaction["id"],
    )
    matched_amount = abs(Decimal(transaction["amount"]))
    state = "settled" if matched_amount >= Decimal(claim["amount"]) else "partially_settled"
    await _write_resolution(
        conn,
        claim["id"],
        state=state,
        matches=same_currency,
        matched_amount=matched_amount,
        matched_currency=currency,
        evidence_horizon_at=horizon,
    )
    return state


__all__ = ["reconcile_cost_claims"]
