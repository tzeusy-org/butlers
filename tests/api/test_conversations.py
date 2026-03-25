"""Unit tests for conversation persistence layer and envelope construction.

Tests data access functions in butlers.api.conversations and envelope
construction in butlers.api.conversation_envelope using mocked asyncpg
connections — no real database required.

Covers tasks 3.1–3.4 and 4.1–4.3 from the dashboard-conversational-input spec.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from butlers.api.conversation_envelope import build_dashboard_envelope
from butlers.api.conversations import (
    build_conversation_context,
    conversation_create,
    conversation_get,
    conversation_list,
    conversation_search,
    conversation_summary,
    conversation_update,
    format_context_preamble,
    generate_conversation_title,
    message_create,
    message_get,
    message_list,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BUTLER = "atlas"
_NOW = datetime.now(tz=UTC)

# ---------------------------------------------------------------------------
# Row factories — mirror asyncpg Record shape as dicts
# ---------------------------------------------------------------------------


def _make_conv_row(
    *,
    conv_id: UUID | None = None,
    butler_name: str = _BUTLER,
    title: str = "New conversation",
    status: str = "active",
    message_count: int = 0,
    total_input_tokens: int = 0,
    total_output_tokens: int = 0,
    total_duration_ms: int = 0,
) -> dict:
    return {
        "id": conv_id or uuid4(),
        "butler_name": butler_name,
        "title": title,
        "status": status,
        "created_at": _NOW,
        "updated_at": _NOW,
        "message_count": message_count,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_duration_ms": total_duration_ms,
    }


def _make_msg_row(
    *,
    msg_id: UUID | None = None,
    conversation_id: UUID | None = None,
    role: str = "user",
    content: str = "Hello",
    session_id: UUID | None = None,
    model_name: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    duration_ms: int | None = None,
    tool_calls=None,
    error: str | None = None,
    request_id: UUID | None = None,
) -> dict:
    return {
        "id": msg_id or uuid4(),
        "conversation_id": conversation_id or uuid4(),
        "role": role,
        "content": content,
        "created_at": _NOW,
        "session_id": session_id,
        "model_name": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_ms": duration_ms,
        "tool_calls": tool_calls,
        "error": error,
        "request_id": request_id,
    }


def _mock_conn(
    *,
    fetchrow_return=None,
    fetchval_return=None,
    fetch_return=None,
    execute_return=None,
) -> MagicMock:
    """Build a mock asyncpg connection with configurable returns."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=_as_record(fetchrow_return) if fetchrow_return else None)
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.fetch = AsyncMock(return_value=[_as_record(r) for r in (fetch_return or [])])
    conn.execute = AsyncMock(return_value=execute_return)
    return conn


def _as_record(row: dict) -> MagicMock:
    """Wrap a dict as a MagicMock that supports item access like asyncpg.Record."""
    rec = MagicMock()
    rec.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    # Make sure .get() works too if ever needed
    rec.get = MagicMock(side_effect=lambda key, default=None: row.get(key, default))
    return rec


# ===========================================================================
# generate_conversation_title
# ===========================================================================


class TestGenerateConversationTitle:
    def test_short_message_returned_unchanged(self) -> None:
        assert generate_conversation_title("Hello!") == "Hello!"

    def test_exact_boundary_not_truncated(self) -> None:
        text = "a" * 80
        assert generate_conversation_title(text) == text

    def test_long_message_truncated_with_ellipsis(self) -> None:
        text = "word " * 30  # 150 chars
        result = generate_conversation_title(text)
        assert len(result) <= 82  # 80 + ellipsis char
        assert result.endswith("…")

    def test_truncates_at_word_boundary(self) -> None:
        text = "the quick brown fox jumps over the lazy dog and keeps going forever and ever yeah"
        result = generate_conversation_title(text)
        assert not result.endswith(" …")  # no trailing space before ellipsis
        # Should end with a word then ellipsis
        assert result.endswith("…")

    def test_strips_leading_trailing_whitespace(self) -> None:
        result = generate_conversation_title("  hello  ")
        assert result == "hello"

    def test_custom_max_length(self) -> None:
        text = "one two three four five six"
        result = generate_conversation_title(text, max_length=10)
        assert len(result) <= 12  # 10 + ellipsis


