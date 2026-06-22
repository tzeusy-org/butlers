"""Pydantic models for the relationship/CRM API.

Provides models for contacts, groups, labels, notes, interactions,
gifts, loans, and upcoming dates used by the relationship butler's
dashboard endpoints.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


def _reject_trusted_internal_src(v: str) -> str:
    """Reject ``src`` values that bypass the owner-entity approval gate.

    The trusted auto-apply set (owner-self sources plus trusted
    internal-derivation jobs) is reserved for internal daemon code paths; an
    external HTTP caller must never be able to supply one (bu-vj46x). The
    canonical set lives in the central writer so there is a single source of
    truth. Imported lazily to avoid an import cycle at module load.
    """
    from butlers.tools.relationship.relationship_assert_fact import _OWNER_AUTO_APPLY_SOURCES

    if v in _OWNER_AUTO_APPLY_SOURCES:
        raise ValueError(
            f"src={v!r} is a reserved internal source and cannot be set via the API. "
            "Trusted internal sources are reachable only from internal daemon code paths."
        )
    return v


class Label(BaseModel):
    """A label/tag that can be applied to contacts or groups."""

    id: UUID
    name: str
    color: str | None = None


class ContactInfoEntry(BaseModel):
    """A single contact_info row for a contact.

    The ``value`` field is set to ``None`` when ``secured=True`` and the
    caller has not been granted reveal access (masked in list views).
    Use GET /relationship/entities/{entity_id}/secrets/{info_id} to retrieve the real value.

    ``source`` discriminates the backing store for this entry:
    - ``None`` / absent — legacy row (default; omitted from serialised output
      for backward compatibility). No legacy rows are served by the current API
      — all entries from ``list_entity_linked_contacts`` carry
      ``source="entity_facts"``.
    - ``"entity_facts"`` — the entry was synthesised from a
      ``relationship.entity_facts`` has-* triple for the linked entity, or is
      a secured credential from ``public.entity_info``.

    For ``source="entity_facts"`` entries, ``predicate`` and ``value_hash``
    are populated to enable entity-keyed mutation (delete / retract).
    They are absent (``None``) for secured ``entity_info`` entries.
    """

    id: UUID
    type: str
    value: str | None  # None means masked (secured=True)
    is_primary: bool = False
    secured: bool = False
    parent_id: UUID | None = None
    context: str | None = None  # personal | work | other | None (unclassified)
    source: Literal["entity_facts"] | None = None
    # Populated only for source="entity_facts" entries — used by the frontend
    # to call DELETE /entities/{id}/contacts/{predicate}/{value_hash}.
    predicate: str | None = None
    value_hash: str | None = None
    # Owner-confirmed flag from relationship.entity_facts.verified.
    # False until the owner explicitly marks the channel verified.
    # Drives the amber unverified-dot in ContactChannelCard.
    verified: bool = False


class OwnerSetupStatus(BaseModel):
    """Response for GET /owner/setup-status."""

    entity_id: UUID | None = None
    has_name: bool
    has_telegram: bool
    has_telegram_chat_id: bool
    has_email: bool


class Group(BaseModel):
    """A named group of contacts."""

    id: UUID
    name: str
    description: str | None = None
    member_count: int = 0
    labels: list[Label] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Group label write models
# ---------------------------------------------------------------------------


class CreateLabelRequest(BaseModel):
    """Request body for POST /labels — create a new label."""

    name: str = Field(..., min_length=1, description="Unique label name.")
    color: str | None = Field(
        default=None,
        description="Optional display colour (e.g. '#ff6347').  None means no colour.",
    )


class CreateLabelResponse(BaseModel):
    """Response for POST /labels."""

    id: UUID
    name: str
    color: str | None = None


class AssignGroupLabelResponse(BaseModel):
    """Response for POST /groups/{group_id}/labels/{label_id}.

    ``assigned`` is True when the label was newly assigned; False when
    the assignment already existed (idempotent endpoint).
    """

    group_id: UUID
    label_id: UUID
    assigned: bool


class RemoveGroupLabelResponse(BaseModel):
    """Response for DELETE /groups/{group_id}/labels/{label_id}.

    ``removed`` is True when the assignment was deleted; False when
    no such assignment existed (idempotent endpoint).
    """

    group_id: UUID
    label_id: UUID
    removed: bool


class GroupLabelsResponse(BaseModel):
    """Response for GET /groups/{group_id}/labels — all labels on a group."""

    group_id: UUID
    labels: list[Label]


class UpcomingDate(BaseModel):
    """Upcoming important date (birthday, anniversary, etc.)."""

    contact_id: UUID
    contact_name: str
    date_type: str  # birthday, anniversary, etc.
    date: date
    days_until: int


class GroupListResponse(BaseModel):
    """Paginated list of groups."""

    groups: list[Group]
    total: int


# ---------------------------------------------------------------------------
# Entity info models
# ---------------------------------------------------------------------------


class EntityInfoEntry(BaseModel):
    """A single entity_info row for an entity.

    The ``value`` field is set to ``None`` when ``secured=True`` and the
    caller has not been granted reveal access (masked in list views).
    Use GET /relationship/entities/{entity_id}/secrets/{info_id} to retrieve the real value.
    """

    id: UUID
    type: str
    value: str | None  # None means masked (secured=True)
    label: str | None = None
    is_primary: bool = False
    secured: bool = False


class EntityDetail(BaseModel):
    """Full entity record with entity_info entries.

    ``state`` reflects the highest-priority curation bucket the entity matches,
    using the same classification logic as ``GET /entities/queue``:

    - ``'healthy'`` — no flags, has a recent fact within the past 365 days, no shared identifiers.
    - ``'unidentified'`` — ``metadata->>'unidentified' = 'true'``.
    - ``'duplicate-candidate'`` — ``metadata->>'duplicate_candidate' = 'true'`` OR shares a
      ``has-email`` / ``has-phone`` fact value with at least one other entity.
    - ``'stale'`` — no active ``relationship.entity_facts`` fact with ``last_seen`` within
      the past 365 days.

    Priority (highest to lowest): unidentified > duplicate-candidate > stale > healthy.

    ``state_evidence`` mirrors the ``evidence`` dict from the queue entry for non-healthy states:

    - ``unidentified`` — ``{}``
    - ``duplicate-candidate`` — ``{"predicate": ..., "shared_value": ...,
      "peer_entity_ids": [...]}`` or ``{}`` when flagged via metadata only (no shared fact).
    - ``stale`` — ``{"last_seen": "<iso-datetime>|null"}``
    - ``healthy`` — ``None``
    """

    id: UUID
    canonical_name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    entity_info: list[EntityInfoEntry] = Field(default_factory=list)
    state: Literal["healthy", "unidentified", "duplicate-candidate", "stale"] = "healthy"
    state_evidence: dict[str, Any] | None = None


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
    """A contact linked to an entity, for the entity detail page linked-contacts section.

    ``contact_info`` contains all non-secured ``relationship.entity_facts`` channel entries
    **plus** masked (value=None) ``public.entity_info`` secured=true rows for the entity.
    Secured rows carry ``secured=True``, ``value=None``, and ``source="entity_facts"``
    so the frontend can render a masked chip + reveal affordance routed to the entity-keyed
    reveal endpoint (GET /entities/{id}/secrets/{info_id}).

    Note: entity_facts and secured entity_info entries are entity-level (not per-contact).
    They are attached to the *first* linked contact only (by name order).  Other contacts
    in the same list will have an empty ``contact_info``.
    The entity-card renders channel chips from this list without needing a secondary
    ``getContact`` call per contact.

    ``labels`` mirrors the ``labels`` field on ``ContactDetail`` — the full list
    of label objects assigned to the contact.

    ``preferred_channel`` is the entity's active ``prefers-channel`` fact value
    (entity-keyed-preferred-channel). It is sourced from
    ``relationship.entity_facts`` (predicate ``prefers-channel``), NOT the
    orphaned ``public.contacts.preferred_channel`` CRM column, and is attached
    only to the first linked contact (entity-level, like ``contact_info``).

    ``reachable_channels`` is the deliverable channel set the entity has a
    contact fact for (``email``/``telegram`` proven by ``has-email`` /
    ``has-handle:telegram:`` facts). The dashboard channel-preference control
    offers only these channels. Mirrors group 1's reachability mapping; also
    attached to the first linked contact only.
    """

    id: UUID
    full_name: str
    email: str | None = None
    phone: str | None = None
    contact_info: list[ContactInfoEntry] = Field(default_factory=list)
    labels: list[Label] = Field(default_factory=list)
    preferred_channel: str | None = None
    reachable_channels: list[str] = Field(default_factory=list)


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
    """Response envelope for PATCH /entities/{id}/dunbar-tier.

    ``contact_id`` is ``null`` when the entity has no linked contact (contactless
    tier pin — override stored directly on the entity).
    """

    entity_id: UUID
    contact_id: UUID | None = None
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
# Entity list models (GET /entities — index + filter + pagination)
# ---------------------------------------------------------------------------


class EntitySummary(BaseModel):
    """Compact entity representation for the index list view.

    ``tier`` is the effective Dunbar tier: the rank-based computed tier for
    listed, entity-linked contacts (scored by interaction recency/frequency and
    bucketed into 5/15/50/150/500/1500, defaulting to 1500 with no interactions),
    with manual ``dunbar_tier_override`` facts taking priority. ``None`` only for
    entities outside the ranking with no pinned override (e.g. organizations,
    locations, or persons with no linked contact).
    ``last_seen`` is the most-recent ``last_seen`` timestamp across all of the
    entity's facts in ``relationship.entity_facts``, or ``None`` when no facts exist.
    ``first_seen`` is the earliest ``last_seen`` timestamp across all of the
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
    first_seen: datetime | None = None
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
    ``canonical_name`` is the neighbour entity's canonical name.
    ``direction`` is ``'forward'`` when the queried entity is the subject
    (queried → neighbour) and ``'reverse'`` when it is the object
    (neighbour → queried).

    Provenance fields are included per the Provenance contract in
    ``specs/dashboard-relationship/spec.md`` — all six fields are always
    present; nullable fields are explicit nulls when absent from the triple.
    """

    entity_id: UUID
    canonical_name: str
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

    When ranked truncation is requested (``rank=weight&per_predicate=N``), each
    group's list is the top-N neighbours by ``weight DESC`` and ``remainders``
    carries the count of unreturned neighbours per predicate (the "+N more"
    affordance for Hop / Columns).  Without those params ``remainders`` is empty
    and every group lists all neighbours (unchanged behaviour).

    Example (ranked, ``per_predicate=6``)::

        {
          "neighbours": {"knows": [<6 entries>]},
          "remainders": {"knows": 34}
        }
    """

    neighbours: dict[str, list[NeighbourEntry]]
    #: Per-predicate count of neighbours NOT returned in ``neighbours`` because
    #: of ranked truncation.  Empty (and omitted predicates mean zero remainder)
    #: when no truncation was applied.
    remainders: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Entity search models (entity-redesign Phase 2, bu-q9uiw)
