"""Tests for conversation decomposition branch in MessagePipeline.

Tests cover:
- Decomposition triggered by payload_type == "conversation_history"
- Standard messages bypass decomposition
- Policy bypass (route_to, skip, metadata_only) honored before decomposition
- Empty conversation_history short-circuit to decomposed_empty
- Structured conversation history forwarded to standard routing prompt
- _format_decomp_conversation_history formatter
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.pipeline import (
    MessagePipeline,
    _build_signal_extraction_prompt,
)
from butlers.tools.switchboard.routing.contracts import (
    IngestControlV1,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_BUTLERS = [
    {"name": "health", "description": "Health tracking"},
    {"name": "finance", "description": "Finance management"},
    {"name": "general", "description": "General assistant"},
]


@dataclass
class FakeSpawnerResult:
    """Mimics SpawnerResult from butlers.core.spawner."""

    output: str | None = None
    success: bool = True
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None
    model: str | None = None
    usage: dict | None = None


class FakeConn:
    """Fake asyncpg connection for mocking pool.acquire()."""

    def __init__(self, rows: list[dict] | None = None, fetchrow_result: dict | None = None):
        self._rows = rows or []
        self._fetchrow_result = fetchrow_result

    async def execute(self, query, *args):
        pass

    async def fetchrow(self, query, *args):
        return self._fetchrow_result

    async def fetch(self, query, *args):
        return self._rows


class FakePool:
    """Fake asyncpg pool for mocking."""

    def __init__(self, conn: FakeConn | None = None):
        self._conn = conn or FakeConn()

    def acquire(self):
        return _FakeAcquire(self._conn)


class _FakeAcquire:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


def _conversation_messages() -> list[dict[str, Any]]:
    """Sample conversation history messages for testing."""
    return [
        {
            "sender": "Alice",
            "text": "I spent $50 on groceries today",
            "timestamp": "2026-03-30T10:00:00Z",
            "message_id": "msg-1",
        },
        {
            "sender": "Bob",
            "text": "My headache is getting worse",
            "timestamp": "2026-03-30T10:01:00Z",
            "message_id": "msg-2",
        },
        {
            "sender": "Alice",
            "text": "Let's split the dinner bill tonight",
            "timestamp": "2026-03-30T10:02:00Z",
            "message_id": "msg-3",
        },
    ]


def _raw_payload_with_conversation() -> dict[str, Any]:
    """Raw payload as stored in message_inbox for conversation_history batches."""
    return {
        "source": {"channel": "telegram_user_client"},
        "payload": {
            "raw": {"conversation_history": _conversation_messages()},
            "normalized_text": "Alice: groceries. Bob: headache. Alice: dinner.",
        },
        "control": {"payload_type": "conversation_history"},
    }


# ---------------------------------------------------------------------------
# _format_decomp_conversation_history (aliased as _build_signal_extraction_prompt)
# ---------------------------------------------------------------------------


class TestFormatDecompConversationHistory:
    """Verify conversation history formatting for routing context."""

    def test_includes_conversation_messages(self):
        result = _build_signal_extraction_prompt(_conversation_messages())
        assert "I spent $50 on groceries today" in result
        assert "Alice" in result
        assert "My headache is getting worse" in result

    def test_empty_messages_returns_empty_string(self):
        result = _build_signal_extraction_prompt([])
        assert result == ""

    def test_untrusted_data_warning_present(self):
        result = _build_signal_extraction_prompt(_conversation_messages())
        assert "UNTRUSTED USER DATA" in result

    def test_messages_fenced_in_code_blocks(self):
        result = _build_signal_extraction_prompt(_conversation_messages())
        assert "```" in result


# ---------------------------------------------------------------------------
# Decomposition branch entry
# ---------------------------------------------------------------------------


class TestDecompositionBranchEntry:
    """Verify payload_type detection routes through standard routing with
    structured conversation history as context."""

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_conversation_history_uses_standard_routing(self, mock_load):
        """When payload_type == conversation_history, CC calls route_to_butler
        with the structured conversation history as context."""
        raw_payload = _raw_payload_with_conversation()
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            # CC calls route_to_butler like the standard routing path
            return FakeSpawnerResult(
                output="Routed to finance and health.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "input": {"butler": "finance", "prompt": "expense tracking"},
                        "result": {"status": "accepted", "butler": "finance"},
                    },
                    {
                        "name": "route_to_butler",
                        "input": {"butler": "health", "prompt": "headache symptom"},
                        "result": {"status": "accepted", "butler": "health"},
                    },
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=mock_dispatch,
        )

        import uuid

        inbox_id = uuid.uuid4()
        result = await pipeline.process(
            message_text="Alice: groceries. Bob: headache.",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {
                    "payload_type": "conversation_history",
                    "source_thread_identity": "chat-123",
                },
                "request_id": str(uuid.uuid4()),
            },
            message_inbox_id=inbox_id,
        )

        assert "finance" in result.acked_targets
        assert "health" in result.acked_targets
        assert result.target_butler == "multi"
        assert result.routing_error is None

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_standard_message_bypasses_decomposition(self, mock_load):
        """Without payload_type, standard classification runs."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to general.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "general"},
                        "result": {"status": "ok"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
        )

        result = await pipeline.process("Hello world")

        assert result.target_butler == "general"
        # Standard CC routing path
        assert "cc_summary" in result.route_result


