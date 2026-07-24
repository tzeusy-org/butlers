"""Wiring/inventory coverage for the dashboard app lifespan (bu-27dxl.6.5).

Complements test_lifespan_supervisor.py (which proves the supervisor helper's
restart/backoff/cancellation semantics in isolation) by driving the *real*
``butlers.api.app.lifespan`` context manager end-to-end -- with only DB/MCP/
pricing dependencies mocked out -- to prove:

1. All nine lifespan background loops are wired through
   ``supervise_lifespan_loop`` exactly once (the lifespan-inventory test).
2. Shutdown cancels every one of the nine tasks, including the calendar-sync
   deadman -- which was previously *omitted* from the shutdown cancellation
   block even though it was created at startup (a real bug this bead fixes;
   see the regression assertion below).
3. None of the nine loops restart as a result of shutdown cancellation.
4. The external-deadman loop is present only when EXTERNAL_DEADMAN_URL is
   configured (existing conditional-start behavior, preserved).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from butlers.api import app as api_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.asyncio

# The nine lifespan loops named in the bead design, independent of
# EXTERNAL_DEADMAN_URL configuration.
_ALWAYS_ON_LOOP_NAMES = {
    "secrets_lifecycle",
    "model_verify",
    "fleet_events_bridge",
    "settings_console_delta",
    "secrets_staleness",
    "migration_drift",
    "calendar_sync_deadman",
    "restore_drill",
}
_EXTERNAL_DEADMAN_LOOP_NAME = "external_deadman"

# The eight ``run_*_loop`` functions imported at module scope into
# ``butlers.api.app`` -- patched directly there. ``run_fleet_events_listener``
# is imported locally inside the lifespan function body, so it is patched at
# its source module instead (see ``_install_common_mocks``).
_MODULE_LEVEL_LOOP_FUNCS = [
    "run_secrets_lifecycle_loop",
    "run_model_verify_loop",
    "run_settings_console_delta_loop",
    "run_secrets_staleness_loop",
    "run_migration_drift_loop",
    "run_calendar_sync_deadman_loop",
    "run_external_deadman_loop",
    "run_restore_drill_loop",
]


def _install_common_mocks(monkeypatch, *, call_counts: dict) -> None:
    """Patch every lifespan startup dependency (DB pools, MCP manager,
    pricing, credential store, CLI-auth token restore) so ``lifespan(app)``
    can run end-to-end against fakes rather than a real Postgres/roster/MCP
    stack -- only the task-supervision wiring under test is real.

    Each of the nine ``run_*_loop`` functions is replaced with a coroutine
    that increments ``call_counts[<func_name>]`` and then blocks forever on
    an ``asyncio.Event`` -- i.e. it behaves like the real loops' steady
    state (never returns, never raises) so lifespan startup completes
    normally and the task set stays stable until the test cancels it.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = MagicMock()

    monkeypatch.setattr(api_app, "init_dependencies", MagicMock(return_value=(MagicMock(), [])))
    monkeypatch.setattr(api_app, "check_infra_default_creds", MagicMock())
    monkeypatch.setattr(api_app, "init_pricing", MagicMock())
    monkeypatch.setattr(api_app, "get_butler_configs", MagicMock(return_value=[]))
    monkeypatch.setattr(api_app, "init_db_manager", AsyncMock(return_value=mock_db))
    monkeypatch.setattr(api_app, "get_db_manager", MagicMock(return_value=mock_db))
    monkeypatch.setattr(api_app, "wire_db_dependencies", MagicMock())
    monkeypatch.setattr(api_app, "get_mcp_manager", MagicMock())
    monkeypatch.setattr(api_app, "get_pricing", MagicMock())
    monkeypatch.setattr(api_app, "resolve_staleness_window_s", MagicMock(return_value=3600.0))
    monkeypatch.setattr(api_app, "shutdown_db_manager", AsyncMock())
    monkeypatch.setattr(api_app, "shutdown_dependencies", AsyncMock())

    # Avoid the real CredentialStore hitting a mocked, non-async-context-manager
    # pool object -- it's caught by app.py's own try/except either way, but
    # stubbing it directly keeps this test's failure surface to task wiring.
    fake_store = MagicMock()
    fake_store.resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(api_app, "CredentialStore", MagicMock(return_value=fake_store))

    import butlers.cli_auth.persistence as persistence_mod

    monkeypatch.setattr(persistence_mod, "restore_tokens", AsyncMock(return_value={}))

    def _make_forever(func_name: str):
        async def _forever(*_args, **_kwargs) -> None:
            call_counts[func_name] = call_counts.get(func_name, 0) + 1
            await asyncio.Event().wait()

        return _forever

    for func_name in _MODULE_LEVEL_LOOP_FUNCS:
        monkeypatch.setattr(api_app, func_name, _make_forever(func_name))

    import butlers.api.fleet_events_bridge as bridge_mod

    monkeypatch.setattr(
        bridge_mod, "run_fleet_events_listener", _make_forever("run_fleet_events_listener")
    )


