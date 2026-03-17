"""Core healing package for butler self-healing infrastructure.

Provides deterministic error fingerprinting, severity scoring, dual-input
support (raw exception objects or structured string fields), the CRUD and gate
query layer for shared.healing_attempts, git worktree lifecycle management,
and the 10-gate dispatch engine.
"""

from __future__ import annotations

from butlers.core.healing.dispatch import (
    DispatchResult,
    HealingConfig,
    dispatch_healing,
)
from butlers.core.healing.fingerprint import (
    FingerprintResult,
    compute_fingerprint,
    compute_fingerprint_from_report,
)
from butlers.core.healing.tracking import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    VALID_STATUSES,
    HealingAttemptRow,
    count_active_attempts,
    create_or_join_attempt,
    get_active_attempt,
    get_attempt,
    get_recent_attempt,
    get_recent_terminal_statuses,
    list_attempts,
    recover_stale_attempts,
    update_attempt_status,
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
    # tracking
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "VALID_STATUSES",
    "HealingAttemptRow",
    "count_active_attempts",
    "create_or_join_attempt",
    "get_active_attempt",
    "get_attempt",
    "get_recent_attempt",
    "get_recent_terminal_statuses",
    "list_attempts",
    "recover_stale_attempts",
    "update_attempt_status",
    # worktree
    "WorktreeCreationError",
    "create_healing_worktree",
    "remove_healing_worktree",
    "reap_stale_worktrees",
]