# ===========================================================================
# conversation_create
# ===========================================================================


class TestConversationCreate:
    async def test_returns_conversation_dict(self) -> None:
        conv_id = uuid4()
        row = _make_conv_row(conv_id=conv_id, butler_name=_BUTLER, title="Say hello")
        conn = _mock_conn(fetchrow_return=row)

        result = await conversation_create(
            conn,
            conversation_id=conv_id,
            butler_name=_BUTLER,
            title="Say hello",
        )

        assert result["id"] == conv_id
        assert result["butler_name"] == _BUTLER
        assert result["title"] == "Say hello"
        assert result["status"] == "active"

    async def test_raises_on_missing_row(self) -> None:
        conn = _mock_conn(fetchrow_return=None)
        conn.fetchrow = AsyncMock(return_value=None)

        with pytest.raises(RuntimeError, match="Failed to insert"):
            await conversation_create(
                conn,
                conversation_id=uuid4(),
                butler_name=_BUTLER,
                title="Test",
            )


# ===========================================================================
# conversation_get
# ===========================================================================


class TestConversationGet:
    async def test_returns_conversation_when_found(self) -> None:
        conv_id = uuid4()
        row = _make_conv_row(conv_id=conv_id)
        conn = _mock_conn(fetchrow_return=row)

        result = await conversation_get(conn, conversation_id=conv_id, butler_name=_BUTLER)

        assert result is not None
        assert result["id"] == conv_id

    async def test_returns_none_when_not_found(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)

        result = await conversation_get(conn, conversation_id=uuid4(), butler_name=_BUTLER)

        assert result is None


# ===========================================================================
# conversation_list
# ===========================================================================


class TestConversationList:
    async def test_returns_paginated_results(self) -> None:
        rows = [_make_conv_row(), _make_conv_row()]
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=2)
        conn.fetch = AsyncMock(return_value=[_as_record(r) for r in rows])

        results, total = await conversation_list(conn, butler_name=_BUTLER)

        assert total == 2
        assert len(results) == 2

    async def test_default_status_filter_active(self) -> None:
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=0)
        conn.fetch = AsyncMock(return_value=[])

        _, _ = await conversation_list(conn, butler_name=_BUTLER)

        # Check that fetchval was called (the count query includes status filter)
        assert conn.fetchval.called
        query_arg = conn.fetchval.call_args[0][0]
        assert "status" in query_arg

    async def test_status_all_skips_filter(self) -> None:
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=0)
        conn.fetch = AsyncMock(return_value=[])

        _, _ = await conversation_list(conn, butler_name=_BUTLER, status="all")

        query_arg = conn.fetchval.call_args[0][0]
        # Should not add status = $N clause when status is "all"
        assert "status =" not in query_arg.lower()
        # Count query should still contain butler_name
        assert "butler_name" in query_arg

    async def test_returns_empty_when_no_results(self) -> None:
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=0)
        conn.fetch = AsyncMock(return_value=[])

        results, total = await conversation_list(conn, butler_name=_BUTLER)

        assert results == []
        assert total == 0


# ===========================================================================
# conversation_update
# ===========================================================================


class TestConversationUpdate:
    async def test_update_title(self) -> None:
        conv_id = uuid4()
        updated_row = _make_conv_row(conv_id=conv_id, title="New title")
        conn = _mock_conn(fetchrow_return=updated_row)

        result = await conversation_update(
            conn,
            conversation_id=conv_id,
            butler_name=_BUTLER,
            title="New title",
        )

        assert result is not None
        assert result["title"] == "New title"

    async def test_update_status(self) -> None:
        conv_id = uuid4()
        updated_row = _make_conv_row(conv_id=conv_id, status="archived")
        conn = _mock_conn(fetchrow_return=updated_row)

        result = await conversation_update(
            conn,
            conversation_id=conv_id,
            butler_name=_BUTLER,
            status="archived",
        )

        assert result is not None
        assert result["status"] == "archived"

    async def test_returns_none_when_not_found(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)

        result = await conversation_update(
            conn,
            conversation_id=uuid4(),
            butler_name=_BUTLER,
            title="Ghost",
        )

        assert result is None


