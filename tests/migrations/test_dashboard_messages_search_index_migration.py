"""Live-Postgres round-trip test for core_221 (dashboard message search index)."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.testing.migration import load_migration

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_221_dashboard_messages_search_index.py"
)


async def _apply(pool: asyncpg.Pool, fn_name: str) -> None:
    sqls: list[str] = []
    migration = load_migration(_MIGRATION_PATH, "core_221_dashboard_messages_search_index")

    mocked_op = MagicMock()
    mocked_op.execute.side_effect = sqls.append
    with patch.object(migration, "op", mocked_op):
        getattr(migration, fn_name)()
    for sql in sqls:
        await pool.execute(sql)


@pytest.fixture
async def search_index_pool(db):
    """The real dashboard tables as core_221's predecessor (core_220) left them."""
    return await db("core", revisions={"core": "core_220"})


async def _insert_conversation(pool: asyncpg.Pool, *, conv_id: uuid.UUID) -> None:
    await pool.execute(
        "INSERT INTO public.dashboard_conversations (id, butler_name) VALUES ($1, 'finance')",
        conv_id,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_generates_search_vector_and_creates_indexes(search_index_pool) -> None:
    conv_id = uuid.uuid4()
    await _insert_conversation(search_index_pool, conv_id=conv_id)

    await _apply(search_index_pool, "upgrade")

    msg_id = uuid.uuid4()
    await search_index_pool.execute(
        "INSERT INTO public.dashboard_messages (id, conversation_id, role, content) "
        "VALUES ($1, $2, 'user', 'Ping the landlord about the lease renewal')",
        msg_id,
        conv_id,
    )

    search_vector = await search_index_pool.fetchval(
        "SELECT search_vector FROM public.dashboard_messages WHERE id = $1", msg_id
    )
    assert search_vector is not None
    matched = await search_index_pool.fetchval(
        "SELECT search_vector @@ plainto_tsquery('english', 'landlord') "
        "FROM public.dashboard_messages WHERE id = $1",
        msg_id,
    )
    assert matched is True

    index_names = {
        row["indexname"]
        for row in await search_index_pool.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'dashboard_messages'"
        )
    }
    assert "idx_dashboard_messages_search_vector" in index_names
    assert "idx_dashboard_messages_content_trgm" in index_names


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_is_idempotent_on_rerun(search_index_pool) -> None:
    await _apply(search_index_pool, "upgrade")
    # Re-run: every statement is guarded, so this must no-op rather than error.
    await _apply(search_index_pool, "upgrade")

    index_names = {
        row["indexname"]
        for row in await search_index_pool.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'dashboard_messages'"
        )
    }
    assert "idx_dashboard_messages_search_vector" in index_names
    assert "idx_dashboard_messages_content_trgm" in index_names


@pytest.mark.asyncio(loop_scope="session")
async def test_downgrade_drops_column_and_indexes(search_index_pool) -> None:
    await _apply(search_index_pool, "upgrade")
    await _apply(search_index_pool, "downgrade")

    has_column = await search_index_pool.fetchval(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'dashboard_messages' "
        "AND column_name = 'search_vector'"
    )
    assert has_column is None

    index_names = {
        row["indexname"]
        for row in await search_index_pool.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'dashboard_messages'"
        )
    }
    assert "idx_dashboard_messages_search_vector" not in index_names
    assert "idx_dashboard_messages_content_trgm" not in index_names
