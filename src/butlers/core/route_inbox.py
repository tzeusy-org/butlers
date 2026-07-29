"""route_inbox — durable work queue for async route dispatch (Section 4.4).

When route.execute is called on a target butler, the request is persisted here
before returning {"status": "accepted"} to the switchboard.  A background task
then processes the request by calling spawner.trigger().

Lifecycle states:
    accepted   — persisted, not yet processed
    processing — background task has started (set before trigger())
    processed  — trigger() completed successfully
    errored    — trigger() raised an exception; error column populated

Crash recovery uses an ownership lease rather than treating a stale row as an
automatic right to re-run it.  A hot-path or recovery worker atomically claims
the row before it invokes a runtime, renews that lease while work is live, and
may settle the row only while it still owns the same claim.  That prevents a
second daemon from replaying a healthy dashboard route just because a startup
scanner observed it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Lifecycle states
STATE_ACCEPTED = "accepted"
STATE_PROCESSING = "processing"
STATE_PROCESSED = "processed"
STATE_ERRORED = "errored"

# Default grace period before crash-recovery scanner picks up stuck rows (seconds)
_DEFAULT_RECOVERY_GRACE_S = 10
# Default scanner batch size
_DEFAULT_RECOVERY_BATCH = 50
# Keep a claim fresh well inside the default recovery grace interval.  The
# lease is a crash detector, not a runtime deadline: a healthy long-running
# session keeps renewing it until it settles.
_DEFAULT_PROCESSING_HEARTBEAT_S = 3


class RouteInboxLeaseLost(RuntimeError):
    """Raised after a live invocation is cancelled because its queue lease was lost."""


async def route_inbox_insert_on_connection(
    conn: asyncpg.Connection,
    *,
    route_envelope: dict[str, Any],
) -> uuid.UUID:
    """Insert a route-inbox row using the caller's connection.

    The caller owns transaction scope.  This is the primitive for a route
    target that must atomically persist its inbox row with a dashboard-turn
    claim; acquiring another pool connection there would split the commit and
    re-open a Stop-versus-handoff race.

    Returns the newly minted route-inbox id.
    """
    row_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO route_inbox (id, route_envelope, lifecycle_state)
        VALUES ($1, $2, $3)
        """,
        row_id,
        route_envelope,
        STATE_ACCEPTED,
    )
    logger.debug("route_inbox: inserted id=%s", row_id)
    return row_id


async def route_inbox_insert(
    pool: asyncpg.Pool,
    *,
    route_envelope: dict[str, Any],
) -> uuid.UUID:
    """Insert a new route_inbox row in 'accepted' state.

    Parameters
    ----------
    pool:
        asyncpg connection pool for this butler's database.
    route_envelope:
        The full validated route envelope dict (JSON-serialisable).

    Returns
    -------
    uuid.UUID
        The newly created row id.
    """
    async with pool.acquire() as conn:
        return await route_inbox_insert_on_connection(conn, route_envelope=route_envelope)


async def route_inbox_claim_processing(
    pool: asyncpg.Pool,
    row_id: uuid.UUID,
    *,
    recovery: bool = False,
    stale_after_s: int = _DEFAULT_RECOVERY_GRACE_S,
) -> uuid.UUID | None:
    """Atomically own a row's processing lease, or return ``None``.

    The hot path can claim only a freshly accepted row.  Recovery may also
    take over a processing row whose owner stopped renewing its lease.  The
    returned opaque id fences every later heartbeat and terminal update.
    """
    claim_id = uuid.uuid4()
    async with pool.acquire() as conn:
        claimed = await conn.fetchval(
            """
            UPDATE route_inbox
            SET lifecycle_state = $1,
                processing_claim_id = $2,
                processing_claimed_at = now()
            WHERE id = $3
              AND (
                  lifecycle_state = $4
                  OR (
                      $5::boolean
                      AND lifecycle_state = $1
                      AND (
                          processing_claimed_at IS NULL
                          OR processing_claimed_at < now() - ($6 * interval '1 second')
                      )
                  )
              )
            RETURNING processing_claim_id
            """,
            STATE_PROCESSING,
            claim_id,
            row_id,
            STATE_ACCEPTED,
            recovery,
            stale_after_s,
        )
    if claimed is None:
        return None
    return claimed if isinstance(claimed, uuid.UUID) else uuid.UUID(str(claimed))


