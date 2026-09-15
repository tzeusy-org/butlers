"""Pydantic models for the lifestyle butler's taste-ledger read surface.

bu-2jtfw.10.
"""

from __future__ import annotations

from pydantic import BaseModel


class TasteSummary(BaseModel):
    """Ledger-wide taste counts (bu-2jtfw.10)."""

    total_works: int
    total_signals: int
    total_verdicts: int
    recent_signals_7d: int
    works_by_kind: dict[str, int]
    signals_by_kind: dict[str, int]
    # Honest-degraded flag (docs/api_and_protocols/response-conventions.md):
    # False only for a genuine failure reading the ledger tables, never for
    # a legitimately empty/pre-migration ledger (which reports real zeros).
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
