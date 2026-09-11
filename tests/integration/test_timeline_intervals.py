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
from butlers.api.routers.notifications import _claim_failed_notification
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
    success: bool | None = True,
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


async def _seed_notification(
    pool: asyncpg.Pool,
    *,
    created_at: datetime,
    status: str,
    trace_id: str | None = None,
):
    notification_id = uuid4()
    await pool.execute(
        """
        INSERT INTO notifications (
            id, source_butler, channel, recipient, message, status, trace_id, created_at
        ) VALUES ($1, 'switchboard', 'telegram', 'owner', 'fixture notification', $2, $3, $4)
        """,
        notification_id,
        status,
        trace_id,
        created_at,
    )
    return notification_id


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


async def test_attention_counts_current_failures_caps_rows_and_excludes_incomplete_statuses(
    timeline_db,
):
    manager, pool = timeline_db
    now = datetime.now(tz=UTC)
    failed_sessions = [
        await _seed_session(pool, started_at=now - timedelta(minutes=index), success=False)
        for index in range(6)
    ]
    pending_session = await _seed_session(pool, started_at=now, success=None)
    successful_session = await _seed_session(pool, started_at=now, success=True)
    failed_notifications = [
        await _seed_notification(pool, created_at=now - timedelta(minutes=index), status="failed")
        for index in range(2)
    ]
    sent_notification = await _seed_notification(pool, created_at=now, status="sent")
    old_failure = await _seed_notification(
        pool, created_at=now - timedelta(hours=25), status="failed"
    )

    response = await _request(manager, "/api/timeline/attention", {"butler": "switchboard"})

    assert response.status_code == 200
    body = response.json()
    meta = body["meta"]
    assert (meta["failed_sessions"], meta["failed_notifications"], meta["total"]) == (6, 2, 8)
    assert meta["has_more"] is True
    assert (meta["expected_sources"], meta["healthy_sources"], meta["availability"]) == (
        2,
        2,
        "complete",
    )
    assert len(body["data"]) == 5
    returned_ids = {item["id"] for item in body["data"]}
    assert returned_ids <= {str(value) for value in [*failed_sessions, *failed_notifications]}
    assert str(pending_session) not in returned_ids
    assert str(successful_session) not in returned_ids
    assert str(sent_notification) not in returned_ids
    assert str(old_failure) not in returned_ids
    assert all(set(item) == {"id", "kind", "butler", "timestamp"} for item in body["data"])

    acknowledged = failed_notifications[0]
    await pool.execute("UPDATE notifications SET status = 'read' WHERE id = $1", acknowledged)
    after_ack = await _request(manager, "/api/timeline/attention", {"butler": "switchboard"})
    assert after_ack.json()["meta"]["failed_notifications"] == 1
    assert str(acknowledged) not in {item["id"] for item in after_ack.json()["data"]}

    claimed = await _claim_failed_notification(pool, failed_notifications[1])
    assert claimed is not None
    after_claim = await _request(manager, "/api/timeline/attention", {"butler": "switchboard"})
    assert after_claim.json()["meta"]["failed_notifications"] == 0
    assert str(failed_notifications[1]) not in {item["id"] for item in after_claim.json()["data"]}
