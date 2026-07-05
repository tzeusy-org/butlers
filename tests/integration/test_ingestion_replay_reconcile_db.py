"""Real-Postgres integration tests for email replay reconciliation (bu-nqkha).

bu-g4oiu's live investigation found 2 email `public.ingestion_events` rows
stuck in `status='replay_pending'` even though the corresponding
`switchboard.message_inbox` row had already reached a terminal lifecycle
state (`parsed`) with butler sessions created — i.e. the re-process fully
succeeded but the reconciliation write back to `ingestion_events` never
landed. The hypothesis was that email bypasses the DurableBuffer
`_buffer_process` reconciliation wrapper other channels route through.

Code-level tracing (switchboard_wiring.py, core_tools/_switchboard.py,
core/buffer.py) found no such channel-based bypass: both the hot path
(`ingest()` -> `DurableBuffer.enqueue()`) and the cold path (the buffer's
30s `message_inbox` scanner) funnel every channel, including email, through
the same `_buffer_process` wrapper, which unconditionally reconciles
`ingestion_events` after `pipeline.process()` returns.

The concrete, reproducible gap is instead in that wrapper's exception
handling: `DurableBuffer.stop()` (invoked on every graceful daemon
shutdown/restart) drains in-flight queue items for only a fixed grace period
(`drain_timeout_s`, default 10s) before force-`cancel()`-ing any worker task
still running. A bare `except Exception` does not catch
`asyncio.CancelledError` (a `BaseException` subclass in Python 3.8+), so a
worker whose message had *already* finished processing (session created,
`message_inbox` reached a terminal lifecycle state) but was cancelled while
awaiting the reconciliation write would permanently strand the
`ingestion_events` row — exactly the symptom bu-g4oiu observed. Slower,
multi-target dispatches (an email digest routed to several butlers,
producing several sessions) are disproportionately likely to still be
mid-flight when a shutdown's drain window expires, which is why email was
the channel where this was actually caught.

This module proves the fix — `ingestion_event_reconcile_after_processing`
(the new single shared call site both reconciliation paths now use) —
against a real, migrated `public.ingestion_events` table:

1. A replay-pending email event reconciles back to `ingested` on success.
2. A failed replay transitions to `replay_failed` with the error detail.
3. The reconciliation write survives cancellation of the calling task
   (the actual bu-nqkha bug) — this specific assertion needs no real
   Postgres semantics (it is pure asyncio), but is included here so the
   whole reconciliation contract for an email-sourced event is proven
   end-to-end in one place, against the real schema, in the same run as the
   mocked-pool version in tests/core/test_ingestion_events.py.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.core.ingestion_events import ingestion_event_reconcile_after_processing
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the core chain — public.ingestion_events."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE public.ingestion_events CASCADE")
    yield p
    await p.close()


async def _seed_email_event(
    pool: asyncpg.Pool,
    *,
    status: str,
) -> uuid.UUID:
    """Insert a `source_channel='email'` ingestion_events row in `status`."""
    event_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.ingestion_events (
            id, received_at, source_channel, source_provider,
            source_endpoint_identity, source_sender_identity, external_event_id,
            dedupe_key, dedupe_strategy, ingestion_tier, policy_tier,
            triage_decision, triage_target, status
        ) VALUES ($1, $2, 'email', 'gmail', 'inbox@example.com',
                  'alice@example.com', $3, $4, 'connector_api', 'full',
                  'default', 'route', 'finance', $5)
        """,
        event_id,
        datetime.now(UTC),
        f"<{event_id}@example.com>",
        f"dedupe-{event_id}",
        status,
    )
    return event_id


async def test_email_replay_reconciles_to_ingested_on_success(pool: asyncpg.Pool) -> None:
    """A successful email re-process flips replay_pending -> ingested.

    This is the exact transition bu-g4oiu found missing: DurableBuffer's
    worker (or its create_task fallback) must be able to call this on an
    email-sourced row and see it land, using the real CHECK constraint and
    schema — not a mocked pool that can't catch a rejected status value.
    """
    event_id = await _seed_email_event(pool, status="replay_pending")

    result = await ingestion_event_reconcile_after_processing(pool, event_id, routing_failed=False)
    assert result is True

    row = await pool.fetchrow(
        "SELECT status, error_detail FROM public.ingestion_events WHERE id = $1", event_id
    )
    assert row["status"] == "ingested"
    assert row["error_detail"] is None


