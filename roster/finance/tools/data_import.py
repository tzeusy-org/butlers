"""Finance butler — historical bank CSV import pipeline.

Supports Chase, American Express (Amex), Capital One, and generic CSV formats.
Auto-detects format by inspecting column headers, normalizes dates and amounts,
deduplicates against existing transactions, and processes in batches of 500.

Public API:
    import_transactions(pool, blob_store, storage_ref, account_id, currency, column_map, dry_run)
    import_transactions_from_file(pool, file_path, account_id, currency, column_map, dry_run)

Internal helpers are prefixed with ``_`` and are not part of the public contract.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Batch size for DB inserts; large enough for throughput, small enough to avoid
# parameter-count limits and memory pressure on 1000+ row files.
_BATCH_SIZE = 500

# Canonical column names in the internal normalised representation.
_COL_DATE = "date"
_COL_MERCHANT = "merchant"
_COL_AMOUNT = "amount"
_COL_DESCRIPTION = "description"
_COL_CATEGORY = "category"
_COL_DIRECTION = "direction"

# Placeholder category assigned to rows whose CSV carries no category value.
# Treated as "no category data" when deciding whether to learn merchant->category
# mappings post-import.
_UNCATEGORIZED = "uncategorized"

# ---------------------------------------------------------------------------
# Format definitions
# ---------------------------------------------------------------------------

# Each format entry has:
#   name        — human-readable label returned to callers
#   header_cols — frozenset of lowercase header names that MUST appear (case-insensitive)
#   col_map     — mapping of canonical_name → csv_column_name (case-insensitive match)
#   amount_col  — which canonical column(s) to read the amount from
#   debit_sign  — "negative" | "positive" | "split" (split = separate debit/credit cols)

_FORMAT_CHASE = {
    "name": "chase",
    # Chase checking/savings: Transaction Date, Description, Amount, Balance
    # Chase credit:           Transaction Date, Post Date, Description, Category, Type, Amount, Memo
    "required_cols": frozenset({"transaction date", "description", "amount"}),
    "col_map": {
        _COL_DATE: "transaction date",
        _COL_MERCHANT: "description",
        _COL_AMOUNT: "amount",
        _COL_DESCRIPTION: "memo",
        _COL_CATEGORY: "category",
    },
    # Chase encodes debits as negative amounts.
    "amount_sign": "negative_debit",
}

_FORMAT_AMEX = {
    "name": "amex",
    # Amex CSV: Date, Description, Card Member, Account #, Amount
    "required_cols": frozenset({"date", "description", "amount", "card member"}),
    "col_map": {
        _COL_DATE: "date",
        _COL_MERCHANT: "description",
        _COL_AMOUNT: "amount",
        _COL_DESCRIPTION: "description",
        _COL_CATEGORY: "category",
    },
    # Amex encodes charges (debits) as POSITIVE and payments (credits) as NEGATIVE.
    "amount_sign": "positive_debit",
}

_FORMAT_CAPITAL_ONE = {
    "name": "capital_one",
    # Capital One: Transaction Date, Posted Date, Card No., Description, Category, Debit, Credit
    "required_cols": frozenset({"transaction date", "posted date", "card no.", "description"}),
    "col_map": {
        _COL_DATE: "transaction date",
        _COL_MERCHANT: "description",
        _COL_AMOUNT: None,  # computed from debit/credit split columns
        _COL_DESCRIPTION: "description",
        _COL_CATEGORY: "category",
    },
    # Capital One uses separate Debit / Credit columns.
    "amount_sign": "split_cols",
    "debit_col": "debit",
    "credit_col": "credit",
}

_FORMAT_GENERIC = {
    "name": "generic",
    # Fallback: requires at least date + (amount or (debit and credit)) + some merchant column.
    "required_cols": frozenset(),  # detection is by exclusion
    "col_map": {
        _COL_DATE: None,  # resolved dynamically
        _COL_MERCHANT: None,
        _COL_AMOUNT: None,
    },
    "amount_sign": "negative_debit",
}

_KNOWN_FORMATS = [_FORMAT_CHASE, _FORMAT_AMEX, _FORMAT_CAPITAL_ONE]

# ---------------------------------------------------------------------------
# Date format detection
# ---------------------------------------------------------------------------

_DATE_PATTERNS: list[tuple[str, str]] = [
    # Pattern             strptime format
    (r"^\d{4}-\d{2}-\d{2}$", "%Y-%m-%d"),  # ISO: 2024-01-15
    (r"^\d{2}/\d{2}/\d{4}$", "%m/%d/%Y"),  # US: 01/15/2024
    (r"^\d{2}-\d{2}-\d{4}$", "%m-%d-%Y"),  # US dash: 01-15-2024
    (r"^\d{1,2}/\d{1,2}/\d{4}$", "%m/%d/%Y"),  # US short: 1/5/2024
    (r"^\d{4}/\d{2}/\d{2}$", "%Y/%m/%d"),  # ISO slash: 2024/01/15
    (r"^\d{2}/\d{2}/\d{2}$", "%m/%d/%y"),  # 2-digit year: 01/15/24
    (r"^\d{2}-\d{2}-\d{2}$", "%m-%d-%y"),  # 2-digit dash: 01-15-24
    # Day-month-year with abbreviated month name (Amex format)
    (r"^\d{1,2} [A-Za-z]{3} \d{4}$", "%d %b %Y"),  # Amex: 15 Jan 2024
    (r"^\d{1,2}-[A-Za-z]{3}-\d{4}$", "%d-%b-%Y"),  # 15-Jan-2024
]


def _detect_date_format(sample_dates: list[str]) -> str | None:
    """Return the strptime format string that matches all provided sample dates.

    Tries each known pattern against every sample; returns the first format
    that successfully parses all non-empty samples.  Returns None when no
    format matches or when no non-empty samples are provided.
    """
    candidates = [fmt for _, fmt in _DATE_PATTERNS]
    active = list(candidates)
    has_data = False

    for raw in sample_dates:
        if not raw or not raw.strip():
            continue
        has_data = True
        val = raw.strip()
        still_valid = []
        for fmt in active:
            try:
                datetime.strptime(val, fmt)
                still_valid.append(fmt)
            except ValueError:
                pass
        active = still_valid
        if not active:
            return None

    if not has_data:
        return None

    return active[0] if active else None


def _parse_date(raw: str, fmt: str) -> datetime:
    """Parse *raw* using *fmt* and return a UTC-aware datetime at midnight."""
    dt = datetime.strptime(raw.strip(), fmt)
    return dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Amount normalisation
# ---------------------------------------------------------------------------

# Strip common non-numeric characters from amount strings before parsing.
_AMOUNT_STRIP_RE = re.compile(r"[\$,\s€£¥]")


def _parse_amount(raw: str) -> Decimal:
    """Parse a raw amount string into a Decimal.

    Handles currency symbols, commas, spaces, and parenthetical negatives.

    Raises:
        ValueError: If the string cannot be parsed as a valid number.
    """
    if not raw or not raw.strip():
        raise ValueError(f"Empty amount: {raw!r}")
    s = raw.strip()
    # Parenthetical negatives: (1,234.56) → -1234.56
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    s = _AMOUNT_STRIP_RE.sub("", s)
    try:
        return Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"Cannot parse amount {raw!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Merchant name normalisation
# ---------------------------------------------------------------------------

# Patterns to strip from raw merchant strings (e.g. trailing transaction IDs).
# Note: do NOT use re.VERBOSE here — '#' is a comment char in verbose mode.
_MERCHANT_CLEANUP_RE = re.compile(r"(\s+\d{4,}$|\s*#\d+$|\s+\d{2}/\d{2}$|\b[A-Z]{2}\s+\d{5}\b)")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_merchant(raw: str) -> str:
    """Normalize a raw merchant string.

    - Strips leading/trailing whitespace.
    - Collapses internal whitespace runs.
    - Removes common transaction-ID / location suffixes appended by banks.
    - Title-cases the result for readability.
    """
    s = raw.strip()
    s = _MERCHANT_CLEANUP_RE.sub("", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s.title()


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def _normalise_headers(headers: list[str]) -> list[str]:
    """Return headers as lowercase stripped strings."""
    return [h.lower().strip() for h in headers]


def detect_format(headers: list[str]) -> dict[str, Any]:
    """Identify the bank CSV format from column headers.

    Returns one of the known format dicts (_FORMAT_CHASE, _FORMAT_AMEX,
    _FORMAT_CAPITAL_ONE) or _FORMAT_GENERIC as the fallback.

    Parameters
    ----------
    headers:
        Raw column header strings from the CSV file.
    """
    norm = frozenset(_normalise_headers(headers))
    for fmt in _KNOWN_FORMATS:
        if fmt["required_cols"].issubset(norm):
            return fmt
    return _FORMAT_GENERIC


# ---------------------------------------------------------------------------
# Generic format column resolution
# ---------------------------------------------------------------------------

# Candidate column names for each canonical field (checked in order).
_GENERIC_DATE_CANDIDATES = [
    "date",
    "transaction date",
    "posted date",
    "trans date",
    "posting date",
    "transaction_date",
    "posted_date",
]
_GENERIC_MERCHANT_CANDIDATES = [
    "description",
    "merchant",
    "payee",
    "vendor",
    "name",
    "narrative",
    "transaction description",
]
_GENERIC_AMOUNT_CANDIDATES = [
    "amount",
    "transaction amount",
    "trans amount",
    "charge",
    "value",
    "transaction_amount",
]
_GENERIC_DEBIT_CANDIDATES = ["debit", "debit amount", "withdrawal", "withdrawals"]
_GENERIC_CREDIT_CANDIDATES = ["credit", "credit amount", "deposit", "deposits"]


def _resolve_generic_cols(headers: list[str]) -> dict[str, str | None]:
    """Resolve canonical → actual column name for generic CSV.

    Returns a dict with keys matching canonical column names, values are the
    matched header (or None if not found).
    """
    norm_map = {h.lower().strip(): h for h in headers}
    norm_set = frozenset(norm_map)

    def _first_match(candidates: list[str]) -> str | None:
        for c in candidates:
            if c in norm_set:
                return norm_map[c]
        return None

    date_col = _first_match(_GENERIC_DATE_CANDIDATES)
    merchant_col = _first_match(_GENERIC_MERCHANT_CANDIDATES)
    amount_col = _first_match(_GENERIC_AMOUNT_CANDIDATES)
    debit_col = _first_match(_GENERIC_DEBIT_CANDIDATES)
    credit_col = _first_match(_GENERIC_CREDIT_CANDIDATES)

    has_split = debit_col is not None and credit_col is not None
    return {
        _COL_DATE: date_col,
        _COL_MERCHANT: merchant_col,
        _COL_AMOUNT: amount_col if not has_split else None,
        "debit_col": debit_col,
        "credit_col": credit_col,
        "use_split": has_split,
    }


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------


def _resolve_col(row: dict[str, str], col_name: str | None) -> str | None:
    """Case-insensitive column lookup in *row*."""
    if col_name is None:
        return None
    col_lower = col_name.lower().strip()
    for k, v in row.items():
        if k.lower().strip() == col_lower:
            return v
    return None


def _parse_row(
    row: dict[str, str],
    fmt: dict[str, Any],
    date_fmt: str,
    resolved_cols: dict[str, str | None] | None,
    default_currency: str,
) -> dict[str, Any] | None:
    """Parse a single CSV row into a normalised transaction dict.

    Returns None when the row should be skipped (e.g. header re-occurrence,
    running balance row, or unparseable data).

    Raises ValueError for rows that have data but cannot be parsed cleanly.
    """
    # Resolve column names for generic format
    if resolved_cols is not None:
        date_col = resolved_cols.get(_COL_DATE)
        merchant_col = resolved_cols.get(_COL_MERCHANT)
        amount_col = resolved_cols.get(_COL_AMOUNT)
        debit_col = resolved_cols.get("debit_col")
        credit_col = resolved_cols.get("credit_col")
        use_split = resolved_cols.get("use_split", False)
    else:
        date_col = fmt["col_map"].get(_COL_DATE)
        merchant_col = fmt["col_map"].get(_COL_MERCHANT)
        amount_col = fmt["col_map"].get(_COL_AMOUNT)
        debit_col = fmt.get("debit_col")
        credit_col = fmt.get("credit_col")
        use_split = fmt.get("amount_sign") == "split_cols"

    # --- Date ---
    raw_date = _resolve_col(row, date_col) if date_col else None
    if not raw_date or not raw_date.strip():
        return None  # skip rows without a date (often footers)
    posted_at = _parse_date(raw_date, date_fmt)

    # --- Merchant ---
    raw_merchant = _resolve_col(row, merchant_col) if merchant_col else None
    if not raw_merchant or not raw_merchant.strip():
        return None
    merchant = _normalize_merchant(raw_merchant)
    if not merchant:
        return None

    # --- Amount and direction ---
    if use_split:
        # Capital One style: separate debit / credit columns
        raw_debit = (_resolve_col(row, debit_col) or "").strip() if debit_col else ""
        raw_credit = (_resolve_col(row, credit_col) or "").strip() if credit_col else ""

        if raw_debit and raw_debit not in ("-", "0", "0.00", ".00"):
            amount = abs(_parse_amount(raw_debit))
            direction = "debit"
        elif raw_credit and raw_credit not in ("-", "0", "0.00", ".00"):
            amount = abs(_parse_amount(raw_credit))
            direction = "credit"
        else:
            return None  # both empty or zero — skip
    else:
        raw_amount = _resolve_col(row, amount_col) if amount_col else None
        if not raw_amount or not raw_amount.strip():
            return None
        raw_decimal = _parse_amount(raw_amount)
        amount_sign = fmt.get("amount_sign", "negative_debit")
        if amount_sign == "negative_debit":
            # Chase: negative = debit
            direction = "credit" if raw_decimal >= 0 else "debit"
            amount = abs(raw_decimal)
        elif amount_sign == "positive_debit":
            # Amex: positive = debit (charge)
            direction = "debit" if raw_decimal >= 0 else "credit"
            amount = abs(raw_decimal)
        else:
            direction = "credit" if raw_decimal >= 0 else "debit"
            amount = abs(raw_decimal)

    # --- Optional fields ---
    category_col = fmt["col_map"].get(_COL_CATEGORY) if "col_map" in fmt else None
    raw_category = (_resolve_col(row, category_col) or "").strip() if category_col else ""
    category = raw_category.lower() if raw_category else _UNCATEGORIZED

    description_col = fmt["col_map"].get(_COL_DESCRIPTION) if "col_map" in fmt else None
    raw_description = (_resolve_col(row, description_col) or "").strip() if description_col else ""
    # For formats where description == merchant col, skip storing duplicate.
    description = raw_description if raw_description != raw_merchant else None

    return {
        "posted_at": posted_at,
        "merchant": merchant,
        "amount": amount,
        "currency": default_currency.upper(),
        "direction": direction,
        "category": category,
        "description": description,
        "raw_merchant": raw_merchant,
    }


# ---------------------------------------------------------------------------
# CSV loading from blob store
# ---------------------------------------------------------------------------


async def _load_csv_from_blob(blob_store: Any, storage_ref: str) -> str:
    """Download CSV bytes from blob storage and return as a UTF-8 string.

    Falls back to latin-1 when the content is not valid UTF-8 (common in
    older bank export files).
    """
    data: bytes = await blob_store.get(storage_ref)
    try:
        return data.decode("utf-8-sig")  # strip BOM if present
    except UnicodeDecodeError:
        return data.decode("latin-1")


# ---------------------------------------------------------------------------
# CSV parsing pipeline
# ---------------------------------------------------------------------------


def _parse_csv_rows(
    content: str,
    fmt: dict[str, Any],
    date_fmt: str,
    default_currency: str,
    column_map: dict[str, str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse all rows from *content* and return (parsed, errors).

    Parameters
    ----------
    content:
        Full CSV file content as a string.
    fmt:
        Detected format dict.
    date_fmt:
        strptime format string for dates in this file.
    default_currency:
        ISO-4217 code to assign when the CSV lacks a currency column.
    column_map:
        Optional caller-provided column name overrides (canonical → csv_col).

    Returns
    -------
    tuple[list[dict], list[dict]]
        Parsed transactions and parse-error records (with ``row_index``,
        ``reason`` keys).
    """
    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []

    # Merge caller-supplied column_map overrides into the format's own map.
    effective_fmt = fmt
    if column_map:
        overridden = {**fmt.get("col_map", {}), **column_map}
        effective_fmt = {**fmt, "col_map": overridden}

    # For generic format, resolve columns dynamically from the actual headers.
    resolved_cols: dict[str, str | None] | None = None
    if fmt["name"] == "generic":
        resolved_cols = _resolve_generic_cols(list(headers))
        # Apply caller overrides on top.
        if column_map:
            resolved_cols.update(column_map)

    parsed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for row_idx, row in enumerate(reader):
        try:
            result = _parse_row(
                row,
                effective_fmt,
                date_fmt,
                resolved_cols,
                default_currency,
            )
            if result is not None:
                result["_row_index"] = row_idx
                parsed.append(result)
        except ValueError as exc:
            errors.append({"row_index": row_idx, "reason": str(exc), "raw": dict(row)})
        except Exception as exc:
            errors.append(
                {
                    "row_index": row_idx,
                    "reason": f"unexpected error: {exc}",
                    "raw": dict(row),
                }
            )

    return parsed, errors


