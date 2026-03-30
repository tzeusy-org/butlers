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

import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for the test module."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17") as pg:
        yield pg


@pytest.fixture
async def pool(postgres_container):
    """Provision a fresh Switchboard database with real Alembic migrations.

    Installs required PostgreSQL extensions (pgvector, pg_trgm, pgcrypto)
    before running migrations, mirroring the production provisioning playbook.
    The pgvector/pgvector:pg17 image ships the extension as a loadable module;
    it still needs to be activated via CREATE EXTENSION before migrations run.
    """
    import asyncpg as _asyncpg

    from butlers.db import Database
    from butlers.migrations import run_migrations

    db = Database(
        db_name=_unique_db_name(),
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    await db.provision()

    # Install required extensions before migrations (must run as superuser /
    # schema owner on the target database — testcontainer user has superuser).
    bootstrap_conn = await _asyncpg.connect(
        host=db.host,
        port=db.port,
        user=db.user,
        password=db.password,
        database=db.db_name,
    )
    try:
        await bootstrap_conn.execute('CREATE EXTENSION IF NOT EXISTS "vector"')
        await bootstrap_conn.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
        await bootstrap_conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        await bootstrap_conn.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')
    finally:
        await bootstrap_conn.close()

    p = await db.connect()

    db_url = f"postgresql://{db.user}:{db.password}@{db.host}:{db.port}/{db.db_name}"
    await run_migrations(db_url, chain="core")
    await run_migrations(db_url, chain="switchboard")

    yield p

    await p.close()
    await db.close()


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
            "identity": "alice@example.com",
        },
        "payload": {
            "raw": {
                "conversation_history": [
                    {
                        "sender": "Alice",
                        "text": "I spent $80 on groceries today",
                        "timestamp": "2026-03-30T09:00:00Z",
                        "message_id": "msg-int-1",
                    },
                    {
                        "sender": "Bob",
                        "text": "My knee is hurting again",
                        "timestamp": "2026-03-30T09:01:00Z",
                        "message_id": "msg-int-2",
                    },
                    {
                        "sender": "Alice",
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
                    "sender": "Alice",
                    "text": "I spent $80 on groceries today",
                    "timestamp": "2026-03-30T09:00:00Z",
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


@dataclass
class FakeSpawnerResult:
    """Mimics SpawnerResult from butlers.core.spawner."""

    output: str | None = None
    success: bool = True
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None
    model: str | None = None
    usage: dict | None = None


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
            usage={"input_tokens": 250, "output_tokens": 80},
        )

    route_call_log: list[dict] = []

    async def mock_route(pool_arg, *, target_butler, tool_name, args, source_butler):
        route_call_log.append(
            {
                "target_butler": target_butler,
                "tool_name": tool_name,
                "fanout_mode": args.get("__switchboard_route_context", {}).get("fanout_mode"),
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
            usage={"input_tokens": 100, "output_tokens": 5},
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
