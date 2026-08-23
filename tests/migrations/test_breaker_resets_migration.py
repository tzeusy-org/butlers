"""Regression tests for core_164 breaker_resets migration (bu-533qx.1).

Proves the schema (``public.breaker_resets`` + index) upgrades/downgrades
cleanly and that the self-guarding backfill deletes ONLY the provably-forged
synthetic-reset rows (``status = 'manual_reset'`` attempts and the orphan
'clean' patrols they point at) while leaving every genuine adjacent row —
including a synthetic patrol that is unexpectedly shared with a real attempt —
intact.
"""

from __future__ import annotations

import importlib.util
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
    / "core_164_breaker_resets.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_164", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _apply(pool: asyncpg.Pool, fn_name: str) -> None:
    """Capture the migration's op.execute() SQL and run it on the real pool."""
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    for sql in sqls:
        await pool.execute(sql)


async def _prereq_tables(pool: asyncpg.Pool) -> None:
    """qa_patrols + healing_attempts slice with the core_054 FK the backfill needs."""
    await pool.execute("""
        CREATE TABLE public.qa_patrols (
            id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            status TEXT NOT NULL DEFAULT 'running'
        )
    """)
    await pool.execute("""
        CREATE TABLE public.healing_attempts (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            status       TEXT NOT NULL,
            qa_patrol_id UUID REFERENCES public.qa_patrols(id) ON DELETE SET NULL
        )
    """)


async def test_upgrade_creates_table_and_index_then_downgrade_drops(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _prereq_tables(pool)  # backfill references these; empty ⇒ no-op

        await _apply(pool, "upgrade")

        assert await pool.fetchval("SELECT to_regclass('public.breaker_resets')") is not None
        idx = await pool.fetchval(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname='public' AND indexname='idx_breaker_resets_breaker_reset_at'"
        )
        assert idx == "idx_breaker_resets_breaker_reset_at"

        # Round-trips as expected: an inserted row reads back with defaults.
        await pool.execute("INSERT INTO public.breaker_resets (breaker) VALUES ('qa')")
        row = await pool.fetchrow("SELECT breaker, reset_by, reset_at FROM public.breaker_resets")
        assert row["breaker"] == "qa"
        assert row["reset_by"] == "dashboard"
        assert row["reset_at"] is not None

        await _apply(pool, "downgrade")
        assert await pool.fetchval("SELECT to_regclass('public.breaker_resets')") is None


async def test_backfill_deletes_only_forged_rows_and_preserves_genuine(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _prereq_tables(pool)

        # --- Forged: synthetic clean patrol + its manual_reset attempt ---
        forged_patrol = await pool.fetchval(
            "INSERT INTO public.qa_patrols (status) VALUES ('clean') RETURNING id"
        )
        await pool.execute(
            "INSERT INTO public.healing_attempts (status, qa_patrol_id) "
            "VALUES ('manual_reset', $1)",
            forged_patrol,
        )

        # --- Genuine adjacent rows: a real error patrol + a real failed attempt ---
        genuine_patrol = await pool.fetchval(
            "INSERT INTO public.qa_patrols (status) VALUES ('error') RETURNING id"
        )
        await pool.execute(
            "INSERT INTO public.healing_attempts (status, qa_patrol_id) VALUES ('failed', $1)",
            genuine_patrol,
        )
        # --- A genuine 'clean' patrol referenced by NO manual_reset row (must stay) ---
        real_clean_patrol = await pool.fetchval(
            "INSERT INTO public.qa_patrols (status) VALUES ('clean') RETURNING id"
        )
        await pool.execute(
            "INSERT INTO public.healing_attempts (status, qa_patrol_id) VALUES ('pr_merged', $1)",
            real_clean_patrol,
        )
        # --- Safety guard: a 'clean' patrol shared by a manual_reset AND a genuine
        #     attempt must be PRESERVED (the NOT EXISTS branch) ---
        shared_patrol = await pool.fetchval(
            "INSERT INTO public.qa_patrols (status) VALUES ('clean') RETURNING id"
        )
        await pool.execute(
            "INSERT INTO public.healing_attempts (status, qa_patrol_id) "
            "VALUES ('manual_reset', $1)",
            shared_patrol,
        )
        await pool.execute(
            "INSERT INTO public.healing_attempts (status, qa_patrol_id) VALUES ('failed', $1)",
            shared_patrol,
        )

        await _apply(pool, "upgrade")

        # All manual_reset attempts (both the orphan and the shared one) are gone.
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.healing_attempts WHERE status = 'manual_reset'"
            )
            == 0
        )
        # The orphan synthetic patrol is deleted.
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.qa_patrols WHERE id = $1", forged_patrol
            )
            == 0
        )
        # Every genuine patrol survives, including the shared-but-guarded one.
        surviving = {r["id"] for r in await pool.fetch("SELECT id FROM public.qa_patrols")}
        assert genuine_patrol in surviving
        assert real_clean_patrol in surviving
        assert shared_patrol in surviving  # preserved by the NOT EXISTS guard
        # Genuine attempts (failed / pr_merged / the shared patrol's failed) survive.
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.healing_attempts WHERE status <> 'manual_reset'"
            )
            == 3
        )
