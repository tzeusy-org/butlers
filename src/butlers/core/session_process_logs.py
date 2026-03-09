"""Session process logs — ephemeral process-level diagnostics for runtime sessions.

Stores raw subprocess output (stderr, exit code, PID, command) from runtime
adapter invocations in a separate ``session_process_logs`` table.  Rows carry
a TTL (default 14 days) and are reaped by ``cleanup()``.

This module intentionally lives outside ``sessions.py`` because the core
session log is append-only by convention (no DELETE/DROP/TRUNCATE), whereas
process logs require periodic TTL cleanup.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

_DEFAULT_TTL_DAYS = 14


async def write(
    pool: asyncpg.Pool,
    session_id: uuid.UUID,
    *,
    pid: int | None = None,
    exit_code: int | None = None,
    command: str | None = None,
    stderr: str | None = None,
    runtime_type: str | None = None,
    ttl_days: int = _DEFAULT_TTL_DAYS,
) -> None:
    """Write process-level diagnostics for a session (one row per session).

    Stored in a separate ``session_process_logs`` table with a TTL to avoid
    storage bloat.  Rows expire after *ttl_days* (default 14) and are reaped
    by ``cleanup()``.

    Stderr is capped at 32 KiB to bound storage per row.
    """
    max_stderr = 32 * 1024
    if stderr and len(stderr) > max_stderr:
        stderr = stderr[:max_stderr] + "\n... [trimmed]"

    await pool.execute(
        """
        INSERT INTO session_process_logs
            (session_id, pid, exit_code, command, stderr, runtime_type, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6, now() + make_interval(days => $7))
        ON CONFLICT (session_id) DO UPDATE SET
            pid          = EXCLUDED.pid,
            exit_code    = EXCLUDED.exit_code,
            command      = EXCLUDED.command,
            stderr       = EXCLUDED.stderr,
            runtime_type = EXCLUDED.runtime_type,
            expires_at   = EXCLUDED.expires_at
        """,
        session_id,
        pid,
        exit_code,
        command,
        stderr,
        runtime_type,
        ttl_days,
    )
    logger.debug("Process log written for session %s (pid=%s, exit=%s)", session_id, pid, exit_code)


async def get(
    pool: asyncpg.Pool,
    session_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Return the process log for a session, or None if not found / expired."""
    row = await pool.fetchrow(
        """
        SELECT pid, exit_code, command, stderr, runtime_type, created_at, expires_at
        FROM session_process_logs
        WHERE session_id = $1 AND expires_at >= now()
        """,
        session_id,
    )
    if row is None:
        return None
    return dict(row)


async def cleanup(pool: asyncpg.Pool) -> int:
    """Delete expired process log rows.  Returns count of deleted rows."""
    result = await pool.execute("DELETE FROM session_process_logs WHERE expires_at < now()")
    # asyncpg returns "DELETE N"
    deleted = int(result.split()[-1])
    if deleted:
        logger.info("Cleaned up %d expired session process log(s)", deleted)
    return deleted
