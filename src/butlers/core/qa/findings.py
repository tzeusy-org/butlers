"""CRUD layer for public.qa_findings.

Provides insert and query operations for QA finding records produced during
patrol cycles.  Each finding represents a normalized error signal discovered
by a DiscoverySource.

Schema reference (public.qa_findings):
    id                              UUID PK
    patrol_id                       UUID FK → qa_patrols
    fingerprint                     TEXT NOT NULL
    source_type                     TEXT NOT NULL
    source_butler                   TEXT NOT NULL
    severity                        INTEGER NOT NULL
    exception_type                  TEXT NOT NULL
    event_summary                   TEXT NOT NULL
    call_site                       TEXT NOT NULL
    occurrence_count                INTEGER NOT NULL DEFAULT 1
    first_seen                      TIMESTAMPTZ NOT NULL
    last_seen                       TIMESTAMPTZ NOT NULL
    dedup_reason                    TEXT (nullable)
    healing_attempt_id              UUID FK → healing_attempts (nullable)
    source_session_trigger_source   TEXT (nullable) — trigger_source of the originating session
    structured_evidence             JSONB (nullable) — structured diagnostic evidence
    dispatch_queued                 BOOLEAN NOT NULL DEFAULT FALSE — queued for retry after cap skip
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg

from butlers.core.qa.models import QaFinding

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

QaFindingRow = dict[str, Any]


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------


async def insert_finding(
    pool: asyncpg.Pool,
    patrol_id: uuid.UUID,
    finding: QaFinding,
    dedup_reason: str | None,
    healing_attempt_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert a QA finding record for the given patrol cycle.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the public schema.
    patrol_id:
        UUID of the qa_patrols row that produced this finding.
    finding:
        Normalized ``QaFinding`` from a discovery source.
    dedup_reason:
        ``None`` for novel findings.  One of ``"active_investigation"``,
        ``"dismissed"``, ``"cooldown"`` for deduplicated findings.
    healing_attempt_id:
        UUID of the healing_attempts row created for this finding, or
        ``None`` if no investigation was dispatched.

    Returns
    -------
    uuid.UUID
        The ``id`` of the newly inserted row.
    """
    structured_evidence_json: str | None = None
    if finding.structured_evidence is not None:
        structured_evidence_json = json.dumps(finding.structured_evidence)

    row_id = await pool.fetchval(
        """
        INSERT INTO public.qa_findings (
            patrol_id, fingerprint, source_type, source_butler,
            severity, exception_type, event_summary, call_site,
            occurrence_count, first_seen, last_seen,
            dedup_reason, healing_attempt_id,
            source_session_trigger_source, structured_evidence
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::jsonb)
        RETURNING id
        """,
        patrol_id,
        finding.fingerprint,
        finding.source_type,
        finding.source_butler,
        finding.severity,
        finding.exception_type,
        finding.event_summary,
        finding.call_site,
        finding.occurrence_count,
        finding.first_seen,
        finding.last_seen,
        dedup_reason,
        str(healing_attempt_id) if healing_attempt_id is not None else None,
        finding.source_session_trigger_source,
        structured_evidence_json,
    )
    return row_id


async def update_finding_attempt(
    pool: asyncpg.Pool,
    finding_id: uuid.UUID,
    healing_attempt_id: uuid.UUID,
) -> None:
    """Link a finding to a healing attempt after dispatch.

    Called after ``create_or_join_attempt`` succeeds so that the finding
    record references the investigation that was spawned for it.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the public schema.
    finding_id:
        UUID of the qa_findings row to update.
    healing_attempt_id:
        UUID of the healing_attempts row to link.
    """
    await pool.execute(
        """
        UPDATE public.qa_findings
        SET healing_attempt_id = $1
        WHERE id = $2
        """,
        str(healing_attempt_id),
        finding_id,
    )


