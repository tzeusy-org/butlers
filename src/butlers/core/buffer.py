"""Durable hybrid message buffer for switchboard ingestion dispatch.

Replaces unbounded asyncio.create_task() dispatch with a bounded in-memory
queue (hot path) backed by periodic DB scanning for crash recovery (cold path).

Architecture (Section 4.1 of docs/architecture/concurrency.md):

    Hot path:
        ingest → queue.put(ref) → worker → pipeline.process()

    Cold path:
        scanner polls DB every 30s for 'accepted' rows older than grace period
        → re-enqueues to the same in-memory queue

Backpressure: when the queue is full, the hot path skips enqueue.  The message
is already in message_inbox with lifecycle_state='accepted', so the scanner
will recover it on its next sweep.

Metrics emitted:
    butlers.buffer.queue_depth         (gauge, sampled on each enqueue attempt)
    butlers.buffer.enqueue_total       (counter, path=hot|cold)
    butlers.buffer.backpressure_total  (counter)
    butlers.buffer.scanner_recovered_total (counter)
    butlers.buffer.process_latency_ms  (histogram)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from butlers.config import BufferConfig
from butlers.core.metrics import ButlerMetrics

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message reference — lightweight envelope passed through the queue
# ---------------------------------------------------------------------------


@dataclass
class _MessageRef:
    """Lightweight reference to a persisted message_inbox row.

    Only carries data needed to call pipeline.process().  The full envelope
    is durably stored in the DB and does not need to be kept in memory.
    """

    request_id: str
    message_inbox_id: Any  # UUID stored in message_inbox
    message_text: str
    source: dict[str, Any]
    event: dict[str, Any]
    sender: dict[str, Any]
    enqueued_at: datetime


# ---------------------------------------------------------------------------
# DurableBuffer
# ---------------------------------------------------------------------------


class DurableBuffer:
    """Bounded in-memory queue with DB-backed crash recovery.

    Parameters
    ----------
    config:
        Buffer tuning knobs from the ``[buffer]`` TOML section.
    pool:
        asyncpg connection pool for the switchboard database.
        Required for the scanner; may be None in tests that skip the scanner.
    process_fn:
        Async callable that processes a single message reference.
        Signature: ``process_fn(ref: _MessageRef) -> None``
        Typically wraps ``pipeline.process()``.
    butler_name:
        Optional butler name used to label OTel metrics.  Defaults to
        ``"switchboard"`` (the only butler that uses DurableBuffer in
        production).
    """

    def __init__(
        self,
        config: BufferConfig,
        pool: Any,
        process_fn: Any,
        *,
        butler_name: str = "switchboard",
    ) -> None:
        self._config = config
        self._pool = pool
        self._process_fn = process_fn
        self._metrics = ButlerMetrics(butler_name=butler_name)

        self._queue: asyncio.Queue[_MessageRef] = asyncio.Queue(maxsize=config.queue_capacity)

        # Background tasks created in start(), cancelled in stop()
        self._worker_tasks: list[asyncio.Task] = []
        self._scanner_task: asyncio.Task | None = None
        self._running = False

        # Counters for observability (incremented in-process; exported via OTEL)
        self._enqueue_hot_total: int = 0
        self._enqueue_cold_total: int = 0
        self._backpressure_total: int = 0
        self._scanner_recovered_total: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn worker coroutines and the periodic scanner."""
        if self._running:
            return
        self._running = True

        for i in range(self._config.worker_count):
            task = asyncio.create_task(
                self._worker_loop(worker_id=i),
                name=f"buffer-worker-{i}",
            )
            self._worker_tasks.append(task)

        if self._pool is not None:
            self._scanner_task = asyncio.create_task(
                self._scanner_loop(),
                name="buffer-scanner",
            )

        logger.info(
            "DurableBuffer started: workers=%d, queue_capacity=%d, "
            "scanner_interval_s=%d, scanner_grace_s=%d",
            self._config.worker_count,
            self._config.queue_capacity,
            self._config.scanner_interval_s,
            self._config.scanner_grace_s,
        )

    async def stop(self, drain_timeout_s: float = 10.0) -> None:
        """Stop the buffer gracefully.

        1. Cancel the scanner so it stops re-enqueueing.
        2. Wait for the in-memory queue to drain up to *drain_timeout_s*.
        3. Cancel worker tasks.
        """
        if not self._running:
            return
        self._running = False

        # Stop scanner first so it doesn't keep re-enqueueing
        if self._scanner_task is not None:
            self._scanner_task.cancel()
            try:
                await self._scanner_task
            except asyncio.CancelledError:
                pass
            self._scanner_task = None

        # Drain remaining queue items
        if drain_timeout_s > 0 and not self._queue.empty():
            try:
                await asyncio.wait_for(
                    self._queue.join(),
                    timeout=drain_timeout_s,
                )
            except TimeoutError:
                logger.warning(
                    "DurableBuffer drain timed out after %.1fs; "
                    "approximately %d messages remain in queue",
                    drain_timeout_s,
                    self._queue.qsize(),
                )

        # Cancel workers
        for task in self._worker_tasks:
            task.cancel()
        for task in self._worker_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker_tasks.clear()

        logger.info(
            "DurableBuffer stopped: hot=%d, cold=%d, backpressure=%d, recovered=%d",
            self._enqueue_hot_total,
            self._enqueue_cold_total,
            self._backpressure_total,
            self._scanner_recovered_total,
        )

    # ------------------------------------------------------------------
    # Hot path
    # ------------------------------------------------------------------

    def enqueue(
        self,
        *,
        request_id: str,
        message_inbox_id: Any,
        message_text: str,
        source: dict[str, Any],
        event: dict[str, Any],
        sender: dict[str, Any],
    ) -> bool:
        """Attempt to enqueue a message reference (non-blocking, hot path).

        Returns True if enqueued, False if the queue was full (backpressure).
        The message is already in message_inbox, so backpressure means the
        scanner will recover it — no data loss.
        """
        ref = _MessageRef(
            request_id=request_id,
            message_inbox_id=message_inbox_id,
            message_text=message_text,
            source=source,
            event=event,
            sender=sender,
            enqueued_at=datetime.now(UTC),
        )

        try:
            self._queue.put_nowait(ref)
            self._enqueue_hot_total += 1
            self._metrics.buffer_enqueue_hot()
            self._metrics.buffer_queue_depth_inc()
            logger.debug(
                "Buffer enqueued (hot): request_id=%s, queue_depth=%d",
                request_id,
                self._queue.qsize(),
            )
            return True
        except asyncio.QueueFull:
            self._backpressure_total += 1
            self._metrics.buffer_backpressure()
            logger.warning(
                "Buffer full (backpressure): request_id=%s will be recovered "
                "by scanner in ~%ds, depth=%d",
                request_id,
                self._config.scanner_interval_s,
                self._queue.qsize(),
            )
            return False

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _worker_loop(self, worker_id: int) -> None:
        """Drain the queue by calling process_fn for each message."""
        while True:
            try:
                ref = await self._queue.get()
            except asyncio.CancelledError:
                break

            try:
                process_latency_ms = (datetime.now(UTC) - ref.enqueued_at).total_seconds() * 1000

                logger.debug(
                    "Buffer worker %d processing request_id=%s queue_wait_ms=%.0f",
                    worker_id,
                    ref.request_id,
                    process_latency_ms,
                )

                self._metrics.record_buffer_process_latency(process_latency_ms)
                self._metrics.buffer_queue_depth_dec()

                await self._process_fn(ref)

            except asyncio.CancelledError:
                # Re-enqueue or just mark done and re-raise
                self._queue.task_done()
                raise
            except Exception:
                logger.exception(
                    "Buffer worker %d: processing failed for request_id=%s",
                    worker_id,
                    ref.request_id,
                )
            finally:
                try:
                    self._queue.task_done()
                except ValueError:
                    pass  # task_done() called more times than put()

    # ------------------------------------------------------------------
    # Scanner loop (cold path / crash recovery)
    # ------------------------------------------------------------------

    async def _scanner_loop(self) -> None:
        """Periodically scan message_inbox for stuck 'accepted' messages.

        Runs every scanner_interval_s seconds.  Queries rows in 'accepted'
        state older than scanner_grace_s seconds to avoid racing the hot path.
        """
        while True:
            try:
                await asyncio.sleep(self._config.scanner_interval_s)
            except asyncio.CancelledError:
                break

            try:
                await self._run_scanner_sweep()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Buffer scanner sweep failed")

    async def _run_scanner_sweep(self) -> int:
        """Execute one scanner sweep and re-enqueue stuck messages.

        Returns the number of messages recovered.
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        id,
                        received_at,
                        request_context,
                        raw_payload,
                        normalized_text
                    FROM message_inbox
                    WHERE lifecycle_state = 'accepted'
                      AND received_at < now() - ($1 * interval '1 second')
                    ORDER BY received_at ASC
                    LIMIT $2
                    """,
                    self._config.scanner_grace_s,
                    self._config.scanner_batch_size,
                )
        except Exception:
            logger.exception("Buffer scanner DB query failed")
            return 0

        if not rows:
            return 0

        recovered = 0
        for row in rows:
            request_context = row["request_context"] or {}
            raw_payload = row["raw_payload"] or {}
            normalized_text = row["normalized_text"] or ""

            if not normalized_text:
                # No text to route; mark as errored so scanner skips it
                try:
                    async with self._pool.acquire() as conn:
                        await conn.execute(
                            """
                            UPDATE message_inbox
                            SET lifecycle_state = 'errored',
                                updated_at = now()
                            WHERE id = $1
                              AND lifecycle_state = 'accepted'
                            """,
                            row["id"],
                        )
                except Exception:
                    logger.exception(
                        "Buffer scanner: failed to mark empty row errored id=%s",
                        row["id"],
                    )
                continue

            # Reconstruct source / event / sender from stored raw_payload
            source = raw_payload.get("source", {})
            event_raw = raw_payload.get("event", {})
            sender = raw_payload.get("sender", {})

            request_id = str(request_context.get("request_id", str(row["id"])))

            ref = _MessageRef(
                request_id=request_id,
                message_inbox_id=row["id"],
                message_text=normalized_text,
                source=source,
                event=event_raw,
                sender=sender,
                enqueued_at=datetime.now(UTC),
            )

            try:
                # Non-blocking; if queue is full, skip and retry next sweep
                self._queue.put_nowait(ref)
                self._enqueue_cold_total += 1
                self._scanner_recovered_total += 1
                recovered += 1
                self._metrics.buffer_enqueue_cold()
                self._metrics.buffer_scanner_recovered()
                self._metrics.buffer_queue_depth_inc()
                logger.info(
                    "Buffer scanner recovered request_id=%s (received_at=%s)",
                    request_id,
                    row["received_at"].isoformat(),
                )
            except asyncio.QueueFull:
                # Queue is full; this message will be retried next sweep
                logger.debug(
                    "Buffer scanner: queue full, skipping request_id=%s (will retry next sweep)",
                    request_id,
                )
                break  # Stop adding more; the queue is at capacity

        if recovered:
            logger.info("Buffer scanner sweep: recovered %d message(s)", recovered)

        return recovered

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    @property
    def queue_depth(self) -> int:
        """Current number of messages in the in-memory queue."""
        return self._queue.qsize()

    @property
    def stats(self) -> dict[str, int]:
        """Snapshot of buffer counters for health reporting."""
        return {
            "queue_depth": self._queue.qsize(),
            "enqueue_hot_total": self._enqueue_hot_total,
            "enqueue_cold_total": self._enqueue_cold_total,
            "backpressure_total": self._backpressure_total,
            "scanner_recovered_total": self._scanner_recovered_total,
        }