# ---------------------------------------------------------------------------
# Date format auto-detection from CSV content
# ---------------------------------------------------------------------------


def _sample_date_values(content: str, date_col: str | None, n: int = 10) -> list[str]:
    """Extract up to *n* non-empty date values from the CSV content.

    Parameters
    ----------
    content:
        Full CSV file content.
    date_col:
        The column name to read dates from.  If None, returns empty list.
    n:
        Maximum number of samples to collect.
    """
    if not date_col:
        return []
    reader = csv.DictReader(io.StringIO(content))
    samples: list[str] = []
    for row in reader:
        val = _resolve_col(row, date_col)
        if val and val.strip():
            samples.append(val.strip())
        if len(samples) >= n:
            break
    return samples


# ---------------------------------------------------------------------------
# Deduplication check
# ---------------------------------------------------------------------------


async def _check_duplicate(
    pool: asyncpg.Pool,
    merchant: str,
    amount: Decimal,
    posted_at: datetime,
    account_id: str | None,
) -> bool:
    """Return True if a matching transaction already exists (composite dedupe).

    Uses the same composite key as dedup Priority 3:
    (account_id, posted_at, amount, merchant).

    Used by _check_duplicates_for_preview for per-row dry-run dedup checks.
    For batch inserts use _check_duplicates_batch instead.
    """
    if account_id is not None:
        row = await pool.fetchrow(
            """
            SELECT id FROM transactions
            WHERE account_id = $1::uuid
              AND posted_at = $2
              AND amount = $3
              AND merchant = $4
            LIMIT 1
            """,
            account_id,
            posted_at,
            amount,
            merchant,
        )
    else:
        row = await pool.fetchrow(
            """
            SELECT id FROM transactions
            WHERE account_id IS NULL
              AND posted_at = $1
              AND amount = $2
              AND merchant = $3
            LIMIT 1
            """,
            posted_at,
            amount,
            merchant,
        )
    return row is not None


