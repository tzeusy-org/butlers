"""CRUD and gate query layer for public.healing_attempts.

This module provides the data backbone for the self-healing dispatcher:

- Atomic attempt creation with INSERT ON CONFLICT (novelty+insert in one round-trip)
- State machine enforcement with terminal-state rejection
- Fingerprint collision detection for observability
- Gate query functions (active attempt, cooldown window, concurrency cap,
  circuit breaker, dashboard listing)
- Daemon restart recovery for stale investigating rows (deadline-aware)
- Child session tracking via public.healing_attempt_sessions
- Dispatch-decision recording via public.healing_dispatch_events

All public functions accept an asyncpg Pool so callers can use any pool
(per-butler pool or shared pool depending on deployment).

Schema reference (public.healing_attempts):
    id                   UUID PK
    fingerprint          TEXT NOT NULL
    butler_name          TEXT NOT NULL
    status               TEXT NOT NULL DEFAULT 'investigating'
    severity             INTEGER NOT NULL
    exception_type       TEXT NOT NULL
    call_site            TEXT NOT NULL
    sanitized_msg        TEXT
    branch_name          TEXT
    worktree_path        TEXT
    pr_url               TEXT
    pr_number            INTEGER
    session_ids          UUID[] NOT NULL DEFAULT '{}'
    healing_session_id   UUID
    current_phase        TEXT          (NULL for single-session attempts)
    workflow_deadline_at TIMESTAMPTZ   (NULL for legacy rows — see recovery)
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
    closed_at            TIMESTAMPTZ
    error_detail         TEXT

Status lifecycle
----------------
``investigating``
    The healing agent is actively running.  Transitions to ``pr_open``
    (success path) or ``failed`` / ``unfixable`` / ``anonymization_failed``
    / ``timeout`` (failure paths).

    Note: ``dispatch_pending`` is NOT a valid status.  The novelty claim and
    row creation are atomic — a row either enters ``investigating`` immediately
    (launch succeeded) or is never created (launch failed before insert).
    Any legacy ``dispatch_pending`` rows are migrated to ``failed`` by
    core_066.

``pr_open``
    A PR has been submitted.  Transitions to ``pr_merged`` (merged by a human)
    or ``failed`` (PR closed without merge).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Type aliases
HealingAttemptSessionRow = dict[str, Any]
HealingDispatchEventRow = dict[str, Any]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: All valid status values for healing_attempts.
#: Note: ``dispatch_pending`` was removed in core_066.  The novelty claim and
#: row creation are atomic — rows are inserted as ``investigating`` directly.
VALID_STATUSES = frozenset(
    {
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

#: Active (non-terminal) statuses — only one row per fingerprint may exist in
#: these states (enforced by partial unique index on the database).
ACTIVE_STATUSES = frozenset({"investigating", "pr_open"})

#: Valid transitions from each state (target states).
_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
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

#: Valid status values for healing_attempt_sessions.
PHASE_SESSION_STATUSES = frozenset({"running", "completed", "failed", "timeout", "cancelled"})

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
    qa_patrol_id: uuid.UUID | None = None,
    workflow_deadline_minutes: int = 60,
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
    qa_patrol_id:
        Optional UUID of the QA patrol that originated this investigation.
        When set, the ``qa_patrol_id`` column on the new row is populated,
        marking this as a QA-originated attempt.  Only applies to newly
        inserted rows — joins to existing attempts do not update this field.
    workflow_deadline_minutes:
        Hard limit (in minutes) for the entire workflow.  Stored as an
        immutable ``workflow_deadline_at`` timestamp set once at row creation.
        Defaults to 60 minutes.  Only applied to newly inserted rows.

    Returns
    -------
    (attempt_id, is_new):
        ``attempt_id`` — UUID of the row (new or existing).
        ``is_new`` — True if a new row was inserted, False if an existing
        active attempt was joined.
    """
    # The INSERT targets the partial unique index:
    #   UNIQUE (fingerprint) WHERE status IN ('investigating', 'pr_open')
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
            INSERT INTO public.healing_attempts (
                fingerprint, butler_name, status, severity,
                exception_type, call_site, sanitized_msg, session_ids,
                qa_patrol_id, workflow_deadline_at
            )
            VALUES (
                $1, $2, 'investigating', $3, $4, $5, $6, ARRAY[$7::uuid], $8::uuid,
                now() + ($9 * INTERVAL '1 minute')
            )
            ON CONFLICT (fingerprint)
            WHERE status IN ('investigating', 'pr_open')
            DO UPDATE
                SET session_ids = CASE
                    WHEN $7::uuid = ANY(public.healing_attempts.session_ids)
                        THEN public.healing_attempts.session_ids
                    ELSE array_append(public.healing_attempts.session_ids, $7::uuid)
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
        str(qa_patrol_id) if qa_patrol_id is not None else None,
        workflow_deadline_minutes,
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
        "SELECT status FROM public.healing_attempts WHERE id = $1",
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
        UPDATE public.healing_attempts
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
        FROM public.healing_attempts
        WHERE fingerprint = $1
          AND status IN ('investigating', 'pr_open')
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
        FROM public.healing_attempts
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


