"""Real-Postgres lifecycle coverage for the fleet entity-rebind ledger."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.db]

_PATH = Path(__file__).resolve().parents[2] / "alembic/versions/core/core_243_entity_rebind_log.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_243", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _run(pool, direction: str) -> None:
    module = _load_migration()
    statements: list[str] = []
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = statements.append
    with (
        patch.object(module, "op", mocked_op),
        patch.object(module, "preflight_runtime_attention_downgrade"),
    ):
        getattr(module, direction)()
    async with pool.acquire() as conn, conn.transaction():
        for statement in statements:
            await conn.execute(statement)


def test_entity_rebind_log_extends_current_core_head() -> None:
    migration = _load_migration()
    assert (migration.revision, migration.down_revision) == ("core_243", "core_242")


def test_entity_rebind_log_preflights_protected_downgrade_before_mutation() -> None:
    migration = _load_migration()
    calls: list[str] = []
    operation = MagicMock()
    operation.execute.side_effect = lambda _statement: calls.append("execute")

    with (
        patch.object(migration, "op", operation),
        patch.object(
            migration,
            "preflight_runtime_attention_downgrade",
            side_effect=lambda _op, _context: calls.append("preflight"),
        ) as preflight,
    ):
        migration.downgrade()

    preflight.assert_called_once_with(operation, migration.context)
    assert calls[0] == "preflight"


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


@pytest.mark.asyncio(loop_scope="session")
async def test_entity_rebind_log_install_is_concurrent_replay_safe(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(min_pool_size=2, max_pool_size=2) as pool:
        await pool.execute(
            "CREATE TABLE public.entities (id UUID PRIMARY KEY DEFAULT gen_random_uuid())"
        )

        await asyncio.gather(_run(pool, "upgrade"), _run(pool, "upgrade"))

        assert await pool.fetchval("SELECT to_regclass('public.entity_rebind_log')") == (
            "entity_rebind_log"
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'entity_rebind_log'"
            )
            == 3
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_entity_rebind_log_enforces_role_bound_cohort_and_settlement(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        for role in ("butler_relationship_rw", "butler_general_rw", "butler_finance_rw"):
            await pool.execute(
                f"DO $$ BEGIN CREATE ROLE {role}; EXCEPTION WHEN duplicate_object THEN NULL; END $$"
            )
        await pool.execute(
            "CREATE TABLE public.entities (id UUID PRIMARY KEY DEFAULT gen_random_uuid())"
        )
        await _run(pool, "upgrade")
        source = await pool.fetchval("INSERT INTO public.entities DEFAULT VALUES RETURNING id")
        target = await pool.fetchval("INSERT INTO public.entities DEFAULT VALUES RETURNING id")
        rebind = await pool.fetchval("SELECT gen_random_uuid()")

        # init-db.sql can re-widen public-table grants after migrations. Prove
        # the RLS policies and immutable-identity trigger remain authoritative
        # even under that deployment replay shape.
        await pool.execute(
            "GRANT SELECT, INSERT, UPDATE ON public.entity_rebind_log TO butler_general_rw"
        )

        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL ROLE butler_relationship_rw")
            await conn.executemany(
                """
                INSERT INTO public.entity_rebind_log (
                    rebind_id, source_entity_id, target_entity_id, target_schema
                ) VALUES ($1, $2, $3, $4)
                """,
                [(rebind, source, target, "general"), (rebind, source, target, "finance")],
            )

        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL ROLE butler_general_rw")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    """
                    INSERT INTO public.entity_rebind_log (
                        rebind_id, source_entity_id, target_entity_id, target_schema
                    ) VALUES (gen_random_uuid(), $1, $2, 'general')
                    """,
                    source,
                    target,
                )

        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL ROLE butler_general_rw")
            assert (
                await conn.execute(
                    """
                    UPDATE public.entity_rebind_log
                    SET status = 'active', completed_at = now()
                    WHERE rebind_id = $1 AND target_schema = 'general'
                    """,
                    rebind,
                )
                == "UPDATE 1"
            )
            assert (
                await conn.execute(
                    """
                    UPDATE public.entity_rebind_log
                    SET status = 'active', completed_at = now()
                    WHERE rebind_id = $1 AND target_schema = 'finance'
                    """,
                    rebind,
                )
                == "UPDATE 0"
            )
            with pytest.raises(asyncpg.RaiseError, match="identity is immutable"):
                await conn.execute(
                    """
                    UPDATE public.entity_rebind_log
                    SET source_entity_id = $2
                    WHERE rebind_id = $1 AND target_schema = 'general'
                    """,
                    rebind,
                    target,
                )
