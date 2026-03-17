"""Core healing package for butler self-healing infrastructure.

Provides deterministic error fingerprinting, severity scoring, and
dual-input support (raw exception objects or structured string fields),
plus the CRUD and gate query layer for shared.healing_attempts.
"""

from __future__ import annotations

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

__all__ = [
    # fingerprint
    "FingerprintResult",
    "compute_fingerprint",
    "compute_fingerprint_from_report",
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
]
