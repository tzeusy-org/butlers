# Spec: REQ-butler-general-010
"""day_sheet_push (bu-5ii4gj.2 S3): deterministic tomorrow-sheet delivery, dark by default."""

from __future__ import annotations

import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from butlers.api.db import DatabaseManager
from butlers.core.temporal.day_sheet import DaySheet, Freshness, Load
from butlers.jobs import day_sheet_push as job

pytestmark = pytest.mark.unit

SGT_NOW = datetime(2026, 11, 23, 14, 40, tzinfo=UTC)  # 22:40 in Singapore
ROOT = Path(__file__).resolve().parents[2]


def _sheet(digest: str = "d1", state: str = "current", unreadable=()) -> DaySheet:
    return DaySheet(
        date=date(2026, 11, 24),
        zone="Asia/Singapore",
        freshness=Freshness(
            state=state, as_of=None, sources_unreadable=list(unreadable), sources_total=1
        ),
        items=[],
        gaps=[],
        load=Load(attended_minutes=0, count=0),
        digest=digest,
        longest_gap=None,
    )


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setenv(job.ENABLED_ENV, "1")
    db = MagicMock(spec=DatabaseManager)
    w = SimpleNamespace(
        db=db,
        zone=AsyncMock(return_value=ZoneInfo("Asia/Singapore")),
        read=AsyncMock(return_value=_sheet()),
        send=AsyncMock(return_value="delivered"),
        delivered=AsyncMock(return_value=False),
        failures=AsyncMock(return_value=(0, None)),
        suppressed=AsyncMock(return_value=None),
        state=AsyncMock(),
    )
    monkeypatch.setattr(job, "owner_zoneinfo", w.zone)
    monkeypatch.setattr(job, "query_calendar_day_sheet", w.read)
    monkeypatch.setattr(job.owner_notify, "send_owner_telegram", w.send)
    monkeypatch.setattr(job.owner_notify, "check_owner_notify_suppression", w.suppressed)
    monkeypatch.setattr(job, "_delivered_row_exists", w.delivered)
    monkeypatch.setattr(job, "failed_attempts", w.failures)
    monkeypatch.setattr(job, "state_set", w.state)
    return w


async def test_dark_by_default_reads_and_sends_nothing(wired, monkeypatch):
    monkeypatch.delenv(job.ENABLED_ENV)
    assert await job.run_day_sheet_push(wired.db, now=SGT_NOW) == {"outcome": "disabled"}
    wired.read.assert_not_called()
    wired.send.assert_not_called()


async def test_outside_the_window_nothing_is_read_or_sent(wired):
    result = await job.run_day_sheet_push(wired.db, now=SGT_NOW - timedelta(hours=2))
    assert result["reason"] == "outside_window"
    wired.read.assert_not_called()


async def test_sends_tomorrows_owner_local_sheet_and_records_both_markers(wired):
    result = await job.run_day_sheet_push(wired.db, now=SGT_NOW)

    assert result["outcome"] == "delivered"
    assert wired.read.await_args.kwargs["local_date"] == date(2026, 11, 24)
    sent = wired.send.await_args.kwargs
    assert sent["message"].startswith("Tomorrow Prep\nYour day: ")
    assert sent["dedup_key"] == "day_sheet_push:2026-11-24:d1"
    keys = [c.args[1] for c in wired.state.await_args_list]
    assert keys == ["day_sheet/pushed/2026-11-24", "day_sheet/pushed/latest"]
    marker = wired.state.await_args_list[1].args[2]
    assert marker["date"] == "2026-11-24" and marker["digest"] == "d1"


