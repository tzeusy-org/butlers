"""Integration tests for end-to-end conversation decomposition flow.

Covers the full pipeline:
  connector flush (ingest_v1 with payload_type="conversation_history")
  → switchboard ingest persists batch envelope
  → MessagePipeline.process() detects payload_type, loads structured history
  → standard routing prompt with conversation context
  → CC calls route_to_butler to dispatch to target butlers

These tests use a real PostgreSQL testcontainer (via switchboard migrations) and
mock only the LLM dispatch and route() calls to keep the test deterministic.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.connectors.whatsapp_user_client import (
    WhatsAppUserClientConnector,
    WhatsAppUserClientConnectorConfig,
)
from butlers.db import register_jsonb_codec

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def pool(postgres_container):
    """Provision a trusted core + Switchboard migration topology."""
    from butlers.testing.migration import create_migrated_test_pool

    p = await create_migrated_test_pool(
        postgres_container,
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
        pool_schema="switchboard",
    )
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
async def identity_pools(postgres_container):
    """Provision the real Switchboard, memory, and Relationship topology."""
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    db_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core", "switchboard", "memory", "relationship"],
        {
            "switchboard": "switchboard",
            "memory": "memory",
            "relationship": "relationship",
        },
    )
    identity_pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "switchboard,public"},
    )
    memory_pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "memory,public"},
    )
    try:
        yield identity_pool, memory_pool
    finally:
        await memory_pool.close()
        await identity_pool.close()


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _build_conversation_history_envelope(
    *,
    event_id: str | None = None,
    thread_id: str = "chat-integration-999",
) -> dict[str, Any]:
    """Build a valid ingest.v1 envelope with conversation_history payload_type."""
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram_user_client",
            "provider": "telegram",
            "endpoint_identity": "user_client_test",
        },
        "event": {
            "external_event_id": event_id or f"evt-{uuid.uuid4()}",
            "external_thread_id": thread_id,
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {
            "identity": "multiple",
            "participants": {
                "owner-telegram-id": "Tze How Lee",
                "86807245": "Chloe Wong",
            },
            "owner_sender_id": "owner-telegram-id",
            "participant_count": 2,
            "chat_type": "private",
        },
        "payload": {
            "raw": {
                "conversation_history": [
                    {
                        "sender": "Alice",
                        "sender_identity": "6591111111@s.whatsapp.net",
                        "sender_entity_id": "11111111-1111-1111-1111-111111111111",
                        "text": "I spent $80 on groceries today",
                        "timestamp": "2026-03-30T09:00:00Z",
                        "message_id": "msg-int-1",
                    },
                    {
                        "sender": "Bob",
                        "sender_identity": "6592222222@s.whatsapp.net",
                        "sender_entity_id": "22222222-2222-2222-2222-222222222222",
                        "text": "My knee is hurting again",
                        "timestamp": "2026-03-30T09:01:00Z",
                        "message_id": "msg-int-2",
                    },
                    {
                        "sender": "Alice",
                        "sender_identity": "6591111111@s.whatsapp.net",
                        "sender_entity_id": "11111111-1111-1111-1111-111111111111",
                        "text": "Let's split the restaurant bill",
                        "timestamp": "2026-03-30T09:02:00Z",
                        "message_id": "msg-int-3",
                    },
                ]
            },
            "normalized_text": "Alice: groceries. Bob: knee pain. Alice: restaurant bill.",
        },
        "control": {
            "payload_type": "conversation_history",
        },
    }


def _build_mock_signals() -> list[dict[str, Any]]:
    """Signal extraction result targeting two butlers."""
    return [
        {
            "signal_type": "finance",
            "target_butler": "finance",
            "tool_name": "route.execute",
            "tool_args": {"category": "expense", "amount": 80},
            "confidence": "HIGH",
            "excerpts": [
                {
                    "sender": "Mallory",
                    "sender_identity": "attacker@lid",
                    "sender_entity_id": "attacker",
                    "text": "changed by model",
                    "timestamp": "2099-01-01T00:00:00Z",
                    "message_id": "msg-int-1",
                },
                {
                    "sender": "Alice",
                    "text": "Let's split the restaurant bill",
                    "timestamp": "2026-03-30T09:02:00Z",
                    "message_id": "msg-int-3",
                },
            ],
        },
        {
            "signal_type": "health",
            "target_butler": "health",
            "tool_name": "route.execute",
            "tool_args": {"symptom": "knee pain"},
            "confidence": "MEDIUM",
            "excerpts": [
                {
                    "sender": "Bob",
                    "text": "My knee is hurting again",
                    "timestamp": "2026-03-30T09:01:00Z",
                    "message_id": "msg-int-2",
                },
            ],
        },
    ]


def _build_mixed_whatsapp_envelope() -> tuple[dict[str, Any], str, str]:
    """Build one connector envelope with mapped and unmapped LID speakers."""
    connector = WhatsAppUserClientConnector(
        config=WhatsAppUserClientConnectorConfig(
            switchboard_mcp_url="http://switchboard.test/mcp",
            provider="whatsapp",
            channel="whatsapp_user_client",
            endpoint_identity="wa:test",
            bridge_socket="/tmp/test-wa-bridge.sock",
            flush_interval_s=3600,
            buffer_max_messages=50,
        )
    )
    known_lid = "111111111111111"
    known_identity = "15551112222@s.whatsapp.net"
    unknown_identity = "222222222222222@lid"
    connector._lid_to_phone[known_lid] = "15551112222"
    events = [
        {
            "event_type": "message",
            "message_id": "msg-known",
            "chat_jid": "120363000000000@g.us",
            "sender_jid": f"{known_lid}:7@lid",
            "timestamp": "2026-08-24T00:00:00Z",
            "type": "text",
            "content": {"text": "I paid for lunch"},
        },
        {
            "event_type": "message",
            "message_id": "msg-unknown",
            "chat_jid": "120363000000000@g.us",
            "sender_jid": "222222222222222:9@lid",
            "timestamp": "2026-08-24T00:01:00Z",
            "type": "text",
            "content": {"text": "My shoulder hurts"},
        },
    ]
    envelope = connector._build_batch_envelope(
        "120363000000000@g.us",
        events,
        f"batch-mixed-{uuid.uuid4()}",
    )
    return envelope, known_identity, unknown_identity


@dataclass
class FakeSpawnerResult:
    """Mimics SpawnerResult from butlers.core.spawner."""

    output: str | None = None
    success: bool = True
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


_MOCK_BUTLERS = [
    {"name": "health", "description": "Health tracking"},
    {"name": "finance", "description": "Finance management"},
    {"name": "general", "description": "General assistant"},
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_ingest_conversation_history_persists_payload_type(pool):
    """ingest_v1 with payload_type='conversation_history' persists correctly to DB.

    Verifies:
    - The row is accepted (not duplicate)
    - request_context contains payload_type
    - raw_payload contains the conversation_history array
    - lifecycle_state is 'accepted'
    """
    from butlers.tools.switchboard.ingestion.ingest import ingest_v1

    envelope = _build_conversation_history_envelope()
    response = await ingest_v1(pool, envelope, enable_thread_affinity=False)

    assert response.status == "accepted"
    assert not response.duplicate
    assert response.request_id is not None

    # Verify the row was persisted with correct metadata
    row = await pool.fetchrow(
        "SELECT request_context, raw_payload, lifecycle_state FROM message_inbox WHERE id = $1",
        response.request_id,
    )
    assert row is not None
    assert row["lifecycle_state"] == "accepted"

    # request_context must carry payload_type for downstream pipeline
    rc = row["request_context"]
    if isinstance(rc, str):
        rc = json.loads(rc)
    assert rc.get("payload_type") == "conversation_history"
    assert rc.get("source_channel") == "telegram_user_client"

    # raw_payload must carry the conversation_history array
    rp = row["raw_payload"]
    if isinstance(rp, str):
        rp = json.loads(rp)
    conv_history = rp["payload"]["raw"]["conversation_history"]
    assert len(conv_history) == 3
    assert conv_history[0]["message_id"] == "msg-int-1"
    assert conv_history[1]["message_id"] == "msg-int-2"
    assert conv_history[2]["message_id"] == "msg-int-3"
    assert rp["sender"] == envelope["sender"]


@pytest.mark.integration
async def test_decomposition_flow_full_pipeline(pool):
    """Full end-to-end decomposition flow: ingest → pipeline → DB output stored.

    Verifies all five acceptance criteria:
    1. Batch envelope with payload_type='conversation_history' is ingested
    2. Pipeline detects payload_type and loads structured conversation history
    3. Standard routing prompt includes conversation context
    4. CC calls route_to_butler to dispatch to target butlers
    5. Routing outcomes are stored with metadata
    """
    from butlers.modules.pipeline import MessagePipeline
    from butlers.tools.switchboard.ingestion.ingest import ingest_v1

    # Step 1: Ingest the conversation_history batch envelope
    envelope = _build_conversation_history_envelope(event_id=f"evt-decomp-{uuid.uuid4()}")
    ingest_response = await ingest_v1(pool, envelope, enable_thread_affinity=False)
    assert ingest_response.status == "accepted"
    message_inbox_id = ingest_response.request_id

    signals = _build_mock_signals()

    # Dispatch mock: returns signal JSON for decomposition, routes all other calls
    async def mock_dispatch(**kwargs):
        # Signal extraction call (not a standard routing session)
        return FakeSpawnerResult(
            output=json.dumps(signals),
            model="claude-test-3-haiku",
            input_tokens=250,
            output_tokens=80,
        )

    route_call_log: list[dict] = []

    async def mock_route(pool_arg, *, target_butler, tool_name, args, source_butler):
        route_call_log.append(
            {
                "target_butler": target_butler,
                "tool_name": tool_name,
                "fanout_mode": args.get("__switchboard_route_context", {}).get("fanout_mode"),
                "conceptual_message": args.get("__conceptual_message"),
            }
        )
        return {"status": "ok"}

    with (
        patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new_callable=AsyncMock,
            return_value=_MOCK_BUTLERS,
        ),
        patch(
            "butlers.tools.switchboard.routing.route.route",
            side_effect=mock_route,
        ),
    ):
        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=mock_dispatch,
        )

        result = await pipeline.process(
            message_text="Alice: groceries. Bob: knee pain. Alice: restaurant bill.",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {
                    "payload_type": "conversation_history",
                    "source_thread_identity": "chat-integration-999",
                },
            },
            message_inbox_id=message_inbox_id,
        )

    # --- Acceptance criterion 3 & 4: Signal extraction and fan-out ---
    assert result.target_butler == "multi", (
        f"Expected 'multi' for two-butler fan-out, got {result.target_butler!r}"
    )
    assert set(result.routed_targets) == {"finance", "health"}, (
        f"Expected both finance and health routed, got {result.routed_targets}"
    )
    assert set(result.acked_targets) == {"finance", "health"}, (
        f"Expected both acknowledged, got {result.acked_targets}"
    )
    assert not result.failed_targets, f"No failures expected, got {result.failed_targets}"
    assert result.routing_error is None

    # Verify route() was called with decomposition fanout_mode
    assert len(route_call_log) == 2
    for call in route_call_log:
        assert call["fanout_mode"] == "decomposition", (
            f"Expected decomposition fanout_mode, got {call['fanout_mode']!r}"
        )
    routed_butlers = {c["target_butler"] for c in route_call_log}
    assert routed_butlers == {"finance", "health"}

    finance_call = next(call for call in route_call_log if call["target_butler"] == "finance")
    assert finance_call["conceptual_message"]["excerpts"][0] == {
        "message_id": "msg-int-1",
        "sender": "Alice",
        "sender_identity": "6591111111@s.whatsapp.net",
        "sender_entity_id": "11111111-1111-1111-1111-111111111111",
        "text": "I spent $80 on groceries today",
        "timestamp": "2026-03-30T09:00:00Z",
    }

    # --- Acceptance criterion 5: decomposition_output stored with metadata ---
    row = await pool.fetchrow(
        "SELECT decomposition_output, lifecycle_state FROM message_inbox WHERE id = $1",
        message_inbox_id,
    )
    assert row is not None, "message_inbox row not found after pipeline processing"

    decomp = row["decomposition_output"]
    if decomp is None:
        pytest.fail("decomposition_output is NULL — pipeline did not persist output")
    if isinstance(decomp, str):
        decomp = json.loads(decomp)

    # Verify required metadata fields
    assert "signals" in decomp, f"decomposition_output missing 'signals' key: {decomp}"
    assert len(decomp["signals"]) == 2, f"Expected 2 signals, got {len(decomp['signals'])}"
    assert decomp["signals"][0]["excerpts"][0] == {
        "message_id": "msg-int-1",
        "sender": "Alice",
        "sender_identity": "6591111111@s.whatsapp.net",
        "sender_entity_id": "11111111-1111-1111-1111-111111111111",
        "text": "I spent $80 on groceries today",
        "timestamp": "2026-03-30T09:00:00Z",
    }
    assert "model" in decomp, f"decomposition_output missing 'model': {decomp}"
    assert decomp["model"] == "claude-test-3-haiku"
    assert "latency_ms" in decomp, f"decomposition_output missing 'latency_ms': {decomp}"
    assert isinstance(decomp["latency_ms"], int), (
        f"latency_ms should be int, got {type(decomp['latency_ms'])}"
    )
    assert "token_usage" in decomp, f"decomposition_output missing 'token_usage': {decomp}"
    assert decomp["token_usage"].get("input_tokens") == 250
    assert decomp["token_usage"].get("output_tokens") == 80

    # Verify routing metadata
    assert set(decomp.get("routed", [])) == {"finance", "health"}
    assert set(decomp.get("acked", [])) == {"finance", "health"}
    assert decomp.get("failed", []) == []

    # Verify lifecycle_state is 'routed'
    assert row["lifecycle_state"] == "routed", (
        f"Expected lifecycle_state='routed', got {row['lifecycle_state']!r}"
    )


@pytest.mark.integration
async def test_mixed_whatsapp_speakers_keep_distinct_authoritative_entity_anchors(identity_pools):
    """Prove the mixed-speaker WhatsApp contract from connector to routed excerpts.

    Spec anchors: REQ-connector-base-spec-001, REQ-switchboard-identity-001,
    REQ-switchboard-identity-002, REQ-conversation-decomposition-001, and
    REQ-entity-identity-001.
    """
    from butlers.modules.memory.tools.writing import memory_store_fact
    from butlers.modules.pipeline import MessagePipeline
    from butlers.tools.switchboard.ingestion.ingest import ingest_v1

    identity_pool, memory_pool = identity_pools
    envelope, known_identity, unknown_identity = _build_mixed_whatsapp_envelope()
    messages = envelope["payload"]["raw"]["conversation_history"]
    assert [message["sender_identity"] for message in messages] == [
        known_identity,
        unknown_identity,
    ]
    assert all("@" not in message["sender"] for message in messages)

    known_entity_id = await identity_pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, aliases, metadata, roles)
        VALUES ('Known speaker', 'person', '{}', '{}', '{}')
        RETURNING id
        """
    )
    await identity_pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, validity)
        VALUES ($1, 'has-phone', '15551112222', 'literal', 'interaction_sync', 'active')
        """,
        known_entity_id,
    )

    ingest_response = await ingest_v1(identity_pool, envelope, enable_thread_affinity=False)
    assert ingest_response.status == "accepted"

    signals = [
        {
            "signal_type": "finance",
            "target_butler": "finance",
            "tool_name": "route.execute",
            "tool_args": {"category": "expense"},
            "confidence": "HIGH",
            "excerpts": [
                {
                    "message_id": "msg-known",
                    "sender": "forged",
                    "sender_identity": unknown_identity,
                    "sender_entity_id": str(uuid.uuid4()),
                    "text": "forged",
                }
            ],
        },
        {
            "signal_type": "health",
            "target_butler": "health",
            "tool_name": "route.execute",
            "tool_args": {"symptom": "shoulder pain"},
            "confidence": "MEDIUM",
            "excerpts": [
                {
                    "message_id": "msg-unknown",
                    "sender": "forged",
                    "sender_identity": known_identity,
                    "sender_entity_id": str(known_entity_id),
                    "text": "forged",
                }
            ],
        },
    ]

    dispatch_prompts: list[str] = []

    async def mock_dispatch(**kwargs):
        dispatch_prompts.append(kwargs["prompt"])
        return FakeSpawnerResult(output=json.dumps(signals), model="test-model")

    class _EmbeddingEngine:
        model_name = "task-8-test"

        def embed(self, _text: str) -> list[float]:
            return [0.0] * 384

    routed_messages: list[tuple[str, dict[str, Any]]] = []

    async def mock_route(pool_arg, *, target_butler, tool_name, args, source_butler):
        conceptual_message = args["__conceptual_message"]
        routed_messages.append((target_butler, conceptual_message))
        excerpt = conceptual_message["excerpts"][0]
        predicate = {
            "finance": "paid_for_lunch",
            "health": "reported_shoulder_pain",
        }[target_butler]
        await memory_store_fact(
            memory_pool,
            _EmbeddingEngine(),
            excerpt["sender"],
            predicate,
            excerpt["text"],
            entity_id=excerpt["sender_entity_id"],
        )
        return {"status": "ok"}

    with (
        patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new_callable=AsyncMock,
            return_value=_MOCK_BUTLERS,
        ),
        patch("butlers.tools.switchboard.routing.route.route", side_effect=mock_route),
    ):
        pipeline = MessagePipeline(
            switchboard_pool=identity_pool,
            dispatch_fn=mock_dispatch,
            enable_identity_resolution=True,
        )
        result = await pipeline.process(
            message_text=envelope["payload"]["normalized_text"],
            tool_args={
                "source_channel": "whatsapp_user_client",
                "source_id": known_identity,
                "request_context": {
                    "payload_type": "conversation_history",
                    "source_thread_identity": envelope["event"]["external_thread_id"],
                },
            },
            message_inbox_id=ingest_response.request_id,
        )

    assert result.target_butler == "multi"
    assert set(result.acked_targets) == {"finance", "health"}
    assert len(routed_messages) == 2
    routed_by_target = dict(routed_messages)
    assert routed_by_target["finance"]["signal_type"] == "finance"
    assert routed_by_target["health"]["signal_type"] == "health"
    known_excerpt = routed_by_target["finance"]["excerpts"][0]
    unknown_excerpt = routed_by_target["health"]["excerpts"][0]
    assert known_excerpt["sender"] == "Known speaker"
    assert known_excerpt["sender_identity"] == known_identity
    assert known_excerpt["sender_entity_id"] == str(known_entity_id)
    assert unknown_excerpt["sender"] == "Unknown WhatsApp sender 2"
    assert unknown_excerpt["sender_identity"] == unknown_identity
    unknown_entity_id = uuid.UUID(unknown_excerpt["sender_entity_id"])
    assert unknown_entity_id != known_entity_id
    assert len(dispatch_prompts) == 1
    prompt = dispatch_prompts[0]
    assert "Known speaker" in prompt
    assert "Unknown WhatsApp sender 2" in prompt
    assert known_identity not in prompt
    assert unknown_identity not in prompt
    assert str(known_entity_id) not in prompt
    assert str(unknown_entity_id) not in prompt

    persisted_unknown = await identity_pool.fetchrow(
        "SELECT canonical_name, metadata FROM public.entities WHERE id = $1",
        unknown_entity_id,
    )
    assert persisted_unknown is not None
    assert persisted_unknown["metadata"]["unidentified"] is True
    stored_facts = await identity_pool.fetch(
        """
        SELECT entity_id, subject, predicate, content
        FROM memory.facts
        ORDER BY predicate
        """
    )
    assert [
        (row["entity_id"], row["subject"], row["predicate"], row["content"])
        for row in stored_facts
    ] == [
        (known_entity_id, "Known speaker", "paid_for_lunch", "I paid for lunch"),
        (
            unknown_entity_id,
            "Unknown WhatsApp sender 2",
            "reported_shoulder_pain",
            "My shoulder hurts",
        ),
    ]
    transport_named_count = await identity_pool.fetchval(
        """
        SELECT count(*)
        FROM public.entities
        WHERE canonical_name ~ '^[0-9]+(?::[0-9]+)?@(s\\.whatsapp\\.net|lid)$'
        """
    )
    assert transport_named_count == 0


@pytest.mark.integration
async def test_decomposition_empty_signals_stores_decomposed_empty(pool):
    """When LLM returns empty signals, lifecycle_state is decomposed_empty.

    Verifies that the pipeline correctly short-circuits and stores an
    appropriate decomposition_output when no signals are extracted.
    """
    from butlers.modules.pipeline import MessagePipeline
    from butlers.tools.switchboard.ingestion.ingest import ingest_v1

    # Ingest a fresh batch envelope
    envelope = _build_conversation_history_envelope(event_id=f"evt-empty-{uuid.uuid4()}")
    ingest_response = await ingest_v1(pool, envelope, enable_thread_affinity=False)
    assert ingest_response.status == "accepted"
    message_inbox_id = ingest_response.request_id

    # LLM returns empty signals
    async def mock_dispatch_empty(**kwargs):
        return FakeSpawnerResult(
            output="[]",
            model="claude-test-haiku",
            input_tokens=100,
            output_tokens=5,
        )

    with (
        patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new_callable=AsyncMock,
            return_value=_MOCK_BUTLERS,
        ),
        patch("butlers.tools.switchboard.routing.route.route", new_callable=AsyncMock),
    ):
        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=mock_dispatch_empty,
        )

        result = await pipeline.process(
            message_text="just chatting",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {
                    "payload_type": "conversation_history",
                },
            },
            message_inbox_id=message_inbox_id,
        )

    assert result.target_butler == "decomposed_empty"
    assert result.routed_targets == []

    # DB should reflect empty decomposition
    row = await pool.fetchrow(
        "SELECT decomposition_output, lifecycle_state FROM message_inbox WHERE id = $1",
        message_inbox_id,
    )
    assert row is not None
    assert row["lifecycle_state"] == "decomposed_empty"

    decomp = row["decomposition_output"]
    if isinstance(decomp, str):
        decomp = json.loads(decomp)
    assert decomp is not None
    assert decomp.get("signals") == []
    assert decomp.get("reason") == "no_signals_extracted"


@pytest.mark.integration
async def test_decomposition_no_conversation_history_in_db(pool):
    """When message_inbox row has no conversation_history, returns decomposed_empty.

    This covers the case where the raw_payload does not contain a
    conversation_history array (e.g. corrupted or truncated batch).
    """
    from butlers.modules.pipeline import MessagePipeline

    # Manually insert a message_inbox row with no conversation_history in raw_payload
    received_at = datetime.now(UTC)
    row_id = uuid.uuid4()

    await pool.execute(
        "SELECT switchboard_message_inbox_ensure_partition($1)",
        received_at,
    )
    await pool.execute(
        """
        INSERT INTO message_inbox (
            id, received_at, request_context, raw_payload,
            normalized_text, lifecycle_state, schema_version,
            processing_metadata, created_at, updated_at
        ) VALUES (
            $1, $2,
            $3::jsonb,
            $4::jsonb,
            'no history here', 'accepted', 'message_inbox.v2',
            '{}'::jsonb, $2, $2
        )
        """,
        row_id,
        received_at,
        json.dumps(
            {
                "payload_type": "conversation_history",
                "source_channel": "telegram_user_client",
            }
        ),
        json.dumps(
            {
                "payload": {
                    "raw": {},  # no conversation_history key
                    "normalized_text": "no history here",
                }
            }
        ),
    )

    async def mock_dispatch(**kwargs):
        raise AssertionError("dispatch_fn should not be called when no history found")

    pipeline = MessagePipeline(
        switchboard_pool=pool,
        dispatch_fn=mock_dispatch,
    )

    result = await pipeline.process(
        message_text="no history",
        tool_args={
            "source_channel": "telegram_user_client",
            "request_context": {"payload_type": "conversation_history"},
        },
        message_inbox_id=row_id,
    )

    assert result.target_butler == "decomposed_empty"
    assert result.route_result.get("reason") == "no_conversation_history"
