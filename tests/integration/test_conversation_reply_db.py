"""Real-Postgres integration tests for the conversation_reply write path (bu-p6ey8.1).

Mocked-pool-only coverage has previously hidden search_path/schema bugs on
main (see project history around ``relationship.facts``), so the
conversation_reply confirm-loop's DB layer — ``conversation_reply_create``,
``conversation_set_routed_butler``, and ``message_find_reply_since`` in
``butlers.api.conversations`` — is exercised here against a live database
rather than only against AsyncMock pools (see tests/api/test_conversations.py
for the mocked-pool unit coverage of the same functions plus the router/SSE
layer above them).

``provisioned_postgres_pool()`` only creates a fresh database + extensions;
it does not run the Alembic chain (see tests/migrations/test_backfill_dashboard_audit_log.py
for the established pattern), so this file provisions the minimal
``public.dashboard_conversations`` / ``public.dashboard_messages`` shape
directly, matching core_006 (creation) + core_153 (``routed_butler`` column).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from butlers.api.conversations import (
    conversation_create,
    conversation_reply_create,
    conversation_set_routed_butler,
    message_find_reply_since,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS public.dashboard_conversations (
    id UUID PRIMARY KEY,
    butler_name TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    message_count INTEGER NOT NULL DEFAULT 0,
    total_input_tokens BIGINT NOT NULL DEFAULT 0,
    total_output_tokens BIGINT NOT NULL DEFAULT 0,
    total_duration_ms BIGINT NOT NULL DEFAULT 0,
    routed_butler TEXT NULL
);

CREATE TABLE IF NOT EXISTS public.dashboard_messages (
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
);
"""


async def test_conversation_reply_create_persists_message_and_bumps_count(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_SCHEMA)
        conv = await conversation_create(pool, butler_name="switchboard", first_message="hi")

        msg = await conversation_reply_create(
            pool, conv["id"], message="Recorded: Alice child-of Bob — correct?"
        )

        assert msg is not None
        assert msg["role"] == "assistant"
        assert msg["content"] == "Recorded: Alice child-of Bob — correct?"

        stored = await pool.fetchrow(
            "SELECT role, content FROM public.dashboard_messages WHERE conversation_id = $1",
            conv["id"],
        )
        assert stored["role"] == "assistant"
        assert stored["content"] == "Recorded: Alice child-of Bob — correct?"

        conv_row = await pool.fetchrow(
            "SELECT message_count FROM public.dashboard_conversations WHERE id = $1",
            conv["id"],
        )
        assert conv_row["message_count"] == 1


async def test_conversation_reply_create_returns_none_for_missing_conversation(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_SCHEMA)

        result = await conversation_reply_create(pool, uuid.uuid4(), message="hello")

        assert result is None
        count = await pool.fetchval("SELECT count(*) FROM public.dashboard_messages")
        assert count == 0


async def test_conversation_set_routed_butler_is_sticky_across_repeat_calls(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_SCHEMA)
        conv = await conversation_create(pool, butler_name="switchboard", first_message="hi")

        await conversation_set_routed_butler(pool, conv["id"], routed_butler="finance")
        await conversation_set_routed_butler(pool, conv["id"], routed_butler="relationship")

        stored = await pool.fetchval(
            "SELECT routed_butler FROM public.dashboard_conversations WHERE id = $1",
            conv["id"],
        )
        # First successful route wins; the second call is a no-op.
        assert stored == "finance"


async def test_message_find_reply_since_ignores_stale_reply_and_finds_fresh_one(
    provisioned_postgres_pool,
) -> None:
    """The poller must not surface a reply from an earlier turn, and must
    pick up a genuinely late reply once it lands (the confirm-loop reply can
    arrive independently of — often before — the routed session finishing)."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_SCHEMA)
        conv = await conversation_create(pool, butler_name="switchboard", first_message="hi")
        conv_id = conv["id"]

        # A stale assistant reply from a previous turn, well before "now".
        await pool.execute(
            """
            INSERT INTO public.dashboard_messages (id, conversation_id, role, content, created_at)
            VALUES (gen_random_uuid(), $1, 'assistant', 'stale reply', $2)
            """,
            conv_id,
            datetime.now(UTC) - timedelta(minutes=5),
        )

        since = datetime.now(UTC)

        # Nothing fresh yet — the stale reply must not satisfy the poll.
        result = await message_find_reply_since(pool, conv_id, since=since)
        assert result is None

        # The real conversation_reply lands after `since`.
        created = await conversation_reply_create(pool, conv_id, message="Recorded: X — correct?")
        assert created is not None

        result = await message_find_reply_since(pool, conv_id, since=since)
        assert result is not None
        assert result["content"] == "Recorded: X — correct?"
        assert result["id"] == created["id"]
