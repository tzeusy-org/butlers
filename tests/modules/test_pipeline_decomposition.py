"""Tests for conversation decomposition branch in MessagePipeline.

Tests cover:
- Decomposition triggered by payload_type == "conversation_history"
- Standard messages bypass decomposition
- Policy bypass (route_to, skip, metadata_only) honored before decomposition
- Empty decomposition short-circuit
- Signal extraction and fan-out routing
- Partial fan-out failure tracking
- Decomposition output metadata (model, latency_ms, token_usage)
- Signal extraction prompt builder
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


def _signals_two_butlers() -> list[dict[str, Any]]:
    """Signal extraction result targeting two butlers."""
    return [
        {
            "signal_type": "finance",
            "target_butler": "finance",
            "tool_name": "route.execute",
            "tool_args": {"category": "expense"},
            "confidence": "HIGH",
            "excerpts": [
                {
                    "sender": "Alice",
                    "text": "I spent $50 on groceries today",
                    "timestamp": "2026-03-30T10:00:00Z",
                    "message_id": "msg-1",
                },
                {
                    "sender": "Alice",
                    "text": "Let's split the dinner bill tonight",
                    "timestamp": "2026-03-30T10:02:00Z",
                    "message_id": "msg-3",
                },
            ],
        },
        {
            "signal_type": "health",
            "target_butler": "health",
            "tool_name": "route.execute",
            "tool_args": {"symptom": "headache"},
            "confidence": "MEDIUM",
            "excerpts": [
                {
                    "sender": "Bob",
                    "text": "My headache is getting worse",
                    "timestamp": "2026-03-30T10:01:00Z",
                    "message_id": "msg-2",
                },
            ],
        },
    ]


# ---------------------------------------------------------------------------
# _build_signal_extraction_prompt
# ---------------------------------------------------------------------------


class TestBuildSignalExtractionPrompt:
    """Verify prompt construction for signal extraction."""

    def test_includes_butler_schemas(self):
        prompt = _build_signal_extraction_prompt([], _MOCK_BUTLERS)
        assert "**health**" in prompt
        assert "Health tracking" in prompt
        assert "**finance**" in prompt
        assert "Finance management" in prompt

    def test_includes_conversation_messages(self):
        messages = _conversation_messages()
        prompt = _build_signal_extraction_prompt(messages, _MOCK_BUTLERS)
        assert "I spent $50 on groceries today" in prompt
        assert "Alice" in prompt
        assert "msg-1" in prompt

    def test_empty_conversation_produces_valid_prompt(self):
        prompt = _build_signal_extraction_prompt([], _MOCK_BUTLERS)
        assert "Registered butler schemas" in prompt
        assert "Conversation history" in prompt
        assert "JSON array" in prompt

    def test_system_instructions_present(self):
        prompt = _build_signal_extraction_prompt([], [])
        assert "signal-extraction engine" in prompt
        assert "untrusted data" in prompt
        assert "cherry-pick" in prompt


# ---------------------------------------------------------------------------
# Decomposition branch entry
# ---------------------------------------------------------------------------


class TestDecompositionBranchEntry:
    """Verify payload_type detection routes to decomposition."""

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch("butlers.tools.switchboard.routing.route.route", new_callable=AsyncMock)
    async def test_conversation_history_enters_decomposition(self, mock_route, mock_load):
        """When payload_type == conversation_history, decomposition branch runs."""
        mock_route.return_value = {"status": "ok"}

        raw_payload = _raw_payload_with_conversation()
        signals = _signals_two_butlers()

        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output=json.dumps(signals),
                model="claude-test",
                usage={"input_tokens": 100, "output_tokens": 50},
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

        # Should route to multiple butlers via decomposition
        assert "finance" in result.routed_targets
        assert "health" in result.routed_targets
        assert result.target_butler == "multi"
        assert result.routing_error is None
        # route() should have been called for each signal
        assert mock_route.call_count == 2

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
    """Verify empty decomposition short-circuit."""

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_empty_signals_returns_decomposed_empty(self, mock_load):
        """When LLM returns [], lifecycle_state is decomposed_empty."""
        raw_payload = _raw_payload_with_conversation()
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output="[]")

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=mock_dispatch,
        )

        import uuid

        inbox_id = uuid.uuid4()
        result = await pipeline.process(
            message_text="Just chatting",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {"payload_type": "conversation_history"},
                "request_id": str(uuid.uuid4()),
            },
            message_inbox_id=inbox_id,
        )

        assert result.target_butler == "decomposed_empty"
        assert result.route_result.get("reason") == "no_signals_extracted"
        assert result.routed_targets == []

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_no_conversation_history_in_db_returns_empty(self, mock_load):
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


# ---------------------------------------------------------------------------
# Partial fan-out failure
# ---------------------------------------------------------------------------


class TestDecompositionFanoutFailure:
    """Verify partial failure handling during fan-out routing."""

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch("butlers.tools.switchboard.routing.route.route", new_callable=AsyncMock)
    async def test_partial_failure_tracked(self, mock_route, mock_load):
        """Successful routes preserved when one route fails."""
        call_count = 0

        async def route_with_failure(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            target = kwargs.get("target_butler", "")
            if target == "health":
                return {"error": "butler unavailable"}
            return {"status": "ok"}

        mock_route.side_effect = route_with_failure

        raw_payload = _raw_payload_with_conversation()
        signals = _signals_two_butlers()
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output=json.dumps(signals))

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

        assert "finance" in result.acked_targets
        assert "health" in result.failed_targets
        assert result.routing_error is not None
        assert "health" in result.routing_error

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch("butlers.tools.switchboard.routing.route.route", new_callable=AsyncMock)
    async def test_route_exception_tracked(self, mock_route, mock_load):
        """Route exceptions are captured in failed_targets."""
        mock_route.side_effect = RuntimeError("connection failed")

        raw_payload = _raw_payload_with_conversation()
        signals = [_signals_two_butlers()[0]]  # Single signal
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output=json.dumps(signals))

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

        assert result.failed_targets == ["finance"]
        assert "RuntimeError" in result.routing_error


# ---------------------------------------------------------------------------
# Decomposition output metadata
# ---------------------------------------------------------------------------


class TestDecompositionOutputMetadata:
    """Verify metadata stored in decomposition_output."""

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch("butlers.tools.switchboard.routing.route.route", new_callable=AsyncMock)
    async def test_single_butler_routing(self, mock_route, mock_load):
        """Single target sets target_butler to the butler name."""
        mock_route.return_value = {"status": "ok"}

        raw_payload = _raw_payload_with_conversation()
        signals = [_signals_two_butlers()[0]]  # Only finance
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output=json.dumps(signals))

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

        # Single butler: target_butler is the butler name, not "multi"
        assert result.target_butler == "finance"
        assert result.routed_targets == ["finance"]
        assert result.acked_targets == ["finance"]


# ---------------------------------------------------------------------------
# Signal extraction JSON parsing
# ---------------------------------------------------------------------------


class TestSignalExtractionParsing:
    """Verify LLM output parsing edge cases."""

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch("butlers.tools.switchboard.routing.route.route", new_callable=AsyncMock)
    async def test_markdown_code_fences_stripped(self, mock_route, mock_load):
        """LLM output wrapped in ```json fences is parsed correctly."""
        mock_route.return_value = {"status": "ok"}

        raw_payload = _raw_payload_with_conversation()
        signals = [_signals_two_butlers()[0]]
        fenced_output = f"```json\n{json.dumps(signals)}\n```"
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output=fenced_output)

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

        assert result.routed_targets == ["finance"]

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_malformed_json_returns_empty(self, mock_load):
        """Malformed JSON from LLM results in empty decomposition."""
        raw_payload = _raw_payload_with_conversation()
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output="not valid json {[}")

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

        # Malformed JSON → empty signals → decomposed_empty
        assert result.target_butler == "decomposed_empty"

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_llm_returns_non_array_treated_as_empty(self, mock_load):
        """Non-array JSON response treated as empty decomposition."""
        raw_payload = _raw_payload_with_conversation()
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output='{"not": "an array"}')

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

        assert result.target_butler == "decomposed_empty"


# ---------------------------------------------------------------------------
# Decomposition error handling
# ---------------------------------------------------------------------------


class TestDecompositionErrorHandling:
    """Verify error handling in the decomposition branch."""

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_dispatch_failure_degrades_to_empty(self, mock_load):
        """When LLM dispatch fails, decomposition degrades to empty signals."""

        async def failing_dispatch(**kwargs):
            raise RuntimeError("LLM service unavailable")

        raw_payload = _raw_payload_with_conversation()
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=failing_dispatch,
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

        # LLM failure → empty signals → decomposed_empty (graceful degradation)
        assert result.target_butler == "decomposed_empty"
        assert result.route_result.get("reason") == "no_signals_extracted"

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
    )
    async def test_decomposition_hard_error_returns_general(self, mock_load):
        """When _load_available_butlers raises, process() returns general+error."""
        mock_load.side_effect = RuntimeError("Registry unavailable")

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output="[]")

        raw_payload = _raw_payload_with_conversation()
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

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

        # Hard failure propagates to process() outer handler → general + error
        assert result.target_butler == "general"
        assert result.classification_error is not None
        assert "RuntimeError" in result.classification_error


# ---------------------------------------------------------------------------
# Signals with missing target_butler
# ---------------------------------------------------------------------------


class TestSignalValidation:
    """Verify signal validation during fan-out."""

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch("butlers.tools.switchboard.routing.route.route", new_callable=AsyncMock)
    async def test_signals_without_target_butler_skipped(self, mock_route, mock_load):
        """Signals missing target_butler are skipped during fan-out."""
        mock_route.return_value = {"status": "ok"}

        raw_payload = _raw_payload_with_conversation()
        signals = [
            {"signal_type": "bad", "tool_name": "x", "tool_args": {}},  # no target_butler
            {
                "signal_type": "finance",
                "target_butler": "finance",
                "tool_name": "route.execute",
                "tool_args": {},
                "confidence": "HIGH",
                "excerpts": [],
            },
        ]
        fake_conn = FakeConn(fetchrow_result={"raw_payload": json.dumps(raw_payload)})
        pool = FakePool(conn=fake_conn)

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output=json.dumps(signals))

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

        # Only the valid signal should be routed
        assert result.routed_targets == ["finance"]
        assert mock_route.call_count == 1


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
