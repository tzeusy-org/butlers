"""Attention-ledger reader endpoints -- the ledger's first reader.

``public.attention_ledger`` (migration ``core_160``, ``butlers.core.attention_ledger``)
has recorded every terminal notify()/insight-delivery-cycle egress decision
since bu-qvnce.8, but until this module `grep attention_ledger src/butlers/api`
returned zero matches: the ledger was write-only. That meant a source could
be suppressed-but-never-delivered indefinitely (e.g. secrets_lifecycle's
deliver() bug, bu-tdd4k.2 -- 120 suppressed / 0 delivered) with nothing to
observe the outage except its absence downstream.

Two endpoints:

- ``GET /api/attention/ledger`` -- windowed, filterable (intent / source /
  outcome / origin_butler) paginated row list.
- ``GET /api/attention/ledger/summary`` -- per-``origin_butler`` delivery-vs-
  suppression rollup over a window, with the ``suppressed_never_delivered``
  flag the Trust Console panel exists to surface loudly.

Naming note: the ledger's own ``source`` column is the two-value notify/insight
choke-point literal; the Trust Console's "per source" grouping means
``origin_butler`` (which butler/job attempted the egress) -- see
``AttentionSourceSummary`` for the decision record.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import asyncpg
from fastapi import APIRouter, Depends, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import PaginationMeta
from butlers.api.models.attention_ledger import (
    AttentionLedgerEntry,
    AttentionLedgerListResponse,
    AttentionLedgerSummaryResponse,
    AttentionSourceSummary,
)

logger = logging.getLogger(__name__)
_missing_ledger_table_warnings: set[str] = set()

router = APIRouter(prefix="/api/attention", tags=["attention"])

# Summary endpoint default lookback when neither since/until is given --
# unbounded would scan the whole ledger on every request, and the
# suppressed-never-delivered signal is only actionable as a recent-window
# read (a source broken 3 months ago and fixed since should not still flag
# today). [decision] 7 days, matching the epic's "did this fix actually
# start delivering" framing -- not specified by the bead, so picked as the
# smallest window that comfortably spans a butler's daily/weekly cron cadence.
_DEFAULT_SUMMARY_WINDOW = timedelta(days=7)


def _get_db_manager() -> DatabaseManager:
    """Dependency stub -- overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _get_shared_pool(db: DatabaseManager) -> asyncpg.Pool | None:
    """Return a pool that can see ``public.attention_ledger``, or ``None``.

    ``public.*`` tables are reachable from any butler's pool (every
    schema-scoped search_path appends ``public`` -- see
    ``butlers.db.schema_search_path``); this module uses the switchboard
    pool as the shared access point, mirroring ``spend.py`` / ``approvals.py``.
    """
    try:
        return db.pool("switchboard")
    except KeyError:
        logger.warning(
            "Switchboard DB pool unavailable; attention-ledger reads degraded",
        )
        return None


def _normalize_metadata(value: object | None) -> dict | None:
    """Normalize ``metadata`` to the API contract shape (object-or-null).

    Production pools decode JSONB via ``register_jsonb_codec``, so this is
    normally already a dict; guards non-mapping legacy values the same way
    ``notifications.py``'s ``_normalize_notification_metadata`` does.
    """
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    return None


def _is_missing_ledger_table_error(exc: Exception) -> bool:
    """Return whether *exc* indicates an unmigrated ``attention_ledger`` table."""
    if exc.__class__.__name__ == "UndefinedTableError":
        return True
    msg = str(exc).lower()
    return "relation" in msg and "attention_ledger" in msg and "does not exist" in msg


def _log_missing_ledger_table_once(*, operation: str) -> None:
    if operation in _missing_ledger_table_warnings:
        logger.debug(
            "attention_ledger table still missing during %s; returning empty payload",
            operation,
        )
        return
    _missing_ledger_table_warnings.add(operation)
    logger.warning(
        "public.attention_ledger is missing during %s; returning empty payload "
        "until core migrations have run.",
        operation,
    )


def _empty_ledger_page(
    *, offset: int, limit: int, source_available: bool = True
) -> AttentionLedgerListResponse:
    return AttentionLedgerListResponse(
        data=[],
        meta=PaginationMeta(total=0, offset=offset, limit=limit),
        source_available=source_available,
    )


