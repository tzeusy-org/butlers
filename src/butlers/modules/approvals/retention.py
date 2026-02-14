"""Retention policy enforcement for approvals data.

Provides configurable retention policies for pending actions, approval rules,
and audit events. Policies control automatic cleanup of old/stale data while
preserving audit trails within configured windows.

Retention knobs:
- pending_actions_retention_days: Archive/delete decided actions older than N days
- approval_rules_retention_days: Cleanup inactive rules older than N days
- approval_events_retention_days: Archive immutable events older than N days (compliance)

Default policies:
- Pending actions: 90 days after decision
- Approval rules: 180 days after deactivation
- Approval events: 365 days (1 year audit window)

SECURITY NOTE - cleanup_old_events():
This function deletes approval_events which are protected by an immutability
trigger. It MUST be called with a database connection that has sufficient
privileges (SUPERUSER or a role with trigger bypass permissions). Calling
this function with a normal user connection will raise a PermissionError.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.modules.approvals.models import ActionStatus

logger = logging.getLogger(__name__)

# Terminal action statuses eligible for cleanup after retention period
TERMINAL_ACTION_STATUSES = [
    ActionStatus.APPROVED.value,
    ActionStatus.REJECTED.value,
    ActionStatus.EXPIRED.value,
    ActionStatus.EXECUTED.value,
]


@dataclass
class RetentionPolicy:
    """Configurable retention windows for approvals data."""

    pending_actions_retention_days: int = 90
    approval_rules_retention_days: int = 180
    approval_events_retention_days: int = 365

    def __post_init__(self):
        """Validate retention policy values."""
        if self.pending_actions_retention_days < 1:
            raise ValueError("pending_actions_retention_days must be >= 1")
        if self.approval_rules_retention_days < 1:
            raise ValueError("approval_rules_retention_days must be >= 1")
        if self.approval_events_retention_days < 1:
            raise ValueError("approval_events_retention_days must be >= 1")


async def cleanup_old_actions(
    pool: Any,
    policy: RetentionPolicy,
    dry_run: bool = False,
) -> dict[str, int]:
    """Delete or archive pending actions older than the retention window.

    Only terminal statuses (approved, rejected, expired, executed) are eligible
    for cleanup. Pending actions remain until resolved.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    policy:
        Retention policy configuration.
    dry_run:
        If True, return counts without deleting.

    Returns
    -------
    dict[str, int]
        Counts of actions deleted by status.
    """
    cutoff = datetime.now(UTC) - timedelta(days=policy.pending_actions_retention_days)

    # Count eligible actions
    count_query = """
        SELECT status, COUNT(*) as count
        FROM pending_actions
        WHERE status = ANY($1::text[])
          AND decided_at IS NOT NULL
          AND decided_at < $2
        GROUP BY status
    """
    rows = await pool.fetch(
        count_query,
        TERMINAL_ACTION_STATUSES,
        cutoff,
    )

    counts = {row["status"]: row["count"] for row in rows}
    total = sum(counts.values())

    if total == 0:
        logger.info(
            "No actions eligible for cleanup (retention=%dd)",
            policy.pending_actions_retention_days,
        )
        return {}

    logger.info(
        "Found %d actions eligible for cleanup (retention=%dd): %s",
        total,
        policy.pending_actions_retention_days,
        counts,
    )

    if dry_run:
        logger.info("DRY RUN: would delete %d actions", total)
        return counts

    # Delete old actions
    delete_query = """
        DELETE FROM pending_actions
        WHERE status = ANY($1::text[])
          AND decided_at IS NOT NULL
          AND decided_at < $2
    """
    await pool.execute(
        delete_query,
        TERMINAL_ACTION_STATUSES,
        cutoff,
    )

    logger.info("Deleted %d old pending actions", total)
    return counts


async def cleanup_old_rules(
    pool: Any,
    policy: RetentionPolicy,
    dry_run: bool = False,
) -> int:
    """Delete inactive approval rules older than the retention window.

    Only rules marked inactive (active=false) are eligible for cleanup.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    policy:
        Retention policy configuration.
    dry_run:
        If True, return count without deleting.

    Returns
    -------
    int
        Number of rules deleted.
    """
    cutoff = datetime.now(UTC) - timedelta(days=policy.approval_rules_retention_days)

    # Count eligible rules
    count_row = await pool.fetchrow(
        """
        SELECT COUNT(*) as count
        FROM approval_rules
        WHERE active = false
          AND created_at < $1
        """,
        cutoff,
    )

    count = count_row["count"] if count_row else 0

    if count == 0:
        logger.info(
            "No rules eligible for cleanup (retention=%dd)",
            policy.approval_rules_retention_days,
        )
        return 0

    logger.info(
        "Found %d inactive rules eligible for cleanup (retention=%dd)",
        count,
        policy.approval_rules_retention_days,
    )

    if dry_run:
        logger.info("DRY RUN: would delete %d rules", count)
        return count

    # Delete old inactive rules
    await pool.execute(
        """
        DELETE FROM approval_rules
        WHERE active = false
          AND created_at < $1
        """,
        cutoff,
    )

    logger.info("Deleted %d old inactive approval rules", count)
    return count


async def cleanup_old_events(
    pool: Any,
    policy: RetentionPolicy,
    dry_run: bool = False,
    *,
    privileged: bool = False,
) -> int:
    """Archive or delete approval events older than the retention window.

    Events are immutable audit records protected by a database trigger.
    This function provides controlled cleanup after the compliance retention
    window expires.

    CRITICAL SECURITY REQUIREMENT:
    The approval_events table has an immutability trigger that prevents
    DELETE operations by normal users. This function MUST be called with
    a privileged database connection (SUPERUSER or role with trigger bypass
    permissions).

    Parameters
    ----------
    pool:
        asyncpg connection pool with SUPERUSER or privileged role.
    policy:
        Retention policy configuration.
    dry_run:
        If True, return count without deleting.
    privileged:
        Safety flag that MUST be set to True to acknowledge the caller
        has verified the connection pool has sufficient permissions.
        This prevents accidental calls with unprivileged connections.

    Returns
    -------
    int
        Number of events deleted.

    Raises
    ------
    PermissionError
        If privileged flag is False, preventing execution with potentially
        insufficient database permissions.
    """
    if not privileged:
        raise PermissionError(
            "cleanup_old_events() requires a privileged database connection. "
            "Set privileged=True only after verifying the pool has SUPERUSER "
            "or trigger bypass permissions."
        )

    cutoff = datetime.now(UTC) - timedelta(days=policy.approval_events_retention_days)

    # Count eligible events
    count_row = await pool.fetchrow(
        """
        SELECT COUNT(*) as count
        FROM approval_events
        WHERE occurred_at < $1
        """,
        cutoff,
    )

    count = count_row["count"] if count_row else 0

    if count == 0:
        logger.info(
            "No events eligible for cleanup (retention=%dd)",
            policy.approval_events_retention_days,
        )
        return 0

    logger.info(
        "Found %d events eligible for cleanup (retention=%dd)",
        count,
        policy.approval_events_retention_days,
    )

    if dry_run:
        logger.info("DRY RUN: would delete %d events", count)
        return count

    # Delete old events (requires SUPERUSER to bypass immutability trigger)
    await pool.execute(
        """
        DELETE FROM approval_events
        WHERE occurred_at < $1
        """,
        cutoff,
    )

    logger.info("Deleted %d old approval events", count)
    return count


async def run_retention_cleanup(
    pool: Any,
    policy: RetentionPolicy | None = None,
    dry_run: bool = False,
    *,
    privileged: bool = False,
) -> dict[str, Any]:
    """Execute all retention cleanup tasks.

    Convenience function that runs all cleanup operations in sequence
    and returns aggregate statistics.

    Note: Event cleanup requires a privileged database connection. Set
    privileged=True only after verifying the connection pool has SUPERUSER
    or trigger bypass permissions.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    policy:
        Retention policy configuration (uses defaults if None).
    dry_run:
        If True, report what would be deleted without actually deleting.
    privileged:
        If True, also run event cleanup (requires SUPERUSER connection).
        If False, skip event cleanup to avoid permission errors.

    Returns
    -------
    dict[str, Any]
        Statistics from all cleanup operations.
    """
    if policy is None:
        policy = RetentionPolicy()

    logger.info(
        "Starting retention cleanup (dry_run=%s, privileged=%s)",
        dry_run,
        privileged,
    )

    actions_counts = await cleanup_old_actions(pool, policy, dry_run)
    rules_count = await cleanup_old_rules(pool, policy, dry_run)

    # Only cleanup events if caller has privileged connection
    events_count = 0
    if privileged:
        events_count = await cleanup_old_events(pool, policy, dry_run, privileged=True)
    else:
        logger.info("Skipping event cleanup (requires privileged connection)")

    stats = {
        "actions": actions_counts,
        "rules": rules_count,
        "events": events_count,
        "total_actions": sum(actions_counts.values()),
        "total_items": sum(actions_counts.values()) + rules_count + events_count,
        "policy": {
            "pending_actions_retention_days": policy.pending_actions_retention_days,
            "approval_rules_retention_days": policy.approval_rules_retention_days,
            "approval_events_retention_days": policy.approval_events_retention_days,
        },
    }

    logger.info("Retention cleanup complete: %s", stats)
    return stats
