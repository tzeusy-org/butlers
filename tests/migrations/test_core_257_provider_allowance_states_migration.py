"""Real-Postgres lifecycle coverage for core_257 provider allowance states."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.db]

_MIGRATION = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_257_provider_allowance_states.py"
)


async def _run_migration(pool: asyncpg.Pool, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("core_257", _MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements: list[str] = []
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = statements.append
    with patch.object(migration, "op", mocked_op):
        getattr(migration, direction)()
    for statement in statements:
        await pool.execute(statement)


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_is_idempotent_constrained_and_downgrade_removes_objects(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(
            "CREATE TABLE public.model_catalog "
            "(id UUID PRIMARY KEY DEFAULT gen_random_uuid(), runtime_type TEXT NOT NULL)"
        )
        await pool.execute("INSERT INTO public.model_catalog (runtime_type) VALUES ('codex')")

        await _run_migration(pool, "upgrade")
        await _run_migration(pool, "upgrade")  # the core chain replays once per butler schema

        # Existing catalog rows need no backfill: NULL means "the runtime default".
        assert (
            await pool.fetchval("SELECT allowance_account FROM public.model_catalog LIMIT 1")
            is None
        )
        await pool.execute(
            "INSERT INTO public.provider_allowance_states "
            "(account_key, state, reset_at, reset_source) "
            "VALUES ('codex', 'exhausted', now() + interval '1 hour', 'parsed')"
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "INSERT INTO public.provider_allowance_states (account_key, state) "
                "VALUES ('x', 'maybe')"
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "INSERT INTO public.provider_allowance_states (account_key, state, reset_source) "
                "VALUES ('y', 'unknown', 'guessed')"
            )

        await _run_migration(pool, "downgrade")
        assert await pool.fetchval("SELECT to_regclass('public.provider_allowance_states')") is None
        assert not await pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'model_catalog' "
            "AND column_name = 'allowance_account')"
        )
        await _run_migration(pool, "downgrade")  # idempotent
