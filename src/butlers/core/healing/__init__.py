"""Core healing package for butler self-healing infrastructure.

Provides deterministic error fingerprinting, severity scoring, dual-input
support (raw exception objects or structured string fields), the CRUD and gate
query layer for public.healing_attempts, git worktree lifecycle management,
and the 10-gate dispatch engine.
"""

from __future__ import annotations

from butlers.core.healing.dispatch import (
    DispatchResult,
    HealingConfig,
    dispatch_healing,
    redispatch_pending_attempt,
)
from butlers.core.healing.fingerprint import (
    FingerprintResult,
    compute_fingerprint,
    compute_fingerprint_from_report,
)
from butlers.core.healing.tracking import (
    ACTIVE_STATUSES,
    PHASE_SESSION_STATUSES,
    TERMINAL_STATUSES,
    VALID_STATUSES,
    HealingAttemptRow,
    HealingAttemptSessionRow,
    HealingDispatchEventRow,
    count_active_attempts,
    create_dispatch_event,
    create_or_join_attempt,
    get_active_attempt,
    get_attempt,
    get_recent_attempt,
    get_recent_terminal_statuses,
    list_attempts,
    list_dispatch_events,
    list_phase_sessions,
    record_phase_session,
    recover_stale_attempts,
    update_attempt_status,
    update_phase_session_status,
)
from butlers.core.healing.worktree import (
    WorktreeCreationError,
    create_healing_worktree,
    reap_stale_worktrees,
    remove_healing_worktree,
)

__all__ = [
    # fingerprint
    "FingerprintResult",
    "compute_fingerprint",
    "compute_fingerprint_from_report",
    # dispatch
    "DispatchResult",
    "HealingConfig",
    "dispatch_healing",
    "redispatch_pending_attempt",
    # tracking
    "ACTIVE_STATUSES",
    "PHASE_SESSION_STATUSES",
    "TERMINAL_STATUSES",
    "VALID_STATUSES",
    "HealingAttemptRow",
    "HealingAttemptSessionRow",
    "HealingDispatchEventRow",
    "count_active_attempts",
    "create_dispatch_event",
    "create_or_join_attempt",
    "get_active_attempt",
    "get_attempt",
    "get_recent_attempt",
    "get_recent_terminal_statuses",
    "list_attempts",
    "list_dispatch_events",
    "list_phase_sessions",
    "record_phase_session",
    "recover_stale_attempts",
    "update_attempt_status",
    "update_phase_session_status",
    # worktree
    "WorktreeCreationError",
    "create_healing_worktree",
    "remove_healing_worktree",
    "reap_stale_worktrees",
]
