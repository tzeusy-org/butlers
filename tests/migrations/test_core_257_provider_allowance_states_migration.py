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


@pytest.mark.asyncio(loop_scope="session")
async def test_runtime_roles_get_select_insert_update_only_and_public_gets_nothing(
    provisioned_postgres_pool,
) -> None:
    """Allowance state is evidence: runtime roles never DELETE it, PUBLIC never sees it."""
    spec = importlib.util.spec_from_file_location("core_257_grants", _MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    async with provisioned_postgres_pool() as pool:
        await pool.execute(
            "CREATE TABLE public.model_catalog "
            "(id UUID PRIMARY KEY DEFAULT gen_random_uuid(), runtime_type TEXT NOT NULL)"
        )
        for role in migration._RUNTIME_ROLES:
            await pool.execute(
                "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '"
                + role
                + "') THEN CREATE ROLE "
                + role
                + " NOLOGIN; END IF; END $$"
            )
        await _run_migration(pool, "upgrade")

        for role in migration._RUNTIME_ROLES:
            for privilege, expected in (
                ("SELECT", True),
                ("INSERT", True),
                ("UPDATE", True),
                ("DELETE", False),
                ("TRUNCATE", False),
                ("REFERENCES", False),
                ("TRIGGER", False),
            ):
                assert (
                    await pool.fetchval(
                        "SELECT has_table_privilege($1, 'public.provider_allowance_states', $2)",
                        role,
                        privilege,
                    )
                    is expected
                ), (role, privilege)

        public_grants = await pool.fetch(
            "SELECT a.privilege_type FROM pg_class c, "
            "LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) a "
            "WHERE c.oid = 'public.provider_allowance_states'::regclass AND a.grantee = 0"
        )
        assert public_grants == []
