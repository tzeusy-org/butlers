"""Regression tests for core_147 model_catalog.complexity_tier DEFAULT fix.

core_004 set ``complexity_tier`` DEFAULT to ``'medium'``; core_093 renamed the
tier vocabulary and replaced the CHECK with the canonical six but left the
column DEFAULT pointing at the now-invalid ``'medium'``.  A bare INSERT that
omits ``complexity_tier`` therefore fell back to ``'medium'`` and violated the
post-core_093 CHECK.

These tests reproduce that violation against a real Postgres pool and prove that
after the core_147 upgrade a bare INSERT succeeds with the valid ``'workhorse'``
default.  Downgrade restores the prior ``'medium'`` default.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = pytest.mark.integration

_NEW_CHECK = "('reasoning', 'workhorse', 'cheap', 'specialty', 'local', 'legacy')"

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_147_model_catalog_complexity_tier_default.py"
)


# ---------------------------------------------------------------------------
# Module loader / replay helpers
# ---------------------------------------------------------------------------


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_147", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _replay(pool: asyncpg.Pool, direction: str) -> None:
    """Replay the migration's upgrade()/downgrade() SQL against the live pool."""
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, direction)()
    for sql in sqls:
        await pool.execute(sql)


async def _create_post_core_093_model_catalog(pool: asyncpg.Pool) -> None:
    """Materialise model_catalog in its post-core_093 state: stale DEFAULT + new CHECK.

    This is the exact bug state on main before core_147 -- column DEFAULT is the
    legacy ``'medium'`` while the CHECK only accepts the canonical six.
    """
    await pool.execute(f"""
        CREATE TABLE public.model_catalog (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alias           TEXT NOT NULL UNIQUE,
            runtime_type    TEXT NOT NULL DEFAULT 'codex',
            model_id        TEXT NOT NULL DEFAULT 'dummy',
            extra_args      JSONB NOT NULL DEFAULT '[]'::jsonb,
            complexity_tier TEXT NOT NULL DEFAULT 'medium',
            enabled         BOOLEAN NOT NULL DEFAULT true,
            priority        INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_model_catalog_complexity_tier
                CHECK (complexity_tier IN {_NEW_CHECK})
        )
    """)


async def _column_default(pool: asyncpg.Pool) -> str:
    return await pool.fetchval(
        "SELECT column_default FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = 'model_catalog'"
        "   AND column_name = 'complexity_tier'"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def migration_pool(provisioned_postgres_pool):
    """Fresh DB with model_catalog in its buggy post-core_093 state."""
    async with provisioned_postgres_pool() as pool:
        await _create_post_core_093_model_catalog(pool)
        yield pool


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------


def test_migration_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "core_147"
    assert mod.down_revision == "core_146"


# ---------------------------------------------------------------------------
# Bug reproduction + fix (real Postgres)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_bare_insert_fails_before_migration(migration_pool: asyncpg.Pool) -> None:
    """Reproduce the bug: a bare INSERT (no complexity_tier) violates the CHECK."""
    pool = migration_pool
    assert (await _column_default(pool)).startswith("'medium'")

    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO public.model_catalog (alias, runtime_type, model_id)"
            " VALUES ('bare-before', 'codex', 'test-model')"
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_bare_insert_succeeds_after_upgrade(migration_pool: asyncpg.Pool) -> None:
    """After core_147, a bare INSERT uses the valid 'workhorse' default and succeeds."""
    pool = migration_pool
    await _replay(pool, "upgrade")

    assert (await _column_default(pool)).startswith("'workhorse'")

    row_id = await pool.fetchval(
        "INSERT INTO public.model_catalog (alias, runtime_type, model_id)"
        " VALUES ('bare-after', 'codex', 'test-model') RETURNING id"
    )
    assert row_id is not None

    tier = await pool.fetchval(
        "SELECT complexity_tier FROM public.model_catalog WHERE alias = 'bare-after'"
    )
    assert tier == "workhorse"


@pytest.mark.asyncio(loop_scope="session")
async def test_downgrade_restores_medium_default(migration_pool: asyncpg.Pool) -> None:
    """Downgrade puts the column DEFAULT back to the pre-core_147 'medium'."""
    pool = migration_pool
    await _replay(pool, "upgrade")
    await _replay(pool, "downgrade")

    assert (await _column_default(pool)).startswith("'medium'")
