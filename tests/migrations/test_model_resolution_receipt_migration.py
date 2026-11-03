"""Real-Postgres lifecycle coverage for core_244 receipt storage."""

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
    / "core_244_model_resolution_receipt.py"
)


async def _run_migration(pool: asyncpg.Pool, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("core_244", _MIGRATION)
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
async def test_upgrade_preserves_rows_accepts_receipts_and_downgrade_removes_column(db) -> None:
    # core_244's predecessor: the real model_dispatch_attempts without receipts.
    pool = await db("core", revisions={"core": "core_243"})
    catalog_id = await pool.fetchval(
        "INSERT INTO public.model_catalog (alias, runtime_type, model_id, complexity_tier) "
        "VALUES ('receipt-test', 'codex', 'test', 'workhorse') RETURNING id"
    )
    legacy_id = await pool.fetchval(
        "INSERT INTO public.model_dispatch_attempts (catalog_entry_id, butler, outcome) "
        "VALUES ($1, 'general', 'success') RETURNING id",
        catalog_id,
    )

    await _run_migration(pool, "upgrade")
    assert (
        await pool.fetchval(
            "SELECT resolution_receipt FROM public.model_dispatch_attempts WHERE id = $1",
            legacy_id,
        )
        is None
    )
    receipt = {"policy_version": "2", "winner": {"model_id": "test"}}
    receipt_id = await pool.fetchval(
        "INSERT INTO public.model_dispatch_attempts "
        "(catalog_entry_id, butler, outcome, resolution_receipt) "
        "VALUES ($1, 'general', 'success', $2::jsonb) RETURNING id",
        catalog_id,
        receipt,
    )
    assert await pool.fetchval(
        "SELECT resolution_receipt = $2::jsonb FROM public.model_dispatch_attempts WHERE id = $1",
        receipt_id,
        receipt,
    )

    await _run_migration(pool, "downgrade")
    assert not await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'model_dispatch_attempts' "
        "AND column_name = 'resolution_receipt')"
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.model_dispatch_attempts WHERE id IN ($1, $2)",
            legacy_id,
            receipt_id,
        )
        == 2
    )