async def _check_duplicates_batch(
    pool: asyncpg.Pool,
    batch: list[dict[str, Any]],
    account_id: str | None,
) -> set[int]:
    """Check all rows in batch for existing duplicates.

    Returns a set of indices (positions in *batch*) that are duplicates.

    Uses a single batch query with (posted_at, amount, merchant) tuples
    to avoid N+1 query pattern. When account_id is provided, also matches
    on account_id to use the full composite key.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    batch:
        List of normalised transaction dicts.
    account_id:
        Optional UUID string of the account. When provided, the composite
        key is (account_id, posted_at, amount, merchant); otherwise
        (posted_at, amount, merchant).

    Returns
    -------
    set[int]
        Indices of rows in *batch* that match an existing transaction.
        Empty set if no duplicates found.
    """
    if not batch:
        return set()

    # Build a list of (posted_at, amount, merchant) tuples for all rows.
    tuples = [(txn["posted_at"], txn["amount"], txn["merchant"]) for txn in batch]

    if account_id is not None:
        # Query with account_id as part of the key.
        rows = await pool.fetch(
            """
            SELECT posted_at, amount, merchant
            FROM transactions
            WHERE account_id = $1::uuid
              AND (posted_at, amount, merchant) = ANY($2)
            """,
            account_id,
            tuples,
        )
    else:
        # Query without account_id constraint.
        rows = await pool.fetch(
            """
            SELECT posted_at, amount, merchant
            FROM transactions
            WHERE account_id IS NULL
              AND (posted_at, amount, merchant) = ANY($1)
            """,
            tuples,
        )

    # Build a set of duplicate tuples using native Python types returned by
    # asyncpg, avoiding string-serialization mismatches (e.g. timezone format
    # differences between Python's datetime and PostgreSQL's ::text cast).
    dup_tuples = {(row["posted_at"], row["amount"], row["merchant"]) for row in rows}

    dup_indices = {i for i, tpl in enumerate(tuples) if tpl in dup_tuples}

    return dup_indices


