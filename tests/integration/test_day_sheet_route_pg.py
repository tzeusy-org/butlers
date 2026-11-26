# Spec: REQ-module-calendar-036
"""GET /api/calendar/workspace/day-sheet through the app over real calendar schemas.

Two migrated databases behind the production ``DatabaseManager``, a non-UTC owner zone read from
the real settings row, and events in both schemas: the route lists exactly the owner's own time in
owner-local order, and a UTC reading of the same rows would differ. Synthetic data only.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from butlers.api.deps import get_db
from butlers.core.general_settings import GENERAL_SETTINGS_STATE_KEY
from butlers.testing.db_manager import db_manager
from tests.api.auth_helpers import create_authenticated_domain_app
from tests.integration.test_calendar_day_sheet_pg import SELF, _source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

ZONE = "Asia/Singapore"  # UTC+8: owner-local 2026-07-01 is 2026-06-30T16:00Z..2026-07-01T16:00Z


async def _event(pool, source_id, ref: str, start: datetime, metadata: dict | None = None) -> None:
    end = start + timedelta(hours=1)
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
        end,
        json.dumps(metadata or {}),
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


async def test_the_route_lists_exactly_the_owners_own_time_in_owner_local_order(db) -> None:
    general = await db("core")
    travel = await db("core")
    await general.execute(
        "INSERT INTO public.state (key, value) VALUES ($1, $2::text::jsonb)",
        GENERAL_SETTINGS_STATE_KEY,
        json.dumps({"timezone": ZONE}),
    )
    owner_g = await _source(general, "owner-g", lane="user", role="owner")
    butler_g = await _source(general, "butler-g", lane="butler", role="butler", synced=False)
    partner_g = await _source(general, "partner-g", lane="user", role="unclassified")
    owner_t = await _source(travel, "owner-t", lane="user", role="owner")

    # 00:30 owner-local on 1 July is 16:30Z on 30 June: a UTC reading would put it on 30 June.
    await _event(general, owner_g, "first-local", datetime(2026, 6, 30, 16, 30, tzinfo=UTC))
    await _event(travel, owner_t, "from-travel", datetime(2026, 7, 1, 2, 0, tzinfo=UTC))
    await _event(general, butler_g, "hold", datetime(2026, 7, 1, 6, 0, tzinfo=UTC))
    # 00:30 owner-local on 2 July is 16:30Z on 1 July: a UTC reading would keep it on 1 July.
    await _event(general, owner_g, "next-local-day", datetime(2026, 7, 1, 16, 30, tzinfo=UTC))
    await _event(general, partner_g, "partner", datetime(2026, 7, 1, 3, 0, tzinfo=UTC))
    await _event(
        travel,
        owner_t,
        "declined",
        datetime(2026, 7, 1, 4, 0, tzinfo=UTC),
        {"attendees": [{**SELF, "response_status": "declined"}]},
    )
    manager = db_manager(
        {"general": general, "travel": travel},
        modules={"general": {"calendar"}, "travel": {"calendar"}},
        credential_pool=general,
    )
    app = create_authenticated_domain_app()
    app.dependency_overrides[get_db] = lambda: manager

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/calendar/workspace/day-sheet", params={"date": "2026-07-01"}
        )

    data = response.json()["data"]
    assert response.status_code == 200
    assert data["zone"] == ZONE
    assert [(i["title"], i["kind"]) for i in data["items"]] == [
        ("Title first-local", "attended"),
        ("Title from-travel", "attended"),
        ("Title hold", "butler"),
    ]
    local_starts = [
        datetime.fromisoformat(i["start"]).astimezone(UTC).isoformat() for i in data["items"]
    ]
    assert local_starts == sorted(local_starts)
    assert data["freshness"]["state"] == "current"
    assert data["attended_count"] == 2
