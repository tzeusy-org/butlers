"""Overlay freshness: the pure rule and the stamped envelope.

The envelope test runs the real writer against Postgres and reads the stored value back, so the rule
is judged on what the job actually wrote.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta

import pytest

from butlers.api.read_models.calendar_workspace import (
    OVERLAY_CONTRIBUTORS,
    CalendarOverlayRow,
    compute_overlay_freshness,
)
from butlers.core.state import state_get
from butlers.jobs import calendar_overlay as job

NOW = datetime(2026, 2, 22, 12, 0, tzinfo=UTC)


def _row(
    butler: str,
    generated_at: datetime | None,
    cadence: int | None = 86400,
    complete: bool | None = None,
):
    value: dict = {"butler": butler, "date": "2026-02-22", "has_entries": False, "entries": []}
    if complete is not None:
        value["complete"] = complete
    if generated_at is not None:
        value["generated_at"] = generated_at.isoformat()
    if cadence is not None:
        value["cadence_seconds"] = cadence
    return CalendarOverlayRow(butler=butler, key="calendar/overlay/2026-02-22", value=value)


def _states(rows, *, available=True, now=NOW):
    return {
        f["butler"]: f["state"]
        for f in compute_overlay_freshness(rows, available=available, now=now)
    }


@pytest.mark.unit
def test_within_twice_the_cadence_is_ok_and_beyond_it_is_stale():
    assert _states([_row("finance", NOW - timedelta(hours=47))])["finance"] == "ok"
    assert _states([_row("finance", NOW - timedelta(hours=49))])["finance"] == "stale"


@pytest.mark.unit
def test_the_newest_envelope_of_a_butler_decides():
    rows = [_row("finance", NOW - timedelta(days=9)), _row("finance", NOW - timedelta(hours=1))]
    assert _states(rows)["finance"] == "ok"


@pytest.mark.unit
def test_no_stamp_is_stale_and_no_envelope_is_missing_and_an_unreadable_view_is_failed():
    assert _states([_row("finance", None, None)])["finance"] == "stale"
    assert _states([])["health"] == "missing"
    assert set(_states([], available=False).values()) == {"failed"}
    assert set(_states([], available=False)) == set(OVERLAY_CONTRIBUTORS)


@pytest.mark.unit
def test_an_incomplete_envelope_reads_partial_and_staleness_outranks_it():
    assert _states([_row("finance", NOW - timedelta(hours=1), complete=False)])["finance"] == (
        "partial"
    )
    assert _states([_row("finance", NOW - timedelta(hours=1), complete=True)])["finance"] == "ok"
    assert _states([_row("finance", NOW - timedelta(hours=60), complete=False)])["finance"] == (
        "stale"
    )


@pytest.mark.unit
def test_a_slower_cadence_widens_the_window():
    weekly = _row("finance", NOW - timedelta(days=10), cadence=7 * 86400)
    assert _states([weekly])["finance"] == "ok"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_the_writer_stamps_every_envelope_and_a_frozen_producer_goes_stale(db, monkeypatch):
    pool = await db("core", schema="finance")
    monkeypatch.setattr(job, "_utcnow", lambda: NOW)
    await job.write_overlay_envelopes(
        pool, butler="finance", today=date(2026, 2, 22), lookahead_days=1, entries_by_date={}
    )

    stored = await state_get(pool, job.overlay_key("2026-02-23"))
    assert stored["generated_at"] == NOW.isoformat()
    assert stored["cadence_seconds"] == job.OVERLAY_CADENCE_SECONDS

    row = CalendarOverlayRow(butler="finance", key=job.overlay_key("2026-02-23"), value=stored)
    assert _states([row], now=NOW + timedelta(hours=10))["finance"] == "ok"
    # The producer is frozen: nothing re-writes it, and the clock moves past twice its cadence.
    assert _states([row], now=NOW + timedelta(hours=49))["finance"] == "stale"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_an_incomplete_write_is_stored_flagged_and_reads_partial(db, monkeypatch):
    pool = await db("core", schema="finance")
    monkeypatch.setattr(job, "_utcnow", lambda: NOW)
    await job.write_overlay_envelopes(
        pool,
        butler="finance",
        today=date(2026, 2, 22),
        lookahead_days=0,
        entries_by_date={},
        complete=False,
    )

    stored = await state_get(pool, job.overlay_key("2026-02-22"))
    assert stored["complete"] is False
    row = CalendarOverlayRow(butler="finance", key=job.overlay_key("2026-02-22"), value=stored)
    assert _states([row], now=NOW + timedelta(hours=1))["finance"] == "partial"