# ---------------------------------------------------------------------------
# Policy bypass honored before decomposition
# ---------------------------------------------------------------------------


class TestPolicyBypassBeforeDecomposition:
    """Verify triage decisions take precedence over decomposition."""

    @patch("butlers.tools.switchboard.routing.route.route", new_callable=AsyncMock)
    async def test_route_to_bypass_skips_decomposition(self, mock_route):
        """route_to triage decision bypasses decomposition even with payload_type."""
        mock_route.return_value = {"status": "ok"}

        async def mock_dispatch(**kwargs):
            # Should NOT be called for classification
            raise AssertionError("dispatch_fn should not be called for policy bypass")

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
        )

        import uuid

        result = await pipeline.process(
            message_text="test",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {
                    "payload_type": "conversation_history",
                    "triage_decision": "route_to",
                    "triage_target": "health",
                },
                "request_id": str(uuid.uuid4()),
            },
        )

        # Policy bypass takes precedence
        assert result.target_butler == "health"

    async def test_skip_triage_bypasses_decomposition(self):
        """skip triage decision bypasses decomposition."""

        async def mock_dispatch(**kwargs):
            raise AssertionError("dispatch_fn should not be called")

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
        )

        result = await pipeline.process(
            message_text="test",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {
                    "payload_type": "conversation_history",
                    "triage_decision": "skip",
                },
            },
        )

        assert result.target_butler == "skipped"


# ---------------------------------------------------------------------------
# Empty decomposition
# ---------------------------------------------------------------------------


class TestEmptyDecomposition:
    """Verify empty conversation_history short-circuit."""

    async def test_no_conversation_history_in_db_returns_empty(self):
        """When DB has no conversation_history, returns decomposed_empty."""
        empty_payload = {"source": {}, "payload": {"raw": {}}, "control": {}}
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(empty_payload)})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            raise AssertionError("Should not call LLM with no conversation")

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=mock_dispatch,
        )

        import uuid

        inbox_id = uuid.uuid4()
        result = await pipeline.process(
            message_text="test",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {"payload_type": "conversation_history"},
                "request_id": str(uuid.uuid4()),
            },
            message_inbox_id=inbox_id,
        )

        assert result.target_butler == "decomposed_empty"
        assert result.route_result.get("reason") == "no_conversation_history"

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_conversation_exists_but_no_route_calls_falls_back(self, mock_load):
        """When conversation exists but CC makes no route_to_butler calls,
        fallback to general (standard routing behavior, not decomposed_empty)."""
        raw_payload = _raw_payload_with_conversation()
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output="Nothing actionable here.", tool_calls=[])

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=mock_dispatch,
        )

        import uuid

        result = await pipeline.process(
            message_text="test",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {"payload_type": "conversation_history"},
                "request_id": str(uuid.uuid4()),
            },
            message_inbox_id=uuid.uuid4(),
        )

        # Standard fallback: no route_to_butler calls → general
        assert result.target_butler == "general"


