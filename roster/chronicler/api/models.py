"""Pydantic models for Chronicler dashboard API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SubsourceCheckpoint(BaseModel):
    """Per-subsource projection checkpoint detail."""

    subsource: str
    last_run_at: datetime | None = None
    last_error: str | None = None


class SourceStateRow(BaseModel):
    """Runtime state for a single source adapter, joined with projection checkpoints."""

    source_name: str
    chronicler_compatibility: str
    read_surface: str | None = None
    boundary_semantics: str | None = None
    optional_schema: bool
    active: bool
    inactive_reason: str | None = None
    last_run_at: datetime | None = None
    last_error: str | None = None
    subsource_checkpoints: list[SubsourceCheckpoint] | None = None


class ChroniclerPointEvent(BaseModel):
    id: str
    source_name: str
    source_ref: str
    event_type: str
    occurred_at: datetime
    precision: str
    title: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    privacy: str
    retention_days: int | None = None
    tombstone_at: datetime | None = None
    canonical_occurred_at: datetime
    canonical_title: str | None = None
    canonical_privacy: str
    corrected_at: datetime | None = None
    correction_note: str | None = None
    created_at: datetime
    updated_at: datetime


class ChroniclerEpisode(BaseModel):
    id: str
    source_name: str
    source_ref: str
    episode_type: str
    start_at: datetime
    end_at: datetime | None = None
    precision: str
    title: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    privacy: str
    retention_days: int | None = None
    tombstone_at: datetime | None = None
    canonical_start_at: datetime
    canonical_end_at: datetime | None = None
    canonical_title: str | None = None
    canonical_privacy: str
    corrected_at: datetime | None = None
    correction_note: str | None = None
    created_at: datetime
    updated_at: datetime


class ChroniclerOverride(BaseModel):
    id: str
    target_kind: str
    target_id: str
    corrected_start_at: datetime | None = None
    corrected_end_at: datetime | None = None
    corrected_title: str | None = None
    corrected_privacy: str | None = None
    corrected_tombstone_at: datetime | None = None
    note: str | None = None
    submitted_by: str
    created_at: datetime


class SubmitCorrectionRequest(BaseModel):
    corrected_start_at: datetime | None = None
    corrected_end_at: datetime | None = None
    corrected_title: str | None = None
    corrected_privacy: str | None = Field(
        default=None,
        description="One of 'normal', 'sensitive', 'restricted'",
    )
    corrected_tombstone_at: datetime | None = None
    note: str | None = None
    submitted_by: str = "user"


__all__ = [
    "ChroniclerEpisode",
    "ChroniclerOverride",
    "ChroniclerPointEvent",
    "SourceStateRow",
    "SubsourceCheckpoint",
    "SubmitCorrectionRequest",
]
