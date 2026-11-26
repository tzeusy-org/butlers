"""Real-Postgres: schedule_create stores a declared anchor consistent with its timezone."""

from __future__ import annotations

import shutil

import pytest

from butlers.config import ScheduleSpec
from butlers.core.scheduler.anchor import effective_anchor
from butlers.core.scheduler.crud import schedule_create
from butlers.core.scheduler.sync import sync_schedules
from butlers.core.scheduler.update import schedule_update

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


async def _general(db):
    """The general butler's schema on the shared test database (the factory, not a fixture)."""
    return await db("core", schemas={"core": "general"}, schema="general")


async def _row(pool, name):
    return await pool.fetchrow(
        "SELECT timezone, anchor_kind, anchor_zone FROM scheduled_tasks WHERE name = $1", name
    )


async def test_a_declared_anchor_is_stored_with_a_matching_timezone(db) -> None:
    pool = await _general(db)
    await schedule_create(
        pool, "pinned", "0 9 * * *", "p", anchor_kind="zone_pinned", anchor_zone="Europe/London"
    )
    await schedule_create(
        pool, "pinned_utc", "0 9 * * *", "p", anchor_kind="zone_pinned", anchor_zone="Etc/UTC"
    )
    await schedule_create(pool, "local", "0 9 * * *", "p", anchor_kind="owner_local")
    await schedule_create(pool, "undeclared", "0 9 * * *", "p", timezone="Asia/Tokyo")

    assert tuple(await _row(pool, "pinned")) == ("Europe/London", "zone_pinned", "Europe/London")
    assert tuple(await _row(pool, "pinned_utc")) == ("Etc/UTC", "zone_pinned", "Etc/UTC")
    assert tuple(await _row(pool, "local")) == ("UTC", "owner_local", None)
    assert tuple(await _row(pool, "undeclared")) == ("Asia/Tokyo", None, None)


async def test_a_refused_declaration_creates_no_row(db) -> None:
    pool = await _general(db)
    with pytest.raises(ValueError):
        await schedule_create(pool, "bad", "0 9 * * *", "p", anchor_kind="zone_pinned")
    assert await _row(pool, "bad") is None


async def _effective(pool, name):
    row = dict(
        await pool.fetchrow(
            "SELECT cron, timezone, task_type, next_run_at, until_at, anchor_kind, anchor_zone "
            "FROM scheduled_tasks WHERE name = $1",
            name,
        )
    )
    return effective_anchor(row)


async def test_a_deadline_row_refuses_a_declared_anchor(db) -> None:
    pool = await _general(db)
    with pytest.raises(ValueError, match="deadline"):
        await schedule_create(
            pool,
            "dl",
            None,
            "p",
            task_type="deadline",
            target_date="2027-01-01",
            lead_time_days=3,
            alert_thresholds=[{"days_before": 1}],
            anchor_kind="absolute",
        )
    assert await _row(pool, "dl") is None


async def test_a_toml_timezone_change_syncs_and_the_effective_kind_follows(db) -> None:
    pool = await _general(db)
    await schedule_create(
        pool, "brief", "0 9 * * *", "p", anchor_kind="zone_pinned", anchor_zone="Europe/London"
    )
    await sync_schedules(
        pool,
        [ScheduleSpec(name="brief", cron="0 9 * * *", prompt="p", timezone="Asia/Tokyo")],
    )

    row = await _row(pool, "brief")
    assert (row["timezone"], row["anchor_kind"], row["anchor_zone"]) == ("Asia/Tokyo", None, None)
    assert (await _effective(pool, "brief")).zone == "Asia/Tokyo"

    await sync_schedules(pool, [ScheduleSpec(name="brief", cron="0 9 * * *", prompt="p")])
    assert (await _effective(pool, "brief")).kind == "owner_local"


async def test_a_declared_owner_local_row_reclaimed_as_a_deadline_is_cleared(db) -> None:
    pool = await _general(db)
    await schedule_create(pool, "dl", "0 9 * * *", "p", anchor_kind="owner_local")

    await sync_schedules(
        pool,
        [
            ScheduleSpec(
                name="dl",
                cron="0 0 * * *",
                prompt="p",
                task_type="deadline",
                target_date="2027-01-01",
                lead_time_days=3,
                alert_thresholds=[{"days_before": 1}],
            )
        ],
    )

    row = await _row(pool, "dl")
    assert (row["anchor_kind"], row["anchor_zone"]) == (None, None)
    assert (await _effective(pool, "dl")).kind == "absolute"


async def test_a_schedule_edit_clears_the_declaration_and_a_noop_edit_keeps_it(db) -> None:
    pool = await _general(db)
    task = await schedule_create(pool, "edit", "0 9 * * *", "p", anchor_kind="owner_local")

    await schedule_update(pool, task, prompt="other")  # touches no anchor input
    assert (await _row(pool, "edit"))["anchor_kind"] == "owner_local"

    await schedule_update(pool, task, timezone="UTC")  # the same timezone again
    assert (await _row(pool, "edit"))["anchor_kind"] == "owner_local"

    await schedule_update(pool, task, timezone="Europe/London")
    row = await _row(pool, "edit")
    assert (row["timezone"], row["anchor_kind"]) == ("Europe/London", None)
    assert (await _effective(pool, "edit")).kind == "zone_pinned"


async def test_a_cron_row_turned_one_shot_is_no_longer_owner_local(db) -> None:
    pool = await _general(db)
    task = await schedule_create(pool, "once", "0 9 * * *", "p", anchor_kind="owner_local")
    next_run = await pool.fetchval("SELECT next_run_at FROM scheduled_tasks WHERE id = $1", task)

    await schedule_update(pool, task, until_at=next_run)

    assert (await _effective(pool, "once")).kind == "absolute"
