"""Pydantic models for the general butler API.

Provides models for collections, entities and owner projects.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Collection(BaseModel):
    """A named collection of entities in the general butler."""

    id: str
    name: str
    description: str | None = None
    entity_count: int = 0  # computed via COUNT
    created_at: str


class Entity(BaseModel):
    """An entity within a collection."""

    id: str
    collection_id: str
    collection_name: str | None = None  # joined from collections
    data: dict = {}  # JSONB
    tags: list[str] = []  # JSONB
    created_at: str
    updated_at: str


class SizeHistogramBucket(BaseModel):
    """A bucket in the collection size distribution histogram."""

    bracket: str  # e.g., "0", "1-10", "11-100", "101+"
    count: int  # number of collections in this bracket


class GeneralStats(BaseModel):
    """Aggregated statistics for the general butler's collections."""

    total_collections: int
    total_entities: int  # sum of entities across all collections
    last_modified_collection: str | None  # name of most recently modified collection
    largest_collection_size: int
    size_histogram: list[SizeHistogramBucket]


# ---------------------------------------------------------------------------
# Owner projects (bu-jv1moa.12): owner-authored, time-bounded charters.
#
# Every request model forbids unknown fields, so a crafted draft lineage,
# promotion flag, actor or state cannot ride along with an owner write.
# ---------------------------------------------------------------------------

ProjectKind = Literal["project", "responsibility", "decision"]
ProjectState = Literal["draft", "closed", "expired"]
ProjectLinkKind = Literal["deadline", "owner_condition", "trip", "drive_file", "calendar_event"]

_STRICT = ConfigDict(extra="forbid", str_strip_whitespace=False)


def _clean_text(value: str, limit: int) -> str:
    if value != value.strip() or not value or len(value) > limit:
        raise ValueError("text must be trimmed, non-empty and bounded")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise ValueError("text must not contain control characters")
    return value


class ProjectBudget(BaseModel):
    """An owner-stated budget figure; descriptive only, it authorises no spend."""

    model_config = _STRICT

    amount: Decimal = Field(gt=0, le=Decimal("100000000"), max_digits=12, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ProjectConstraints(BaseModel):
    """Typed charter constraints: an optional budget and things that must hold."""

    model_config = _STRICT

    budget: ProjectBudget | None = None
    must_keep: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("must_keep")
    @classmethod
    def _must_keep(cls, value: list[str]) -> list[str]:
        cleaned = [_clean_text(item, 200) for item in value]
        if len({item.casefold() for item in cleaned}) != len(cleaned):
            raise ValueError("must_keep entries must be distinct")
        return cleaned


class ProjectCharterInput(BaseModel):
    """The owner-authored charter content shared by create and edit."""

    model_config = _STRICT

    title: str
    horizon_end: datetime
    objective: str
    done_condition: str
    constraints: ProjectConstraints = Field(default_factory=ProjectConstraints)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        return _clean_text(value, 200)

    @field_validator("objective", "done_condition")
    @classmethod
    def _long_text(cls, value: str) -> str:
        return _clean_text(value, 1000)

    @field_validator("horizon_end")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("horizon_end must carry a timezone")
        return value


class ProjectCreateRequest(ProjectCharterInput):
    """Create a draft project; replaying the same key and request returns it."""

    kind: ProjectKind
    idempotency_key: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")


class ProjectCharterEditRequest(ProjectCharterInput):
    """Write charter version ``expected_version + 1`` under compare-and-swap."""

    expected_version: int = Field(ge=1)


class ProjectCloseRequest(BaseModel):
    """Close a draft at its current charter version; closing touches no linked record."""

    model_config = _STRICT

    expected_version: int = Field(ge=1)


class ProjectLinkRequest(BaseModel):
    """Link one existing record by kind and opaque id; no URL, no copied content."""

    model_config = _STRICT

    ref_kind: ProjectLinkKind
    ref_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")

    @model_validator(mode="after")
    def _canonical_uuid_kinds(self) -> ProjectLinkRequest:
        if self.ref_kind in {"owner_condition", "trip", "calendar_event"}:
            try:
                parsed = UUID(self.ref_id)
            except ValueError as exc:
                raise ValueError("this link kind takes a canonical UUID") from exc
            if str(parsed) != self.ref_id:
                raise ValueError("this link kind takes a canonical UUID")
        return self


class ProjectCharter(BaseModel):
    """One insert-only charter version."""

    version: int
    title: str
    horizon_end: datetime
    objective: str
    done_condition: str
    constraints: ProjectConstraints
    created_by: str
    created_at: datetime


class ProjectLink(BaseModel):
    """A typed reference; the dashboard resolves it through the record's own reader."""

    id: UUID
    ref_kind: ProjectLinkKind
    ref_id: str
    created_at: datetime


class ProjectSummary(BaseModel):
    """A project as listed.  ``state`` is effective: a draft past its horizon is expired."""

    id: UUID
    kind: ProjectKind
    title: str
    state: ProjectState
    current_charter_version: int
    horizon_end: datetime
    closed_at: datetime | None
    link_count: int
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectSummary):
    """A project with its current charter and links."""

    charter: ProjectCharter
    links: list[ProjectLink]
