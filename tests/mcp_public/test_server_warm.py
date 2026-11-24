"""bu-swkpoo.1: the server warms the host before its ports open, bounded and best-effort."""

from __future__ import annotations

import asyncio

import pytest

from butlers.mcp_public import server

pytestmark = pytest.mark.unit


class _Host:
    def __init__(self, delay: float = 0.0, error: BaseException | None = None):
        self.delay, self.error, self.calls = delay, error, 0

    async def warm(self) -> None:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error


async def test_warm_runs_the_host_warm_up_once():
    host = _Host()
    await server.warm(host)  # type: ignore[arg-type]
    assert host.calls == 1


async def test_a_slow_warm_up_is_cut_off_and_serving_starts(caplog):
    host = _Host(delay=5)
    with caplog.at_level("WARNING", logger="butlers.mcp_public.server"):
        await server.warm(host, limit_s=0.01)  # type: ignore[arg-type]
    assert any("timed out" in r.getMessage() for r in caplog.records)


def test_the_warm_up_bound_covers_one_probe():
    from butlers.capability_host.host import PROBE_TIMEOUT_S

    assert server.WARM_TIMEOUT_S > PROBE_TIMEOUT_S
