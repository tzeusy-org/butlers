"""End-to-end conformance tests for Switchboard lifecycle flows.

Tests cover:
- Ingress → decomposition → fanout → completion
- Partial failure handling
- Timeout scenarios
- Dead-letter capture and replay
- Operator controls
"""

from __future__ import annotations

import json
import shutil
import uuid

import pytest

# Skip tests if Docker not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def switchboard_pool(provisioned_postgres_pool):
    """Provision a fresh database with switchboard tables."""
    async with provisioned_postgres_pool() as pool:
        # Create minimal required tables for conformance tests
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS message_inbox (
                id UUID NOT NULL DEFAULT gen_random_uuid(),
                received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                request_context JSONB NOT NULL DEFAULT '{}'::jsonb,
                raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                normalized_text TEXT NOT NULL,
                decomposition_output JSONB,
                dispatch_outcomes JSONB,
                response_summary TEXT,
                lifecycle_state TEXT NOT NULL DEFAULT 'accepted',
                schema_version TEXT NOT NULL DEFAULT 'message_inbox.v2',
                processing_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                final_state_at TIMESTAMPTZ,
                trace_id TEXT,
                session_id UUID,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (received_at, id)
            ) PARTITION BY RANGE (received_at)
            """
        )

        # Create default partition
        await pool.execute(
            """
            CREATE TABLE message_inbox_p202602 PARTITION OF message_inbox
            FOR VALUES FROM ('2026-02-01') TO ('2026-03-01')
            """
        )

        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS fanout_execution_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_channel TEXT NOT NULL,
                source_id TEXT,
                tool_name TEXT NOT NULL,
                fanout_mode TEXT NOT NULL,
                join_policy TEXT NOT NULL,
                abort_policy TEXT NOT NULL,
                plan_payload JSONB NOT NULL,
                execution_payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS dead_letter_queue (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                original_request_id UUID NOT NULL,
                source_table TEXT NOT NULL,
                failure_reason TEXT NOT NULL,
                failure_category TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_retry_at TIMESTAMPTZ,
                original_payload JSONB NOT NULL,
                request_context JSONB NOT NULL,
                error_details JSONB NOT NULL DEFAULT '{}'::jsonb,
                replay_eligible BOOLEAN NOT NULL DEFAULT true,
                replayed_at TIMESTAMPTZ,
                replayed_request_id UUID,
                replay_outcome TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT valid_failure_category CHECK (
                    failure_category IN (
                        'timeout',
                        'retry_exhausted',
                        'circuit_open',
                        'policy_violation',
                        'validation_error',
                        'downstream_failure',
                        'unknown'
                    )
                ),
                CONSTRAINT valid_replay_outcome CHECK (
                    replay_outcome IS NULL OR replay_outcome IN (
                        'success',
                        'failed',
                        'rejected'
                    )
                )
            )
            """
        )

        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_audit_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_type TEXT NOT NULL,
                target_request_id UUID NOT NULL,
                target_table TEXT NOT NULL,
                operator_identity TEXT NOT NULL,
                reason TEXT NOT NULL,
                action_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                outcome TEXT NOT NULL,
                outcome_details JSONB NOT NULL DEFAULT '{}'::jsonb,
                performed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT valid_action_type CHECK (
                    action_type IN (
                        'manual_reroute',
                        'cancel_request',
                        'abort_request',
                        'controlled_replay',
                        'controlled_retry',
                        'force_complete'
                    )
                ),
                CONSTRAINT valid_outcome CHECK (
                    outcome IN ('success', 'failed', 'rejected', 'partial')
                )
            )
            """
        )

        yield pool


class TestIngressToCompletion:
    """Test full ingress → decomposition → fanout → completion flow."""

    async def test_single_target_success_flow(self, switchboard_pool):
        """Test successful request flow to a single target butler."""
        async with switchboard_pool.acquire() as conn:
            # Ingest a request
            request_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO message_inbox (
                    id,
                    request_context,
                    raw_payload,
                    normalized_text,
                    lifecycle_state
                )
                VALUES ($1, $2::jsonb, $3::jsonb, $4, $5)
                """,
                request_id,
                json.dumps(
                    {
                        "source_channel": "telegram",
                        "source_sender_identity": "test_user",
                    }
                ),
                json.dumps({"content": "Test message"}),
                "Test message",
                "accepted",
            )

            # Verify ingress
            request = await conn.fetchrow("SELECT * FROM message_inbox WHERE id = $1", request_id)
            assert request["lifecycle_state"] == "accepted"
            assert request["normalized_text"] == "Test message"

            # Simulate decomposition
            await conn.execute(
                """
                UPDATE message_inbox
                SET
                    decomposition_output = $1::jsonb,
                    lifecycle_state = $2,
                    updated_at = now()
                WHERE id = $3
                """,
                json.dumps([{"butler": "general", "prompt": "Test message"}]),
                "decomposed",
                request_id,
            )

            # Simulate dispatch
            await conn.execute(
                """
                UPDATE message_inbox
                SET
                    dispatch_outcomes = $1::jsonb,
                    lifecycle_state = $2,
                    updated_at = now()
                WHERE id = $3
                """,
                json.dumps({"target": "general", "status": "dispatched"}),
                "dispatched",
                request_id,
            )

            # Simulate completion
            await conn.execute(
                """
                UPDATE message_inbox
                SET
                    lifecycle_state = $1,
                    final_state_at = now(),
                    response_summary = $2,
                    updated_at = now()
                WHERE id = $3
                """,
                "completed",
                "Success",
                request_id,
            )

            # Verify final state
            final = await conn.fetchrow("SELECT * FROM message_inbox WHERE id = $1", request_id)
            assert final["lifecycle_state"] == "completed"
            assert final["response_summary"] == "Success"
            assert final["final_state_at"] is not None

    async def test_multi_target_fanout_flow(self, switchboard_pool):
        """Test fanout to multiple target butlers."""
        async with switchboard_pool.acquire() as conn:
            request_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO message_inbox (
                    id,
                    request_context,
                    raw_payload,
                    normalized_text,
                    lifecycle_state
                )
                VALUES ($1, $2::jsonb, $3::jsonb, $4, $5)
                """,
                request_id,
                json.dumps(
                    {
                        "source_channel": "telegram",
                        "source_sender_identity": "test_user",
                    }
                ),
                json.dumps({"content": "Multi-domain message"}),
                "Multi-domain message",
                "accepted",
            )

            # Simulate multi-target decomposition
            await conn.execute(
                """
                UPDATE message_inbox
                SET
                    decomposition_output = $1::jsonb,
                    lifecycle_state = $2,
                    updated_at = now()
                WHERE id = $3
                """,
                json.dumps(
                    [
                        {"butler": "health", "prompt": "Track medication"},
                        {"butler": "relationship", "prompt": "Send card"},
                    ]
                ),
                "decomposed",
                request_id,
            )

            # Log fanout execution
            fanout_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO fanout_execution_log (
                    id,
                    source_channel,
                    source_id,
                    tool_name,
                    fanout_mode,
                    join_policy,
                    abort_policy,
                    plan_payload,
                    execution_payload
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb)
                """,
                fanout_id,
                "telegram",
                str(request_id),
                "decompose_and_route",
                "parallel",
                "all",
                "any_failure",
                json.dumps({"targets": ["health", "relationship"]}),
                json.dumps(
                    {
                        "health": {"status": "success"},
                        "relationship": {"status": "success"},
                    }
                ),
            )

            # Verify fanout was logged
            fanout_log = await conn.fetchrow(
                "SELECT * FROM fanout_execution_log WHERE id = $1", fanout_id
            )
            assert fanout_log["fanout_mode"] == "parallel"
            assert json.loads(fanout_log["execution_payload"])["health"]["status"] == "success"