# ===========================================================================
# conversation_search
# ===========================================================================


class TestConversationSearch:
    async def test_returns_results_with_snippet(self) -> None:
        conv_id = uuid4()
        row_data = _make_conv_row(conv_id=conv_id)
        row_data["snippet"] = "Hello world, this is a match"

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[_as_record(row_data)])

        results = await conversation_search(conn, butler_name=_BUTLER, query="hello")

        assert len(results) == 1
        assert results[0]["snippet"] == "Hello world, this is a match"
        assert results[0]["id"] == conv_id

    async def test_returns_empty_list_when_no_matches(self) -> None:
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])

        results = await conversation_search(conn, butler_name=_BUTLER, query="no match")

        assert results == []


# ===========================================================================
# conversation_summary
# ===========================================================================


class TestConversationSummary:
    async def test_returns_aggregate_stats(self) -> None:
        summary_row = {
            "total_conversations": 10,
            "active_conversations": 7,
            "total_messages": 42,
            "total_input_tokens": 1000,
            "total_output_tokens": 2000,
            "total_duration_ms": 5000,
        }
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=_as_record(summary_row))

        result = await conversation_summary(conn, butler_name=_BUTLER)

        assert result["total_conversations"] == 10
        assert result["active_conversations"] == 7
        assert result["total_messages"] == 42
        assert result["total_input_tokens"] == 1000
        assert result["total_output_tokens"] == 2000
        assert result["total_duration_ms"] == 5000

    async def test_returns_zeros_when_no_conversations(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)

        result = await conversation_summary(conn, butler_name=_BUTLER)

        assert result["total_conversations"] == 0
        assert result["total_messages"] == 0


# ===========================================================================
# message_create
# ===========================================================================


