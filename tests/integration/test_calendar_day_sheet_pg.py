# Spec: REQ-module-calendar-036
"""The day sheet over real calendar schemas (bu-5ii4gj.2 S2).

Two migrated calendar databases behind the production ``DatabaseManager``: the owner-time rule is
applied to real rows, and a schema whose read fails makes the sheet unreadable rather than clear.
Synthetic data only.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.api.read_models.calendar_day_sheet import query_calendar_day_sheet
from butlers.core.temporal.day_sheet import render_day_sheet_text
from butlers.testing.db_manager import db_manager

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

DAY = date(2026, 7, 1)
NINE = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
SELF = {"self": True, "response_status": "accepted"}


async def _source(pool, key: str, *, lane: str, role: str, synced: bool = True) -> str:
    source_id = await pool.fetchval(
        "INSERT INTO calendar_sources (source_key, source_kind, lane, time_role) "
        "VALUES ($1, 'provider_event', $2, $3) RETURNING id",
        key,
        lane,
        role,
    )
    if synced:
        await pool.execute(
            "INSERT INTO calendar_sync_cursors (source_id, cursor_name, last_synced_at, "
            "last_success_at) VALUES ($1, 'main', now(), now())",
            source_id,
        )
    return source_id


async def _event(pool, source_id, ref: str, hour: int, metadata: dict | None = None) -> None:
    start = NINE + timedelta(hours=hour - 9)
    event_id = await pool.fetchval(
        """
        INSERT INTO calendar_events
            (source_id, origin_ref, title, timezone, starts_at, ends_at, status, metadata,
             source_butler)
        VALUES ($1, $2, $3, 'UTC', $4, $5, 'confirmed', $6::text::jsonb, 'general')
        RETURNING id
        """,
        source_id,
        ref,
        f"Title {ref}",
        start,
        start + timedelta(hours=1),
        json.dumps(metadata or {}),
    )
    await pool.execute(
        "INSERT INTO calendar_event_instances (event_id, source_id, origin_instance_ref, "
        "timezone, starts_at, ends_at) VALUES ($1, $2, $3, 'UTC', $4, $5)",
        event_id,
        source_id,
        f"{ref}:0",
        start,
        start + timedelta(hours=1),
    )


async def test_the_owner_time_rule_applies_to_real_rows(db) -> None:
    general = await db("core")
    travel = await db("core")
    owner = await _source(general, "owner", lane="user", role="owner")
    partner = await _source(general, "partner", lane="user", role="unclassified")
    butler = await _source(general, "butler", lane="butler", role="butler", synced=False)
    await _source(travel, "travel-owner", lane="user", role="owner")

    await _event(general, owner, "accepted", 10)
    await _event(
        general,
        owner,
        "declined",
        11,
        {"attendees": [{**SELF, "response_status": "declined"}]},
    )
    await _event(general, owner, "free", 12, {"transparency": "transparent"})
    await _event(general, butler, "hold", 13)
    await _event(general, partner, "partner", 14)

    manager = db_manager(
        {"general": general, "travel": travel},
        modules={"general": {"calendar"}, "travel": {"calendar"}},
    )
    sheet = await query_calendar_day_sheet(manager, local_date=DAY, zone=ZoneInfo("UTC"))

    assert [(i.title, i.kind) for i in sheet.items] == [
        ("Title accepted", "attended"),
        ("Title hold", "butler"),
    ]
    assert sheet.freshness.state == "current"


async def test_an_unreadable_schema_makes_the_sheet_unreadable_not_clear(db) -> None:
    general = await db("core")
    travel = await db("core")
    for pool, key in ((general, "g"), (travel, "t")):
        await _source(pool, key, lane="user", role="owner")
    manager = db_manager(
        {"general": general, "travel": travel},
        modules={"general": {"calendar"}, "travel": {"calendar"}},
    )
    await travel.close()  # the read grant is gone: this schema cannot be read

    sheet = await query_calendar_day_sheet(manager, local_date=DAY, zone=ZoneInfo("UTC"))
    text = render_day_sheet_text(sheet)

    assert sheet.freshness.state == "unreadable"
    assert "unreadable" in text
    assert "Nothing on your calendar" not in text
