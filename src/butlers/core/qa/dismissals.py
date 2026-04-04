"""CRUD layer for public.qa_dismissals.

Dismissals suppress investigation dispatch for a specific error fingerprint
until the dismissal expires or is manually removed.  At most one dismissal
row exists per fingerprint (upsert semantics via INSERT … ON CONFLICT).

Schema reference (public.qa_dismissals):
    fingerprint       TEXT PRIMARY KEY
    dismissed_until   TIMESTAMPTZ NOT NULL
    dismissed_by      TEXT NOT NULL
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

DismissalRow = dict[str, Any]


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


async def upsert_dismissal(
    pool: asyncpg.Pool,
    fingerprint: str,
    dismissed_by: str,
    duration_hours: float = 24.0,
) -> DismissalRow:
    """Create or extend a dismissal for *fingerprint*.

    Uses INSERT … ON CONFLICT to upsert: if a dismissal already exists for
    this fingerprint, it is replaced with the new ``dismissed_until`` and
    ``dismissed_by`` values.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the public schema.
    fingerprint:
        64-character SHA-256 hex string of the dismissed error.
    dismissed_by:
        Identifier of who triggered the dismissal (e.g. ``"dashboard"``,
        ``"owner"``).
    duration_hours:
        How long the dismissal should last, in hours.  Default 24.

    Returns
    -------
    DismissalRow
        The upserted row as a plain dict.
    """
    dismissed_until = datetime.now(UTC) + timedelta(hours=duration_hours)
    row = await pool.fetchrow(
        """
        INSERT INTO public.qa_dismissals (fingerprint, dismissed_until, dismissed_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (fingerprint) DO UPDATE
            SET dismissed_until = EXCLUDED.dismissed_until,
                dismissed_by    = EXCLUDED.dismissed_by
        RETURNING *
        """,
        fingerprint,
        dismissed_until,
        dismissed_by,
    )
    return dict(row)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------


async def is_dismissed(
    pool: asyncpg.Pool,
    fingerprint: str,
) -> bool:
    """Return ``True`` if *fingerprint* has an active (non-expired) dismissal.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the public schema.
    fingerprint:
        64-character SHA-256 hex string.

    Returns
    -------
    bool
        ``True`` if a dismissal row exists with ``dismissed_until > now()``.
    """
    result = await pool.fetchval(
        """
        SELECT EXISTS(
            SELECT 1
            FROM public.qa_dismissals
            WHERE fingerprint = $1
              AND dismissed_until > now()
        )
        """,
        fingerprint,
    )
    return bool(result)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def list_active_dismissals(
    pool: asyncpg.Pool,
) -> list[DismissalRow]:
    """Return all active (non-expired) dismissals.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the public schema.

    Returns
    -------
    list[DismissalRow]
        Rows with ``dismissed_until > now()``, ordered by created_at DESC.
    """
    rows = await pool.fetch(
        """
        SELECT *
        FROM public.qa_dismissals
        WHERE dismissed_until > now()
        ORDER BY created_at DESC
        """
    )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def delete_dismissal(
    pool: asyncpg.Pool,
    fingerprint: str,
) -> bool:
    """Delete the dismissal for *fingerprint* (immediately re-enables dispatch).

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the public schema.
    fingerprint:
        64-character SHA-256 hex string.

    Returns
    -------
    bool
        ``True`` if a row was deleted, ``False`` if no dismissal existed.
    """
    result = await pool.fetchval(
        """
        DELETE FROM public.qa_dismissals
        WHERE fingerprint = $1
        RETURNING fingerprint
        """,
        fingerprint,
    )
    return result is not None