class TestMessageCreate:
    async def test_creates_user_message(self) -> None:
        msg_id = uuid4()
        conv_id = uuid4()
        row = _make_msg_row(msg_id=msg_id, conversation_id=conv_id, role="user", content="Hi!")
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=_as_record(row))
        conn.execute = AsyncMock()

        result = await message_create(
            conn,
            message_id=msg_id,
            conversation_id=conv_id,
            role="user",
            content="Hi!",
        )

        assert result["id"] == msg_id
        assert result["role"] == "user"
        assert result["content"] == "Hi!"

    async def test_creates_assistant_message_with_tokens(self) -> None:
        msg_id = uuid4()
        conv_id = uuid4()
        session_id = uuid4()
        row = _make_msg_row(
            msg_id=msg_id,
            conversation_id=conv_id,
            role="assistant",
            content="I can help!",
            session_id=session_id,
            model_name="claude-opus-4-5",
            input_tokens=100,
            output_tokens=50,
            duration_ms=1234,
        )
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=_as_record(row))
        conn.execute = AsyncMock()

        result = await message_create(
            conn,
            message_id=msg_id,
            conversation_id=conv_id,
            role="assistant",
            content="I can help!",
            session_id=session_id,
            model_name="claude-opus-4-5",
            input_tokens=100,
            output_tokens=50,
            duration_ms=1234,
        )

        assert result["model_name"] == "claude-opus-4-5"
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        # Verify that execute was called to update aggregates
        assert conn.execute.called

    async def test_aggregate_update_called_for_user_message(self) -> None:
        msg_id = uuid4()
        conv_id = uuid4()
        row = _make_msg_row(msg_id=msg_id, conversation_id=conv_id, role="user")
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=_as_record(row))
        conn.execute = AsyncMock()

        await message_create(
            conn,
            message_id=msg_id,
            conversation_id=conv_id,
            role="user",
            content="Hey",
        )

        # execute should be called once (increment message_count only for user)
        assert conn.execute.call_count == 1
        # The SQL should NOT update token columns for user messages
        sql_called = conn.execute.call_args[0][0]
        assert "total_input_tokens" not in sql_called

    async def test_aggregate_update_includes_tokens_for_assistant(self) -> None:
        msg_id = uuid4()
        conv_id = uuid4()
        row = _make_msg_row(msg_id=msg_id, conversation_id=conv_id, role="assistant")
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=_as_record(row))
        conn.execute = AsyncMock()

        await message_create(
            conn,
            message_id=msg_id,
            conversation_id=conv_id,
            role="assistant",
            content="Response",
            input_tokens=10,
            output_tokens=20,
            duration_ms=500,
        )

        sql_called = conn.execute.call_args[0][0]
        assert "total_input_tokens" in sql_called
        assert "total_output_tokens" in sql_called

    async def test_raises_on_missing_insert_row(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        conn.execute = AsyncMock()

        with pytest.raises(RuntimeError, match="Failed to insert message"):
            await message_create(
                conn,
                message_id=uuid4(),
                conversation_id=uuid4(),
                role="user",
                content="Hi",
            )

    async def test_tool_calls_serialized_to_json(self) -> None:
        """tool_calls list should be passed as JSON string to asyncpg."""
        msg_id = uuid4()
        conv_id = uuid4()
        tool_calls = [{"tool": "get_weather", "result": "sunny"}]
        row = _make_msg_row(msg_id=msg_id, role="assistant", tool_calls=tool_calls)
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=_as_record(row))
        conn.execute = AsyncMock()

        await message_create(
            conn,
            message_id=msg_id,
            conversation_id=conv_id,
            role="assistant",
            content="Weather response",
            tool_calls=tool_calls,
        )

        # Check that the 10th positional arg to fetchrow is a JSON string
        call_args = conn.fetchrow.call_args[0]
        # $10 is index 10 (0-indexed: query=0, then params 1-12)
        # tool_calls is the 10th param (index 10)
        tool_calls_arg = call_args[10]
        parsed = json.loads(tool_calls_arg)
        assert parsed == tool_calls


# ===========================================================================
# message_list
# ===========================================================================


class TestMessageList:
    async def test_returns_none_when_conversation_not_found(self) -> None:
        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=[None])  # EXISTS check returns None
        conn.fetch = AsyncMock(return_value=[])

        result = await message_list(
            conn,
            conversation_id=uuid4(),
            butler_name=_BUTLER,
        )

        assert result is None

    async def test_returns_paginated_messages(self) -> None:
        conv_id = uuid4()
        msg_rows = [
            _make_msg_row(conversation_id=conv_id, role="user", content="Hello"),
            _make_msg_row(conversation_id=conv_id, role="assistant", content="Hi there"),
        ]
        conn = AsyncMock()
        # First fetchval: EXISTS check → 1; second fetchval: count → 2
        conn.fetchval = AsyncMock(side_effect=[1, 2])
        conn.fetch = AsyncMock(return_value=[_as_record(r) for r in msg_rows])

        result = await message_list(conn, conversation_id=conv_id, butler_name=_BUTLER)

        assert result is not None
        messages, total = result
        assert total == 2
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"


# ===========================================================================
# message_get
# ===========================================================================


class TestMessageGet:
    async def test_returns_message_when_found(self) -> None:
        msg_id = uuid4()
        row = _make_msg_row(msg_id=msg_id, role="user", content="Test")
        conn = _mock_conn(fetchrow_return=row)

        result = await message_get(conn, message_id=msg_id)

        assert result is not None
        assert result["id"] == msg_id
        assert result["content"] == "Test"

    async def test_returns_none_when_not_found(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)

        result = await message_get(conn, message_id=uuid4())

        assert result is None

    async def test_tool_calls_parsed_from_json_string(self) -> None:
        msg_id = uuid4()
        tool_calls_data = [{"tool": "search", "result": "42"}]
        row = _make_msg_row(msg_id=msg_id, role="assistant", tool_calls=json.dumps(tool_calls_data))
        conn = _mock_conn(fetchrow_return=row)

        result = await message_get(conn, message_id=msg_id)

        assert result is not None
        assert result["tool_calls"] == tool_calls_data


