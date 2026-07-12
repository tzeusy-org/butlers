"""Regression tests for core_166 healing breaker-reset backfill (bu-dz7ac).

Proves the self-guarding backfill deletes ONLY the provably-forged
``reset-sentinel-*`` synthetic ``healing_attempts`` rows left behind by the
retired ``POST /api/healing/circuit-breaker/reset`` handler, while leaving
every genuine adjacent row (including a real ``pr_merged`` attempt) intact.

Unlike core_164 (which introduced ``public.breaker_resets``), core_166 makes
no schema change — it reuses the ``breaker`` discriminator column already
created by core_164 and only backfill-deletes forged rows.
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
    / "core_166_healing_breaker_reset_backfill.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_166", _MIGRATION_PATH)
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


async def _prereq_table(pool: asyncpg.Pool) -> None:
    """Minimal healing_attempts slice the backfill needs."""
    await pool.execute("""
        CREATE TABLE public.healing_attempts (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            fingerprint TEXT NOT NULL,
            status      TEXT NOT NULL
        )
    """)


def test_migration_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "core_166"
    assert mod.down_revision == "core_165"


async def test_upgrade_is_noop_when_healing_attempts_table_absent(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        # No public.healing_attempts table at all — must not raise.
        await _apply(pool, "upgrade")


async def test_backfill_deletes_only_sentinel_rows_and_preserves_genuine(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _prereq_table(pool)

        # --- Forged: reset-sentinel pr_merged rows from the retired handler ---
        await pool.execute(
            "INSERT INTO public.healing_attempts (fingerprint, status) VALUES ($1, 'pr_merged')",
            "reset-sentinel-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        await pool.execute(
            "INSERT INTO public.healing_attempts (fingerprint, status) VALUES ($1, 'pr_merged')",
            "reset-sentinel-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )

        # --- Genuine adjacent rows: a real failure + a real pr_merged success ---
        await pool.execute(
            "INSERT INTO public.healing_attempts (fingerprint, status) VALUES ($1, 'failed')",
            "c" * 64,
        )
        await pool.execute(
            "INSERT INTO public.healing_attempts (fingerprint, status) VALUES ($1, 'pr_merged')",
            "d" * 64,
        )

        await _apply(pool, "upgrade")

        # Both sentinel rows are gone.
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.healing_attempts "
                "WHERE fingerprint LIKE 'reset-sentinel-%'"
            )
            == 0
        )
        # Genuine rows (including the real pr_merged one) survive untouched.
        surviving = {
            r["fingerprint"]
            for r in await pool.fetch("SELECT fingerprint FROM public.healing_attempts")
        }
        assert surviving == {"c" * 64, "d" * 64}


async def test_upgrade_is_noop_on_clean_db(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _prereq_table(pool)
        await _apply(pool, "upgrade")
        assert await pool.fetchval("SELECT count(*) FROM public.healing_attempts") == 0


async def test_downgrade_is_noop(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _prereq_table(pool)
        await pool.execute(
            "INSERT INTO public.healing_attempts (fingerprint, status) VALUES ($1, 'failed')",
            "e" * 64,
        )
        await _apply(pool, "downgrade")
        # Nothing to undo — downgrade is a pass.
        assert await pool.fetchval("SELECT count(*) FROM public.healing_attempts") == 1