# ---------------------------------------------------------------------------
# Batch insertion
# ---------------------------------------------------------------------------


async def _insert_batch(
    pool: asyncpg.Pool,
    batch: list[dict[str, Any]],
    account_id: str | None,
    import_batch_id: str,
) -> tuple[int, int, list[dict[str, Any]], list[dict[str, Any]]]:
    """Insert a batch of normalised transactions.

    Performs a single batch deduplication query instead of N per-row checks,
    reducing the database roundtrips from N+1 to 2 (one batch check query,
    one insert transaction).

    Returns (imported_count, skipped_count, error_details, imported_txns).
    ``imported_txns`` is the list of transaction dicts that were inserted,
    used by the caller to summarise the import (date range, categories) without
    a second pass over the data.
    """
    imported = 0
    skipped = 0
    errors: list[dict[str, Any]] = []
    imported_txns: list[dict[str, Any]] = []

    # Single batch deduplication check (replaces per-row _check_duplicate calls).
    try:
        dup_indices = await _check_duplicates_batch(pool, batch, account_id)
    except Exception as exc:
        # If batch check fails, fall back to inserting all rows and let
        # the ON CONFLICT handle duplicates. Record errors for failed rows.
        logger.warning(f"batch dedup check failed: {exc}, will rely on ON CONFLICT")
        dup_indices = set()

    for idx, txn in enumerate(batch):
        merchant = txn["merchant"]
        amount = txn["amount"]
        posted_at = txn["posted_at"]
        row_idx = txn.get("_row_index", -1)

        # Check if this row was identified as a duplicate in the batch query.
        if idx in dup_indices:
            skipped += 1
            continue

        try:
            await pool.execute(
                """
                INSERT INTO transactions (
                    account_id,
                    posted_at,
                    merchant,
                    description,
                    amount,
                    currency,
                    direction,
                    category,
                    metadata
                ) VALUES (
                    $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9
                )
                ON CONFLICT DO NOTHING
                """,
                account_id,
                posted_at,
                merchant,
                txn.get("description"),
                amount,
                txn["currency"],
                txn["direction"],
                txn["category"],
                {
                    "import_batch_id": import_batch_id,
                    "raw_merchant": txn.get("raw_merchant", ""),
                },
            )
            imported += 1
            imported_txns.append(txn)
        except asyncpg.UniqueViolationError:
            skipped += 1
        except Exception as exc:
            errors.append(
                {
                    "row_index": row_idx,
                    "reason": f"db_error: {exc}",
                }
            )

    return imported, skipped, errors, imported_txns


