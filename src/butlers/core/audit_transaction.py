"""Explicit transaction ownership for after-commit audit metrics.

Only action labels and a recorder are buffered. No audit row or caller content
is retained here. An acknowledged commit is a recording boundary, not durable
exactly-once telemetry; an interrupted commit can have an unknown SQL outcome.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import asyncpg


class AuditTransactionError(RuntimeError):
    """The caller does not own the audit insert's transaction boundary."""


class AuditMetricRecordingError(RuntimeError):
    """SQL committed, but its synchronous audit metric recorder failed."""


@dataclass(eq=False)
class _Frame:
    connection: Any
    task: asyncio.Task[Any] | None
    parent: _Frame | None
    pending: list[tuple[str, Callable[[str], None]]] = field(default_factory=list)
    open: bool = True


_frames: ContextVar[tuple[_Frame, ...]] = ContextVar("audit_transactions", default=())


def _owner(connection: Any) -> _Frame | None:
    task = asyncio.current_task()
    return next(
        (
            frame
            for frame in reversed(_frames.get())
            if frame.connection is connection and frame.task is task and frame.open
        ),
        None,
    )


def _in_transaction(connection: Any) -> bool:
    probe = getattr(connection, "is_in_transaction", None)
    if not callable(probe) or inspect.iscoroutinefunction(probe):
        raise AuditTransactionError("Audit transaction state is unavailable")
    state = probe()
    if type(state) is not bool:
        raise AuditTransactionError("Audit transaction state is unavailable")
    return state


def pending_append(connection: Any) -> _Frame | None:
    """Validate the owner BEFORE INSERT; None denotes a committed implicit write.

    Pools execute each write in their own implicit transaction. Connections
    must expose asyncpg's synchronous public transaction-state protocol.
    """
    if isinstance(connection, asyncpg.Pool):
        return None
    owner = _owner(connection)
    state = getattr(connection, "is_in_transaction", None)
    if owner is None and state is None and callable(getattr(connection, "acquire", None)):
        return None
    if not _in_transaction(connection):
        if owner is not None:
            raise AuditTransactionError("Audit transaction boundary changed")
        return None
    if owner is None:
        raise AuditTransactionError("Audit append requires an owned transaction")
    return owner


def record_append(frame: _Frame | None, action: str, recorder: Callable[[str], None]) -> None:
    """Enroll a successful insert, or record its acknowledged implicit commit."""
    if frame is None:
        try:
            recorder(action)
        except Exception:
            raise AuditMetricRecordingError("Committed audit metric recording failed") from None
    elif frame.open and frame.task is asyncio.current_task():
        frame.pending.append((action, recorder))
    else:
        raise AuditTransactionError("Audit transaction owner is no longer active")


@asynccontextmanager
async def audit_transaction(
    connection: Any,
    *,
    isolation: str | None = None,
    readonly: bool = False,
    deferrable: bool = False,
) -> AsyncIterator[None]:
    """Own a real transaction/savepoint and record only after outer commit.

    Existing unmanaged transactions are refused before starting a savepoint.
    Never manually commit/rollback the connection or introduce unmanaged
    savepoints around canonical appends inside this scope.
    """
    parent = _owner(connection)
    if _in_transaction(connection):
        if parent is None:
            raise AuditTransactionError("Audit transaction requires its outer owner")
    elif parent is not None:
        raise AuditTransactionError("Audit transaction boundary changed")
    options = {}
    if isolation is not None:
        options["isolation"] = isolation
    if readonly:
        options["readonly"] = readonly
    if deferrable:
        options["deferrable"] = deferrable
    frame = _Frame(connection, asyncio.current_task(), parent)
    token = _frames.set((*_frames.get(), frame))
    try:
        # asyncpg's public context manager owns start/commit/rollback, including
        # exception and cancellation unwinding. A successful exit acknowledges
        # commit (or savepoint release); never intercept/retry its commit.
        async with connection.transaction(**options):
            yield
            if frame.pending:
                if not _in_transaction(connection):
                    raise AuditTransactionError("Audit transaction boundary changed")
                # COMMIT of an aborted PostgreSQL transaction can report success
                # while actually rolling back. A swallowed SQL error must refuse.
                await connection.execute("SELECT 1")
        frame.open = False
        if parent is not None:
            if not parent.open or parent.task is not asyncio.current_task():
                raise AuditTransactionError("Audit outer transaction is no longer active")
            parent.pending.extend(frame.pending)
        else:
            for action, recorder in frame.pending:
                record_append(None, action, recorder)
    finally:
        frame.open = False
        frame.pending.clear()
        _frames.reset(token)
