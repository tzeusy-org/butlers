"""run_startup hands each TOML schedule's timezone and the owner quiet policy to sync_schedules."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers import lifecycle
from butlers.config import ButlerType, ScheduleSpec

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


async def _sync_with(monkeypatch: pytest.MonkeyPatch, shared_pool) -> AsyncMock:
    sync = AsyncMock()
    monkeypatch.setattr(lifecycle, "sync_schedules", sync)
    monkeypatch.setattr(lifecycle, "resolve_general_timezone", AsyncMock(return_value="UTC"))
    monkeypatch.setattr(
        lifecycle, "get_approvals_policy_quiet_hours", AsyncMock(return_value=_POLICY)
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
