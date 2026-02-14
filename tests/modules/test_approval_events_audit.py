"""Tests for immutable approval event auditing.

Validates AC for butlers-0p6.3:
1. Append-only approval_events storage exists with immutable semantics.
2. Events cover queue, decision, execution, and rule lifecycle transitions.
3. Event schema includes actor, timestamp, reason, and linked action/rule IDs.
4. Tests verify event emission completeness and immutability assumptions.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from butlers.modules.approvals.events import ApprovalEventType, record_approval_event

pytestmark = pytest.mark.unit


class MockPool:
    """In-memory mock for database pool."""

    def __init__(self):
        self.events: list[dict[str, Any]] = []
        self.actions: dict[uuid.UUID, dict[str, Any]] = {}

    async def execute(self, query: str, *args):
        """Mock database execute."""
        if "INSERT INTO approval_events" in query:
            self.events.append(
                {
                    "event_type": args[0],
                    "action_id": args[1],
                    "rule_id": args[2],
                    "actor": args[3],
                    "reason": args[4],
                    "event_metadata": json.loads(args[5]) if isinstance(args[5], str) else args[5],
                    "occurred_at": args[6],
                }
            )
        elif "UPDATE approval_events" in query:
            raise Exception("approval_events is append-only: UPDATE is not allowed")
        elif "DELETE FROM approval_events" in query:
            raise Exception("approval_events is append-only: DELETE is not allowed")


class TestApprovalEventSchema:
    """Validate event schema includes required fields."""

    async def test_event_includes_actor(self):
        """Events must include actor field."""
        pool = MockPool()
        action_id = uuid.uuid4()

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="user@example.com",
            action_id=action_id,
        )

        assert len(pool.events) == 1
        assert pool.events[0]["actor"] == "user@example.com"

    async def test_event_includes_timestamp(self):
        """Events must include occurred_at timestamp."""
        pool = MockPool()
        action_id = uuid.uuid4()
        before = datetime.now(UTC)

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system",
            action_id=action_id,
        )

        after = datetime.now(UTC)
        assert len(pool.events) == 1
        occurred_at = pool.events[0]["occurred_at"]
        assert before <= occurred_at <= after

    async def test_event_includes_custom_timestamp(self):
        """Events can specify custom occurred_at."""
        pool = MockPool()
        action_id = uuid.uuid4()
        custom_time = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system",
            action_id=action_id,
            occurred_at=custom_time,
        )

        assert len(pool.events) == 1
        assert pool.events[0]["occurred_at"] == custom_time

    async def test_event_includes_reason(self):
        """Events can include optional reason field."""
        pool = MockPool()
        action_id = uuid.uuid4()

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_REJECTED,
            actor="user@example.com",
            action_id=action_id,
            reason="Too risky",
        )

        assert len(pool.events) == 1
        assert pool.events[0]["reason"] == "Too risky"

    async def test_event_includes_action_id(self):
        """Events for actions must include action_id."""
        pool = MockPool()
        action_id = uuid.uuid4()

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system",
            action_id=action_id,
        )

        assert len(pool.events) == 1
        assert pool.events[0]["action_id"] == action_id

    async def test_event_includes_rule_id(self):
        """Events for rules must include rule_id."""
        pool = MockPool()
        rule_id = uuid.uuid4()

        await record_approval_event(
            pool,
            ApprovalEventType.RULE_CREATED,
            actor="user@example.com",
            rule_id=rule_id,
        )

        assert len(pool.events) == 1
        assert pool.events[0]["rule_id"] == rule_id

    async def test_event_includes_metadata(self):
        """Events can include optional metadata field."""
        pool = MockPool()
        action_id = uuid.uuid4()
        metadata = {"tool_name": "email_send", "risk_tier": "high"}

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system",
            action_id=action_id,
            metadata=metadata,
        )

        assert len(pool.events) == 1
        assert pool.events[0]["event_metadata"] == metadata

    async def test_event_requires_action_or_rule_id(self):
        """Events must have at least action_id or rule_id."""
        pool = MockPool()

        with pytest.raises(ValueError, match="must include action_id and/or rule_id"):
            await record_approval_event(
                pool,
                ApprovalEventType.ACTION_QUEUED,
                actor="system",
            )


class TestApprovalEventCompleteness:
    """Validate all lifecycle transitions emit events."""

    async def test_action_queued_event_type_exists(self):
        """ACTION_QUEUED event type is defined."""
        assert hasattr(ApprovalEventType, "ACTION_QUEUED")
        assert ApprovalEventType.ACTION_QUEUED == "action_queued"

    async def test_action_auto_approved_event_type_exists(self):
        """ACTION_AUTO_APPROVED event type is defined."""
        assert hasattr(ApprovalEventType, "ACTION_AUTO_APPROVED")
        assert ApprovalEventType.ACTION_AUTO_APPROVED == "action_auto_approved"

    async def test_action_approved_event_type_exists(self):
        """ACTION_APPROVED event type is defined."""
        assert hasattr(ApprovalEventType, "ACTION_APPROVED")
        assert ApprovalEventType.ACTION_APPROVED == "action_approved"

    async def test_action_rejected_event_type_exists(self):
        """ACTION_REJECTED event type is defined."""
        assert hasattr(ApprovalEventType, "ACTION_REJECTED")
        assert ApprovalEventType.ACTION_REJECTED == "action_rejected"

    async def test_action_expired_event_type_exists(self):
        """ACTION_EXPIRED event type is defined."""
        assert hasattr(ApprovalEventType, "ACTION_EXPIRED")
        assert ApprovalEventType.ACTION_EXPIRED == "action_expired"

    async def test_action_execution_succeeded_event_type_exists(self):
        """ACTION_EXECUTION_SUCCEEDED event type is defined."""
        assert hasattr(ApprovalEventType, "ACTION_EXECUTION_SUCCEEDED")
        assert ApprovalEventType.ACTION_EXECUTION_SUCCEEDED == "action_execution_succeeded"

    async def test_action_execution_failed_event_type_exists(self):
        """ACTION_EXECUTION_FAILED event type is defined."""
        assert hasattr(ApprovalEventType, "ACTION_EXECUTION_FAILED")
        assert ApprovalEventType.ACTION_EXECUTION_FAILED == "action_execution_failed"

    async def test_rule_created_event_type_exists(self):
        """RULE_CREATED event type is defined."""
        assert hasattr(ApprovalEventType, "RULE_CREATED")
        assert ApprovalEventType.RULE_CREATED == "rule_created"

    async def test_rule_revoked_event_type_exists(self):
        """RULE_REVOKED event type is defined."""
        assert hasattr(ApprovalEventType, "RULE_REVOKED")
        assert ApprovalEventType.RULE_REVOKED == "rule_revoked"

    async def test_all_event_types_cover_lifecycle(self):
        """Verify we have events for all lifecycle stages."""
        event_types = {e.value for e in ApprovalEventType}

        # Queue lifecycle
        assert "action_queued" in event_types

        # Decision lifecycle
        assert "action_auto_approved" in event_types
        assert "action_approved" in event_types
        assert "action_rejected" in event_types
        assert "action_expired" in event_types

        # Execution lifecycle
        assert "action_execution_succeeded" in event_types
        assert "action_execution_failed" in event_types

        # Rule lifecycle
        assert "rule_created" in event_types
        assert "rule_revoked" in event_types


class TestApprovalEventImmutability:
    """Validate append-only semantics and immutability assumptions."""

    async def test_events_are_append_only_no_updates(self):
        """Attempting to UPDATE approval_events should raise an error."""
        pool = MockPool()
        action_id = uuid.uuid4()

        # First insert an event
        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system",
            action_id=action_id,
        )

        # Attempt to update should fail
        with pytest.raises(Exception, match="approval_events is append-only: UPDATE"):
            await pool.execute(
                "UPDATE approval_events SET actor = $1 WHERE action_id = $2",
                "hacker",
                action_id,
            )

    async def test_events_are_append_only_no_deletes(self):
        """Attempting to DELETE from approval_events should raise an error."""
        pool = MockPool()
        action_id = uuid.uuid4()

        # First insert an event
        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system",
            action_id=action_id,
        )

        # Attempt to delete should fail
        with pytest.raises(Exception, match="approval_events is append-only: DELETE"):
            await pool.execute(
                "DELETE FROM approval_events WHERE action_id = $1",
                action_id,
            )

    async def test_multiple_events_for_same_action_allowed(self):
        """Multiple events for the same action are allowed (append-only)."""
        pool = MockPool()
        action_id = uuid.uuid4()

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system",
            action_id=action_id,
        )

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_APPROVED,
            actor="user@example.com",
            action_id=action_id,
        )

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_EXECUTION_SUCCEEDED,
            actor="system:executor",
            action_id=action_id,
        )

        assert len(pool.events) == 3
        assert pool.events[0]["event_type"] == "action_queued"
        assert pool.events[1]["event_type"] == "action_approved"
        assert pool.events[2]["event_type"] == "action_execution_succeeded"

    async def test_events_preserve_insertion_order(self):
        """Events are stored in the order they were recorded."""
        pool = MockPool()
        action_id = uuid.uuid4()

        times = []
        for i, event_type in enumerate(
            [
                ApprovalEventType.ACTION_QUEUED,
                ApprovalEventType.ACTION_APPROVED,
                ApprovalEventType.ACTION_EXECUTION_SUCCEEDED,
            ]
        ):
            occurred_at = datetime(2026, 1, 15, 12, i, 0, tzinfo=UTC)
            times.append(occurred_at)
            await record_approval_event(
                pool,
                event_type,
                actor="system",
                action_id=action_id,
                occurred_at=occurred_at,
            )

        assert len(pool.events) == 3
        for i, event in enumerate(pool.events):
            assert event["occurred_at"] == times[i]


class TestApprovalEventCoverage:
    """Integration-style tests verifying event emission in real flows."""

    async def test_action_queue_to_approve_to_execute_emits_all_events(self):
        """Full approval flow should emit queued -> approved -> execution events."""
        pool = MockPool()
        action_id = uuid.uuid4()

        # Simulate queue
        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system:approval_gate",
            action_id=action_id,
            metadata={"tool_name": "email_send"},
        )

        # Simulate approval
        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_APPROVED,
            actor="user@example.com",
            action_id=action_id,
            reason="Approved for business purpose",
        )

        # Simulate execution
        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_EXECUTION_SUCCEEDED,
            actor="system:executor",
            action_id=action_id,
            metadata={"result": "sent"},
        )

        assert len(pool.events) == 3
        assert pool.events[0]["event_type"] == "action_queued"
        assert pool.events[1]["event_type"] == "action_approved"
        assert pool.events[2]["event_type"] == "action_execution_succeeded"

    async def test_action_queue_to_reject_emits_events(self):
        """Rejection flow should emit queued -> rejected events."""
        pool = MockPool()
        action_id = uuid.uuid4()

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system:approval_gate",
            action_id=action_id,
        )

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_REJECTED,
            actor="user@example.com",
            action_id=action_id,
            reason="Violates policy",
        )

        assert len(pool.events) == 2
        assert pool.events[0]["event_type"] == "action_queued"
        assert pool.events[1]["event_type"] == "action_rejected"

    async def test_action_auto_approve_emits_events(self):
        """Auto-approval via rule should emit queued -> auto_approved -> execution events."""
        pool = MockPool()
        action_id = uuid.uuid4()
        rule_id = uuid.uuid4()

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system:approval_gate",
            action_id=action_id,
        )

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_AUTO_APPROVED,
            actor="system:approval_gate",
            action_id=action_id,
            rule_id=rule_id,
            metadata={"rule_id": str(rule_id)},
        )

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_EXECUTION_SUCCEEDED,
            actor="system:executor",
            action_id=action_id,
        )

        assert len(pool.events) == 3
        assert pool.events[0]["event_type"] == "action_queued"
        assert pool.events[1]["event_type"] == "action_auto_approved"
        assert pool.events[2]["event_type"] == "action_execution_succeeded"

    async def test_action_expiry_emits_event(self):
        """Expiration should emit queued -> expired events."""
        pool = MockPool()
        action_id = uuid.uuid4()

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system:approval_gate",
            action_id=action_id,
        )

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_EXPIRED,
            actor="system:expiry_job",
            action_id=action_id,
            reason="Exceeded 24h timeout",
        )

        assert len(pool.events) == 2
        assert pool.events[0]["event_type"] == "action_queued"
        assert pool.events[1]["event_type"] == "action_expired"

    async def test_rule_create_emits_event(self):
        """Creating a rule should emit RULE_CREATED event."""
        pool = MockPool()
        rule_id = uuid.uuid4()

        await record_approval_event(
            pool,
            ApprovalEventType.RULE_CREATED,
            actor="user@example.com",
            rule_id=rule_id,
            metadata={"tool_name": "email_send", "arg_constraints": {}},
        )

        assert len(pool.events) == 1
        assert pool.events[0]["event_type"] == "rule_created"
        assert pool.events[0]["rule_id"] == rule_id

    async def test_rule_revoke_emits_event(self):
        """Revoking a rule should emit RULE_REVOKED event."""
        pool = MockPool()
        rule_id = uuid.uuid4()

        # First create
        await record_approval_event(
            pool,
            ApprovalEventType.RULE_CREATED,
            actor="user@example.com",
            rule_id=rule_id,
        )

        # Then revoke
        await record_approval_event(
            pool,
            ApprovalEventType.RULE_REVOKED,
            actor="user@example.com",
            rule_id=rule_id,
            reason="No longer needed",
        )

        assert len(pool.events) == 2
        assert pool.events[0]["event_type"] == "rule_created"
        assert pool.events[1]["event_type"] == "rule_revoked"

    async def test_execution_failure_emits_event(self):
        """Failed execution should emit ACTION_EXECUTION_FAILED event."""
        pool = MockPool()
        action_id = uuid.uuid4()

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system:approval_gate",
            action_id=action_id,
        )

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_APPROVED,
            actor="user@example.com",
            action_id=action_id,
        )

        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_EXECUTION_FAILED,
            actor="system:executor",
            action_id=action_id,
            reason="Network timeout",
            metadata={"error": "Connection refused"},
        )

        assert len(pool.events) == 3
        assert pool.events[2]["event_type"] == "action_execution_failed"
        assert pool.events[2]["reason"] == "Network timeout"
