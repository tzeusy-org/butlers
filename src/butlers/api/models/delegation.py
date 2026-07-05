"""Cross-butler delegation ledger Pydantic models.

See ``src/butlers/core/delegation_ledger.py`` for the writer/reader and
``alembic/versions/core/core_162_delegation_ledger.py`` for the table this
models. bu-gxmfx.
"""

from __future__ import annotations

from pydantic import BaseModel


class DelegationLedgerEntry(BaseModel):
    """One row of ``public.delegation_ledger`` — a cross-butler question/answer."""

    id: str
    asked_at: str
    asking_butler: str
    question: str
    target_butler: str | None = None
    catalog_match_id: str | None = None
    catalog_score: float | None = None
    status: str
    reason: str | None = None
    answer: str | None = None
    answered_at: str | None = None
    answering_butler: str | None = None
