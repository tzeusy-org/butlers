# Spec: REQ-butler-general-010
"""day_sheet_push (bu-5ii4gj.2 S3): deterministic tomorrow-sheet delivery, dark by default."""

from __future__ import annotations

import tomllib
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.core.temporal.day_sheet import DaySheet, Freshness, Load
from butlers.jobs import day_sheet_push as job

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 23, 14, 30, tzinfo=UTC)  # 22:30 in Singapore
ROOT = Path(__file__).resolve().parents[2]


def _sheet(digest: str = "d1", state: str = "current") -> DaySheet:
    return DaySheet(
        date=date(2026, 11, 24),
        zone="Asia/Singapore",
        freshness=Freshness(state=state, as_of=None, sources_unreadable=[], sources_total=1),
        items=[],
        gaps=[],
        load=Load(attended_minutes=0, count=0),
        digest=digest,
        longest_gap=None,
    )


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setenv(job.ENABLED_ENV, "1")
    pool = object()
    read = AsyncMock(return_value=_sheet())
    send = AsyncMock(return_value="delivered")
    seen = AsyncMock(return_value=False)
    state = AsyncMock()
    monkeypatch.setattr(
        job, "load_general_settings", AsyncMock(return_value={"timezone": "Asia/Singapore"})
    )
    monkeypatch.setattr(job, "query_calendar_day_sheet", read)
    monkeypatch.setattr(job.owner_notify, "send_owner_telegram", send)
    monkeypatch.setattr(job, "attention_event_recorded_since", seen)
    monkeypatch.setattr(job, "state_set", state)
    return SimpleNamespace(pool=pool, read=read, send=send, seen=seen, state=state)


async def test_dark_by_default_reads_and_sends_nothing(wired, monkeypatch):
    monkeypatch.delenv(job.ENABLED_ENV)
    assert await job.run_day_sheet_push(wired.pool, now=NOW) == {"outcome": "disabled"}
    wired.read.assert_not_called()
    wired.send.assert_not_called()


async def test_sends_tomorrows_owner_local_sheet_and_records_the_outcome(wired):
    result = await job.run_day_sheet_push(wired.pool, now=NOW)

    assert result["outcome"] == "delivered"
    assert wired.read.await_args.kwargs["local_date"] == date(2026, 11, 24)
    sent = wired.send.await_args.kwargs
    assert sent["message"].startswith("Tomorrow Prep\nYour day: ")
    assert sent["dedup_key"] == "day_sheet_push:2026-11-24:d1"
    key, value = wired.state.await_args.args[1:]
    assert key == "day_sheet/pushed/2026-11-24"
    assert value["outcome"] == "delivered" and value["digest"] == "d1"


async def test_a_rerun_of_unchanged_content_sends_nothing(wired):
    wired.seen.return_value = True
    result = await job.run_day_sheet_push(wired.pool, now=NOW)
    assert result["reason"] == "already_sent"
    wired.send.assert_not_called()


async def test_an_unreadable_sheet_is_sent_as_unreadable_never_clear(wired):
    wired.read.return_value = DaySheet(
        date=date(2026, 11, 24),
        zone="Asia/Singapore",
        freshness=Freshness(
            state="unreadable", as_of=None, sources_unreadable=["general"], sources_total=1
        ),
        items=[],
        gaps=[],
        load=Load(attended_minutes=0, count=0),
        digest="u",
        longest_gap=None,
    )
    await job.run_day_sheet_push(wired.pool, now=NOW)
    message = wired.send.await_args.kwargs["message"]
    assert "unreadable" in message and "Nothing on your calendar" not in message


async def test_a_failed_send_is_recorded_not_raised(wired):
    wired.send.return_value = "failed"
    result = await job.run_day_sheet_push(wired.pool, now=NOW)
    assert result["outcome"] == "failed"
    assert wired.state.await_args.args[2]["outcome"] == "failed"


def test_the_job_is_registered_and_scheduled_ahead_of_the_prompt():
    from butlers.scheduled_jobs import _build_deterministic_schedule_job_registry

    assert "day_sheet_push" in _build_deterministic_schedule_job_registry()["general"]
    config = tomllib.loads((ROOT / "roster/general/butler.toml").read_text())
    schedules = {s["name"]: s for s in config["butler"]["schedule"]}
    assert schedules["day_sheet_push"]["dispatch_mode"] == "job"
    assert schedules["day_sheet_push"]["job_name"] == "day_sheet_push"
    prompt = schedules["eod-tomorrow-prep"]["prompt"]
    assert "day_sheet/pushed/" in prompt
    assert "never say" in prompt and "No events scheduled" in prompt


async def test_the_single_schema_adapter_reports_a_failed_read_not_an_empty_one():
    class Boom:
        async def fetch(self, *_a):
            raise RuntimeError("down")

    db = job._SingleSchemaDb("general", Boom())  # type: ignore[arg-type]
    results, failed = await db.fan_out_with_status("SELECT 1")
    assert failed == ["general"] and results == {"general": []}
    assert db.butlers_with_module("calendar") == ["general"]
