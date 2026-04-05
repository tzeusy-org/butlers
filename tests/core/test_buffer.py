"""Unit tests for DurableBuffer (butlers-963.4, butlers-dsa4.5).

Covers:
- Bounded per-tier queues: enqueue, workers drain, backpressure on full queue
- Tier-aware dequeue ordering: high_priority > interactive > default
- Within-tier FIFO preservation
- Starvation guard: forced lower-tier dequeue after max_consecutive_same_tier
- Worker pool: N workers drain concurrently, errors are caught
- Scanner: recovers 'accepted' rows, routes to correct tier queue
- Graceful shutdown: drain + cancel workers
- Integration: buffer wired into daemon's _wire_pipelines flow
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import BufferConfig
from butlers.core.buffer import (
    POLICY_TIER_DEFAULT,
    POLICY_TIER_HIGH_PRIORITY,
    POLICY_TIER_INTERACTIVE,
    DurableBuffer,
    _MessageRef,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    queue_capacity: int = 10,
    worker_count: int = 1,
    scanner_interval_s: int = 30,
    scanner_grace_s: int = 10,
    scanner_batch_size: int = 50,
    max_consecutive_same_tier: int = 10,
) -> BufferConfig:
    return BufferConfig(
        queue_capacity=queue_capacity,
        worker_count=worker_count,
        scanner_interval_s=scanner_interval_s,
        scanner_grace_s=scanner_grace_s,
        scanner_batch_size=scanner_batch_size,
        max_consecutive_same_tier=max_consecutive_same_tier,
    )


def _make_ref(
    request_id: str = "test-req-1",
    message_text: str = "hello",
    policy_tier: str = POLICY_TIER_DEFAULT,
) -> _MessageRef:
    return _MessageRef(
        request_id=request_id,
        message_inbox_id=request_id,
        message_text=message_text,
        source={"channel": "telegram", "endpoint_identity": "bot123"},
        event={"observed_at": "2026-02-18T12:00:00Z", "external_event_id": "evt1"},
        sender={"identity": "user42"},
        enqueued_at=datetime.now(UTC),
        policy_tier=policy_tier,
    )


def _enqueue(
    buf: DurableBuffer,
    request_id: str,
    policy_tier: str = POLICY_TIER_DEFAULT,
    message_text: str = "msg",
) -> bool:
    return buf.enqueue(
        request_id=request_id,
        message_inbox_id=request_id,
        message_text=message_text,
        source={},
        event={},
        sender={},
        policy_tier=policy_tier,
    )


# ---------------------------------------------------------------------------
# enqueue() — hot path
# ---------------------------------------------------------------------------


class TestEnqueue:
    """Tests for the synchronous enqueue() hot-path method."""

    async def test_enqueue_behavior(self) -> None:
        """enqueue() returns True on success, False when full; routes correctly; unknown tier → default."""
        process_fn = AsyncMock()

        # Success + counter
        buf = DurableBuffer(config=_make_config(queue_capacity=5), pool=None, process_fn=process_fn)
        assert buf.enqueue(
            request_id="r1", message_inbox_id="r1", message_text="hello",
            source={"channel": "telegram"}, event={}, sender={"identity": "u1"},
        ) is True
        assert buf.queue_depth == 1 and buf._enqueue_hot_total == 1

        # Multi-tier routing
        _enqueue(buf, "hp", POLICY_TIER_HIGH_PRIORITY)
        _enqueue(buf, "ia", POLICY_TIER_INTERACTIVE)
        assert buf.tier_depths[POLICY_TIER_HIGH_PRIORITY] == 1
        assert buf.tier_depths[POLICY_TIER_INTERACTIVE] == 1

        # Backpressure on full tier
        buf2 = DurableBuffer(config=_make_config(queue_capacity=1), pool=None, process_fn=process_fn)
        _enqueue(buf2, "r1", POLICY_TIER_DEFAULT)
        assert _enqueue(buf2, "r2", POLICY_TIER_DEFAULT) is False
        assert buf2._backpressure_total == 1

        # Unknown tier falls back to default
        buf3 = DurableBuffer(config=_make_config(), pool=None, process_fn=process_fn)
        result = buf3.enqueue(
            request_id="r1", message_inbox_id="r1", message_text="msg",
            source={}, event={}, sender={}, policy_tier="bogus_tier",
        )
        assert result is True and buf3.tier_depths[POLICY_TIER_DEFAULT] == 1


# ---------------------------------------------------------------------------
# Tier ordering
# ---------------------------------------------------------------------------


class TestTierOrdering:
    """Tests for the high_priority > interactive > default dequeue order."""

    async def test_tier_order_and_fifo(self) -> None:
        """Full ordering: high_priority → interactive → default; within-tier FIFO preserved."""
        order: list[str] = []

        async def process_fn(ref: _MessageRef) -> None:
            order.append(ref.policy_tier)

        buf = DurableBuffer(config=_make_config(worker_count=1), pool=None, process_fn=process_fn)
        _enqueue(buf, "df", POLICY_TIER_DEFAULT)
        _enqueue(buf, "hp", POLICY_TIER_HIGH_PRIORITY)
        _enqueue(buf, "ia", POLICY_TIER_INTERACTIVE)
        await buf.start()
        await asyncio.wait_for(buf._drain_all_queues(), timeout=2.0)
        await buf.stop(drain_timeout_s=1.0)
        assert order == [POLICY_TIER_HIGH_PRIORITY, POLICY_TIER_INTERACTIVE, POLICY_TIER_DEFAULT]

        # FIFO within same tier
        fifo_order: list[str] = []

        async def fifo_process_fn(ref: _MessageRef) -> None:
            fifo_order.append(ref.request_id)

        buf2 = DurableBuffer(config=_make_config(worker_count=1), pool=None, process_fn=fifo_process_fn)
        for i in range(4):
            _enqueue(buf2, f"hp-{i}", POLICY_TIER_HIGH_PRIORITY)
        await buf2.start()
        await asyncio.wait_for(buf2._drain_all_queues(), timeout=2.0)
        await buf2.stop(drain_timeout_s=1.0)
        assert fifo_order == ["hp-0", "hp-1", "hp-2", "hp-3"]


# ---------------------------------------------------------------------------
# Starvation guard
# ---------------------------------------------------------------------------


class TestStarvationGuard:
    """Tests for the starvation-prevention mechanism."""

    async def test_starvation_guard_forces_lower_tier_after_max_consecutive(self) -> None:
        """After max_consecutive_same_tier dequeues from high_priority, next comes from default."""
        order: list[tuple[str, str]] = []  # (tier, request_id)

        async def process_fn(ref: _MessageRef) -> None:
            order.append((ref.policy_tier, ref.request_id))

        # max_consecutive=2 so after 2 high_priority dequeues, 1 default is forced
        buf = DurableBuffer(
            config=_make_config(worker_count=1, max_consecutive_same_tier=2),
            pool=None,
            process_fn=process_fn,
        )

        # Queue 3 high_priority and 1 default
        _enqueue(buf, "hp-0", POLICY_TIER_HIGH_PRIORITY)
        _enqueue(buf, "hp-1", POLICY_TIER_HIGH_PRIORITY)
        _enqueue(buf, "hp-2", POLICY_TIER_HIGH_PRIORITY)
        _enqueue(buf, "df-0", POLICY_TIER_DEFAULT)

        await buf.start()
        await asyncio.wait_for(buf._drain_all_queues(), timeout=2.0)
        await buf.stop(drain_timeout_s=1.0)

        # First two: high_priority (consecutive = 2)
        # Third: forced to default (starvation guard)
        # Fourth: high_priority resumes (highest non-empty tier)
        assert order[0] == (POLICY_TIER_HIGH_PRIORITY, "hp-0")
        assert order[1] == (POLICY_TIER_HIGH_PRIORITY, "hp-1")
        assert order[2] == (POLICY_TIER_DEFAULT, "df-0")  # forced lower
        assert order[3] == (POLICY_TIER_HIGH_PRIORITY, "hp-2")

    async def test_starvation_guard_edge_cases(self) -> None:
        """Guard skips force when lower tiers empty; counter resets on natural tier change."""
        # Scenario A: max_consecutive=1 — guard triggers immediately, but only high_priority exists
        order_a: list[str] = []

        async def process_fn_a(ref: _MessageRef) -> None:
            order_a.append(ref.policy_tier)

        buf_a = DurableBuffer(
            config=_make_config(worker_count=1, max_consecutive_same_tier=1),
            pool=None,
            process_fn=process_fn_a,
        )
        for i in range(3):
            _enqueue(buf_a, f"hp-{i}", POLICY_TIER_HIGH_PRIORITY)

        await buf_a.start()
        await asyncio.wait_for(buf_a._drain_all_queues(), timeout=2.0)
        await buf_a.stop(drain_timeout_s=1.0)
        # All three should still be high_priority (no lower tier to force)
        assert order_a == [POLICY_TIER_HIGH_PRIORITY] * 3

        # Scenario B: counter resets when dequeued tier naturally changes
        order_b: list[str] = []

        async def process_fn_b(ref: _MessageRef) -> None:
            order_b.append(ref.policy_tier)

        buf_b = DurableBuffer(
            config=_make_config(worker_count=1, max_consecutive_same_tier=3),
            pool=None,
            process_fn=process_fn_b,
        )
        # high_priority drains first (naturally), then interactive (re-evaluates)
        _enqueue(buf_b, "hp-0", POLICY_TIER_HIGH_PRIORITY)
        _enqueue(buf_b, "ia-0", POLICY_TIER_INTERACTIVE)
        _enqueue(buf_b, "ia-1", POLICY_TIER_INTERACTIVE)

        await buf_b.start()
        await asyncio.wait_for(buf_b._drain_all_queues(), timeout=2.0)
        await buf_b.stop(drain_timeout_s=1.0)
        assert order_b == [
            POLICY_TIER_HIGH_PRIORITY,
            POLICY_TIER_INTERACTIVE,
            POLICY_TIER_INTERACTIVE,
        ]


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


class TestWorkers:
    """Tests for the worker coroutines that drain the queue."""

    async def test_single_worker_processes_message(self) -> None:
        """A worker drains a message from the queue and calls process_fn."""
        processed: list[_MessageRef] = []

        async def process_fn(ref: _MessageRef) -> None:
            processed.append(ref)

        buf = DurableBuffer(
            config=_make_config(worker_count=1),
            pool=None,
            process_fn=process_fn,
        )
        await buf.start()

        buf.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="hello",
            source={"channel": "telegram", "endpoint_identity": "bot123"},
            event={"observed_at": "2026-02-18T12:00:00Z"},
            sender={"identity": "u1"},
        )

        # Wait for the worker to process
        await asyncio.wait_for(buf._drain_all_queues(), timeout=2.0)
        await buf.stop(drain_timeout_s=1.0)

        assert len(processed) == 1
        assert processed[0].request_id == "r1"

    async def test_worker_handles_process_fn_exception(self) -> None:
        """Worker catches exceptions from process_fn and continues processing."""
        processed: list[str] = []

        async def process_fn(ref: _MessageRef) -> None:
            if ref.request_id == "bad":
                raise RuntimeError("boom")
            processed.append(ref.request_id)

        buf = DurableBuffer(
            config=_make_config(worker_count=1),
            pool=None,
            process_fn=process_fn,
        )
        await buf.start()

        buf.enqueue(
            request_id="bad",
            message_inbox_id="bad",
            message_text="x",
            source={},
            event={},
            sender={},
        )
        buf.enqueue(
            request_id="good",
            message_inbox_id="good",
            message_text="y",
            source={},
            event={},
            sender={},
        )

        await asyncio.wait_for(buf._drain_all_queues(), timeout=2.0)
        await buf.stop(drain_timeout_s=1.0)

        # "good" should have been processed despite "bad" failing
        assert processed == ["good"]

    async def test_multiple_workers_drain_concurrently(self) -> None:
        """Multiple workers process messages concurrently."""
        barrier = asyncio.Barrier(2)
        processed: list[str] = []

        async def process_fn(ref: _MessageRef) -> None:
            await barrier.wait()  # Both workers must reach this simultaneously
            processed.append(ref.request_id)

        buf = DurableBuffer(
            config=_make_config(worker_count=2, queue_capacity=10),
            pool=None,
            process_fn=process_fn,
        )
        await buf.start()

        buf.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="a",
            source={},
            event={},
            sender={},
        )
        buf.enqueue(
            request_id="r2",
            message_inbox_id="r2",
            message_text="b",
            source={},
            event={},
            sender={},
        )

        # Wait with a timeout — if workers were serial, both would deadlock at barrier
        await asyncio.wait_for(buf._drain_all_queues(), timeout=2.0)
        await buf.stop(drain_timeout_s=1.0)

        assert set(processed) == {"r1", "r2"}


# ---------------------------------------------------------------------------
# Backpressure
# ---------------------------------------------------------------------------


class TestBackpressure:
    """Tests for backpressure behavior when the queue is full."""

    async def test_backpressure_per_tier_independent(self) -> None:
        """Backpressure in one tier queue does not block other tiers; stats updated."""
        process_fn = AsyncMock()
        buf = DurableBuffer(config=_make_config(queue_capacity=1), pool=None, process_fn=process_fn)

        _enqueue(buf, "df-0", POLICY_TIER_DEFAULT)
        result_df = _enqueue(buf, "df-1", POLICY_TIER_DEFAULT)  # full → backpressure

        # high_priority tier still has capacity
        result_hp = _enqueue(buf, "hp-0", POLICY_TIER_HIGH_PRIORITY)

        assert result_df is False
        assert result_hp is True
        assert buf._backpressure_total == 1
        assert buf.stats["backpressure_total"] == 1
        assert buf.stats["enqueue_hot_total"] == 2


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class TestScanner:
    """Tests for the DB scanner cold-path sweep."""

    def _make_row(
        self,
        request_id: str,
        normalized_text: str,
        received_at: datetime | None = None,
        policy_tier: str = POLICY_TIER_DEFAULT,
    ) -> MagicMock:
        """Build a fake asyncpg record dict."""
        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "id": request_id,
            "received_at": received_at or datetime.now(UTC) - timedelta(minutes=5),
            "request_context": {"request_id": request_id},
            "raw_payload": {
                "source": {"channel": "telegram", "endpoint_identity": "bot"},
                "event": {"observed_at": "2026-02-18T12:00:00Z"},
                "sender": {"identity": "u1"},
                "control": {"policy_tier": policy_tier},
            },
            "normalized_text": normalized_text,
        }[key]
        return row

    async def test_scanner_sweep_recovers_stuck_messages(self) -> None:
        """_run_scanner_sweep() re-enqueues rows with lifecycle_state='accepted'."""
        recovered: list[str] = []

        async def process_fn(ref: _MessageRef) -> None:
            recovered.append(ref.request_id)

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        rows = [
            self._make_row("r1", "hello from scan"),
            self._make_row("r2", "another message"),
        ]
        mock_conn.fetch = AsyncMock(return_value=rows)

        buf = DurableBuffer(
            config=_make_config(queue_capacity=10),
            pool=mock_pool,
            process_fn=process_fn,
        )
        await buf.start()

        count = await buf._run_scanner_sweep()

        assert count == 2
        assert buf._scanner_recovered_total == 2
        assert buf._enqueue_cold_total == 2

        # Let workers drain
        await asyncio.wait_for(buf._drain_all_queues(), timeout=2.0)
        await buf.stop(drain_timeout_s=1.0)

        assert set(recovered) == {"r1", "r2"}

    async def test_scanner_routes_to_correct_tier_queue(self) -> None:
        """Scanner places recovered messages into the correct tier queue."""
        recovered_tiers: list[str] = []

        async def process_fn(ref: _MessageRef) -> None:
            recovered_tiers.append(ref.policy_tier)

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        rows = [
            self._make_row("hp-1", "urgent", policy_tier=POLICY_TIER_HIGH_PRIORITY),
            self._make_row("ia-1", "direct", policy_tier=POLICY_TIER_INTERACTIVE),
            self._make_row("df-1", "newsletter", policy_tier=POLICY_TIER_DEFAULT),
        ]
        mock_conn.fetch = AsyncMock(return_value=rows)

        buf = DurableBuffer(
            config=_make_config(queue_capacity=10, worker_count=1, max_consecutive_same_tier=10),
            pool=mock_pool,
            process_fn=process_fn,
        )
        await buf.start()

        count = await buf._run_scanner_sweep()
        assert count == 3

        await asyncio.wait_for(buf._drain_all_queues(), timeout=2.0)
        await buf.stop(drain_timeout_s=1.0)

        # Processing order should respect tiers
        assert recovered_tiers == [
            POLICY_TIER_HIGH_PRIORITY,
            POLICY_TIER_INTERACTIVE,
            POLICY_TIER_DEFAULT,
        ]

    async def test_scanner_sweep_skips_empty_normalized_text(self) -> None:
        """Rows with empty normalized_text are marked errored, not re-enqueued."""
        process_fn = AsyncMock()

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        empty_row = self._make_row("r1", "")
        mock_conn.fetch = AsyncMock(return_value=[empty_row])
        mock_conn.execute = AsyncMock()

        buf = DurableBuffer(
            config=_make_config(),
            pool=mock_pool,
            process_fn=process_fn,
        )

        count = await buf._run_scanner_sweep()

        assert count == 0
        # Should have called execute to mark it errored
        mock_conn.execute.assert_awaited_once()
        process_fn.assert_not_awaited()

    async def test_scanner_sweep_stops_on_full_queue(self) -> None:
        """Scanner stops adding to a tier queue when it hits QueueFull mid-sweep."""
        process_fn = AsyncMock()

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        rows = [self._make_row(f"r{i}", f"msg {i}") for i in range(5)]
        mock_conn.fetch = AsyncMock(return_value=rows)

        # Default tier queue only holds 2
        buf = DurableBuffer(
            config=_make_config(queue_capacity=2),
            pool=mock_pool,
            process_fn=process_fn,
        )

        count = await buf._run_scanner_sweep()

        # Should have recovered 2 (stopped when queue full)
        assert count == 2
        assert buf.tier_depths[POLICY_TIER_DEFAULT] == 2

    async def test_scanner_sweep_returns_zero_on_empty_db(self) -> None:
        """Scanner returns 0 when no stuck messages are found."""
        process_fn = AsyncMock()

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        mock_conn.fetch = AsyncMock(return_value=[])

        buf = DurableBuffer(
            config=_make_config(),
            pool=mock_pool,
            process_fn=process_fn,
        )

        count = await buf._run_scanner_sweep()

        assert count == 0
        assert buf._scanner_recovered_total == 0

    async def test_scanner_sweep_handles_db_error(self) -> None:
        """Scanner returns 0 and logs when DB query fails."""
        process_fn = AsyncMock()

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        mock_conn.fetch = AsyncMock(side_effect=RuntimeError("DB error"))

        buf = DurableBuffer(
            config=_make_config(),
            pool=mock_pool,
            process_fn=process_fn,
        )

        count = await buf._run_scanner_sweep()

        assert count == 0


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    """Tests for the start/stop lifecycle."""

    async def test_stop_lifecycle(self) -> None:
        """stop() cancels workers; drains queue; idempotent double calls; cancels scanner."""
        # Cancels workers
        buf = DurableBuffer(config=_make_config(worker_count=2), pool=None, process_fn=AsyncMock())
        await buf.start()
        assert len(buf._worker_tasks) == 2
        await buf.stop(drain_timeout_s=0.1)
        assert len(buf._worker_tasks) == 0

        # Drains queue before cancelling
        processed: list[str] = []

        async def process_fn2(ref: _MessageRef) -> None:
            processed.append(ref.request_id)

        buf2 = DurableBuffer(config=_make_config(worker_count=1), pool=None, process_fn=process_fn2)
        await buf2.start()
        for i in range(3):
            buf2.enqueue(request_id=f"r{i}", message_inbox_id=f"r{i}", message_text="msg", source={}, event={}, sender={})
        await buf2.stop(drain_timeout_s=2.0)
        assert len(processed) == 3

        # Double start: no extra workers
        buf3 = DurableBuffer(config=_make_config(worker_count=1), pool=None, process_fn=AsyncMock())
        await buf3.start()
        c1 = len(buf3._worker_tasks)
        await buf3.start()
        c2 = len(buf3._worker_tasks)
        await buf3.stop(drain_timeout_s=0.1)
        assert c1 == c2 == 1

        # Double stop: no raise
        buf4 = DurableBuffer(config=_make_config(), pool=None, process_fn=AsyncMock())
        await buf4.start()
        await buf4.stop(drain_timeout_s=0.1)
        await buf4.stop(drain_timeout_s=0.1)

        # Scanner cancelled by stop()
        mock_pool = MagicMock()
        buf5 = DurableBuffer(config=_make_config(scanner_interval_s=3600), pool=mock_pool, process_fn=AsyncMock())
        await buf5.start()
        assert buf5._scanner_task is not None and not buf5._scanner_task.done()
        await buf5.stop(drain_timeout_s=0.1)
        assert buf5._scanner_task is None


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class TestObservability:
    """Tests for stats, queue_depth, and tier_depths properties."""

    async def test_queue_depth_tier_depths_and_stats(self) -> None:
        """queue_depth, tier_depths, and stats all reflect correct values."""
        process_fn = AsyncMock()
        buf = DurableBuffer(config=_make_config(queue_capacity=5), pool=None, process_fn=process_fn)

        assert buf.queue_depth == 0
        stats = buf.stats
        assert set(stats.keys()) == {
            "queue_depth",
            "enqueue_hot_total",
            "enqueue_cold_total",
            "backpressure_total",
            "scanner_recovered_total",
        }
        for v in stats.values():
            assert v == 0

        _enqueue(buf, "hp-0", POLICY_TIER_HIGH_PRIORITY)
        _enqueue(buf, "ia-0", POLICY_TIER_INTERACTIVE)
        _enqueue(buf, "ia-1", POLICY_TIER_INTERACTIVE)

        assert buf.queue_depth == 3
        depths = buf.tier_depths
        assert depths[POLICY_TIER_HIGH_PRIORITY] == 1
        assert depths[POLICY_TIER_INTERACTIVE] == 2
        assert depths[POLICY_TIER_DEFAULT] == 0


# ---------------------------------------------------------------------------
# Daemon integration
# ---------------------------------------------------------------------------


class TestDaemonIntegration:
    """Verify buffer is wired into _wire_pipelines and shutdown."""

    def _make_butler_toml(self, tmp_path: Any) -> Any:
        """Write a minimal switchboard butler.toml."""
        from pathlib import Path

        butler_dir = Path(tmp_path)
        toml_content = "\n".join(
            [
                "[butler]",
                'name = "switchboard"',
                "port = 41100",
                "",
                "[butler.db]",
                'name = "butlers"',
                'schema = "switchboard"',
            ]
        )
        (butler_dir / "butler.toml").write_text(toml_content)
        return butler_dir

    def _patch_infra(self) -> dict:
        """Return patches for daemon infrastructure."""
        mock_pool = AsyncMock()
        mock_db = MagicMock()
        mock_db.provision = AsyncMock()
        mock_db.connect = AsyncMock(return_value=mock_pool)
        mock_db.close = AsyncMock()
        mock_db.pool = mock_pool
        mock_db.user = "postgres"
        mock_db.password = "postgres"
        mock_db.host = "localhost"
        mock_db.port = 5432
        mock_db.db_name = "butlers"

        mock_spawner = MagicMock()
        mock_spawner.stop_accepting = MagicMock()
        mock_spawner.drain = AsyncMock()
        mock_spawner.trigger = AsyncMock()

        # MockAdapter must accept **kwargs because daemon calls
        # adapter_cls(butler_name=..., log_root=...) for claude runtimes.
        class _MockAdapterInstance:
            binary_name = "claude"

        class _MockAdapterCls:
            binary_name = "claude"

            def __new__(cls, **kwargs: Any) -> _MockAdapterInstance:
                return _MockAdapterInstance()

        return {
            "db_from_env": patch("butlers.daemon.Database.from_env", return_value=mock_db),
            "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
            "validate_credentials": patch("butlers.daemon.validate_credentials"),
            "validate_module_credentials": patch(
                "butlers.daemon.validate_module_credentials_async",
                new_callable=AsyncMock,
                return_value={},
            ),
            "init_telemetry": patch("butlers.daemon.init_telemetry"),
            "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
            "FastMCP": patch("butlers.daemon.FastMCP"),
            "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
            "get_adapter": patch(
                "butlers.daemon.get_adapter",
                return_value=_MockAdapterCls,
            ),
            "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
            "start_mcp_server": patch(
                "butlers.daemon.ButlerDaemon._start_mcp_server", new_callable=AsyncMock
            ),
            "connect_switchboard": patch(
                "butlers.daemon.ButlerDaemon._connect_switchboard", new_callable=AsyncMock
            ),
            "mock_db": mock_db,
            "mock_pool": mock_pool,
            "mock_spawner": mock_spawner,
        }

    async def test_buffer_lifecycle_and_butler_scoping(self, tmp_path: Any) -> None:
        """Switchboard gets DurableBuffer; non-switchboard has None; shutdown clears buffer."""
        from pathlib import Path

        from butlers.core.buffer import DurableBuffer
        from butlers.daemon import ButlerDaemon

        # Switchboard: buffer created and cleared on shutdown
        Path(tmp_path / "sw").mkdir(exist_ok=True)
        butler_dir = self._make_butler_toml(tmp_path / "sw")
        patches = self._patch_infra()
        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()
        assert daemon._buffer is not None and isinstance(daemon._buffer, DurableBuffer)
        await daemon.shutdown()
        assert daemon._buffer is None

        # Non-switchboard: no buffer
        health_dir = Path(tmp_path / "health")
        health_dir.mkdir(exist_ok=True)
        (health_dir / "butler.toml").write_text(
            "[butler]\nname = \"health\"\nport = 41200\n[butler.db]\nname = \"butlers\"\nschema = \"health\"\n"
        )
        patches2 = self._patch_infra()
        with (
            patches2["db_from_env"],
            patches2["run_migrations"],
            patches2["validate_credentials"],
            patches2["validate_module_credentials"],
            patches2["init_telemetry"],
            patches2["sync_schedules"],
            patches2["FastMCP"],
            patches2["Spawner"],
            patches2["get_adapter"],
            patches2["shutil_which"],
            patches2["start_mcp_server"],
            patches2["connect_switchboard"],
        ):
            daemon2 = ButlerDaemon(health_dir)
            await daemon2.start()
        assert daemon2._buffer is None
        await daemon2.shutdown()
