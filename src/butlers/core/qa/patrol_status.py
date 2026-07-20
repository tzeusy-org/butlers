"""Canonical vocabulary for persisted QA patrol outcomes."""

from __future__ import annotations

from typing import Final, Literal, TypeGuard

QaPatrolStatus = Literal[
    "running",
    "clean",
    "findings_dispatched",
    "error",
    "skipped_overlap",
    "suppressed",
]

VALID_PATROL_STATUSES: Final[frozenset[QaPatrolStatus]] = frozenset(
    {
        "running",
        "clean",
        "findings_dispatched",
        "error",
        "skipped_overlap",
        "suppressed",
    }
)


def is_valid_patrol_status(value: str) -> TypeGuard[QaPatrolStatus]:
    """Return whether a request value is part of the persisted patrol vocabulary."""
    return value in VALID_PATROL_STATUSES


def require_patrol_status(value: str) -> QaPatrolStatus:
    """Return a canonical patrol status or reject an invalid writer value."""
    if not is_valid_patrol_status(value):
        raise ValueError(f"Unknown QA patrol status: {value}")
    return value
