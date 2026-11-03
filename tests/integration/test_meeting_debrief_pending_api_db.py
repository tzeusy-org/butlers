"""Real-Postgres check that the pending-debriefs endpoint matches the tool (bu-q7vx1q.63).

Docker-gated, so it runs in CI only. Seeds ``relationship.meeting_debriefs`` directly with an
active attendee, a non-active one and an unresolved guest, then compares the endpoint body with
``meeting_debrief_pending`` on the same pool: same items, same order, same posture filtering.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.tools.relationship.meeting_debrief import meeting_debrief_pending
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def debrief_pool(db) -> asyncpg.Pool:
    p = await db("core", "memory", "relationship", schema="relationship", max_size=1)
    for table in ("public.entities", "relationship.meeting_debriefs"):
        await p.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608
    return p


async def _entity(pool: asyncpg.Pool, name: str, posture: str) -> uuid.UUID:
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, 'person') "
        "RETURNING id",
        name,
    )
    if posture != "active":
        await pool.execute(
            "UPDATE public.entities SET posture = $2 WHERE id = $1", entity_id, posture
        )
    return entity_id


async def _debrief(
    pool: asyncpg.Pool, title: str, attendees: list[dict], *, prompted_at: datetime, position: int
) -> None:
    await pool.execute(
        """
        INSERT INTO relationship.meeting_debriefs
            (event_id, occurrence_start, occurrence_end, event_title, attendees,
             state, batch_position, prompted_at)
        VALUES ($1, $2, $3, $4, $5::jsonb, 'pending', $6, $7)
        """,
        uuid.uuid4(),
        prompted_at - timedelta(hours=2),
        prompted_at - timedelta(hours=1),
        title,
        json.dumps(attendees),
        position,
        prompted_at,
    )


async def test_endpoint_matches_the_tool_including_posture_filtering(
    debrief_pool: asyncpg.Pool,
) -> None:
    active = await _entity(debrief_pool, "Sam Example", "active")
    quiet = await _entity(debrief_pool, "Quiet Example", "quiet")
    now = datetime.now(UTC)
    await _debrief(
        debrief_pool,
        "Older sync",
        [{"entity_id": str(active), "name": "Sam Example", "email": "sam@example.test"}],
        prompted_at=now - timedelta(days=1),
        position=1,
    )
    await _debrief(
        debrief_pool,
        "Newer sync",
        [
            {"entity_id": str(active), "name": "Sam Example", "email": "sam@example.test"},
            {"entity_id": str(quiet), "name": "Quiet Example", "email": "d@example.test"},
            {"entity_id": None, "name": "Guest", "email": "guest@example.test"},
        ],
        prompted_at=now,
        position=1,
    )
    await _debrief(
        debrief_pool,
        "Only quiet",
        [{"entity_id": str(quiet), "name": "Quiet Example", "email": "d@example.test"}],
        prompted_at=now - timedelta(hours=1),
        position=2,
    )

    expected = await meeting_debrief_pending(debrief_pool)

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = debrief_pool
    app = create_app()
    for butler_name, _router_module in app.state.butler_routers:
        if butler_name == "relationship":
            app.dependency_overrides[get_db] = lambda: db
            break
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/relationship/meeting-debriefs/pending")

    assert response.status_code == 200
    body = response.json()
    assert body["debriefs_available"] is True
    assert body["debriefs"] == expected["debriefs"]
    assert [d["event_title"] for d in body["debriefs"]] == ["Newer sync", "Older sync"]
    newer = body["debriefs"][0]
    assert [a["name"] for a in newer["attendees"]] == ["Sam Example", "Guest"]
