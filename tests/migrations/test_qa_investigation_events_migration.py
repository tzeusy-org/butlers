"""Regression tests for core_091 qa_investigation_events migration."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_091_qa_investigation_events.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_091", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
async def qa_investigation_events_pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as pool:
        await pool.execute("""
            CREATE TABLE public.healing_attempts (
                id UUID PRIMARY KEY
            )
        """)
        await pool.execute("""
            CREATE TABLE public.qa_findings (
                id UUID PRIMARY KEY
            )
        """)
        yield pool


async def _run_upgrade(pool: asyncpg.Pool) -> None:
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    for sql in sqls:
        await pool.execute(sql)


def test_migration_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "core_091"
    assert mod.down_revision == "core_090"


@pytest.mark.asyncio(loop_scope="session")
async def test_qa_investigation_events_rejects_unknown_step(
    qa_investigation_events_pool: asyncpg.Pool,
) -> None:
    pool = qa_investigation_events_pool
    await _run_upgrade(pool)

    attempt_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO public.healing_attempts (id) VALUES ($1)",
        attempt_id,
    )

    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO public.qa_investigation_events (id, attempt_id, step, text)
            VALUES ($1, $2, $3, $4)
            """,
            uuid.uuid4(),
            attempt_id,
            "unknown",
            "unexpected investigation step",
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_healing_attempt_cascades_investigation_events(
    qa_investigation_events_pool: asyncpg.Pool,
) -> None:
    pool = qa_investigation_events_pool
    await _run_upgrade(pool)

    attempt_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO public.healing_attempts (id) VALUES ($1)",
        attempt_id,
    )
    await pool.execute(
        """
        INSERT INTO public.qa_investigation_events (id, attempt_id, step, text)
        VALUES ($1, $2, $3, $4)
        """,
        uuid.uuid4(),
        attempt_id,
        "flagged",
        "finding was flagged for investigation",
    )

    before_delete = await pool.fetchval(
        "SELECT COUNT(*) FROM public.qa_investigation_events WHERE attempt_id = $1",
        attempt_id,
    )
    assert before_delete == 1

    await pool.execute("DELETE FROM public.healing_attempts WHERE id = $1", attempt_id)

    after_delete = await pool.fetchval(
        "SELECT COUNT(*) FROM public.qa_investigation_events WHERE attempt_id = $1",
        attempt_id,
    )
    assert after_delete == 0
