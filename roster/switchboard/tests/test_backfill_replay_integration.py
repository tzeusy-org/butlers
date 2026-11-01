"""Real-PG: connector backfill calls survive CachedMCPClient's reconnect-retry.

``CachedMCPClient`` resends a call once after a transport drop, so the server may
see the same ``backfill.progress`` or ``backfill.poll`` twice after it already
applied the first (bu-f4iw1n). Progress must not double-count its deltas, and a
poll whose response was lost must hand the same connector its active job back.
"""

from __future__ import annotations

import importlib
import shutil
import uuid

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

# butlers.tools.* is an import-time alias of roster/*/tools that pyright cannot resolve.
_connector = importlib.import_module("butlers.tools.switchboard.backfill.connector")
backfill_poll = _connector.backfill_poll
backfill_progress = _connector.backfill_progress

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_TYPE = "gmail"
_IDENTITY = "gmail:user:replay@example.com"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "switchboard"]
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=2, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE backfill_jobs")
    yield p
    await p.close()


async def _insert_job(pool: asyncpg.Pool, *, status: str, cap: int = 500) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO backfill_jobs
            (connector_type, endpoint_identity, date_from, date_to, status,
             daily_cost_cap_cents)
        VALUES ($1, $2, DATE '2020-01-01', DATE '2020-12-31', $3, $4)
        RETURNING id
        """,
        _TYPE,
        _IDENTITY,
        status,
        cap,
    )


async def _progress(pool: asyncpg.Pool, job_id: uuid.UUID, token: str | None, **kw):
    return await backfill_progress(
        pool,
        job_id=job_id,
        connector_type=_TYPE,
        endpoint_identity=_IDENTITY,
        rows_processed=kw.get("rows_processed", 10),
        rows_skipped=kw.get("rows_skipped", 2),
        cost_spent_cents_delta=kw.get("cost", 30),
        cursor={"page_token": "p1"},
        progress_token=token,
    )


async def test_replayed_progress_applies_once_and_answers_the_same(pool):
    job_id = await _insert_job(pool, status="active", cap=50)

    first = await _progress(pool, job_id, "tok-1")
    replay = await _progress(pool, job_id, "tok-1")
    assert first == replay == {"status": "active"}

    row = await pool.fetchrow(
        "SELECT rows_processed, rows_skipped, cost_spent_cents, status "
        "FROM backfill_jobs WHERE id = $1",
        job_id,
    )
    # Applied once: the replay would otherwise reach the 50c cap (30 + 30).
    assert dict(row) == {
        "rows_processed": 10,
        "rows_skipped": 2,
        "cost_spent_cents": 30,
        "status": "active",
    }

    # A fresh token is a new batch and applies normally (and here caps the job).
    assert await _progress(pool, job_id, "tok-2") == {"status": "cost_capped"}
    assert await _progress(pool, job_id, "tok-2") == {"status": "cost_capped"}
    assert (
        await pool.fetchval("SELECT cost_spent_cents FROM backfill_jobs WHERE id = $1", job_id)
        == 60
    )


async def test_tokenless_progress_keeps_additive_semantics(pool):
    job_id = await _insert_job(pool, status="active")
    await _progress(pool, job_id, None)
    await _progress(pool, job_id, None)
    assert (
        await pool.fetchval("SELECT rows_processed FROM backfill_jobs WHERE id = $1", job_id) == 20
    )


async def test_progress_does_not_resurrect_a_job_paused_mid_call(pool):
    job_id = await _insert_job(pool, status="paused")
    assert await _progress(pool, job_id, "tok-1") == {"status": "paused"}
    assert (
        await pool.fetchval("SELECT rows_processed FROM backfill_jobs WHERE id = $1", job_id) == 0
    )


async def test_poll_is_reentrant_for_the_same_connector(pool):
    older = await _insert_job(pool, status="pending")
    newer = await _insert_job(pool, status="pending")

    claimed = await backfill_poll(pool, connector_type=_TYPE, endpoint_identity=_IDENTITY)
    assert claimed is not None and claimed["job_id"] == str(older)
    started_at = await pool.fetchval("SELECT started_at FROM backfill_jobs WHERE id = $1", older)

    # The response was lost and the connector polls again: it gets the job it
    # already owns back (resume from its cursor), not the next pending job.
    await pool.execute(
        'UPDATE backfill_jobs SET cursor = \'{"page_token": "p9"}\'::jsonb WHERE id = $1',
        older,
    )
    again = await backfill_poll(pool, connector_type=_TYPE, endpoint_identity=_IDENTITY)
    assert again is not None and again["job_id"] == str(older)
    assert again["cursor"] == {"page_token": "p9"}

    statuses = dict(await pool.fetch("SELECT id, status FROM backfill_jobs"))
    assert statuses == {older: "active", newer: "pending"}
    assert (
        await pool.fetchval("SELECT started_at FROM backfill_jobs WHERE id = $1", older)
        == started_at
    )

    # Another connector identity never inherits this one's active job.
    assert (
        await backfill_poll(pool, connector_type=_TYPE, endpoint_identity="gmail:user:other")
        is None
    )
