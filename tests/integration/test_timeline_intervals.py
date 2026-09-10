"""Real-Postgres coverage for Timeline interval counting and pagination."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.timeline import _get_db_manager
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

SINCE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
UNTIL = SINCE + timedelta(hours=1)


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
    )


@pytest.fixture
async def timeline_db(migrated_db_url: str):
    parsed = urlparse(migrated_db_url)
    manager = DatabaseManager(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "postgres",
        ssl="disable",
    )
    await manager.add_butler(
        "switchboard",
        db_name=parsed.path.lstrip("/"),
        db_schema="switchboard",
    )
    pool = manager.pool("switchboard")
    await pool.execute("TRUNCATE TABLE sessions, notifications CASCADE")
    try:
        yield manager, pool
    finally:
        await manager.close()


async def _request(manager: DatabaseManager, path: str, params):
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: manager
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, params=params)


async def _seed_session(
    pool: asyncpg.Pool,
    *,
    started_at: datetime,
    success: bool = True,
    trace_id: str | None = None,
):
    event_id = uuid4()
    await pool.execute(
        """
        INSERT INTO sessions (id, prompt, trigger_source, success, trace_id, request_id, started_at)
        VALUES ($1, 'fixture', 'route', $2, $3, $4, $5)
        """,
        event_id,
        success,
        trace_id,
        str(uuid4()),
        started_at,
    )
    return event_id


async def test_histogram_counts_beyond_head_page_and_interval_cursor_is_lossless(timeline_db):
    manager, pool = timeline_db
    shared_timestamp = SINCE + timedelta(minutes=17)
    inserted_ids = {
        await _seed_session(pool, started_at=shared_timestamp, trace_id="busy-minute")
        for _ in range(61)
    }
    await _seed_session(pool, started_at=UNTIL, trace_id="busy-minute")
    params = {
        "since": SINCE.isoformat(),
        "until": UNTIL.isoformat(),
        "event_type": "session",
        "trace": "busy-minute",
    }

    histogram = await _request(manager, "/api/timeline/histogram", params)
    assert histogram.status_code == 200
    assert sum(bucket["count"] for bucket in histogram.json()["data"]) == 61
    assert max(bucket["count"] for bucket in histogram.json()["data"]) == 61

    first = await _request(manager, "/api/timeline", {**params, "limit": 50})
    second = await _request(
        manager,
        "/api/timeline",
        {**params, "limit": 50, "before": first.json()["meta"]["cursor"]},
    )
    returned_ids = {event["id"] for event in first.json()["data"] + second.json()["data"]}
    assert returned_ids == {str(event_id) for event_id in inserted_ids}


async def test_histogram_and_list_share_error_butler_trace_and_boundary_predicates(timeline_db):
    manager, pool = timeline_db
    matching_session = await _seed_session(
        pool,
        started_at=SINCE,
        success=False,
        trace_id="matching-trace",
    )
    await _seed_session(
        pool,
        started_at=SINCE + timedelta(minutes=1),
        success=True,
        trace_id="matching-trace",
    )
    notification_id = uuid4()
    await pool.execute(
        """
        INSERT INTO notifications (
            id, source_butler, channel, recipient, message, status, trace_id, created_at
        ) VALUES ($1, 'switchboard', 'telegram', 'owner', 'content stays out of aggregate',
                  'failed', 'matching-trace', $2)
        """,
        notification_id,
        SINCE + timedelta(minutes=2),
    )
    params = {
        "since": SINCE.isoformat(),
        "until": UNTIL.isoformat(),
        "event_type": "error",
        "butler": "switchboard",
        "trace": "matching-trace",
    }

    histogram = await _request(manager, "/api/timeline/histogram", params)
    events = await _request(manager, "/api/timeline", params)

    assert histogram.status_code == events.status_code == 200
    assert sum(bucket["count"] for bucket in histogram.json()["data"]) == 2
    assert {event["id"] for event in events.json()["data"]} == {
        str(matching_session),
        str(notification_id),
    }
    assert histogram.json()["meta"]["availability"] == "complete"
    assert histogram.json()["meta"]["expected_sources"] == 2
