"""Strict allowlisted wire models for snapshot-backed Bead details."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BeadDependencySummary(BaseModel):
    """A direct dependency summary with no raw-edge or tracker data."""

    model_config = {"extra": "forbid"}

    id: str
    title: str | None = None
    status: str | None = None
    priority: int | None = None
    type: str | None = None


class BeadDetail(BaseModel):
    """The complete, intentionally small dashboard Bead detail contract."""

    model_config = {"extra": "forbid"}

    id: str
    title: str | None = None
    status: str | None = None
    priority: int | None = None
    type: str | None = None
    description: str | None = None
    design: str | None = None
    acceptance_criteria: str | None = None
    labels: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    closed_at: datetime | None = None
    due_at: datetime | None = None
    dependencies: list[BeadDependencySummary] = Field(default_factory=list)
    external_ref: str | None = None
