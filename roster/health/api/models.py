"""Pydantic models for the health butler API.

Provides models for measurements, medications, doses, conditions,
symptoms, meals, and research used by the health butler's dashboard
endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Measurement(BaseModel):
    """A health measurement (e.g. blood pressure, weight)."""

    id: str
    type: str
    value: dict  # JSONB — e.g. {"systolic": 120, "diastolic": 80}
    measured_at: str
    notes: str | None = None
    created_at: str


_MEASUREMENT_TYPE = Literal["weight", "blood_pressure", "heart_rate", "blood_sugar", "temperature"]


class MeasurementCreateRequest(BaseModel):
    """Request body for POST /measurements.

    Persisted through the same ``measurement_log`` fact-store path the Health
    butler's own MCP tool uses (predicate ``measurement_{type}``, scope
    ``health``), so a dashboard-logged measurement is indistinguishable from a
    butler-logged one and is read back by GET /measurements.  Measurements are
    TEMPORAL facts: ``measured_at`` becomes the fact's ``valid_at`` and multiple
    readings coexist by design (no supersession).  ``value`` is stored as JSONB
    and may be a scalar (e.g. ``{"value": 165}``) or a compound dict (e.g.
    ``{"systolic": 120, "diastolic": 80}``).
    """

    type: _MEASUREMENT_TYPE
    value: dict
    measured_at: datetime | None = None
    notes: str | None = None


class MeasurementUpdateRequest(BaseModel):
    """Request body for PUT /measurements/{id}.

    All fields are optional; only the supplied (non-null) fields are applied to
    the existing measurement fact via the in-place ``measurement_update`` path
    (temporal facts are edited in place rather than superseded).  At least one
    field must be provided.  Changing ``type`` rewrites the underlying
    ``measurement_{type}`` predicate.
    """

    type: _MEASUREMENT_TYPE | None = None
    value: dict | None = None
    measured_at: datetime | None = None
    notes: str | None = None


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


class MedicationCreateRequest(BaseModel):
    """Request body for POST /medications.

    Persisted through the same ``medication_add`` fact-store path the Health
    butler's own MCP tool uses (predicate ``medication``, scope ``health``), so
    a dashboard-created medication is indistinguishable from a butler-created
    one and is read back by GET /medications.
    """

    name: str = Field(..., min_length=1)
    dosage: str = Field(..., min_length=1)
    frequency: str = Field(..., min_length=1)
    schedule: list[str] = []
    notes: str | None = None


class MedicationUpdateRequest(BaseModel):
    """Request body for PUT /medications/{id}.

    All fields are optional; only the supplied (non-null) fields are merged into
    the existing medication fact via the superseding ``medication_update`` path.
    At least one field must be provided.
    """

    name: str | None = Field(default=None, min_length=1)
    dosage: str | None = Field(default=None, min_length=1)
    frequency: str | None = Field(default=None, min_length=1)
    schedule: list[str] | None = None
    active: bool | None = None
    notes: str | None = None


class Dose(BaseModel):
    """A recorded dose of a medication."""

    id: str
    medication_id: str
    taken_at: str
    skipped: bool = False
    notes: str | None = None
    created_at: str


class DoseLogRequest(BaseModel):
    """Request body for POST /medications/{id}/doses.

    Persisted through the same ``medication_log_dose`` fact-store path the Health
    butler's own MCP tool uses (predicate ``took_dose``, scope ``health``,
    ``valid_at`` = taken_at), so a dashboard-logged dose is indistinguishable
    from a butler-logged one and is read back by GET /medications/{id}/doses.
    Doses are TEMPORAL facts: multiple entries coexist by design (no
    supersession).  Set ``skipped=True`` to record a missed dose.  ``taken_at``
    defaults to now when omitted.
    """

    taken_at: datetime | None = None
    skipped: bool = False
    notes: str | None = None


class MedicationAdherenceResponse(BaseModel):
    """Response for GET /medications/{id}/adherence.

    Aggregated from the ``took_dose`` facts scoped to a single medication
    (the same surface the ``medication_log_dose`` MCP tool writes), over an
    optional ``start``/``end`` window.

    ``expected_doses`` is derived from the medication's prescribed frequency
    over the window using the shared ``frequency_to_doses_per_day`` helper
    (the same denominator the insight-scan job uses).  ``adherence_rate`` is
    the percentage of non-skipped (taken) doses out of ``expected_doses``
    (``null`` when ``expected_doses`` is zero).
    """

    medication_id: str
    total_doses: int
    taken_doses: int
    skipped_doses: int
    expected_doses: int
    adherence_rate: float | None = None


class Condition(BaseModel):
    """A health condition being tracked."""

    id: str
    name: str
    status: str = "active"  # active, resolved, managed
    diagnosed_at: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str


class ConditionCreateRequest(BaseModel):
    """Request body for POST /conditions.

    Persisted through the same ``condition_add`` fact-store path the Health
    butler's own MCP tool uses (predicate ``condition``, scope ``health``), so a
    dashboard-created condition is indistinguishable from a butler-created one
    and is read back by GET /conditions.  ``status`` must be one of
    ``active``, ``managed``, or ``resolved``.  ``diagnosed_at`` is the onset /
    diagnosis timestamp (ISO-8601).
    """

    name: str = Field(..., min_length=1)
    status: Literal["active", "managed", "resolved"] = "active"
    diagnosed_at: datetime | None = None
    notes: str | None = None


class ConditionUpdateRequest(BaseModel):
    """Request body for PUT /conditions/{id}.

    All fields are optional; only the supplied (non-null) fields are merged into
    the existing condition fact via the superseding ``condition_update`` path.
    At least one field must be provided.  When supplied, ``status`` must be one
    of ``active``, ``managed``, or ``resolved``.
    """

    name: str | None = Field(default=None, min_length=1)
    status: Literal["active", "managed", "resolved"] | None = None
    diagnosed_at: datetime | None = None
    notes: str | None = None


class Symptom(BaseModel):
    """A recorded symptom occurrence."""

    id: str
    name: str
    severity: int  # 1-10
    condition_id: str | None = None
    occurred_at: str
    notes: str | None = None
    created_at: str


class SymptomCreateRequest(BaseModel):
    """Request body for POST /symptoms.

    Persisted through the same ``symptom_log`` fact-store path the Health
    butler's own MCP tool uses (predicate ``symptom``, scope ``health``), so a
    dashboard-logged symptom is indistinguishable from a butler-logged one and
    is read back by GET /symptoms.  Symptoms are TEMPORAL facts: ``occurred_at``
    becomes the fact's ``valid_at`` and multiple entries coexist by design (no
    supersession).  ``severity`` is on a 1-10 scale.  ``condition_id``, when
    supplied, must reference an existing condition.
    """

    name: str = Field(..., min_length=1)
    severity: int = Field(..., ge=1, le=10)
    condition_id: str | None = None
    occurred_at: datetime | None = None
    notes: str | None = None


class SymptomUpdateRequest(BaseModel):
    """Request body for PUT /symptoms/{id}.

    All fields are optional; only the supplied (non-null) fields are applied to
    the existing symptom fact via the in-place ``symptom_update`` path (temporal
    facts are edited in place rather than superseded).  At least one field must
    be provided.  When supplied, ``severity`` must be between 1 and 10.
    """

    name: str | None = Field(default=None, min_length=1)
    severity: int | None = Field(default=None, ge=1, le=10)
    condition_id: str | None = None
    occurred_at: datetime | None = None
    notes: str | None = None


class Meal(BaseModel):
    """A recorded meal with optional nutrition data."""

    id: str
    type: str  # breakfast, lunch, dinner, snack
    description: str
    nutrition: dict | None = None  # JSONB
    eaten_at: str
    notes: str | None = None
    created_at: str


_MEAL_TYPE = Literal["breakfast", "lunch", "dinner", "snack"]


class MealCreateRequest(BaseModel):
    """Request body for POST /meals.

    Persisted through the same ``meal_log`` fact-store path the Health butler's
    own MCP tool uses (predicate ``meal_{type}``, scope ``health``), so a
    dashboard-logged meal is indistinguishable from a butler-logged one and is
    read back by GET /meals.  Meals are TEMPORAL facts: ``eaten_at`` becomes the
    fact's ``valid_at`` and multiple entries coexist by design (no
    supersession).  ``nutrition`` is an optional dict shaped like
    ``{"calories": N, "protein_g": N, "carbs_g": N, "fat_g": N}``.
    """

    type: _MEAL_TYPE
    description: str = Field(..., min_length=1)
    eaten_at: datetime
    nutrition: dict | None = None
    notes: str | None = None


class MealUpdateRequest(BaseModel):
    """Request body for PUT /meals/{id}.

    All fields are optional; only the supplied (non-null) fields are applied to
    the existing meal fact via the in-place ``meal_update`` path (temporal facts
    are edited in place rather than superseded).  At least one field must be
    provided.  Changing ``type`` rewrites the underlying ``meal_{type}``
    predicate.
    """

    type: _MEAL_TYPE | None = None
    description: str | None = Field(default=None, min_length=1)
    eaten_at: datetime | None = None
    nutrition: dict | None = None
    notes: str | None = None


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


class ResearchCreateRequest(BaseModel):
    """Request body for POST /research.

    Persisted through the same ``research_save`` fact-store path the Health
    butler's own MCP tool uses (predicate ``research``, scope ``health``), so a
    dashboard-created research note is indistinguishable from a butler-created
    one and is read back by GET /research.  Research notes are PROPERTY facts
    (like conditions): supersession is keyed on the ``research:{title}`` subject,
    so a note with the same title supersedes its predecessor.  ``condition_id``,
    when supplied, must reference an existing condition.
    """

    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    tags: list[str] = []
    source_url: str | None = None
    condition_id: str | None = None


class ResearchUpdateRequest(BaseModel):
    """Request body for PUT /research/{id}.

    All fields are optional; only the supplied (non-null) fields are merged into
    the existing research fact via the superseding ``research_update`` path (a
    property fact keyed on the ``research:{title}`` subject).  At least one field
    must be provided.  When supplied, ``condition_id`` must reference an existing
    condition.
    """

    title: str | None = Field(default=None, min_length=1)
    content: str | None = Field(default=None, min_length=1)
    tags: list[str] | None = None
    source_url: str | None = None
    condition_id: str | None = None


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


# ---------------------------------------------------------------------------
# Nutrition — aggregate summary
# ---------------------------------------------------------------------------


class NutritionDailyAverage(BaseModel):
    """Per-day average macros within a nutrition summary window."""

    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float


class NutritionSummaryResponse(BaseModel):
    """Response for GET /nutrition/summary?start=&end=.

    Aggregates nutrition from ``meal_*`` facts (the same surface the ``meal_log``
    MCP tool writes) over the requested window via the ``nutrition_summary``
    tool.  Meals without nutrition metadata are excluded.  ``days`` is the
    inclusive span used to compute the daily averages (minimum 1).
    """

    total_calories: float
    total_protein_g: float
    total_carbs_g: float
    total_fat_g: float
    daily_avg: NutritionDailyAverage
    meal_count: int
    days: int


class Briefing(BaseModel):
    """The six-field health Voice briefing returned by GET /api/health/briefing.

    Mirrors the dashboard briefing contract (``GET /api/dashboard/briefing``).
    ``source`` is exactly ``"llm"`` (a model-written elaboration that passed the
    non-diagnostic voice-lint) or ``"fallback"`` (the deterministic templated
    paragraph). The dashboard BriefingStatus pill renders ``source = "llm"`` as
    ``llm · cached`` and ``source = "fallback"`` as ``templated``.
    """

    greet: str
    headline: str
    elaboration: str
    source: str  # "llm" or "fallback"
    state_class: str
    generated_at: str  # ISO 8601