# ---------------------------------------------------------------------------


class SearchResultEntry(BaseModel):
    """A single entity search result with its match score and match kind.

    ``entity_id`` is the UUID of the matching entity.
    ``canonical_name`` is the entity's canonical name.
    ``entity_type`` is the entity's type (person, organization, place, …).
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
    entity_type: str
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
    ``entity_count`` is the number of distinct subject entities with at least
    one active fact for this predicate.  Drives the per-tab population badge
    in the concentration UI.
    """

    predicate: str
    label: str
    description: str | None = None
    entity_count: int = 0


class ConcentrationTarget(BaseModel):
    """The object ("where") of a relational triple contributing to a row.

    For a ``works-at`` triple the subject is the person and the *target* is the
    organization they work at.  Each contributing triple surfaces one target so
    the UI can answer not just *who* has the predicate but *where it points*.

    ``object_kind`` mirrors ``relationship.entity_facts.object_kind``:
    ``'entity'`` (the object is another entity) or ``'literal'`` (a free-text
    value).  When ``'entity'``, ``entity_id`` is the target entity's UUID and
    ``name`` is its canonical name — the frontend renders this as a hyperlink to
    that entity's detail page.  When ``'literal'``, ``entity_id`` is ``None`` and
    ``name`` is the literal value (rendered as plain text).
    """

    name: str
    entity_id: UUID | None = None
    object_kind: str


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
    # Targets of this entity's active triples for the predicate (the "where" —
    # e.g. the organizations for a ``works-at`` row).  Empty for predicates that
    # produce no resolvable objects.
    targets: list[ConcentrationTarget] = Field(default_factory=list)


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

    Security: ``src`` values in the trusted auto-apply set (owner-self sources
    plus trusted internal-derivation jobs such as ``interaction_sync``) are
    rejected with a validation error — those source strings bypass the
    owner-entity approval gate and are reserved for internal daemon code paths;
    they must not be reachable from external HTTP callers (bu-vj46x).
    """

    predicate: str
    value: str
    src: str = "relationship"
    verified: bool = False
    primary: bool | None = None
    conf: float = Field(default=1.0, ge=0.0, le=1.0)
    channel_type: str | None = None
    """Source channel type (e.g. ``"telegram"``, ``"email"``) when the caller
    knows it.  Used to normalise the stored value to its canonical
    ``entity_facts`` form — telegram handles are stored ``telegram:<bare>`` so
    storage, resolution, and delivery agree on one format.  When omitted the
    value is stored verbatim (the ``has-*`` predicate alone cannot distinguish a
    telegram handle from a linkedin/twitter handle)."""

    @field_validator("src")
    @classmethod
    def src_not_trusted_internal(cls, v: str) -> str:
        return _reject_trusted_internal_src(v)


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


class MarkContactVerifiedResponse(BaseModel):
    """Response for ``POST /entities/{id}/contacts/{pred}/{valueHash}/verify``.

    ``verified`` is always True on success.
    ``fact_id`` is the UUID of the now-verified entity_facts row.
    """

    verified: bool
    fact_id: UUID


class SetPreferredChannelRequest(BaseModel):
    """Request body for ``PUT /entities/{id}/preferred-channel``.

    ``channel`` is the bare channel name the entity prefers to be reached on
    (e.g. ``"telegram"`` or ``"email"``). The backend rejects a channel the
    entity has no contact fact for (reachability validation in
    ``assert_prefers_channel``).
    """

    channel: str


class SetPreferredChannelResponse(BaseModel):
    """Response for ``PUT /entities/{id}/preferred-channel``.

    ``outcome`` is one of ``inserted``, ``unchanged``, or ``superseded`` from
    the single-valued ``prefers-channel`` assert path. ``channel`` echoes the
    now-active preferred channel.
    """

    outcome: str
    channel: str


class ClearPreferredChannelResponse(BaseModel):
    """Response for ``DELETE /entities/{id}/preferred-channel``.

    ``cleared`` is the number of active ``prefers-channel`` rows retracted
    (``0`` when no preference was set — the clear is idempotent).
    """

    cleared: int


class UpdateContactRequest(BaseModel):
    """Request body for ``PUT /entities/{id}/contacts/{pred}/{valueHash}``.

    ``new_value`` is the replacement contact object value (e.g. a new email address).
    All provenance fields (``src``, ``verified``, ``primary``, ``conf``) are
    optional and default to the same values used by the add endpoint when omitted.

    The predicate is fixed by the URL path — the edit changes only the value.

    Security: ``src`` values in the trusted auto-apply set (owner-self sources
    plus trusted internal-derivation jobs such as ``interaction_sync``) are
    rejected with a validation error — those source strings bypass the
    owner-entity approval gate and are reserved for internal daemon code paths;
    they must not be reachable from external HTTP callers (bu-vj46x).
    """

    new_value: str
    src: str = "relationship"
    verified: bool = False
    primary: bool | None = None
    conf: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("src")
    @classmethod
    def src_not_trusted_internal(cls, v: str) -> str:
        return _reject_trusted_internal_src(v)


class UpdateContactResponse(BaseModel):
    """Response for ``PUT /entities/{id}/contacts/{pred}/{valueHash}``.

    ``outcome`` is one of ``inserted``, ``unchanged``, ``superseded``, or
    ``pending_approval``.  When ``outcome == 'pending_approval'``, ``fact``
    is ``None`` and ``action_id`` carries the pending-actions row UUID;
    the HTTP status is 202.

    ``retracted_fact_id`` is the UUID of the old (retracted) row.
    ``fact`` is the new active fact row (``None`` when pending_approval).
    """

    outcome: str
    retracted_fact_id: UUID | None = None
    fact: ContactFact | None = None
    action_id: UUID | None = None


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


# ---------------------------------------------------------------------------
# Entity provenance facts models (bu-mg4dk)
# ---------------------------------------------------------------------------


class EntityFactEntry(BaseModel):
    """One triple from ``relationship.entity_facts`` for the entity provenance grid.

    Returned by ``GET /entities/{id}/facts`` — the Workbench-mode provenance
    endpoint.  Each row represents one active fact authored by the relationship
    butler's triple store.

    Provenance fields per §6b Amendment 7 (entity-brief.md):

    - ``weight`` — relational aggregation weight (NULL when not yet scored).
    - ``last_observed_at`` — most-recent observation timestamp (``last_seen``
      in the DB column; NULL when the fact has never been re-observed).
    - ``object_kind`` — ``'literal'`` for plain values; ``'entity'`` when the
      object is another entity's UUID.
    - ``src`` — butler slug that authored the fact (e.g. ``'relationship'``).

    Note: ``source_event_id`` is not yet a column in ``relationship.entity_facts``;
    it is a planned addition tracked separately.  Use ``src`` for authorship.
    """

    id: UUID
    subject: UUID
    predicate: str
    object: str
    object_kind: str
    src: str
    conf: float
    weight: int | None = None
    last_observed_at: datetime | None = None
    verified: bool
    primary: bool | None = None
    validity: str
    created_at: datetime
    #: Store of origin for this row — ``'identity'`` (``relationship.entity_facts``)
    #: or ``'narrative'`` (memory-module ``facts`` table).  Only surfaced as a
    #: distinguishing label when ``store=all`` is requested; identity-store rows
    #: still carry it for an unambiguous grid.
    store: Literal["identity", "narrative"] = "identity"
    #: Read-time staleness band (``fresh`` / ``aging`` / ``stale``) derived from
    #: ``COALESCE(observed_at, last_seen, created_at)`` (identity) or
    #: ``COALESCE(observed_at, last_confirmed_at, created_at)`` (narrative) —
    #: never stored on the row.  Confidence (``conf``) and staleness are separate
    #: axes (lifecycle §"Age").
    staleness_band: str


class EntityFactsResponse(BaseModel):
    """Keyset-paginated response for ``GET /entities/{id}/facts``.

    Keyset (cursor) pagination per the repo convention (CLAUDE.md §"Cursor
    Pagination"): ordered ``created_at DESC, id DESC``, no ``total`` field.

    - ``items`` — the page of fact rows (active identity rows by default;
      labeled narrative rows additionally when ``store=all``).
    - ``next_cursor`` — opaque cursor for the next page (``None`` on last page).
    - ``has_more`` — True when more rows exist beyond this page.
    """

    items: list[EntityFactEntry]
    next_cursor: str | None = None
    has_more: bool


# ---------------------------------------------------------------------------
# Activity binning (entity v3 — "Activity binning parameter")
# ---------------------------------------------------------------------------


class ActivityBin(BaseModel):
    """One day's activity count for the 90-day sparkline.

    ``date`` is an ISO calendar date (``YYYY-MM-DD``). ``count`` is the number of
    merged activity entries (relationship facts + chronicler episodes) whose
    timestamp falls on that local-UTC day. Zero-activity days are present with
    ``count=0`` — the sparkline renders quiet days honestly rather than
    collapsing them out (spec: "no day MUST be omitted or interpolated").
    """

    date: date
    count: int


class ActivityBinsResponse(BaseModel):
    """Response for ``GET /entities/{id}/activity?bins=daily`` when
    ``bins_only=true``.

    ``bins`` is a dense, ascending-by-date series covering the full window
    (one entry per day, including zero-count days). When the endpoint is called
    without ``bins_only`` it returns :class:`ActivityResponse` with an added
    ``bins`` field instead (the merged stream is preserved).
    """

    bins: list[ActivityBin]


# ---------------------------------------------------------------------------
# View marks + delta-since-last-visit (entity v3 — "Delta-since-last-visit")
# ---------------------------------------------------------------------------


class ViewMarkResponse(BaseModel):
    """Response for ``POST /entities/{id}/view-mark``.

    ``marked_at`` is the timestamp persisted to ``relationship.entity_view_marks``
    for this entity (upserted: one mark per entity). The frontend posts this only
    *after* reading ``GET /entities/{id}/delta-facts``, so the next visit's delta
    is computed relative to this mark.
    """

    entity_id: UUID
    marked_at: datetime


class DeltaFactEntry(BaseModel):
    """One fact that changed since the entity's view mark.

    Carries the same provenance shape as the facts-drill rows so the detail page
    can highlight the delta in place. ``store`` discriminates identity vs
    narrative origin; ``changed_at`` is the per-store change timestamp that beat
    the view mark (identity: ``GREATEST(created_at, updated_at)``; narrative:
    ``GREATEST(created_at, COALESCE(last_confirmed_at, created_at))``).
    """

    id: UUID
    subject: UUID
    predicate: str
    object: str
    object_kind: str
    src: str
    conf: float
    store: Literal["identity", "narrative"]
    validity: str
    created_at: datetime
    changed_at: datetime


class DeltaFactsResponse(BaseModel):
    """Response for ``GET /entities/{id}/delta-facts``.

    ``marked_at`` is the view mark the delta was computed against (``None`` on a
    first visit — no mark row exists yet, so ``items`` is empty and the frontend
    renders no banner). ``items`` are the facts changed since ``marked_at`` across
    both stores. The endpoint never moves the mark; the caller posts the mark
    afterwards via ``POST /entities/{id}/view-mark``.
    """

    marked_at: datetime | None = None
    items: list[DeltaFactEntry]


# ---------------------------------------------------------------------------
# Core dates block (entity v3 — "Core dates block", server half)
# ---------------------------------------------------------------------------


class CoreDateEntry(BaseModel):
    """A date-kind fact with its owner-relevant next occurrence.

    Server-extracted from the facts API (not client-side string matching).
    ``predicate`` is the date-kind predicate (e.g. ``has-birthday``). ``value`` is
    the raw stored object (an ISO ``YYYY-MM-DD`` or ``--MM-DD`` partial date).
    ``next_occurrence`` is the next calendar occurrence of (month, day) on or after
    the request date; ``days_until`` is the integer day count to it. Provenance
    fields mirror the facts-drill contract so each row renders provenance.
    """

    id: UUID
    predicate: str
    value: str
    month: int
    day: int
    year: int | None = None
    next_occurrence: date
    days_until: int
    src: str
    conf: float
    verified: bool
    staleness_band: str


class CoreDatesResponse(BaseModel):
    """Response for ``GET /entities/{id}/core-dates``.

    ``items`` are date-kind facts ordered by ``days_until`` ascending (the
    soonest upcoming date first), so the detail page surfaces the next occurrence
    without client-side sorting.
    """

    items: list[CoreDateEntry]


# ---------------------------------------------------------------------------
# Merge-review: compare endpoint + dismissal (entity v3, relationship-merge-review)
# ---------------------------------------------------------------------------


class CompareRequest(BaseModel):
    """Request body for ``POST /entities/compare``.

    ``entity_a`` and ``entity_b`` are the two entity UUIDs to structurally diff.
    The endpoint returns a server-computed, deterministic diff (no scoring, no
    ranking, no generated text) — the duplicate evidence the owner reviews before
    a merge-or-dismiss decision.
    """

    entity_a: UUID = Field(..., description="UUID of the first entity to compare.")
    entity_b: UUID = Field(..., description="UUID of the second entity to compare.")


class CompareFact(BaseModel):
    """One fact row in a compare block, carrying full provenance.

    Used for the per-entity ``identity_facts`` / ``narrative_facts`` blocks and
    for the ``shared`` / ``divergent`` lists. ``last_seen`` is nullable and omitted
    (``None``) on narrative-store rows, which have no ``last_seen`` column.
    ``staleness_band`` is the read-time band (``fresh`` / ``aging`` / ``stale``)
    derived per the originating store's COALESCE chain.

    For ``shared`` / ``divergent`` entries (identity store only), ``entity_id``
    identifies which entity the row belongs to so the two-column diff can place it.
    """

    id: UUID
    entity_id: UUID
    predicate: str
    object: str
    object_kind: str
    store: Literal["identity", "narrative"]
    src: str
    conf: float
    verified: bool
    primary: bool | None = None
    observed_at: datetime | None = None
    last_seen: datetime | None = None
    staleness_band: str


class CompareEntitySummary(BaseModel):
    """Identity summary of an entity inside a compare block."""

    id: UUID
    canonical_name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    tier: int | None = None
    state: str


class CompareEntityBlock(BaseModel):
    """Per-entity block (``a`` or ``b``) in a compare response.

    ``entity`` carries the identity summary (``tier`` is the pinned Dunbar tier
    override, nullable). ``identity_facts`` are active ``relationship.entity_facts``
    rows; ``narrative_facts`` are active memory-module narrative rows. Both lists
    carry full provenance + ``staleness_band``.
    """

    entity: CompareEntitySummary
    identity_facts: list[CompareFact]
    narrative_facts: list[CompareFact]


class CompareResponse(BaseModel):
    """Response for ``POST /entities/compare`` — a structural diff only.

    - ``a`` / ``b`` — per-entity blocks with identity + narrative facts.
    - ``shared`` — identity-store rows present on BOTH entities with identical
      ``(predicate, object)`` (the duplicate evidence). One pair of rows per match
      (the ``a`` row then the ``b`` row).
    - ``divergent`` — identity-store rows, ONLY for predicates whose registry
      ``cardinality = 'single'`` and whose objects differ between the two entities
      (the conflicts a merge must resolve). Multi-valued predicates union on merge
      and never appear here.

    There is no scoring, no ranking, no similarity percentage, and no generated
    text of any kind.
    """

    a: CompareEntityBlock
    b: CompareEntityBlock
    shared: list[CompareFact]
    divergent: list[CompareFact]


class DismissPairRequest(BaseModel):
    """Request body for ``POST /entities/dismiss-pair``.

    Records a ``merge_reviews`` row with ``outcome = 'dismissed'`` for the pair,
    capturing the shared-evidence snapshot at dismissal time. The dismissal
    suppresses the pair from the duplicate-candidate queue bucket until new shared
    evidence (a ``{predicate, shared_value}`` not in the snapshot) arises.
    """

    entity_a: UUID = Field(..., description="UUID of the first entity in the pair.")
    entity_b: UUID = Field(..., description="UUID of the second entity in the pair.")


class DismissPairResponse(BaseModel):
    """Response for ``POST /entities/dismiss-pair``.

    ``review_id`` is the UUID of the written ``merge_reviews`` audit row.
    ``shared_facts`` echoes the evidence snapshot captured at dismissal time.
    """

    review_id: UUID
    entity_a: UUID
    entity_b: UUID
    outcome: Literal["dismissed"]
    shared_facts: list[CompareFact]
