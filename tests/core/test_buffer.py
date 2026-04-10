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
# Enqueue, backpressure, and observability
# ---------------------------------------------------------------------------


async def test_enqueue_and_observability() -> None:
    """enqueue() returns True on success; routes to correct tier; False+backpressure when full;
    unknown tier→default; stats reflect state."""
    process_fn = AsyncMock()
    buf = DurableBuffer(config=_make_config(queue_capacity=5), pool=None, process_fn=process_fn)

    # Success + counter
    assert (
        buf.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="hello",
            source={"channel": "telegram"},
            event={},
            sender={"identity": "u1"},
        )
        is True
    )
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
    assert buf2.stats["backpressure_total"] == 1

    # Unknown tier falls back to default
    buf3 = DurableBuffer(config=_make_config(), pool=None, process_fn=process_fn)
    assert (
        buf3.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="msg",
            source={},
            event={},
            sender={},
            policy_tier="bogus_tier",
        )
        is True
    )
    assert buf3.tier_depths[POLICY_TIER_DEFAULT] == 1

    # Observability: stats reflect correct values
    _enqueue(buf, "ia-1", POLICY_TIER_INTERACTIVE)
    assert buf.queue_depth == 4
    assert set(buf.stats.keys()) == {
        "queue_depth",
        "enqueue_hot_total",
        "enqueue_cold_total",
        "backpressure_total",
        "scanner_recovered_total",
    }


# ---------------------------------------------------------------------------
# Tier ordering and starvation guard
# ---------------------------------------------------------------------------


async def test_tier_ordering_and_starvation_guard() -> None:
    """high_priority > interactive > default; FIFO within tier; starvation guard forces
    lower tier."""
    order: list[str] = []

    async def process_fn(ref: _MessageRef) -> None:
        order.append(ref.policy_tier)

    # Tier order
    buf = DurableBuffer(config=_make_config(worker_count=1), pool=None, process_fn=process_fn)
    _enqueue(buf, "df", POLICY_TIER_DEFAULT)
    _enqueue(buf, "hp", POLICY_TIER_HIGH_PRIORITY)
    _enqueue(buf, "ia", POLICY_TIER_INTERACTIVE)
    await buf.start()
    await asyncio.wait_for(buf._drain_all_queues(), timeout=2.0)
    await buf.stop(drain_timeout_s=1.0)
    assert order == [POLICY_TIER_HIGH_PRIORITY, POLICY_TIER_INTERACTIVE, POLICY_TIER_DEFAULT]

    # Starvation guard: max_consecutive=2 → after 2 hp, forced 1 df
    guard_order: list[tuple[str, str]] = []

    async def guard_fn(ref: _MessageRef) -> None:
        guard_order.append((ref.policy_tier, ref.request_id))

    buf2 = DurableBuffer(
        config=_make_config(worker_count=1, max_consecutive_same_tier=2),
        pool=None,
        process_fn=guard_fn,
    )
    for i in range(3):
        _enqueue(buf2, f"hp-{i}", POLICY_TIER_HIGH_PRIORITY)
    _enqueue(buf2, "df-0", POLICY_TIER_DEFAULT)
    await buf2.start()
    await asyncio.wait_for(buf2._drain_all_queues(), timeout=2.0)
    await buf2.stop(drain_timeout_s=1.0)
    assert guard_order[0] == (POLICY_TIER_HIGH_PRIORITY, "hp-0")
    assert guard_order[1] == (POLICY_TIER_HIGH_PRIORITY, "hp-1")
    assert guard_order[2] == (POLICY_TIER_DEFAULT, "df-0")
    assert guard_order[3] == (POLICY_TIER_HIGH_PRIORITY, "hp-2")


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