# ===========================================================================
# build_conversation_context
# ===========================================================================


class TestBuildConversationContext:
    async def test_returns_empty_string_when_no_messages(self) -> None:
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])

        result = await build_conversation_context(conn, conversation_id=uuid4())

        assert result == ""

    async def test_formats_messages_chronologically(self) -> None:
        conv_id = uuid4()
        # fetch returns in DESC order (most recent first); function reverses
        msg_rows = [
            {"role": "assistant", "content": "Hi, how can I help?"},
            {"role": "user", "content": "Hello butler"},
        ]
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[_as_record(r) for r in msg_rows])

        result = await build_conversation_context(conn, conversation_id=conv_id)

        assert "Prior conversation context" in result
        assert "User: Hello butler" in result
        assert "Assistant: Hi, how can I help?" in result
        # User should come before Assistant in chronological (reversed) order
        assert result.index("User:") < result.index("Assistant:")

    async def test_limits_to_max_pairs(self) -> None:
        conv_id = uuid4()
        # Simulate 6 messages returned from DB (max_pairs=3 → limit=6)
        msg_rows = [{"role": "user", "content": f"msg {i}"} for i in range(6)]
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[_as_record(r) for r in msg_rows])

        await build_conversation_context(conn, conversation_id=conv_id, max_pairs=3)

        # Verify the LIMIT in the query was max_pairs * 2 = 6
        call_args = conn.fetch.call_args[0]
        limit_arg = call_args[2]  # $2 is the LIMIT param
        assert limit_arg == 6

    async def test_includes_context_markers(self) -> None:
        conv_id = uuid4()
        msg_rows = [{"role": "user", "content": "Question"}]
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[_as_record(r) for r in msg_rows])

        result = await build_conversation_context(conn, conversation_id=conv_id)

        assert "--- Prior conversation context ---" in result
        assert "--- End of prior context ---" in result


# ===========================================================================
# format_context_preamble
# ===========================================================================


class TestFormatContextPreamble:
    def test_returns_message_unchanged_when_no_context(self) -> None:
        result = format_context_preamble("", "Hello butler")
        assert result == "Hello butler"

    def test_prepends_context_to_message(self) -> None:
        context = "--- Prior context ---\nUser: Hi\nAssistant: Hello\n--- End ---"
        message = "How are you?"
        result = format_context_preamble(context, message)

        assert result.startswith(context)
        assert "New message: How are you?" in result

    def test_separator_between_context_and_message(self) -> None:
        result = format_context_preamble("context", "new message")
        assert "\n\n" in result


# ===========================================================================
# build_dashboard_envelope
# ===========================================================================


