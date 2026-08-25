"""Regression tests for mem_008 fading-validity backfill (bu-5ud8p.1).

Proves the self-guarding backfill converts legacy facts that a prior
``run_decay_sweep`` marked only via ``metadata.status = 'fading'`` (the dead
data contract: every reader queries the ``validity`` column) into
``validity = 'fading'`` with the stale ``metadata.status`` key removed, while
leaving already-correct and unrelated rows untouched.
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
    / "src"
    / "butlers"
    / "modules"
    / "memory"
    / "migrations"
    / "008_backfill_fading_validity.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("mem_008", _MIGRATION_PATH)
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
    """Minimal facts slice the backfill needs (validity + metadata)."""
    await pool.execute("""
        CREATE TABLE facts (
            id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            content  TEXT NOT NULL DEFAULT 'x',
            validity TEXT NOT NULL DEFAULT 'active',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        )
    """)


async def test_upgrade_is_noop_when_facts_table_absent(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        # No facts table at all (pre-memory-module schema) — must not raise.
        await _apply(pool, "upgrade")


async def test_backfill_converts_legacy_fading_and_preserves_others(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _prereq_table(pool)

        # --- Legacy fading: validity='active' but metadata.status='fading' ---
        legacy_id = await pool.fetchval(
            "INSERT INTO facts (content, validity, metadata) "
            "VALUES ('legacy-fading', 'active', '{\"status\": \"fading\"}'::jsonb) "
            "RETURNING id"
        )

        # --- Already-correct fading row: must be left exactly as-is ---
        already_id = await pool.fetchval(
            "INSERT INTO facts (content, validity, metadata) "
            "VALUES ('already-fading', 'fading', '{}'::jsonb) RETURNING id"
        )

        # --- Healthy active row: untouched ---
        healthy_id = await pool.fetchval(
            "INSERT INTO facts (content, validity, metadata) "
            "VALUES ('healthy', 'active', '{}'::jsonb) RETURNING id"
        )

        # --- Retracted row that happens to carry a stale status key: the
        # backfill predicate requires validity='active', so a retracted row
        # must never be resurrected to 'fading'. ---
        retracted_id = await pool.fetchval(
            "INSERT INTO facts (content, validity, metadata) "
            "VALUES ('retracted-with-stale-key', 'retracted', "
            '\'{"status": "fading"}\'::jsonb) RETURNING id'
        )

        await _apply(pool, "upgrade")

        legacy_row = await pool.fetchrow(
            "SELECT validity, metadata FROM facts WHERE id = $1", legacy_id
        )
        assert legacy_row["validity"] == "fading"
        assert "status" not in legacy_row["metadata"]

        already_row = await pool.fetchrow(
            "SELECT validity, metadata FROM facts WHERE id = $1", already_id
        )
        assert already_row["validity"] == "fading"
        assert already_row["metadata"] == {}

        healthy_row = await pool.fetchrow(
            "SELECT validity, metadata FROM facts WHERE id = $1", healthy_id
        )
        assert healthy_row["validity"] == "active"
        assert healthy_row["metadata"] == {}

        retracted_row = await pool.fetchrow(
            "SELECT validity, metadata FROM facts WHERE id = $1", retracted_id
        )
        assert retracted_row["validity"] == "retracted"
        assert retracted_row["metadata"].get("status") == "fading"


async def test_backfill_is_idempotent(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _prereq_table(pool)
        await pool.execute(
            "INSERT INTO facts (content, validity, metadata) "
            "VALUES ('legacy-fading', 'active', '{\"status\": \"fading\"}'::jsonb)"
        )

        await _apply(pool, "upgrade")
        # Second run: nothing left matching the legacy predicate, parity
        # check trivially satisfied (0 == 0).
        await _apply(pool, "upgrade")

        row = await pool.fetchrow("SELECT validity, metadata FROM facts")
        assert row["validity"] == "fading"
        assert "status" not in row["metadata"]


async def test_downgrade_is_noop(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _prereq_table(pool)
        await pool.execute(
            "INSERT INTO facts (content, validity, metadata) "
            "VALUES ('legacy-fading', 'active', '{\"status\": \"fading\"}'::jsonb)"
        )
        await _apply(pool, "downgrade")
        # Nothing to undo — downgrade is a pass.
        row = await pool.fetchrow("SELECT validity, metadata FROM facts")
        assert row["validity"] == "active"
        assert row["metadata"] == {"status": "fading"}
