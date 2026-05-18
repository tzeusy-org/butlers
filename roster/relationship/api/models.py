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

    ``last_interaction_at`` is the timestamp of the most recent interaction
    fact for this contact, or None if no interactions have been recorded.
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
    last_interaction_at: datetime | None = None


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

    Provenance fields (``src``, ``conf``, ``last_seen``, ``weight``,
    ``verified``, ``primary``) are always present per the Provenance contract
    (spec §"Provenance contract — every fact carries its origin").  The legacy
    ``facts`` table does not carry these columns, so they are returned as
    explicit nulls (or False/default for boolean fields).  ``src`` is set to
    the static literal ``'memory_module_legacy'`` to indicate the origin.
    """

    id: UUID
    content: str
    emotion: str | None = None
    created_at: datetime | None = None
    # Provenance contract fields (spec §"Provenance contract").
    src: str = "memory_module_legacy"
    conf: float | None = None
    last_seen: datetime | None = None
    weight: float | None = None
    verified: bool = False
    primary: bool = False


class EntityInteraction(BaseModel):
    """An interaction fact for an entity (predicate LIKE 'interaction_%').

    ``type`` is the predicate suffix (e.g. 'meeting' from 'interaction_meeting').
    ``direction`` and ``group_size`` are sparse and rendered as null when absent.

    Provenance fields are always present per the Provenance contract.
    The legacy ``facts`` table does not carry these columns; they are explicit
    nulls / defaults.  ``src`` is ``'memory_module_legacy'``.
    """

    id: UUID
    type: str
    summary: str | None = None
    occurred_at: datetime | None = None
    direction: str | None = None
    group_size: str | None = None
    # Provenance contract fields (spec §"Provenance contract").
    src: str = "memory_module_legacy"
    conf: float | None = None
    last_seen: datetime | None = None
    weight: float | None = None
    verified: bool = False
    primary: bool = False


class EntityGift(BaseModel):
    """A gift fact for an entity (predicate='gift').

    ``description`` maps to ``fact.content``.
    ``occasion`` and ``status`` are sparse metadata fields.
    ``created_at`` maps to ``fact.created_at``.

    Provenance fields are always present per the Provenance contract.
    The legacy ``facts`` table does not carry these columns; they are explicit
    nulls / defaults.  ``src`` is ``'memory_module_legacy'``.
    """

    id: UUID
    description: str | None = None
    occasion: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    # Provenance contract fields (spec §"Provenance contract").
    src: str = "memory_module_legacy"
    conf: float | None = None
    last_seen: datetime | None = None
    weight: float | None = None
    verified: bool = False
    primary: bool = False


class EntityLoan(BaseModel):
    """A loan fact for an entity (predicate='loan').

    ``description`` maps to ``fact.content``.
    All other fields are sparse metadata rendered as null when absent.
    ``amount_cents`` and ``currency`` are kept as strings (raw metadata).

    Provenance fields are always present per the Provenance contract.
    The legacy ``facts`` table does not carry these columns; they are explicit
    nulls / defaults.  ``src`` is ``'memory_module_legacy'``.
    """

    id: UUID
    description: str | None = None
    amount_cents: str | None = None
    currency: str | None = None
    direction: str | None = None
    settled: str | None = None
    settled_at: str | None = None
    created_at: datetime | None = None
    # Provenance contract fields (spec §"Provenance contract").
    src: str = "memory_module_legacy"
    conf: float | None = None
    last_seen: datetime | None = None
    weight: float | None = None
    verified: bool = False
    primary: bool = False


class EntityTimelineItem(BaseModel):
    """A single entry in an entity's unified timeline.

    ``kind`` identifies the predicate family:
    ``note``, ``interaction``, ``gift``, ``loan``, ``life_event``,
    ``dunbar_tier_override``.

    ``metadata`` is the raw JSONB dict from the facts table.
    Sparse fields are rendered as null — never omitted.

    Provenance fields are always present per the Provenance contract.
    The legacy ``facts`` table does not carry these columns; they are explicit
    nulls / defaults.  ``src`` is ``'memory_module_legacy'``.
    """

    kind: str
    id: UUID
    content: str | None = None
    valid_at: datetime | None = None
    predicate: str
    metadata: dict | None = None
    # Provenance contract fields (spec §"Provenance contract").
    src: str = "memory_module_legacy"
    conf: float | None = None
    last_seen: datetime | None = None
    weight: float | None = None
    verified: bool = False
    primary: bool = False


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


# ---------------------------------------------------------------------------
# Entity list models (GET /entities — index + filter + pagination)
# ---------------------------------------------------------------------------


class EntitySummary(BaseModel):
    """Compact entity representation for the index list view.

    ``tier`` is the pinned Dunbar tier override (from a ``dunbar_tier_override``
    fact), or ``None`` when the entity has no pinned tier (rank-based assignment).
    ``last_seen`` is the most-recent ``last_seen`` timestamp across all of the
    entity's facts in ``relationship.entity_facts``, or ``None`` when no facts exist.
    ``contact_fact_count`` is the count of active contact-type facts
    (``has-email | has-phone | has-handle | has-address | has-birthday |
    has-website``) in ``relationship.entity_facts`` for this entity.
    """

    id: UUID
    canonical_name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    tier: int | None = None
    last_seen: datetime | None = None
    contact_fact_count: int = 0
    created_at: datetime
    updated_at: datetime


class EntityListResponse(BaseModel):
    """Paginated response for GET /entities.

    ``total`` is the total count of entities matching the active filters
    (before pagination).  Clients use ``offset + len(items)`` to determine
    whether a next page exists.
    """

    items: list[EntitySummary]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Entity neighbours models (entity-redesign Phase 2, bu-4wn79)
# ---------------------------------------------------------------------------


class NeighbourEntry(BaseModel):
    """A single neighbour entity reached via a relational triple.

    ``entity_id`` is the UUID of the OTHER entity (not the queried one).
    ``direction`` is ``'forward'`` when the queried entity is the subject
    (queried → neighbour) and ``'reverse'`` when it is the object
    (neighbour → queried).

    Provenance fields are included per the Provenance contract in
    ``specs/dashboard-relationship/spec.md`` — all six fields are always
    present; nullable fields are explicit nulls when absent from the triple.
    """

    entity_id: UUID
    direction: Literal["forward", "reverse"]
    src: str
    conf: float
    last_seen: datetime | None = None
    weight: int | None = None
    verified: bool
    primary: bool | None = None


class NeighboursResponse(BaseModel):
    """Response for GET /entities/{id}/neighbours.

    ``neighbours`` maps each relational predicate to the list of neighbour
    entries reachable via that predicate.  Only ``kind='relational'``
    predicates from ``relationship.entity_predicate_registry`` are included.

    Example::

        {
          "neighbours": {
            "knows": [NeighbourEntry, ...],
            "family-of": [NeighbourEntry, ...]
          }
        }
    """

    neighbours: dict[str, list[NeighbourEntry]]


# ---------------------------------------------------------------------------
# Entity search models (entity-redesign Phase 2, bu-q9uiw)
# ---------------------------------------------------------------------------


class SearchResultEntry(BaseModel):
    """A single entity search result with its match score and match kind.

    ``entity_id`` is the UUID of the matching entity.
    ``canonical_name`` is the entity's canonical name.
    ``score`` is the ranking score:
      - 100  prefix match on name or alias
      - 70   substring match on a contact-fact value
      - 50   substring match on name or alias
      - 30   substring match on a predicate label
    ``match_kind`` indicates which rule produced the highest score:
      - ``prefix``       — query is a prefix of the name or an alias
      - ``contact_fact`` — query matches a contact-fact object value
      - ``substring``    — query is a substring of the name or an alias
      - ``predicate``    — query matches a predicate label
    """

    entity_id: UUID
    canonical_name: str
    score: int
    match_kind: Literal["prefix", "contact_fact", "substring", "predicate"]


class SearchResponse(BaseModel):
    """Response for GET /entities/search.

    ``results`` is ordered by score descending (highest first).  Ties are
    broken deterministically by entity UUID (stable sort).  The response
    contains at most ``limit`` items (default 20, max 50).
    """

    results: list[SearchResultEntry]
    total: int
    q: str
    limit: int


# ---------------------------------------------------------------------------
# Entity curation queue models (entity-redesign Phase 2, bu-t1zfd)
# ---------------------------------------------------------------------------


class QueueEntry(BaseModel):
    """A single entry in the curation queue.

    Each entry identifies one entity that needs operator attention and records
    which bucket sourced it plus structured evidence explaining why.

    ``bucket`` is one of:

    - ``'unidentified'`` — entity has ``metadata->>'unidentified' = 'true'``.
    - ``'duplicate-candidate'`` — entity shares a contact-fact value (email or
      phone) with at least one other entity (deterministic SQL; no LLM).
    - ``'stale'`` — entity has no active facts in ``relationship.entity_facts`` with
      ``last_seen`` within the past 365 days.

    ``evidence`` carries bucket-specific detail:

    - ``unidentified`` — ``{}`` (no additional evidence needed).
    - ``duplicate-candidate`` — ``{"predicate": "<has-email|has-phone>",
      "shared_value": "<value>", "peer_entity_ids": ["<uuid>", ...]}``.
    - ``stale`` — ``{"last_seen": "<iso-datetime>|null"}`` (last fact timestamp
      or ``null`` if no facts exist at all).

    ``last_seen`` is the most-recent ``last_seen`` across all active
    ``relationship.entity_facts`` rows for the entity, or ``null`` when none exist.
    """

    entity_id: UUID
    canonical_name: str
    entity_type: str
    bucket: Literal["unidentified", "duplicate-candidate", "stale"]
    evidence: dict[str, Any]
    last_seen: datetime | None = None


class QueueResponse(BaseModel):
    """Paginated response for GET /entities/queue.

    Section ordering inside ``items`` (per spec §1): Unidentified first,
    then Duplicate-candidate, then Stale.  Within each bucket entries are
    ordered by ``canonical_name ASC`` so the queue is stable across calls.

    ``total`` is the total number of queue entries before pagination (across
    all three buckets, post-deduplication by entity_id within each bucket).
    ``limit`` and ``offset`` echo the request parameters.
    """

    items: list[QueueEntry]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Entity concentration models (entity-redesign Phase 2, bu-0vosj)
# ---------------------------------------------------------------------------


class PredicateTab(BaseModel):
    """A predicate tab enumerated from ``relationship.entity_predicate_registry``.

    Only predicates with ``kind='relational'`` are surfaced as concentration
    tabs (contact predicates like ``has-email`` do not produce meaningful
    weight aggregations for the balance-sheet view).

    ``predicate`` is the stable identifier (e.g. ``'knows'``).
    ``label`` is the human-readable display label (e.g. ``'Knows'``).
    ``description`` is an optional long-form description from the registry.
    """

    predicate: str
    label: str
    description: str | None = None


class ConcentrationEntry(BaseModel):
    """One row in the concentration balance-sheet for a given predicate.

    ``entity_id`` identifies the entity being aggregated.
    ``canonical_name`` is the entity's display name.
    ``weight_sum`` is the sum of ``weight`` values across all active triples
    for this entity and predicate (NULLs are treated as 1 per triple so that
    every edge contributes to the aggregate).
    ``fact_count`` is the raw count of active triples (before weight).
    ``share`` is ``weight_sum / total_weight_sum`` (0.0–1.0); ``null`` when
    ``total_weight_sum = 0``.
    ``last_seen`` is the most-recent ``last_seen`` across all contributing
    triples, or ``null`` if none carry a timestamp.

    Provenance fields are included per the Provenance contract in
    ``specs/dashboard-relationship/spec.md`` — all six fields are always
    present; nullable fields are explicit nulls when absent.  For aggregated
    rows, provenance is taken from the most-recent contributing triple (the
    same triple whose ``last_seen`` surfaces above).
    """

    entity_id: UUID
    canonical_name: str
    weight_sum: int
    fact_count: int
    share: float | None = None
    last_seen: datetime | None = None
    # Provenance from the most-recent contributing triple.
    src: str
    conf: float
    verified: bool
    primary: bool | None = None


class ConcentrationRollup(BaseModel):
    """Header rollup for the concentration page.

    ``total`` is the sum of all ``weight_sum`` values across all entities for
    the active predicate.
    ``top3_share`` is the combined share of the top-3 entities by weight
    (``top3_weight_sum / total``); ``null`` when ``total = 0`` or fewer than
    one entity exists.
    """

    total: int
    top3_share: float | None = None


class ConcentrationResponse(BaseModel):
    """Response for ``GET /entities/concentration?pred=<predicate>``.

    ``predicate`` echoes the active predicate (the one whose data is in
    ``items``).
    ``items`` is the sorted list of concentration entries (descending by
    ``weight_sum``, then ``canonical_name ASC`` for stability).
    ``rollup`` carries the header summary (total weight and top-3 share).
    ``predicate_tabs`` lists all relational predicates from the registry so
    the frontend can render the tab strip without a separate API call.
    ``total`` is the number of entities in ``items`` (no pagination — the
    concentration view is not paginated; the full ranking is returned).
    """

    predicate: str
    items: list[ConcentrationEntry]
    rollup: ConcentrationRollup
    predicate_tabs: list[PredicateTab]
    total: int


# ---------------------------------------------------------------------------
# POST /entities (promote unidentified) models (entity-redesign Phase 2, bu-pzp9m)
# ---------------------------------------------------------------------------


class InitialFact(BaseModel):
    """A single triple to be asserted via the central writer as part of a promote request.

    ``predicate`` must be registered in ``relationship.entity_predicate_registry``.
    ``object`` is the literal value (e.g. an email address) or entity UUID string.
    ``object_kind`` defaults to ``'literal'``; use ``'entity'`` for relational triples.
    ``conf`` defaults to ``1.0`` (owner-authored).
    ``primary`` marks whether this is the primary value for the predicate kind.
    """

    predicate: str
    object: str
    object_kind: str = "literal"
    conf: float = Field(default=1.0, ge=0.0, le=1.0)
    primary: bool | None = None


class PromoteEntityRequest(BaseModel):
    """Request body for POST /entities (promote unidentified → canonical entity).

    ``entity_id`` identifies an existing ``public.entities`` row to promote.
    When provided the row is updated in-place: ``canonical_name`` is set,
    ``metadata->>'unidentified'`` is cleared, and optional ``entity_type`` /
    ``roles`` fields are applied.

    When ``entity_id`` is ``None`` a brand-new canonical entity is created
    (the "create" path).  Either way the caller must be an owner-role entity
    (Amendment 12a owner-only gate).

    ``initial_facts`` is an optional list of contact-fact triples to emit via
    the central writer (``relationship_assert_fact``) as part of the same
    transaction.  Each entry must carry ``predicate``, ``object``, and
    optionally ``object_kind`` (default ``'literal'``), ``conf`` (default
    ``1.0``), ``primary`` (default ``None``).  Predicates must exist in
    ``relationship.entity_predicate_registry``; an unregistered predicate causes the
    whole request to fail with HTTP 422.
    """

    entity_id: UUID | None = Field(
        default=None,
        description=(
            "UUID of an existing unidentified entity to promote.  "
            "When None a fresh canonical entity is created."
        ),
    )
    canonical_name: str = Field(..., min_length=1, description="Human-readable canonical name.")
    entity_type: str = Field(default="person", description="Entity type (person, organization, …).")
    roles: list[str] | None = Field(
        default=None,
        description="Role tags to assign (e.g. ['owner']).  None leaves roles unchanged.",
    )
    initial_facts: list[InitialFact] = Field(
        default_factory=list,
        description="Contact-fact triples to emit via relationship_assert_fact in the same tx.",
    )


# ---------------------------------------------------------------------------
# Entity contacts models (entity-redesign Phase 2, bu-u1w78)
# ---------------------------------------------------------------------------


class ContactFact(BaseModel):
    """One contact-fact triple returned by ``GET /entities/{id}/contacts``.

    ``id`` is the fact UUID in ``relationship.entity_facts``.
    ``predicate`` is the contact predicate (e.g. ``has-email``, ``has-phone``).
    ``object`` is the contact-fact value (e.g. an email address or phone number).
    ``value_hash`` is SHA-256[:16] of the object value, used as the stable
    URL-safe identifier in DELETE paths.

    Provenance fields (``src``, ``conf``, ``last_seen``, ``weight``,
    ``verified``, ``primary``) are always included per the Provenance contract
    (spec §"Provenance contract — every fact carries its origin").
    """

    id: UUID
    predicate: str
    object: str
    value_hash: str
    src: str
    conf: float
    last_seen: datetime | None = None
    weight: int | None = None
    verified: bool
    primary: bool | None = None


class ContactsResponse(BaseModel):
    """Response for ``GET /entities/{id}/contacts``.

    ``facts`` is a flat list of contact-fact triples (all active
    ``has-*`` predicates for the entity).  The list is ordered by
    ``predicate ASC, primary DESC NULLS LAST, created_at DESC``.
    """

    facts: list[ContactFact]


class AddContactRequest(BaseModel):
    """Request body for ``POST /entities/{id}/contacts``.

    ``predicate`` must be a registered ``has-*`` contact predicate.
    ``value`` is the contact object value (e.g. ``"alice@example.com"``).
    ``src`` defaults to ``"relationship"`` when omitted.
    ``verified`` defaults to False.
    ``primary`` is optional; used to mark one entry as the primary of its kind.
    ``conf`` defaults to 1.0.
    """

    predicate: str
    value: str
    src: str = "relationship"
    verified: bool = False
    primary: bool | None = None
    conf: float = Field(default=1.0, ge=0.0, le=1.0)


class AddContactResponse(BaseModel):
    """Response for ``POST /entities/{id}/contacts``.

    On success, returns the resulting fact (new or updated).
    ``outcome`` is one of ``inserted``, ``unchanged``, ``superseded``, or
    ``pending_approval``.  When ``outcome == 'pending_approval'``, ``fact``
    is ``None`` and ``action_id`` carries the pending-actions row UUID;
    the HTTP status is 202.
    """

    outcome: str
    fact: ContactFact | None = None
    action_id: UUID | None = None


class DeleteContactResponse(BaseModel):
    """Response for ``DELETE /entities/{id}/contacts/{pred}/{valueHash}``.

    ``deleted`` is always True on success (the fact was retracted).
    ``fact_id`` is the UUID of the retracted row.
    """

    deleted: bool
    fact_id: UUID


# ---------------------------------------------------------------------------
# Entity merge models (entity-redesign Phase 2, bu-jp6r6)
# ---------------------------------------------------------------------------


class MergeEntitiesRequest(BaseModel):
    """Request body for ``POST /entities/{id}/merge``.

    Merges two entities by rewiring all ``relationship.entity_facts`` triples from the
    source entity to the target entity, then tombstoning the source.

    ``entityA`` and ``entityB`` are the two entity UUIDs to merge.
    ``keepAs`` selects which entity survives: ``'A'`` keeps ``entityA``,
    ``'B'`` keeps ``entityB``.  The other entity is the source (tombstoned).

    The `id` path parameter is ignored for routing purposes; the canonical
    request body carries both entity IDs.
    """

    entityA: UUID = Field(..., description="UUID of entity A.")
    entityB: UUID = Field(..., description="UUID of entity B.")
    keepAs: Literal["A", "B"] = Field(
        ...,
        description="Which entity to keep: 'A' survives, 'B' is tombstoned — or vice versa.",
    )


class MergeEntitiesResponse(BaseModel):
    """Response for ``POST /entities/{id}/merge``.

    ``kept_entity_id`` is the UUID of the surviving entity.
    ``tombstoned_entity_id`` is the UUID of the entity that was merged away.
    ``subject_facts_rewired`` is the count of ``relationship.entity_facts`` rows whose
    ``subject`` column was updated from source to target.
    ``object_facts_rewired`` is the count of ``relationship.entity_facts`` rows whose
    ``object`` column was updated (where ``object_kind='entity'``).
    """

    kept_entity_id: UUID
    tombstoned_entity_id: UUID
    subject_facts_rewired: int
    object_facts_rewired: int


# ---------------------------------------------------------------------------
# Entity promote-tier models (entity-redesign Phase 2, bu-wmigz)
# ---------------------------------------------------------------------------

#: Valid Dunbar tier values (must stay in sync with dunbar.py::VALID_TIERS).
_VALID_PROMOTE_TIERS: frozenset[int] = frozenset({5, 15, 50, 150, 500, 1500})


class PromoteTierRequest(BaseModel):
    """Request body for ``POST /entities/{id}/promote-tier``.

    Writes a ``dunbar_tier_override`` triple to ``relationship.entity_facts`` via
    the central writer (``relationship_assert_fact()``).

    Per Amendment 6, tier promotion is a FACT not a column — this endpoint
    MUST NOT write to ``public.entities.tier``.

    ``tier`` must be one of the six canonical Dunbar layer sizes:
    5, 15, 50, 150, 500, or 1500.
    """

    tier: int = Field(
        ...,
        description=("Dunbar tier to assign.  Must be one of: 5, 15, 50, 150, 500, 1500."),
    )


class PromoteTierResponse(BaseModel):
    """Response for ``POST /entities/{id}/promote-tier``.

    ``outcome`` is one of ``'inserted'``, ``'superseded'``, ``'unchanged'``,
    or ``'pending_approval'``.

    ``fact_id`` is the UUID of the active ``dunbar_tier_override`` row in
    ``relationship.entity_facts`` (None for ``pending_approval`` outcomes).

    ``action_id`` is set only when ``outcome='pending_approval'`` (owner-entity
    carve-out per RFC 0017 §2.3).
    """

    entity_id: UUID
    tier: int
    outcome: Literal["inserted", "superseded", "unchanged", "pending_approval"]
    fact_id: UUID | None = None
    action_id: UUID | None = None


# ---------------------------------------------------------------------------
# Entity queue-dismiss models (entity-redesign Phase 2, bu-297lj)
# ---------------------------------------------------------------------------


class DismissQueueRequest(BaseModel):
    """Request body for ``POST /entities/queue/dismiss``.

    Dismisses a single entity from the curation queue by writing a
    ``queue.dismissed`` state-marker triple via the central writer
    ``relationship_assert_fact()``.
    """

    entity_id: UUID


class DismissQueueItemResult(BaseModel):
    """Per-entity result within a ``DismissQueueResponse``.

    ``entity_id`` is the dismissed entity's UUID.
    ``outcome`` is the outcome returned by ``relationship_assert_fact()``:
    ``'inserted'`` (first dismiss), ``'unchanged'`` (already dismissed),
    ``'superseded'`` (provenance changed), or ``'pending_approval'`` (owner
    entity carve-out — write parked for human approval).
    ``action_id`` is populated only when ``outcome='pending_approval'``.
    ``fact_id`` is the UUID of the now-active ``queue.dismissed`` triple, or
    ``None`` for ``pending_approval``.
    """

    entity_id: UUID
    outcome: str
    fact_id: UUID | None = None
    action_id: UUID | None = None


class DismissQueueResponse(BaseModel):
    """Response for ``POST /entities/queue/dismiss``.

    ``dismissed`` lists per-entity outcomes (always a single entry for
    single-entity requests).
    ``status`` is ``'ok'`` on full success, or ``'pending_approval'`` when
    the carve-out was triggered for the subject entity.
    """

    dismissed: list[DismissQueueItemResult]
    status: str


# ---------------------------------------------------------------------------
# Entity activity aggregator models (entity-redesign Phase 2, bu-ihiw4)
# ---------------------------------------------------------------------------


class ActivityEntry(BaseModel):
    """A single entry in the entity activity stream.

    The ``src`` field discriminates the origin:

    - ``'relationship'`` — sourced from ``relationship.facts`` (notes,
      interactions, gifts, loans, life events, dunbar_tier_override, etc.).
    - ``'chronicler'`` — sourced via the chronicler MCP tool
      ``chronicler_list_episodes``.

    Fields present for ``src='relationship'`` rows:
    - ``id`` — fact UUID from ``relationship.facts``
    - ``ts`` — ``last_seen`` of the fact (falls back to ``created_at``)
    - ``kind`` — predicate family (e.g. ``'note'``, ``'interaction'``, ``'gift'``)
    - ``predicate`` — the raw predicate string

    Fields present for ``src='chronicler'`` rows:
    - ``id`` — episode UUID from the chronicler
    - ``ts`` — ``canonical_start_at`` from the corrected episode
    - ``kind`` — always ``'episode'``
    - ``episode_id`` — same as ``id`` (kept for explicit episode-typed access)
    - ``summary`` — ``canonical_title`` from the corrected episode

    Fields absent in a given row are ``None``.
    """

    id: UUID
    ts: datetime | None = None
    kind: str
    src: Literal["relationship", "chronicler"]
    # relationship-only
    predicate: str | None = None
    # chronicler-only
    episode_id: UUID | None = None
    summary: str | None = None


class ActivityResponse(BaseModel):
    """Response for ``GET /entities/{id}/activity``.

    ``items`` is a merged, timestamp-descending stream of relationship facts
    and chronicler episodes for the given entity.  Each entry carries a
    ``src`` field so clients can distinguish the origin.

    ``total`` is the total number of items across both sources before
    pagination (relationship_count + chronicler_count).
    ``limit`` and ``offset`` echo the request parameters.
    """

    items: list[ActivityEntry]
    total: int
    limit: int
    offset: int
