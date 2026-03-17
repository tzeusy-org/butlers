"""CRUD and gate query layer for shared.healing_attempts.

This module provides the data backbone for the self-healing dispatcher:

- Atomic attempt creation with INSERT ON CONFLICT (novelty+insert in one round-trip)
- State machine enforcement with terminal-state rejection
- Fingerprint collision detection for observability
- Gate query functions (active attempt, cooldown window, concurrency cap,
  circuit breaker, dashboard listing)
- Daemon restart recovery for stale investigating / dispatch_pending rows

All public functions accept an asyncpg Pool so callers can use any pool
(per-butler pool or shared pool depending on deployment).

Schema reference (shared.healing_attempts):
    id              UUID PK
    fingerprint     TEXT NOT NULL
    butler_name     TEXT NOT NULL
    status          TEXT NOT NULL DEFAULT 'investigating'
    severity        INTEGER NOT NULL
    exception_type  TEXT NOT NULL
    call_site       TEXT NOT NULL
    sanitized_msg   TEXT
    branch_name     TEXT
    worktree_path   TEXT
    pr_url          TEXT
    pr_number       INTEGER
    session_ids     UUID[] NOT NULL DEFAULT '{}'
    healing_session_id UUID
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    closed_at       TIMESTAMPTZ
    error_detail    TEXT

Status lifecycle
----------------
``dispatch_pending``
    Created by the retry endpoint when no in-process dispatch hook is
    configured.  The row exists but no healing agent has been spawned yet.
    On daemon restart, ``recover_stale_attempts`` picks up these rows and
    re-dispatches the healing agent (transition → ``investigating``).
    After 30 minutes without dispatch the row is failed instead.

``investigating``
    The healing agent is actively running.  Transitions to ``pr_open``
    (success path) or ``failed`` / ``unfixable`` / ``anonymization_failed``
    / ``timeout`` (failure paths).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: All valid status values for healing_attempts.
VALID_STATUSES = frozenset(
    {
        "dispatch_pending",
        "investigating",
        "pr_open",
        "pr_merged",
        "failed",
        "unfixable",
        "anonymization_failed",
        "timeout",
    }
)

#: Terminal states — no further transitions are allowed once reached.
TERMINAL_STATUSES = frozenset(
    {"pr_merged", "failed", "unfixable", "anonymization_failed", "timeout"}
)

#: Active (non-terminal) statuses — only one row per fingerprint may exist in these states
#: (enforced by partial unique index on the database).
ACTIVE_STATUSES = frozenset({"dispatch_pending", "investigating", "pr_open"})

#: Valid transitions from each state (target states).
_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    # dispatch_pending → investigating (agent dispatched) or failed (extended timeout)
    "dispatch_pending": frozenset({"investigating", "failed"}),
    "investigating": frozenset(
        {"pr_open", "failed", "unfixable", "anonymization_failed", "timeout"}
    ),
    "pr_open": frozenset({"pr_merged", "failed"}),
    # Terminal states — no outgoing transitions
    "pr_merged": frozenset(),
    "failed": frozenset(),
    "unfixable": frozenset(),
    "anonymization_failed": frozenset(),
    "timeout": frozenset(),
}

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

HealingAttemptRow = dict[str, Any]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _decode_row(row: asyncpg.Record) -> HealingAttemptRow:
    """Convert an asyncpg Record to a plain dict."""
    return dict(row)


# ---------------------------------------------------------------------------
# Atomic attempt creation
# ---------------------------------------------------------------------------


async def create_or_join_attempt(
    pool: asyncpg.Pool,
    fingerprint: str,
    butler_name: str,
    severity: int,
    exception_type: str,
    call_site: str,
    session_id: uuid.UUID,
    sanitized_msg: str | None = None,
) -> tuple[uuid.UUID, bool]:
    """Atomically create a new healing attempt or join an existing active one.

    Uses INSERT … ON CONFLICT to ensure at most one ``investigating`` row
    exists per fingerprint at any time (the partial unique index prevents
    duplicates).  When a conflict occurs the calling session's ID is appended
    to the existing attempt's ``session_ids`` array (idempotent — no duplicate
    entries).

    Fingerprint collision detection is performed on join: when the
    ``(exception_type, call_site)`` of the incoming report differs from what
    is stored on the existing attempt a CRITICAL log is emitted.  The session
    is still joined — the collision is an observability signal, not a blocker.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    fingerprint:
        64-character SHA-256 hex string identifying the error.
    butler_name:
        Name of the butler that experienced the failure.
    severity:
        Integer severity score (0=critical … 4=info).
    exception_type:
        Fully qualified exception class name.
    call_site:
        ``<file>:<function>`` of the innermost app frame.
    session_id:
        UUID of the failed session being linked to this attempt.
    sanitized_msg:
        Optional sanitized error message with dynamic values replaced.

    Returns
    -------
    (attempt_id, is_new):
        ``attempt_id`` — UUID of the row (new or existing).
        ``is_new`` — True if a new row was inserted, False if an existing
        active attempt was joined.
    """
    # The INSERT targets the partial unique index:
    #   UNIQUE (fingerprint) WHERE status IN ('dispatch_pending', 'investigating', 'pr_open')
    #
    # On conflict we:
    # 1. Append the session_id (only if not already present — idempotent).
    # 2. Return the existing row's id + its exception_type / call_site for
    #    collision detection.
    #
    # We distinguish "was inserted" vs "was joined" by comparing the RETURNING
    # xmax value: on a fresh INSERT xmax=0; on an UPDATE xmax>0.  An
    # alternative is to use a CTE with an explicit flag column.
    sql = """
        WITH inserted AS (
            INSERT INTO shared.healing_attempts (
                fingerprint, butler_name, status, severity,
                exception_type, call_site, sanitized_msg, session_ids
            )
            VALUES ($1, $2, 'investigating', $3, $4, $5, $6, ARRAY[$7::uuid])
            ON CONFLICT (fingerprint)
            WHERE status IN ('dispatch_pending', 'investigating', 'pr_open')
            DO UPDATE
                SET session_ids = CASE
                    WHEN $7::uuid = ANY(shared.healing_attempts.session_ids)
                        THEN shared.healing_attempts.session_ids
                    ELSE array_append(shared.healing_attempts.session_ids, $7::uuid)
                END,
                updated_at = now()
            RETURNING
                id,
                exception_type  AS existing_exc_type,
                call_site       AS existing_call_site,
                (xmax = 0)      AS was_inserted
        )
        SELECT id, existing_exc_type, existing_call_site, was_inserted
        FROM inserted
    """
    row = await pool.fetchrow(
        sql,
        fingerprint,
        butler_name,
        severity,
        exception_type,
        call_site,
        sanitized_msg,
        str(session_id),
    )

    if row is None:
        # Should not happen — the INSERT always returns a row.
        raise RuntimeError(
            f"create_or_join_attempt: unexpected empty result for fingerprint={fingerprint!r}"
        )

    attempt_id: uuid.UUID = row["id"]
    was_inserted: bool = bool(row["was_inserted"])

    if not was_inserted:
        # We joined an existing attempt — run collision detection.
        existing_exc = row["existing_exc_type"]
        existing_cs = row["existing_call_site"]
        if existing_exc != exception_type or existing_cs != call_site:
            logger.critical(
                "Fingerprint collision detected for %s: existing=%s@%s, new=%s@%s",
                fingerprint,
                existing_exc,
                existing_cs,
                exception_type,
                call_site,
            )
        else:
            logger.debug(
                "Session %s joined existing healing attempt %s for fingerprint %s",
                session_id,
                attempt_id,
                fingerprint,
            )

    return attempt_id, was_inserted


# ---------------------------------------------------------------------------
# Status updates (state machine)
# ---------------------------------------------------------------------------


async def update_attempt_status(
    pool: asyncpg.Pool,
    attempt_id: uuid.UUID,
    new_status: str,
    *,
    error_detail: str | None = None,
    pr_url: str | None = None,
    pr_number: int | None = None,
    branch_name: str | None = None,
    worktree_path: str | None = None,
    healing_session_id: uuid.UUID | None = None,
) -> bool:
    """Transition a healing attempt to a new status, enforcing the state machine.

    Terminal states reject further transitions (logs a warning, returns False).
    The ``updated_at`` timestamp is always refreshed.  ``closed_at`` is set
    when transitioning to any terminal state.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    attempt_id:
        UUID of the healing attempt to update.
    new_status:
        Target status.  Must be a member of ``VALID_STATUSES``.
    error_detail:
        Optional human-readable explanation for failures/timeouts.
    pr_url:
        PR URL (set when transitioning to ``pr_open``).
    pr_number:
        PR number (set when transitioning to ``pr_open``).
    branch_name:
        Git branch name for the healing worktree.
    worktree_path:
        Filesystem path of the healing worktree.
    healing_session_id:
        UUID of the spawned healing agent session.

    Returns
    -------
    bool
        True if the row was updated, False if the transition was rejected
        (terminal state, invalid status, or row not found).
    """
    if new_status not in VALID_STATUSES:
        logger.warning(
            "update_attempt_status: invalid status %r for attempt %s", new_status, attempt_id
        )
        return False

    # Fetch the current status to validate the transition.
    current_row = await pool.fetchrow(
        "SELECT status FROM shared.healing_attempts WHERE id = $1",
        attempt_id,
    )
    if current_row is None:
        logger.warning("update_attempt_status: attempt %s not found", attempt_id)
        return False

    current_status: str = current_row["status"]

    if current_status in TERMINAL_STATUSES:
        logger.warning(
            "update_attempt_status: attempt %s is in terminal state %r — ignoring transition to %r",
            attempt_id,
            current_status,
            new_status,
        )
        return False

    allowed = _VALID_TRANSITIONS.get(current_status, frozenset())
    if new_status not in allowed:
        logger.warning(
            "update_attempt_status: invalid transition %r → %r for attempt %s",
            current_status,
            new_status,
            attempt_id,
        )
        return False

    is_terminal = new_status in TERMINAL_STATUSES

    # Include the expected current_status in the WHERE clause to close the race window
    # between the SELECT above and this UPDATE.  If another process transitions the
    # attempt in the meantime, the UPDATE will affect 0 rows and we return False.
    updated = await pool.fetchval(
        """
        UPDATE shared.healing_attempts
        SET
            status              = $2,
            updated_at          = now(),
            closed_at           = CASE WHEN $3 THEN now() ELSE closed_at END,
            error_detail        = COALESCE($4, error_detail),
            pr_url              = COALESCE($5, pr_url),
            pr_number           = COALESCE($6, pr_number),
            branch_name         = COALESCE($7, branch_name),
            worktree_path       = COALESCE($8, worktree_path),
            healing_session_id  = COALESCE($9, healing_session_id)
        WHERE id = $1 AND status = $10
        RETURNING id
        """,
        attempt_id,
        new_status,
        is_terminal,
        error_detail,
        pr_url,
        pr_number,
        branch_name,
        worktree_path,
        str(healing_session_id) if healing_session_id else None,
        current_status,
    )
    if updated is None:
        logger.warning(
            "update_attempt_status: attempt %s not found or status changed from %r during update",
            attempt_id,
            current_status,
        )
        return False

    logger.info(
        "Healing attempt %s transitioned %r → %r",
        attempt_id,
        current_status,
        new_status,
    )
    return True


# ---------------------------------------------------------------------------
# Gate query functions
# ---------------------------------------------------------------------------


async def get_active_attempt(
    pool: asyncpg.Pool,
    fingerprint: str,
) -> HealingAttemptRow | None:
    """Return the active (investigating or pr_open) attempt for *fingerprint*, or None.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    fingerprint:
        64-character SHA-256 hex string.

    Returns
    -------
    HealingAttemptRow or None
    """
    row = await pool.fetchrow(
        """
        SELECT *
        FROM shared.healing_attempts
        WHERE fingerprint = $1
          AND status IN ('dispatch_pending', 'investigating', 'pr_open')
        LIMIT 1
        """,
        fingerprint,
    )
    return _decode_row(row) if row is not None else None


async def get_recent_attempt(
    pool: asyncpg.Pool,
    fingerprint: str,
    window_minutes: int,
) -> HealingAttemptRow | None:
    """Return the most recent terminal attempt for *fingerprint* closed within the window.

    Used for cooldown gate checks: prevents re-investigating an error that was
    recently resolved (or failed) within *window_minutes*.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    fingerprint:
        64-character SHA-256 hex string.
    window_minutes:
        How far back (in minutes) to look for a terminal attempt.

    Returns
    -------
    HealingAttemptRow or None
        The most recently closed terminal attempt, or None if none exists.
    """
    row = await pool.fetchrow(
        """
        SELECT *
        FROM shared.healing_attempts
        WHERE fingerprint = $1
          AND status = ANY($2::text[])
          AND closed_at >= now() - ($3 * INTERVAL '1 minute')
        ORDER BY closed_at DESC
        LIMIT 1
        """,
        fingerprint,
        list(TERMINAL_STATUSES),
        window_minutes,
    )
    return _decode_row(row) if row is not None else None


async def count_active_attempts(pool: asyncpg.Pool) -> int:
    """Return the count of rows with status ``investigating``.

    Used for the concurrency cap gate: the dispatcher rejects new healing
    dispatches when this count reaches ``max_concurrent``.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.

    Returns
    -------
    int
    """
    result: int = await pool.fetchval(
        """
        SELECT COUNT(*)
        FROM shared.healing_attempts
        WHERE status = 'investigating'
        """
    )
    return int(result)


async def get_recent_terminal_statuses(
    pool: asyncpg.Pool,
    limit: int,
) -> list[str]:
    """Return the status values of the N most recent terminal attempts.

    Ordered by ``closed_at DESC``.  ``unfixable`` entries are included —
    the caller decides whether to count them as failures for circuit-breaker
    purposes.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    limit:
        Maximum number of entries to return.

    Returns
    -------
    list[str]
        Status strings (e.g. ``["failed", "failed", "unfixable", "pr_merged"]``).
    """
    rows = await pool.fetch(
        """
        SELECT status
        FROM shared.healing_attempts
        WHERE status = ANY($1::text[])
        ORDER BY closed_at DESC
        LIMIT $2
        """,
        list(TERMINAL_STATUSES),
        limit,
    )
    return [row["status"] for row in rows]


async def list_attempts(
    pool: asyncpg.Pool,
    limit: int = 20,
    offset: int = 0,
    status_filter: str | None = None,
    butler_name: str | None = None,
) -> list[HealingAttemptRow]:
    """Return paginated healing attempt rows for dashboard display.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    limit:
        Maximum number of rows to return.
    offset:
        Number of rows to skip (for pagination).
    status_filter:
        If provided, only return rows with this status value.
    butler_name:
        If provided, only return rows for this butler.  When omitted, rows
        for all butlers are returned (used by admin/cross-butler dashboards).

    Returns
    -------
    list[HealingAttemptRow]
        Rows ordered by ``created_at DESC``.
    """
    if status_filter is not None and butler_name is not None:
        rows = await pool.fetch(
            """
            SELECT *
            FROM shared.healing_attempts
            WHERE status = $1
              AND butler_name = $2
            ORDER BY created_at DESC
            LIMIT $3 OFFSET $4
            """,
            status_filter,
            butler_name,
            limit,
            offset,
        )
    elif status_filter is not None:
        rows = await pool.fetch(
            """
            SELECT *
            FROM shared.healing_attempts
            WHERE status = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            status_filter,
            limit,
            offset,
        )
    elif butler_name is not None:
        rows = await pool.fetch(
            """
            SELECT *
            FROM shared.healing_attempts
            WHERE butler_name = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            butler_name,
            limit,
            offset,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT *
            FROM shared.healing_attempts
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
    return [_decode_row(row) for row in rows]


# ---------------------------------------------------------------------------
# Daemon restart recovery
# ---------------------------------------------------------------------------


async def recover_stale_attempts(
    pool: asyncpg.Pool,
    timeout_minutes: int = 30,
    dispatch_pending_timeout_minutes: int = 30,
) -> tuple[int, list[dict]]:
    """Recover incomplete healing attempts left behind by a prior daemon crash.

    Called once on dispatcher startup, **before** accepting new errors.

    Rules:
    1. ``investigating`` rows with ``updated_at`` older than *timeout_minutes*
       and a non-NULL ``healing_session_id`` → transition to ``timeout``
       with a recovery error_detail message.
    2. ``investigating`` rows with ``healing_session_id = NULL`` and
       ``created_at`` older than 5 minutes → transition to ``failed``
       (agent was never spawned before the crash).
    3. ``dispatch_pending`` rows older than *dispatch_pending_timeout_minutes*
       → transition to ``failed`` (dispatch never happened after extended wait).
    4. ``dispatch_pending`` rows newer than *dispatch_pending_timeout_minutes*
       are returned as a list for the caller to re-dispatch immediately.

    Rows updated within the last 5 minutes are left alone — a still-running
    agent may have been in the middle of work before the daemon restarted.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    timeout_minutes:
        How many minutes of inactivity before an ``investigating`` row is
        considered stale.  Should match the dispatcher's ``[healing]
        timeout_minutes`` config value.
    dispatch_pending_timeout_minutes:
        How many minutes before a ``dispatch_pending`` row is given up on
        (transitioned to ``failed``).  Defaults to 30 minutes, which is
        longer than the ``investigating`` stale check window to allow the
        daemon time to re-dispatch on restart.

    Returns
    -------
    tuple[int, list[dict]]
        ``(recovered_count, pending_rows)`` where ``recovered_count`` is the
        total number of rows closed (timed-out + failed), and ``pending_rows``
        is a list of ``dispatch_pending`` row dicts that should be re-dispatched
        by the caller.
    """
    # Rule 1: stale rows with a healing_session_id → timeout
    timeout_count: int = await pool.fetchval(
        """
        WITH recovered AS (
            UPDATE shared.healing_attempts
            SET
                status       = 'timeout',
                updated_at   = now(),
                closed_at    = now(),
                error_detail = 'Recovered on daemon restart — investigation was interrupted'
            WHERE status = 'investigating'
              AND healing_session_id IS NOT NULL
              AND updated_at < now() - ($1 * INTERVAL '1 minute')
            RETURNING id
        )
        SELECT COUNT(*) FROM recovered
        """,
        timeout_minutes,
    )

    # Rule 2: rows with no session_id and old enough → failed
    failed_count: int = await pool.fetchval(
        """
        WITH recovered AS (
            UPDATE shared.healing_attempts
            SET
                status       = 'failed',
                updated_at   = now(),
                closed_at    = now(),
                error_detail = 'Recovered on daemon restart — agent was never spawned'
            WHERE status = 'investigating'
              AND healing_session_id IS NULL
              AND created_at < now() - INTERVAL '5 minutes'
            RETURNING id
        )
        SELECT COUNT(*) FROM recovered
        """,
    )

    # Rule 3: dispatch_pending rows past the extended timeout → failed
    dp_failed_count: int = await pool.fetchval(
        """
        WITH recovered AS (
            UPDATE shared.healing_attempts
            SET
                status       = 'failed',
                updated_at   = now(),
                closed_at    = now(),
                error_detail = 'Recovered on daemon restart — dispatch never completed'
            WHERE status = 'dispatch_pending'
              AND created_at < now() - ($1 * INTERVAL '1 minute')
            RETURNING id
        )
        SELECT COUNT(*) FROM recovered
        """,
        dispatch_pending_timeout_minutes,
    )

    # Rule 4: dispatch_pending rows within the timeout — return them for re-dispatch
    pending_rows_raw = await pool.fetch(
        """
        SELECT *
        FROM shared.healing_attempts
        WHERE status = 'dispatch_pending'
          AND created_at >= now() - ($1 * INTERVAL '1 minute')
        ORDER BY created_at ASC
        """,
        dispatch_pending_timeout_minutes,
    )
    pending_rows = [_decode_row(row) for row in pending_rows_raw]

    total = int(timeout_count) + int(failed_count) + int(dp_failed_count)
    if total > 0:
        logger.info(
            "recover_stale_attempts: recovered %d rows "
            "(%d timeout, %d failed, %d dispatch_pending_failed)",
            total,
            int(timeout_count),
            int(failed_count),
            int(dp_failed_count),
        )
    if pending_rows:
        logger.info(
            "recover_stale_attempts: found %d dispatch_pending rows to re-dispatch",
            len(pending_rows),
        )
    return total, pending_rows


# ---------------------------------------------------------------------------
# Single-row lookup
# ---------------------------------------------------------------------------


async def get_attempt(
    pool: asyncpg.Pool,
    attempt_id: uuid.UUID,
) -> HealingAttemptRow | None:
    """Return a single healing attempt by ID, or None if not found.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    attempt_id:
        UUID primary key of the attempt.

    Returns
    -------
    HealingAttemptRow or None
    """
    row = await pool.fetchrow(
        "SELECT * FROM shared.healing_attempts WHERE id = $1",
        attempt_id,
    )
    return _decode_row(row) if row is not None else None
