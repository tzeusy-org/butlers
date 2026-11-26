"""Health visits reach the overlay from the calendar projection (bu-rx0tyg, bu-5ii4gj.13 S5).

Runs the real Health overlay job against real Postgres: an appointment Health booked (its applied
``workspace_user_create`` row) shows in the overlay with a claim keyed by its event, and nothing
else on the calendar does. This is the source the visit brief already uses.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from butlers.core.state import state_get
from butlers.jobs import calendar_overlay as job

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

TODAY = date(2030, 3, 1)
NOW = datetime(2030, 3, 1, 4, 0, tzinfo=UTC)  # 12:00 SGT
VISIT = datetime(2030, 3, 4, 2, 0, tzinfo=UTC)  # 10:00 SGT on the 4th


async def _source(pool, *, butler: str | None, specific: bool) -> str:
    source_id = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO calendar_sources (id, source_key, source_kind, lane, butler_name, metadata) "
        "VALUES ($1::uuid, $2, 'provider', 'user', $3, $4)",
        source_id,
        f"src-{source_id}",
        butler,
        {"butler_specific": specific},
    )
    return source_id


async def _event(pool, source_id, *, title, booked, status="confirmed", starts_at=VISIT) -> str:
    event_id = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO calendar_events (id, source_id, origin_ref, title, timezone, starts_at, "
        "ends_at, all_day, status, metadata, source_butler) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, 'UTC', $5, $6, false, $7, '{}'::jsonb, 'health')",
        event_id,
        source_id,
        f"ref-{event_id}",
        title,
        starts_at,
        starts_at + timedelta(hours=1),
        status,
    )
    if booked:
        await pool.execute(
            "INSERT INTO calendar_action_log (idempotency_key, action_type, action_status, "
            "source_id, event_id, origin_ref) "
            "VALUES ($1, 'workspace_user_create', 'applied', $2::uuid, $3::uuid, $4)",
            f"book-{event_id}",
            source_id,
            event_id,
            f"ref-{event_id}",
        )
    return event_id


async def _world(db, monkeypatch):
    pool = await db("core", "memory", schema="health")
    monkeypatch.setattr("butlers.jobs.briefing._now", lambda: NOW)
    monkeypatch.setattr(job, "_utcnow", lambda: NOW)
    for table in ("calendar_sources", "calendar_events"):
        await pool.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608
    return pool


async def test_an_appointment_health_booked_reaches_the_overlay_and_nothing_else_does(
    db, monkeypatch
):
    pool = await _world(db, monkeypatch)
    health = await _source(pool, butler="health", specific=True)
    other = await _source(pool, butler="finance", specific=True)
    primary = await _source(pool, butler=None, specific=False)
    booked = await _event(pool, health, title="Dr Tan, cardiology", booked=True)
    await _event(pool, health, title="Owner added, not booked by Health", booked=False)
    await _event(pool, health, title="Cancelled visit", booked=True, status="cancelled")
    await _event(pool, other, title="Another butler's booking", booked=True)
    await _event(pool, primary, title="Primary calendar event", booked=False)

    result = await job.run_health_calendar_overlay_contribution(pool, None)

    envelope = await state_get(pool, job.overlay_key("2030-03-04"))
    assert [e["label"] for e in envelope["entries"]] == ["Dr Tan, cardiology"]
    assert envelope["entries"][0]["meta"]["appointment_id"] == booked
    assert [c["claim_key"] for c in envelope["claims"]] == [f"health:visit:{booked}"]
    assert envelope["claims"][0]["presence"] == "overlay"
    assert result["appointment_entries"] == 1 and result["complete"] is True


async def test_a_rescheduled_appointment_moves_its_entry_and_keeps_its_claim_key(db, monkeypatch):
    pool = await _world(db, monkeypatch)
    health = await _source(pool, butler="health", specific=True)
    booked = await _event(pool, health, title="Dental", booked=True)
    await job.run_health_calendar_overlay_contribution(pool, None)

    await pool.execute(
        "UPDATE calendar_events SET starts_at = $2, ends_at = $3 WHERE id = $1::uuid",
        booked,
        VISIT + timedelta(days=2),
        VISIT + timedelta(days=2, hours=1),
    )
    await job.run_health_calendar_overlay_contribution(pool, None)

    old = await state_get(pool, job.overlay_key("2030-03-04"))
    new = await state_get(pool, job.overlay_key("2030-03-06"))
    assert old["entries"] == [] and old["claims"] == []
    assert [c["claim_key"] for c in new["claims"]] == [f"health:visit:{booked}"]
