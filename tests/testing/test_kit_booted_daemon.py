"""The shared ``booted_daemon`` kit: a real daemon boot with no LLM (bu-rvgzmf.2 S7a)."""

from __future__ import annotations

import asyncio
import statistics
import time
import warnings
from pathlib import Path

import pytest

from butlers.testing.daemon import FakeRuntime

pytestmark = pytest.mark.integration

# Async fixtures run on the session loop (asyncio_default_fixture_loop_scope), so tests
# that use them must too.
session_loop = pytest.mark.asyncio(loop_scope="session")

_BOOTS = 10


def _write_butler(root: Path, name: str = "kit_smoke") -> Path:
    config_dir = root / name
    config_dir.mkdir()
    # One-db topology on a schema that has a runtime role, as production boots.
    (config_dir / "butler.toml").write_text(
        f'[butler]\nname = "{name}"\nport = 0\ndescription = "booted_daemon smoke"\n\n'
        '[butler.db]\nname = "butlers"\nschema = "general"\n'
    )
    return config_dir


def _live_tasks() -> set[asyncio.Task]:
    return {task for task in asyncio.all_tasks() if not task.done()}


@session_loop
async def test_booted_daemon_serves_a_tool_and_shuts_down_clean(booted_daemon, tmp_path) -> None:
    before = _live_tasks()
    booted = await booted_daemon(_write_butler(tmp_path))

    # The real spawner holds the fake adapter, so no LLM CLI can start.
    assert isinstance(booted.daemon.spawner._runtime, FakeRuntime)
    status = await booted.call_tool("status")
    assert status["name"] == "kit_smoke"
    assert status["health"] == "ok"

    await booted.shutdown()
    assert booted.daemon.db.pool is None
    # Let cancelled tasks finish unwinding before counting what is left.
    await asyncio.sleep(0)
    leaked = _live_tasks() - before - {asyncio.current_task()}
    assert not leaked, sorted(task.get_name() for task in leaked)


@session_loop
async def test_boot_cost_is_reported(booted_daemon, tmp_path) -> None:
    await (await booted_daemon(_write_butler(tmp_path, "warm"))).shutdown()
    cycles: list[float] = []
    starts: list[float] = []
    for index in range(_BOOTS):
        began = time.monotonic()
        booted = await booted_daemon(_write_butler(tmp_path, f"boot_{index}"))
        starts.append(booted.start_seconds)
        await booted.call_tool("status")
        await booted.shutdown()
        cycles.append(time.monotonic() - began)
    # junit properties are lost under xdist and passing output is captured; a warning
    # reaches the CI log's warnings summary. A cycle is clone + start + call + shutdown.
    warnings.warn(
        f"DAEMON-BOOT-SECONDS p50={statistics.median(cycles):.3f} "
        f"start_p50={statistics.median(starts):.3f} max={max(cycles):.3f} n={_BOOTS}",
        UserWarning,
        stacklevel=1,
    )
    assert statistics.median(cycles) < 30
