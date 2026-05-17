"""Pydantic response models for the ingestion event API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
    ``public.ingestion_events`` (status='ingested') with
    ``connectors.filtered_events`` (status = filtered/error/replay_*).
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
    cost: dict[str, Any] | None = None
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