def _summarize_import(
    imported_txns: list[dict[str, Any]], batches_processed: int
) -> dict[str, Any]:
    """Build the date_range / categories_used / batches_processed summary.

    Computed from the transactions actually imported, using data already
    threaded out of the batch loop — no extra pass over the CSV.

    - ``date_range``: ``{"start", "end"}`` ISO timestamps spanning the earliest
      and latest ``posted_at`` among imported rows (``None`` when nothing was
      imported).
    - ``categories_used``: sorted, de-duplicated list of non-empty categories
      assigned to imported rows.
    - ``batches_processed``: number of batches the import ran in.
    """
    date_range: dict[str, str] | None = None
    if imported_txns:
        dates = [txn["posted_at"] for txn in imported_txns]
        date_range = {
            "start": min(dates).isoformat(),
            "end": max(dates).isoformat(),
        }
    categories_used = sorted(
        {
            (txn.get("category") or "").strip()
            for txn in imported_txns
            if (txn.get("category") or "").strip()
        }
    )
    return {
        "date_range": date_range,
        "categories_used": categories_used,
        "batches_processed": batches_processed,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def import_transactions(
    pool: asyncpg.Pool,
    blob_store: Any,
    storage_ref: str,
    account_id: str | None = None,
    currency: str = "USD",
    column_map: dict[str, str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import transactions from a bank CSV export stored in blob storage.

    Automatically detects Chase, Amex, Capital One, and generic CSV formats.
    Normalizes dates, amounts, and merchant names.  Deduplicates against
    existing rows using a composite key (account_id, posted_at, amount, merchant).
    Processes in batches of 500 rows.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    blob_store:
        BlobStore instance used to retrieve the CSV file.
    storage_ref:
        Blob storage reference string (e.g. ``s3://bucket/path/file.csv``).
    account_id:
        UUID string of the account to associate all transactions with.
    currency:
        ISO-4217 currency code.  Applied to all rows unless the CSV contains
        a per-row currency column.
    column_map:
        Optional mapping of canonical field names (``date``, ``merchant``,
        ``amount``, ``description``, ``category``) to actual CSV column names.
        Useful for non-standard or renamed headers.
    dry_run:
        When True, parses and validates without inserting; returns a preview
        of the first 10 transactions.

    Returns
    -------
    dict
        ``{total, imported, skipped, errors, import_batch_id, detected_format,
           dry_run, categories_learned, preview (when dry_run=True)}``

    On a successful non-dry-run import where the rows carry category data,
    ``learn_merchant_categories()`` is triggered to upsert merchant->category
    mappings and the count is reported via ``categories_learned``.
    """
    import_batch_id = str(uuid.uuid4())

    # --- Load from blob storage ---
    try:
        content = await _load_csv_from_blob(blob_store, storage_ref)
    except Exception as exc:
        return {
            "status": "error",
            "error": f"Failed to load file from blob storage: {exc}",
            "storage_ref": storage_ref,
            "import_batch_id": import_batch_id,
        }

    # --- Detect format ---
    reader_probe = csv.DictReader(io.StringIO(content))
    headers = list(reader_probe.fieldnames or [])
    fmt = detect_format(headers)

    # Apply caller-supplied column overrides for format selection if provided.
    if column_map and fmt["name"] == "generic":
        # Re-resolve with the caller's mapping hints.
        pass  # column_map is forwarded to _parse_csv_rows below

    # --- Detect date format ---
    # Determine which column holds dates for this format.
    if fmt["name"] == "generic":
        resolved_generic = _resolve_generic_cols(headers)
        date_col_name = resolved_generic.get(_COL_DATE)
    else:
        date_col_name = fmt["col_map"].get(_COL_DATE)
    if column_map and _COL_DATE in column_map:
        date_col_name = column_map[_COL_DATE]

    sample_dates = _sample_date_values(content, date_col_name, n=10)
    date_fmt = _detect_date_format(sample_dates)

    if date_fmt is None:
        return {
            "status": "error",
            "error": (
                "Could not auto-detect date format. "
                f"Sample values: {sample_dates[:5]}. "
                "Provide a column_map with 'date' pointing to the correct column, "
                "or verify the file contains a date column."
            ),
            "import_batch_id": import_batch_id,
            "detected_format": fmt["name"],
        }

    # --- Parse all rows ---
    parsed, parse_errors = _parse_csv_rows(content, fmt, date_fmt, currency, column_map)
    total_rows = len(parsed) + len(parse_errors)

    if dry_run:
        preview = []
        for txn in parsed[:10]:
            preview.append(
                {
                    "posted_at": txn["posted_at"].isoformat(),
                    "merchant": txn["merchant"],
                    "amount": str(txn["amount"]),
                    "currency": txn["currency"],
                    "direction": txn["direction"],
                    "category": txn["category"],
                    "description": txn.get("description"),
                }
            )
        return {
            "dry_run": True,
            "total": total_rows,
            "parsed": len(parsed),
            "parse_errors": len(parse_errors),
            "preview": preview,
            "import_batch_id": import_batch_id,
            "detected_format": fmt["name"],
            "date_format_detected": date_fmt,
            "error_details": parse_errors[:20],
        }

    # --- Process in batches of _BATCH_SIZE ---
    total_imported = 0
    total_skipped = 0
    all_errors: list[dict[str, Any]] = list(parse_errors)
    imported_txns: list[dict[str, Any]] = []
    batches_processed = 0

    for batch_start in range(0, len(parsed), _BATCH_SIZE):
        batch = parsed[batch_start : batch_start + _BATCH_SIZE]
        batch_imported, batch_skipped, batch_errors, batch_txns = await _insert_batch(
            pool, batch, account_id, import_batch_id
        )
        total_imported += batch_imported
        total_skipped += batch_skipped
        all_errors.extend(batch_errors)
        imported_txns.extend(batch_txns)
        batches_processed += 1

        logger.info(
            "import_transactions: batch %d-%d — imported=%d skipped=%d errors=%d",
            batch_start,
            batch_start + len(batch) - 1,
            batch_imported,
            batch_skipped,
            len(batch_errors),
        )

    # --- Post-import merchant-category learning ---
    # Build merchant->category mappings from the imported category data so future
    # imports and transaction records can auto-categorise (spec: finance-data-import
    # "Post-import merchant categorization learning").
    categories_learned = 0
    if total_imported > 0 and _has_category_data(parsed):
        categories_learned = await _trigger_learn_merchant_categories(pool)

    return {
        "total": total_rows,
        "imported": total_imported,
        "skipped": total_skipped,
        "errors": len(all_errors),
        "import_batch_id": import_batch_id,
        "detected_format": fmt["name"],
        "date_format_detected": date_fmt,
        "dry_run": False,
        "categories_learned": categories_learned,
        **_summarize_import(imported_txns, batches_processed),
        "error_details": all_errors[:50],  # cap to first 50 for response size
    }


# ---------------------------------------------------------------------------
# File-path based CSV loading
# ---------------------------------------------------------------------------


def _load_csv_from_file(file_path: str) -> str:
    """Read CSV content from a local filesystem path.

    Falls back to latin-1 when the content is not valid UTF-8 (common in
    older bank export files).

    Raises
    ------
    FileNotFoundError
        When the file does not exist at *file_path*.
    PermissionError
        When the process lacks read access to the file.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"CSV file not found: {file_path}")
    try:
        with open(file_path, encoding="utf-8-sig") as fh:
            return fh.read()
    except UnicodeDecodeError:
        with open(file_path, encoding="latin-1") as fh:
            return fh.read()


# ---------------------------------------------------------------------------
# Merchant mapping lookup helpers
# ---------------------------------------------------------------------------


async def _has_merchant_mappings_table(pool: asyncpg.Pool) -> bool:
    """Return True if the merchant_mappings table exists in the current schema."""
    exists = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = 'merchant_mappings'
        )
        """
    )
    return bool(exists)


async def _lookup_merchant_category(
    pool: asyncpg.Pool,
    merchant: str,
) -> str | None:
    """Look up the best matching category for *merchant* in merchant_mappings.

    Checks whether the stored merchant name is a substring of the input
    merchant string (e.g., "Whole Foods" stored in the DB matches the bank
    statement value "WHOLE FOODS MARKET 1234").  Returns the category with
    the highest confidence, or None when no mapping matches.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    merchant:
        Normalized merchant name to match.

    Returns
    -------
    str | None
        Matched category string, or None when no active mapping matches.
    """
    row = await pool.fetchrow(
        """
        SELECT category
        FROM merchant_mappings
        WHERE is_active = true
          AND $1 ILIKE '%' || merchant || '%'
        ORDER BY confidence DESC
        LIMIT 1
        """,
        merchant,
    )
    return row["category"] if row is not None else None


async def _apply_merchant_mappings(
    pool: asyncpg.Pool,
    parsed: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Auto-apply merchant category mappings to parsed transaction rows.

    For each row whose category is ``"uncategorized"``, looks up the merchant
    in ``finance.merchant_mappings``.  When a mapping is found, the row's
    ``category`` field is updated in-place.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    parsed:
        List of normalised transaction dicts from ``_parse_csv_rows``.

    Returns
    -------
    tuple[list[dict], int]
        The (possibly updated) list of rows and the count of rows that had
        their category auto-assigned from the mappings table.
    """
    if not await _has_merchant_mappings_table(pool):
        return parsed, 0

    # Pre-collect unique uncategorized merchants to avoid N+1 DB round-trips.
    merchants_to_look_up: set[str] = set()
    for row in parsed:
        if row.get("category", "uncategorized") == "uncategorized":
            merchants_to_look_up.add(row["merchant"])

    if not merchants_to_look_up:
        return parsed, 0

    mapping_cache: dict[str, str | None] = {}
    for merchant in merchants_to_look_up:
        mapping_cache[merchant] = await _lookup_merchant_category(pool, merchant)

    auto_applied = 0
    for row in parsed:
        if row.get("category", "uncategorized") == "uncategorized":
            mapped = mapping_cache.get(row["merchant"])
            if mapped:
                row["category"] = mapped
                auto_applied += 1

    return parsed, auto_applied


# ---------------------------------------------------------------------------
# Post-import triggers
# ---------------------------------------------------------------------------


async def _refresh_spending_summaries(pool: asyncpg.Pool) -> bool:
    """Attempt to refresh the spending_summaries materialized view.

    Returns True on success, False when the MV does not exist or refresh fails.
    """
    try:
        has_mv = await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_matviews
                WHERE schemaname = current_schema()
                  AND matviewname = 'spending_summaries'
            )
            """
        )
        if not has_mv:
            return False
        await pool.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY spending_summaries")
        logger.info("import: refreshed spending_summaries materialized view")
        return True
    except asyncpg.PostgresError as exc:
        logger.warning("import: failed to refresh spending_summaries: %s", exc)
        return False


async def _trigger_compute_baselines(pool: asyncpg.Pool) -> bool:
    """Fire-and-forget call to compute_baselines after a large import.

    Returns True when triggered, False when the function is unavailable.
    """
    try:
        from butlers.tools.finance.anomaly_detection import compute_baselines

        await compute_baselines(pool)
        logger.info("import: compute_baselines triggered post-import")
        return True
    except Exception as exc:
        logger.warning("import: compute_baselines trigger failed: %s", exc)
        return False


def _has_category_data(parsed: list[dict[str, Any]]) -> bool:
    """Return True when any parsed row carries a real (non-placeholder) category.

    Rows with no category in the source CSV are normalised to ``_UNCATEGORIZED``;
    those do not count as category data for the purpose of merchant-category
    learning.
    """
    return any((txn.get("category") or "").strip() not in ("", _UNCATEGORIZED) for txn in parsed)


async def _trigger_learn_merchant_categories(pool: asyncpg.Pool) -> int:
    """Trigger merchant-category learning after an import.

    Reuses ``pattern_recognition.learn_merchant_categories`` to aggregate the
    most-frequent category per merchant from the imported transactions and
    upsert the result into ``finance.merchant_mappings``.

    Returns the number of mappings upserted, or 0 when the learning function is
    unavailable or fails (best-effort, non-fatal to the import).
    """
    try:
        from butlers.tools.finance.pattern_recognition import learn_merchant_categories

        result = await learn_merchant_categories(pool)
        upserted = int(result.get("upserted", 0)) if isinstance(result, dict) else 0
        logger.info("import: learn_merchant_categories upserted %d mapping(s)", upserted)
        return upserted
    except Exception as exc:
        logger.warning("import: learn_merchant_categories trigger failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Dry run with duplicate detection
# ---------------------------------------------------------------------------


async def _check_duplicates_for_preview(
    pool: asyncpg.Pool,
    parsed: list[dict[str, Any]],
    account_id: str | None,
) -> list[dict[str, Any]]:
    """Add a ``is_duplicate`` flag to each row in the preview list.

    Performs the same composite dedup check as ``_check_duplicate``.
    """
    result = []
    for txn in parsed[:10]:
        is_dup = False
        try:
            is_dup = await _check_duplicate(
                pool,
                txn["merchant"],
                txn["amount"],
                txn["posted_at"],
                account_id,
            )
        except Exception:
            pass
        entry = {
            "posted_at": txn["posted_at"].isoformat(),
            "merchant": txn["merchant"],
            "amount": str(txn["amount"]),
            "currency": txn["currency"],
            "direction": txn["direction"],
            "category": txn["category"],
            "description": txn.get("description"),
            "is_duplicate": is_dup,
        }
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Public entry point — file-path based
# ---------------------------------------------------------------------------


async def import_transactions_from_file(
    pool: asyncpg.Pool,
    file_path: str,
    account_id: str | None = None,
    currency: str = "USD",
    column_map: dict[str, str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import transactions from a bank CSV file on the local filesystem.

    Automatically detects Chase, Amex, Capital One, and generic CSV formats.
    Normalizes dates, amounts, and merchant names.  Deduplicates against
    existing rows using a composite key (account_id, posted_at, amount, merchant).
    Auto-applies merchant category mappings for uncategorized rows.
    Processes in batches of 500 rows.

    Post-import triggers (non-dry-run only, when 1+ rows imported):
    - Refreshes the ``spending_summaries`` materialized view (if present).
    - Calls ``compute_baselines()`` when 50 or more rows were imported.
    - Calls ``learn_merchant_categories()`` when imported rows carry category
      data, upserting merchant->category mappings and reporting the count via
      ``categories_learned``.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    file_path:
        Absolute or relative path to the CSV file on the local filesystem.
    account_id:
        UUID string of the account to associate all transactions with.
    currency:
        ISO-4217 currency code.  Applied to all rows unless the CSV contains
        a per-row currency column.
    column_map:
        Optional mapping of canonical field names (``date``, ``merchant``,
        ``amount``, ``description``, ``category``) to actual CSV column names.
        Useful for non-standard or renamed headers.
    dry_run:
        When True, parses, validates, checks for duplicates against the DB,
        and returns a preview of the first 10 transactions without inserting.
        Preview includes an ``is_duplicate`` flag per row.

    Returns
    -------
    dict
        ``{total, imported, skipped, errors, import_batch_id, detected_format,
           dry_run, merchant_mappings_applied, mv_refreshed,
           baselines_triggered, categories_learned, preview (when dry_run=True)}``
    """
    import_batch_id = str(uuid.uuid4())

    # --- Load from local filesystem ---
    try:
        content = _load_csv_from_file(file_path)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return {
            "status": "error",
            "error": f"Failed to load CSV file: {exc}",
            "file_path": file_path,
            "import_batch_id": import_batch_id,
        }

    # --- Detect format ---
    reader_probe = csv.DictReader(io.StringIO(content))
    headers = list(reader_probe.fieldnames or [])
    fmt = detect_format(headers)

    # --- Detect date format ---
    if fmt["name"] == "generic":
        resolved_generic = _resolve_generic_cols(headers)
        date_col_name = resolved_generic.get(_COL_DATE)
    else:
        date_col_name = fmt["col_map"].get(_COL_DATE)
    if column_map and _COL_DATE in column_map:
        date_col_name = column_map[_COL_DATE]

    sample_dates = _sample_date_values(content, date_col_name, n=10)
    date_fmt = _detect_date_format(sample_dates)

    if date_fmt is None:
        return {
            "status": "error",
            "error": (
                "Could not auto-detect date format. "
                f"Sample values: {sample_dates[:5]}. "
                "Provide a column_map with 'date' pointing to the correct column, "
                "or verify the file contains a date column."
            ),
            "import_batch_id": import_batch_id,
            "detected_format": fmt["name"],
        }

    # --- Parse all rows ---
    parsed, parse_errors = _parse_csv_rows(content, fmt, date_fmt, currency, column_map)
    total_rows = len(parsed) + len(parse_errors)

    # --- Merchant mapping auto-apply ---
    try:
        parsed, merchant_mappings_applied = await _apply_merchant_mappings(pool, parsed)
    except Exception as exc:
        logger.warning("import_from_file: merchant mapping lookup failed: %s", exc)
        merchant_mappings_applied = 0

    if dry_run:
        # Dry run: detect duplicates and return preview without inserting.
        preview = await _check_duplicates_for_preview(pool, parsed, account_id)
        return {
            "dry_run": True,
            "total": total_rows,
            "parsed": len(parsed),
            "parse_errors": len(parse_errors),
            "preview": preview,
            "import_batch_id": import_batch_id,
            "detected_format": fmt["name"],
            "date_format_detected": date_fmt,
            "merchant_mappings_applied": merchant_mappings_applied,
            "error_details": parse_errors[:20],
        }

    # --- Process in batches of _BATCH_SIZE ---
    total_imported = 0
    total_skipped = 0
    all_errors: list[dict[str, Any]] = list(parse_errors)
    imported_txns: list[dict[str, Any]] = []
    batches_processed = 0

    for batch_start in range(0, len(parsed), _BATCH_SIZE):
        batch = parsed[batch_start : batch_start + _BATCH_SIZE]
        batch_imported, batch_skipped, batch_errors, batch_txns = await _insert_batch(
            pool, batch, account_id, import_batch_id
        )
        total_imported += batch_imported
        total_skipped += batch_skipped
        all_errors.extend(batch_errors)
        imported_txns.extend(batch_txns)
        batches_processed += 1

        logger.info(
            "import_from_file: batch %d-%d — imported=%d skipped=%d errors=%d",
            batch_start,
            batch_start + len(batch) - 1,
            batch_imported,
            batch_skipped,
            len(batch_errors),
        )

    # --- Post-import triggers ---
    mv_refreshed = False
    baselines_triggered = False
    categories_learned = 0
    if total_imported > 0:
        mv_refreshed = await _refresh_spending_summaries(pool)
        if total_imported >= 50:
            baselines_triggered = await _trigger_compute_baselines(pool)
        # Learn merchant->category mappings from the imported category data.
        if _has_category_data(parsed):
            categories_learned = await _trigger_learn_merchant_categories(pool)

    return {
        "total": total_rows,
        "imported": total_imported,
        "skipped": total_skipped,
        "errors": len(all_errors),
        "import_batch_id": import_batch_id,
        "detected_format": fmt["name"],
        "date_format_detected": date_fmt,
        "dry_run": False,
        "merchant_mappings_applied": merchant_mappings_applied,
        "mv_refreshed": mv_refreshed,
        "baselines_triggered": baselines_triggered,
        "categories_learned": categories_learned,
        **_summarize_import(imported_txns, batches_processed),
        "error_details": all_errors[:50],  # cap to first 50 for response size
    }
