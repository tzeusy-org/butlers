"""Live-Postgres up/down round-trip for dashboard_messages.sources (core_213)."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_213_dashboard_messages_sources.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "core_213_dashboard_messages_sources", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _apply(pool: asyncpg.Pool, fn_name: str) -> None:
    sqls: list[str] = []
    migration = _load_migration()
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = sqls.append
    with patch.object(migration, "op", mocked_op):
        getattr(migration, fn_name)()
    for sql in sqls:
        await pool.execute(sql)


@pytest.fixture
async def dashboard_messages_pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as pool:
        await pool.execute(
            """
            CREATE TABLE public.dashboard_conversations (
                id UUID PRIMARY KEY,
                butler_name TEXT NOT NULL
            )
            """
        )
        await pool.execute(
            """
            CREATE TABLE public.dashboard_messages (
                id UUID PRIMARY KEY,
                conversation_id UUID NOT NULL REFERENCES public.dashboard_conversations(id)
                    ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                session_id UUID,
                model_name TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                duration_ms INTEGER,
                tool_calls JSONB,
                error TEXT,
                request_id UUID
            )
            """
        )
        yield pool


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_adds_nullable_sources_column(dashboard_messages_pool) -> None:
    await _apply(dashboard_messages_pool, "upgrade")

    conv_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    await dashboard_messages_pool.execute(
        "INSERT INTO public.dashboard_conversations (id, butler_name) VALUES ($1, 'finance')",
        conv_id,
    )
    await dashboard_messages_pool.execute(
        """
        INSERT INTO public.dashboard_messages (id, conversation_id, role, content)
        VALUES ($1, $2, 'assistant', 'hi')
        """,
        msg_id,
        conv_id,
    )

    value = await dashboard_messages_pool.fetchval(
        "SELECT sources FROM public.dashboard_messages WHERE id = $1", msg_id
    )
    assert value is None


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_persists_a_sources_array(dashboard_messages_pool) -> None:
    await _apply(dashboard_messages_pool, "upgrade")

    conv_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    await dashboard_messages_pool.execute(
        "INSERT INTO public.dashboard_conversations (id, butler_name) VALUES ($1, 'finance')",
        conv_id,
    )
    await dashboard_messages_pool.execute(
        """
        INSERT INTO public.dashboard_messages (id, conversation_id, role, content, sources)
        VALUES ($1, $2, 'assistant', 'You spent $312.', $3::jsonb)
        """,
        msg_id,
        conv_id,
        '["finance.get_budget", "transaction#a1b2c3"]',
    )

    value = await dashboard_messages_pool.fetchval(
        "SELECT sources FROM public.dashboard_messages WHERE id = $1", msg_id
    )
    assert value == '["finance.get_budget", "transaction#a1b2c3"]' or value == [
        "finance.get_budget",
        "transaction#a1b2c3",
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_downgrade_drops_the_sources_column(dashboard_messages_pool) -> None:
    await _apply(dashboard_messages_pool, "upgrade")
    await _apply(dashboard_messages_pool, "downgrade")

    exists = await dashboard_messages_pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'dashboard_messages'
                AND column_name = 'sources'
        )
        """
    )
    assert exists is False