async def test_email_replay_failure_marks_replay_failed(pool: asyncpg.Pool) -> None:
    """A failed re-process transitions replay_pending -> replay_failed with detail.

    Exercises the real CHECK constraint (core_057) that widened the allowed
    status values to include 'replay_failed' — a mocked pool cannot verify
    the constraint accepts it.
    """
    event_id = await _seed_email_event(pool, status="replay_pending")

    result = await ingestion_event_reconcile_after_processing(
        pool,
        event_id,
        routing_failed=True,
        error_detail="failed_targets: ['general']",
    )
    assert result is True

    row = await pool.fetchrow(
        "SELECT status, error_detail FROM public.ingestion_events WHERE id = $1", event_id
    )
    assert row["status"] == "replay_failed"
    assert row["error_detail"] == "failed_targets: ['general']"


async def test_email_reconcile_is_noop_for_non_replay_pending_rows(pool: asyncpg.Pool) -> None:
    """mark_replay_complete only matches replay_pending — a plain 'ingested'
    row (the common case: an email that never needed replay) must be left
    untouched, proving the shared reconciliation call is safe to invoke
    unconditionally after every successful routing, replayed or not.
    """
    event_id = await _seed_email_event(pool, status="ingested")

    result = await ingestion_event_reconcile_after_processing(pool, event_id, routing_failed=False)
    assert result is False

    row = await pool.fetchrow("SELECT status FROM public.ingestion_events WHERE id = $1", event_id)
    assert row["status"] == "ingested"


class _DelayedExecutePool:
    """Thin proxy adding a fixed delay before delegating ``execute`` to a real pool.

    ``ingestion_event_mark_replay_complete``/``_mark_failed`` only ever call
    ``pool.execute``, so this is the sole method that needs proxying. The
    delay makes the cancellation race deterministic: without it, a real
    asyncpg round-trip against a local test container can complete faster
    than the test can reliably call ``task.cancel()`` mid-flight, which would
    make the assertion true for the wrong reason (the write had already
    finished, not because shielding saved it).
    """

    def __init__(self, real_pool: asyncpg.Pool, delay_s: float = 0.1) -> None:
        self._real_pool = real_pool
        self._delay_s = delay_s

    async def execute(self, sql, *args):
        await asyncio.sleep(self._delay_s)
        return await self._real_pool.execute(sql, *args)


async def test_email_reconcile_write_survives_caller_cancellation(pool: asyncpg.Pool) -> None:
    """The bu-nqkha bug, proven against a real connection pool.

    Cancelling the task awaiting ingestion_event_reconcile_after_processing
    (as DurableBuffer.stop() does to any worker still running once its
    shutdown drain grace period elapses) must not abort the UPDATE — it must
    still land against the real database shortly after, even though the
    cancellation itself propagates to the caller immediately.
    """
    event_id = await _seed_email_event(pool, status="replay_pending")
    delayed_pool = _DelayedExecutePool(pool, delay_s=0.1)

    task = asyncio.ensure_future(
        ingestion_event_reconcile_after_processing(delayed_pool, event_id, routing_failed=False)
    )
    # Let the task start and enter the shielded write (inside the artificial
    # delay, before the real execute() has even been issued).
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Cancellation must propagate to the caller promptly, without waiting for
    # the shielded write to land.
    row = await pool.fetchrow("SELECT status FROM public.ingestion_events WHERE id = $1", event_id)
    assert row["status"] == "replay_pending"

    # ...but the write itself must still complete shortly after, against the
    # real database, unblocked by the caller's cancellation.
    for _ in range(50):
        row = await pool.fetchrow(
            "SELECT status FROM public.ingestion_events WHERE id = $1", event_id
        )
        if row["status"] == "ingested":
            break
        await asyncio.sleep(0.05)

    assert row["status"] == "ingested", (
        "shielded reconciliation write should still land against the real "
        "database despite the caller being cancelled — this is the bu-nqkha fix"
    )
