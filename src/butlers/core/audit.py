"""Daemon-side audit logging helper.

Appends audit entries to the canonical ``public.audit_log`` table using a
raw ``asyncpg.Pool``.  This is the daemon-level counterpart of
:func:`butlers.api.routers.audit.log_audit_entry` (which requires a
:class:`DatabaseManager`).

Fire-and-forget: exceptions are logged and swallowed so that audit
logging never blocks or breaks the primary operation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

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
    """Append an audit log entry to the canonical ``public.audit_log`` table.

    Daemon-side compatibility shim that maps the legacy ``dashboard_audit_log``
    field shape onto :func:`butlers.api.routers.audit.append` (bu-h47nm):

    - ``butler``        -> ``actor``
    - ``operation``     -> ``action``
    - ``request_summary.path`` -> ``target`` (when present)
    - ``request_summary`` -> ``metadata`` JSONB
    - ``result`` / ``error`` -> the ``result`` / ``error`` columns

    Writes ONLY to ``public.audit_log``; the read surface UNIONs the legacy
    table so readers still see every row.

    Parameters
    ----------
    pool:
        asyncpg connection pool.  The insert is fully schema-qualified
        (``public.audit_log``) so any reachable pool works.  If ``None``, the
        call is a silent no-op.
    butler:
        Butler name that produced the activity.
    operation:
        Activity type (e.g. ``"session"``).
    request_summary:
        Arbitrary dict stored on the ``metadata`` JSONB column.  Non-JSON-safe
        values are coerced to strings.
    result:
        ``"success"`` or ``"error"``.
    error:
        Error message (only meaningful when *result* is ``"error"``).
    """
    if pool is None:
        return

    # Imported lazily: the daemon must not pull in the API router layer at
    # import time (mirrors spawner.py's lazy import of emit_spend_event).
    from butlers.api.routers.audit import AuditTableNotAvailableError, append

    # Coerce non-JSON-safe values (e.g. UUID, datetime) to strings before they
    # land on the ``metadata`` JSONB column.
    safe_summary = json.loads(json.dumps(request_summary, default=str))

    target = safe_summary.get("path")
    target_str = str(target) if target else None

    metadata: dict[str, Any] = {"request_summary": safe_summary}

    try:
        await append(
            pool,
            butler,
            operation,
            target=target_str,
            metadata=metadata,
            result=result,
            error=error,
        )
    except AuditTableNotAvailableError:
        logger.warning(
            "Audit table unavailable, dropping daemon audit entry: butler=%s operation=%s",
            butler,
            operation,
        )
    except Exception:
        logger.warning(
            "Failed to write daemon audit entry: butler=%s operation=%s",
            butler,
            operation,
            exc_info=True,
        )
