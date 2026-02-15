"""Pydantic models for the health butler API.

Provides models for measurements, medications, doses, conditions,
symptoms, meals, and research used by the health butler's dashboard
endpoints.
"""

from __future__ import annotations

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
