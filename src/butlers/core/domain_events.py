"""The domain-event bus — standing cross-butler publish/subscribe log.

bu-ep4ks.10 (2026-07-25 JARVIS pursuit dossier, ranked move #10). See
``alembic/versions/core/core_186_domain_events.py`` for the tables and
``src/butlers/core/domain_event_wake.py`` / ``src/butlers/core_tools/
_domain_events.py`` for the subscriber-side task reconciliation and MCP
tool surface.

Design
------
Cross-butler interaction so far is one-shot pull (``delegate_ask``/answer,
``butlers.core.delegation_ledger``) or a frozen, fixed-vocabulary read
(``public.user_context``, ``butlers.context_bus``). This module is the
durable append log a standing subscription reacts to instead: any butler
publishes an event under an open, namespaced ``event_type`` (``"<butler>.
<event>"``, e.g. ``"travel.trip_booked"``) -- deliberately not a closed enum
like ``ContextSignal``, since a fixed vocabulary with hardcoded writers is
exactly the limitation this move exists to fix.

Fan-out idempotence
--------------------
``public.domain_event_deliveries`` has ``UNIQUE (event_id, subscriber_
butler)``. :func:`claim_delivery` is the atomic claim: the first caller for
a given (event, subscriber) pair gets the fresh ``pending`` row back and
must dispatch; every subsequent caller (retry, crash-replay, a periodic
reconciliation sweep) gets the *same* row back and must inspect its
``status`` rather than re-insert -- a ``delivered`` row is never re-
dispatched, but a ``pending``/``failed`` row is a legitimate retry target
for the caller (mirrors ``delegation_wake``'s deterministic-name
reconciliation, just keyed by a unique index instead of a task name).

The periodic reconciliation sweep this design anticipated (bu-1yw6d) is
``run_domain_event_reconciliation_sweep`` in ``src/butlers/core_tools/
_domain_events.py``, dispatched by the Switchboard's ``domain_event_
reconciliation_sweep`` scheduled job: it re-drives ``pending`` rows stuck
since a crash (:func:`select_stale_pending_deliveries`) and retries
``failed`` rows a bounded number of times with backoff
(:func:`select_retryable_failed_deliveries`). The sweep serializes overlapping
invocations with a session advisory lock, then re-observes each selected row
before dispatch so a live path that settled it in the interim is skipped.
:func:`mark_delivery_failed` transitions a delivery to the terminal
``failed_permanent`` status -- distinct from the retryable ``failed`` --
once a route error is classified permanent (e.g. the subscriber lacks the
``domain_events`` core group) or the retry bound is reached, so a delivery
that can never succeed is surfaced honestly instead of retried forever or
silently dropped.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta
from typing import Any

import asyncpg

VALID_DELIVERY_STATUSES = frozenset(
    {"pending", "delivered", "conflict", "failed", "failed_permanent"}
)

# "<namespace>.<event>" -- namespace is conventionally the publishing
# butler's name. Deliberately permissive (no fixed enum): any butler can
# mint a new event_type without a schema change, unlike context_bus's
# ContextSignal vocabulary.
_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def is_valid_event_type(event_type: str) -> bool:
    """Return whether *event_type* matches the ``"<namespace>.<event>"`` shape."""
    return bool(event_type) and bool(_EVENT_TYPE_RE.match(event_type))


def _dumps_payload(payload: dict[str, Any] | None) -> str:
    return json.dumps(payload if payload is not None else {})


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# Event log: write + read
# ---------------------------------------------------------------------------


async def record_event(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    event_type: str,
    source_butler: str,
    payload: dict[str, Any] | None = None,
) -> str:
    """Insert one ``public.domain_events`` row and return its id.

    Not best-effort: the event row *is* the publish record, so a write
    failure here must propagate to the caller rather than being silently
    absorbed (mirrors ``delegation_ledger.record_ask``).
    """
    event_id = await pool.fetchval(
        """
        INSERT INTO public.domain_events (event_type, source_butler, payload)
        VALUES ($1, $2, $3::jsonb)
        RETURNING id
        """,
        event_type,
        source_butler,
        _dumps_payload(payload),
    )
    return str(event_id)


async def get_event(pool: asyncpg.Pool, event_id: uuid.UUID | str) -> dict[str, Any] | None:
    """Return a single domain-event row by id, or ``None`` if it does not exist."""
    row = await pool.fetchrow(
        """
        SELECT id, event_type, source_butler, payload, occurred_at, created_at
        FROM public.domain_events
        WHERE id = $1
        """,
        uuid.UUID(str(event_id)),
    )
    return _row_to_dict(row) if row is not None else None


async def list_events(
    pool: asyncpg.Pool,
    *,
    event_type: str | None = None,
    source_butler: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    """List domain events, most-recent first, with optional filters.

    Returns ``(total, rows)`` where ``total`` is the unfiltered-by-page count
    matching the given filters (for pagination).
    """
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if event_type is not None:
        conditions.append(f"event_type = ${idx}")
        args.append(event_type)
        idx += 1
    if source_butler is not None:
        conditions.append(f"source_butler = ${idx}")
        args.append(source_butler)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM public.domain_events{where}", *args)
    rows = await pool.fetch(
        f"""
        SELECT id, event_type, source_butler, payload, occurred_at, created_at
        FROM public.domain_events{where}
        ORDER BY occurred_at DESC
        OFFSET ${idx} LIMIT ${idx + 1}
        """,
        *args,
        offset,
        limit,
    )
    return int(total or 0), [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


async def upsert_subscription(
    pool: asyncpg.Pool,
    *,
    subscriber_butler: str,
    event_type: str,
) -> dict[str, Any]:
    """Create or reactivate a standing subscription; idempotent."""
    row = await pool.fetchrow(
        """
        INSERT INTO public.butler_subscriptions (subscriber_butler, event_type, active)
        VALUES ($1, $2, true)
        ON CONFLICT (subscriber_butler, event_type)
        DO UPDATE SET active = true, updated_at = now()
        RETURNING id, subscriber_butler, event_type, active, created_at, updated_at
        """,
        subscriber_butler,
        event_type,
    )
    return _row_to_dict(row)


async def remove_subscription(
    pool: asyncpg.Pool,
    *,
    subscriber_butler: str,
    event_type: str,
) -> bool:
    """Deactivate a standing subscription; idempotent (no-op if absent/already inactive).

    Returns ``True`` if a subscription row exists (active or not) after the
    call, ``False`` if there was never one to begin with.
    """
    result = await pool.execute(
        """
        UPDATE public.butler_subscriptions
        SET active = false, updated_at = now()
        WHERE subscriber_butler = $1 AND event_type = $2
        """,
        subscriber_butler,
        event_type,
    )
    return result != "UPDATE 0"


async def list_subscriptions(
    pool: asyncpg.Pool,
    *,
    subscriber_butler: str | None = None,
    event_type: str | None = None,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    """List subscription rows, most-recently-updated first."""
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if subscriber_butler is not None:
        conditions.append(f"subscriber_butler = ${idx}")
        args.append(subscriber_butler)
        idx += 1
    if event_type is not None:
        conditions.append(f"event_type = ${idx}")
        args.append(event_type)
        idx += 1
    if active_only:
        conditions.append("active")

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = await pool.fetch(
        f"""
        SELECT id, subscriber_butler, event_type, active, created_at, updated_at
        FROM public.butler_subscriptions{where}
        ORDER BY updated_at DESC
        """,
        *args,
    )
    return [_row_to_dict(r) for r in rows]


async def get_active_subscribers(pool: asyncpg.Pool, event_type: str) -> list[str]:
    """Return the distinct list of butlers actively subscribed to *event_type*."""
    rows = await pool.fetch(
        """
        SELECT subscriber_butler
        FROM public.butler_subscriptions
        WHERE event_type = $1 AND active
        ORDER BY subscriber_butler
        """,
        event_type,
    )
    return [r["subscriber_butler"] for r in rows]


# ---------------------------------------------------------------------------
# Delivery ledger: atomic per-subscriber fan-out claim/outcome
# ---------------------------------------------------------------------------


async def claim_delivery(
    pool: asyncpg.Pool,
    *,
    event_id: uuid.UUID | str,
    subscriber_butler: str,
) -> dict[str, Any]:
    """Atomically claim (or re-observe) the delivery row for one (event, subscriber) pair.

    ``UNIQUE (event_id, subscriber_butler)`` makes this the fan-out
    idempotence boundary: the first call for a pair inserts a fresh
    ``pending`` row; every subsequent call (retry, crash-replay) instead
    reads back whatever row already exists. Callers must branch on the
    returned row's ``status`` -- only ``delivered`` means "do not dispatch
    again."
    """
    inserted = await pool.fetchrow(
        """
        INSERT INTO public.domain_event_deliveries (event_id, subscriber_butler, status)
        VALUES ($1, $2, 'pending')
        ON CONFLICT (event_id, subscriber_butler) DO NOTHING
        RETURNING id, event_id, subscriber_butler, status, task_id, task_name,
                  error_message, delivered_at, created_at, updated_at
        """,
        uuid.UUID(str(event_id)),
        subscriber_butler,
    )
    if inserted is not None:
        return _row_to_dict(inserted)

    existing = await pool.fetchrow(
        """
        SELECT id, event_id, subscriber_butler, status, task_id, task_name,
               error_message, delivered_at, created_at, updated_at
        FROM public.domain_event_deliveries
        WHERE event_id = $1 AND subscriber_butler = $2
        """,
        uuid.UUID(str(event_id)),
        subscriber_butler,
    )
    # Unreachable in practice (the ON CONFLICT proves a row exists), but fail
    # closed rather than raise a confusing AttributeError on None.
    if existing is None:
        raise RuntimeError(
            f"claim_delivery: conflict reported for event_id={event_id!r} "
            f"subscriber_butler={subscriber_butler!r} but no row could be re-read."
        )
    return _row_to_dict(existing)


async def mark_delivery_delivered(
    pool: asyncpg.Pool,
    delivery_id: uuid.UUID | str,
    *,
    task_id: uuid.UUID | str,
    task_name: str,
) -> None:
    """Record a successful fan-out dispatch (subscriber reconciled its own task)."""
    await pool.execute(
        """
        UPDATE public.domain_event_deliveries
        SET status = 'delivered', task_id = $2, task_name = $3,
            delivered_at = now(), updated_at = now()
        WHERE id = $1
        """,
        uuid.UUID(str(delivery_id)),
        uuid.UUID(str(task_id)),
        task_name,
    )


async def mark_delivery_conflict(pool: asyncpg.Pool, delivery_id: uuid.UUID | str) -> None:
    """Record that the subscriber found a conflicting deterministic-named task.

    Never downgrades an already-``delivered`` row (mirrors
    ``delegation_ledger.record_wake_task_conflict``).
    """
    await pool.execute(
        """
        UPDATE public.domain_event_deliveries
        SET status = 'conflict', updated_at = now()
        WHERE id = $1 AND status != 'delivered'
        """,
        uuid.UUID(str(delivery_id)),
    )


async def mark_delivery_failed(
    pool: asyncpg.Pool,
    delivery_id: uuid.UUID | str,
    error_message: str,
    *,
    retryable: bool = True,
    max_attempts: int | None = None,
) -> str | None:
    """Record a route()-level dispatch failure and bump ``attempt_count``.

    Never downgrades an already-``delivered`` or already-``failed_permanent``
    row -- both are terminal. Transitions to the terminal ``failed_permanent``
    status instead of the retryable ``failed`` when either:

    - ``retryable`` is ``False`` -- the caller (see
      ``_domain_events._is_retryable_route_error_text``) classified this as a
      permanent route error (e.g. the subscriber lacks the ``domain_events``
      core group, surfaced as an "unknown tool" route failure) rather than a
      transient one (connection/timeout); retrying a permanent error can
      never succeed, so there is no reason to wait for the attempt bound.
    - ``max_attempts`` is given and ``attempt_count`` would reach it -- the
      periodic reconciliation sweep's bounded-retry cap
      (``run_domain_event_reconciliation_sweep``). Pass ``None`` (the
      default) to skip the attempt-count bound entirely, e.g. for a
      fresh/first dispatch attempt where an early attempt number can never
      legitimately equal the cap.

    Returns the delivery's resulting status (``"failed"`` or
    ``"failed_permanent"``), or ``None`` if no row was updated (already
    ``delivered``/``failed_permanent`` -- nothing to record).
    """
    return await pool.fetchval(
        """
        UPDATE public.domain_event_deliveries
        SET status = CASE
                WHEN NOT $3 THEN 'failed_permanent'
                WHEN $4::int IS NOT NULL AND attempt_count + 1 >= $4 THEN 'failed_permanent'
                ELSE 'failed'
            END,
            attempt_count = attempt_count + 1,
            error_message = $2,
            updated_at = now()
        WHERE id = $1 AND status NOT IN ('delivered', 'failed_permanent')
        RETURNING status
        """,
        uuid.UUID(str(delivery_id)),
        error_message,
        retryable,
        max_attempts,
    )


async def list_deliveries_for_event(
    pool: asyncpg.Pool, event_id: uuid.UUID | str
) -> list[dict[str, Any]]:
    """List every delivery row for one event, subscriber-ordered (dashboard/debug read)."""
    rows = await pool.fetch(
        """
        SELECT id, event_id, subscriber_butler, status, task_id, task_name,
               error_message, attempt_count, delivered_at, created_at, updated_at
        FROM public.domain_event_deliveries
        WHERE event_id = $1
        ORDER BY subscriber_butler
        """,
        uuid.UUID(str(event_id)),
    )
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Reconciliation sweep candidate reads (bu-1yw6d)
# ---------------------------------------------------------------------------


async def select_stale_pending_deliveries(
    pool: asyncpg.Pool,
    *,
    older_than: timedelta,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return ``pending`` deliveries whose claim is older than *older_than*.

    A delivery row is claimed (inserted ``pending``) the moment
    :func:`claim_delivery` first sees a fresh ``(event, subscriber)`` pair,
    and ``updated_at`` is set at that same insert (nothing bumps it again
    until an outcome is recorded). A row still ``pending`` well past a
    normal dispatch's lifetime (bounded by the ~30s route-call timeout, plus
    generous scheduling slack) means the claiming process crashed -- or
    otherwise never reached ``mark_delivery_delivered``/``mark_delivery_
    failed`` -- before it could record an outcome. Joins in the owning
    event's ``event_type``/``source_butler``/``payload`` -- the periodic
    reconciliation sweep (``run_domain_event_reconciliation_sweep``) needs
    these to re-drive the dispatch, exactly as ``fan_out_event`` needs them
    for a fresh publish. Ordered oldest-claimed-first so a backlog drains in
    claim order rather than arbitrarily.
    """
    rows = await pool.fetch(
        """
        SELECT d.id, d.event_id, d.subscriber_butler, d.status, d.attempt_count,
               e.event_type, e.source_butler, e.payload
        FROM public.domain_event_deliveries d
        JOIN public.domain_events e ON e.id = d.event_id
        WHERE d.status = 'pending' AND d.updated_at < now() - $1::interval
        ORDER BY d.updated_at ASC
        LIMIT $2
        """,
        older_than,
        limit,
    )
    return [_row_to_dict(r) for r in rows]


