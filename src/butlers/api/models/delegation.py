"""Cross-butler delegation ledger Pydantic models.

See ``src/butlers/core/delegation_ledger.py`` for the writer/reader and
``alembic/versions/core/core_162_delegation_ledger.py`` for the table this
models. bu-gxmfx.
"""

from __future__ import annotations

from pydantic import BaseModel


class DelegationLedgerEntry(BaseModel):
    """One row of ``public.delegation_ledger`` — a cross-butler question/answer.

    ``wake_*``/``answer_digest`` (bu-ep4ks.3) widen this entry with the
    return-callback/task lifecycle migration core_181 added to the ledger --
    see ``butlers.core.delegation_ledger`` module docstring ("Wake state").
    ``wake_state`` defaults to ``"not_applicable"``, matching the DB column's
    default for a row with no v1 answer yet. ``callback_failed`` and
    ``task_conflict`` are the two failure states the wake protocol introduces;
    before this widening they were indistinguishable from an ordinary
    answered row over this API.
    """

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
    answer_digest: str | None = None
    wake_key: str | None = None
    wake_state: str = "not_applicable"
    wake_task_id: str | None = None
    wake_task_name: str | None = None
    wake_updated_at: str | None = None