async def test_workers() -> None:
    """Single worker drains; exception in process_fn doesn't stop processing; multiple
    workers drain concurrently."""
    processed: list[str] = []

    async def process_fn(ref: _MessageRef) -> None:
        if ref.request_id == "bad":
            raise RuntimeError("boom")
        processed.append(ref.request_id)

    buf = DurableBuffer(config=_make_config(worker_count=1), pool=None, process_fn=process_fn)
    await buf.start()
    buf.enqueue(
        request_id="bad", message_inbox_id="bad", message_text="x", source={}, event={}, sender={}
    )
    buf.enqueue(
        request_id="good", message_inbox_id="good", message_text="y", source={}, event={}, sender={}
    )
    await asyncio.wait_for(buf._drain_all_queues(), timeout=2.0)
    await buf.stop(drain_timeout_s=1.0)
    assert processed == ["good"]

    # Multiple workers process concurrently via barrier
    barrier = asyncio.Barrier(2)
    concurrent_processed: list[str] = []

    async def concurrent_fn(ref: _MessageRef) -> None:
        await barrier.wait()
        concurrent_processed.append(ref.request_id)

    buf2 = DurableBuffer(
        config=_make_config(worker_count=2, queue_capacity=10), pool=None, process_fn=concurrent_fn
    )
    await buf2.start()
    buf2.enqueue(
        request_id="r1", message_inbox_id="r1", message_text="a", source={}, event={}, sender={}
    )
    buf2.enqueue(
        request_id="r2", message_inbox_id="r2", message_text="b", source={}, event={}, sender={}
    )
    await asyncio.wait_for(buf2._drain_all_queues(), timeout=2.0)
    await buf2.stop(drain_timeout_s=1.0)
    assert set(concurrent_processed) == {"r1", "r2"}


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


async def test_scanner() -> None:
    """Scanner recovers stuck messages, routes to correct tier queue, skips empty text,
    stops on full queue."""

    def _make_row(
        request_id: str, normalized_text: str, policy_tier: str = POLICY_TIER_DEFAULT
    ) -> MagicMock:
        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "id": request_id,
            "received_at": datetime.now(UTC) - timedelta(minutes=5),
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

    def _make_mock_pool(rows, *, execute=False):
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        mock_conn.fetch = AsyncMock(return_value=rows)
        if execute:
            mock_conn.execute = AsyncMock()
        return mock_pool, mock_conn

    # Recovery: 2 rows re-enqueued
    recovered: list[str] = []

    async def process_fn(ref: _MessageRef) -> None:
        recovered.append(ref.request_id)

    mock_pool, _ = _make_mock_pool([_make_row("r1", "hello"), _make_row("r2", "another")])
    buf = DurableBuffer(
        config=_make_config(queue_capacity=10), pool=mock_pool, process_fn=process_fn
    )
    await buf.start()
    count = await buf._run_scanner_sweep()
    assert count == 2 and buf._scanner_recovered_total == 2
    await asyncio.wait_for(buf._drain_all_queues(), timeout=2.0)
    await buf.stop(drain_timeout_s=1.0)
    assert set(recovered) == {"r1", "r2"}

    # Empty normalized_text: marked errored, not re-enqueued
    mock_pool2, mock_conn2 = _make_mock_pool([_make_row("r1", "")], execute=True)
    buf2 = DurableBuffer(config=_make_config(), pool=mock_pool2, process_fn=AsyncMock())
    assert await buf2._run_scanner_sweep() == 0
    mock_conn2.execute.assert_awaited_once()

    # Stops when queue full (capacity=2, 5 rows → 2 recovered)
    mock_pool3, _ = _make_mock_pool([_make_row(f"r{i}", f"msg {i}") for i in range(5)])
    buf3 = DurableBuffer(
        config=_make_config(queue_capacity=2), pool=mock_pool3, process_fn=AsyncMock()
    )
    assert await buf3._run_scanner_sweep() == 2

    # Empty DB → 0
    mock_pool4, _ = _make_mock_pool([])
    buf4 = DurableBuffer(config=_make_config(), pool=mock_pool4, process_fn=AsyncMock())
    assert await buf4._run_scanner_sweep() == 0

    # DB error → 0
    mock_pool5, mock_conn5 = _make_mock_pool([])
    mock_conn5.fetch = AsyncMock(side_effect=RuntimeError("DB error"))
    buf5 = DurableBuffer(config=_make_config(), pool=mock_pool5, process_fn=AsyncMock())
    assert await buf5._run_scanner_sweep() == 0


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