# ---------------------------------------------------------------------------
# IngestControlV1 payload_type field
# ---------------------------------------------------------------------------


class TestIngestControlPayloadType:
    """Verify IngestControlV1 accepts payload_type field."""

    def test_payload_type_accepted(self):
        """IngestControlV1 accepts payload_type='conversation_history'."""
        control = IngestControlV1(payload_type="conversation_history")
        assert control.payload_type == "conversation_history"

    def test_payload_type_defaults_to_none(self):
        """IngestControlV1 defaults payload_type to None."""
        control = IngestControlV1()
        assert control.payload_type is None

    def test_invalid_payload_type_rejected(self):
        """IngestControlV1 rejects invalid payload_type values."""
        with pytest.raises(Exception):
            IngestControlV1(payload_type="invalid_type")


# ---------------------------------------------------------------------------
# metadata_only policy bypass
# ---------------------------------------------------------------------------


class TestMetadataOnlyPolicyBypass:
    """Verify metadata_only triage decision bypasses decomposition."""

    async def test_metadata_only_bypasses_decomposition(self):
        """metadata_only triage decision bypasses decomposition and LLM."""

        async def mock_dispatch(**kwargs):
            raise AssertionError("dispatch_fn should not be called for metadata_only bypass")

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
        )

        import uuid

        result = await pipeline.process(
            message_text="test",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {
                    "payload_type": "conversation_history",
                    "triage_decision": "metadata_only",
                },
                "request_id": str(uuid.uuid4()),
            },
        )

        assert result.target_butler == "metadata_only"
        assert result.route_result.get("triage_decision") == "metadata_only"
        assert result.route_result.get("policy_bypass") is True


# ---------------------------------------------------------------------------
# DB raw_payload as pre-parsed dict (not string)
# ---------------------------------------------------------------------------


class TestDecompositionRawPayloadDict:
    """Verify raw_payload pre-parsed as dict is handled correctly."""

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_raw_payload_as_dict_not_string(self, mock_load):
        """raw_payload stored as dict (not JSON string) is loaded without error."""
        raw_payload = _raw_payload_with_conversation()

        # DB returns dict directly (not JSON string)
        fake_conn = FakeConn(fetchrow_result={"raw_payload": raw_payload})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to finance.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "input": {"butler": "finance", "prompt": "expenses"},
                        "result": {"status": "accepted", "butler": "finance"},
                    },
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=mock_dispatch,
        )

        import uuid

        result = await pipeline.process(
            message_text="test",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {"payload_type": "conversation_history"},
                "request_id": str(uuid.uuid4()),
            },
            message_inbox_id=uuid.uuid4(),
        )

        assert result.acked_targets == ["finance"]
        assert result.target_butler == "finance"


# ---------------------------------------------------------------------------
# No message_inbox_id with conversation_history payload_type
# ---------------------------------------------------------------------------


class TestDecompositionNoInboxId:
    """Verify decomposition works when message_inbox_id is None."""

    async def test_no_inbox_id_returns_decomposed_empty_without_db_query(self):
        """With message_inbox_id=None, decomposition returns empty without DB lookup."""
        # Pool should never be acquired since inbox_id is None
        pool = FakePool(conn=FakeConn())

        async def mock_dispatch(**kwargs):
            raise AssertionError("Should not reach LLM with no conversation history")

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=mock_dispatch,
        )

        import uuid

        result = await pipeline.process(
            message_text="test",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {"payload_type": "conversation_history"},
                "request_id": str(uuid.uuid4()),
            },
            message_inbox_id=None,  # explicitly None
        )

        # No DB → no conversation_history → decomposed_empty
        assert result.target_butler == "decomposed_empty"
        assert result.route_result.get("reason") == "no_conversation_history"
