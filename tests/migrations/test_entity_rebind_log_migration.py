"""Real-Postgres lifecycle coverage for the fleet entity-rebind ledger."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.entity_rebind import _process_pending_entity_rebinds_on_conn
from butlers.testing.migration import load_migration

pytestmark = [pytest.mark.integration, pytest.mark.db]

_PATH = Path(__file__).resolve().parents[2] / "alembic/versions/core/core_243_entity_rebind_log.py"
_ENTITY_INSERT = (
    "INSERT INTO public.entities (canonical_name) "
    "VALUES ('rebind ' || gen_random_uuid()) RETURNING id"
)


@asynccontextmanager
async def _pre_core_243(db, **pool_kwargs):
    """The real core_242 schema: public.entities exists, the ledger does not."""
    yield await db("core", revisions={"core": "core_242"}, **pool_kwargs)


async def _run(pool, direction: str) -> None:
    module = load_migration(_PATH, "core_243")
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
    migration = load_migration(_PATH, "core_243")
    assert (migration.revision, migration.down_revision) == ("core_243", "core_242")


def test_entity_rebind_log_preflights_protected_downgrade_before_mutation() -> None:
    migration = load_migration(_PATH, "core_243")
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
async def test_entity_rebind_log_forward_and_backward(db) -> None:
    async with _pre_core_243(db) as pool:
        await _run(pool, "upgrade")
        source = await pool.fetchval(_ENTITY_INSERT)
        target = await pool.fetchval(_ENTITY_INSERT)
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
    db,
) -> None:
    async with _pre_core_243(db, min_size=2, max_size=2) as pool:
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
async def test_a_whole_retry_runs_under_a_non_relationship_runtime_role(db) -> None:
    """Sweep, survivor lock, moves and receipt under the role production uses (bu-dhloi1.52)."""
    pool = await db("core", "memory", schemas={"core": "general", "memory": "general"})
    source = await pool.fetchval(_ENTITY_INSERT)
    target = await pool.fetchval(_ENTITY_INSERT)
    survivor = await pool.fetchval(_ENTITY_INSERT)
    await pool.execute(
        "UPDATE public.entities SET metadata = metadata || jsonb_build_object("
        "'merged_into', $2::text) WHERE id = $1",
        target,
        str(survivor),
    )
    rebind = await pool.fetchval("SELECT gen_random_uuid()")
    await pool.execute(
        """
        INSERT INTO public.entity_rebind_log (
            rebind_id, source_entity_id, target_entity_id, target_schema,
            status, error_class, completed_at, updated_at
        ) VALUES ($1, $2, $3, 'general', 'failed', 'RuntimeError', now(),
                  now() - interval '1 day')
        """,
        rebind,
        source,
        target,
    )

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL search_path TO general, public")
        fact = await conn.fetchval(
            "INSERT INTO facts (subject, entity_id, predicate, content) "
            "VALUES ('opaque-subject', $1, 'note', 'opaque') RETURNING id",
            source,
        )
        await conn.execute("SET LOCAL ROLE butler_general_rw")
        receipts = await _process_pending_entity_rebinds_on_conn(conn, target_schema="general")
        moved = await conn.fetchval("SELECT entity_id FROM facts WHERE id = $1", fact)

    assert [(r.status, r.error_class, r.references_rebound) for r in receipts] == [
        ("active", None, 1)
    ]
    assert moved == survivor


@pytest.mark.asyncio(loop_scope="session")
async def test_entity_rebind_log_enforces_role_bound_cohort_and_settlement(
    db,
) -> None:
    # The real bootstrap provisions butler_{relationship,general,finance}_rw.
    async with _pre_core_243(db) as pool:
        await _run(pool, "upgrade")
        source = await pool.fetchval(_ENTITY_INSERT)
        target = await pool.fetchval(_ENTITY_INSERT)
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