async def count_active_attempts(pool: asyncpg.Pool, *, qa_only: bool = False) -> int:
    """Return the count of rows with status ``investigating``.

    Used for the concurrency cap gate: the dispatcher rejects new healing
    dispatches when this count reaches ``max_concurrent``.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    qa_only:
        When ``True``, only count rows where ``qa_patrol_id IS NOT NULL``
        (QA-originated investigations).  When ``False`` (default), count all
        ``investigating`` rows regardless of origin (legacy per-butler path).

    Returns
    -------
    int
    """
    if qa_only:
        result: int = await pool.fetchval(
            """
            SELECT COUNT(*)
            FROM public.healing_attempts
            WHERE status = 'investigating'
              AND qa_patrol_id IS NOT NULL
            """
        )
    else:
        result = await pool.fetchval(
            """
            SELECT COUNT(*)
            FROM public.healing_attempts
            WHERE status = 'investigating'
            """
        )
    return int(result)


async def get_recent_terminal_statuses(
    pool: asyncpg.Pool,
    limit: int,
) -> list[str]:
    """Return the status values of the N most recent terminal attempts that launched a session.

    Only attempts with ``healing_session_id IS NOT NULL`` are included —
    gate rejections (which never launch a session) are excluded from the
    circuit-breaker signal.  Ordered by ``closed_at DESC``.  ``unfixable``
    entries are included — the caller decides whether to count them as
    failures for circuit-breaker purposes.

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
        FROM public.healing_attempts
        WHERE status = ANY($1::text[])
          AND healing_session_id IS NOT NULL
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
            FROM public.healing_attempts
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
            FROM public.healing_attempts
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
            FROM public.healing_attempts
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
            FROM public.healing_attempts
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
) -> int:
    """Recover incomplete healing attempts left behind by a prior daemon crash.

    Called once on dispatcher startup, **before** accepting new errors.

    Rules
    -----
    1. ``investigating`` rows with ``workflow_deadline_at IS NOT NULL`` and
       ``now() > workflow_deadline_at`` → transition to ``timeout``.
       (The workflow deadline is the authoritative signal — ``updated_at`` is
       not consulted for these rows.)

    2. ``investigating`` rows with ``workflow_deadline_at IS NULL`` and
       ``updated_at`` older than *timeout_minutes* → transition to ``timeout``
       using the legacy heuristic.  This path exists only for rows created
       before ``workflow_deadline_at`` was introduced (core_066).

    3. ``investigating`` rows with ``healing_session_id = NULL`` and
       ``created_at`` older than 5 minutes → transition to ``failed``
       (agent was never spawned before the crash).  Applies regardless of
       ``workflow_deadline_at`` — no deadline has been consumed if no agent
       ever launched.

    Rows that are within budget (``workflow_deadline_at > now()``) and have a
    live session ID are left untouched — the per-phase watchdog will handle
    them if the agent is truly dead.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    timeout_minutes:
        Fallback timeout for legacy rows where ``workflow_deadline_at IS NULL``.
        Should match the dispatcher's ``[healing] timeout_minutes`` config.

    Returns
    -------
    int
        Total number of rows closed (timed-out + failed).
    """
    # Rule 1: deadline-expired rows with a session_id → timeout (deadline authority)
    deadline_timeout_count: int = await pool.fetchval(
        """
        WITH recovered AS (
            UPDATE public.healing_attempts
            SET
                status       = 'timeout',
                updated_at   = now(),
                closed_at    = now(),
                error_detail = 'Recovered on daemon restart — workflow deadline exceeded'
            WHERE status = 'investigating'
              AND healing_session_id IS NOT NULL
              AND workflow_deadline_at IS NOT NULL
              AND now() > workflow_deadline_at
            RETURNING id
        )
        SELECT COUNT(*) FROM recovered
        """
    )

    # Rule 2: legacy rows (no deadline) with stale updated_at → timeout (fallback)
    legacy_timeout_count: int = await pool.fetchval(
        """
        WITH recovered AS (
            UPDATE public.healing_attempts
            SET
                status       = 'timeout',
                updated_at   = now(),
                closed_at    = now(),
                error_detail = 'Recovered on daemon restart — interrupted (no deadline set)'
            WHERE status = 'investigating'
              AND healing_session_id IS NOT NULL
              AND workflow_deadline_at IS NULL
              AND updated_at < now() - ($1 * INTERVAL '1 minute')
            RETURNING id
        )
        SELECT COUNT(*) FROM recovered
        """,
        timeout_minutes,
    )

    # Rule 3: rows with no session_id and old enough → failed (agent never spawned)
    failed_count: int = await pool.fetchval(
        """
        WITH recovered AS (
            UPDATE public.healing_attempts
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
        """
    )

    total = int(deadline_timeout_count) + int(legacy_timeout_count) + int(failed_count)
    if total > 0:
        logger.info(
            "recover_stale_attempts: recovered %d rows "
            "(%d deadline-timeout, %d legacy-timeout, %d never-spawned-failed)",
            total,
            int(deadline_timeout_count),
            int(legacy_timeout_count),
            int(failed_count),
        )
    return total


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
        "SELECT * FROM public.healing_attempts WHERE id = $1",
        attempt_id,
    )
    return _decode_row(row) if row is not None else None


# ---------------------------------------------------------------------------
# Dispatch event recording
# ---------------------------------------------------------------------------


async def create_dispatch_event(
    pool: asyncpg.Pool,
    fingerprint: str,
    butler_name: str,
    decision: str,
    *,
    reason: str | None = None,
    attempt_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Record a dispatch decision in ``public.healing_dispatch_events``.

    Every gate evaluation that does NOT launch a healing session (cooldown,
    concurrency cap, circuit breaker, no-model, novelty join) should be
    recorded here.  Gate rejections before launch MUST NOT create or mark
    any ``healing_attempts`` row as ``failed``.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    fingerprint:
        64-character SHA-256 hex string identifying the error.
    butler_name:
        Name of the butler from whose context dispatch was attempted.
    decision:
        The dispatch outcome label, e.g. ``"cooldown"``, ``"concurrency_cap"``,
        ``"circuit_breaker"``, ``"novelty_join"``, ``"no_model"``, ``"accepted"``,
        ``"severity"``, ``"disabled"``.
    reason:
        Optional free-form detail explaining the decision.
    attempt_id:
        UUID of the related ``healing_attempts`` row, if any.  For example,
        a ``novelty_join`` decision may link to the existing active attempt that
        was joined.

    Returns
    -------
    uuid.UUID
        The newly created dispatch event ID.
    """
    event_id = await pool.fetchval(
        """
        INSERT INTO public.healing_dispatch_events
            (fingerprint, butler_name, decision, reason, attempt_id)
        VALUES ($1, $2, $3, $4, $5::uuid)
        RETURNING id
        """,
        fingerprint,
        butler_name,
        decision,
        reason,
        str(attempt_id) if attempt_id is not None else None,
    )
    return uuid.UUID(str(event_id))


async def list_dispatch_events(
    pool: asyncpg.Pool,
    limit: int = 20,
    offset: int = 0,
    decision_filter: str | None = None,
) -> list[HealingDispatchEventRow]:
    """Return paginated dispatch-decision rows for dashboard display.

    Dispatch events are distinct from healing attempts (execution records).
    They represent gate evaluations — whether or not a healing session was
    launched.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    limit:
        Maximum number of rows to return.
    offset:
        Number of rows to skip (for pagination).
    decision_filter:
        If provided, only return rows with this ``decision`` value.

    Returns
    -------
    list[HealingDispatchEventRow]
        Rows ordered by ``created_at DESC``.
    """
    if decision_filter is not None:
        rows = await pool.fetch(
            """
            SELECT *
            FROM public.healing_dispatch_events
            WHERE decision = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            decision_filter,
            limit,
            offset,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT *
            FROM public.healing_dispatch_events
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
    return [_decode_row(row) for row in rows]


# ---------------------------------------------------------------------------
# Phase session tracking
# ---------------------------------------------------------------------------


async def record_phase_session(
    pool: asyncpg.Pool,
    attempt_id: uuid.UUID,
    phase: str,
    session_id: uuid.UUID,
) -> uuid.UUID:
    """Record a newly launched runtime session for a healing workflow phase.

    Inserts a row into ``public.healing_attempt_sessions`` and updates the
    parent ``healing_attempts`` row with:
    - ``current_phase`` set to *phase*
    - ``healing_session_id`` set to *session_id* (backward-compat field)

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    attempt_id:
        UUID of the parent ``healing_attempts`` row.
    phase:
        Workflow phase label: ``"diagnose"``, ``"implement"``, ``"verify"``,
        or any custom label.
    session_id:
        UUID of the spawned runtime session.

    Returns
    -------
    uuid.UUID
        The newly created ``healing_attempt_sessions`` row ID.
    """
    # Insert child row and update parent atomically so the two writes are
    # never observed in an inconsistent state (child recorded but parent
    # current_phase / healing_session_id still stale).
    async with pool.acquire() as conn:
        async with conn.transaction():
            child_id = await conn.fetchval(
                """
                INSERT INTO public.healing_attempt_sessions
                    (attempt_id, phase, session_id, status)
                VALUES ($1::uuid, $2, $3::uuid, 'running')
                RETURNING id
                """,
                str(attempt_id),
                phase,
                str(session_id),
            )

            # Update parent: current_phase + healing_session_id (compat)
            await conn.execute(
                """
                UPDATE public.healing_attempts
                SET
                    current_phase      = $2,
                    healing_session_id = $3::uuid,
                    updated_at         = now()
                WHERE id = $1::uuid
                """,
                str(attempt_id),
                phase,
                str(session_id),
            )

    return uuid.UUID(str(child_id))


async def update_phase_session_status(
    pool: asyncpg.Pool,
    phase_session_id: uuid.UUID,
    new_status: str,
    *,
    error_detail: str | None = None,
) -> bool:
    """Update the status of a ``healing_attempt_sessions`` row.

    Sets ``updated_at`` to now().  When *new_status* is a terminal value
    (``completed``, ``failed``, ``timeout``, ``cancelled``), also sets
    ``completed_at``.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    phase_session_id:
        UUID primary key of the ``healing_attempt_sessions`` row.
    new_status:
        Target status.  Must be one of ``PHASE_SESSION_STATUSES``.
    error_detail:
        Optional error description (for failure/timeout transitions).

    Returns
    -------
    bool
        True if the row was updated, False if the status was invalid or the
        row was not found.
    """
    if new_status not in PHASE_SESSION_STATUSES:
        logger.warning(
            "update_phase_session_status: invalid status %r for session %s",
            new_status,
            phase_session_id,
        )
        return False

    terminal = new_status in {"completed", "failed", "timeout", "cancelled"}
    updated = await pool.fetchval(
        """
        UPDATE public.healing_attempt_sessions
        SET
            status       = $2,
            updated_at   = now(),
            completed_at = CASE WHEN $3 THEN now() ELSE completed_at END,
            error_detail = COALESCE($4, error_detail)
        WHERE id = $1::uuid
        RETURNING id
        """,
        str(phase_session_id),
        new_status,
        terminal,
        error_detail,
    )
    if updated is None:
        logger.warning("update_phase_session_status: session %s not found", phase_session_id)
        return False
    return True


async def list_phase_sessions(
    pool: asyncpg.Pool,
    attempt_id: uuid.UUID,
) -> list[HealingAttemptSessionRow]:
    """Return all phase session rows for a given healing attempt.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the shared schema.
    attempt_id:
        UUID of the parent ``healing_attempts`` row.

    Returns
    -------
    list[HealingAttemptSessionRow]
        Rows ordered by ``started_at ASC``.
    """
    rows = await pool.fetch(
        """
        SELECT *
        FROM public.healing_attempt_sessions
        WHERE attempt_id = $1::uuid
        ORDER BY started_at ASC
        """,
        str(attempt_id),
    )
    return [_decode_row(row) for row in rows]
