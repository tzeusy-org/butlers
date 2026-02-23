"""Durable hybrid message buffer for switchboard ingestion dispatch.

Replaces unbounded asyncio.create_task() dispatch with a bounded in-memory
queue (hot path) backed by periodic DB scanning for crash recovery (cold path).

Architecture (Section 4.1 of docs/architecture/concurrency.md):

    Hot path:
        ingest → enqueue(ref, tier) → tier_queue.put(ref) → worker → pipeline.process()

    Cold path:
        scanner polls DB every 30s for 'accepted' rows older than grace period
        → re-enqueues to the appropriate tier queue

Backpressure: when all tier queues are full, the hot path skips enqueue.  The
message is already in message_inbox with lifecycle_state='accepted', so the
scanner will recover it on its next sweep.

Priority tiers (highest to lowest, per docs/switchboard/email_priority_queuing.md):
    high_priority > interactive > default

Starvation guard: after N consecutive dequeues from tier T, if any lower-priority
tier is non-empty the worker is forced to serve the next item from the highest
non-empty lower tier.  Configurable via BufferConfig.max_consecutive_same_tier
(default 10).

Metrics emitted:
    butlers.buffer.queue_depth              (gauge, sampled on each enqueue attempt)
    butlers.buffer.enqueue_total            (counter, path=hot|cold)
    butlers.buffer.backpressure_total       (counter)
    butlers.buffer.scanner_recovered_total  (counter)
    butlers.buffer.process_latency_ms       (histogram)
    butlers.switchboard.queue.dequeue_by_tier (counter, policy_tier, starvation_override)
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from butlers.config import BufferConfig
from butlers.core.metrics import ButlerMetrics

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Policy tier constants and ordering
# ---------------------------------------------------------------------------

POLICY_TIER_HIGH_PRIORITY = "high_priority"
POLICY_TIER_INTERACTIVE = "interactive"
POLICY_TIER_DEFAULT = "default"

# Ordered from highest to lowest priority
POLICY_TIER_ORDER: list[str] = [
    POLICY_TIER_HIGH_PRIORITY,
    POLICY_TIER_INTERACTIVE,
    POLICY_TIER_DEFAULT,
]


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
    policy_tier: str = field(default=POLICY_TIER_DEFAULT)


# ---------------------------------------------------------------------------
# Starvation guard state
# ---------------------------------------------------------------------------


@dataclass
class _StarvedTierState:
    """Tracks consecutive dequeue counts per tier for starvation prevention.

    The guard works as follows:
    - current_tier: the tier most recently dequeued from (None at startup).
    - consecutive_count: how many times in a row we have dequeued from current_tier.

    When consecutive_count reaches max_consecutive and a lower-priority queue is
    non-empty, the next dequeue is forced from the highest available lower tier.
    After a forced lower-tier dequeue the counter resets and re-evaluates from
    the highest available tier on the next call.
    """

    current_tier: str | None = None
    consecutive_count: int = 0


# ---------------------------------------------------------------------------
# DurableBuffer
# ---------------------------------------------------------------------------


class DurableBuffer:
    """Bounded in-memory priority queue with DB-backed crash recovery.

    Three tier queues are maintained internally:
        high_priority_queue → interactive_queue → default_queue

    Workers dequeue from the highest non-empty tier, subject to the starvation
    guard that prevents indefinite deferral of lower tiers.

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

        # Per-tier queues; total capacity shared equally across tiers.
        # Each tier queue holds up to queue_capacity items so the overall
        # system can buffer at most 3 * queue_capacity messages.
        tier_capacity = config.queue_capacity
        self._tier_queues: dict[str, asyncio.Queue[_MessageRef]] = {
            POLICY_TIER_HIGH_PRIORITY: asyncio.Queue(maxsize=tier_capacity),
            POLICY_TIER_INTERACTIVE: asyncio.Queue(maxsize=tier_capacity),
            POLICY_TIER_DEFAULT: asyncio.Queue(maxsize=tier_capacity),
        }

        # Starvation guard: shared across all workers via asyncio single-thread
        # semantics (no mutex needed inside a single event loop).
        self._starvation_state = _StarvedTierState()

        # Event that workers wait on when all queues are empty.
        # Set whenever a message is enqueued.
        self._has_work: asyncio.Event = asyncio.Event()

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
            "DurableBuffer started: workers=%d, queue_capacity=%d (per tier), "
            "scanner_interval_s=%d, scanner_grace_s=%d, max_consecutive_same_tier=%d",
            self._config.worker_count,
            self._config.queue_capacity,
            self._config.scanner_interval_s,
            self._config.scanner_grace_s,
            self._config.max_consecutive_same_tier,
        )

    async def stop(self, drain_timeout_s: float = 10.0) -> None:
        """Stop the buffer gracefully.

        1. Cancel the scanner so it stops re-enqueueing.
        2. Wait for all in-memory queues to drain up to *drain_timeout_s*.
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
        if drain_timeout_s > 0 and not self._all_queues_empty():
            try:
                await asyncio.wait_for(
                    self._drain_all_queues(),
                    timeout=drain_timeout_s,
                )
            except TimeoutError:
                remaining = self.queue_depth
                logger.warning(
                    "DurableBuffer drain timed out after %.1fs; "
                    "approximately %d messages remain in queues",
                    drain_timeout_s,
                    remaining,
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
        policy_tier: str = POLICY_TIER_DEFAULT,
    ) -> bool:
        """Attempt to enqueue a message reference (non-blocking, hot path).

        Routes the message to the appropriate tier queue.

        Returns True if enqueued, False if the tier queue was full (backpressure).
        The message is already in message_inbox, so backpressure means the
        scanner will recover it — no data loss.
        """
        # Validate / normalise tier
        if policy_tier not in self._tier_queues:
            logger.warning(
                "Unknown policy_tier=%r for request_id=%s; falling back to 'default'",
                policy_tier,
                request_id,
            )
            policy_tier = POLICY_TIER_DEFAULT

        ref = _MessageRef(
            request_id=request_id,
            message_inbox_id=message_inbox_id,
            message_text=message_text,
            source=source,
            event=event,
            sender=sender,
            enqueued_at=datetime.now(UTC),
            policy_tier=policy_tier,
        )

        queue = self._tier_queues[policy_tier]
        try:
            queue.put_nowait(ref)
            self._enqueue_hot_total += 1
            self._metrics.buffer_enqueue_hot()
            self._metrics.buffer_queue_depth_inc()
            self._has_work.set()
            logger.debug(
                "Buffer enqueued (hot): request_id=%s tier=%s queue_depth=%d",
                request_id,
                policy_tier,
                self.queue_depth,
            )
            return True
        except asyncio.QueueFull:
            self._backpressure_total += 1
            self._metrics.buffer_backpressure()
            logger.warning(
                "Buffer tier=%s full (backpressure): request_id=%s will be recovered "
                "by scanner in ~%ds",
                policy_tier,
                request_id,
                self._config.scanner_interval_s,
            )
            return False

    # ------------------------------------------------------------------
    # Tier-aware dequeue with starvation guard
    # ------------------------------------------------------------------

    async def _dequeue(self) -> tuple[_MessageRef, bool]:
        """Wait for and return the next message reference to process.

        Applies the starvation guard:
        - Normally dequeues from the highest non-empty tier.
        - After max_consecutive_same_tier consecutive dequeues from the same
          tier, if a lower-priority tier is non-empty, forces one dequeue from
          the highest available lower tier (starvation_override=True).

        Returns a ``(ref, starvation_override)`` tuple.
        """
        while True:
            # Wait until at least one queue has work
            while self._all_queues_empty():
                self._has_work.clear()
                try:
                    await self._has_work.wait()
                except asyncio.CancelledError:
                    raise

            ref, starvation_override = self._try_dequeue_with_guard()
            if ref is not None:
                return ref, starvation_override

            # Edge case: another worker emptied a queue between our check and
            # our dequeue attempt. Loop back and wait again.

    def _try_dequeue_with_guard(self) -> tuple[_MessageRef | None, bool]:
        """Attempt a single tier-aware dequeue without blocking.

        Returns ``(ref, starvation_override)`` or ``(None, False)`` if all
        queues are empty at this moment.
        """
        state = self._starvation_state
        max_consec = self._config.max_consecutive_same_tier

        # Determine whether the starvation guard should force a lower tier
        force_lower = (
            state.current_tier is not None
            and state.consecutive_count >= max_consec
            and self._has_non_empty_lower_tier(state.current_tier)
        )

        if force_lower:
            # Dequeue from the highest non-empty tier *below* current_tier
            current_idx = POLICY_TIER_ORDER.index(state.current_tier)
            for tier in POLICY_TIER_ORDER[current_idx + 1 :]:
                q = self._tier_queues[tier]
                if not q.empty():
                    try:
                        ref = q.get_nowait()
                    except asyncio.QueueEmpty:
                        continue
                    # Reset counter — next selection re-evaluates from top
                    state.current_tier = tier
                    state.consecutive_count = 1
                    return ref, True
            # If we reach here, all lower tiers emptied concurrently — fall through
            force_lower = False

        if not force_lower:
            # Normal path: dequeue from highest non-empty tier
            for tier in POLICY_TIER_ORDER:
                q = self._tier_queues[tier]
                if not q.empty():
                    try:
                        ref = q.get_nowait()
                    except asyncio.QueueEmpty:
                        continue

                    if tier == state.current_tier:
                        state.consecutive_count += 1
                    else:
                        state.current_tier = tier
                        state.consecutive_count = 1
                    return ref, False

        return None, False

    def _all_queues_empty(self) -> bool:
        return all(q.empty() for q in self._tier_queues.values())

    def _has_non_empty_lower_tier(self, current_tier: str) -> bool:
        """Return True if any tier below current_tier has items."""
        current_idx = POLICY_TIER_ORDER.index(current_tier)
        return any(not self._tier_queues[t].empty() for t in POLICY_TIER_ORDER[current_idx + 1 :])

    async def _drain_all_queues(self) -> None:
        """Wait for all tier queues to drain (used during stop)."""
        await asyncio.gather(
            *(q.join() for q in self._tier_queues.values()),
        )

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _worker_loop(self, worker_id: int) -> None:
        """Drain the priority queues by calling process_fn for each message."""
        while True:
            try:
                ref, starvation_override = await self._dequeue()
            except asyncio.CancelledError:
                break

            try:
                process_latency_ms = (datetime.now(UTC) - ref.enqueued_at).total_seconds() * 1000

                logger.debug(
                    "Buffer worker %d processing request_id=%s tier=%s "
                    "starvation_override=%s queue_wait_ms=%.0f",
                    worker_id,
                    ref.request_id,
                    ref.policy_tier,
                    starvation_override,
                    process_latency_ms,
                )

                self._metrics.record_buffer_process_latency(process_latency_ms)
                self._metrics.buffer_queue_depth_dec()
                self._metrics.buffer_dequeue_by_tier(
                    ref.policy_tier,
                    starvation_override=starvation_override,
                )

                await self._process_fn(ref)

            except asyncio.CancelledError:
                # Mark queue tasks done then re-raise
                self._tier_queues[ref.policy_tier].task_done()
                raise
            except Exception:
                logger.exception(
                    "Buffer worker %d: processing failed for request_id=%s",
                    worker_id,
                    ref.request_id,
                )
            finally:
                try:
                    self._tier_queues[ref.policy_tier].task_done()
                except (ValueError, KeyError):
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
            if isinstance(request_context, str):
                request_context = json.loads(request_context)
            raw_payload = row["raw_payload"] or {}
            if isinstance(raw_payload, str):
                raw_payload = json.loads(raw_payload)
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

            # Recover policy_tier from stored context; fall back to default
            control = raw_payload.get("control", {})
            policy_tier = control.get("policy_tier", POLICY_TIER_DEFAULT)
            if policy_tier not in self._tier_queues:
                policy_tier = POLICY_TIER_DEFAULT

            request_id = str(request_context.get("request_id", str(row["id"])))

            ref = _MessageRef(
                request_id=request_id,
                message_inbox_id=row["id"],
                message_text=normalized_text,
                source=source,
                event=event_raw,
                sender=sender,
                enqueued_at=datetime.now(UTC),
                policy_tier=policy_tier,
            )

            tier_queue = self._tier_queues[policy_tier]
            try:
                # Non-blocking; if queue is full, skip and retry next sweep
                tier_queue.put_nowait(ref)
                self._enqueue_cold_total += 1
                self._scanner_recovered_total += 1
                recovered += 1
                self._metrics.buffer_enqueue_cold()
                self._metrics.buffer_scanner_recovered()
                self._metrics.buffer_queue_depth_inc()
                self._has_work.set()
                logger.info(
                    "Buffer scanner recovered request_id=%s tier=%s (received_at=%s)",
                    request_id,
                    policy_tier,
                    row["received_at"].isoformat(),
                )
            except asyncio.QueueFull:
                # Queue is full; this message will be retried next sweep
                logger.debug(
                    "Buffer scanner: tier=%s queue full, skipping request_id=%s (will retry)",
                    policy_tier,
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
        """Current total number of messages across all tier queues."""
        return sum(q.qsize() for q in self._tier_queues.values())

    @property
    def tier_depths(self) -> dict[str, int]:
        """Per-tier queue depths for health reporting."""
        return {tier: q.qsize() for tier, q in self._tier_queues.items()}

    @property
    def stats(self) -> dict[str, int]:
        """Snapshot of buffer counters for health reporting."""
        return {
            "queue_depth": self.queue_depth,
            "enqueue_hot_total": self._enqueue_hot_total,
            "enqueue_cold_total": self._enqueue_cold_total,
            "backpressure_total": self._backpressure_total,
            "scanner_recovered_total": self._scanner_recovered_total,
        }
