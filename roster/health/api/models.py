"""Pydantic models for the health butler API.

Provides models for measurements, medications, doses, conditions,
symptoms, meals, and research used by the health butler's dashboard
endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class Measurement(BaseModel):
    """A health measurement (e.g. blood pressure, weight)."""

    id: str
    type: str
    value: dict  # JSONB — e.g. {"systolic": 120, "diastolic": 80}
    measured_at: str
    notes: str | None = None
    created_at: str


class Medication(BaseModel):
    """A tracked medication with dosage and schedule."""

    id: str
    name: str
    dosage: str
    frequency: str
    schedule: list = []  # JSONB
    active: bool = True
    notes: str | None = None
    created_at: str
    updated_at: str


class Dose(BaseModel):
    """A recorded dose of a medication."""

    id: str
    medication_id: str
    taken_at: str
    skipped: bool = False
    notes: str | None = None
    created_at: str


class Condition(BaseModel):
    """A health condition being tracked."""

    id: str
    name: str
    status: str = "active"  # active, resolved, managed
    diagnosed_at: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str


class Symptom(BaseModel):
    """A recorded symptom occurrence."""

    id: str
    name: str
    severity: int  # 1-10
    condition_id: str | None = None
    occurred_at: str
    notes: str | None = None
    created_at: str


class Meal(BaseModel):
    """A recorded meal with optional nutrition data."""

    id: str
    type: str  # breakfast, lunch, dinner, snack
    description: str
    nutrition: dict | None = None  # JSONB
    eaten_at: str
    notes: str | None = None
    created_at: str


class Research(BaseModel):
    """A health research entry or reference."""

    id: str
    title: str
    content: str
    tags: list[str] = []  # JSONB
    source_url: str | None = None
    condition_id: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Measurements — latest-by-type, sleep, sources
# ---------------------------------------------------------------------------


class LatestMeasurementEntry(BaseModel):
    """Latest measurement row for a single type.

    Backed by the ``facts`` table (predicate ``measurement_{type}``).
    ``value`` is extracted from ``metadata.value`` — may be a scalar or a
    compound dict (e.g. blood pressure ``{"systolic": 120, "diastolic": 80}``).
    ``unit`` is extracted from ``metadata.unit`` and ``metadata`` carries any
    remaining fields (e.g. ``source``, ``notes``).
    """

    measured_at: str
    value: Any  # JSONB from DB — scalar or compound dict
    unit: str | None = None
    metadata: dict | None = None


class LatestMeasurementsResponse(BaseModel):
    """Response for GET /measurements/latest?types=X,Y,Z.

    Keys are the requested type strings.  A key maps to ``None`` when no row
    exists for that type.
    """

    measurements: dict[str, LatestMeasurementEntry | None]


class SleepStage(BaseModel):
    """A single sleep-stage entry in a sleep session."""

    kind: str  # deep | light | rem | awake
    minutes: int


class SleepSessionResponse(BaseModel):
    """Response for GET /measurements/sleep/latest.

    Derived from the ``sleep_session`` fact stored by the Google Health
    connector.  ``total_duration_minutes`` is computed from
    ``metadata.duration_ms``.  ``stages`` is populated from
    ``metadata.stages`` when present.
    """

    session_start: str
    session_end: str | None = None
    total_duration_minutes: int
    stages: list[SleepStage] = []


class MeasurementSource(BaseModel):
    """A single data-source entry observed across measurements."""

    name: str
    last_sample_at: str
    sample_count: int


class MeasurementSourcesResponse(BaseModel):
    """Response for GET /measurements/sources."""

    sources: list[MeasurementSource]


# ---------------------------------------------------------------------------
# Measurements — trend aggregation
# ---------------------------------------------------------------------------


class TrendBucket(BaseModel):
    """A single time bucket in a measurement trend response.

    Backed by ``date_trunc('day' | 'hour', valid_at AT TIME ZONE 'UTC')``
    aggregation over the ``facts`` table.  ``bucket_start`` is the start of
    the bucket in UTC.  Only rows with scalar numeric ``metadata.value`` are
    included.
    """

    bucket_start: datetime
    value_mean: float
    value_min: float
    value_max: float
    sample_count: int


class TrendResponse(BaseModel):
    """Response for GET /measurements/trend.

    Aggregates ``facts`` rows for a single measurement type into hourly or
    daily buckets over a requested window.
    """

    type: str
    window_days: int
    bucket: Literal["hourly", "daily"]
    buckets: list[TrendBucket]