async def select_retryable_failed_deliveries(
    pool: asyncpg.Pool,
    *,
    backoff_after: timedelta,
    max_attempts: int,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return ``failed`` deliveries eligible for a bounded, backed-off retry.

    Eligible when ``attempt_count < max_attempts`` (the same bound
    :func:`mark_delivery_failed` uses to transition a row to the terminal
    ``failed_permanent`` status instead) and at least *backoff_after* has
    elapsed since the last attempt (``updated_at``, bumped by every
    ``mark_delivery_failed`` call), so a genuinely down subscriber is not
    hammered on every sweep tick. A row already at ``failed_permanent`` never
    matches ``status = 'failed'`` and is correctly excluded -- it is a
    terminal state, not a retry candidate.
    """
    rows = await pool.fetch(
        """
        SELECT d.id, d.event_id, d.subscriber_butler, d.status, d.attempt_count,
               d.error_message, e.event_type, e.source_butler, e.payload
        FROM public.domain_event_deliveries d
        JOIN public.domain_events e ON e.id = d.event_id
        WHERE d.status = 'failed'
          AND d.attempt_count < $1
          AND d.updated_at < now() - $2::interval
        ORDER BY d.updated_at ASC
        LIMIT $3
        """,
        max_attempts,
        backoff_after,
        limit,
    )
    return [_row_to_dict(r) for r in rows]


async def list_recent_deliveries(
    pool: asyncpg.Pool,
    *,
    subscriber_butler: str | None = None,
    source_butler: str | None = None,
    status: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    """List fan-out deliveries joined with their event, most-recent first.

    Dashboard subscription-visibility read (bu-317s5 slice 2): unlike
    :func:`list_deliveries_for_event` (all deliveries for one known event),
    this is the fleet-wide "what has this butler recently been fanned out to
    (or fanned out itself)" query, so it joins in ``event_type``/
    ``source_butler``/``occurred_at`` from ``public.domain_events`` rather
    than requiring the caller to already know the event id.

    Returns ``(total, rows)`` where ``total`` is the unfiltered-by-page count
    matching the given filters (for pagination).
    """
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if subscriber_butler is not None:
        conditions.append(f"d.subscriber_butler = ${idx}")
        args.append(subscriber_butler)
        idx += 1
    if source_butler is not None:
        conditions.append(f"e.source_butler = ${idx}")
        args.append(source_butler)
        idx += 1
    if status is not None:
        conditions.append(f"d.status = ${idx}")
        args.append(status)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(
        f"""
        SELECT count(*)
        FROM public.domain_event_deliveries d
        JOIN public.domain_events e ON e.id = d.event_id
        {where}
        """,
        *args,
    )
    rows = await pool.fetch(
        f"""
        SELECT d.id, d.event_id, d.subscriber_butler, d.status, d.task_id, d.task_name,
               d.error_message, d.attempt_count, d.delivered_at, d.created_at, d.updated_at,
               e.event_type, e.source_butler, e.occurred_at
        FROM public.domain_event_deliveries d
        JOIN public.domain_events e ON e.id = d.event_id
        {where}
        ORDER BY d.created_at DESC
        OFFSET ${idx} LIMIT ${idx + 1}
        """,
        *args,
        offset,
        limit,
    )
    return int(total or 0), [_row_to_dict(r) for r in rows]
