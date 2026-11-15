"""Public MCP kill-switch models (bu-y66o00.9, REQ-dashboard-settings-console-001)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PublicMcpControl(BaseModel):
    """One ``mcp_surface.controls`` row. ``state`` is null when the row is missing."""

    domain: str
    state: Literal["enabled", "disabled"] | None
    changed_at: datetime | None
    changed_by: str | None


class PublicMcpSummary(BaseModel):
    """What the Settings console panel shows."""

    disabled_domains: int
    total_domains: int
    all_disabled: bool
    #: Control rows absent from the table; they are neither enabled nor disabled.
    missing_rows: int = 0


class PublicMcpControls(BaseModel):
    """The ``all`` switch first, then the seven domains, with the summary."""

    controls: list[PublicMcpControl]
    summary: PublicMcpSummary


class PublicMcpControlWrite(BaseModel):
    """Set one switch. No other field exists: the actor comes from the session."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["enabled", "disabled"]
