"""Pydantic response models for the ingestion event API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ReplayHistoryEntry(BaseModel):
    """One replay attempt entry from public.audit_log."""

    ts: datetime
    actor: str
    result: str | None = None
    cost: float | None = None


class SenderContactResolution(BaseModel):
    """Contact resolution result for an event's sender_identity.

    ``resolved`` is True when a matching contact was found.
    ``name`` is the canonical contact name (None if not resolved or name not set).
    ``raw`` is the original sender_identity value.
    """

    resolved: bool
    name: str | None = None
    raw: str | None = None


class IngestionEventSummary(BaseModel):
    """Lightweight ingestion event representation for list views.

    Returned from the unified timeline endpoint which merges
    ``public.ingestion_events`` (status = ingested, or the derived 'skipped'
    for skip-triaged rows) with ``connectors.filtered_events``
    (status = filtered/error/replay_*).
    """

    id: str
    received_at: datetime
    source_channel: str | None = None
    source_provider: str | None = None
    source_endpoint_identity: str | None = None
    source_sender_identity: str | None = None
    source_thread_identity: str | None = None
    external_event_id: str | None = None
    dedupe_key: str | None = None
    dedupe_strategy: str | None = None
    ingestion_tier: str | None = None
    policy_tier: str | None = None
    triage_decision: str | None = None
    triage_target: str | None = None
    # Unified timeline fields — present on all rows regardless of source table.
    status: str = "ingested"
    filter_reason: str | None = None
    error_detail: str | None = None


class IngestionEventDetail(IngestionEventSummary):
    """Full ingestion event detail, augmented with decomposition lifecycle state.

    Includes ``lifecycle_state`` and ``decomposition_output`` from
    ``message_inbox`` when available (joined via the switchboard schema pool).
    Both fields are ``None`` when the message_inbox row is not accessible
    (e.g. the switchboard pool is unavailable or the row has been pruned).
    """

    lifecycle_state: str | None = None
    decomposition_output: dict[str, Any] | None = None


class IngestionEventSession(BaseModel):
    """A butler session linked to an ingestion event."""

    id: str
    butler_name: str
    trigger_source: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    success: bool | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    trace_id: str | None = None
    model: str | None = None


class ButlerRollupEntry(BaseModel):
    """Per-butler cost and token breakdown."""

    sessions: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


class IngestionEventRollup(BaseModel):
    """Aggregate cost/token totals for a single ingestion event."""

    request_id: str
    total_sessions: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    by_butler: dict[str, ButlerRollupEntry] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Window rollup model (GET /api/ingestion/rollup)
# ---------------------------------------------------------------------------


class IngestionWindowRollupWindow(BaseModel):
    """The time window boundaries for the rollup."""

    from_: str | None = Field(None, alias="from")
    to: str | None = None

    model_config = {"populate_by_name": True}


class IngestionWindowRollup(BaseModel):
    """Aggregate event/session counts for the active filter window.

    Returned by GET /api/ingestion/rollup.  The ``cost`` field is always
    ``None`` until cost-per-event data is available at the window level
    (tracked as a separate follow-up bead).
    """

    events: int
    sessions: int
    cost: float | None = None
    window: dict[str, str | None]


# ---------------------------------------------------------------------------
# Priority contacts models
# ---------------------------------------------------------------------------


class IngestionEventPayload(BaseModel):
    """Raw inbound payload for an ingestion event.

    Returned by ``GET /api/ingestion/events/{id}/payload``.

    Access is audit-gated: the backend records an audit entry on every read.
    The endpoint returns HTTP 404 when no event with that id exists, and
    HTTP 503 when a required database pool (shared or switchboard) is unavailable.
    """

    content: str
    """Pretty-printed JSON or raw text of the original inbound payload."""
    bytes: int
    """Byte size of the full payload (may exceed the truncated content length)."""
    truncated: bool = False
    """Whether the content was truncated due to size limits."""
    channel: str | None = None
    """Source channel/connector that produced this payload."""


class PriorityContactEntry(BaseModel):
    """One row in public.priority_contacts, joined to public.contacts for display.

    ``name`` is the canonical contact name from public.contacts.  It may be
    None if the contact has no display name set.

    ``contact_info_values`` is a list of non-sensitive channel identifiers
    (email addresses, Telegram handles, etc.) derived from active
    ``relationship.entity_facts`` triples (migration bead bu-hjo3i).
    Credential-bearing entries (secured triples) are excluded.

    ``is_inert`` is True when the entry would silently match nothing at runtime.
    For Gmail: the policy evaluator resolves senders via a 3-hop join
    (priority_contacts → contacts.entity_id → entity_facts has-email); a contact
    is inert when its entity_id is NULL or its entity carries no active has-email
    fact.  For other butlers: only a missing entity_id makes the row inert (they
    use has-handle or other predicates).  The UI surfaces this as a warning badge.
    """

    contact_id: UUID
    butler: str
    added_at: datetime
    added_by: str | None = None
    name: str | None = None
    contact_info_values: list[str] = Field(default_factory=list)
    is_inert: bool = False


class PriorityContactAddRequest(BaseModel):
    """Request body for POST /api/ingestion/priority-contacts.

    Role mutations are NOT accepted here — the handler returns HTTP 400 if a
    ``roles`` field is present.  Use PATCH /api/contacts to update contact roles.
    """

    contact_id: UUID
    butler: str


class PriorityContactAddResponse(BaseModel):
    """Response body for POST /api/ingestion/priority-contacts (201)."""

    contact_id: UUID
    butler: str
    added_at: datetime
    added_by: str | None = None
