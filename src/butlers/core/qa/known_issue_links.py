"""Owner-asserted links from a QA finding to the work item that already tracks it.

A finding linked to a bead, pull request or issue is not re-investigated while that work is
open: the 'tracked' triage gate persists it every patrol with no dispatch and no breaker
budget. Links are written by the owner (dashboard API) or, for QA's own pull requests, by QA
itself; a model-inferred match is not provenance and never creates one (bu-ejow5p.9).

Schema reference (public.qa_known_issue_links): see migration core_372.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Final

import asyncpg

TRACKER_KINDS: Final[tuple[str, ...]] = ("bead", "github_pr", "github_issue")
LINKED_BY: Final[tuple[str, ...]] = ("owner", "qa_auto")
LINK_STATES: Final[tuple[str, ...]] = (
    "tracked",
    "fix_merged",
    "awaiting_recovery",
    "regressed",
    "closed",
)
#: States in which a recurrence is held back instead of investigated.
SUPPRESSING_STATES: Final[frozenset[str]] = frozenset({"tracked", "fix_merged"})
#: A link with no explicit expiry lapses after this long, so it can never suppress forever.
DEFAULT_LINK_TTL: Final[timedelta] = timedelta(days=14)

_COLUMNS = (
    "id, fingerprint, tracker_kind, tracker_ref, linked_by, linked_at, fix_ref, state, expires_at"
)

LinkRow = dict[str, Any]


async def upsert_link(
    pool: asyncpg.Pool,
    *,
    fingerprint: str,
    tracker_kind: str,
    tracker_ref: str,
    linked_by: str,
    expires_at: datetime,
) -> tuple[LinkRow, bool]:
    """Create the link, or refresh an existing one; returns ``(row, created)``.

    ``(fingerprint, tracker_ref)`` is unique, so a repeat write is idempotent. Re-linking a
    closed link reopens it as ``tracked``; any other state is kept, so a repeat never undoes
    progress made by the PR/deploy lifecycle.
    """
    row = await pool.fetchrow(
        f"""
        INSERT INTO public.qa_known_issue_links
            (fingerprint, tracker_kind, tracker_ref, linked_by, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (fingerprint, tracker_ref) DO UPDATE
            SET expires_at = EXCLUDED.expires_at,
                state = CASE
                    WHEN public.qa_known_issue_links.state = 'closed' THEN 'tracked'
                    ELSE public.qa_known_issue_links.state
                END
        RETURNING {_COLUMNS}, (xmax = 0) AS created
        """,
        fingerprint,
        tracker_kind,
        tracker_ref,
        linked_by,
        expires_at,
    )
    data = dict(row)
    return data, bool(data.pop("created"))


async def close_link(pool: asyncpg.Pool, fingerprint: str, tracker_ref: str) -> bool:
    """Close a link so its finding is investigated again; ``False`` if there was none open."""
    closed = await pool.fetchval(
        """
        UPDATE public.qa_known_issue_links
           SET state = 'closed'
         WHERE fingerprint = $1 AND tracker_ref = $2 AND state <> 'closed'
        RETURNING id
        """,
        fingerprint,
        tracker_ref,
    )
    return closed is not None


async def get_gating_link(pool: asyncpg.Pool, fingerprint: str) -> LinkRow | None:
    """The newest unexpired link that still decides this fingerprint's triage, if any.

    ``tracked`` and ``fix_merged`` hold the finding back; ``awaiting_recovery`` re-admits a
    recurrence as a regression. ``regressed`` and ``closed`` links no longer gate.
    """
    row = await pool.fetchrow(
        f"""
        SELECT {_COLUMNS}
          FROM public.qa_known_issue_links
         WHERE fingerprint = $1
           AND state IN ('tracked', 'fix_merged', 'awaiting_recovery')
           AND (expires_at IS NULL OR expires_at > now())
         ORDER BY linked_at DESC
         LIMIT 1
        """,
        fingerprint,
    )
    return dict(row) if row is not None else None


async def mark_link_regressed(pool: asyncpg.Pool, link_id: uuid.UUID) -> None:
    """Record that a recurrence followed the fix, so the link stops gating."""
    await pool.execute(
        "UPDATE public.qa_known_issue_links SET state = 'regressed' "
        "WHERE id = $1 AND state = 'awaiting_recovery'",
        link_id,
    )


async def list_links(pool: asyncpg.Pool, fingerprint: str) -> list[LinkRow]:
    """Every link for a fingerprint, newest first (for the dossier)."""
    rows = await pool.fetch(
        f"SELECT {_COLUMNS} FROM public.qa_known_issue_links "
        "WHERE fingerprint = $1 ORDER BY linked_at DESC",
        fingerprint,
    )
    return [dict(r) for r in rows]
