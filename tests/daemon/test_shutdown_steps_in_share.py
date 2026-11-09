"""Every shutdown step stays inside the daemon's share when a budget is set (bu-dhloi1.247).

``butlers up`` gives each daemon a share of the process budget (``_shutdown_budget_s``). The
drain steps and pool closes were bounded by it; the MCP server's graceful timeout, each module's
``on_shutdown``, the blob store close, the Switchboard client close, the approval-delivery worker
stop and the tick cancel grace were not, and the catalog retirement ran before the deadline was
taken. With no share set every one of them behaves as before.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers import lifecycle
from butlers.config import ButlerConfig
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


def _daemon(tmp_path: Path, share: float | None) -> ButlerDaemon:
    (tmp_path / "butler.toml").write_text(
        '[butler]\nname = "test-butler"\nport = 9100\n\n'
        '[butler.db]\nname = "butlers"\nschema = "test_butler"\n'
    )
    daemon = ButlerDaemon(tmp_path)
    daemon.config = ButlerConfig(name="test-butler", port=9100)
    daemon._shutdown_budget_s = share
    daemon.spawner = MagicMock(drain=AsyncMock())
    return daemon


async def _hang(*_args, **_kwargs) -> None:
    await asyncio.Event().wait()


def _module(name: str, on_shutdown) -> SimpleNamespace:
    return SimpleNamespace(name=name, on_shutdown=on_shutdown)


async def test_the_mcp_servers_graceful_timeout_is_the_remaining_share(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path, share=7.0)
    config = SimpleNamespace(timeout_graceful_shutdown=30.0)
    daemon._server = SimpleNamespace(should_exit=False, config=config)
    daemon._server_task = asyncio.create_task(asyncio.sleep(0))
    deadline = asyncio.get_running_loop().time() + 7.0

    await lifecycle._shutdown_resources(daemon, deadline)

    assert 6.0 < config.timeout_graceful_shutdown <= 7.0


async def test_the_mcp_servers_graceful_timeout_is_untouched_without_a_share(
    tmp_path: Path,
) -> None:
    daemon = _daemon(tmp_path, share=None)
    config = SimpleNamespace(timeout_graceful_shutdown=30.0)
    daemon._server = SimpleNamespace(should_exit=False, config=config)
    daemon._server_task = asyncio.create_task(asyncio.sleep(0))

    await lifecycle._shutdown_resources(daemon)

    assert config.timeout_graceful_shutdown == 30.0


async def test_a_module_that_will_not_shut_down_does_not_hold_the_rest(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path, share=1.5)
    later = AsyncMock()
    daemon._modules = [_module("later", later), _module("stuck", _hang)]  # reversed: stuck first
    started = time.monotonic()

    with patch(
        "butlers.core.approval_delivery_worker.stop_approval_delivery_worker", new=AsyncMock()
    ):
        await lifecycle._shutdown_chronicler_dependents(
            daemon, asyncio.get_running_loop().time() + 1.5
        )

    later.assert_awaited_once()
    assert time.monotonic() - started < 5


async def test_the_blob_store_the_client_and_the_delivery_worker_are_bounded(
    tmp_path: Path,
) -> None:
    daemon = _daemon(tmp_path, share=1.0)
    daemon.blob_store = MagicMock(close=_hang)
    daemon._modules = []
    started = time.monotonic()

    with patch("butlers.core.approval_delivery_worker.stop_approval_delivery_worker", new=_hang):
        await lifecycle._shutdown_chronicler_dependents(
            daemon, asyncio.get_running_loop().time() + 1.0
        )
    daemon._disconnect_switchboard = _hang  # type: ignore[method-assign]
    await lifecycle._shutdown_resources(daemon, asyncio.get_running_loop().time() + 1.0)

    assert time.monotonic() - started < 8


async def test_a_tick_that_ignores_cancel_is_left_after_the_remaining_share(
    tmp_path: Path,
) -> None:
    daemon = _daemon(tmp_path, share=2.0)

    done = False

    async def ignores_cancel() -> None:
        while not done:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue

    task = asyncio.create_task(ignores_cancel())
    await asyncio.sleep(0)  # let it reach its sleep, or the first cancel ends it unstarted
    daemon._scheduler_loop_task = task
    started = time.monotonic()

    await lifecycle._stop_scheduler_loop(daemon, asyncio.get_running_loop().time())

    # The fixed grace was 5s; with the share spent it is the 1s floor.
    assert time.monotonic() - started < 3
    done = True
    await task


async def test_the_deadline_is_taken_before_the_catalog_retires_when_a_share_is_set(
    tmp_path: Path,
) -> None:
    daemon = _daemon(tmp_path, share=10.0)
    seen: list[float] = []

    async def slow_retire(_daemon) -> None:
        await asyncio.sleep(1.0)

    async def spy(_daemon, deadline=None) -> None:
        seen.append(deadline)

    start = asyncio.get_running_loop().time()
    with (
        patch("butlers.core.catalog_publication.close_catalog_publication", new=slow_retire),
        patch.object(lifecycle, "_shutdown_resources", new=spy),
    ):
        await lifecycle._run_shutdown(daemon)

    assert seen[0] == pytest.approx(start + 10.0, abs=0.5)  # not start + 1s retire + 10s


async def test_without_a_share_the_deadline_is_still_taken_after_the_catalog_retires(
    tmp_path: Path,
) -> None:
    daemon = _daemon(tmp_path, share=None)
    seen: list[float | None] = []

    async def slow_retire(_daemon) -> None:
        await asyncio.sleep(1.0)

    async def spy(_daemon, deadline=None) -> None:
        seen.append(deadline)

    with (
        patch("butlers.core.catalog_publication.close_catalog_publication", new=slow_retire),
        patch.object(lifecycle, "_shutdown_resources", new=spy),
    ):
        await lifecycle._run_shutdown(daemon)

    assert seen == [None]  # _shutdown_resources takes it itself, as before