class TestPartialFailureHandling:
    """Test partial failure scenarios in fanout."""

    async def test_partial_fanout_failure(self, switchboard_pool):
        """Test fanout with one target failing."""
        async with switchboard_pool.acquire() as conn:
            request_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO message_inbox (
                    id,
                    request_context,
                    raw_payload,
                    normalized_text,
                    lifecycle_state
                )
                VALUES ($1, $2::jsonb, $3::jsonb, $4, $5)
                """,
                request_id,
                json.dumps(
                    {
                        "source_channel": "telegram",
                        "source_sender_identity": "test_user",
                    }
                ),
                json.dumps({"content": "Partial failure test"}),
                "Partial failure test",
                "accepted",
            )

            # Simulate partial failure
            fanout_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO fanout_execution_log (
                    id,
                    source_channel,
                    source_id,
                    tool_name,
                    fanout_mode,
                    join_policy,
                    abort_policy,
                    plan_payload,
                    execution_payload
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb)
                """,
                fanout_id,
                "telegram",
                str(request_id),
                "decompose_and_route",
                "parallel",
                "best_effort",
                "continue",
                json.dumps({"targets": ["health", "relationship"]}),
                json.dumps(
                    {
                        "health": {"status": "success"},
                        "relationship": {"status": "failed", "error": "Target unavailable"},
                    }
                ),
            )

            # Verify partial success recorded
            fanout_log = await conn.fetchrow(
                "SELECT * FROM fanout_execution_log WHERE id = $1", fanout_id
            )
            assert json.loads(fanout_log["execution_payload"])["health"]["status"] == "success"
            assert json.loads(fanout_log["execution_payload"])["relationship"]["status"] == "failed"


