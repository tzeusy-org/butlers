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


def _server(daemon: ButlerDaemon) -> SimpleNamespace:
    """A stopped MCP server whose graceful timeout reports the deadline shutdown used."""
    config = SimpleNamespace(timeout_graceful_shutdown=30.0)
    daemon._server = SimpleNamespace(should_exit=False, config=config)
    daemon._server_task = asyncio.create_task(asyncio.sleep(0))
    return config


async def _slow_retire(_daemon) -> None:
    await asyncio.sleep(1.0)


async def test_the_deadline_is_taken_before_the_catalog_retires_when_a_share_is_set(
    tmp_path: Path,
) -> None:
    daemon = _daemon(tmp_path, share=10.0)
    config = _server(daemon)

    with patch("butlers.core.catalog_publication.close_catalog_publication", new=_slow_retire):
        await lifecycle._run_shutdown(daemon)

    # The real shutdown sets the server's timeout to what is left of the share: the 1s
    # retirement came out of it (about 9s), not a fresh 10s taken after it.
    assert config.timeout_graceful_shutdown == pytest.approx(9.0, abs=0.4)


async def test_without_a_share_the_deadline_is_still_taken_after_the_catalog_retires(
    tmp_path: Path,
) -> None:
    daemon = _daemon(tmp_path, share=None)
    config = _server(daemon)

    with patch("butlers.core.catalog_publication.close_catalog_publication", new=_slow_retire):
        await lifecycle._run_shutdown(daemon)

    # No share: no deadline reaches the server, which keeps its own timeout, as before.
    assert config.timeout_graceful_shutdown == 30.0


async def test_a_step_that_swallows_cancel_is_still_bounded_and_logged(
    tmp_path: Path, caplog
) -> None:
    """``asyncio.timeout`` cancels once, so a step that swallows the cancel (the calendar module
    does it three times) ran on unbounded and unlogged: 0.5s share, 3.5s taken, no log."""
    daemon = _daemon(tmp_path, share=0.5)
    swallowed = 0
    release = asyncio.Event()

    async def stubborn() -> None:
        nonlocal swallowed
        while not release.is_set():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                swallowed += 1

    started = time.monotonic()
    with caplog.at_level("WARNING", logger="butlers.lifecycle"):
        await lifecycle._within_share(
            daemon, asyncio.get_running_loop().time() + 0.5, stubborn(), "stubborn step"
        )

    # The share plus the floor to unwind, not however long the step would have taken.
    assert time.monotonic() - started < 2.5
    assert swallowed >= 1  # it was cancelled, and refused
    assert any("stubborn step" in r.getMessage() for r in caplog.records)
    assert any("ignored the cancel" in r.getMessage() for r in caplog.records)
    release.set()
    await asyncio.sleep(0.2)  # let the abandoned task end before the loop closes


async def test_a_step_that_unwinds_on_cancel_gets_its_cleanup_and_is_logged(
    tmp_path: Path, caplog
) -> None:
    daemon = _daemon(tmp_path, share=0.3)
    cleaned = asyncio.Event()

    async def tidy() -> None:
        try:
            await asyncio.sleep(60)
        finally:
            await asyncio.sleep(0.2)  # its own cleanup, which an inline cancel would also cut
            cleaned.set()

    with caplog.at_level("WARNING", logger="butlers.lifecycle"):
        await lifecycle._within_share(
            daemon, asyncio.get_running_loop().time() + 0.3, tidy(), "tidy step"
        )

    assert cleaned.is_set()
    assert any("cancelled it" in r.getMessage() for r in caplog.records)


async def test_a_step_that_fails_inside_its_share_still_raises(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path, share=5.0)

    async def broken() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await lifecycle._within_share(
            daemon, asyncio.get_running_loop().time() + 5.0, broken(), "broken step"
        )


async def test_the_mcp_servers_timeout_never_drops_below_the_floor(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path, share=7.0)
    config = SimpleNamespace(timeout_graceful_shutdown=30.0)
    daemon._server = SimpleNamespace(should_exit=False, config=config)
    daemon._server_task = asyncio.create_task(asyncio.sleep(0))
    spent = asyncio.get_running_loop().time() - 5.0  # catalog retirement used the share up

    await lifecycle._shutdown_resources(daemon, spent)

    assert config.timeout_graceful_shutdown == 1.0
