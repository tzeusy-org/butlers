"""Live-Postgres up/down round-trip for dashboard_messages.page_context (core_219)."""

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
    / "alembic/versions/core/core_219_dashboard_messages_page_context.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "core_219_dashboard_messages_page_context", _MIGRATION_PATH
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
                request_id UUID,
                sources JSONB
            )
            """
        )
        yield pool


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_adds_nullable_page_context_and_captured_at_columns(
    dashboard_messages_pool,
) -> None:
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

    row = await dashboard_messages_pool.fetchrow(
        "SELECT page_context, captured_at FROM public.dashboard_messages WHERE id = $1", msg_id
    )
    assert row["page_context"] is None
    assert row["captured_at"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_persists_a_page_context_snapshot_and_captured_at(
    dashboard_messages_pool,
) -> None:
    await _apply(dashboard_messages_pool, "upgrade")

    conv_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    await dashboard_messages_pool.execute(
        "INSERT INTO public.dashboard_conversations (id, butler_name) VALUES ($1, 'finance')",
        conv_id,
    )
    await dashboard_messages_pool.execute(
        """
        INSERT INTO public.dashboard_messages
            (id, conversation_id, role, content, page_context, captured_at)
        VALUES ($1, $2, 'user', 'why is this so expensive', $3::jsonb, now())
        """,
        msg_id,
        conv_id,
        '{"route": "/spend", "query_params": {"window": "week"}}',
    )

    row = await dashboard_messages_pool.fetchrow(
        "SELECT page_context, captured_at FROM public.dashboard_messages WHERE id = $1", msg_id
    )
    page_context = row["page_context"]
    if isinstance(page_context, str):
        import json

        page_context = json.loads(page_context)
    assert page_context == {"route": "/spend", "query_params": {"window": "week"}}
    assert row["captured_at"] is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_downgrade_drops_both_columns(dashboard_messages_pool) -> None:
    await _apply(dashboard_messages_pool, "upgrade")
    await _apply(dashboard_messages_pool, "downgrade")

    exists = await dashboard_messages_pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'dashboard_messages'
                AND column_name IN ('page_context', 'captured_at')
        )
        """
    )
    assert exists is False
