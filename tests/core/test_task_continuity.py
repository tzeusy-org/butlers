"""bu-2jtfw.13: ``public.task_continuity`` ledger primitive — real Postgres semantics.

Covers:
- AC9: calling ``carry_forward`` twice in one session updates the same row
  (dedup), never appending a duplicate.
- The live-row supersession contract: a new session's carry_forward archives
  the previous session's live row rather than leaving two live rows.
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.core.task_continuity import fetch_live_carry_forward, record_carry_forward
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]
_asyncio_session = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
    )
    # public.task_continuity has RLS FORCEd with no DELETE policy (matching
    # public.expected_signals' precedent — an append/upsert-only ledger), and
    # this pool's login is an ordinary non-superuser role, so a cleanup DELETE
    # here would silently no-op. Tests use unique task names instead of
    # relying on row cleanup between tests.
    yield p
    await p.close()


@_asyncio_session
async def test_carry_forward_twice_in_one_session_updates_the_same_row(pool) -> None:
    task_name = "dedup-review"
    session_id = uuid.uuid4()

    id1 = await record_carry_forward(
        pool,
        butler_name="health",
        task_name=task_name,
        session_id=session_id,
        content="First draft.",
    )
    id2 = await record_carry_forward(
        pool,
        butler_name="health",
        task_name=task_name,
        session_id=session_id,
        content="Final content.",
    )

    assert id1 == id2

    rows = await pool.fetch(
        "SELECT carry_forward FROM public.task_continuity "
        "WHERE butler_name = $1 AND task_name = $2",
        "health",
        task_name,
    )
    assert len(rows) == 1
    assert rows[0]["carry_forward"] == "Final content."


@_asyncio_session
async def test_new_session_supersedes_previous_live_row(pool) -> None:
    task_name = "supersession-review"
    session_1 = uuid.uuid4()
    session_2 = uuid.uuid4()

    await record_carry_forward(
        pool,
        butler_name="health",
        task_name=task_name,
        session_id=session_1,
        content="Run 1 content.",
    )
    await record_carry_forward(
        pool,
        butler_name="health",
        task_name=task_name,
        session_id=session_2,
        content="Run 2 content.",
    )

    live_rows = await pool.fetch(
        "SELECT session_id, carry_forward FROM public.task_continuity "
        "WHERE butler_name = $1 AND task_name = $2 AND is_live",
        "health",
        task_name,
    )
    assert len(live_rows) == 1
    assert live_rows[0]["session_id"] == session_2
    assert live_rows[0]["carry_forward"] == "Run 2 content."

    all_rows = await pool.fetch(
        "SELECT session_id FROM public.task_continuity WHERE butler_name = $1 AND task_name = $2",
        "health",
        task_name,
    )
    assert {r["session_id"] for r in all_rows} == {session_1, session_2}

    live = await fetch_live_carry_forward(pool, butler_name="health", task_name=task_name)
    assert live is not None
    assert live["carry_forward"] == "Run 2 content."


@_asyncio_session
async def test_carry_forward_is_scoped_per_butler_and_task(pool) -> None:
    task_name = "scoped-review"
    await record_carry_forward(
        pool,
        butler_name="health",
        task_name=task_name,
        session_id=uuid.uuid4(),
        content="health content",
    )
    await record_carry_forward(
        pool,
        butler_name="finance",
        task_name=task_name,
        session_id=uuid.uuid4(),
        content="finance content",
    )

    health_live = await fetch_live_carry_forward(pool, butler_name="health", task_name=task_name)
    finance_live = await fetch_live_carry_forward(pool, butler_name="finance", task_name=task_name)
    assert health_live["carry_forward"] == "health content"
    assert finance_live["carry_forward"] == "finance content"
