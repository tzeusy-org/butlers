"""Deterministic bill↔payment reconciliation for the finance butler.

Design: openspec/changes/finance-bill-payment-reconciliation/design.md
Epic:   bu-hpmgo / bead bu-fo2uv (Track B)

Fixes a real owner incident: a paid HSBC bill (SGD 717.57) stayed pending $0.00
because settlement was LLM-only and broke across sessions.  This module provides
a pure SQL/Python matcher and sweep — no LLM in the daemon path (Rule 4).

Key constants (spec-defined):
    LOOKBACK_DAYS = 45  — bills are often paid well before the printed due date
    GRACE_DAYS    = 7   — allow a short window past due date
    Tolerance     = max($1.00, 1% of bill amount)

Invocation surfaces:
    1. reconcile_bills()  — batch sweep, registered as an MCP tool (Track B4)
    2. match_transaction_to_bills()  — single-transaction matcher (used by Track C)
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.tools.finance.bills import _mirror_bill_to_spo

# --- Spec constants -------------------------------------------------------
LOOKBACK_DAYS = 45  # days before anchor that a payment can arrive early
GRACE_DAYS = 7  # days after anchor that a payment can arrive late
_AMOUNT_TOLERANCE_FLOOR = Decimal("1.00")  # minimum tolerance floor


# ---------------------------------------------------------------------------
# Payee normalization
# ---------------------------------------------------------------------------


def _normalize_payee(name: str) -> str:
    """Normalize a payee/merchant name for case-insensitive comparison.

    Steps:
    1. Lowercase and strip
    2. Remove common corporate legal-suffix noise (Ltd, Inc, Corp, …)
    3. Replace non-alphanumeric characters with spaces
    4. Collapse runs of whitespace

    Domain words ("credit", "card", "bank") are preserved so that
    "HSBC Credit Card" and "DBS Credit Card" remain distinguishable.
    """
    s = name.lower().strip()
    # Strip trailing punctuation on corporate suffixes (e.g. "Ltd.")
    s = re.sub(
        r"\b(ltd|inc|corp|llc|plc|pte|pvt|sdn|bhd)\b\.?",
        "",
        s,
    )
    # Replace punctuation/special chars with space
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _payee_match(
    bill_payee: str,
    txn_merchant: str,
    txn_normalized_merchant: str | None = None,
) -> tuple[bool, bool]:
    """Match bill payee against a transaction merchant name.

    Uses the normalized forms; also checks the pre-normalized merchant string
    from ``T.metadata->>'normalized_merchant'`` when available.

    Returns
    -------
    (is_match, is_exact)
        is_exact:  normalized strings are equal (drives auto_settle tier)
        is_match:  one set of tokens is a whole-token subset of the other
                   (drives confirm tier)
    """
    norm_bill = _normalize_payee(bill_payee)
    norm_txn = _normalize_payee(txn_merchant)
    norm_alt = _normalize_payee(txn_normalized_merchant) if txn_normalized_merchant else norm_txn

    # Exact match
    if norm_bill == norm_txn or norm_bill == norm_alt:
        return True, True

    # Whole-token subset match (spec: "one contains the other as a whole-token substring")
    bill_tokens = set(norm_bill.split()) - {""}
    txn_tokens = set(norm_txn.split()) - {""}
    alt_tokens = set(norm_alt.split()) - {""}

    if bill_tokens:
        if bill_tokens <= txn_tokens or txn_tokens <= bill_tokens:
            return True, False
        if alt_tokens and (bill_tokens <= alt_tokens or alt_tokens <= bill_tokens):
            return True, False

    # Raw string containment as a conservative fallback
    if norm_bill and (norm_bill in norm_txn or norm_txn in norm_bill):
        return True, False
    if norm_alt and norm_bill and (norm_bill in norm_alt or norm_alt in norm_bill):
        return True, False

    return False, False


# ---------------------------------------------------------------------------
# Amount compatibility
# ---------------------------------------------------------------------------


def _amount_compatible(
    bill_amount: Decimal,
    txn_abs_amount: Decimal,
) -> tuple[bool, bool]:
    """Check whether a transaction amount is compatible with a bill amount.

    Returns
    -------
    (is_compatible, is_placeholder)
        is_placeholder: bill_amount == 0 (unknown; compatible with any txn)
        is_compatible:  txn amount within max($1.00, 1%) of bill amount
    """
    if bill_amount == Decimal("0.00"):
        return True, True

    tolerance = max(_AMOUNT_TOLERANCE_FLOOR, bill_amount * Decimal("0.01"))
    return abs(txn_abs_amount - bill_amount) <= tolerance, False


# ---------------------------------------------------------------------------
# Single-transaction matcher
# ---------------------------------------------------------------------------


async def match_transaction_to_bills(
    pool: asyncpg.Pool,
    txn: dict[str, Any],
    *,
    bills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Match a single debit transaction against open (pending/overdue) bills.

    Pure SQL/Python — no LLM.  Used by the post-``record_transaction`` hook
    (Track C) for real-time inline settlement, and by ``reconcile_bills`` for
    the batch sweep (N+1-free path via the ``bills`` parameter).

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    txn:
        Transaction dict as returned by ``record_transaction``.  Required keys:
        ``id``, ``direction``, ``merchant``, ``currency``, ``amount`` (absolute),
        ``posted_at`` (ISO string or datetime).  Optional: ``metadata`` dict with
        ``normalized_merchant``.
    bills:
        Optional pre-fetched list of pending/overdue bill dicts.  When provided,
        candidate filtering by currency is performed in-memory and no DB query
        is issued for bills — eliminating the per-transaction DB round-trip in
        the ``reconcile_bills`` sweep.  The caller is responsible for excluding
        already-settled bills from this list (e.g. by filtering on
        ``settled_bill_ids``).  When ``None`` (standalone / Track C path), bills
        are fetched from the DB with conservative date bounds derived from the
        transaction date, and an "already used" guard query is also run.

    Returns
    -------
    dict
        ``tier``       : ``'auto_settle'`` | ``'confirm'`` | ``'none'``
        ``bill``       : matched BillRecord (only for ``auto_settle`` tier)
        ``candidates`` : list of candidate BillRecords (for ``confirm`` tier)
    """
    # Only debit transactions settle bills (spec: "credits/refunds never settle a bill")
    if txn.get("direction") != "debit":
        return {"tier": "none", "bill": None, "candidates": []}

    txn_merchant = txn.get("merchant", "")
    txn_currency = txn.get("currency", "")
    txn_amount = abs(Decimal(str(txn.get("amount", 0))))

    # Parse posted_at
    txn_posted_at = txn.get("posted_at")
    if txn_posted_at is None:
        return {"tier": "none", "bill": None, "candidates": []}
    if isinstance(txn_posted_at, str):
        from datetime import datetime

        txn_posted_at = datetime.fromisoformat(txn_posted_at)

    # UTC date of transaction (spec: "truncation timezone is UTC, fixed")
    try:
        txn_date = txn_posted_at.astimezone(UTC).date()
    except (AttributeError, TypeError):
        txn_date = txn_posted_at.date()  # type: ignore[union-attr]

    # Extract optional pre-normalized merchant from metadata
    txn_normalized: str | None = None
    meta = txn.get("metadata")
    if isinstance(meta, dict):
        txn_normalized = meta.get("normalized_merchant")

    if bills is None:
        # Standalone path (Track C inline hook): guard against a txn that already
        # reconciled another bill, then fetch candidate bills from the DB.
        txn_id_raw = txn.get("id")
        txn_uuid = uuid.UUID(str(txn_id_raw)) if isinstance(txn_id_raw, str) else txn_id_raw
        already_used = await pool.fetchrow(
            "SELECT id FROM bills WHERE reconciled_transaction_id = $1 LIMIT 1",
            txn_uuid,
        )
        if already_used is not None:
            return {"tier": "none", "bill": None, "candidates": []}

        # Conservative date bounds derived from spec window constants:
        #   anchor - LOOKBACK_DAYS <= txn_date  →  anchor <= txn_date + LOOKBACK_DAYS
        #   txn_date <= anchor + GRACE_DAYS      →  anchor >= txn_date - GRACE_DAYS
        # where anchor = COALESCE(statement_period_end, due_date).
        date_lo = txn_date - timedelta(days=GRACE_DAYS)
        date_hi = txn_date + timedelta(days=LOOKBACK_DAYS)

        rows = await pool.fetch(
            """
            SELECT * FROM bills
            WHERE status IN ('pending', 'overdue')
              AND reconciled_transaction_id IS NULL
              AND currency = $1
              AND COALESCE(statement_period_end, due_date) >= $2
              AND COALESCE(statement_period_end, due_date) <= $3
            ORDER BY due_date ASC
            """,
            txn_currency,
            date_lo,
            date_hi,
        )
        bill_candidates: list[dict[str, Any]] = [dict(row) for row in rows]
    else:
        # Batch path (reconcile_bills sweep): filter pre-fetched bills by currency
        # in-memory.  The caller has already excluded settled bills from this list.
        bill_candidates = [b for b in bills if b.get("currency") == txn_currency]

    in_window: list[dict[str, Any]] = []
    for bill in bill_candidates:
        # Anchor: statement_period_end if set, else due_date
        anchor = bill["statement_period_end"] if bill["statement_period_end"] else bill["due_date"]
        window_start = anchor - timedelta(days=LOOKBACK_DAYS)
        window_end = anchor + timedelta(days=GRACE_DAYS)

        if not (window_start <= txn_date <= window_end):
            continue

        # Payee match
        is_match, is_exact = _payee_match(bill["payee"], txn_merchant, txn_normalized)
        if not is_match:
            continue

        # Amount compatibility
        bill_amount = Decimal(str(bill["amount"]))
        is_compatible, is_placeholder = _amount_compatible(bill_amount, txn_amount)
        if not is_compatible:
            continue

        in_window.append(
            {
                "bill_row": bill,
                "is_exact_payee": is_exact,
                "is_placeholder": is_placeholder,
                "anchor": anchor,
            }
        )

    if not in_window:
        return {"tier": "none", "bill": None, "candidates": []}

    # Confidence classification (spec: design.md "Confidence classification" table)
    if len(in_window) == 1:
        c = in_window[0]
        if c["is_exact_payee"]:
            # Single candidate + exact payee match → auto_settle
            return {
                "tier": "auto_settle",
                "bill": c["bill_row"],
                "candidates": [c["bill_row"]],
            }
        # Single candidate but fuzzy payee → confirm
        return {
            "tier": "confirm",
            "bill": None,
            "candidates": [c["bill_row"]],
        }

    # Multiple in-window candidates → always confirm (auto_settle never fires)
    return {
        "tier": "confirm",
        "bill": None,
        "candidates": [c["bill_row"] for c in in_window],
    }


