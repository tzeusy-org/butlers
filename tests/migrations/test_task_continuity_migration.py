"""Real-Postgres contract for the shared task-continuity ledger (bu-2jtfw.13)."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic/versions/core/core_229_task_continuity_ledger.py"
)


def _load_migration(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _run_migration(pool, path: Path, direction: str) -> None:
    statements: list[str] = []
    module = _load_migration(path)
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = statements.append
    with patch.object(module, "op", mocked_op):
        getattr(module, direction)()
    for statement in statements:
        await pool.execute(statement)


@pytest.mark.asyncio(loop_scope="session")
async def test_migration_creates_indexes_rls_and_columns(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        # sessions and scheduled_tasks are core-chain tables assumed present in
        # the target schema; the migration's ADD COLUMN IF NOT EXISTS needs them.
        await pool.execute(
            "CREATE TABLE IF NOT EXISTS sessions (id UUID PRIMARY KEY DEFAULT gen_random_uuid())"
        )
        await pool.execute(
            "CREATE TABLE IF NOT EXISTS scheduled_tasks (id UUID PRIMARY KEY DEFAULT gen_random_uuid())"
        )

        await _run_migration(pool, _MIGRATION_PATH, "upgrade")

        assert await pool.fetchval("SELECT to_regclass('public.task_continuity')") is not None

        indexes = await pool.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'task_continuity'"
        )
        index_names = {r["indexname"] for r in indexes}
        assert "ux_task_continuity_session" in index_names
        assert "ux_task_continuity_live" in index_names

        policies = await pool.fetchval(
            "SELECT count(*) FROM pg_policy WHERE polrelid = 'public.task_continuity'::regclass"
        )
        assert policies == 3

        assert await pool.fetchval(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'sessions' AND column_name = 'continuation_of_session_id'"
        )
        assert await pool.fetchval(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'scheduled_tasks' AND column_name = 'continuity'"
        )

        # Two live rows for the same (butler, task) is rejected by the partial
        # unique index -- the hard backstop the record_carry_forward
        # transaction relies on.
        session_a, session_b = uuid.uuid4(), uuid.uuid4()
        await pool.execute(
            "INSERT INTO public.task_continuity "
            "(butler_name, task_name, session_id, carry_forward, is_live) "
            "VALUES ('health', 'daily', $1, 'a', TRUE)",
            session_a,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                "INSERT INTO public.task_continuity "
                "(butler_name, task_name, session_id, carry_forward, is_live) "
                "VALUES ('health', 'daily', $1, 'b', TRUE)",
                session_b,
            )

        # Same session_id twice is rejected by the session-scoped unique index
        # unless upserted (ON CONFLICT) -- proving the dedup key exists.
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                "INSERT INTO public.task_continuity "
                "(butler_name, task_name, session_id, carry_forward, is_live) "
                "VALUES ('health', 'daily', $1, 'a-again', FALSE)",
                session_a,
            )

        await _run_migration(pool, _MIGRATION_PATH, "downgrade")
        assert await pool.fetchval("SELECT to_regclass('public.task_continuity')") is None
        assert not await pool.fetchval(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'sessions' AND column_name = 'continuation_of_session_id'"
        )
        assert not await pool.fetchval(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'scheduled_tasks' AND column_name = 'continuity'"
        )
