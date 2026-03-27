"""Finance backfill — migrate existing SPO transaction facts into finance.transactions.

Phase 2 backfill: reads all active transaction facts (predicate in
('transaction_debit', 'transaction_credit'), scope='finance') from the
public.facts table and inserts them into finance.transactions using defensive
JSONB extraction and NOT EXISTS deduplication.

Key behaviors:
- Defensive extraction: malformed amounts, missing required fields are logged
  and the row is skipped (not a hard error).
- Deduplication: uses NOT EXISTS against existing finance.transactions rows,
  checking the same three-tier key hierarchy used by record_transaction():
    1. source_message_id + merchant + amount + posted_at (email-extracted)
    2. external_ref + account_id (bank API with stable IDs)
    3. exact posted_at + merchant + amount + currency composite (CSV fallback)
- Error reporting: returns a BackfillResult with inserted, skipped, and a list
  of SkippedRow records (fact_id, reason, raw_metadata snippet).
- Idempotent: safe to run multiple times; already-migrated rows are skipped.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PREDICATE_TRANSACTION_DEBIT = "transaction_debit"
_PREDICATE_TRANSACTION_CREDIT = "transaction_credit"
_TRANSACTION_PREDICATES = [_PREDICATE_TRANSACTION_DEBIT, _PREDICATE_TRANSACTION_CREDIT]

_REQUIRED_METADATA_FIELDS = ("merchant", "amount", "currency", "category")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SkippedRow:
    """Record describing a fact that could not be migrated."""

    fact_id: str
    reason: str
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackfillResult:
    """Summary of a backfill run."""

    inserted: int = 0
    skipped: int = 0
    skipped_rows: list[SkippedRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "skipped": self.skipped,
            "skipped_rows": [
                {
                    "fact_id": r.fact_id,
                    "reason": r.reason,
                    "raw_metadata": r.raw_metadata,
                }
                for r in self.skipped_rows
            ],
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_metadata(raw: Any) -> dict[str, Any]:
    """Parse JSONB metadata from asyncpg (may arrive as str or dict)."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _extract_amount(meta: dict[str, Any]) -> Decimal | None:
    """Extract and validate the amount field from metadata.

    Returns None when the field is missing or not a valid decimal.
    """
    raw = meta.get("amount")
    if raw is None:
        return None
    try:
        d = Decimal(str(raw))
        if d < 0:
            # Amounts are stored as absolute values in finance.transactions
            d = abs(d)
        return d
    except (InvalidOperation, TypeError, ValueError):
        return None


def _extract_posted_at(fact_row: Any) -> datetime | None:
    """Extract posted_at from the fact row's valid_at column.

    Falls back to created_at when valid_at is NULL.
    """
    val = fact_row.get("valid_at") or fact_row.get("created_at")
    if val is None:
        return None
    if isinstance(val, datetime):
        # Ensure timezone-aware
        if val.tzinfo is None:
            return val.replace(tzinfo=UTC)
        return val
    # Handle string timestamps (e.g. in some test scenarios)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return None
    return None


def _extract_direction(meta: dict[str, Any], predicate: str) -> str:
    """Resolve direction from metadata or predicate name."""
    direction = meta.get("direction")
    if direction in ("debit", "credit"):
        return direction
    # Fall back to predicate name
    if predicate == _PREDICATE_TRANSACTION_DEBIT:
        return "debit"
    return "credit"


