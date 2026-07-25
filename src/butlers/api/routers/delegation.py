"""Cross-butler delegation ledger — read-only discovery endpoints.

Exposes ``public.delegation_ledger`` (bu-gxmfx) so a question posted via the
``delegate_ask`` MCP tool is discoverable outside the asking/answering
butlers' own sessions. See ``src/butlers/core/delegation_ledger.py`` for the
writer/reader this router delegates to.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.delegation import DelegationLedgerEntry
from butlers.core.delegation_ledger import VALID_STATUSES, get_delegation, list_delegations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/delegation", tags=["delegation"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _any_pool(db: DatabaseManager) -> object:
    """Return any available pool — public.delegation_ledger is reachable from every one."""
    for name in sorted(db.butler_names):
        try:
            return db.pool(name)
        except KeyError:
            continue
    raise HTTPException(status_code=503, detail="No database pools available")


def _row_to_entry(row: dict) -> DelegationLedgerEntry:
    return DelegationLedgerEntry(
        id=str(row["id"]),
        asked_at=str(row["asked_at"]),
        asking_butler=row["asking_butler"],
        question=row["question"],
        target_butler=row.get("target_butler"),
        catalog_match_id=str(row["catalog_match_id"]) if row.get("catalog_match_id") else None,
        catalog_score=row.get("catalog_score"),
        status=row["status"],
        reason=row.get("reason"),
        answer=row.get("answer"),
        answered_at=str(row["answered_at"]) if row.get("answered_at") else None,
        answering_butler=row.get("answering_butler"),
        answer_digest=row.get("answer_digest"),
        wake_key=row.get("wake_key"),
        wake_state=row.get("wake_state") or "not_applicable",
        wake_task_id=str(row["wake_task_id"]) if row.get("wake_task_id") else None,
        wake_task_name=row.get("wake_task_name"),
        wake_updated_at=str(row["wake_updated_at"]) if row.get("wake_updated_at") else None,
    )


@router.get("/ledger", response_model=PaginatedResponse[DelegationLedgerEntry])
async def list_delegation_ledger(
    status: str | None = Query(
        None, description=f"Filter by status. One of: {', '.join(sorted(VALID_STATUSES))}."
    ),
    asking_butler: str | None = Query(None, description="Filter by the asking butler's name."),
    target_butler: str | None = Query(
        None, description="Filter by the resolved target butler's name."
    ),
    wake_stuck: bool = Query(
        False,
        description=(
            "When true, only return rows whose wake_state is one of the two "
            "failure states the wake protocol introduces: callback_failed or "
            "task_conflict."
        ),
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[DelegationLedgerEntry]:
    """List cross-butler delegated questions, most-recent first.

    ``public.delegation_ledger`` already aggregates every butler's asks into
    one shared table reachable from any pool, so — like
    ``GET /api/memory/catalog/search`` — this is deliberately NOT a per-butler
    fan-out; a single query answers the whole fleet.
    """
    if status is not None and status not in VALID_STATUSES:
        allowed = ", ".join(sorted(VALID_STATUSES))
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status {status!r}. Must be one of: {allowed}",
        )

    pool = _any_pool(db)
    total, rows = await list_delegations(
        pool,
        status=status,
        asking_butler=asking_butler,
        target_butler=target_butler,
        wake_stuck=wake_stuck,
        offset=offset,
        limit=limit,
    )
    return PaginatedResponse[DelegationLedgerEntry](
        data=[_row_to_entry(r) for r in rows],
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


@router.get("/ledger/{ledger_id}", response_model=ApiResponse[DelegationLedgerEntry])
async def get_delegation_ledger_entry(
    ledger_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[DelegationLedgerEntry]:
    """Return a single delegation-ledger entry by id."""
    pool = _any_pool(db)
    try:
        row = await get_delegation(pool, ledger_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid ledger id (must be a UUID)") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Delegation ledger entry not found")
    return ApiResponse[DelegationLedgerEntry](data=_row_to_entry(row))
