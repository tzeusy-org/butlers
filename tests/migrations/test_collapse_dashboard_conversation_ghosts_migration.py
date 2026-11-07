"""Live-Postgres test for the dashboard ghost-conversation collapse (core_214)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.testing.migration import load_migration

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_214_collapse_dashboard_conversation_ghosts.py"
)


async def _apply(pool: asyncpg.Pool, fn_name: str) -> None:
    sqls: list[str] = []
    migration = load_migration(_MIGRATION_PATH, "core_214_collapse_dashboard_conversation_ghosts")
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = sqls.append
    with patch.object(migration, "op", mocked_op):
        getattr(migration, fn_name)()
    for sql in sqls:
        await pool.execute(sql)


@pytest.fixture
async def ghost_collapse_pool(db):
    """core_214's predecessor: the real dashboard conversation tables."""
    return await db("core", revisions={"core": "core_213"})


async def _insert_conversation(
    pool: asyncpg.Pool,
    *,
    conv_id: uuid.UUID,
    butler_name: str,
    source_thread_identity: str | None = None,
    provider_session_id: str | None = None,
    provider_session_updated_at: datetime | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO public.dashboard_conversations
            (id, butler_name, source_thread_identity, provider_session_id,
             provider_runtime_type, provider_session_updated_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        conv_id,
        butler_name,
        source_thread_identity,
        provider_session_id,
        "claude_cli" if provider_session_id else None,
        provider_session_updated_at,
    )


async def _insert_message(
    pool: asyncpg.Pool, *, conv_id: uuid.UUID, role: str = "assistant"
) -> uuid.UUID:
    msg_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO public.dashboard_messages (id, conversation_id, role, content) "
        "VALUES ($1, $2, $3, 'hi')",
        msg_id,
        conv_id,
        role,
    )
    return msg_id


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_folds_ghost_messages_and_deletes_ghost(ghost_collapse_pool) -> None:
    parent_id = uuid.uuid4()
    ghost_id = uuid.uuid4()
    await _insert_conversation(ghost_collapse_pool, conv_id=parent_id, butler_name="switchboard")
    await _insert_conversation(
        ghost_collapse_pool,
        conv_id=ghost_id,
        butler_name="finance",
        source_thread_identity=str(parent_id),
        provider_session_id="sess-ghost",
        provider_session_updated_at=datetime.now(UTC),
    )
    parent_msg = await _insert_message(ghost_collapse_pool, conv_id=parent_id, role="user")
    ghost_reply = await _insert_message(ghost_collapse_pool, conv_id=ghost_id, role="assistant")

    await _apply(ghost_collapse_pool, "upgrade")

    ghost_row = await ghost_collapse_pool.fetchrow(
        "SELECT 1 FROM public.dashboard_conversations WHERE id = $1", ghost_id
    )
    assert ghost_row is None

    reply_owner = await ghost_collapse_pool.fetchval(
        "SELECT conversation_id FROM public.dashboard_messages WHERE id = $1", ghost_reply
    )
    assert reply_owner == parent_id

    parent = await ghost_collapse_pool.fetchrow(
        "SELECT provider_session_id, message_count FROM public.dashboard_conversations WHERE id = $1",
        parent_id,
    )
    assert parent["provider_session_id"] == "sess-ghost"
    assert parent["message_count"] == 2

    # Both messages (the original parent message and the folded ghost reply)
    # now live under the parent.
    remaining = await ghost_collapse_pool.fetch(
        "SELECT id FROM public.dashboard_messages WHERE conversation_id = $1", parent_id
    )
    assert {r["id"] for r in remaining} == {parent_msg, ghost_reply}


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_keeps_parents_newer_provider_session(ghost_collapse_pool) -> None:
    parent_id = uuid.uuid4()
    ghost_id = uuid.uuid4()
    now = datetime.now(UTC)
    await _insert_conversation(
        ghost_collapse_pool,
        conv_id=parent_id,
        butler_name="switchboard",
        provider_session_id="sess-parent",
        provider_session_updated_at=now,
    )
    await _insert_conversation(
        ghost_collapse_pool,
        conv_id=ghost_id,
        butler_name="finance",
        source_thread_identity=str(parent_id),
        provider_session_id="sess-ghost-older",
        provider_session_updated_at=now - timedelta(hours=1),
    )

    await _apply(ghost_collapse_pool, "upgrade")

    parent_session = await ghost_collapse_pool.fetchval(
        "SELECT provider_session_id FROM public.dashboard_conversations WHERE id = $1", parent_id
    )
    assert parent_session == "sess-parent"


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_leaves_non_ghost_fixture_untouched(ghost_collapse_pool) -> None:
    real_id = uuid.uuid4()
    await _insert_conversation(ghost_collapse_pool, conv_id=real_id, butler_name="finance")
    msg_id = await _insert_message(ghost_collapse_pool, conv_id=real_id, role="user")

    await _apply(ghost_collapse_pool, "upgrade")

    row = await ghost_collapse_pool.fetchrow(
        "SELECT id FROM public.dashboard_conversations WHERE id = $1", real_id
    )
    assert row is not None
    msg = await ghost_collapse_pool.fetchrow(
        "SELECT conversation_id FROM public.dashboard_messages WHERE id = $1", msg_id
    )
    assert msg["conversation_id"] == real_id


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_is_idempotent_on_rerun(ghost_collapse_pool) -> None:
    parent_id = uuid.uuid4()
    ghost_id = uuid.uuid4()
    await _insert_conversation(ghost_collapse_pool, conv_id=parent_id, butler_name="switchboard")
    await _insert_conversation(
        ghost_collapse_pool,
        conv_id=ghost_id,
        butler_name="finance",
        source_thread_identity=str(parent_id),
    )
    await _insert_message(ghost_collapse_pool, conv_id=ghost_id, role="assistant")

    await _apply(ghost_collapse_pool, "upgrade")
    # Re-run: no ghosts remain, so this must no-op rather than error.
    await _apply(ghost_collapse_pool, "upgrade")

    remaining_conversations = await ghost_collapse_pool.fetch(
        "SELECT id FROM public.dashboard_conversations"
    )
    assert {r["id"] for r in remaining_conversations} == {parent_id}
