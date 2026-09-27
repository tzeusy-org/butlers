"""Lifestyle butler taste-ledger read surface (bu-2jtfw.10).

Backs the Taste tab's ledger-derived panels: aggregate counts, a paginated
works list, and a paginated verdicts (owner assertions) list. All list
endpoints return honest ``meta.total`` from ``COUNT(*)`` rather than the
fetched page length (docs/api_and_protocols/response-conventions.md).
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta

_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("lifestyle_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["lifestyle_api_models"] = _models
    _spec.loader.exec_module(_models)

    TasteSummary = _models.TasteSummary
    TasteSummaryQueryAvailability = _models.TasteSummaryQueryAvailability
    TasteWork = _models.TasteWork
    TasteVerdict = _models.TasteVerdict
else:
    raise RuntimeError("Failed to load lifestyle API models")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lifestyle", tags=["lifestyle"])

BUTLER_DB = "lifestyle"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the lifestyle butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Lifestyle butler database is not available",
        )


# ---------------------------------------------------------------------------
# GET /taste/summary — ledger-wide counts
# ---------------------------------------------------------------------------


async def _run_summary_query(
    query_name: str,
    query: Callable[[], Awaitable[Any]],
    *,
    fallback: Any,
    availability: list[Any],
) -> Any:
    """Run one summary query without discarding successful sibling sections.

    Missing ledger tables retain the endpoint's established 503 contract.
    Other failures are represented by a fixed, content-blind reason so the
    response can identify the unavailable section without exposing database
    errors or source payloads.
    """
    try:
        value = await query()
    except asyncpg.UndefinedTableError:
        raise
    except Exception:
        logger.warning("Taste summary query unavailable: query=%s", query_name)
        availability.append(
            TasteSummaryQueryAvailability(
                query=query_name,
                state="unavailable",
                reason="query_failed",
            )
        )
        return fallback

    availability.append(TasteSummaryQueryAvailability(query=query_name, state="available"))
    return value


@router.get("/taste/summary", response_model=ApiResponse[TasteSummary])
async def get_taste_summary(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[TasteSummary]:
    """Return ledger-wide counts: works/signals/verdicts totals, works and
    signals grouped by kind, and a 7-day recent-signal count.

    A missing ledger table is an unavailable read model, not an empty ledger,
    and therefore returns 503. Other query failures are isolated to the
    affected section; successful sibling sections remain in the response.
    """
    pool = _pool(db)
    query_availability: list[Any] = []
    try:
        cutoff = datetime.now(UTC) - timedelta(days=7)
        total_works = await _run_summary_query(
            "total_works",
            lambda: pool.fetchval("SELECT count(*) FROM works"),
            fallback=0,
            availability=query_availability,
        )
        total_signals = await _run_summary_query(
            "total_signals",
            lambda: pool.fetchval("SELECT count(*) FROM taste_signals"),
            fallback=0,
            availability=query_availability,
        )
        total_verdicts = await _run_summary_query(
            "total_verdicts",
            lambda: pool.fetchval("SELECT count(*) FROM verdicts"),
            fallback=0,
            availability=query_availability,
        )
        recent_signals_7d = await _run_summary_query(
            "recent_signals_7d",
            lambda: pool.fetchval(
                "SELECT count(*) FROM taste_signals WHERE occurred_at >= $1", cutoff
            ),
            fallback=0,
            availability=query_availability,
        )
        works_by_kind = await _run_summary_query(
            "works_by_kind",
            lambda: _fetch_grouped_counts(
                pool,
                "SELECT kind, count(*) AS n FROM works GROUP BY kind ORDER BY n DESC",
                "kind",
            ),
            fallback={},
            availability=query_availability,
        )
        signals_by_kind = await _run_summary_query(
            "signals_by_kind",
            lambda: _fetch_grouped_counts(
                pool,
                "SELECT signal_kind, count(*) AS n FROM taste_signals"
                " GROUP BY signal_kind ORDER BY n DESC",
                "signal_kind",
            ),
            fallback={},
            availability=query_availability,
        )
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(status_code=503, detail="Taste ledger is not available") from exc

    unavailable = [query for query in query_availability if query.state == "unavailable"]
    if not unavailable:
        availability = "complete"
    elif len(unavailable) == len(query_availability):
        availability = "unavailable"
    else:
        availability = "partial"

    return ApiResponse[TasteSummary](
        data=TasteSummary(
            total_works=total_works or 0,
            total_signals=total_signals or 0,
            total_verdicts=total_verdicts or 0,
            recent_signals_7d=recent_signals_7d or 0,
            works_by_kind=works_by_kind,
            signals_by_kind=signals_by_kind,
            availability=availability,
            query_availability=query_availability,
            ledger_available=availability != "unavailable",
        )
    )


async def _fetch_grouped_counts(pool: Any, query: str, key: str) -> dict[str, int]:
    rows = await pool.fetch(query)
    return {row[key]: row["n"] for row in rows}


# ---------------------------------------------------------------------------
# GET /taste/works — paginated works list
# ---------------------------------------------------------------------------


@router.get("/taste/works", response_model=PaginatedResponse[TasteWork])
async def list_taste_works(
    kind: str | None = Query(None, description="Filter by work kind, e.g. 'track'."),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[TasteWork]:
    """List works in the taste ledger, newest first."""
    pool = _pool(db)
    try:
        total = (
            await pool.fetchval(
                "SELECT count(*) FROM works WHERE ($1::text IS NULL OR kind = $1)", kind
            )
            or 0
        )
        rows = await pool.fetch(
            """
            SELECT id, kind, title, external_ids, created_at FROM works
            WHERE ($1::text IS NULL OR kind = $1)
            ORDER BY created_at DESC
            OFFSET $2 LIMIT $3
            """,
            kind,
            offset,
            limit,
        )
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(status_code=503, detail="Taste ledger is not available") from exc

    data = [
        TasteWork(
            id=str(r["id"]),
            kind=r["kind"],
            title=r["title"],
            external_ids=dict(r["external_ids"]) if r["external_ids"] else {},
            created_at=r["created_at"].isoformat(),
        )
        for r in rows
    ]
    return PaginatedResponse[TasteWork](
        data=data, meta=PaginationMeta(total=total, offset=offset, limit=limit)
    )


# ---------------------------------------------------------------------------
# GET /taste/verdicts — paginated owner-assertion list
# ---------------------------------------------------------------------------


@router.get("/taste/verdicts", response_model=PaginatedResponse[TasteVerdict])
async def list_taste_verdicts(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[TasteVerdict]:
    """List owner-asserted taste verdicts (including migrated legacy facts),
    newest first."""
    pool = _pool(db)
    try:
        total = await pool.fetchval("SELECT count(*) FROM verdicts") or 0
        rows = await pool.fetch(
            """
            SELECT id, work_id, predicate, verdict_text, source, created_at
            FROM verdicts
            ORDER BY created_at DESC
            OFFSET $1 LIMIT $2
            """,
            offset,
            limit,
        )
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(status_code=503, detail="Taste ledger is not available") from exc

    data = [
        TasteVerdict(
            id=str(r["id"]),
            work_id=str(r["work_id"]) if r["work_id"] else None,
            predicate=r["predicate"],
            verdict_text=r["verdict_text"],
            source=r["source"],
            created_at=r["created_at"].isoformat(),
        )
        for r in rows
    ]
    return PaginatedResponse[TasteVerdict](
        data=data, meta=PaginationMeta(total=total, offset=offset, limit=limit)
    )
