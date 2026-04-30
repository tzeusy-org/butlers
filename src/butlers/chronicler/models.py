"""Typed models for Chronicler storage primitives.

Mirror the `chronicler` schema tables and views one-to-one. All rows carry
source provenance, boundary precision, and privacy/retention metadata per
RFC 0014.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


class Precision(enum.StrEnum):
    """Boundary precision declared by the source adapter.

    ``exact`` — sub-minute precision (timestamps are trustworthy).
    ``minute``/``hour``/``day`` — truncated precision levels.
    ``unknown`` — adapter cannot declare precision.
    """

    EXACT = "exact"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    UNKNOWN = "unknown"


class Privacy(enum.StrEnum):
    """Privacy class inherited from the source declaration."""

    NORMAL = "normal"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


class Compatibility(enum.StrEnum):
    """Source compatibility status for the Chronicler contract registry."""

    SUPPORTED = "supported"
    DEFERRED = "deferred"
    NOT_TIME_BEARING = "not_time_bearing"
    PLANNED = "planned"


class LinkRelation(enum.StrEnum):
    """How a point event relates to an episode it is linked to."""

    SUPPORTS = "supports"
    BOUNDARY_START = "boundary_start"
    BOUNDARY_END = "boundary_end"
    EVIDENCE = "evidence"


class OverrideTarget(enum.StrEnum):
    """Kind of row an override targets."""

    EPISODE = "episode"
    POINT_EVENT = "point_event"


@dataclass
class SourceAdapterState:
    """Row in `chronicler.source_adapter_state`."""

    source_name: str
    chronicler_compatibility: Compatibility
    read_surface: str | None = None
    boundary_semantics: str | None = None
    optional_schema: bool = False
    active: bool = False
    inactive_reason: str | None = None
    schema_version: int = 1
    registered_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class ProjectionCheckpoint:
    """Row in `chronicler.projection_checkpoints`.

    ``subsource`` is ``None`` for global (adapter-level) checkpoints and a
    non-empty string (e.g. the butler schema name) for per-sub-source rows.

    ``watermark_id`` is the row ``id`` of the last-projected row from the source
    evidence table, forming a tuple watermark ``(watermark, watermark_id)`` that
    eliminates the batch-boundary missed-row edge case when multiple rows share
    the same timestamp.  It is ``None`` for checkpoints written before migration
    ``chronicler_005``; adapters fall back to single-column ``>`` semantics in
    that case.
    """

    source_name: str
    subsource: str | None = None
    watermark: datetime | None = None
    watermark_id: int | None = None
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    rows_projected: int = 0
    run_count: int = 0
    updated_at: datetime | None = None


@dataclass
class PointEvent:
    """Canonical row in `chronicler.point_events` (before override overlay)."""

    source_name: str
    source_ref: str
    event_type: str
    occurred_at: datetime
    precision: Precision = Precision.EXACT
    title: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    privacy: Privacy = Privacy.NORMAL
    retention_days: int | None = None
    tombstone_at: datetime | None = None
    tombstone_reason: str | None = None
    id: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Episode:
    """Canonical row in `chronicler.episodes` (before override overlay)."""

    source_name: str
    source_ref: str
    episode_type: str
    start_at: datetime
    end_at: datetime | None = None
    precision: Precision = Precision.EXACT
    title: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    privacy: Privacy = Privacy.NORMAL
    retention_days: int | None = None
    tombstone_at: datetime | None = None
    tombstone_reason: str | None = None
    id: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class CorrectedEpisode:
    """Row from `v_episodes_corrected` — effective values after overlay."""

    id: UUID
    source_name: str
    source_ref: str
    episode_type: str
    start_at: datetime
    end_at: datetime | None
    precision: Precision
    title: str | None
    payload: dict[str, Any]
    privacy: Privacy
    retention_days: int | None
    tombstone_at: datetime | None
    canonical_start_at: datetime
    canonical_end_at: datetime | None
    canonical_title: str | None
    canonical_privacy: Privacy
    corrected_at: datetime | None
    correction_note: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def is_corrected(self) -> bool:
        return self.corrected_at is not None


@dataclass
class CorrectedPointEvent:
    """Row from `v_point_events_corrected` — effective values after overlay."""

    id: UUID
    source_name: str
    source_ref: str
    event_type: str
    occurred_at: datetime
    precision: Precision
    title: str | None
    payload: dict[str, Any]
    privacy: Privacy
    retention_days: int | None
    tombstone_at: datetime | None
    canonical_occurred_at: datetime
    canonical_title: str | None
    canonical_privacy: Privacy
    corrected_at: datetime | None
    correction_note: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def is_corrected(self) -> bool:
        return self.corrected_at is not None


@dataclass
class Override:
    """Row in `chronicler.overrides`.

    At least one ``corrected_*`` field or ``note`` must be non-None
    (enforced by the DB CHECK constraint).
    """

    target_kind: OverrideTarget
    target_id: UUID
    corrected_start_at: datetime | None = None
    corrected_end_at: datetime | None = None
    corrected_title: str | None = None
    corrected_privacy: Privacy | None = None
    corrected_tombstone_at: datetime | None = None
    note: str | None = None
    submitted_by: str = "user"
    id: UUID | None = None
    created_at: datetime | None = None


__all__ = [
    "Compatibility",
    "CorrectedEpisode",
    "CorrectedPointEvent",
    "Episode",
    "LinkRelation",
    "Override",
    "OverrideTarget",
    "PointEvent",
    "Precision",
    "Privacy",
    "ProjectionCheckpoint",
    "SourceAdapterState",
]
