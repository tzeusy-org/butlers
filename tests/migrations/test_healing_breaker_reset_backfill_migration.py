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

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.testing.migration import load_migration

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_166_healing_breaker_reset_backfill.py"
)


async def _apply(pool: asyncpg.Pool, fn_name: str) -> None:
    """Capture the migration's op.execute() SQL and run it on the real pool."""
    mod = load_migration(_MIGRATION_PATH, "core_166")
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    for sql in sqls:
        await pool.execute(sql)


_INSERT = (
    "INSERT INTO public.healing_attempts "
    "(fingerprint, status, butler_name, severity, exception_type, call_site) "
    "VALUES ($1, $2, 'qa', 1, 'SyntheticError', 'test:core_166')"
)


@asynccontextmanager
async def _pre_core_166(db):
    """The real public.healing_attempts as core_165 left it."""
    yield await db("core", revisions={"core": "core_165"})


async def test_upgrade_is_noop_when_healing_attempts_table_absent(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        # No public.healing_attempts table at all — must not raise.
        await _apply(pool, "upgrade")


async def test_backfill_deletes_only_sentinel_rows_and_preserves_genuine(
    db,
) -> None:
    async with _pre_core_166(db) as pool:
        # --- Forged: reset-sentinel pr_merged rows from the retired handler ---
        await pool.execute(
            _INSERT,
            "reset-sentinel-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "pr_merged",
        )
        await pool.execute(
            _INSERT,
            "reset-sentinel-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "pr_merged",
        )

        # --- Genuine adjacent rows: a real failure + a real pr_merged success ---
        await pool.execute(
            _INSERT,
            "c" * 64,
            "failed",
        )
        await pool.execute(
            _INSERT,
            "d" * 64,
            "pr_merged",
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


async def test_upgrade_is_noop_on_clean_db(db) -> None:
    async with _pre_core_166(db) as pool:
        await _apply(pool, "upgrade")
        assert await pool.fetchval("SELECT count(*) FROM public.healing_attempts") == 0


async def test_downgrade_is_noop(db) -> None:
    async with _pre_core_166(db) as pool:
        await pool.execute(
            _INSERT,
            "e" * 64,
            "failed",
        )
        await _apply(pool, "downgrade")
        # Nothing to undo — downgrade is a pass.
        assert await pool.fetchval("SELECT count(*) FROM public.healing_attempts") == 1
