"""Pydantic models for the module state management API.

Provides request/response models for the module-states endpoints:
- GET /api/butlers/{name}/module-states
- PUT /api/butlers/{name}/module-states/{module_name}/enabled
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ModuleRuntimeStateResponse(BaseModel):
    """Runtime state of a single butler module.

    Reflects the daemon's ``ModuleRuntimeState`` dataclass plus additional
    metadata derived from the butler config.
    """

    name: str = Field(description="Module name")
    health: Literal["active", "failed", "cascade_failed"] = Field(
        description="Module health status"
    )
    enabled: bool = Field(description="Whether the module is currently enabled")
    failure_phase: str | None = Field(
        default=None,
        description="Startup phase where failure occurred (null if healthy)",
    )
    failure_error: str | None = Field(
        default=None,
        description="Error message from startup failure (null if healthy)",
    )
    has_config: bool = Field(
        default=False,
        description="Whether butler.toml has a [modules.{name}] section for this module",
    )


class ModuleSetEnabledRequest(BaseModel):
    """Request body for toggling a module's enabled state."""

    enabled: bool = Field(description="Whether to enable (true) or disable (false) the module")
