"""Tests for approval retention policies.

Validates AC for butlers-0p6.4:
3. Retention policy knobs for actions/rules/events are defined and enforced.
4. Tests cover retention behavior.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.retention import (
    RetentionPolicy,
    cleanup_old_actions,
    cleanup_old_events,
    cleanup_old_rules,
    run_retention_cleanup,
)

pytestmark = pytest.mark.unit


class MockPool:
    """Mock database pool for retention testing."""

    def __init__(self):
        self.actions = []
        self.rules = []
        self.events = []
        self.executed_queries = []

    async def fetch(self, query: str, *args):
        """Mock fetch operation."""
        self.executed_queries.append(("fetch", query, args))

        if "GROUP BY status" in query:
            # Count actions by status
            cutoff = args[-1]
            counts = {}
            for action in self.actions:
                if (
                    action["status"]
                    in [
                        ActionStatus.APPROVED.value,
                        ActionStatus.REJECTED.value,
                        ActionStatus.EXPIRED.value,
                        ActionStatus.EXECUTED.value,
                    ]
                    and action.get("decided_at")
                    and action["decided_at"] < cutoff
                ):
                    status = action["status"]
                    counts[status] = counts.get(status, 0) + 1
            return [{"status": k, "count": v} for k, v in counts.items()]

        return []

    async def fetchrow(self, query: str, *args):
        """Mock fetchrow operation."""
        self.executed_queries.append(("fetchrow", query, args))

        if "approval_rules" in query:
            # Count inactive old rules
            cutoff = args[0]
            count = sum(
                1 for rule in self.rules if not rule["active"] and rule["created_at"] < cutoff
            )
            return {"count": count}

        if "approval_events" in query:
            # Count old events
            cutoff = args[0]
            count = sum(1 for event in self.events if event["occurred_at"] < cutoff)
            return {"count": count}

        return None

    async def execute(self, query: str, *args):
        """Mock execute operation."""
        self.executed_queries.append(("execute", query, args))

        if "DELETE FROM pending_actions" in query:
            # Remove old actions
            cutoff = args[-1]
            self.actions = [
                a
                for a in self.actions
                if not (
                    a["status"]
                    in [
                        ActionStatus.APPROVED.value,
                        ActionStatus.REJECTED.value,
                        ActionStatus.EXPIRED.value,
                        ActionStatus.EXECUTED.value,
                    ]
                    and a.get("decided_at")
                    and a["decided_at"] < cutoff
                )
            ]

        elif "DELETE FROM approval_rules" in query:
            # Remove inactive old rules
            cutoff = args[0]
            self.rules = [r for r in self.rules if r["active"] or r["created_at"] >= cutoff]

        elif "DELETE FROM approval_events" in query:
            # Remove old events
            cutoff = args[0]
            self.events = [e for e in self.events if e["occurred_at"] >= cutoff]


class TestRetentionPolicy:
    """Test retention policy configuration."""

    def test_default_retention_windows(self):
        """Default policy has reasonable retention windows."""
        policy = RetentionPolicy()
        assert policy.pending_actions_retention_days == 90
        assert policy.approval_rules_retention_days == 180
        assert policy.approval_events_retention_days == 365

    def test_custom_retention_windows(self):
        """Custom retention windows can be configured."""
        policy = RetentionPolicy(
            pending_actions_retention_days=30,
            approval_rules_retention_days=60,
            approval_events_retention_days=730,
        )
        assert policy.pending_actions_retention_days == 30
        assert policy.approval_rules_retention_days == 60
        assert policy.approval_events_retention_days == 730

    def test_validates_positive_retention_days(self):
        """Retention days must be positive."""
        with pytest.raises(ValueError, match="must be >= 1"):
            RetentionPolicy(pending_actions_retention_days=0)

        with pytest.raises(ValueError, match="must be >= 1"):
            RetentionPolicy(approval_rules_retention_days=-1)

        with pytest.raises(ValueError, match="must be >= 1"):
            RetentionPolicy(approval_events_retention_days=0)


class TestCleanupOldActions:
    """Test pending actions cleanup."""

    async def test_deletes_old_decided_actions(self):
        """Actions past retention window are deleted."""
        pool = MockPool()
        now = datetime.now(UTC)
        old_date = now - timedelta(days=100)

        pool.actions = [
            {
                "id": uuid.uuid4(),
                "status": ActionStatus.EXECUTED.value,
                "decided_at": old_date,
            },
            {
                "id": uuid.uuid4(),
                "status": ActionStatus.APPROVED.value,
                "decided_at": old_date,
            },
        ]

        policy = RetentionPolicy(pending_actions_retention_days=90)
        counts = await cleanup_old_actions(pool, policy, dry_run=False)

        assert counts[ActionStatus.EXECUTED.value] == 1
        assert counts[ActionStatus.APPROVED.value] == 1
        assert len(pool.actions) == 0

    async def test_preserves_recent_actions(self):
        """Actions within retention window are preserved."""
        pool = MockPool()
        now = datetime.now(UTC)
        recent_date = now - timedelta(days=30)

        pool.actions = [
            {
                "id": uuid.uuid4(),
                "status": ActionStatus.EXECUTED.value,
                "decided_at": recent_date,
            },
        ]

        policy = RetentionPolicy(pending_actions_retention_days=90)
        counts = await cleanup_old_actions(pool, policy, dry_run=False)

        assert counts == {}
        assert len(pool.actions) == 1

    async def test_preserves_pending_actions(self):
        """Pending actions are never deleted."""
        pool = MockPool()
        now = datetime.now(UTC)
        old_date = now - timedelta(days=100)

        pool.actions = [
            {
                "id": uuid.uuid4(),
                "status": ActionStatus.PENDING.value,
                "decided_at": None,
                "requested_at": old_date,
            },
        ]

        policy = RetentionPolicy(pending_actions_retention_days=90)
        counts = await cleanup_old_actions(pool, policy, dry_run=False)

        assert counts == {}
        assert len(pool.actions) == 1

    async def test_dry_run_mode(self):
        """Dry run reports counts without deleting."""
        pool = MockPool()
        now = datetime.now(UTC)
        old_date = now - timedelta(days=100)

        pool.actions = [
            {
                "id": uuid.uuid4(),
                "status": ActionStatus.EXECUTED.value,
                "decided_at": old_date,
            },
        ]

        policy = RetentionPolicy(pending_actions_retention_days=90)
        counts = await cleanup_old_actions(pool, policy, dry_run=True)

        assert counts[ActionStatus.EXECUTED.value] == 1
        assert len(pool.actions) == 1  # Not deleted


class TestCleanupOldRules:
    """Test approval rules cleanup."""

    async def test_deletes_old_inactive_rules(self):
        """Inactive rules past retention window are deleted."""
        pool = MockPool()
        now = datetime.now(UTC)
        old_date = now - timedelta(days=200)

        pool.rules = [
            {
                "id": uuid.uuid4(),
                "active": False,
                "created_at": old_date,
            },
        ]

        policy = RetentionPolicy(approval_rules_retention_days=180)
        count = await cleanup_old_rules(pool, policy, dry_run=False)

        assert count == 1
        assert len(pool.rules) == 0

    async def test_preserves_active_rules(self):
        """Active rules are never deleted."""
        pool = MockPool()
        now = datetime.now(UTC)
        old_date = now - timedelta(days=200)

        pool.rules = [
            {
                "id": uuid.uuid4(),
                "active": True,
                "created_at": old_date,
            },
        ]

        policy = RetentionPolicy(approval_rules_retention_days=180)
        count = await cleanup_old_rules(pool, policy, dry_run=False)

        assert count == 0
        assert len(pool.rules) == 1

    async def test_preserves_recent_inactive_rules(self):
        """Recently deactivated rules are preserved."""
        pool = MockPool()
        now = datetime.now(UTC)
        recent_date = now - timedelta(days=100)

        pool.rules = [
            {
                "id": uuid.uuid4(),
                "active": False,
                "created_at": recent_date,
            },
        ]

        policy = RetentionPolicy(approval_rules_retention_days=180)
        count = await cleanup_old_rules(pool, policy, dry_run=False)

        assert count == 0
        assert len(pool.rules) == 1


class TestCleanupOldEvents:
    """Test approval events cleanup."""

    async def test_requires_privileged_flag(self):
        """cleanup_old_events requires privileged=True flag."""
        pool = MockPool()
        policy = RetentionPolicy()

        with pytest.raises(PermissionError, match="privileged database connection"):
            await cleanup_old_events(pool, policy, dry_run=False, privileged=False)

    async def test_deletes_old_events_with_privilege(self):
        """Events past retention window are deleted with privileged connection."""
        pool = MockPool()
        now = datetime.now(UTC)
        old_date = now - timedelta(days=400)

        pool.events = [
            {
                "event_id": uuid.uuid4(),
                "occurred_at": old_date,
            },
        ]

        policy = RetentionPolicy(approval_events_retention_days=365)
        count = await cleanup_old_events(pool, policy, dry_run=False, privileged=True)

        assert count == 1
        assert len(pool.events) == 0

    async def test_preserves_recent_events(self):
        """Recent events are preserved."""
        pool = MockPool()
        now = datetime.now(UTC)
        recent_date = now - timedelta(days=100)

        pool.events = [
            {
                "event_id": uuid.uuid4(),
                "occurred_at": recent_date,
            },
        ]

        policy = RetentionPolicy(approval_events_retention_days=365)
        count = await cleanup_old_events(pool, policy, dry_run=False, privileged=True)

        assert count == 0
        assert len(pool.events) == 1


class TestRunRetentionCleanup:
    """Test full retention cleanup workflow."""

    async def test_runs_all_cleanup_tasks_with_privilege(self):
        """All cleanup tasks are executed with privileged flag."""
        pool = MockPool()
        now = datetime.now(UTC)
        old_date = now - timedelta(days=400)

        pool.actions = [
            {
                "id": uuid.uuid4(),
                "status": ActionStatus.EXECUTED.value,
                "decided_at": old_date,
            },
        ]
        pool.rules = [
            {
                "id": uuid.uuid4(),
                "active": False,
                "created_at": old_date,
            },
        ]
        pool.events = [
            {
                "event_id": uuid.uuid4(),
                "occurred_at": old_date,
            },
        ]

        policy = RetentionPolicy(
            pending_actions_retention_days=90,
            approval_rules_retention_days=180,
            approval_events_retention_days=365,
        )

        stats = await run_retention_cleanup(pool, policy, dry_run=False, privileged=True)

        assert stats["rules"] == 1
        assert stats["events"] == 1
        assert stats["total_actions"] == 1
        assert stats["total_items"] == 3
        assert len(pool.actions) == 0
        assert len(pool.rules) == 0
        assert len(pool.events) == 0

    async def test_skips_events_without_privilege(self):
        """Events cleanup is skipped without privileged flag."""
        pool = MockPool()
        now = datetime.now(UTC)
        old_date = now - timedelta(days=400)

        pool.events = [
            {
                "event_id": uuid.uuid4(),
                "occurred_at": old_date,
            },
        ]

        stats = await run_retention_cleanup(pool, dry_run=False, privileged=False)

        assert stats["events"] == 0
        assert len(pool.events) == 1  # Not deleted

    async def test_uses_default_policy(self):
        """Default policy is used when none provided."""
        pool = MockPool()
        stats = await run_retention_cleanup(pool, policy=None, dry_run=True, privileged=False)

        assert stats["policy"]["pending_actions_retention_days"] == 90
        assert stats["policy"]["approval_rules_retention_days"] == 180
        assert stats["policy"]["approval_events_retention_days"] == 365

    async def test_dry_run_mode_for_full_cleanup(self):
        """Dry run mode reports stats without deletions."""
        pool = MockPool()
        now = datetime.now(UTC)
        old_date = now - timedelta(days=400)

        pool.actions = [
            {
                "id": uuid.uuid4(),
                "status": ActionStatus.EXECUTED.value,
                "decided_at": old_date,
            },
        ]

        stats = await run_retention_cleanup(pool, dry_run=True, privileged=False)

        assert stats["total_items"] >= 1
        assert len(pool.actions) == 1  # Not deleted