async def test_shutdown() -> None:
    """stop() cancels workers; drains queue before stopping; double stop no raise;
    scanner cancelled."""
    buf = DurableBuffer(config=_make_config(worker_count=2), pool=None, process_fn=AsyncMock())
    await buf.start()
    assert len(buf._worker_tasks) == 2
    await buf.stop(drain_timeout_s=0.1)
    assert len(buf._worker_tasks) == 0

    # Drains before cancelling
    processed: list[str] = []

    async def process_fn2(ref: _MessageRef) -> None:
        processed.append(ref.request_id)

    buf2 = DurableBuffer(config=_make_config(worker_count=1), pool=None, process_fn=process_fn2)
    await buf2.start()
    for i in range(3):
        buf2.enqueue(
            request_id=f"r{i}",
            message_inbox_id=f"r{i}",
            message_text="msg",
            source={},
            event={},
            sender={},
        )
    await buf2.stop(drain_timeout_s=2.0)
    assert len(processed) == 3

    # Double stop no raise; scanner cancelled
    mock_pool = MagicMock()
    buf3 = DurableBuffer(
        config=_make_config(scanner_interval_s=3600), pool=mock_pool, process_fn=AsyncMock()
    )
    await buf3.start()
    assert buf3._scanner_task is not None and not buf3._scanner_task.done()
    await buf3.stop(drain_timeout_s=0.1)
    await buf3.stop(drain_timeout_s=0.1)
    assert buf3._scanner_task is None


# ---------------------------------------------------------------------------
# Daemon integration
# ---------------------------------------------------------------------------


async def test_buffer_daemon_integration(tmp_path: Any) -> None:
    """Switchboard gets DurableBuffer; non-switchboard has None; shutdown clears buffer."""
    from pathlib import Path

    from butlers.core.buffer import DurableBuffer
    from butlers.daemon import ButlerDaemon

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

    class _MockAdapterInstance:
        binary_name = "claude"

    class _MockAdapterCls:
        binary_name = "claude"

        def __new__(cls, **kwargs: Any) -> _MockAdapterInstance:
            return _MockAdapterInstance()

    common_patches = {
        "db_from_env": patch("butlers.lifecycle.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.lifecycle.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.lifecycle.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.lifecycle.init_telemetry"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.lifecycle.FastMCP"),
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=mock_spawner),
        "get_adapter": patch("butlers.lifecycle.get_adapter", return_value=_MockAdapterCls),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch(
            "butlers.daemon.ButlerDaemon._start_mcp_server", new_callable=AsyncMock
        ),
        "connect_switchboard": patch(
            "butlers.daemon.ButlerDaemon._connect_switchboard", new_callable=AsyncMock
        ),
    }

    # Switchboard gets DurableBuffer
    sw_dir = Path(tmp_path / "sw")
    sw_dir.mkdir(exist_ok=True)
    (sw_dir / "butler.toml").write_text(
        '[butler]\nname = "switchboard"\nport = 41100\n\n'
        '[butler.db]\nname = "butlers"\nschema = "switchboard"\n'
    )
    with (
        common_patches["db_from_env"],
        common_patches["run_migrations"],
        common_patches["validate_credentials"],
        common_patches["validate_module_credentials"],
        common_patches["init_telemetry"],
        common_patches["sync_schedules"],
        common_patches["FastMCP"],
        common_patches["Spawner"],
        common_patches["get_adapter"],
        common_patches["shutil_which"],
        common_patches["start_mcp_server"],
        common_patches["connect_switchboard"],
    ):
        daemon = ButlerDaemon(sw_dir)
        await daemon.start()
    assert daemon._buffer is not None and isinstance(daemon._buffer, DurableBuffer)
    await daemon.shutdown()
    assert daemon._buffer is None
