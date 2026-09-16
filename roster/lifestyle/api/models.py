"""Pydantic models for the lifestyle butler's taste-ledger read surface.

bu-2jtfw.10.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

type TasteSummaryQueryName = Literal[
    "total_works",
    "total_signals",
    "total_verdicts",
    "recent_signals_7d",
    "works_by_kind",
    "signals_by_kind",
]


class TasteSummaryQueryAvailability(BaseModel):
    """Content-blind availability of one ledger summary query."""

    query: TasteSummaryQueryName
    state: Literal["available", "unavailable"]
    reason: Literal["query_failed"] | None = None


class TasteSummary(BaseModel):
    """Ledger-wide taste counts (bu-2jtfw.10)."""

    total_works: int
    total_signals: int
    total_verdicts: int
    recent_signals_7d: int
    works_by_kind: dict[str, int]
    signals_by_kind: dict[str, int]
    # A successful query returning zero rows is still a complete, genuine
    # empty ledger. "partial" means at least one sibling query failed while
    # "unavailable" means no summary query produced a result.
    availability: Literal["complete", "partial", "unavailable"] = "complete"
    query_availability: list[TasteSummaryQueryAvailability] = Field(default_factory=list)
    # Honest-degraded flag (docs/api_and_protocols/response-conventions.md):
    # False only when every summary query failed; a partial response remains
    # useful and keeps this compatibility flag true.
    ledger_available: bool = True


class TasteWork(BaseModel):
    """One work (track, artist, ...) in the taste ledger."""

    id: str
    kind: str
    title: str | None
    external_ids: dict
    created_at: str


class TasteVerdict(BaseModel):
    """One owner assertion about taste, optionally tied to a work."""

    id: str
    work_id: str | None
    predicate: str
    verdict_text: str
    source: str
    created_at: str
