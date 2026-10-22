"""sync_schedules honours a per-schedule zone and warns on always-quiet schedules. [bu-kqnum.3.7]

The lint is warn-only: it never rejects, never fires without a usable quiet-window policy, and
never fires for a schedule with at least one firing outside the window or whose notification
intent is unknown.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from butlers.config import ScheduleSpec
from butlers.core.scheduler import schedule_always_in_quiet_window, sync_schedules

pytestmark = pytest.mark.unit

# 22:00-07:00 Singapore time, end-exclusive.
SGT_POLICY = {"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "Asia/Singapore"}
NOW = datetime(2026, 10, 19, 3, 0, tzinfo=UTC)


class _FakePool:
    """Records writes; reports every optional column present and no existing rows."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.inserted: list[tuple[Any, ...]] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "information_schema" in sql:
            return True
        self.inserted.append(args)
        return uuid.uuid4()

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        return []

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "OK"


def _schedule(name: str, cron: str, **extra: Any) -> ScheduleSpec:
    base: dict[str, Any] = {
        "name": name,
        "cron": cron,
        "prompt": 'Do the thing, then notify(channel="telegram", intent="send", message="x").',
    }
    base.update(extra)
    return ScheduleSpec(**base)


async def _sync(schedules: list[ScheduleSpec], **kwargs: Any) -> _FakePool:
    pool = _FakePool()
    await sync_schedules(pool, schedules, default_timezone="Asia/Singapore", **kwargs)  # type: ignore[arg-type]
    return pool


def _warnings(caplog: pytest.LogCaptureFixture, needle: str = "quiet window") -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and needle in r.getMessage()
    ]


@pytest.mark.parametrize(
    ("cron", "expect_warning"),
    [
        ("0 23 * * *", True),  # every firing inside 22:00-07:00 SGT
        ("0 3 * * 1-5", True),
        ("0 6,23 * * *", True),
        ("0 6,8 * * *", False),  # 08:00 SGT is outside the window
        ("0 23 * * 1", True),
        ("*/30 * * * *", False),
    ],
)
async def test_one_warning_only_for_an_always_quiet_schedule(
    caplog: pytest.LogCaptureFixture, cron: str, expect_warning: bool
) -> None:
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        await _sync([_schedule("nightly", cron)], quiet_policy=SGT_POLICY)
    assert len(_warnings(caplog)) == (1 if expect_warning else 0)


async def test_repeated_sync_repeats_the_same_single_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        await _sync([_schedule("nightly", "0 23 * * *")], quiet_policy=SGT_POLICY)
        await _sync([_schedule("nightly", "0 23 * * *")], quiet_policy=SGT_POLICY)
    assert len(_warnings(caplog)) == 2
    assert len(set(_warnings(caplog))) == 1


async def test_sync_proceeds_and_writes_the_row_despite_the_warning() -> None:
    pool = await _sync([_schedule("nightly", "0 23 * * *")], quiet_policy=SGT_POLICY)
    assert len(pool.inserted) == 1


@pytest.mark.parametrize("policy", [None, {}, {"quiet_start_hour": None, "quiet_end_hour": None}])
async def test_no_usable_policy_means_no_warning(
    caplog: pytest.LogCaptureFixture, policy: dict[str, Any] | None
) -> None:
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        await _sync([_schedule("nightly", "0 23 * * *")], quiet_policy=policy)
    assert _warnings(caplog) == []


async def test_job_mode_and_non_notifying_prompts_are_not_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    schedules = [
        ScheduleSpec(name="job", cron="0 23 * * *", dispatch_mode="job", job_name="some_job"),
        _schedule("silent", "0 23 * * *", prompt="Tidy the state store."),
    ]
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        await _sync(schedules, quiet_policy=SGT_POLICY)
    assert _warnings(caplog) == []


