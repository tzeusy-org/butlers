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
    conversation_clear_provider_session,
    conversation_create,
    conversation_get_or_create_by_thread,
    conversation_get_provider_session,
    conversation_list,
    conversation_reply_create,
    conversation_search,
    conversation_set_provider_session,
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


async def test_conversation_search_exposes_latest_assistant_reply_at(
    migrated_core_postgres_pool,
) -> None:
    """Search results retain the summary watermark for unread-reply detection."""
    async with migrated_core_postgres_pool() as pool:
        conv = await conversation_create(
            pool,
            butler_name="switchboard",
            first_message="Find the needle in this conversation",
        )
        _, is_new = await message_create_idempotent(
            pool,
            message_id=uuid.uuid4(),
            conversation_id=conv["id"],
            role="user",
            content="Find the needle in this conversation",
        )
        assert is_new is True
        reply = await conversation_reply_create(
            pool,
            conv["id"],
            message="The matching response does not repeat the search term.",
        )
        assert reply is not None

        rows, total = await conversation_search(
            pool,
            butler_name="switchboard",
            query="needle",
        )

        assert total == 1
        assert len(rows) == 1
        assert rows[0]["snippet"] == "Find the needle in this conversation"
        assert rows[0]["latest_assistant_reply_at"] == reply["created_at"]


# ---------------------------------------------------------------------------
# bu-ep4ks.8: channel-agnostic conversation anchor + provider resume ledger
# ---------------------------------------------------------------------------


async def test_conversation_get_or_create_by_thread_creates_once_then_reuses(
    migrated_core_postgres_pool,
) -> None:
    """Repeat ingress for the same thread converges on one anchor row.

    Exercises the real partial unique index (core_185) via
    ``ON CONFLICT ... DO NOTHING`` — a mocked pool cannot catch a broken
    conflict-target/index mismatch, only a live database can.
    """
    async with migrated_core_postgres_pool() as pool:
        first, first_is_new = await conversation_get_or_create_by_thread(
            pool,
            butler_name="telegram-relay",
            source_channel="telegram",
            external_conversation_id="telegram:12345",
            first_message="hello from telegram",
        )
        second, second_is_new = await conversation_get_or_create_by_thread(
            pool,
            butler_name="telegram-relay",
            source_channel="telegram",
            external_conversation_id="telegram:12345",
            first_message="a different first message on retry",
        )

        assert first_is_new is True
        assert second_is_new is False
        assert second["id"] == first["id"]
        # The winning row keeps the first writer's title, not the retry's.
        assert second["title"] == first["title"]

        count = await pool.fetchval(
            """
            SELECT count(*) FROM public.dashboard_conversations
            WHERE butler_name = $1 AND source_channel = $2 AND external_conversation_id = $3
            """,
            "telegram-relay",
            "telegram",
            "telegram:12345",
        )
        assert count == 1


async def test_conversation_get_or_create_by_thread_distinguishes_channels_and_threads(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool() as pool:
        telegram_conv, _ = await conversation_get_or_create_by_thread(
            pool,
            butler_name="switchboard",
            source_channel="telegram",
            external_conversation_id="telegram:1",
            first_message="hi from telegram",
        )
        email_conv, _ = await conversation_get_or_create_by_thread(
            pool,
            butler_name="switchboard",
            source_channel="email",
            external_conversation_id="telegram:1",
            first_message="hi from email",
        )
        other_thread_conv, _ = await conversation_get_or_create_by_thread(
            pool,
            butler_name="switchboard",
            source_channel="telegram",
            external_conversation_id="telegram:2",
            first_message="a different telegram thread",
        )

        ids = {telegram_conv["id"], email_conv["id"], other_thread_conv["id"]}
        assert len(ids) == 3

        # Pre-existing dashboard-created rows (NULL source_thread_identity)
        # are untouched by the partial index and never collide with anchors.
        dashboard_conv = await conversation_create(
            pool, butler_name="switchboard", first_message="dashboard-native conversation"
        )
        assert dashboard_conv["id"] not in ids


async def test_provider_session_round_trip_and_eviction(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool() as pool:
        conv = await conversation_create(pool, butler_name="switchboard", first_message="hi")

        # No provider session recorded yet.
        assert (
            await conversation_get_provider_session(pool, conv["id"], butler_name="switchboard")
            is None
        )

        await conversation_set_provider_session(
            pool,
            conv["id"],
            butler_name="switchboard",
            provider_session_id="claude-session-abc",
            provider_runtime_type="claude",
        )

        stored = await conversation_get_provider_session(
            pool, conv["id"], butler_name="switchboard"
        )
        assert stored is not None
        assert stored["provider_session_id"] == "claude-session-abc"
        assert stored["provider_runtime_type"] == "claude"
        assert stored["provider_session_updated_at"] is not None

        # A later turn overwrites the handle -- one memory per thread.
        await conversation_set_provider_session(
            pool,
            conv["id"],
            butler_name="switchboard",
            provider_session_id="claude-session-def",
            provider_runtime_type="claude",
        )
        updated = await conversation_get_provider_session(
            pool, conv["id"], butler_name="switchboard"
        )
        assert updated["provider_session_id"] == "claude-session-def"

        # Eviction (fall-back-to-cold) clears the handle entirely.
        await conversation_clear_provider_session(pool, conv["id"], butler_name="switchboard")
        assert (
            await conversation_get_provider_session(pool, conv["id"], butler_name="switchboard")
            is None
        )


async def test_provider_session_scoped_by_butler_name(
    migrated_core_postgres_pool,
) -> None:
    """Provider session writes/reads are scoped by (id, butler_name), like other mutations."""
    async with migrated_core_postgres_pool() as pool:
        conv = await conversation_create(pool, butler_name="finance", first_message="hi")

        await conversation_set_provider_session(
            pool,
            conv["id"],
            butler_name="wrong-butler",
            provider_session_id="claude-session-xyz",
            provider_runtime_type="claude",
        )

        # The write was scoped away from the real owner -- nothing landed.
        assert (
            await conversation_get_provider_session(pool, conv["id"], butler_name="finance") is None
        )
