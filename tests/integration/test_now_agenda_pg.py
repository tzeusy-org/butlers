# Spec: REQ-module-calendar-052
"""The /now agenda line over a real calendar schema (bu-5ii4gj.2 P3 seams).

``_agenda_line`` is the route's own reducer call over the production ``DatabaseManager``: the text,
the exact ``fresh_until`` and the unknown-never-free behaviour are checked against migrated rows
rather than a fake fan-out. Synthetic data only.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta

import pytest

from butlers.api.read_models.calendar_day_sheet import STALE_AFTER
from butlers.api.routers import now as now_route
from butlers.core.general_settings import GENERAL_SETTINGS_STATE_KEY
from butlers.testing.db_manager import db_manager
from tests.integration.test_calendar_day_sheet_pg import _source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

T = datetime(2026, 11, 24, 6, 0, tzinfo=UTC)  # 14:00 in Singapore


async def _event(pool, source_id, ref: str, start: datetime, end: datetime) -> None:
    event_id = await pool.fetchval(
        "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, ends_at, "
        "status, metadata, source_butler) VALUES ($1, $2, $3, 'UTC', $4, $5, 'confirmed', "
        "'{}'::jsonb, 'general') RETURNING id",
        source_id,
        ref,
        f"Title {ref}",
        start,
        end,
    )
    await pool.execute(
        "INSERT INTO calendar_event_instances (event_id, source_id, origin_instance_ref, "
        "timezone, starts_at, ends_at) VALUES ($1, $2, $3, 'UTC', $4, $5)",
        event_id,
        source_id,
        f"{ref}:0",
        start,
        end,
    )


async def _world(db, monkeypatch, *, synced_at: datetime, current_end: timedelta):
    pool = await db("core")
    await pool.execute(
        "INSERT INTO public.state (key, value) VALUES ($1, $2::text::jsonb)",
        GENERAL_SETTINGS_STATE_KEY,
        json.dumps({"timezone": "Asia/Singapore"}),
    )
    source = await _source(pool, "owner", lane="user", role="owner", synced=False)
    await pool.execute(
        "INSERT INTO calendar_sync_cursors (source_id, cursor_name, last_synced_at, "
        "last_success_at) VALUES ($1, 'main', $2, $2)",
        source,
        synced_at,
    )
    await _event(pool, source, "now", T - timedelta(minutes=20), T + current_end)
    await _event(pool, source, "next", T + timedelta(minutes=90), T + timedelta(minutes=150))
    manager = db_manager(
        {"general": pool},
        modules={"general": {"calendar"}},
        credential_pool=pool,
    )
    monkeypatch.setattr(now_route, "get_db_optional", lambda: manager)
    monkeypatch.setattr(now_route, "_last_agenda", None)
    return pool


async def test_the_line_says_what_is_next_and_is_fresh_until_the_sync_bound(db, monkeypatch):
    synced = T - timedelta(minutes=2)
    await _world(db, monkeypatch, synced_at=synced, current_end=timedelta(minutes=40))

    line = await now_route._agenda_line(T)

    assert (line.state, line.stale) == ("known", False)
    # 14:40 end of the current meeting, next at 15:30 Singapore time; never a title.
    assert line.text == "In meeting until 14:40; next 15:30"
    assert "Title" not in line.text
    # The sync bound (STALE_AFTER after the last successful sync) comes before the 14:40 end.
    assert line.fresh_until == synced + STALE_AFTER


async def test_the_line_is_fresh_until_the_meeting_ends_when_that_comes_first(db, monkeypatch):
    await _world(
        db, monkeypatch, synced_at=T - timedelta(minutes=1), current_end=timedelta(minutes=5)
    )

    line = await now_route._agenda_line(T)

    assert line.text == "In meeting until 14:05; next 15:30"
    assert line.fresh_until == T + timedelta(minutes=5)


async def test_a_failed_calendar_read_is_unknown_keeps_the_last_value_and_never_claims_free(
    db, monkeypatch
):
    pool = await _world(
        db, monkeypatch, synced_at=T - timedelta(minutes=2), current_end=timedelta(minutes=40)
    )
    known = await now_route._agenda_line(T)
    assert known.state == "known"

    await pool.execute("ALTER TABLE calendar_event_instances RENAME TO calendar_instances_gone")
    later = T + timedelta(minutes=5)
    line = await now_route._agenda_line(later)

    assert (line.state, line.stale) == ("unknown", True)
    assert line.text == "Unknown (last known: In meeting until 14:40; next 15:30, as of 14:00)"
    assert "Free" not in line.text and "No more today" not in line.text
    assert line.fresh_until == later + STALE_AFTER
