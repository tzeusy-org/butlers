"""google_drive_002: the effect-receipts table upgrades, keys on effect_id, and downgrades."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.testing.migration import load_migration

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "butlers"
    / "modules"
    / "google_drive"
    / "migrations"
    / "002_google_drive_effect_receipts.py"
)


async def _apply(pool: asyncpg.Pool, operation: str) -> None:
    module = load_migration(_PATH, "google_drive_002")
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(module, "op", fake_op):
        getattr(module, operation)()
    for statement in statements:
        await pool.execute(statement)


async def test_upgrade_creates_a_receipt_table_keyed_by_effect_and_downgrade_drops_it(db) -> None:
    pool = await db(
        "core",
        "google_drive",
        schemas={"core": "gdrive", "google_drive": "gdrive"},
        schema="gdrive",
        revisions={"google_drive": "google_drive_001"},
    )
    assert await pool.fetchval("SELECT to_regclass('google_drive_effect_receipts')") is None

    await _apply(pool, "upgrade")
    effect = uuid.uuid4()
    insert = (
        "INSERT INTO google_drive_effect_receipts (effect_id, tool, receipt) "
        "VALUES ($1, 'drive_create_doc', '{}'::jsonb) ON CONFLICT (effect_id) DO NOTHING"
    )
    await pool.execute(insert, effect)
    await pool.execute(insert, effect)
    assert await pool.fetchval("SELECT count(*) FROM google_drive_effect_receipts") == 1

    await _apply(pool, "downgrade")
    assert await pool.fetchval("SELECT to_regclass('google_drive_effect_receipts')") is None