class TestTimeoutScenarios:
    """Test timeout handling."""

    async def test_request_timeout(self, switchboard_pool):
        """Test request that exceeds timeout budget."""
        async with switchboard_pool.acquire() as conn:
            request_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO message_inbox (
                    id,
                    request_context,
                    raw_payload,
                    normalized_text,
                    lifecycle_state
                )
                VALUES ($1, $2::jsonb, $3::jsonb, $4, $5)
                """,
                request_id,
                json.dumps(
                    {
                        "source_channel": "telegram",
                        "source_sender_identity": "test_user",
                        "timeout_budget_ms": 5000,
                    }
                ),
                json.dumps({"content": "Timeout test"}),
                "Timeout test",
                "accepted",
            )

            # Simulate timeout
            await conn.execute(
                """
                UPDATE message_inbox
                SET
                    lifecycle_state = $1,
                    final_state_at = now(),
                    response_summary = $2,
                    updated_at = now()
                WHERE id = $3
                """,
                "failed",
                "Request timed out after 5000ms",
                request_id,
            )

            # Capture to dead-letter queue
            from roster.switchboard.tools.dead_letter.capture import (
                capture_to_dead_letter,
            )

            dl_id = await capture_to_dead_letter(
                conn,
                original_request_id=request_id,
                source_table="message_inbox",
                failure_reason="Request exceeded timeout budget",
                failure_category="timeout",
                retry_count=0,
                last_retry_at=None,
                original_payload={"content": "Timeout test"},
                request_context={
                    "source_channel": "telegram",
                    "timeout_budget_ms": 5000,
                },
                error_details={"timeout_ms": 5000},
            )

            # Verify dead-letter entry
            dl_entry = await conn.fetchrow("SELECT * FROM dead_letter_queue WHERE id = $1", dl_id)
            assert dl_entry["failure_category"] == "timeout"
            assert dl_entry["replay_eligible"] is True


class TestDeadLetterReplay:
    """Test dead-letter capture and replay."""

    async def test_capture_and_replay_flow(self, switchboard_pool):
        """Test capturing a failed request and replaying it."""
        async with switchboard_pool.acquire() as conn:
            from roster.switchboard.tools.dead_letter.capture import (
                capture_to_dead_letter,
            )
            from roster.switchboard.tools.dead_letter.replay import (
                replay_dead_letter_request,
            )

            # Create a failed request
            failed_request_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO message_inbox (
                    id,
                    request_context,
                    raw_payload,
                    normalized_text,
                    lifecycle_state
                )
                VALUES ($1, $2::jsonb, $3::jsonb, $4, $5)
                """,
                failed_request_id,
                json.dumps(
                    {
                        "source_channel": "telegram",
                        "source_sender_identity": "test_user",
                    }
                ),
                json.dumps({"content": "Failed message"}),
                "Failed message",
                "failed",
            )

            # Capture to dead-letter
            dl_id = await capture_to_dead_letter(
                conn,
                original_request_id=failed_request_id,
                source_table="message_inbox",
                failure_reason="Downstream service unavailable",
                failure_category="downstream_failure",
                retry_count=3,
                last_retry_at=None,  # Could be datetime.now(UTC) but not needed
                original_payload={"content": "Failed message"},
                request_context={
                    "source_channel": "telegram",
                    "source_sender_identity": "test_user",
                },
                error_details={"error": "Service unavailable"},
            )

            # Verify capture
            dl_entry = await conn.fetchrow("SELECT * FROM dead_letter_queue WHERE id = $1", dl_id)
            assert dl_entry["original_request_id"] == failed_request_id
            assert dl_entry["replay_eligible"] is True

            # Replay the request
            result = await replay_dead_letter_request(
                conn,
                dead_letter_id=dl_id,
                operator_identity="test_operator",
                reason="Infrastructure recovered",
            )

            print(f"DEBUG: replay result={result}")
            assert result["success"] is True
            assert "replayed_request_id" in result

            # Verify replay metadata
            replayed_id = uuid.UUID(result["replayed_request_id"])
            replayed_request = await conn.fetchrow(
                "SELECT * FROM message_inbox WHERE id = $1", replayed_id
            )
            assert replayed_request["lifecycle_state"] == "accepted"
            assert (
                json.loads(replayed_request["request_context"])["replay_metadata"]["is_replay"]
                is True
            )
            assert json.loads(replayed_request["request_context"])["replay_metadata"][
                "original_request_id"
            ] == str(failed_request_id)

            # Verify dead-letter updated
            dl_updated = await conn.fetchrow("SELECT * FROM dead_letter_queue WHERE id = $1", dl_id)
            assert dl_updated["replayed_at"] is not None
            assert dl_updated["replayed_request_id"] == replayed_id

            # Verify audit log
            audit_log = await conn.fetchrow(
                """
                SELECT * FROM operator_audit_log
                WHERE action_type = 'controlled_replay'
                AND target_request_id = $1
                """,
                failed_request_id,
            )
            assert audit_log is not None
            assert audit_log["operator_identity"] == "test_operator"
            assert audit_log["outcome"] == "success"

    async def test_replay_idempotency(self, switchboard_pool):
        """Test that replaying an already-replayed request fails."""
        async with switchboard_pool.acquire() as conn:
            from roster.switchboard.tools.dead_letter.capture import (
                capture_to_dead_letter,
            )
            from roster.switchboard.tools.dead_letter.replay import (
                replay_dead_letter_request,
            )

            failed_request_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO message_inbox (
                    id,
                    request_context,
                    raw_payload,
                    normalized_text,
                    lifecycle_state
                )
                VALUES ($1, $2::jsonb, $3::jsonb, $4, $5)
                """,
                failed_request_id,
                json.dumps({"source_channel": "test"}),
                json.dumps({"content": "Test"}),
                "Test",
                "failed",
            )

            dl_id = await capture_to_dead_letter(
                conn,
                original_request_id=failed_request_id,
                source_table="message_inbox",
                failure_reason="Test",
                failure_category="unknown",
                retry_count=0,
                last_retry_at=None,
                original_payload={"content": "Test"},
                request_context={"source_channel": "test"},
                error_details={},
            )

            # First replay should succeed
            result1 = await replay_dead_letter_request(
                conn,
                dead_letter_id=dl_id,
                operator_identity="test_op",
                reason="Test replay",
            )
            assert result1["success"] is True

            # Second replay should fail
            result2 = await replay_dead_letter_request(
                conn,
                dead_letter_id=dl_id,
                operator_identity="test_op",
                reason="Second replay attempt",
            )
            assert result2["success"] is False
            assert result2["error"] == "already_replayed"


class TestOperatorControls:
    """Test operator intervention tools."""

    async def test_manual_reroute(self, switchboard_pool):
        """Test manual reroute of a request."""
        async with switchboard_pool.acquire() as conn:
            from roster.switchboard.tools.operator.controls import manual_reroute_request

            request_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO message_inbox (
                    id,
                    request_context,
                    raw_payload,
                    normalized_text,
                    lifecycle_state
                )
                VALUES ($1, $2::jsonb, $3::jsonb, $4, $5)
                """,
                request_id,
                json.dumps({"source_channel": "test"}),
                json.dumps({"content": "Reroute test"}),
                "Reroute test",
                "dispatched",
            )

            # Perform reroute
            result = await manual_reroute_request(
                conn,
                request_id=request_id,
                new_target_butler="health",
                operator_identity="test_operator",
                reason="Misclassified by decomposer",
            )

            print(f"DEBUG: replay result={result}")
            assert result["success"] is True
            assert result["new_target"] == "health"

            # Verify request updated
            request = await conn.fetchrow("SELECT * FROM message_inbox WHERE id = $1", request_id)
            assert request["lifecycle_state"] == "rerouted"
            assert (
                json.loads(request["request_context"])["manual_reroute"]["new_target"] == "health"
            )

            # Verify audit log
            audit = await conn.fetchrow(
                """
                SELECT * FROM operator_audit_log
                WHERE action_type = 'manual_reroute'
                AND target_request_id = $1
                """,
                request_id,
            )
            assert audit["outcome"] == "success"
            assert audit["operator_identity"] == "test_operator"

    async def test_cancel_request(self, switchboard_pool):
        """Test cancelling an in-flight request."""
        async with switchboard_pool.acquire() as conn:
            from roster.switchboard.tools.operator.controls import cancel_request

            request_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO message_inbox (
                    id,
                    request_context,
                    raw_payload,
                    normalized_text,
                    lifecycle_state
                )
                VALUES ($1, $2::jsonb, $3::jsonb, $4, $5)
                """,
                request_id,
                json.dumps({"source_channel": "test"}),
                json.dumps({"content": "Cancel test"}),
                "Cancel test",
                "dispatched",
            )

            result = await cancel_request(
                conn,
                request_id=request_id,
                operator_identity="test_operator",
                reason="User requested cancellation",
            )

            print(f"DEBUG: replay result={result}")
            assert result["success"] is True
            assert result["lifecycle_state"] == "cancelled"

            # Verify terminal state
            request = await conn.fetchrow("SELECT * FROM message_inbox WHERE id = $1", request_id)
            assert request["lifecycle_state"] == "cancelled"
            assert request["final_state_at"] is not None

    async def test_force_complete(self, switchboard_pool):
        """Test force-completing a request."""
        async with switchboard_pool.acquire() as conn:
            from roster.switchboard.tools.operator.controls import (
                force_complete_request,
            )

            request_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO message_inbox (
                    id,
                    request_context,
                    raw_payload,
                    normalized_text,
                    lifecycle_state
                )
                VALUES ($1, $2::jsonb, $3::jsonb, $4, $5)
                """,
                request_id,
                json.dumps({"source_channel": "test"}),
                json.dumps({"content": "Force complete test"}),
                "Force complete test",
                "dispatched",
            )

            result = await force_complete_request(
                conn,
                request_id=request_id,
                operator_identity="test_operator",
                reason="Manual resolution required",
                completion_summary="Resolved via external system",
            )

            print(f"DEBUG: replay result={result}")
            assert result["success"] is True
            assert result["lifecycle_state"] == "completed"

            # Verify completion
            request = await conn.fetchrow("SELECT * FROM message_inbox WHERE id = $1", request_id)
            assert request["lifecycle_state"] == "completed"
            assert "Force-completed by operator" in request["response_summary"]

    async def test_cannot_reroute_terminal_request(self, switchboard_pool):
        """Test that terminal requests cannot be rerouted."""
        async with switchboard_pool.acquire() as conn:
            from roster.switchboard.tools.operator.controls import manual_reroute_request

            request_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO message_inbox (
                    id,
                    request_context,
                    raw_payload,
                    normalized_text,
                    lifecycle_state
                )
                VALUES ($1, $2::jsonb, $3::jsonb, $4, $5)
                """,
                request_id,
                json.dumps({"source_channel": "test"}),
                json.dumps({"content": "Terminal test"}),
                "Terminal test",
                "completed",
            )

            result = await manual_reroute_request(
                conn,
                request_id=request_id,
                new_target_butler="health",
                operator_identity="test_op",
                reason="Should fail",
            )

            assert result["success"] is False
            assert result["error"] == "request_already_terminal"
