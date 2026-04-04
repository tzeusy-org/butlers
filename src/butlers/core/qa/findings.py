"""CRUD layer for public.qa_findings.

Provides insert and query operations for QA finding records produced during
patrol cycles.  Each finding represents a normalized error signal discovered
by a DiscoverySource.

Schema reference (public.qa_findings):
    id                  UUID PK
    patrol_id           UUID FK → qa_patrols
    fingerprint         TEXT NOT NULL
    source_type         TEXT NOT NULL
    source_butler       TEXT NOT NULL
    severity            INTEGER NOT NULL
    exception_type      TEXT NOT NULL
    event_summary       TEXT NOT NULL
    call_site           TEXT NOT NULL
    occurrence_count    INTEGER NOT NULL DEFAULT 1
    first_seen          TIMESTAMPTZ NOT NULL
    last_seen           TIMESTAMPTZ NOT NULL
    dedup_reason        TEXT (nullable)
    healing_attempt_id  UUID FK → healing_attempts (nullable)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
"""

from __future__ import annotations

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
    row_id = await pool.fetchval(
        """
        INSERT INTO public.qa_findings (
            patrol_id, fingerprint, source_type, source_butler,
            severity, exception_type, event_summary, call_site,
            occurrence_count, first_seen, last_seen,
            dedup_reason, healing_attempt_id
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
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
