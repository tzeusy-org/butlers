"""The Switchboard durable buffer is built from an injected processor, not a Spawner.

The buffer survives the RFC 0038 switch while the session Spawner does not, so its
construction must work with ``daemon.spawner = None`` and must never consult it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from butlers.config import BufferConfig
from butlers.core.buffer import DurableBuffer
from butlers.switchboard_wiring import build_switchboard_buffer

pytestmark = pytest.mark.unit


class _NoSpawnerDaemon:
    """Daemon stub whose Spawner is absent and whose every Spawner read is recorded."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(name="switchboard", buffer=BufferConfig(worker_count=1))
        self._buffer = None
        self.spawner_reads = 0

    @property
    def spawner(self) -> None:
        self.spawner_reads += 1
        return None


async def test_buffer_builds_starts_processes_and_stops_without_a_spawner() -> None:
    daemon = _NoSpawnerDaemon()
    processed: list[str] = []

    async def process_fn(ref) -> None:
        processed.append(ref.request_id)

    build_switchboard_buffer(daemon, None, process_fn)

    assert isinstance(daemon._buffer, DurableBuffer)
    await daemon._buffer.start()
    accepted = daemon._buffer.enqueue(
        request_id="req-1",
        message_inbox_id=None,
        message_text="hello",
        source={"channel": "telegram_bot"},
        event={},
        sender={"identity": "1"},
    )
    assert accepted is True
    await asyncio.wait_for(daemon._buffer._drain_all_queues(), timeout=2.0)
    await daemon._buffer.stop(drain_timeout_s=1.0)

    assert processed == ["req-1"]
    assert daemon.spawner_reads == 0
