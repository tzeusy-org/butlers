"""Butlers-list read-model v1 — versioned read boundary for the butlers list endpoint.

Centralises the scalar fan-out query that counts recent sessions per butler,
used by ``GET /api/butlers`` to enrich each butler summary with a 24-hour
session count.

A breaking schema change (renamed column, changed table name) should produce a
new ``butlers_v2`` module rather than silently altering this one.

Public surface
--------------
Query functions (all async):
    query_sessions_24h(db, butler_names, timeout_s) -> dict[str, int]

Version marker:
    READ_MODEL_VERSION
"""

from __future__ import annotations

import asyncio
import logging

from butlers.api.db import DatabaseManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------

#: Stability contract — bump to ``butlers_v2`` for breaking changes.
READ_MODEL_VERSION = "butlers_v1"

# ---------------------------------------------------------------------------
# SQL template (v1 schema contract)
# ---------------------------------------------------------------------------

#: Scalar SELECT that returns the count of sessions started in the last 24 hours.
#: Uses ``to_regclass`` to skip the count gracefully when the butler has no
#: ``sessions`` table (returns 0 instead of raising).  Changing this query is a
#: breaking change — create ``butlers_v2`` instead.
SESSIONS_24H_SQL: str = (
    "SELECT CASE WHEN to_regclass('sessions') IS NOT NULL"
    " THEN (SELECT count(*) FROM sessions WHERE started_at >= $1)"
    " ELSE 0 END"
)

# ---------------------------------------------------------------------------
# Query function
# ---------------------------------------------------------------------------


async def query_sessions_24h(
    db: DatabaseManager,
    butler_names: list[str] | None = None,
    *,
    timeout_s: float = 5.0,
) -> dict[str, int]:
    """Return a mapping of butler_name → session count for the last 24 hours.

    Uses a fan-out query against every butler's ``sessions`` table.  This call
    is best-effort: any DB or query failure returns an empty mapping so the
    list endpoint stays available when the DB is unhealthy.

    Butlers without a ``sessions`` table are handled gracefully: the SQL uses
    ``to_regclass`` to short-circuit the count, avoiding warning spam on every
    call.

    Parameters
    ----------
    db:
        The :class:`~butlers.api.db.DatabaseManager` instance.
    butler_names:
        Subset of butler names to query.  Defaults to all registered butlers.
    timeout_s:
        Overall timeout for the fan-out call, in seconds.  Defaults to 5 s.

    Returns
    -------
    dict[str, int]
        ``{butler_name: session_count}`` for each butler that responded.
        Missing or errored butlers are omitted (callers should default to 0).
    """
    from datetime import UTC, datetime, timedelta

    since = datetime.now(UTC) - timedelta(hours=24)
    try:
        raw = await asyncio.wait_for(
            db.fan_out(SESSIONS_24H_SQL, args=(since,), butler_names=butler_names),
            timeout=timeout_s,
        )
    except Exception:
        logger.warning("Failed to fetch 24h session counts", exc_info=True)
        return {}

    result: dict[str, int] = {}
    for butler_name, rows in raw.items():
        if rows:
            try:
                result[butler_name] = int(rows[0][0])
            except (IndexError, TypeError, ValueError):
                result[butler_name] = 0
    return result