class TestBuildDashboardEnvelope:
    def test_schema_version_is_ingest_v1(self) -> None:
        conv_id = uuid4()
        msg_id = uuid4()
        env = build_dashboard_envelope(conv_id, msg_id, "Hello")
        assert env["schema_version"] == "ingest.v1"

    def test_source_channel_is_dashboard(self) -> None:
        env = build_dashboard_envelope(uuid4(), uuid4(), "Hello")
        assert env["source"]["channel"] == "dashboard"

    def test_source_provider_is_internal(self) -> None:
        env = build_dashboard_envelope(uuid4(), uuid4(), "Hello")
        assert env["source"]["provider"] == "internal"

    def test_endpoint_identity_includes_conversation_id(self) -> None:
        conv_id = uuid4()
        env = build_dashboard_envelope(conv_id, uuid4(), "Hello")
        assert env["source"]["endpoint_identity"] == f"dashboard:web:{conv_id}"

    def test_event_external_event_id_is_message_id(self) -> None:
        msg_id = uuid4()
        env = build_dashboard_envelope(uuid4(), msg_id, "Hello")
        assert env["event"]["external_event_id"] == str(msg_id)

    def test_event_external_thread_id_is_conversation_id(self) -> None:
        conv_id = uuid4()
        env = build_dashboard_envelope(conv_id, uuid4(), "Hello")
        assert env["event"]["external_thread_id"] == str(conv_id)

    def test_event_observed_at_is_set(self) -> None:
        env = build_dashboard_envelope(uuid4(), uuid4(), "Hello")
        assert "observed_at" in env["event"]
        assert env["event"]["observed_at"]  # non-empty

    def test_sender_identity_is_dashboard_operator(self) -> None:
        env = build_dashboard_envelope(uuid4(), uuid4(), "Hello")
        assert env["sender"]["identity"] == "dashboard:operator"

    def test_payload_normalized_text_is_message(self) -> None:
        env = build_dashboard_envelope(uuid4(), uuid4(), "Hello butler")
        assert env["payload"]["normalized_text"] == "Hello butler"

    def test_payload_raw_has_source_dashboard(self) -> None:
        env = build_dashboard_envelope(uuid4(), uuid4(), "Hello")
        assert env["payload"]["raw"]["source"] == "dashboard"

    def test_payload_raw_has_conversation_and_message_ids(self) -> None:
        conv_id = uuid4()
        msg_id = uuid4()
        env = build_dashboard_envelope(conv_id, msg_id, "Hello")
        assert env["payload"]["raw"]["conversation_id"] == str(conv_id)
        assert env["payload"]["raw"]["message_id"] == str(msg_id)

    def test_payload_raw_message_matches_text(self) -> None:
        env = build_dashboard_envelope(uuid4(), uuid4(), "My question")
        assert env["payload"]["raw"]["message"] == "My question"

    def test_control_policy_tier_is_interactive(self) -> None:
        env = build_dashboard_envelope(uuid4(), uuid4(), "Hello")
        assert env["control"]["policy_tier"] == "interactive"

    def test_control_ingestion_tier_is_full(self) -> None:
        env = build_dashboard_envelope(uuid4(), uuid4(), "Hello")
        assert env["control"]["ingestion_tier"] == "full"

    def test_custom_observed_at(self) -> None:
        ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        env = build_dashboard_envelope(uuid4(), uuid4(), "Hello", observed_at=ts)
        assert "2026-01-15" in env["event"]["observed_at"]

    def test_conversation_context_embedded_in_raw(self) -> None:
        ctx = "--- Prior context ---\nUser: Hi\n--- End ---"
        env = build_dashboard_envelope(uuid4(), uuid4(), "Follow up", conversation_context=ctx)
        assert env["payload"]["raw"]["conversation_context"] == ctx

    def test_no_context_key_in_raw_when_empty(self) -> None:
        env = build_dashboard_envelope(uuid4(), uuid4(), "First message", conversation_context="")
        assert "conversation_context" not in env["payload"]["raw"]

    def test_returns_dict_type(self) -> None:
        env = build_dashboard_envelope(uuid4(), uuid4(), "Hello")
        assert isinstance(env, dict)

    def test_all_required_top_level_keys_present(self) -> None:
        env = build_dashboard_envelope(uuid4(), uuid4(), "Hello")
        required = {"schema_version", "source", "event", "sender", "payload", "control"}
        assert required.issubset(env.keys())

    def test_with_follow_up_message_including_context(self) -> None:
        """Simulate a follow-up message with context preamble in normalized_text."""
        context = "--- Prior conversation context ---\nUser: Hello\nAssistant: Hi\n--- End ---"
        from butlers.api.conversations import format_context_preamble

        combined = format_context_preamble(context, "Follow up question")
        env = build_dashboard_envelope(uuid4(), uuid4(), combined, conversation_context=context)
        assert "Follow up question" in env["payload"]["normalized_text"]
        assert "Prior conversation context" in env["payload"]["normalized_text"]
