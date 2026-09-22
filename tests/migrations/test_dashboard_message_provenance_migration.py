"""Live-Postgres round-trip for dashboard message provenance (core_247)."""

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
    / "alembic/versions/core/core_247_dashboard_message_citations.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "core_247_dashboard_message_citations", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _apply(pool: asyncpg.Pool, fn_name: str) -> None:
    statements: list[str] = []
    migration = _load_migration()
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = statements.append
    with patch.object(migration, "op", mocked_op):
        getattr(migration, fn_name)()
    for statement in statements:
        await pool.execute(statement)


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
                conversation_id UUID NOT NULL REFERENCES public.dashboard_conversations(id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sources JSONB
            )
            """
        )
        yield pool


@pytest.mark.asyncio(loop_scope="session")
async def test_migration_backfills_valid_sources_preserves_unknown_authors_and_round_trips(
    dashboard_messages_pool,
) -> None:
    conversation_id = uuid.uuid4()
    valid_id = uuid.uuid4()
    invalid_object_id = uuid.uuid4()
    invalid_nonarray_id = uuid.uuid4()
    await dashboard_messages_pool.execute(
        "INSERT INTO public.dashboard_conversations (id, butler_name) VALUES ($1, 'finance')",
        conversation_id,
    )
    await dashboard_messages_pool.execute(
        """
        INSERT INTO public.dashboard_messages (id, conversation_id, role, content, sources)
        VALUES
            ($1, $4, 'assistant', 'valid', '["  finance.get_budget  "]'::jsonb),
            ($2, $4, 'assistant', 'invalid object', '[{"label":"not legacy"}]'::jsonb),
            ($3, $4, 'assistant', 'invalid nonarray', '{"source":"legacy"}'::jsonb)
        """,
        valid_id,
        invalid_object_id,
        invalid_nonarray_id,
        conversation_id,
    )

    await _apply(dashboard_messages_pool, "upgrade")

    valid = await dashboard_messages_pool.fetchrow(
        "SELECT citations, routed_butler FROM public.dashboard_messages WHERE id = $1",
        valid_id,
    )
    invalid = await dashboard_messages_pool.fetchrow(
        "SELECT citations, routed_butler FROM public.dashboard_messages WHERE id = $1",
        invalid_object_id,
    )
    invalid_nonarray = await dashboard_messages_pool.fetchrow(
        "SELECT citations, routed_butler FROM public.dashboard_messages WHERE id = $1",
        invalid_nonarray_id,
    )
    assert valid["citations"] == [
        {"kind": "unlinked", "label": "finance.get_budget", "target": None}
    ]
    assert valid["routed_butler"] is None
    assert invalid["citations"] is None
    assert invalid["routed_butler"] is None
    assert invalid_nonarray["citations"] is None
    assert invalid_nonarray["routed_butler"] is None

    await _apply(dashboard_messages_pool, "downgrade")
    columns = await dashboard_messages_pool.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'dashboard_messages'
        """
    )
    names = {row["column_name"] for row in columns}
    assert "citations" not in names
    assert "routed_butler" not in names
    assert "sources" in names
