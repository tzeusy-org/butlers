"""Database integration tests for approval events immutability.

Tests the PostgreSQL trigger that prevents UPDATE/DELETE on approval_events.
"""

from __future__ import annotations

import json
import uuid

import pytest

from butlers.modules.approvals.events import ApprovalEventType, record_approval_event

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]


class TestApprovalEventsDatabaseImmutability:
    """Validate database-level immutability enforcement."""

    async def _create_pending_action(self, pool, action_id: uuid.UUID):
        """Helper to create a pending action for testing."""
        await pool.execute(
            """
            INSERT INTO pending_actions (id, tool_name, tool_args, status)
            VALUES ($1, $2, $3, $4)
            """,
            action_id,
            "test_tool",
            json.dumps({}),
            "pending",
        )

    async def _create_approval_rule(self, pool, rule_id: uuid.UUID):
        """Helper to create an approval rule for testing."""
        await pool.execute(
            """
            INSERT INTO approval_rules (id, tool_name, arg_constraints, description)
            VALUES ($1, $2, $3, $4)
            """,
            rule_id,
            "test_tool",
            json.dumps({}),
            "Test rule",
        )

    async def test_trigger_prevents_update(self, approvals_pool):
        """PostgreSQL trigger should prevent UPDATE on approval_events."""
        action_id = uuid.uuid4()

        # Create the referenced pending action
        await self._create_pending_action(approvals_pool, action_id)

        # Insert an event
        await record_approval_event(
            approvals_pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system",
            action_id=action_id,
        )

        # Verify it was inserted
        row = await approvals_pool.fetchrow(
            "SELECT * FROM approval_events WHERE action_id = $1", action_id
        )
        assert row is not None
        original_actor = row["actor"]

        # Attempt to update should fail
        with pytest.raises(Exception) as exc_info:
            await approvals_pool.execute(
                "UPDATE approval_events SET actor = $1 WHERE action_id = $2",
                "hacker",
                action_id,
            )

        assert "approval_events is append-only" in str(exc_info.value)
        assert "UPDATE is not allowed" in str(exc_info.value)

        # Verify the row was not modified
        row_after = await approvals_pool.fetchrow(
            "SELECT * FROM approval_events WHERE action_id = $1", action_id
        )
        assert row_after["actor"] == original_actor

    async def test_trigger_prevents_delete(self, approvals_pool):
        """PostgreSQL trigger should prevent DELETE from approval_events."""
        action_id = uuid.uuid4()

        # Create the referenced pending action
        await self._create_pending_action(approvals_pool, action_id)

        # Insert an event
        await record_approval_event(
            approvals_pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system",
            action_id=action_id,
        )

        # Verify it exists
        count_before = await approvals_pool.fetchval(
            "SELECT COUNT(*) FROM approval_events WHERE action_id = $1", action_id
        )
        assert count_before == 1

        # Attempt to delete should fail
        with pytest.raises(Exception) as exc_info:
            await approvals_pool.execute(
                "DELETE FROM approval_events WHERE action_id = $1",
                action_id,
            )

        assert "approval_events is append-only" in str(exc_info.value)
        assert "DELETE is not allowed" in str(exc_info.value)

        # Verify the row still exists
        count_after = await approvals_pool.fetchval(
            "SELECT COUNT(*) FROM approval_events WHERE action_id = $1", action_id
        )
        assert count_after == 1

    async def test_insert_still_allowed(self, approvals_pool):
        """INSERT operations should continue to work normally."""
        action_id = uuid.uuid4()

        # Create the referenced pending action
        await self._create_pending_action(approvals_pool, action_id)

        # Multiple inserts for the same action should succeed
        await record_approval_event(
            approvals_pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system",
            action_id=action_id,
        )

        await record_approval_event(
            approvals_pool,
            ApprovalEventType.ACTION_APPROVED,
            actor="user@example.com",
            action_id=action_id,
        )

        await record_approval_event(
            approvals_pool,
            ApprovalEventType.ACTION_EXECUTION_SUCCEEDED,
            actor="system:executor",
            action_id=action_id,
        )

        # Verify all three events exist
        count = await approvals_pool.fetchval(
            "SELECT COUNT(*) FROM approval_events WHERE action_id = $1", action_id
        )
        assert count == 3

        # Verify they are in the correct order
        rows = await approvals_pool.fetch(
            "SELECT event_type FROM approval_events WHERE action_id = $1 ORDER BY occurred_at",
            action_id,
        )
        assert rows[0]["event_type"] == "action_queued"
        assert rows[1]["event_type"] == "action_approved"
        assert rows[2]["event_type"] == "action_execution_succeeded"

    async def test_constraint_requires_action_or_rule_id(self, approvals_pool):
        """Database constraint should enforce action_id OR rule_id is present."""
        # This should fail at the database level
        with pytest.raises(Exception) as exc_info:
            await approvals_pool.execute(
                "INSERT INTO approval_events (event_type, actor) VALUES ($1, $2)",
                "action_queued",
                "system",
            )

        # The constraint name or violation message should be in the error
        error_msg = str(exc_info.value).lower()
        assert (
            "approval_events_link_check" in error_msg
            or "check constraint" in error_msg
            or "violates check" in error_msg
        )

    async def test_constraint_validates_event_type(self, approvals_pool):
        """Database constraint should validate event_type enum values."""
        action_id = uuid.uuid4()

        # Create the referenced pending action
        await self._create_pending_action(approvals_pool, action_id)

        # Invalid event type should fail
        with pytest.raises(Exception) as exc_info:
            await approvals_pool.execute(
                "INSERT INTO approval_events (event_type, action_id, actor) VALUES ($1, $2, $3)",
                "invalid_event_type",
                action_id,
                "system",
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "approval_events_type_check" in error_msg
            or "check constraint" in error_msg
            or "violates check" in error_msg
        )