def _spy_on_supervisor(monkeypatch) -> list[tuple[str, asyncio.Task]]:
    """Wrap the real ``supervise_lifespan_loop`` so every call app.py makes
    is recorded as (name, task) -- the ground truth for "wired through the
    supervisor exactly once", independent of internal bookkeeping like
    ``_BACKGROUND_TASKS``.
    """
    from butlers.api.lifespan_supervisor import supervise_lifespan_loop as real_supervise

    calls: list[tuple[str, asyncio.Task]] = []

    def _spy(name, coro_factory, **kwargs):
        task = real_supervise(name, coro_factory, **kwargs)
        calls.append((name, task))
        return task

    monkeypatch.setattr(api_app, "supervise_lifespan_loop", _spy)
    return calls


async def test_all_nine_loops_wired_through_supervisor_exactly_once(monkeypatch):
    monkeypatch.delenv(api_app.EXTERNAL_DEADMAN_URL_ENV, raising=False)
    call_counts: dict[str, int] = {}
    _install_common_mocks(monkeypatch, call_counts=call_counts)
    calls = _spy_on_supervisor(monkeypatch)

    app = FastAPI()
    async with api_app.lifespan(app):
        names = [name for name, _ in calls]
        # Exactly once each -- no double-wrapping, no missing loop.
        assert sorted(names) == sorted(_ALWAYS_ON_LOOP_NAMES)
        assert len(names) == len(set(names))

        tasks_by_name = dict(calls)
        for name, task in tasks_by_name.items():
            assert not task.done(), f"{name} task must be running, not finished, at steady state"


async def test_external_deadman_wired_only_when_url_configured(monkeypatch):
    monkeypatch.setenv(api_app.EXTERNAL_DEADMAN_URL_ENV, "https://deadman.example.test/ping")
    call_counts: dict[str, int] = {}
    _install_common_mocks(monkeypatch, call_counts=call_counts)
    calls = _spy_on_supervisor(monkeypatch)

    app = FastAPI()
    async with api_app.lifespan(app):
        names = {name for name, _ in calls}
        assert names == _ALWAYS_ON_LOOP_NAMES | {_EXTERNAL_DEADMAN_LOOP_NAME}


async def test_external_deadman_absent_when_url_not_configured(monkeypatch):
    monkeypatch.delenv(api_app.EXTERNAL_DEADMAN_URL_ENV, raising=False)
    call_counts: dict[str, int] = {}
    _install_common_mocks(monkeypatch, call_counts=call_counts)
    calls = _spy_on_supervisor(monkeypatch)

    app = FastAPI()
    async with api_app.lifespan(app):
        names = {name for name, _ in calls}
        assert _EXTERNAL_DEADMAN_LOOP_NAME not in names
        assert names == _ALWAYS_ON_LOOP_NAMES


async def test_shutdown_cancels_all_nine_loops_including_calendar_deadman(monkeypatch, caplog):
    """Regression coverage for the bug this bead fixes: calendar_deadman_task
    was created at startup but never referenced in the shutdown cancellation
    block, so it kept running (or leaked) past app shutdown while every
    sibling loop was cleanly cancelled.
    """
    monkeypatch.setenv(api_app.EXTERNAL_DEADMAN_URL_ENV, "https://deadman.example.test/ping")
    call_counts: dict[str, int] = {}
    _install_common_mocks(monkeypatch, call_counts=call_counts)
    calls = _spy_on_supervisor(monkeypatch)

    app = FastAPI()
    with caplog.at_level("INFO"):
        async with api_app.lifespan(app):
            names = {name for name, _ in calls}
            assert names == _ALWAYS_ON_LOOP_NAMES | {_EXTERNAL_DEADMAN_LOOP_NAME}
            assert "calendar_sync_deadman" in names

    # The `async with` block has now run the full shutdown section of
    # lifespan() (everything after `yield`), which cancels + awaits each
    # named task in turn.
    tasks_by_name = dict(calls)
    calendar_task = tasks_by_name["calendar_sync_deadman"]
    assert calendar_task.cancelled(), (
        "calendar_sync_deadman must be cancelled on shutdown (bu-27dxl.6.5 AC4)"
    )
    for name, task in tasks_by_name.items():
        assert task.done(), f"{name} must be fully awaited/done after shutdown"
        assert task.cancelled(), f"{name} must terminate via cancellation, not crash, on shutdown"

    # No loop restarted as a result of shutdown: each underlying run_*_loop
    # fake was invoked exactly once (its one and only steady-state call).
    assert all(count == 1 for count in call_counts.values()), (
        f"a loop restarted during/after shutdown: {call_counts}"
    )
    assert "restarting in" not in caplog.text
