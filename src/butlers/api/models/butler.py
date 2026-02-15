"""Pydantic models for the butler discovery and status API.

Provides models for listing butlers, viewing butler details, and
inspecting per-module health status.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModuleStatus(BaseModel):
    """Health status of a single butler module."""

    name: str
    enabled: bool
    status: str
    phase: str | None = None
    error: str | None = None


class ButlerSummary(BaseModel):
    """Lightweight butler representation for list views."""

    name: str
    status: str
    port: int
    db: str
    modules: list[str] = Field(default_factory=list)
    schedule_count: int = 0


class ButlerDetail(ButlerSummary):
    """Extended butler representation with config, skills, and module health.

    Inherits all fields from ``ButlerSummary`` and adds runtime details
    used by the butler detail view in the dashboard.
    """

    config: dict = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    module_health: list[ModuleStatus] = Field(default_factory=list)
