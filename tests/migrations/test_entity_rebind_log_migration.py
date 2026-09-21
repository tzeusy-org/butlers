"""Real-Postgres lifecycle coverage for the fleet entity-rebind ledger."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.db]

_PATH = Path(__file__).resolve().parents[2] / "alembic/versions/core/core_242_entity_rebind_log.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_242", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _run(pool, direction: str) -> None:
    module = _load_migration()
    statements: list[str] = []
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = statements.append
    with patch.object(module, "op", mocked_op):
        getattr(module, direction)()
    for statement in statements:
        await pool.execute(statement)


def test_entity_rebind_log_extends_current_core_head() -> None:
    migration = _load_migration()
    assert (migration.revision, migration.down_revision) == ("core_242", "core_241")


@pytest.mark.asyncio(loop_scope="session")
async def test_entity_rebind_log_forward_and_backward(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(
            "CREATE TABLE public.entities (id UUID PRIMARY KEY DEFAULT gen_random_uuid())"
        )
        await _run(pool, "upgrade")
        source = await pool.fetchval("INSERT INTO public.entities DEFAULT VALUES RETURNING id")
        target = await pool.fetchval("INSERT INTO public.entities DEFAULT VALUES RETURNING id")
        rebind = await pool.fetchval("SELECT gen_random_uuid()")
        await pool.execute(
            """
            INSERT INTO public.entity_rebind_log (
                rebind_id, source_entity_id, target_entity_id, target_schema
            ) VALUES ($1, $2, $3, 'finance')
            """,
            rebind,
            source,
            target,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                UPDATE public.entity_rebind_log
                SET status = 'failed', completed_at = now(), error_class = NULL
                WHERE rebind_id = $1 AND target_schema = 'finance'
                """,
                rebind,
            )

        with pytest.raises(asyncpg.RaiseError, match="entity rebind receipts exist"):
            await _run(pool, "downgrade")
        await pool.execute("DELETE FROM public.entity_rebind_log")
        await _run(pool, "downgrade")
        assert await pool.fetchval("SELECT to_regclass('public.entity_rebind_log')") is None
