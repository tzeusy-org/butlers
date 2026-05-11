"""Pydantic models for the relationship/CRM API.

Provides models for contacts, groups, labels, notes, interactions,
gifts, loans, and upcoming dates used by the relationship butler's
dashboard endpoints.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
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
    parent_id: UUID | None = None
    context: str | None = None  # personal | work | other | None (unclassified)


class ContactSummary(BaseModel):
    """Compact contact representation for list views.

    The ``warmth`` field is a recency/frequency blend scored 0.0–1.0:

        recency_score  = max(0, 1 - days_since_last_contact / tier_cadence_days)
        frequency_score = min(1, interactions_in_last_30d / tier_target_per_30d)
        warmth = 0.6 * recency_score + 0.4 * frequency_score

    where ``tier_cadence_days`` and ``tier_target_per_30d`` are derived from
    the contact's Dunbar tier (TIER_CADENCE in the dunbar engine).
    ``tier_target_per_30d = 30 / tier_cadence_days`` (expected touches per
    30-day window).  A None value means warmth was not computed for this
    request (e.g. bulk list without Dunbar context).
    """

    id: UUID
    full_name: str
    first_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None
    email: str | None = None
    phone: str | None = None
    labels: list[Label] = Field(default_factory=list)
    last_interaction_at: datetime | None = None
    warmth: float | None = None


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
    preferred_channel: str | None = None


class ContactPatchRequest(BaseModel):
    """Request body for PATCH /contacts/{id}.

    All fields are optional; only provided fields are updated.
    ``roles`` is the sole write path for role assignment.
    """

    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None
    company: str | None = None
    job_title: str | None = None
    roles: list[str] | None = None
    preferred_channel: str | None = None


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

    entity_id: UUID | None = None
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
    parent_id: UUID | None = None
    context: str | None = None  # personal | work | other | None (unclassified)


class PatchContactInfoRequest(BaseModel):
    """Request body for PATCH /contacts/{id}/contact-info/{info_id}.

    All fields are optional; only provided fields are updated.
    """

    type: str | None = None
    value: str | None = None
    is_primary: bool | None = None
    context: str | None = None  # personal | work | other | None (unclassified)


class CreateContactInfoResponse(BaseModel):
    """Response for POST /contacts/{id}/contact-info."""

    id: UUID
    contact_id: UUID
    type: str
    value: str
    is_primary: bool
    secured: bool
    parent_id: UUID | None = None
    context: str | None = None  # personal | work | other | None (unclassified)


class Group(BaseModel):
    """A named group of contacts."""

    id: UUID
    name: str
    description: str | None = None
    member_count: int = 0
    labels: list[Label] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


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


class ContactsSyncTriggerResponse(BaseModel):
    """Response payload for manual contacts sync trigger."""

    provider: str = "google"
    mode: str
    fetched: int | None = None
    applied: int | None = None
    skipped: int | None = None
    deleted: int | None = None
    provider_total: int | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None


# ---------------------------------------------------------------------------
# Unlinked contacts / entity disambiguation
# ---------------------------------------------------------------------------


class EntitySuggestion(BaseModel):
    """A candidate entity that might match an unlinked contact."""

    entity_id: UUID
    canonical_name: str
    entity_type: str
    score: float
    name_match: str
    aliases: list[str] = Field(default_factory=list)


class UnlinkedContactSummary(BaseModel):
    """Compact view of a contact that has no entity_id linked."""

    id: UUID
    full_name: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    suggestions: list[EntitySuggestion] = Field(default_factory=list)


class UnlinkedContactsResponse(BaseModel):
    """Paginated list of unlinked contacts with pre-computed suggestions."""

    contacts: list[UnlinkedContactSummary]
    total: int


class LinkEntityRequest(BaseModel):
    """Request body for POST /contacts/{id}/link-entity."""

    entity_id: UUID


class LinkEntityResponse(BaseModel):
    """Response for POST /contacts/{id}/link-entity."""

    contact_id: UUID
    entity_id: UUID


class CreateAndLinkEntityRequest(BaseModel):
    """Request body for POST /contacts/{id}/create-entity.

    All fields are optional — defaults are inferred from the contact record.
    """

    canonical_name: str | None = None
    entity_type: str = "person"
    aliases: list[str] | None = None
    metadata: dict[str, Any] | None = None


class CreateAndLinkEntityResponse(BaseModel):
    """Response for POST /contacts/{id}/create-entity."""

    contact_id: UUID
    entity_id: UUID
    canonical_name: str


# ---------------------------------------------------------------------------
# Entity info models
# ---------------------------------------------------------------------------


class EntityInfoEntry(BaseModel):
    """A single entity_info row for an entity.

    The ``value`` field is set to ``None`` when ``secured=True`` and the
    caller has not been granted reveal access (masked in list views).
    Use GET /entities/{id}/secrets/{info_id} to retrieve the real value.
    """

    id: UUID
    type: str
    value: str | None  # None means masked (secured=True)
    label: str | None = None
    is_primary: bool = False
    secured: bool = False


class EntityDetail(BaseModel):
    """Full entity record with entity_info entries."""

    id: UUID
    canonical_name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    entity_info: list[EntityInfoEntry] = Field(default_factory=list)


class CreateEntityInfoRequest(BaseModel):
    """Request body for POST /entities/{id}/info."""

    type: str
    value: str
    label: str | None = None
    is_primary: bool = False
    secured: bool = False


class UpdateEntityInfoRequest(BaseModel):
    """Request body for PATCH /entities/{id}/info/{info_id}.

    All fields are optional; only provided fields are updated.
    """

    type: str | None = None
    value: str | None = None
    label: str | None = None
    is_primary: bool | None = None


class CreateEntityInfoResponse(BaseModel):
    """Response for POST /entities/{id}/info."""

    id: UUID
    entity_id: UUID
    type: str
    value: str
    label: str | None = None
    is_primary: bool
    secured: bool


class OwnerEntityInfoResponse(BaseModel):
    """Response for GET /owner/entity-info — all entity_info entries for the owner entity."""

    entity_id: UUID
    entity_name: str
    entries: list[EntityInfoEntry] = Field(default_factory=list)


class DunbarEntry(BaseModel):
    """Dunbar tier ranking entry for a single contact/entity.

    The ``warmth`` field uses the same formula as ``ContactSummary.warmth``
    (see docstring there) but is always computed in the ranking endpoint since
    Dunbar tier context is already available.
    """

    contact_id: UUID
    entity_id: UUID
    canonical_name: str
    dunbar_tier: int
    dunbar_score: float
    dunbar_tier_override: bool
    avatar_url: str | None = None
    aliases: list[str] = Field(default_factory=list)
    warmth: float | None = None


class DunbarRankingResponse(BaseModel):
    """Response for GET /dunbar/ranking."""

    entries: list[DunbarEntry]
    owner_entity_id: UUID | None = None


# ---------------------------------------------------------------------------
# Entity-level tab API models (entity-keyed, facts-based)
# ---------------------------------------------------------------------------


class EntityNote(BaseModel):
    """A note fact for an entity (predicate='contact_note').

    ``created_at`` is mapped from ``fact.valid_at``.
    ``emotion`` is sparse — rendered as null when the metadata key is absent.
    """

    id: UUID
    content: str
    emotion: str | None = None
    created_at: datetime | None = None


class EntityInteraction(BaseModel):
    """An interaction fact for an entity (predicate LIKE 'interaction_%').

    ``type`` is the predicate suffix (e.g. 'meeting' from 'interaction_meeting').
    ``direction`` and ``group_size`` are sparse and rendered as null when absent.
    """

    id: UUID
    type: str
    summary: str | None = None
    occurred_at: datetime | None = None
    direction: str | None = None
    group_size: str | None = None


class EntityGift(BaseModel):
    """A gift fact for an entity (predicate='gift').

    ``description`` maps to ``fact.content``.
    ``occasion`` and ``status`` are sparse metadata fields.
    ``created_at`` maps to ``fact.created_at``.
    """

    id: UUID
    description: str | None = None
    occasion: str | None = None
    status: str | None = None
    created_at: datetime | None = None


class EntityLoan(BaseModel):
    """A loan fact for an entity (predicate='loan').

    ``description`` maps to ``fact.content``.
    All other fields are sparse metadata rendered as null when absent.
    ``amount_cents`` and ``currency`` are kept as strings (raw metadata).
    """

    id: UUID
    description: str | None = None
    amount_cents: str | None = None
    currency: str | None = None
    direction: str | None = None
    settled: str | None = None
    settled_at: str | None = None
    created_at: datetime | None = None


class EntityTimelineItem(BaseModel):
    """A single entry in an entity's unified timeline.

    ``kind`` identifies the predicate family:
    ``note``, ``interaction``, ``gift``, ``loan``, ``life_event``,
    ``dunbar_tier_override``.

    ``metadata`` is the raw JSONB dict from the facts table.
    Sparse fields are rendered as null — never omitted.
    """

    kind: str
    id: UUID
    content: str | None = None
    valid_at: datetime | None = None
    predicate: str
    metadata: dict | None = None


class LinkedContactSummary(BaseModel):
    """A contact linked to an entity, for the entity detail page linked-contacts section."""

    id: UUID
    full_name: str
    email: str | None = None
    phone: str | None = None


class EntityImportantDate(BaseModel):
    """A row from public.important_dates scoped to one of an entity's contacts.

    ``upcoming_date`` is computed by the API: the next occurrence of (month, day)
    on or after the request date. Same shape contract as the global
    ``GET /upcoming-dates`` response.
    """

    contact_id: UUID
    contact_name: str
    label: str
    month: int
    day: int
    year: int | None = None
    upcoming_date: date


class DunbarTierOverrideRequest(BaseModel):
    """Body for PATCH /entities/{id}/dunbar-tier.

    ``tier`` must be one of the canonical Dunbar layer sizes (5, 15, 50, 150,
    500, 1500) or ``None`` to clear an existing pin.
    """

    tier: int | None = Field(
        default=None,
        description=(
            "One of 5, 15, 50, 150, 500, 1500 to pin the entity to that tier, "
            "or null to clear the pin and revert to rank-based assignment."
        ),
    )


class DunbarTierOverrideResponse(BaseModel):
    """Response envelope for PATCH /entities/{id}/dunbar-tier."""

    entity_id: UUID
    contact_id: UUID
    tier: int | None = None
    action: str
    message: str


class MessageThreadSummary(BaseModel):
    """One row of incoming/outgoing message activity for an entity.

    Aggregates rows from ``switchboard.message_inbox`` whose
    ``request_context ->> 'source_sender_identity'`` matches one of the
    contact identifiers (email, phone, telegram chat id) attached to the
    entity's linked contacts. One row per ``(source_channel, thread_identity)``.

    Returns an empty list when the switchboard pool is not registered, or
    when no identifiers match — graceful degrade.
    """

    source_channel: str | None = None
    thread_identity: str | None = None
    sender_identity: str | None = None
    message_count: int
    last_received_at: datetime | None = None
    last_direction: str | None = None
    last_snippet: str | None = None


# ---------------------------------------------------------------------------
# Contact interaction thread models
# ---------------------------------------------------------------------------


class ContactInteractionItem(BaseModel):
    """A single interaction event in a contact's chronological thread.

    ``direction`` discriminates the flow of the interaction:
    - ``'in'``      — contact reached out to the owner
    - ``'out'``     — owner reached out to the contact
    - ``'drafted'`` — a drafted but unsent message (LLM suggestion, etc.)

    ``text`` is the raw interaction content (summary or message body).
    Absent in the DB becomes an empty string, not null, to keep the
    consumer contract simple.
    """

    ts: datetime | None = None
    direction: Literal["in", "out", "drafted"] | None = None
    text: str


class ContactInteractionThreadResponse(BaseModel):
    """Response for GET /contacts/{contact_id}/interactions."""

    interactions: list[ContactInteractionItem]


# ---------------------------------------------------------------------------
# Overdue contacts models
# ---------------------------------------------------------------------------


class OverdueContactItem(BaseModel):
    """One overdue contact entry with cadence context.

    ``owed_days`` is the number of days past the effective threshold:
        owed_days = max(1, int(days_since_last_contact - target_cadence_days))

    Contacts with no recorded interactions are sorted at the top with a large
    sentinel value (never-contacted = highest urgency).
    ``last_contact_date`` is null for contacts with no recorded interactions.
    ``target_cadence_days`` is the effective cadence used for filtering (the
    shorter of the ``days`` query parameter and the contact's Dunbar tier
    cadence, or their explicit ``stay_in_touch_days`` override).
    """

    contact_id: UUID
    name: str
    tier: str
    owed_days: int
    last_contact_date: date | None = None
    target_cadence_days: int


class OverdueContactsResponse(BaseModel):
    """Response for GET /contacts/overdue."""

    contacts: list[OverdueContactItem]