async def route_inbox_mark_processing(
    pool: asyncpg.Pool,
    row_id: uuid.UUID,
) -> bool:
    """Compatibility wrapper for callers that only need hot-path ownership."""
    return await route_inbox_claim_processing(pool, row_id) is not None


async def route_inbox_renew_processing_claim(
    pool: asyncpg.Pool,
    row_id: uuid.UUID,
    processing_claim_id: uuid.UUID,
) -> bool:
    """Refresh one processing lease and report whether its owner still owns it."""
    async with pool.acquire() as conn:
        renewed = await conn.fetchval(
            """
            UPDATE route_inbox
            SET processing_claimed_at = now()
            WHERE id = $1
              AND lifecycle_state = $2
              AND processing_claim_id = $3
            RETURNING id
            """,
            row_id,
            STATE_PROCESSING,
            processing_claim_id,
        )
    return renewed is not None


@asynccontextmanager
async def route_inbox_processing_lease_heartbeat(
    pool: asyncpg.Pool,
    row_id: uuid.UUID,
    processing_claim_id: uuid.UUID,
    *,
    interval_s: float = _DEFAULT_PROCESSING_HEARTBEAT_S,
) -> AsyncIterator[asyncio.Event]:
    """Fence a processing claim before protected work and keep it fresh."""
    lease_lost = asyncio.Event()

    try:
        owns_claim = await route_inbox_renew_processing_claim(
            pool,
            row_id,
            processing_claim_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception(
            "route_inbox: initial processing lease fence failed id=%s claim=%s",
            row_id,
            processing_claim_id,
        )
        raise RouteInboxLeaseLost(
            "route inbox processing lease could not be verified before protected work"
        ) from exc
    if not owns_claim:
        raise RouteInboxLeaseLost("route inbox processing lease was lost before protected work")

    async def _heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(interval_s)
                if not await route_inbox_renew_processing_claim(
                    pool,
                    row_id,
                    processing_claim_id,
                ):
                    logger.warning(
                        "route_inbox: processing lease lost id=%s claim=%s",
                        row_id,
                        processing_claim_id,
                    )
                    lease_lost.set()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # A failed renewal must not be treated as ownership.  A later
            # recovery worker may take the stale lease, and this worker's
            # fenced terminal write will then safely fail.
            logger.exception(
                "route_inbox: processing lease heartbeat failed id=%s claim=%s",
                row_id,
                processing_claim_id,
            )
            lease_lost.set()

    task = asyncio.create_task(
        _heartbeat(),
        name=f"route-inbox-lease-{row_id}",
    )
    try:
        yield lease_lost
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def route_inbox_wait_while_claimed[ResultT](
    pool: asyncpg.Pool,
    row_id: uuid.UUID,
    processing_claim_id: uuid.UUID,
    lease_lost: asyncio.Event,
    invocation_factory: Callable[[], Awaitable[ResultT]],
) -> ResultT:
    """Await an invocation only while its route-inbox processing lease remains owned.

    A fenced terminal write prevents a displaced worker from changing queue
    state, but it cannot stop an already-running local runtime.  Race the
    invocation with the heartbeat's lease-loss event so this worker cancels
    its own runtime before a recovery owner can consider the row again.  This
    remains best-effort across a process or database partition; dashboard
    recovery separately treats an unprovable predecessor as ambiguous rather
    than automatically replaying it.
    """
    # Do not create the runtime coroutine until ownership is known. Creating
    # it first makes a pre-existing loss both observable too late and easy to
    # leak as an un-awaited coroutine in callers/tests. The heartbeat covers
    # long-running protected work; this immediate database fence closes the
    # final handoff between that work and runtime creation.
    if lease_lost.is_set():
        raise RouteInboxLeaseLost("route inbox processing lease was already lost")
    try:
        owns_claim = await route_inbox_renew_processing_claim(
            pool,
            row_id,
            processing_claim_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception(
            "route_inbox: pre-invocation processing lease fence failed id=%s claim=%s",
            row_id,
            processing_claim_id,
        )
        lease_lost.set()
        raise RouteInboxLeaseLost(
            "route inbox processing lease could not be verified before invocation"
        ) from exc
    if not owns_claim or lease_lost.is_set():
        lease_lost.set()
        raise RouteInboxLeaseLost("route inbox processing lease was lost before invocation")

    invocation_task = asyncio.ensure_future(invocation_factory())
    lease_wait_task = asyncio.create_task(lease_lost.wait())
    try:
        done, _ = await asyncio.wait(
            {invocation_task, lease_wait_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if lease_wait_task in done or lease_lost.is_set():
            if not invocation_task.done():
                invocation_task.cancel()
                try:
                    await invocation_task
                except (asyncio.CancelledError, Exception):
                    # Lease loss is the authoritative boundary.  A runtime's
                    # cancellation cleanup may itself fail, but that must not
                    # fall through to a generic route terminal write after a
                    # recovery owner has taken the row.
                    logger.debug(
                        "route_inbox: invocation cleanup raised after lease loss",
                        exc_info=True,
                    )
            else:
                # Consume a simultaneous exception so the cancelled lease
                # boundary remains the one surfaced to callers.
                try:
                    invocation_task.result()
                except (asyncio.CancelledError, Exception):
                    pass
            raise RouteInboxLeaseLost(
                "route inbox processing lease was lost while the runtime was live"
            )
        return await invocation_task
    finally:
        lease_wait_task.cancel()
        try:
            await lease_wait_task
        except asyncio.CancelledError:
            pass
        if not invocation_task.done():
            invocation_task.cancel()
            try:
                await invocation_task
            except (asyncio.CancelledError, Exception):
                pass


async def route_inbox_mark_processed(
    pool: asyncpg.Pool,
    row_id: uuid.UUID,
    session_id: uuid.UUID | None,
    *,
    processing_claim_id: uuid.UUID | None = None,
) -> bool:
    """Transition a route_inbox row to 'processed' on success.

    When a processing claim is supplied, only its current owner can settle the
    row.  The optional form remains for legacy non-leased callers.
    """
    async with pool.acquire() as conn:
        settled = await conn.fetchval(
            """
        UPDATE route_inbox
        SET lifecycle_state = $1,
            processed_at = now(),
            session_id = $2,
            processing_claim_id = NULL,
            processing_claimed_at = NULL
        WHERE id = $3
          AND lifecycle_state = $5
          AND ($4::uuid IS NULL OR processing_claim_id = $4)
        RETURNING id
        """,
            STATE_PROCESSED,
            session_id,
            row_id,
            processing_claim_id,
            STATE_PROCESSING,
        )
    if settled is not None:
        logger.debug("route_inbox: processed id=%s session_id=%s", row_id, session_id)
    return settled is not None


async def route_inbox_mark_errored(
    pool: asyncpg.Pool,
    row_id: uuid.UUID,
    error: str,
    *,
    processing_claim_id: uuid.UUID | None = None,
) -> bool:
    """Transition a route_inbox row to 'errored' and store the error message.

    A supplied claim id fences an old worker from overwriting a newer owner's
    result after recovery has taken over the row.
    """
    async with pool.acquire() as conn:
        settled = await conn.fetchval(
            """
        UPDATE route_inbox
        SET lifecycle_state = $1,
            processed_at = now(),
            error = $2,
            processing_claim_id = NULL,
            processing_claimed_at = NULL
        WHERE id = $3
          AND lifecycle_state = $5
          AND ($4::uuid IS NULL OR processing_claim_id = $4)
        RETURNING id
        """,
            STATE_ERRORED,
            error,
            row_id,
            processing_claim_id,
            STATE_PROCESSING,
        )
    if settled is not None:
        logger.warning("route_inbox: errored id=%s error=%s", row_id, error[:200])
    return settled is not None


async def route_inbox_scan_unprocessed(
    pool: asyncpg.Pool,
    *,
    grace_s: int = _DEFAULT_RECOVERY_GRACE_S,
    batch_size: int = _DEFAULT_RECOVERY_BATCH,
) -> list[dict[str, Any]]:
    """Scan for route_inbox rows whose accept or processing lease is stale.

    Returns rows older than *grace_s* seconds that have not completed
    processing.  Used for crash recovery on startup.

    Both 'accepted' and stale 'processing' rows are included because a daemon
    crash or graceful shutdown can leave either state behind.  The subsequent
    atomic claim is authoritative; this scan is only candidate discovery.

    Parameters
    ----------
    pool:
        asyncpg connection pool for this butler's database.
    grace_s:
        Minimum age in seconds before a row is considered stuck.
    batch_size:
        Maximum number of rows to return per call.

    Returns
    -------
    list[dict]
        Each dict has keys: id, received_at, route_envelope.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, received_at, route_envelope, lifecycle_state
            FROM route_inbox
            WHERE (
                    lifecycle_state = $1
                    AND received_at < now() - ($3 * interval '1 second')
                  )
               OR (
                    lifecycle_state = $2
                    AND (
                        processing_claimed_at IS NULL
                        OR processing_claimed_at < now() - ($3 * interval '1 second')
                    )
                  )
            ORDER BY coalesce(processing_claimed_at, received_at) ASC
            LIMIT $4
            """,
            STATE_ACCEPTED,
            STATE_PROCESSING,
            grace_s,
            batch_size,
        )

    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "received_at": row["received_at"],
                "route_envelope": json.loads(row["route_envelope"])
                if isinstance(row["route_envelope"], str)
                else dict(row["route_envelope"]),
                "lifecycle_state": row.get("lifecycle_state", STATE_ACCEPTED)
                if hasattr(row, "get")
                else STATE_ACCEPTED,
            }
        )
    logger.debug("route_inbox scan: found %d unprocessed row(s)", len(result))
    return result


async def route_inbox_recovery_sweep(
    pool: asyncpg.Pool,
    *,
    grace_s: int = _DEFAULT_RECOVERY_GRACE_S,
    batch_size: int = _DEFAULT_RECOVERY_BATCH,
    dispatch_fn: Any,
) -> int:
    """Recover and re-dispatch stuck route_inbox rows.

    Called on startup (and optionally periodically) to process rows that were
    accepted but never processed due to a crash or restart.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    grace_s:
        Minimum age for a row to be considered stuck.
    batch_size:
        Maximum rows per sweep.
    dispatch_fn:
        Async callable with signature
        ``dispatch_fn(row_id, route_envelope, processing_claim_id,
        recovery_from_processing) -> None``.  It receives an already-acquired
        lease and must use that id for its heartbeat and terminal write.
        ``recovery_from_processing`` distinguishes a never-started accepted
        row from a stale row whose predecessor may already have invoked a
        runtime.

    Returns
    -------
    int
        Number of rows recovered (dispatched for re-processing).
    """
    rows = await route_inbox_scan_unprocessed(pool, grace_s=grace_s, batch_size=batch_size)
    if not rows:
        return 0

    recovered = 0
    now = datetime.now(UTC)
    for row in rows:
        row_id = row["id"]
        raw_envelope = row["route_envelope"]
        route_envelope = (
            json.loads(raw_envelope) if isinstance(raw_envelope, str) else dict(raw_envelope)
        )
        recovery_from_processing = row.get("lifecycle_state") == STATE_PROCESSING
        age_s = (now - row["received_at"].replace(tzinfo=UTC)).total_seconds()
        logger.info(
            "route_inbox recovery: re-dispatching id=%s (age=%.0fs)",
            row_id,
            age_s,
        )
        processing_claim_id = await route_inbox_claim_processing(
            pool,
            row_id,
            recovery=True,
            stale_after_s=grace_s,
        )
        if processing_claim_id is None:
            logger.debug("route_inbox recovery: claim lost id=%s", row_id)
            continue
        try:
            await dispatch_fn(
                row_id=row_id,
                route_envelope=route_envelope,
                processing_claim_id=processing_claim_id,
                recovery_from_processing=recovery_from_processing,
            )
            recovered += 1
        except Exception:
            logger.exception("route_inbox recovery: dispatch failed for id=%s", row_id)

    if recovered:
        logger.info("route_inbox recovery sweep: recovered %d row(s)", recovered)
    return recovered