# ---------------------------------------------------------------------------
# Guarded settlement UPDATE
# ---------------------------------------------------------------------------


async def _settle_bill(
    pool: asyncpg.Pool,
    bill_id: uuid.UUID | str | Any,
    txn: dict[str, Any],
) -> bool:
    """Apply the guarded settlement UPDATE to a single bill.

    The WHERE clause (``status <> 'paid' AND reconciled_transaction_id IS NULL``)
    makes settlement idempotent at the database level: two near-simultaneous
    callers cannot both win.  Callers MUST check the return value.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    bill_id:
        UUID of the bill to settle.
    txn:
        Dict with keys: ``id``, ``amount`` (absolute), ``posted_at``,
        ``payment_method`` (optional).

    Returns
    -------
    bool
        True if this call settled the bill; False if already settled by another
        caller (zero rows updated — not an error).
    """
    bill_uuid = uuid.UUID(str(bill_id)) if not isinstance(bill_id, uuid.UUID) else bill_id

    txn_id_raw = txn.get("id")
    txn_uuid = uuid.UUID(str(txn_id_raw)) if not isinstance(txn_id_raw, uuid.UUID) else txn_id_raw

    txn_abs_amount = abs(Decimal(str(txn.get("amount", 0))))

    txn_posted_at = txn.get("posted_at")
    if isinstance(txn_posted_at, str):
        from datetime import datetime

        txn_posted_at = datetime.fromisoformat(txn_posted_at)

    txn_payment_method = txn.get("payment_method")

    result = await pool.execute(
        """
        UPDATE bills SET
            status                    = 'paid',
            amount                    = CASE WHEN amount = 0 THEN $3 ELSE amount END,
            paid_at                   = $4,
            payment_method            = COALESCE($5, payment_method),
            reconciled_transaction_id = $2,
            metadata                  = metadata || jsonb_build_object(
                                          'reconciled_at', now()::text,
                                          'reconciliation', 'auto'
                                        ),
            updated_at                = now()
        WHERE id = $1
          AND status <> 'paid'
          AND reconciled_transaction_id IS NULL
        """,
        bill_uuid,
        txn_uuid,
        txn_abs_amount,
        txn_posted_at,
        txn_payment_method,
    )
    # asyncpg returns "UPDATE N" as a string
    rows_updated = int(result.split()[-1]) if result else 0
    return rows_updated > 0


