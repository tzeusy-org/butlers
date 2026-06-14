"""Finance butler endpoints.

Provides endpoints for transactions, subscriptions, bills, accounts,
spending summaries, and upcoming bills. All data is queried directly
from the finance butler's PostgreSQL database via asyncpg.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from butlers.api.audit_emit import emit_dashboard_audit
from butlers.api.db import DatabaseManager
from butlers.api.models import PaginatedResponse, PaginationMeta

# Dynamically load models module from the same directory
_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("finance_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["finance_api_models"] = _models
    _spec.loader.exec_module(_models)

    AccountModel = _models.AccountModel
    BillModel = _models.BillModel
    BulkTransactionErrorDetail = _models.BulkTransactionErrorDetail
    BulkTransactionItem = _models.BulkTransactionItem
    BulkTransactionRequest = _models.BulkTransactionRequest
    BulkTransactionResponse = _models.BulkTransactionResponse
    BulkUpdateOpResultModel = _models.BulkUpdateOpResultModel
    BulkUpdateRequestModel = _models.BulkUpdateRequestModel
    BulkUpdateResponseModel = _models.BulkUpdateResponseModel
    DistinctMerchantModel = _models.DistinctMerchantModel
    SpendingGroupModel = _models.SpendingGroupModel
    SpendingSummaryModel = _models.SpendingSummaryModel
    SubscriptionModel = _models.SubscriptionModel
    TransactionModel = _models.TransactionModel
    UpcomingBillItemModel = _models.UpcomingBillItemModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/finance", tags=["finance"])

BUTLER_DB = "finance"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the finance butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Finance butler database is not available",
        )


# ---------------------------------------------------------------------------
# Facts overlay join (split-brain read fix — bu-v3a4x.1 / bu-v3a4x.2)
# ---------------------------------------------------------------------------
#
# Bulk-metadata edits, merchant normalization, and category inference write to
# the bitemporal `facts` overlay (scope='finance', predicate transaction_*),
# NOT to `finance.transactions`. The dashboard reads `finance.transactions`, so
# without this join the overlay values (normalized_merchant / inferred_category
# / bulk-metadata edits) are invisible and the projection columns are always
# null. We keep `finance.transactions` as the base store of record and MERGE the
# overlay on read.
#
# Overlay key: a transaction is linked to its facts row by the SAME natural key
# the fact's idempotency hash is derived from — (posted_at, merchant, currency,
# absolute amount). facts stores valid_at=posted_at, metadata->>'merchant' (raw),
# metadata->>'currency' (uppercase), metadata->>'amount' (NUMERIC(14,2) string,
# always positive). finance.transactions.amount is SIGNED, so we match on ABS().
# Both amount columns are NUMERIC(14,2), so the ::numeric cast comparison is
# precision-safe. Only the latest active fact (created_at DESC) is surfaced.
_OVERLAY_JOIN = (
    " LEFT JOIN LATERAL ("
    "   SELECT f.metadata AS overlay_metadata"
    "   FROM facts f"
    "   WHERE f.scope = 'finance'"
    "     AND f.validity = 'active'"
    "     AND f.predicate IN ('transaction_debit', 'transaction_credit')"
    "     AND f.valid_at = t.posted_at"
    "     AND f.metadata->>'merchant' = t.merchant"
    "     AND upper(f.metadata->>'currency') = upper(t.currency)"
    "     AND (f.metadata->>'amount')::numeric = abs(t.amount)"
    "   ORDER BY f.created_at DESC"
    "   LIMIT 1"
    " ) ovl ON true"
)

# Effective (overlay-preferred) expressions for filtering/grouping. The overlay
# value wins when present; otherwise the base column is used.
_EFFECTIVE_MERCHANT = "COALESCE(ovl.overlay_metadata->>'normalized_merchant', t.merchant)"
_EFFECTIVE_CATEGORY = "COALESCE(ovl.overlay_metadata->>'inferred_category', t.category)"

# Overlay keys that are projected onto dedicated TransactionModel fields rather
# than left in the merged `metadata` blob.
_OVERLAY_PROJECTED_KEYS = ("normalized_merchant", "inferred_category")


def _coerce_jsonb(value: object) -> dict:
    """Coerce an asyncpg JSONB value (dict or JSON string) into a plain dict."""
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(value, dict):
        return dict(value)
    return {}


def _overlay_transaction_model(r) -> TransactionModel:
    """Build a TransactionModel, merging the facts overlay over the base row.

    The base ``finance.transactions`` row is the store of record. The facts
    overlay (``overlay_metadata``) carries normalized_merchant / inferred_category
    and any bulk-metadata edits. Overlay values win on conflict; base metadata is
    preserved where the overlay is absent. The two projected overlay fields are
    surfaced on dedicated model fields and stripped from the merged ``metadata``
    blob to keep the response shape stable.
    """
    base_meta = _coerce_jsonb(r["metadata"])
    overlay_meta = _coerce_jsonb(r["overlay_metadata"])

    # Surface the projected overlay fields, falling back to any value already on
    # the base metadata (legacy rows that wrote the overlay inline).
    normalized_merchant = overlay_meta.get("normalized_merchant") or base_meta.get(
        "normalized_merchant"
    )
    inferred_category = overlay_meta.get("inferred_category") or base_meta.get("inferred_category")

    # Merge overlay edits onto the base metadata blob (overlay wins), then drop
    # the projected keys so they only appear on their dedicated fields.
    merged_meta = {**base_meta, **overlay_meta}
    for key in _OVERLAY_PROJECTED_KEYS:
        merged_meta.pop(key, None)

    return TransactionModel(
        id=str(r["id"]),
        posted_at=str(r["posted_at"]),
        merchant=r["merchant"],
        normalized_merchant=normalized_merchant,
        description=r["description"],
        amount=str(r["amount"]),
        currency=r["currency"],
        direction=r["direction"],
        category=r["category"],
        inferred_category=inferred_category,
        payment_method=r["payment_method"],
        account_id=str(r["account_id"]) if r["account_id"] else None,
        receipt_url=r["receipt_url"],
        external_ref=r["external_ref"],
        source_message_id=r["source_message_id"],
        metadata=merged_meta,
        created_at=str(r["created_at"]),
        updated_at=str(r["updated_at"]),
    )


# ---------------------------------------------------------------------------
# GET /transactions — list transactions
# ---------------------------------------------------------------------------


@router.get("/transactions", response_model=PaginatedResponse[TransactionModel])
async def list_transactions(
    category: str | None = Query(None, description="Filter by category"),
    merchant: str | None = Query(None, description="Filter by merchant (case-insensitive)"),
    account_id: str | None = Query(None, description="Filter by account ID"),
    since: str | None = Query(None, description="Filter from this timestamp (inclusive)"),
    until: str | None = Query(None, description="Filter up to this timestamp (inclusive)"),
    min_amount: float | None = Query(None, description="Filter by minimum amount"),
    max_amount: float | None = Query(None, description="Filter by maximum amount"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[TransactionModel]:
    """List transactions with optional filters."""
    pool = _pool(db)

    # Soft-delete exclusion (spec: finance-crud-operations §"Filtered transaction
    # listing"). Every transaction read SHALL exclude soft-deleted rows.
    #
    # Reads are overlay-aware (bu-v3a4x.1): merchant/category filters resolve
    # against the facts overlay (normalized_merchant / inferred_category) first,
    # falling back to the base columns. Base-table columns are `t.`-qualified
    # because the overlay LATERAL join is always present.
    conditions: list[str] = ["t.deleted_at IS NULL"]
    args: list[object] = []
    idx = 1

    if category is not None:
        conditions.append(f"{_EFFECTIVE_CATEGORY} = ${idx}")
        args.append(category)
        idx += 1

    if merchant is not None:
        conditions.append(f"{_EFFECTIVE_MERCHANT} ILIKE '%' || ${idx} || '%'")
        args.append(merchant)
        idx += 1

    if account_id is not None:
        conditions.append(f"t.account_id = ${idx}::uuid")
        args.append(account_id)
        idx += 1

    if since is not None:
        conditions.append(f"t.posted_at >= ${idx}")
        args.append(since)
        idx += 1

    if until is not None:
        conditions.append(f"t.posted_at <= ${idx}")
        args.append(until)
        idx += 1

    if min_amount is not None:
        conditions.append(f"t.amount >= ${idx}")
        args.append(min_amount)
        idx += 1

    if max_amount is not None:
        conditions.append(f"t.amount <= ${idx}")
        args.append(max_amount)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = (
        await pool.fetchval(
            f"SELECT count(*) FROM finance.transactions t{_OVERLAY_JOIN}{where}",
            *args,
        )
        or 0
    )

    rows = await pool.fetch(
        f"SELECT t.id, t.posted_at, t.merchant, t.description, t.amount, t.currency,"
        f" t.direction, t.category, t.payment_method, t.account_id, t.receipt_url,"
        f" t.external_ref, t.source_message_id, t.metadata, t.created_at, t.updated_at,"
        f" ovl.overlay_metadata AS overlay_metadata"
        f" FROM finance.transactions t{_OVERLAY_JOIN}{where}"
        f" ORDER BY t.posted_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [_overlay_transaction_model(r) for r in rows]

    return PaginatedResponse[TransactionModel](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /subscriptions — list subscriptions
# ---------------------------------------------------------------------------


@router.get("/subscriptions", response_model=PaginatedResponse[SubscriptionModel])
async def list_subscriptions(
    status: str | None = Query(None, description="Filter by status (active, cancelled, paused)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[SubscriptionModel]:
    """List subscriptions with optional status filter."""
    pool = _pool(db)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if status is not None:
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM finance.subscriptions{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, service, amount, currency, frequency, next_renewal, status,"
        f" auto_renew, payment_method, account_id, source_message_id, metadata,"
        f" created_at, updated_at"
        f" FROM finance.subscriptions{where}"
        f" ORDER BY next_renewal ASC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [
        SubscriptionModel(
            id=str(r["id"]),
            service=r["service"],
            amount=str(r["amount"]),
            currency=r["currency"],
            frequency=r["frequency"],
            next_renewal=str(r["next_renewal"]),
            status=r["status"],
            auto_renew=r["auto_renew"],
            payment_method=r["payment_method"],
            account_id=str(r["account_id"]) if r["account_id"] else None,
            source_message_id=r["source_message_id"],
            metadata=dict(r["metadata"]) if r["metadata"] else {},
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[SubscriptionModel](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /bills — list bills
# ---------------------------------------------------------------------------


@router.get("/bills", response_model=PaginatedResponse[BillModel])
async def list_bills(
    status: str | None = Query(None, description="Filter by status (pending, paid, overdue)"),
    payee: str | None = Query(None, description="Filter by payee (case-insensitive substring)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[BillModel]:
    """List bills with optional status and payee filters."""
    pool = _pool(db)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if status is not None:
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1

    if payee is not None:
        conditions.append(f"payee ILIKE '%' || ${idx} || '%'")
        args.append(payee)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM finance.bills{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, payee, amount, currency, due_date, frequency, status,"
        f" payment_method, account_id, source_message_id, statement_period_start,"
        f" statement_period_end, paid_at, metadata, created_at, updated_at"
        f" FROM finance.bills{where}"
        f" ORDER BY due_date ASC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [
        BillModel(
            id=str(r["id"]),
            payee=r["payee"],
            amount=str(r["amount"]),
            currency=r["currency"],
            due_date=str(r["due_date"]),
            frequency=r["frequency"],
            status=r["status"],
            payment_method=r["payment_method"],
            account_id=str(r["account_id"]) if r["account_id"] else None,
            source_message_id=r["source_message_id"],
            statement_period_start=(
                str(r["statement_period_start"]) if r["statement_period_start"] else None
            ),
            statement_period_end=(
                str(r["statement_period_end"]) if r["statement_period_end"] else None
            ),
            paid_at=str(r["paid_at"]) if r["paid_at"] else None,
            metadata=dict(r["metadata"]) if r["metadata"] else {},
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[BillModel](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /accounts — list accounts
# ---------------------------------------------------------------------------


@router.get("/accounts", response_model=PaginatedResponse[AccountModel])
async def list_accounts(
    type: str | None = Query(None, description="Filter by account type"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[AccountModel]:
    """List accounts with optional type filter."""
    pool = _pool(db)

    # Only surface active accounts. accounts.is_active (migration finance_006)
    # defaults to true; deactivated accounts are excluded from dashboard reads.
    conditions: list[str] = ["is_active = true"]
    args: list[object] = []
    idx = 1

    if type is not None:
        conditions.append(f"type = ${idx}")
        args.append(type)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM finance.accounts{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, institution, type, name, last_four, currency, metadata,"
        f" created_at, updated_at"
        f" FROM finance.accounts{where}"
        f" ORDER BY institution ASC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [
        AccountModel(
            id=str(r["id"]),
            institution=r["institution"],
            type=r["type"],
            name=r["name"],
            last_four=r["last_four"],
            currency=r["currency"],
            metadata=dict(r["metadata"]) if r["metadata"] else {},
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[AccountModel](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /spending-summary — aggregate spending
# ---------------------------------------------------------------------------


@router.get("/spending-summary", response_model=SpendingSummaryModel)
async def get_spending_summary(
    start_date: str | None = Query(None, description="Start date (YYYY-MM-DD, inclusive)"),
    end_date: str | None = Query(None, description="End date (YYYY-MM-DD, inclusive)"),
    group_by: str = Query("category", description="Group by: category, merchant, week, month"),
    account_id: str | None = Query(None, description="Filter by account ID"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> SpendingSummaryModel:
    """Aggregate debit spending over a date range, grouped by the specified dimension."""
    pool = _pool(db)

    valid_group_by = {"category", "merchant", "week", "month"}
    if group_by not in valid_group_by:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid group_by '{group_by}'. Must be one of: {sorted(valid_group_by)}",
        )

    today = date.today()
    start = date.fromisoformat(start_date) if start_date else today.replace(day=1)
    end = date.fromisoformat(end_date) if end_date else today

    # Spec: finance-crud-operations §"Spending aggregation" — aggregate from
    # transactions WHERE direction = 'debit' AND deleted_at IS NULL. Soft-deleted
    # transactions must not inflate spending totals.
    #
    # 'transfer' and 'uncategorized' are NOT real spend: transfers move money
    # between the owner's own accounts (~$11k in the live data) and uncategorized
    # is an unclassified bucket. Excluding both — keyed off the effective category
    # (overlay inferred_category preferred, else the raw category column) — keeps
    # the 'Top category' KPI and the spend total honest. Exclusion applies to
    # every group_by dimension, not just category.
    #
    # Reads are overlay-aware (bu-v3a4x.1): the effective category/merchant
    # resolve against the facts overlay first, so normalized merchants and
    # inferred categories drive both the exclusion filter and the grouping.
    conditions: list[str] = [
        "t.direction = 'debit'",
        "t.deleted_at IS NULL",
        f"{_EFFECTIVE_CATEGORY} NOT IN ('transfer', 'uncategorized')",
        "t.posted_at::date >= $1",
        "t.posted_at::date <= $2",
    ]
    args: list[object] = [start, end]
    idx = 3

    if account_id is not None:
        conditions.append(f"t.account_id = ${idx}::uuid")
        args.append(account_id)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    # Build group expression — prefer overlay fields when present
    if group_by == "category":
        group_expr = _EFFECTIVE_CATEGORY
    elif group_by == "merchant":
        group_expr = _EFFECTIVE_MERCHANT
    elif group_by == "week":
        group_expr = "to_char(t.posted_at, 'IYYY-\"W\"IW')"
    else:  # month
        group_expr = "to_char(t.posted_at, 'YYYY-MM')"

    total_row = await pool.fetchrow(
        f"SELECT COALESCE(SUM(t.amount), 0) AS total, COALESCE(MAX(t.currency), 'USD') AS currency"
        f" FROM finance.transactions t{_OVERLAY_JOIN}{where}",
        *args,
    )
    total_spend = str(total_row["total"])
    currency = total_row["currency"]

    group_rows = await pool.fetch(
        f"SELECT {group_expr} AS key, SUM(t.amount) AS amount, COUNT(*) AS count"
        f" FROM finance.transactions t{_OVERLAY_JOIN}{where}"
        f" GROUP BY {group_expr}"
        f" ORDER BY SUM(t.amount) DESC",
        *args,
    )

    groups = [
        SpendingGroupModel(
            key=str(r["key"]),
            amount=str(r["amount"]),
            count=int(r["count"]),
        )
        for r in group_rows
    ]

    return SpendingSummaryModel(
        start_date=str(start),
        end_date=str(end),
        currency=currency,
        total_spend=total_spend,
        groups=groups,
    )


# ---------------------------------------------------------------------------
# GET /upcoming-bills — bills due soon
# ---------------------------------------------------------------------------


@router.get("/upcoming-bills")
async def get_upcoming_bills(
    days_ahead: int = Query(14, ge=1, le=365, description="Look-ahead window in days"),
    include_overdue: bool = Query(True, description="Include overdue bills"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> dict:
    """List bills due within the look-ahead window, with urgency classification."""
    pool = _pool(db)

    today = date.today()
    horizon = today + timedelta(days=days_ahead)

    if include_overdue:
        rows = await pool.fetch(
            "SELECT id, payee, amount, currency, due_date, frequency, status,"
            " payment_method, account_id, source_message_id, statement_period_start,"
            " statement_period_end, paid_at, metadata, created_at, updated_at"
            " FROM finance.bills"
            " WHERE status != 'paid' AND due_date <= $1"
            " ORDER BY due_date ASC",
            horizon,
        )
    else:
        rows = await pool.fetch(
            "SELECT id, payee, amount, currency, due_date, frequency, status,"
            " payment_method, account_id, source_message_id, statement_period_start,"
            " statement_period_end, paid_at, metadata, created_at, updated_at"
            " FROM finance.bills"
            " WHERE status != 'paid' AND due_date >= $1 AND due_date <= $2"
            " ORDER BY due_date ASC",
            today,
            horizon,
        )

    items = []
    total_amount = 0.0

    for r in rows:
        due = r["due_date"]
        # due_date is a date column; may come back as date object
        if hasattr(due, "isoformat"):
            due_date = due
        else:
            due_date = date.fromisoformat(str(due))

        days_until = (due_date - today).days

        if days_until < 0:
            urgency = "overdue"
        elif days_until == 0:
            urgency = "due_today"
        elif days_until <= 3:
            urgency = "due_soon"
        else:
            urgency = "upcoming"

        bill = BillModel(
            id=str(r["id"]),
            payee=r["payee"],
            amount=str(r["amount"]),
            currency=r["currency"],
            due_date=str(r["due_date"]),
            frequency=r["frequency"],
            status=r["status"],
            payment_method=r["payment_method"],
            account_id=str(r["account_id"]) if r["account_id"] else None,
            source_message_id=r["source_message_id"],
            statement_period_start=(
                str(r["statement_period_start"]) if r["statement_period_start"] else None
            ),
            statement_period_end=(
                str(r["statement_period_end"]) if r["statement_period_end"] else None
            ),
            paid_at=str(r["paid_at"]) if r["paid_at"] else None,
            metadata=dict(r["metadata"]) if r["metadata"] else {},
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        item = UpcomingBillItemModel(bill=bill, urgency=urgency, days_until_due=days_until)
        items.append(item.model_dump())
        total_amount += float(r["amount"])

    return {
        "items": items,
        "total_amount": str(round(total_amount, 2)),
        "count": len(items),
        "days_ahead": days_ahead,
        "include_overdue": include_overdue,
    }


# ---------------------------------------------------------------------------
# POST /transactions/bulk — bulk transaction ingestion
# ---------------------------------------------------------------------------


@router.post("/transactions/bulk", response_model=BulkTransactionResponse)
async def bulk_ingest_transactions(
    http_request: Request,
    request: BulkTransactionRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> BulkTransactionResponse:
    """Bulk-ingest normalized transaction objects.

    Accepts 1–500 items per request. Returns per-row counts for imported,
    skipped (dedup), and errored rows. Embeddings are skipped for performance;
    tsvector (full-text search) is still computed.

    error_details entries include an index and reason:
    - "duplicate" — already exists (composite or source_message_id dedup)
    - "invalid_date" — unparseable posted_at
    - "invalid_amount" — non-numeric amount
    """
    if len(request.transactions) == 0 or len(request.transactions) > 500:
        raise HTTPException(
            status_code=422,
            detail=f"transactions must contain 1–500 items; got {len(request.transactions)}",
        )

    pool = _pool(db)
    _facts_mod = _load_facts_tools()

    txn_dicts = [item.model_dump() for item in request.transactions]

    try:
        result = await _facts_mod.bulk_record_transactions(
            pool,
            transactions=txn_dicts,
            account_id=request.account_id,
            source=request.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    error_details = [
        BulkTransactionErrorDetail(index=e["index"], reason=e["reason"])
        for e in result.get("error_details", [])
    ]

    # Explicit audit — middleware also fires; this carries the semantic operation label.
    await emit_dashboard_audit(
        db,
        butler="finance",
        operation="transaction_bulk_ingest",
        method="POST",
        path="/api/finance/transactions/bulk",
        request=http_request,
        body={
            "account_id": str(request.account_id) if request.account_id else None,
            "source": request.source,
            "count": len(request.transactions),
        },
        response_status=200,
    )

    return BulkTransactionResponse(
        total=result["total"],
        imported=result["imported"],
        skipped=result["skipped"],
        errors=result["errors"],
        error_details=error_details,
    )


# ---------------------------------------------------------------------------
# GET /merchants/distinct — distinct merchants with aggregate stats
# ---------------------------------------------------------------------------

_FACTS_TOOLS_PATH = Path(__file__).parents[1] / "tools" / "facts.py"


def _load_facts_tools():
    """Dynamically load the finance facts tools module."""
    import importlib.util
    import sys

    module_name = "finance_facts_tools"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, _FACTS_TOOLS_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


@router.get("/merchants/distinct", response_model=PaginatedResponse[DistinctMerchantModel])
async def list_distinct_merchants(
    start_date: str | None = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date filter (YYYY-MM-DD)"),
    min_count: int | None = Query(None, ge=1, description="Minimum transaction count (HAVING)"),
    unnormalized_only: bool = Query(False, description="Only merchants without normalization"),
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[DistinctMerchantModel]:
    """Return distinct merchants from active transaction facts with aggregate stats."""
    pool = _pool(db)

    _facts_mod = _load_facts_tools()
    result = await _facts_mod.list_distinct_merchants(
        pool,
        start_date=start_date,
        end_date=end_date,
        min_count=min_count,
        unnormalized_only=unnormalized_only,
        limit=limit,
        offset=offset,
    )

    data = [
        DistinctMerchantModel(
            merchant=item["merchant"],
            normalized_merchant=item.get("normalized_merchant"),
            count=item["count"],
            total_amount=item["total_amount"],
        )
        for item in result["items"]
    ]

    return PaginatedResponse[DistinctMerchantModel](
        data=data,
        meta=PaginationMeta(total=result["total"], offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# PATCH /transactions/bulk-metadata — bulk metadata overlay
# ---------------------------------------------------------------------------


@router.patch("/transactions/bulk-metadata", response_model=BulkUpdateResponseModel)
async def bulk_update_transactions(
    http_request: Request,
    request: BulkUpdateRequestModel = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> BulkUpdateResponseModel:
    """Apply bulk metadata overlay to matching transaction facts.

    Each op specifies an ILIKE merchant_pattern and a set of overlay fields
    (normalized_merchant, inferred_category). The original fact content
    (merchant, category, subject, predicate, content, embedding) is never modified.
    """
    pool = _pool(db)

    # Serialize ops back to the dict format expected by the tools layer
    ops_raw = [
        {
            "match": {"merchant_pattern": op.match.merchant_pattern},
            "set": op.set.model_dump(exclude_none=True),
        }
        for op in request.ops
    ]

    if len(ops_raw) > 200:
        raise HTTPException(
            status_code=422,
            detail=f"Too many ops: {len(ops_raw)} exceeds max 200",
        )

    _facts_mod = _load_facts_tools()
    try:
        result = await _facts_mod.bulk_update_transactions(pool, ops=ops_raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    op_results = [
        BulkUpdateOpResultModel(
            pattern=r["pattern"],
            set=r["set"],
            matched=r["matched"],
            updated=r["updated"],
        )
        for r in result["results"]
    ]

    # Explicit audit — middleware also fires; this carries the semantic operation label.
    await emit_dashboard_audit(
        db,
        butler="finance",
        operation="transaction_bulk_update",
        method="PATCH",
        path="/api/finance/transactions/bulk-metadata",
        body={"op_count": len(ops_raw)},
        response_status=200,
        request=http_request,
    )

    return BulkUpdateResponseModel(
        updated_total=result["updated_total"],
        results=op_results,
    )
