"""Pydantic models for the relationship/CRM API.

Provides models for contacts, groups, labels, notes, interactions,
gifts, loans, and upcoming dates used by the relationship butler's
dashboard endpoints.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class Label(BaseModel):
    """A label/tag that can be applied to contacts or groups."""

    id: UUID
    name: str
    color: str | None = None


class ContactSummary(BaseModel):
    """Compact contact representation for list views."""

    id: UUID
    full_name: str
    nickname: str | None = None
    email: str | None = None
    phone: str | None = None
    labels: list[Label] = Field(default_factory=list)
    last_interaction_at: datetime | None = None


class ContactDetail(ContactSummary):
    """Full contact with all fields."""

    notes: str | None = None
    birthday: date | None = None
    company: str | None = None
    job_title: str | None = None
    address: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class Group(BaseModel):
    """A named group of contacts."""

    id: UUID
    name: str
    description: str | None = None
    member_count: int = 0
    labels: list[Label] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class Note(BaseModel):
    """A freeform note attached to a contact."""

    id: UUID
    contact_id: UUID
    content: str
    created_at: datetime
    updated_at: datetime


class Interaction(BaseModel):
    """A recorded interaction with a contact."""

    id: UUID
    contact_id: UUID
    type: str  # email, call, meeting, message, etc.
    summary: str
    details: str | None = None
    occurred_at: datetime
    created_at: datetime


class Gift(BaseModel):
    """A gift given to or received from a contact."""

    id: UUID
    contact_id: UUID
    description: str
    direction: str  # "given" or "received"
    occasion: str | None = None
    date: date
    value: float | None = None
    created_at: datetime


class Loan(BaseModel):
    """A loan (money or item) between the user and a contact."""

    id: UUID
    contact_id: UUID
    description: str
    direction: str  # "lent" or "borrowed"
    amount: float
    currency: str = "USD"
    status: str = "active"  # active, repaid, forgiven
    date: date
    due_date: date | None = None
    created_at: datetime


class UpcomingDate(BaseModel):
    """Upcoming important date (birthday, anniversary, etc.)."""

    contact_id: UUID
    contact_name: str
    date_type: str  # birthday, anniversary, etc.
    date: date
    days_until: int


class ContactListResponse(BaseModel):
    """Paginated list of contacts."""

    contacts: list[ContactSummary]
    total: int


class GroupListResponse(BaseModel):
    """Paginated list of groups."""

    groups: list[Group]
    total: int


class ActivityFeedItem(BaseModel):
    """A single entry in a contact's activity feed."""

    id: UUID
    contact_id: UUID
    action: str
    details: dict = Field(default_factory=dict)
    created_at: datetime


class ContactsSyncTriggerResponse(BaseModel):
    """Response payload for manual contacts sync trigger."""

    provider: str = "google"
    mode: str
    created: int | None = None
    updated: int | None = None
    skipped: int | None = None
    errors: int | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