async def test_per_schedule_zone_decides_whether_firings_are_quiet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 23:00 in Singapore is quiet; 23:00 UTC is 07:00 SGT, which is outside the window.
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        await _sync(
            [_schedule("pinned-utc", "0 23 * * *", timezone="Etc/UTC")], quiet_policy=SGT_POLICY
        )
    assert _warnings(caplog) == []


async def test_stagger_pushing_a_firing_out_of_the_window_means_no_warning() -> None:
    # 06:55 SGT is quiet, but a 15 minute stagger can push it to 07:10, which is not.
    assert not schedule_always_in_quiet_window(
        "55 6 * * *", timezone="Asia/Singapore", quiet_policy=SGT_POLICY, now=NOW
    )
    assert schedule_always_in_quiet_window(
        "55 6 * * *",
        timezone="Asia/Singapore",
        quiet_policy=SGT_POLICY,
        now=NOW,
        max_stagger_seconds=0,
    )


async def test_explicit_zone_drives_next_run_and_is_stored() -> None:
    utc_pinned = await _sync([_schedule("infra", "15 4 * * *", timezone="Etc/UTC")])
    sg_default = await _sync([_schedule("infra", "15 4 * * *")])
    pinned_next = utc_pinned.inserted[0][7]
    default_next = sg_default.inserted[0][7]
    # 04:15 UTC versus 04:15 Singapore (= 20:15 UTC the day before); stagger is under 15 minutes.
    assert 4 <= pinned_next.astimezone(UTC).hour < 5
    assert default_next.astimezone(UTC).hour in {19, 20}
    stored = {args[1] for sql, args in utc_pinned.executed if "SET timezone" in sql}
    assert stored == {"Etc/UTC"}
    sentinel = {args[1] for sql, args in sg_default.executed if "SET timezone" in sql}
    assert sentinel == {"UTC"}


@pytest.mark.parametrize("bad", ["Not/AZone", 5])
async def test_invalid_schedule_timezone_is_rejected(bad: Any) -> None:
    with pytest.raises(ValueError, match="timezone"):
        _schedule("bad", "0 4 * * *", timezone=bad)


@pytest.mark.parametrize("sentinel", ["UTC", "utc", "", "  "])
async def test_sentinel_schedule_timezone_is_refused_naming_etc_utc(sentinel: str) -> None:
    with pytest.raises(ValueError, match=r"Etc/UTC"):
        _schedule("bad", "0 4 * * *", timezone=sentinel)


class _ExistingRowPool(_FakePool):
    """Reports one existing TOML row identical to the schedule except for its timezone."""

    def __init__(self, stored_timezone: str, schedule: ScheduleSpec) -> None:
        super().__init__()
        self._row = {
            "id": uuid.uuid4(),
            "name": schedule.name,
            "source": "toml",
            "cron": schedule.cron,
            "prompt": schedule.prompt,
            "dispatch_mode": "prompt",
            "job_name": None,
            "job_args": None,
            "complexity": "workhorse",
            "enabled": True,
            "task_type": "cron",
            "max_token_budget": None,
            "continuity": False,
            "timezone": stored_timezone,
        }

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        return [self._row] if "name = ANY" in sql else []


@pytest.mark.parametrize(
    ("stored", "declared", "expect_update"),
    [("UTC", "Etc/UTC", True), ("Etc/UTC", None, True), ("Etc/UTC", "Etc/UTC", False)],
)
async def test_a_changed_timezone_alone_updates_the_existing_row(
    stored: str, declared: str | None, expect_update: bool
) -> None:
    schedule = _schedule("infra", "15 4 * * *", timezone=declared)
    pool = _ExistingRowPool(stored, schedule)
    await sync_schedules(pool, [schedule], default_timezone="Asia/Singapore")  # type: ignore[arg-type]
    updated = [sql for sql, _ in pool.executed if "UPDATE scheduled_tasks" in sql]
    assert bool(updated) is expect_update
    if expect_update:
        assert any(
            args[1] == (declared or "UTC") for sql, args in pool.executed if "SET timezone" in sql
        )