def _empty_ledger_summary(
    *, since: datetime | None, until: datetime | None, source_available: bool = True
) -> AttentionLedgerSummaryResponse:
    return AttentionLedgerSummaryResponse(
        since=since,
        until=until,
        by_source=[],
        flagged_sources=[],
        source_available=source_available,
    )


# ---------------------------------------------------------------------------
# GET /api/attention/ledger -- windowed, filterable row list
# ---------------------------------------------------------------------------


async def _query_ledger(
    pool: asyncpg.Pool,
    *,
    offset: int,
    limit: int,
    since: datetime | None,
    until: datetime | None,
    intent: str | None,
    source: str | None,
    outcome: str | None,
    origin_butler: str | None,
) -> AttentionLedgerListResponse:
    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if since is not None:
        conditions.append(f"occurred_at >= ${idx}")
        args.append(since)
        idx += 1
    if until is not None:
        conditions.append(f"occurred_at <= ${idx}")
        args.append(until)
        idx += 1
    if intent is not None:
        conditions.append(f"intent = ${idx}")
        args.append(intent)
        idx += 1
    if source is not None:
        conditions.append(f"source = ${idx}")
        args.append(source)
        idx += 1
    if outcome is not None:
        conditions.append(f"outcome = ${idx}")
        args.append(outcome)
        idx += 1
    if origin_butler is not None:
        conditions.append(f"origin_butler = ${idx}")
        args.append(origin_butler)
        idx += 1

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    count_sql = f"SELECT count(*) FROM public.attention_ledger{where_clause}"
    total = await pool.fetchval(count_sql, *args) or 0

    data_sql = (
        "SELECT id, occurred_at, origin_butler, source, channel, intent, "
        "priority_label, priority_score, dedup_key, outcome, reason, "
        f"notification_ref, metadata FROM public.attention_ledger{where_clause} "
        f"ORDER BY occurred_at DESC OFFSET ${idx} LIMIT ${idx + 1}"
    )
    args.extend([offset, limit])
    rows = await pool.fetch(data_sql, *args)

    entries = [
        AttentionLedgerEntry(
            id=row["id"],
            occurred_at=row["occurred_at"],
            origin_butler=row["origin_butler"],
            source=row["source"],
            channel=row["channel"],
            intent=row["intent"],
            priority_label=row["priority_label"],
            priority_score=row["priority_score"],
            dedup_key=row["dedup_key"],
            outcome=row["outcome"],
            reason=row["reason"],
            notification_ref=row["notification_ref"],
            metadata=_normalize_metadata(row["metadata"]),
        )
        for row in rows
    ]

    return AttentionLedgerListResponse(
        data=entries,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


@router.get("/ledger", response_model=AttentionLedgerListResponse)
async def list_attention_ledger(
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    since: datetime | None = Query(None, description="Only rows occurred_at at/after this time"),
    until: datetime | None = Query(None, description="Only rows occurred_at at/before this time"),
    intent: str | None = Query(None, description="Filter by intent (e.g. send, insight)"),
    source: str | None = Query(None, description="Filter by choke point: notify or insight"),
    outcome: str | None = Query(
        None, description="Filter by outcome: delivered, coalesced, deferred, suppressed, failed"
    ),
    origin_butler: str | None = Query(None, description="Filter by originating butler/job name"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> AttentionLedgerListResponse:
    """Return paginated attention-ledger rows, newest first.

    Filterable by intent, ``source`` (the notify/insight choke-point column),
    ``outcome``, and ``origin_butler``; windowed by ``since``/``until``.
    """
    pool = _get_shared_pool(db)
    if pool is None:
        return _empty_ledger_page(offset=offset, limit=limit, source_available=False)
    try:
        return await _query_ledger(
            pool,
            offset=offset,
            limit=limit,
            since=since,
            until=until,
            intent=intent,
            source=source,
            outcome=outcome,
            origin_butler=origin_butler,
        )
    except Exception as exc:
        if not _is_missing_ledger_table_error(exc):
            raise
        _log_missing_ledger_table_once(operation="list_attention_ledger")
        return _empty_ledger_page(offset=offset, limit=limit)


# ---------------------------------------------------------------------------
# GET /api/attention/ledger/summary -- per-origin_butler delivery-vs-suppression
# ---------------------------------------------------------------------------


async def _query_ledger_summary(
    pool: asyncpg.Pool,
    *,
    since: datetime,
    until: datetime | None,
    intent: str | None,
    source: str | None,
    origin_butler: str | None,
) -> AttentionLedgerSummaryResponse:
    conditions: list[str] = ["occurred_at >= $1"]
    args: list[object] = [since]
    idx = 2

    if until is not None:
        conditions.append(f"occurred_at <= ${idx}")
        args.append(until)
        idx += 1
    if intent is not None:
        conditions.append(f"intent = ${idx}")
        args.append(intent)
        idx += 1
    if source is not None:
        conditions.append(f"source = ${idx}")
        args.append(source)
        idx += 1
    if origin_butler is not None:
        conditions.append(f"origin_butler = ${idx}")
        args.append(origin_butler)
        idx += 1

    where_clause = " WHERE " + " AND ".join(conditions)
    sql = (
        "SELECT origin_butler, "
        "COUNT(*) FILTER (WHERE outcome = 'delivered') AS delivered, "
        "COUNT(*) FILTER (WHERE outcome = 'coalesced') AS coalesced, "
        "COUNT(*) FILTER (WHERE outcome = 'deferred') AS deferred, "
        "COUNT(*) FILTER (WHERE outcome = 'suppressed') AS suppressed, "
        "COUNT(*) FILTER (WHERE outcome = 'failed') AS failed, "
        "COUNT(*) AS total "
        f"FROM public.attention_ledger{where_clause} "
        "GROUP BY origin_butler "
        "ORDER BY total DESC"
    )
    rows = await pool.fetch(sql, *args)

    by_source = [
        AttentionSourceSummary(
            origin_butler=row["origin_butler"],
            delivered=row["delivered"],
            coalesced=row["coalesced"],
            deferred=row["deferred"],
            suppressed=row["suppressed"],
            failed=row["failed"],
            total=row["total"],
            suppressed_never_delivered=row["suppressed"] > 0 and row["delivered"] == 0,
        )
        for row in rows
    ]
    flagged_sources = [s.origin_butler for s in by_source if s.suppressed_never_delivered]

    return AttentionLedgerSummaryResponse(
        since=since,
        until=until,
        by_source=by_source,
        flagged_sources=flagged_sources,
    )


@router.get("/ledger/summary", response_model=AttentionLedgerSummaryResponse)
async def get_attention_ledger_summary(
    since: datetime | None = Query(
        None,
        description=(
            "Window start. Defaults to 7 days ago when omitted -- an unbounded "
            "scan is never the default for this aggregate."
        ),
    ),
    until: datetime | None = Query(None, description="Window end. Defaults to now when omitted."),
    intent: str | None = Query(None, description="Filter by intent (e.g. send, insight)"),
    source: str | None = Query(None, description="Filter by choke point: notify or insight"),
    origin_butler: str | None = Query(
        None, description="Narrow the rollup to a single originating butler/job"
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> AttentionLedgerSummaryResponse:
    """Return per-``origin_butler`` delivery-vs-suppression counts over a window.

    This is the Trust Console panel's data source: each row's
    ``suppressed_never_delivered`` flag is True when that source has been
    suppressed at least once and never delivered in the window -- the exact
    live failure this epic fixed (secrets_lifecycle showed 120 suppressed / 0
    delivered before bu-tdd4k.2).
    """
    effective_since = since if since is not None else datetime.now(UTC) - _DEFAULT_SUMMARY_WINDOW
    pool = _get_shared_pool(db)
    if pool is None:
        return _empty_ledger_summary(since=effective_since, until=until, source_available=False)
    try:
        return await _query_ledger_summary(
            pool,
            since=effective_since,
            until=until,
            intent=intent,
            source=source,
            origin_butler=origin_butler,
        )
    except Exception as exc:
        if not _is_missing_ledger_table_error(exc):
            raise
        _log_missing_ledger_table_once(operation="get_attention_ledger_summary")
        return _empty_ledger_summary(since=effective_since, until=until)
