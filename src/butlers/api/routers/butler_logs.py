"""Butler structured log endpoint.

Provides:

    GET /api/butlers/{name}/logs
        ?level=INFO   — filter: returns lines whose level >= the given level
                        (DEBUG < INFO < WARN < ERROR).  Optional.
        ?since=<ISO>  — returns lines with ts >= since.  Optional.
        ?limit=100    — default 100, max 1000.

Response shape::

    { "lines": [{ "ts", "level", "msg", "source", "request_id", "metadata" }] }

Level filter semantics: ``?level=WARN`` returns WARN and ERROR lines.
This is the conventional "minimum severity" interpretation used by most log
viewers and is more useful for dashboards than exact-match filtering.

503 is returned when the butler's DB pool is not registered with the
DatabaseManager (butler not running or not yet initialised).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from butlers.api.db import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butlers", "logs"])

# Severity order — used for >= level filtering.
_LEVEL_ORDER = ("DEBUG", "INFO", "WARN", "ERROR")
_VALID_LEVELS = frozenset(_LEVEL_ORDER)


def _level_rank(level: str) -> int:
    try:
        return _LEVEL_ORDER.index(level.upper())
    except ValueError:
        return -1


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class LogLine(BaseModel):
    """A single structured log entry from ``butler_logs``."""

    ts: datetime
    level: str
    msg: str
    source: str | None = None
    request_id: UUID | None = None
    metadata: dict[str, Any] | None = None


class LogLines(BaseModel):
    """Response wrapper for the log list endpoint."""

    lines: list[LogLine]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/{name}/logs", response_model=LogLines)
async def list_butler_logs(
    name: str,
    level: str | None = Query(
        None,
        description="Minimum log level to return (DEBUG < INFO < WARN < ERROR). "
        "Omit to return all levels.",
    ),
    since: datetime | None = Query(
        None,
        description="ISO 8601 timestamp; returns lines with ts >= since.",
    ),
    limit: int = Query(
        100,
        ge=1,
        le=1000,
        description="Maximum number of log lines to return (default 100, max 1000).",
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> LogLines:
    """Return recent structured log lines from a butler's ``butler_logs`` table.

    Lines are ordered newest-first (ts DESC).  The ``level`` filter applies a
    minimum-severity threshold rather than an exact match: ``?level=WARN``
    returns WARN and ERROR lines.
    """
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' database is not available",
        )

    # Validate level parameter
    if level is not None:
        norm_level = level.upper()
        if norm_level not in _VALID_LEVELS:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid level '{level}'. Must be one of: DEBUG, INFO, WARN, ERROR",
            )
        min_rank = _level_rank(norm_level)
        allowed_levels = [lv for lv in _LEVEL_ORDER if _level_rank(lv) >= min_rank]
    else:
        allowed_levels = None

    # Build query dynamically
    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if allowed_levels is not None:
        placeholders = ", ".join(f"${i}" for i in range(idx, idx + len(allowed_levels)))
        conditions.append(f"level IN ({placeholders})")
        args.extend(allowed_levels)
        idx += len(allowed_levels)

    if since is not None:
        conditions.append(f"ts >= ${idx}")
        args.append(since)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = (
        f"SELECT ts, level, msg, source, request_id, metadata"
        f" FROM butler_logs{where}"
        f" ORDER BY ts DESC"
        f" LIMIT ${idx}"
    )
    args.append(limit)

    try:
        rows = await pool.fetch(sql, *args)
    except Exception:
        logger.warning("Failed to query butler_logs for butler %s", name, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=f"Failed to query logs for butler '{name}'",
        )

    lines: list[LogLine] = []
    for row in rows:
        raw_metadata = row["metadata"]
        if isinstance(raw_metadata, str):
            try:
                raw_metadata = json.loads(raw_metadata)
            except (ValueError, TypeError):
                raw_metadata = None

        lines.append(
            LogLine(
                ts=row["ts"],
                level=row["level"],
                msg=row["msg"],
                source=row["source"],
                request_id=row["request_id"],
                metadata=raw_metadata,
            )
        )

    return LogLines(lines=lines)
