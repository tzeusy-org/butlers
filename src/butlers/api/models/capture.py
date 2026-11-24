"""Owner capture intake and status projections (RFC0037, REQ-general-capture-001/-006).

Requests forbid every field the server derives itself: no actor, owner, source,
target, schema, table or role. Responses project opaque IDs and fixed categories
only; original intake text and reference handles never leave the ledger here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CaptureDoor = Literal["dashboard", "chat"]


class CaptureReference(BaseModel):
    """A normalized owning-source attachment handle, never a URL or locator."""

    model_config = ConfigDict(extra="forbid")

    source_id: UUID
    attachment_id: UUID


class CaptureIntakeRequest(BaseModel):
    """Explicit owner capture body; size limits are re-checked by the service."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=32768)
    references: list[CaptureReference] = Field(default_factory=list, max_length=8)
    mutation_id: str | None = Field(default=None, min_length=1, max_length=128)


class CaptureIntakeContext(BaseModel):
    """Server-issued, expiring owner action grant for one intake door."""

    intake_context: str
    door: CaptureDoor
    expires_at: datetime


class CaptureTarget(BaseModel):
    """Typed source-owned target receipt; no locator beyond opaque IDs."""

    owner: str
    operation_id: UUID
    item_id: UUID
    version: int


class CaptureStatus(BaseModel):
    """Content-free owner status of one capture."""

    capture_id: UUID
    disposition: Literal["held", "routed", "refused"]
    category: str
    source_door: CaptureDoor | Literal["internal"]
    target: CaptureTarget | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