def _check_existing_transaction_sql() -> str:
    """Return the NOT EXISTS condition used for deduplication.

    Checks the three-tier dedup hierarchy in priority order:
    1. source_message_id + merchant + amount + posted_at
    2. external_ref + account_id (external_ref must be non-null)
    3. posted_at + merchant + amount + currency composite
    """
    return """
        NOT EXISTS (
            SELECT 1 FROM transactions t
            WHERE (
                -- Priority 1: email-extracted source_message_id dedup
                (
                    $1::text IS NOT NULL
                    AND t.source_message_id = $1
                    AND lower(t.merchant) = lower($2)
                    AND t.amount = $3
                    AND t.posted_at = $4
                )
                OR
                -- Priority 2: bank API external_ref dedup
                (
                    $5::text IS NOT NULL
                    AND $6::uuid IS NOT NULL
                    AND t.external_ref = $5
                    AND t.account_id = $6::uuid
                )
                OR
                -- Priority 3: composite fallback dedup
                (
                    $1::text IS NULL
                    AND $5::text IS NULL
                    AND t.posted_at = $4
                    AND lower(t.merchant) = lower($2)
                    AND t.amount = $3
                    AND t.currency = $7
                )
            )
        )
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def backfill_spo_transactions(
    pool: asyncpg.Pool,
    *,
    batch_size: int = 500,
    scope: str = "finance",
) -> BackfillResult:
    """Backfill existing SPO transaction facts into finance.transactions.

    Reads all active transaction facts (predicate in
    ('transaction_debit', 'transaction_credit')) from the facts table and
    inserts them into transactions using defensive JSONB extraction and
    NOT EXISTS deduplication.

    Parameters
    ----------
    pool:
        asyncpg connection pool pointing to the finance butler schema.
    batch_size:
        Number of facts to process per batch (default 500).
    scope:
        Facts scope filter (default 'finance').

    Returns
    -------
    BackfillResult
        Summary with inserted count, skipped count, and per-row skip reasons.
    """
    result = BackfillResult()

    # Check that the facts table is accessible.
    try:
        facts_count = await pool.fetchval(
            """
            SELECT COUNT(*)
            FROM facts
            WHERE predicate = ANY($1::text[])
              AND validity = 'active'
              AND scope = $2
            """,
            _TRANSACTION_PREDICATES,
            scope,
        )
    except asyncpg.PostgresError as exc:
        logger.warning("backfill_spo_transactions: cannot read facts table: %s", exc)
        return result

    if facts_count == 0:
        logger.info("backfill_spo_transactions: no SPO transaction facts found; nothing to do")
        return result

    logger.info("backfill_spo_transactions: found %d SPO transaction facts to process", facts_count)

    # Check that the transactions table exists.
    try:
        await pool.fetchval("SELECT 1 FROM transactions LIMIT 1")
    except asyncpg.PostgresError as exc:
        logger.warning("backfill_spo_transactions: cannot read transactions table: %s", exc)
        return result

    offset = 0
    while True:
        rows = await pool.fetch(
            """
            SELECT id, predicate, valid_at, created_at, metadata
            FROM facts
            WHERE predicate = ANY($1::text[])
              AND validity = 'active'
              AND scope = $2
            ORDER BY valid_at ASC, id ASC
            LIMIT $3 OFFSET $4
            """,
            _TRANSACTION_PREDICATES,
            scope,
            batch_size,
            offset,
        )

        if not rows:
            break

        for row in rows:
            fact_id = str(row["id"])
            meta = _parse_metadata(row["metadata"])

            # --- Required field validation ---
            merchant = meta.get("merchant")
            if not merchant or not isinstance(merchant, str):
                reason = "missing or invalid 'merchant' field"
                logger.debug("backfill: skipping fact %s — %s", fact_id, reason)
                result.skipped += 1
                result.skipped_rows.append(
                    SkippedRow(fact_id=fact_id, reason=reason, raw_metadata=meta)
                )
                continue

            amount = _extract_amount(meta)
            if amount is None:
                raw_amount = meta.get("amount")
                reason = f"invalid or missing 'amount' field: {raw_amount!r}"
                logger.debug("backfill: skipping fact %s — %s", fact_id, reason)
                result.skipped += 1
                result.skipped_rows.append(
                    SkippedRow(fact_id=fact_id, reason=reason, raw_metadata=meta)
                )
                continue

            currency = meta.get("currency")
            if not currency or not isinstance(currency, str):
                reason = "missing or invalid 'currency' field"
                logger.debug("backfill: skipping fact %s — %s", fact_id, reason)
                result.skipped += 1
                result.skipped_rows.append(
                    SkippedRow(fact_id=fact_id, reason=reason, raw_metadata=meta)
                )
                continue

            category = meta.get("category")
            if not category or not isinstance(category, str):
                reason = "missing or invalid 'category' field"
                logger.debug("backfill: skipping fact %s — %s", fact_id, reason)
                result.skipped += 1
                result.skipped_rows.append(
                    SkippedRow(fact_id=fact_id, reason=reason, raw_metadata=meta)
                )
                continue

            posted_at = _extract_posted_at(dict(row))
            if posted_at is None:
                reason = "cannot determine posted_at (valid_at and created_at are NULL)"
                logger.debug("backfill: skipping fact %s — %s", fact_id, reason)
                result.skipped += 1
                result.skipped_rows.append(
                    SkippedRow(fact_id=fact_id, reason=reason, raw_metadata=meta)
                )
                continue

            direction = _extract_direction(meta, row["predicate"])
            source_message_id: str | None = meta.get("source_message_id") or None
            external_ref: str | None = meta.get("external_ref") or None
            account_id: str | None = meta.get("account_id") or None
            description: str | None = meta.get("description") or None
            payment_method: str | None = meta.get("payment_method") or None
            receipt_url: str | None = meta.get("receipt_url") or None

            # Build extra metadata: preserve any non-standard fields from the
            # fact metadata for storage in finance.transactions.metadata.
            _standard_fields = {
                "merchant",
                "amount",
                "currency",
                "category",
                "direction",
                "description",
                "payment_method",
                "account_id",
                "receipt_url",
                "external_ref",
                "source_message_id",
            }
            extra_meta = {k: v for k, v in meta.items() if k not in _standard_fields}
            # Tag the row as backfilled for provenance tracking.
            extra_meta["backfilled_from_fact_id"] = fact_id

            # --- INSERT with NOT EXISTS deduplication ---
            try:
                row_inserted = await pool.fetchval(
                    f"""
                    INSERT INTO transactions (
                        source_message_id,
                        posted_at,
                        merchant,
                        description,
                        amount,
                        currency,
                        direction,
                        category,
                        payment_method,
                        account_id,
                        receipt_url,
                        external_ref,
                        metadata
                    )
                    SELECT
                        $1, $4, $2, $8, $3, $7, $9, $10, $11, $6::uuid, $12, $5, $13::jsonb
                    WHERE {_check_existing_transaction_sql()}
                    RETURNING id
                    """,
                    source_message_id,  # $1
                    merchant,  # $2
                    amount,  # $3
                    posted_at,  # $4
                    external_ref,  # $5
                    account_id,  # $6
                    currency.upper(),  # $7
                    description,  # $8
                    direction,  # $9
                    category,  # $10
                    payment_method,  # $11
                    receipt_url,  # $12
                    json.dumps(extra_meta),  # $13
                )
                if row_inserted is not None:
                    result.inserted += 1
                    logger.debug(
                        "backfill: inserted transaction %s from fact %s",
                        row_inserted,
                        fact_id,
                    )
                else:
                    # NOT EXISTS check failed — row already exists
                    result.skipped += 1
                    result.skipped_rows.append(
                        SkippedRow(
                            fact_id=fact_id,
                            reason="duplicate: matching row already in finance.transactions",
                            raw_metadata=meta,
                        )
                    )

            except asyncpg.PostgresError as exc:
                reason = f"insert error: {exc}"
                logger.warning("backfill: error inserting fact %s — %s", fact_id, reason)
                result.skipped += 1
                result.skipped_rows.append(
                    SkippedRow(fact_id=fact_id, reason=reason, raw_metadata=meta)
                )

        offset += len(rows)
        if len(rows) < batch_size:
            break

    logger.info(
        "backfill_spo_transactions: complete — inserted=%d skipped=%d",
        result.inserted,
        result.skipped,
    )
    return result
