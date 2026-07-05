"""Identity resolution dashboard API.

Provides:

- ``router`` — endpoints under ``/api/identity``

Endpoints
---------
GET /api/identity/email-match-rate — email sender -> entity match-rate metric (bu-qeaou)

Fleet-wide degraded-envelope convention (CLAUDE.md, bu-qvnce.1): a source that
raises or is unreachable must never render as a truthful empty/zero result —
see ``aggregates_available`` on :class:`EmailMatchRate`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.identity import normalize_email_sender

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/identity", tags=["identity"])

# Defensive bound on the number of ingestion_events rows scanned per request —
# this is a synchronous dashboard-metric query, not a background job, so it
# stays cheap regardless of table growth. Not treated as a degraded source
# (unlike a query failure): it is an approximation bound on an otherwise
# healthy read, so aggregates_available stays True when hit.
_MAX_SCANNED_ROWS = 200_000


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


class EmailMatchRate(BaseModel):
    """Email sender -> entity match-rate summary (bu-qeaou).

    ``distinct_senders`` counts normalized (bare, lowercased) email addresses
    observed in ``public.ingestion_events`` within the lookback window.
    ``matched_senders`` is the subset that resolve to an entity via an active
    ``has-email`` fact in ``relationship.entity_facts``.
    """

    distinct_senders: int = 0
    matched_senders: int = 0
    match_rate: float | None = None
    lookback_days: int = 180
    scanned_rows_truncated: bool = False
    aggregates_available: bool = True


@router.get("/email-match-rate")
async def get_email_match_rate(
    lookback_days: int = Query(default=180, ge=1, le=3650),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[EmailMatchRate]:
    """Return the email sender -> entity match-rate metric.

    Degrades to ``aggregates_available=False`` (zeros elsewhere) when the
    relationship schema is unreachable or the underlying query fails — this
    must never be mistaken for "0% match rate" by the UI (see
    ``SourceDegradedNote`` in ``frontend/src/components/ui/query-boundary.tsx``).
    """
    if "relationship" not in db_mgr.butler_names:
        return ApiResponse(
            data=EmailMatchRate(lookback_days=lookback_days, aggregates_available=False)
        )

    try:
        pool = db_mgr.pool("relationship")
        since = datetime.now(UTC) - timedelta(days=lookback_days)

        rows = await pool.fetch(
            """
            SELECT source_sender_identity AS raw_address
            FROM public.ingestion_events
            WHERE source_channel = 'email'
              AND status = 'ingested'
              AND source_sender_identity IS NOT NULL
              AND received_at >= $1
            ORDER BY received_at DESC
            LIMIT $2
            """,
            since,
            _MAX_SCANNED_ROWS + 1,
        )
        truncated = len(rows) > _MAX_SCANNED_ROWS
        if truncated:
            rows = rows[:_MAX_SCANNED_ROWS]

        # Historical rows may still hold a raw "Name <addr>" source_sender_identity
        # (pre bu-qeaou ingest-time normalization) — normalize + dedupe in Python
        # so the same physical sender under different raw formatting counts once.
        addresses = {normalize_email_sender(r["raw_address"]) for r in rows if r["raw_address"]}
        addresses.discard("")
        distinct_senders = len(addresses)

        matched_senders = 0
        if addresses:
            matched_rows = await pool.fetch(
                """
                SELECT DISTINCT object
                FROM relationship.entity_facts
                WHERE predicate = 'has-email'
                  AND object_kind = 'literal'
                  AND validity = 'active'
                  AND object = ANY($1::text[])
                """,
                list(addresses),
            )
            matched_senders = len(matched_rows)

        match_rate = (matched_senders / distinct_senders) if distinct_senders > 0 else None

        return ApiResponse(
            data=EmailMatchRate(
                distinct_senders=distinct_senders,
                matched_senders=matched_senders,
                match_rate=match_rate,
                lookback_days=lookback_days,
                scanned_rows_truncated=truncated,
                aggregates_available=True,
            )
        )
    except Exception:
        logger.warning("get_email_match_rate: query failed; degrading", exc_info=True)
        return ApiResponse(
            data=EmailMatchRate(lookback_days=lookback_days, aggregates_available=False)
        )
