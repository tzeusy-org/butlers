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
