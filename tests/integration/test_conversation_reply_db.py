"""Real-Postgres integration tests for the conversation_reply write path (bu-p6ey8.1).

Mocked-pool-only coverage has previously hidden search_path/schema bugs on
main (see project history around ``relationship.facts``), so the
conversation_reply confirm-loop's DB layer — ``conversation_reply_create``,
``conversation_set_routed_butler``, and ``message_find_reply_since`` in
``butlers.api.conversations`` — is exercised here against a live database
rather than only against AsyncMock pools (see tests/api/test_conversations.py
for the mocked-pool unit coverage of the same functions plus the router/SSE
layer above them).

``migrated_core_postgres_pool()`` runs the core Alembic chain against the
fresh provisioned database, so this test exercises the production schema
rather than a hand-maintained approximation.

bu-qesw0: also covers ``conversation_list``'s ``latest_assistant_reply_at``
subquery against a live database. The unread-badge dead-signal bug (bu-qesw0)
went undetected because the mocked-pool unit tests in
tests/api/test_conversations.py feed ``conversation_list``'s *return value*
directly and never execute the real SQL — a real-Postgres assertion here is
the only thing that actually exercises the ``MAX(...) FILTER``-equivalent
correlated subquery against ``public.dashboard_messages``.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from butlers.api.conversations import (
    conversation_create,
    conversation_list,
    conversation_reply_create,
    conversation_set_routed_butler,
    message_create_idempotent,
    message_find_reply_since,
    message_get_by_id,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


async def test_conversation_reply_create_persists_message_and_bumps_count(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool() as pool:
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
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool() as pool:
        result = await conversation_reply_create(pool, uuid.uuid4(), message="hello")

        assert result is None
        count = await pool.fetchval("SELECT count(*) FROM public.dashboard_messages")
        assert count == 0


async def test_message_create_idempotent_reuses_the_original_user_row(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool() as pool:
        conv = await conversation_create(pool, butler_name="switchboard", first_message="Retry me")
        message_id = uuid.uuid4()

        first, first_is_new = await message_create_idempotent(
            pool,
            message_id=message_id,
            conversation_id=conv["id"],
            role="user",
            content="Retry me",
        )
        retry, retry_is_new = await message_create_idempotent(
            pool,
            message_id=message_id,
            conversation_id=conv["id"],
            role="user",
            content="Retry me",
        )

        assert first_is_new is True
        assert retry_is_new is False
        assert retry == first
        assert await message_get_by_id(pool, message_id) == first
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.dashboard_messages WHERE id = $1", message_id
            )
            == 1
        )


async def test_conversation_set_routed_butler_is_sticky_across_repeat_calls(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool() as pool:
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
    migrated_core_postgres_pool,
) -> None:
    """The poller must not surface a reply from an earlier turn, and must
    pick up a genuinely late reply once it lands (the confirm-loop reply can
    arrive independently of — often before — the routed session finishing)."""
    async with migrated_core_postgres_pool() as pool:
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


async def test_conversation_list_exposes_latest_assistant_reply_at(
    migrated_core_postgres_pool,
) -> None:
    """conversation_list's latest_assistant_reply_at follows assistant replies."""
    async with migrated_core_postgres_pool() as pool:
        conv = await conversation_create(pool, butler_name="switchboard", first_message="hi")
        conv_id = conv["id"]

        # No replies yet: latest_assistant_reply_at is None.
        rows, _ = await conversation_list(pool, butler_name="switchboard")
        assert len(rows) == 1
        assert rows[0]["latest_assistant_reply_at"] is None
        assert "total_output_tokens" not in rows[0]

        first_reply = await conversation_reply_create(
            pool, conv_id, message="Recorded: Alice child-of Bob — correct?"
        )
        assert first_reply is not None

        rows, _ = await conversation_list(pool, butler_name="switchboard")
        assert rows[0]["latest_assistant_reply_at"] == first_reply["created_at"]
        assert "total_output_tokens" not in rows[0]

        # A stale user message must not move latest_assistant_reply_at.
        await pool.execute(
            """
            INSERT INTO public.dashboard_messages (id, conversation_id, role, content, created_at)
            VALUES (gen_random_uuid(), $1, 'user', 'a follow-up question', $2)
            """,
            conv_id,
            first_reply["created_at"] + timedelta(seconds=1),
        )
        rows, _ = await conversation_list(pool, butler_name="switchboard")
        assert rows[0]["latest_assistant_reply_at"] == first_reply["created_at"]

        # A second, later assistant reply advances the watermark signal.
        second_reply = await conversation_reply_create(
            pool, conv_id, message="Second reply — correct?"
        )
        assert second_reply is not None
        rows, _ = await conversation_list(pool, butler_name="switchboard")
        assert rows[0]["latest_assistant_reply_at"] == second_reply["created_at"]
        assert second_reply["created_at"] > first_reply["created_at"]
