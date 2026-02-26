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


class ContactInfoEntry(BaseModel):
    """A single contact_info row for a contact.

    The ``value`` field is set to ``None`` when ``secured=True`` and the
    caller has not been granted reveal access (masked in list views).
    Use GET /contacts/{id}/secrets/{info_id} to retrieve the real value.
    """

    id: UUID
    type: str
    value: str | None  # None means masked (secured=True)
    is_primary: bool = False
    secured: bool = False


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
    """Full contact with all fields including identity fields."""

    notes: str | None = None
    birthday: date | None = None
    company: str | None = None
    job_title: str | None = None
    address: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    # Identity fields (added by contacts-identity-model migration)
    roles: list[str] = Field(default_factory=list)
    entity_id: UUID | None = None
    contact_info: list[ContactInfoEntry] = Field(default_factory=list)


class ContactPatchRequest(BaseModel):
    """Request body for PATCH /contacts/{id}.

    All fields are optional; only provided fields are updated.
    ``roles`` is the sole write path for role assignment.
    """

    full_name: str | None = None
    nickname: str | None = None
    company: str | None = None
    job_title: str | None = None
    roles: list[str] | None = None


class ContactMergeRequest(BaseModel):
    """Request body for POST /contacts/{id}/merge.

    Merges the temp contact identified by ``source_contact_id`` into
    the contact identified by the URL path parameter (target).
    """

    source_contact_id: UUID


class ContactMergeResponse(BaseModel):
    """Response for POST /contacts/{id}/merge."""

    target_contact_id: UUID
    source_contact_id: UUID
    contact_info_moved: int
    entity_merged: bool


class OwnerSetupStatus(BaseModel):
    """Response for GET /owner/setup-status."""

    contact_id: UUID | None = None
    has_name: bool
    has_telegram: bool
    has_telegram_chat_id: bool
    has_email: bool


class CreateContactInfoRequest(BaseModel):
    """Request body for POST /contacts/{id}/contact-info."""

    type: str
    value: str
    is_primary: bool = False
    secured: bool = False


class PatchContactInfoRequest(BaseModel):
    """Request body for PATCH /contacts/{id}/contact-info/{info_id}.

    All fields are optional; only provided fields are updated.
    """

    type: str | None = None
    value: str | None = None
    is_primary: bool | None = None


class CreateContactInfoResponse(BaseModel):
    """Response for POST /contacts/{id}/contact-info."""

    id: UUID
    contact_id: UUID
    type: str
    value: str
    is_primary: bool
    secured: bool


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
    occasion: str | None = None
    status: str = "idea"  # idea, purchased, wrapped, given, thanked
    created_at: datetime
    updated_at: datetime


class Loan(BaseModel):
    """A loan (money or item) between the user and a contact."""

    id: UUID
    contact_id: UUID
    amount: float
    direction: str  # "lent" or "borrowed"
    description: str | None = None
    settled: bool = False
    created_at: datetime
    settled_at: datetime | None = None


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
