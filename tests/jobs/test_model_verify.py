"""Tests for butlers.jobs.model_verify (bu-hmdqz.2, cut over in bu-0uqgo.11).

Covers:
- run_model_verify_sweep: delegates to run_verify_all_models as the
  ``scheduler`` control caller with audit_actor="model_verify_sweep"; returns
  None (no raise) when no shared pool is configured.
- run_model_verify_loop: sleeps first, ticks on interval, can be cancelled,
  skips a tick when no pool is available, swallows a bad tick.
- REQ-core-credentials-002 criterion 8 (and REQ-dashboard-model-settings-001's
  verification surface): this loop is the *only* internal scheduled caller of
  the runtime-probe control plane, and the private probe command is not
  reachable through generic MCP.

That last group is the reason this file scans source.  A second scheduled
caller would be an ordinary-looking addition --- another job importing the
same shared core --- and nothing about it would fail a behavioural test; the
capability grammar admits exactly two caller classes, so "which code may sign
as the scheduler" has to be asked of the tree rather than of one module.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from butlers.core.runtime_probe_control.capability import CALLERS
from butlers.jobs.model_verify import (
    _CALLER,
    DEFAULT_MODEL_VERIFY_INTERVAL_S,
    run_model_verify_loop,
    run_model_verify_sweep,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.unit


class _FakeDatabaseManager:
    def __init__(self, *, pool: object | None = None, missing: bool = False) -> None:
        self._pool = pool
        self._missing = missing

    def credential_shared_pool(self):
        if self._missing:
            raise KeyError("no shared pool")
        return self._pool


# ---------------------------------------------------------------------------
# run_model_verify_sweep
# ---------------------------------------------------------------------------


async def test_sweep_returns_none_when_pool_missing():
    result = await run_model_verify_sweep(_FakeDatabaseManager(missing=True))
    assert result is None


async def test_sweep_delegates_to_run_verify_all_models(monkeypatch):
    """One shared core, called as the scheduler, with the automated actor.

    The summary preserves ``unavailable`` as a separate coordinator outcome:
    it is a different fact from a model that answered badly, and the other
    summary counts pass through unchanged.
    """
    verify_result = AsyncMock(
        return_value=type("R", (), {"total": 3, "ok": 2, "failed": 1, "unavailable": 0})()
    )
    monkeypatch.setattr("butlers.api.routers.model_settings.run_verify_all_models", verify_result)
    pool = object()

    result = await run_model_verify_sweep(_FakeDatabaseManager(pool=pool))

    verify_result.assert_awaited_once_with(
        pool,
        audit_actor="model_verify_sweep",
        caller="scheduler",
    )
    assert result == {"total": 3, "ok": 2, "failed": 1, "unavailable": 0}


async def test_the_sweep_builds_no_credential_authority_of_its_own():
    """Criterion 4: this process holds no adapter, and so needs no provider authority.

    Before the cutover it constructed a ``CredentialStore`` over the shared
    pool to hand Codex its authority.  Switchboard resolves authority inside
    the probe now, so the scheduler container has no reason to hold one --- and
    a reintroduced one would be a provider credential in a process that is
    supposed to have stopped touching providers.
    """
    import butlers.jobs.model_verify as module

    assert not hasattr(module, "CredentialStore")


# ---------------------------------------------------------------------------
# run_model_verify_loop
# ---------------------------------------------------------------------------


async def test_loop_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        await run_model_verify_loop(_FakeDatabaseManager(pool=object()), interval_s=0)


async def test_default_interval_is_one_hour():
    assert DEFAULT_MODEL_VERIFY_INTERVAL_S == 3600.0


async def test_loop_sleeps_then_ticks_and_can_be_cancelled(monkeypatch):
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    sweep_mock = AsyncMock(return_value={"total": 0, "ok": 0, "failed": 0})
    monkeypatch.setattr("butlers.jobs.model_verify.run_model_verify_sweep", sweep_mock)

    with pytest.raises(asyncio.CancelledError):
        await run_model_verify_loop(_FakeDatabaseManager(pool=object()), interval_s=5)

    assert sleep_calls == [5, 5]
    assert sweep_mock.await_count == 1


async def test_loop_swallows_a_bad_tick(monkeypatch):
    call_count = 0

    async def _fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    sweep_mock = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("butlers.jobs.model_verify.run_model_verify_sweep", sweep_mock)

    with pytest.raises(asyncio.CancelledError):
        await run_model_verify_loop(_FakeDatabaseManager(pool=object()), interval_s=5)

    assert sweep_mock.await_count == 1


# ---------------------------------------------------------------------------
# Criterion 8: the only internal scheduled caller, and not an MCP command
# ---------------------------------------------------------------------------


def _production_sources() -> list[Path]:
    return sorted(
        path for directory in ("src", "roster") for path in (_REPO_ROOT / directory).rglob("*.py")
    )


def test_scheduler_is_a_registered_caller_class_and_the_grammar_is_closed():
    """Two caller classes exist; this job owns one of them."""
    assert _CALLER == "scheduler"
    assert set(CALLERS) == {"dashboard", "scheduler"}


def test_this_job_is_the_only_module_that_calls_the_shared_verification_core():
    """Criterion 8: one scheduled caller, enumerated rather than assumed.

    The dashboard router is where the core lives, so it is expected here; any
    third module would be a second automated sweep signing capabilities on a
    cadence nobody registered.
    """
    callers = {
        path.relative_to(_REPO_ROOT).as_posix()
        for path in _production_sources()
        if "run_verify_all_models" in path.read_text(encoding="utf-8")
    }

    assert callers == {
        "src/butlers/api/routers/model_settings.py",
        "src/butlers/jobs/model_verify.py",
    }


def test_the_sweep_loop_is_registered_exactly_once():
    """A second registration would double the sweep's cadence silently."""
    app_source = (_REPO_ROOT / "src" / "butlers" / "api" / "app.py").read_text(encoding="utf-8")

    assert app_source.count("run_model_verify_loop(") == 1


def test_no_mcp_tool_reaches_the_probe_command():
    """Criterion 8: the private command is not exposed through generic MCP.

    Tool surfaces are the module packages and the roster; a decorated function
    in either that could reach the control plane would put a signed probe one
    tool call away from a model session.  Checked by import rather than by
    grepping for a decorator name, since a tool can be registered under any
    spelling but cannot call what its module never imports.
    """
    tool_surfaces = [
        path
        for path in _production_sources()
        if "modules" in path.parts or path.is_relative_to(_REPO_ROOT / "roster")
    ]
    reaching = set()
    for path in tool_surfaces:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: set[str] = set()
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = {node.module}
            if any(
                name.startswith("butlers.core.runtime_probe_control")
                or name.startswith("butlers.jobs.model_verify")
                for name in names
            ):
                reaching.add(path.relative_to(_REPO_ROOT).as_posix())

    assert tool_surfaces, "no tool surface was scanned -- the enumeration stopped matching"
    assert reaching == set()
