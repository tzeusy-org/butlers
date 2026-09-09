"""Held-capture ledger endpoint -- the Dispatch held-capture lane's data source.

``GET /api/captures?state=held`` surfaces orphaned captures: rows the
``capture()`` core tool wrote synchronously but whose routing session never
finished (bu-2jtfw.9). This is a core (cross-butler) router because
``public.captures`` is a shared table, not owned by any one roster butler's
schema.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query

from butlers.api.db import DatabaseManager
from butlers.api.degraded import DegradedSources
from butlers.api.models import KeysetMeta, KeysetResponse
from butlers.api.models.capture import CaptureSummary
from butlers.core.keyset_cursor import decode_cursor, encode_cursor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/captures", tags=["captures"])

# public.captures is granted to every butler role; General is the pool this
# route queries through since it's the capture system's natural home
# (MANIFESTO: "the default home and router of last resort").
_QUERY_BUTLER = "general"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


@router.get("", response_model=KeysetResponse[CaptureSummary])
async def list_captures(
    state: Literal["held", "routed", "refused"] | None = Query(
        None, description="Filter by receipt_state. Omit to return all states."
    ),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, description="Opaque keyset cursor from a prior page."),
    db: DatabaseManager = Depends(_get_db_manager),
) -> KeysetResponse[CaptureSummary]:
    """List captures, most recent first. Always HTTP 200, even when degraded."""
    tracker = DegradedSources(logger)

    try:
        pool = db.pool(_QUERY_BUTLER)
    except KeyError:
        tracker.mark(_QUERY_BUTLER, msg="Captures ledger pool unavailable")
        return KeysetResponse[CaptureSummary](
            data=[],
            meta=KeysetMeta(
                limit=limit, next_cursor=None, has_more=False, sources_degraded=tracker.names
            ),
        )

    conditions: list[str] = []
    params: list[object] = []
    idx = 1

    if state is not None:
        conditions.append(f"receipt_state = ${idx}")
        params.append(state)
        idx += 1

    cursor_created_at: object | None = None
    cursor_id: str | None = None
    if cursor:
        cursor_created_at, cursor_id = decode_cursor(cursor)
        conditions.append(f"(created_at, capture_id) < (${idx}, ${idx + 1})")
        params.append(cursor_created_at)
        params.append(cursor_id)
        idx += 2

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit + 1)

    try:
        rows = await pool.fetch(
            f"""
            SELECT capture_id, channel, content, receipt_state, routed_kind,
                   target_schema, target_table, target_row_id, refusal_reason,
                   source_butler, created_at, updated_at
            FROM public.captures
            {where}
            ORDER BY created_at DESC, capture_id DESC
            LIMIT ${idx}
            """,
            *params,
        )
    except Exception:
        logger.warning("Captures ledger query failed", exc_info=True)
        tracker.mark(_QUERY_BUTLER, msg="Captures ledger query failed")
        return KeysetResponse[CaptureSummary](
            data=[],
            meta=KeysetMeta(
                limit=limit, next_cursor=None, has_more=False, sources_degraded=tracker.names
            ),
        )

    has_more = len(rows) > limit
    page = rows[:limit]

    data = [
        CaptureSummary(
            capture_id=str(r["capture_id"]),
            channel=r["channel"],
            content=r["content"],
            receipt_state=r["receipt_state"],
            routed_kind=r["routed_kind"],
            target_schema=r["target_schema"],
            target_table=r["target_table"],
            target_row_id=str(r["target_row_id"]) if r["target_row_id"] else None,
            refusal_reason=r["refusal_reason"],
            source_butler=r["source_butler"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in page
    ]

    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(last["created_at"], last["capture_id"])

    return KeysetResponse[CaptureSummary](
        data=data,
        meta=KeysetMeta(limit=limit, next_cursor=next_cursor, has_more=has_more),
    )
