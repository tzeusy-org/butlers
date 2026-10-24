"""run_startup hands each TOML schedule's timezone and the owner quiet policy to sync_schedules."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers import lifecycle
from butlers.config import ButlerType, ScheduleSpec
from butlers.core.approvals_policy import get_approvals_policy_quiet_hours

pytestmark = pytest.mark.unit

_POLICY = {"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "Asia/Singapore"}


def _daemon() -> SimpleNamespace:
    config = SimpleNamespace(
        name="general",
        type=ButlerType.BUTLER,
        config_dir=Path("/nonexistent"),
        schedules=[
            ScheduleSpec(name="infra", cron="15 4 * * *", prompt="x", timezone="Etc/UTC"),
            ScheduleSpec(name="owner-local", cron="0 23 * * *", prompt="y"),
        ],
    )
    return SimpleNamespace(config=config, config_dir=Path("/nonexistent"))


async def _sync_with(
    monkeypatch: pytest.MonkeyPatch, shared_pool, *, policy_accessor=None
) -> AsyncMock:
    sync = AsyncMock()
    monkeypatch.setattr(lifecycle, "sync_schedules", sync)
    monkeypatch.setattr(lifecycle, "resolve_general_timezone", AsyncMock(return_value="UTC"))
    monkeypatch.setattr(
        lifecycle,
        "get_approvals_policy_quiet_hours",
        policy_accessor if policy_accessor is not None else AsyncMock(return_value=_POLICY),
    )
    monkeypatch.setattr(lifecycle, "get_skills_dir", lambda _dir: Path("/skills"))
    await lifecycle._sync_toml_schedules(
        _daemon(), object(), SimpleNamespace(shared_pool=shared_pool)
    )
    return sync


async def test_a_pinned_schedule_reaches_sync_with_its_zone_and_the_quiet_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync = await _sync_with(monkeypatch, shared_pool=object())

    schedules = {s.name: s for s in sync.await_args.args[1]}
    assert schedules["infra"].timezone == "Etc/UTC"
    assert schedules["owner-local"].timezone is None
    assert sync.await_args.kwargs["quiet_policy"] == _POLICY


async def test_without_a_shared_pool_the_lint_gets_no_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync = await _sync_with(monkeypatch, shared_pool=None)

    assert sync.await_args.kwargs["quiet_policy"] is None


@pytest.mark.parametrize("read", ["missing", "null-window", "unavailable", "unexpected", "cancel"])
async def test_advisory_policy_read_keeps_sync_and_propagates_unrelated_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, read: str
) -> None:
    row = {"quiet_start_hour": None, "quiet_end_hour": None, "timezone": "UTC"}
    fetch = AsyncMock(
        return_value=row if read == "null-window" else None,
        side_effect=RuntimeError("database unavailable") if read == "unavailable" else None,
    )
    accessor = get_approvals_policy_quiet_hours
    if read in {"unexpected", "cancel"}:
        error = ValueError if read == "unexpected" else asyncio.CancelledError
        accessor = AsyncMock(side_effect=error)
        with pytest.raises(error):
            await _sync_with(monkeypatch, SimpleNamespace(fetchrow=fetch), policy_accessor=accessor)
        lifecycle.sync_schedules.assert_not_awaited()
        return

    sync = await _sync_with(monkeypatch, SimpleNamespace(fetchrow=fetch), policy_accessor=accessor)
    fetch.assert_awaited_once()
    sync.assert_awaited_once()
    assert sync.await_args.kwargs["quiet_policy"] == (row if read == "null-window" else None)
    assert [s.name for s in sync.await_args.args[1]] == ["infra", "owner-local"]
    assert sync.await_args.kwargs["default_timezone"] == "UTC"
    assert sync.await_args.kwargs["stagger_key"] == "general"
    assert sync.await_args.kwargs["skills_dir"] == Path("/skills")
    assert ("advisory quiet-window lint unavailable" in caplog.text) == (read == "unavailable")
    assert "database unavailable" not in caplog.text
