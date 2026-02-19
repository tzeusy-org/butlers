"""Unit tests for DurableBuffer (butlers-963.4).

Covers:
- Bounded in-memory queue: enqueue, workers drain, backpressure on full queue
- Worker pool: N workers drain concurrently, errors are caught
- Scanner: recovers 'accepted' rows older than grace period, skips recent rows
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
from butlers.core.buffer import DurableBuffer, _MessageRef

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
) -> BufferConfig:
    return BufferConfig(
        queue_capacity=queue_capacity,
        worker_count=worker_count,
        scanner_interval_s=scanner_interval_s,
        scanner_grace_s=scanner_grace_s,
        scanner_batch_size=scanner_batch_size,
    )


def _make_ref(request_id: str = "test-req-1", message_text: str = "hello") -> _MessageRef:
    return _MessageRef(
        request_id=request_id,
        message_inbox_id=request_id,
        message_text=message_text,
        source={"channel": "telegram", "endpoint_identity": "bot123"},
        event={"observed_at": "2026-02-18T12:00:00Z", "external_event_id": "evt1"},
        sender={"identity": "user42"},
        enqueued_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# enqueue() — hot path
# ---------------------------------------------------------------------------


class TestEnqueue:
    """Tests for the synchronous enqueue() hot-path method."""

    async def test_enqueue_returns_true_on_success(self) -> None:
        """enqueue() returns True when the queue has capacity."""
        process_fn = AsyncMock()
        buf = DurableBuffer(config=_make_config(), pool=None, process_fn=process_fn)

        enqueued = buf.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="hello",
            source={"channel": "telegram"},
            event={},
            sender={"identity": "u1"},
        )

        assert enqueued is True
        assert buf.queue_depth == 1
        assert buf._enqueue_hot_total == 1

    async def test_enqueue_returns_false_when_queue_full(self) -> None:
        """enqueue() returns False and increments backpressure counter on QueueFull."""
        process_fn = AsyncMock()
        buf = DurableBuffer(config=_make_config(queue_capacity=1), pool=None, process_fn=process_fn)

        # Fill the queue
        buf.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="first",
            source={},
            event={},
            sender={},
        )

        # Second enqueue should hit backpressure
        enqueued = buf.enqueue(
            request_id="r2",
            message_inbox_id="r2",
            message_text="second",
            source={},
            event={},
            sender={},
        )

        assert enqueued is False
        assert buf.queue_depth == 1  # Queue still holds only the first
        assert buf._backpressure_total == 1
        assert buf._enqueue_hot_total == 1

    async def test_enqueue_hot_counter_increments(self) -> None:
        """Each successful enqueue increments _enqueue_hot_total."""
        process_fn = AsyncMock()
        buf = DurableBuffer(config=_make_config(queue_capacity=5), pool=None, process_fn=process_fn)

        for i in range(3):
            buf.enqueue(
                request_id=f"r{i}",
                message_inbox_id=f"r{i}",
                message_text="msg",
                source={},
                event={},
                sender={},
            )

        assert buf._enqueue_hot_total == 3


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
        await buf._queue.join()
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

        await buf._queue.join()
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
        await asyncio.wait_for(buf._queue.join(), timeout=2.0)
        await buf.stop(drain_timeout_s=1.0)

        assert set(processed) == {"r1", "r2"}


# ---------------------------------------------------------------------------
# Backpressure
# ---------------------------------------------------------------------------


class TestBackpressure:
    """Tests for backpressure behavior when the queue is full."""

    async def test_backpressure_does_not_raise(self) -> None:
        """enqueue() on a full queue returns False without raising."""
        process_fn = AsyncMock()
        buf = DurableBuffer(config=_make_config(queue_capacity=2), pool=None, process_fn=process_fn)

        buf.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="x",
            source={},
            event={},
            sender={},
        )
        buf.enqueue(
            request_id="r2",
            message_inbox_id="r2",
            message_text="y",
            source={},
            event={},
            sender={},
        )
        result = buf.enqueue(
            request_id="r3",
            message_inbox_id="r3",
            message_text="z",
            source={},
            event={},
            sender={},
        )

        assert result is False
        assert buf._backpressure_total == 1
        # Queue still only has 2 messages
        assert buf.queue_depth == 2

    async def test_stats_reflects_backpressure_count(self) -> None:
        """stats property includes backpressure_total."""
        process_fn = AsyncMock()
        buf = DurableBuffer(config=_make_config(queue_capacity=1), pool=None, process_fn=process_fn)

        buf.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="x",
            source={},
            event={},
            sender={},
        )
        buf.enqueue(
            request_id="r2",
            message_inbox_id="r2",
            message_text="y",
            source={},
            event={},
            sender={},
        )

        stats = buf.stats
        assert stats["backpressure_total"] == 1
        assert stats["enqueue_hot_total"] == 1


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
        await buf._queue.join()
        await buf.stop(drain_timeout_s=1.0)

        assert set(recovered) == {"r1", "r2"}

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
        """Scanner stops adding to queue when it hits QueueFull mid-sweep."""
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

        # Queue only holds 2
        buf = DurableBuffer(
            config=_make_config(queue_capacity=2),
            pool=mock_pool,
            process_fn=process_fn,
        )

        count = await buf._run_scanner_sweep()

        # Should have recovered 2 (stopped when queue full)
        assert count == 2
        assert buf.queue_depth == 2

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

    async def test_stop_cancels_workers(self) -> None:
        """stop() cancels worker tasks."""
        process_fn = AsyncMock()
        buf = DurableBuffer(config=_make_config(worker_count=2), pool=None, process_fn=process_fn)
        await buf.start()

        assert len(buf._worker_tasks) == 2

        await buf.stop(drain_timeout_s=0.1)

        assert len(buf._worker_tasks) == 0

    async def test_stop_drains_queue_before_cancel(self) -> None:
        """stop() drains remaining queue items before cancelling workers."""
        processed: list[str] = []

        async def process_fn(ref: _MessageRef) -> None:
            processed.append(ref.request_id)

        buf = DurableBuffer(config=_make_config(worker_count=1), pool=None, process_fn=process_fn)
        await buf.start()

        for i in range(3):
            buf.enqueue(
                request_id=f"r{i}",
                message_inbox_id=f"r{i}",
                message_text="msg",
                source={},
                event={},
                sender={},
            )

        await buf.stop(drain_timeout_s=2.0)

        assert len(processed) == 3

    async def test_double_start_is_idempotent(self) -> None:
        """Calling start() twice does not create extra worker tasks."""
        process_fn = AsyncMock()
        buf = DurableBuffer(config=_make_config(worker_count=1), pool=None, process_fn=process_fn)

        await buf.start()
        task_count_after_first = len(buf._worker_tasks)

        await buf.start()  # Second call — should be a no-op
        task_count_after_second = len(buf._worker_tasks)

        await buf.stop(drain_timeout_s=0.1)

        assert task_count_after_first == task_count_after_second == 1

    async def test_double_stop_is_idempotent(self) -> None:
        """Calling stop() twice does not raise."""
        process_fn = AsyncMock()
        buf = DurableBuffer(config=_make_config(), pool=None, process_fn=process_fn)
        await buf.start()
        await buf.stop(drain_timeout_s=0.1)
        await buf.stop(drain_timeout_s=0.1)  # Should not raise

    async def test_stop_cancels_scanner(self) -> None:
        """stop() cancels the scanner task when pool is provided."""
        process_fn = AsyncMock()
        mock_pool = MagicMock()
        buf = DurableBuffer(
            config=_make_config(scanner_interval_s=3600),  # Very long interval
            pool=mock_pool,
            process_fn=process_fn,
        )
        await buf.start()

        assert buf._scanner_task is not None
        assert not buf._scanner_task.done()

        await buf.stop(drain_timeout_s=0.1)

        assert buf._scanner_task is None


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class TestObservability:
    """Tests for stats and queue_depth properties."""

    async def test_queue_depth_reflects_current_depth(self) -> None:
        """queue_depth returns number of items currently in the queue."""
        process_fn = AsyncMock()
        buf = DurableBuffer(config=_make_config(queue_capacity=5), pool=None, process_fn=process_fn)

        assert buf.queue_depth == 0

        buf.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="x",
            source={},
            event={},
            sender={},
        )
        assert buf.queue_depth == 1

        buf.enqueue(
            request_id="r2",
            message_inbox_id="r2",
            message_text="y",
            source={},
            event={},
            sender={},
        )
        assert buf.queue_depth == 2

    async def test_stats_returns_all_counters(self) -> None:
        """stats property includes all observable counters."""
        process_fn = AsyncMock()
        buf = DurableBuffer(config=_make_config(), pool=None, process_fn=process_fn)

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
                "port = 40100",
                "",
                "[butler.db]",
                'name = "butler_switchboard"',
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
        mock_db.db_name = "butler_switchboard"

        mock_spawner = MagicMock()
        mock_spawner.stop_accepting = MagicMock()
        mock_spawner.drain = AsyncMock()
        mock_spawner.trigger = AsyncMock()

        # MockAdapter must accept **kwargs because daemon calls
        # adapter_cls(butler_name=..., log_root=...) for claude-code runtimes.
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
                "butlers.daemon.validate_module_credentials", return_value={}
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

    async def test_buffer_is_created_for_switchboard(self, tmp_path: Any) -> None:
        """_wire_pipelines creates _buffer for the switchboard butler."""
        from butlers.core.buffer import DurableBuffer
        from butlers.daemon import ButlerDaemon

        butler_dir = self._make_butler_toml(tmp_path)
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

        assert daemon._buffer is not None
        assert isinstance(daemon._buffer, DurableBuffer)

        # Cleanup
        await daemon.shutdown()

    async def test_buffer_is_none_for_non_switchboard(self, tmp_path: Any) -> None:
        """Non-switchboard butlers should have _buffer = None."""
        from pathlib import Path

        from butlers.daemon import ButlerDaemon

        butler_dir = Path(tmp_path)
        (butler_dir / "butler.toml").write_text(
            "\n".join(
                [
                    "[butler]",
                    'name = "health"',
                    "port = 40200",
                    "[butler.db]",
                    'name = "butler_health"',
                ]
            )
        )

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

        assert daemon._buffer is None

        await daemon.shutdown()

    async def test_shutdown_stops_buffer(self, tmp_path: Any) -> None:
        """daemon.shutdown() calls buffer.stop() and sets _buffer to None."""
        from butlers.daemon import ButlerDaemon

        butler_dir = self._make_butler_toml(tmp_path)
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

        assert daemon._buffer is not None

        await daemon.shutdown()

        assert daemon._buffer is None
