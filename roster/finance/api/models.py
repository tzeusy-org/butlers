"""Pydantic models for the finance butler API.

Provides models for transactions, subscriptions, bills, accounts,
and spending summaries used by the finance butler's dashboard endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel


class TransactionModel(BaseModel):
    """A recorded financial transaction."""

    id: str
    posted_at: str
    merchant: str
    description: str | None = None
    amount: str  # numeric as string to preserve precision
    currency: str
    direction: str
    category: str
    payment_method: str | None = None
    account_id: str | None = None
    receipt_url: str | None = None
    external_ref: str | None = None
    source_message_id: str | None = None
    metadata: dict = {}
    created_at: str
    updated_at: str


class SubscriptionModel(BaseModel):
    """A tracked recurring subscription commitment."""

    id: str
    service: str
    amount: str
    currency: str
    frequency: str
    next_renewal: str
    status: str
    auto_renew: bool = True
    payment_method: str | None = None
    account_id: str | None = None
    source_message_id: str | None = None
    metadata: dict = {}
    created_at: str
    updated_at: str


class BillModel(BaseModel):
    """A tracked payable bill obligation."""

    id: str
    payee: str
    amount: str
    currency: str
    due_date: str
    frequency: str
    status: str
    payment_method: str | None = None
    account_id: str | None = None
    source_message_id: str | None = None
    statement_period_start: str | None = None
    statement_period_end: str | None = None
    paid_at: str | None = None
    metadata: dict = {}
    created_at: str
    updated_at: str


class AccountModel(BaseModel):
    """A tracked financial account."""

    id: str
    institution: str
    type: str
    name: str | None = None
    last_four: str | None = None
    currency: str
    metadata: dict = {}
    created_at: str
    updated_at: str


class SpendingGroupModel(BaseModel):
    """A spending aggregation bucket (category, merchant, week, or month)."""

    key: str
    amount: str
    count: int


class SpendingSummaryModel(BaseModel):
    """Aggregated spending summary over a date range."""

    start_date: str
    end_date: str
    currency: str
    total_spend: str
    groups: list[SpendingGroupModel] = []


class UpcomingBillItemModel(BaseModel):
    """A bill with urgency classification for the upcoming-bills endpoint."""

    bill: BillModel
    urgency: str
    days_until_due: int


class DistinctMerchantModel(BaseModel):
    """Aggregate row from the distinct-merchants query."""

    merchant: str
    normalized_merchant: str | None = None
    count: int
    total_amount: str  # numeric as string to preserve precision


class BulkUpdateMatchModel(BaseModel):
    """Match criteria for a single bulk-update op."""

    merchant_pattern: str


class BulkUpdateSetModel(BaseModel):
    """Fields to overlay on matching transaction fact metadata."""

    normalized_merchant: str | None = None
    inferred_category: str | None = None


class BulkUpdateOpModel(BaseModel):
    """A single op in a bulk-update request."""

    match: BulkUpdateMatchModel
    set: BulkUpdateSetModel


class BulkUpdateOpResultModel(BaseModel):
    """Result of a single bulk-update op."""

    pattern: str
    set: dict
    matched: int
    updated: int


class BulkUpdateRequestModel(BaseModel):
    """Request body for the bulk-metadata-update endpoint."""

    ops: list[BulkUpdateOpModel]


class BulkUpdateResponseModel(BaseModel):
    """Response from the bulk-metadata-update endpoint."""

    updated_total: int
    results: list[BulkUpdateOpResultModel]
