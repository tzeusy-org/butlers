"""Structured DB log helper for butler daemons.

Design decision — fire-and-forget vs. buffered writes
------------------------------------------------------
This module uses **fire-and-forget** ``asyncio.create_task()`` for each write.

Rationale:
- Session execution must never block on log I/O.  A buffer would introduce
  latency at flush time (each flush awaits the pool) and complexity in
  lifecycle management (flush-on-shutdown, timer tasks, error handling).
- The cost of one extra asyncpg acquire per log line is acceptable for the
  INFO+ call rate expected from butler sessions.
- If the DB pool is unavailable the write silently drops (logged at WARNING
  level to stderr via the stdlib logger).  This matches the principle that
  structured logs are observability data, not a transaction log.

Buffer flush cadence: N/A — no buffer.  Each ``log_nowait()`` call schedules
one ``create_task`` immediately; ``log()`` awaits ``_write()`` directly.

Retention policy: no automatic vacuum or partition.  Retention is handled
out-of-band by the operator (cron delete, pg_partman, or table truncation).
See migration ``core_089`` for the table definition.

Usage
-----
>>> from butlers.core.butler_logging import ButlerLogger
>>> bl = ButlerLogger(pool=asyncpg_pool, schema="general")
>>> await bl.log("INFO", "Session started", source="spawner")
>>> bl.log_nowait("INFO", "Quick note")   # fire-and-forget, no await needed

To bridge stdlib ``logging`` into ``butler_logs``, attach
:class:`ButlerDBLogHandler` to the root logger once the butler's DB pool is
ready (see ``butlers.lifecycle.run_startup``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Ordered list of severity levels for >= comparisons.
_LEVEL_ORDER = ("DEBUG", "INFO", "WARN", "ERROR")
_VALID_LEVELS = frozenset(_LEVEL_ORDER)


def _level_rank(level: str) -> int:
    """Return numeric rank for a level string (higher = more severe)."""
    try:
        return _LEVEL_ORDER.index(level.upper())
    except ValueError:
        return -1


class ButlerLogger:
    """Writes structured log lines to ``{schema}.butler_logs`` via asyncpg.

    Parameters
    ----------
    pool:
        An asyncpg connection pool scoped to the butler's schema (its
        ``search_path`` is already set to ``{schema}``, so the table name
        ``butler_logs`` resolves correctly without a qualified prefix).
    schema:
        The butler schema name.  Used only for diagnostic log messages.
    min_level:
        Minimum level to persist.  Lines below this rank are silently dropped.
        Default ``"INFO"`` — DEBUG lines are not written unless explicitly
        lowered.
    """

    def __init__(
        self,
        pool: Any,  # asyncpg.Pool — typed loosely to avoid hard import at module level
        schema: str,
        min_level: str = "INFO",
    ) -> None:
        norm_min = min_level.upper()
        if norm_min not in _VALID_LEVELS:
            raise ValueError(
                f"Invalid min_level {min_level!r}. Must be one of: {', '.join(_LEVEL_ORDER)}"
            )
        self._pool = pool
        self._schema = schema
        self._min_rank = _level_rank(norm_min)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_nowait(
        self,
        level: str,
        msg: str,
        *,
        source: str | None = None,
        request_id: str | UUID | None = None,
        metadata: dict[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> None:
        """Schedule a log write without awaiting it (fire-and-forget).

        Safe to call from synchronous or async contexts.  If there is no
        running event loop the write is silently skipped.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "butler_logging: no running event loop; dropping %s log line for schema %s",
                level,
                self._schema,
            )
            return
        loop.create_task(
            self._write(level, msg, source=source, request_id=request_id, metadata=metadata, ts=ts)
        )

    async def log(
        self,
        level: str,
        msg: str,
        *,
        source: str | None = None,
        request_id: str | UUID | None = None,
        metadata: dict[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> None:
        """Await a single structured log write.

        Prefer ``log_nowait()`` when you do not need confirmation that the
        write completed.
        """
        await self._write(
            level, msg, source=source, request_id=request_id, metadata=metadata, ts=ts
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _write(
        self,
        level: str,
        msg: str,
        *,
        source: str | None,
        request_id: str | UUID | None,
        metadata: dict[str, Any] | None,
        ts: datetime | None,
    ) -> None:
        """Execute the INSERT.  Errors are swallowed to protect session flow."""
        norm_level = level.upper()
        if norm_level not in _VALID_LEVELS:
            logger.debug(
                "butler_logging: unknown level %r for schema %s; dropping line", level, self._schema
            )
            return

        if _level_rank(norm_level) < self._min_rank:
            return

        # Normalise request_id to str or None
        request_id_str: str | None = str(request_id) if request_id is not None else None

        # Serialise metadata to JSON string for asyncpg JSONB binding
        metadata_json: str | None = None
        if metadata is not None:
            try:
                metadata_json = json.dumps(metadata)
            except (TypeError, ValueError):
                logger.debug(
                    "butler_logging: could not serialise metadata for schema %s", self._schema
                )

        try:
            async with self._pool.acquire() as conn:
                if ts is not None:
                    await conn.execute(
                        """
                        INSERT INTO butler_logs (ts, level, msg, source, request_id, metadata)
                        VALUES ($1, $2, $3, $4, $5::uuid, $6::jsonb)
                        """,
                        ts,
                        norm_level,
                        msg,
                        source,
                        request_id_str,
                        metadata_json,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO butler_logs (level, msg, source, request_id, metadata)
                        VALUES ($1, $2, $3, $4::uuid, $5::jsonb)
                        """,
                        norm_level,
                        msg,
                        source,
                        request_id_str,
                        metadata_json,
                    )
        except Exception:
            logger.warning(
                "butler_logging: failed to write log line to schema %s",
                self._schema,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Stdlib logging bridge
# ---------------------------------------------------------------------------


def _map_pylog_level(levelno: int) -> str | None:
    """Map a stdlib log level number to one of DEBUG/INFO/WARN/ERROR.

    CRITICAL and above collapse to ERROR; NOTSET and unknowns return ``None``
    so the record is dropped.
    """
    if levelno >= logging.ERROR:
        return "ERROR"
    if levelno >= logging.WARNING:
        return "WARN"
    if levelno >= logging.INFO:
        return "INFO"
    if levelno >= logging.DEBUG:
        return "DEBUG"
    return None


class ButlerDBLogHandler(logging.Handler):
    """Logging handler that persists records to a butler's ``butler_logs`` table.

    Attach one instance per butler to the root logger after the butler's
    asyncpg pool is ready, and detach it before the pool is closed.

    Why a context filter
    --------------------
    ``butlers up`` runs every butler in a single Python process, all sharing
    the root logger.  Without a filter, every butler's handler would receive
    every other butler's log lines and we would write each line into every
    schema's ``butler_logs``.  We use the ``_butler_context`` ContextVar
    (set by :func:`butlers.core.logging.configure_logging`) to route records
    to the correct DB.

    Records emitted from tasks created *outside* any butler context (e.g.
    the parent ``_start_all`` task once startup finishes) are dropped, which
    is the same behaviour as the file-handler routing in core.logging.
    """

    def __init__(
        self,
        butler_logger: ButlerLogger,
        butler_name: str,
        level: int = logging.INFO,
    ) -> None:
        super().__init__(level=level)
        self._butler_logger = butler_logger
        self._butler_name = butler_name

    def emit(self, record: logging.LogRecord) -> None:
        # Lazy import to avoid a circular reference (core.logging imports
        # nothing from butler_logging today, but the dependency direction
        # belongs that way around).
        from butlers.core.logging import get_butler_context

        if get_butler_context() != self._butler_name:
            return

        # Suppress our own warnings to avoid feedback loops if the pool fails
        # and ButlerLogger emits a WARNING on the same logger we are draining.
        if record.name == __name__:
            return

        level = _map_pylog_level(record.levelno)
        if level is None:
            return

        try:
            msg = self.format(record)
        except Exception:
            # Never raise from a log handler.
            return

        try:
            self._butler_logger.log_nowait(level, msg, source=record.name)
        except Exception:
            # log_nowait already swallows; defensive guard for unexpected paths.
            pass
