"""Daemon-side audit logging helper.

Writes audit entries to the Switchboard's ``dashboard_audit_log`` table
using a raw ``asyncpg.Pool``.  This is the daemon-level counterpart of
:func:`butlers.api.routers.audit.log_audit_entry` (which requires a
:class:`DatabaseManager`).

Fire-and-forget: exceptions are logged and swallowed so that audit
logging never blocks or breaks the primary operation.
"""

from __future__ import annotations

import json
import logging

import asyncpg

logger = logging.getLogger(__name__)


async def write_audit_entry(
    pool: asyncpg.Pool | None,
    butler: str,
    operation: str,
    request_summary: dict,
    result: str = "success",
    error: str | None = None,
) -> None:
    """Insert an audit log entry into the switchboard database.

    Parameters
    ----------
    pool:
        asyncpg connection pool pointed at ``butler_switchboard``.
        If ``None``, the call is a silent no-op.
    butler:
        Butler name that produced the activity.
    operation:
        Activity type (e.g. ``"session"``).
    request_summary:
        Arbitrary dict serialized as JSON into the ``request_summary`` column.
    result:
        ``"success"`` or ``"error"``.
    error:
        Error message (only meaningful when *result* is ``"error"``).
    """
    if pool is None:
        return

    try:
        await pool.execute(
            "INSERT INTO dashboard_audit_log "
            "(butler, operation, request_summary, result, error, user_context) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            butler,
            operation,
            json.dumps(request_summary),
            result,
            error,
            json.dumps({}),
        )
    except Exception:
        logger.warning(
            "Failed to write daemon audit entry: butler=%s operation=%s",
            butler,
            operation,
            exc_info=True,
        )