async def test_the_marker_date_follows_the_owner_clock_not_sgt(wired):
    wired.zone.return_value = ZoneInfo("America/Los_Angeles")
    # 22:40 on Nov 23 in Los Angeles is already Nov 24 in UTC and Nov 24 14:40 in Singapore.
    now = datetime(2026, 11, 24, 6, 40, tzinfo=UTC)
    await job.run_day_sheet_push(wired.db, now=now)
    assert wired.read.await_args.kwargs["local_date"] == date(2026, 11, 24)
    assert wired.state.await_args_list[1].args[2]["zone"] == "America/Los_Angeles"
    # Singapore's 22:40 is a different instant and a different owner-local tomorrow.
    wired.zone.return_value = ZoneInfo("Asia/Singapore")
    await job.run_day_sheet_push(wired.db, now=SGT_NOW)
    assert wired.read.await_args.kwargs["local_date"] == date(2026, 11, 24)
    late = datetime(2026, 12, 31, 15, 40, tzinfo=UTC)  # Dec 31 22:40 UTC-0? Singapore Jan 1 03:40
    wired.read.reset_mock()
    wired.zone.return_value = ZoneInfo("Etc/UTC")
    await job.run_day_sheet_push(wired.db, now=datetime(2026, 12, 31, 22, 40, tzinfo=UTC))
    assert wired.read.await_args.kwargs["local_date"] == date(2027, 1, 1)
    del late


async def test_a_delivered_row_for_the_same_digest_ends_the_day(wired):
    wired.delivered.return_value = True
    result = await job.run_day_sheet_push(wired.db, now=SGT_NOW)
    assert result["reason"] == "already_sent"
    wired.send.assert_not_called()


async def test_only_a_delivered_row_counts_so_a_failed_send_retries(wired):
    # The ledger holds a failed attempt but no delivered row: the next tick sends again.
    wired.failures.return_value = (1, timedelta(minutes=10))
    result = await job.run_day_sheet_push(wired.db, now=SGT_NOW)
    assert result["outcome"] == "delivered"
    wired.send.assert_awaited_once()


async def test_a_recent_failure_backs_off(wired):
    wired.failures.return_value = (1, timedelta(minutes=1))
    result = await job.run_day_sheet_push(wired.db, now=SGT_NOW)
    assert result["reason"] == "retry_backoff"
    wired.send.assert_not_called()


async def test_a_suppressed_tick_sends_nothing_and_records_nothing(wired):
    wired.suppressed.return_value = "quiet_hours"
    result = await job.run_day_sheet_push(wired.db, now=SGT_NOW)
    assert result == {"outcome": "skipped", "reason": "quiet_hours"}
    wired.send.assert_not_called()
    wired.state.assert_not_called()


async def test_an_unreadable_sheet_is_sent_as_unreadable_never_clear(wired):
    wired.read.return_value = _sheet("u", "unreadable", ["general"])
    await job.run_day_sheet_push(wired.db, now=SGT_NOW)
    message = wired.send.await_args.kwargs["message"]
    assert "unreadable" in message and "Nothing on your calendar" not in message


async def test_a_failed_send_is_recorded_not_raised(wired):
    wired.send.return_value = "failed"
    result = await job.run_day_sheet_push(wired.db, now=SGT_NOW)
    assert result["outcome"] == "failed"
    assert wired.state.await_args.args[2]["outcome"] == "failed"


async def test_the_switchboard_client_is_bound_for_the_send_and_released(wired):
    from butlers.core.tool_call_capture import get_current_switchboard_client

    seen = []

    async def send(*_a, **_k):
        seen.append(get_current_switchboard_client())
        return "delivered"

    wired.send.side_effect = send
    client = object()
    await job.run_day_sheet_push(
        wired.db, client_provider=AsyncMock(return_value=client), now=SGT_NOW
    )
    assert seen == [client]
    assert get_current_switchboard_client() is None


def test_the_prompt_reads_the_latest_marker_and_no_schedule_remains_on_the_butler():
    config = tomllib.loads((ROOT / "roster/general/butler.toml").read_text())
    schedules = {s["name"]: s for s in config["butler"]["schedule"]}
    assert "day_sheet_push" not in schedules  # it runs in the API process
    prompt = schedules["eod-tomorrow-prep"]["prompt"]
    assert "day_sheet/pushed/latest" in prompt
    assert "SGT" not in prompt and "UTC+8" not in prompt
    assert "No events scheduled" in prompt and "never say" in prompt