# ---------------------------------------------------------------------------
# B3 — reconcile_bills sweep
# ---------------------------------------------------------------------------


async def reconcile_bills(
    pool: asyncpg.Pool,
    lookback_days: int = 90,
    payee: str | None = None,
) -> dict[str, Any]:
    """Deterministic bill→transaction reconciliation sweep.

    Iterates unlinked debit transactions in the trailing ``lookback_days``
    window and, for each, applies ``match_transaction_to_bills`` to find
    candidate bills.  This approach catches the
    **payment-recorded-before-bill-existed** case: when a debit was recorded
    before the matching bill row was created, the inline hook missed it, but
    the sweep finds both.

    Matches are classified by the same confidence tiers as
    ``match_transaction_to_bills``:

    * ``auto_settle`` — single in-window candidate + exact payee match.
      Settled immediately using the guarded UPDATE.
    * ``confirm`` — multiple candidates or fuzzy payee.  Surfaced for human /
      LLM confirmation; nothing is mutated.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    lookback_days:
        Outer scan horizon — only consider debits posted in the past this many
        days.  Default: 90.  The per-bill LOOKBACK/GRACE window applies inside.
    payee:
        Optional exact payee filter — restricts results to bills whose payee
        equals this value (case-sensitive).  Applied as a predicate in the bill
        pre-fetch query, so the DB returns only matching-payee bills.

    Returns
    -------
    dict
        ``auto_settled``: ``[{bill_id, payee, amount, paid_at, txn_id}, ...]``
        ``candidates``  : ``[{bill_id, payee, due_date, amount,
                             candidates: [{txn_id, merchant, amount, posted_at}]}, ...]``

    Notes
    -----
    - Idempotent: already-paid bills and already-linked transactions are skipped
      by the guarded UPDATE and the ``reconciled_transaction_id IS NULL`` filter
      inside ``match_transaction_to_bills``.
    - Two concurrent calls cannot double-settle: the guarded UPDATE enforces
      atomicity at the database level.
    - Log the returned counts for observability.
    """
    from datetime import datetime

    horizon_cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

    # Pre-fetch all pending/overdue bills once to avoid N+1 queries.
    # match_transaction_to_bills receives an active-bills slice each iteration
    # (already-settled bills removed) and filters by currency in-memory.
    #
    # The optional ``payee`` filter is pushed down into this query so the DB only
    # returns bills for the requested payee — at scale we no longer fetch every
    # open bill just to discard non-matching ones in Python.  The predicate uses
    # exact equality to mirror the prior in-memory ``bill["payee"] != payee``
    # check (case-sensitive), so results are identical to before.
    if payee is not None:
        all_bills_rows = await pool.fetch(
            """
            SELECT * FROM bills
            WHERE status IN ('pending', 'overdue')
              AND reconciled_transaction_id IS NULL
              AND payee = $1
            ORDER BY due_date ASC
            """,
            payee,
        )
    else:
        all_bills_rows = await pool.fetch(
            """
            SELECT * FROM bills
            WHERE status IN ('pending', 'overdue')
              AND reconciled_transaction_id IS NULL
            ORDER BY due_date ASC
            """,
        )
    all_bills: list[dict[str, Any]] = [dict(row) for row in all_bills_rows]

    # Fetch all unlinked debit transactions in the outer lookback window.
    # These are the candidate payment events we want to match against bills.
    txn_rows = await pool.fetch(
        """
        SELECT t.* FROM transactions t
        WHERE t.direction = 'debit'
          AND t.posted_at >= $1
          AND t.deleted_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM bills b
              WHERE b.reconciled_transaction_id = t.id
          )
        ORDER BY t.posted_at DESC
        """,
        horizon_cutoff,
    )

    auto_settled: list[dict[str, Any]] = []
    confirm_list: list[dict[str, Any]] = []
    # Track bill IDs already settled in this run to prevent double-settlement
    # when multiple txns match the same bill across iterations.
    settled_bill_ids: set[Any] = set()

    for txn_row in txn_rows:
        # Build the txn dict expected by match_transaction_to_bills
        txn_dict: dict[str, Any] = {
            "id": txn_row["id"],
            "direction": txn_row["direction"],
            "merchant": txn_row["merchant"],
            "currency": txn_row["currency"],
            "amount": abs(Decimal(str(txn_row["amount"]))),
            "posted_at": txn_row["posted_at"],
            "metadata": txn_row.get("metadata"),
        }

        # Pass only bills not yet settled in this run so the matcher never
        # sees a bill that was claimed by an earlier txn in the same sweep.
        active_bills = [b for b in all_bills if b["id"] not in settled_bill_ids]
        match = await match_transaction_to_bills(pool, txn_dict, bills=active_bills)

        tier = match["tier"]

        if tier == "auto_settle":
            bill = match["bill"]
            bill_id = bill["id"]

            # No in-memory payee filter needed: the ``payee`` predicate is applied
            # in the pre-fetch query above, so ``match`` can only reference a bill
            # whose payee already matches.

            # Guard against settling the same bill twice in one sweep
            if bill_id in settled_bill_ids:
                continue

            settle_txn: dict[str, Any] = {
                "id": txn_row["id"],
                "amount": abs(Decimal(str(txn_row["amount"]))),
                "posted_at": txn_row["posted_at"],
                "payment_method": txn_row.get("payment_method"),
            }
            settled = await _settle_bill(pool, bill_id, settle_txn)
            if settled:
                settled_bill_ids.add(bill_id)
                auto_settled.append(
                    {
                        "bill_id": str(bill_id),
                        "payee": bill["payee"],
                        "amount": str(Decimal(str(txn_row["amount"]))),
                        "paid_at": txn_row["posted_at"].isoformat()
                        if txn_row["posted_at"]
                        else None,
                        "txn_id": str(txn_row["id"]),
                    }
                )
                # Mirror the settled state to public.facts immediately.
                # Amount post-settlement: txn amount backfills a $0 placeholder;
                # otherwise the original bill amount is unchanged.
                # Mirror the settled state to public.facts.
                # Amount post-settlement: txn amount backfills a $0 placeholder;
                # otherwise the original bill amount is unchanged.
                # Awaited directly — pool.fetch() above materialized all rows
                # into Python lists before the loop, so no connection is held
                # open here and there is no re-entrancy risk.
                bill_amount = Decimal(str(bill["amount"]))
                settled_amount = (
                    float(settle_txn["amount"]) if bill_amount == 0 else float(bill_amount)
                )
                await _mirror_bill_to_spo(
                    pool=pool,
                    payee=bill["payee"],
                    amount=settled_amount,
                    currency=bill["currency"],
                    due_date=bill["due_date"],
                    frequency=bill["frequency"],
                    status="paid",
                    payment_method=(settle_txn.get("payment_method") or bill.get("payment_method")),
                    account_id=str(bill["account_id"]) if bill.get("account_id") else None,
                    paid_at=txn_row["posted_at"],
                    reconciled_transaction_id=txn_row["id"],
                    source_message_id=bill.get("source_message_id"),
                )

        elif tier == "confirm":
            candidates = match["candidates"]

            # No in-memory payee filter needed: the ``payee`` predicate is applied
            # in the pre-fetch query above, so every candidate bill already matches.
            if not candidates:
                continue

            # Surface the first (bill,) — each is reported individually so
            # the LLM can decide per-bill.  Deduplicate by bill_id.
            for candidate_bill in candidates:
                bill_id = candidate_bill["id"]
                if bill_id in settled_bill_ids:
                    continue
                # Check if already in confirm_list for this bill
                if any(c["bill_id"] == str(bill_id) for c in confirm_list):
                    continue
                confirm_list.append(
                    {
                        "bill_id": str(bill_id),
                        "payee": candidate_bill["payee"],
                        "due_date": candidate_bill["due_date"].isoformat()
                        if hasattr(candidate_bill["due_date"], "isoformat")
                        else str(candidate_bill["due_date"]),
                        "amount": str(Decimal(str(candidate_bill["amount"]))),
                        "candidates": [
                            {
                                "txn_id": str(txn_row["id"]),
                                "merchant": txn_row["merchant"],
                                "amount": str(Decimal(str(txn_row["amount"]))),
                                "posted_at": txn_row["posted_at"].isoformat()
                                if txn_row["posted_at"]
                                else None,
                            }
                        ],
                    }
                )

    return {
        "auto_settled": auto_settled,
        "candidates": confirm_list,
    }