async def update_finding_dedup_reason(
    pool: asyncpg.Pool,
    finding_id: uuid.UUID,
    dedup_reason: str,
) -> None:
    """Write an authoritative gate rejection reason back to a qa_findings row.

    Called by the QA dispatcher when a post-novelty gate (cooldown, concurrency
    cap, circuit breaker, no-model) rejects a finding after triage classified
    it as novel.  Triage performs a fast non-atomic dedup check; the dispatcher
    performs the authoritative atomic claim.  When the dispatcher rejects a
    triage-novel finding, this function records the authoritative reason.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the public schema.
    finding_id:
        UUID of the qa_findings row to update.
    dedup_reason:
        Authoritative gate rejection reason, e.g. ``"cooldown"``,
        ``"concurrency_cap"``, ``"circuit_breaker"``, ``"no_model"``,
        ``"already_investigating"``.
    """
    await pool.execute(
        """
        UPDATE public.qa_findings
        SET dedup_reason = $1
        WHERE id = $2
        """,
        dedup_reason,
        finding_id,
    )


async def update_finding_dispatch_queued(
    pool: asyncpg.Pool,
    finding_id: uuid.UUID,
    queued: bool,
) -> None:
    """Set or clear the dispatch_queued flag on a qa_findings row.

    Set to ``True`` by the dispatcher for findings that were skipped due to
    concurrency pressure.  Set back to ``False`` once the finding is loaded
    for re-triage in a later patrol cycle.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the public schema.
    finding_id:
        UUID of the qa_findings row to update.
    queued:
        ``True`` to mark the finding as queued for retry; ``False`` to clear.
    """
    await pool.execute(
        """
        UPDATE public.qa_findings
        SET dispatch_queued = $1
        WHERE id = $2
        """,
        queued,
        finding_id,
    )


async def get_dispatch_queued_findings(
    pool: asyncpg.Pool,
    limit: int = 50,
) -> list[QaFindingRow]:
    """Return findings queued for retry dispatch and atomically clear the flag.

    Fetches rows with ``dispatch_queued = TRUE``, ordered by severity (critical
    first) and occurrence_count descending.  Immediately clears
    ``dispatch_queued`` on all returned rows so that a concurrent patrol cycle
    cannot pick up the same rows.

    The caller is responsible for passing the reconstituted findings back
    through triage so that freshness checks (active investigation, dismissal,
    cooldown) are re-applied before any dispatch attempt.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the public schema.
    limit:
        Maximum number of queued rows to return per call.  Acts as a
        concurrency-pressure relief valve: bounded batch size prevents a
        large backlog from overwhelming the next patrol.

    Returns
    -------
    list[QaFindingRow]
        Rows ordered by severity ASC, occurrence_count DESC.  Empty list
        when no findings are queued.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT *
                FROM public.qa_findings
                WHERE dispatch_queued = TRUE
                ORDER BY severity ASC, occurrence_count DESC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
                """,
                limit,
            )
            if rows:
                ids = [row["id"] for row in rows]
                await conn.execute(
                    """
                    UPDATE public.qa_findings
                    SET dispatch_queued = FALSE
                    WHERE id = ANY($1::uuid[])
                    """,
                    ids,
                )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


async def get_findings_by_patrol(
    pool: asyncpg.Pool,
    patrol_id: uuid.UUID,
) -> list[QaFindingRow]:
    """Return all findings for a given patrol, ordered by severity asc.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the public schema.
    patrol_id:
        UUID of the qa_patrols row.

    Returns
    -------
    list[QaFindingRow]
        Rows ordered by severity (critical first).
    """
    rows = await pool.fetch(
        """
        SELECT *
        FROM public.qa_findings
        WHERE patrol_id = $1
        ORDER BY severity ASC, occurrence_count DESC
        """,
        patrol_id,
    )
    return [dict(row) for row in rows]


async def get_findings_by_fingerprint(
    pool: asyncpg.Pool,
    fingerprint: str,
    limit: int = 20,
) -> list[QaFindingRow]:
    """Return recent findings with a given fingerprint.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the public schema.
    fingerprint:
        64-character SHA-256 hex string.
    limit:
        Maximum number of rows to return.

    Returns
    -------
    list[QaFindingRow]
        Rows ordered by created_at DESC.
    """
    rows = await pool.fetch(
        """
        SELECT *
        FROM public.qa_findings
        WHERE fingerprint = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        fingerprint,
        limit,
    )
    return [dict(row) for row in rows]
