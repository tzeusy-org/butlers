"""Real migrated PostgreSQL: durable Gmail send fence and retention-safe downgrade."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import asyncpg
import pytest

from butlers.modules.gmail.receipts import SendReceipts
from butlers.testing.migration import load_migration

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
MIGRATION = Path(__file__).resolve().parents[1] / "migrations/005_gmail_owner_sends.py"


async def apply_migration(pool, operation):
    migration = load_migration(MIGRATION)
    statements = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: statements.append(str(sql))
    with patch.object(migration, "op", mock_op):
        getattr(migration, operation)()
    async with pool.acquire() as connection, connection.transaction():
        for statement in statements:
            await connection.execute(statement)


async def test_send_fence_is_committed_unique_and_content_blind(provisioned_postgres_pool):
    async with provisioned_postgres_pool(schema="messenger", max_pool_size=4) as pool:
        await pool.execute("CREATE SCHEMA messenger")
        await apply_migration(pool, "upgrade")
        store, effect = SendReceipts(pool), uuid4()
        rows = await asyncio.gather(
            *[
                store.reserve(effect, "owner@example.test", "t1", "<id@example.test>", "a" * 64)
                for _ in range(2)
            ]
        )
        assert sum(row is not None for row in rows) == 1
        # A different acquisition sees the fence committed before a provider could run.
        assert (await store.get(effect))["state"] == "dispatching"
        assert await pool.fetchval("SELECT count(*) FROM gmail_owner_sends") == 1
        row = await store.settle(effect, "unknown")
        assert row["state"] == "unknown"
        assert (
            await store.reserve(effect, "owner@example.test", "t1", "<id@example.test>", "a" * 64)
            is None
        )
        assert (await store.settle(effect, "sent", "gmail1"))["gmail_message_id"] == "gmail1"
        assert (await store.settle(effect, "failed"))["state"] == "sent"
        columns = await pool.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='messenger' AND table_name='gmail_owner_sends'"
        )
        assert {r["column_name"] for r in columns} == {
            "effect_id",
            "account",
            "thread_id",
            "message_id",
            "call_digest",
            "gmail_message_id",
            "state",
            "draft_cleanup",
            "created_at",
            "updated_at",
        }
        with pytest.raises(asyncpg.PostgresError, match="Gmail send receipts exist"):
            await apply_migration(pool, "downgrade")
        assert (await store.get(effect))["state"] == "sent"


async def test_empty_receipts_downgrade_and_reupgrade(provisioned_postgres_pool):
    async with provisioned_postgres_pool(schema="messenger") as pool:
        await pool.execute("CREATE SCHEMA messenger")
        await apply_migration(pool, "upgrade")
        await apply_migration(pool, "downgrade")
        assert await pool.fetchval("SELECT to_regclass('gmail_owner_sends')") is None
        await apply_migration(pool, "upgrade")
        assert await pool.fetchval("SELECT count(*) FROM gmail_owner_sends") == 0
