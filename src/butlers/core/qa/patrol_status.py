"""Canonical vocabulary for persisted QA patrol outcomes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, Literal, TypeGuard

QaPatrolStatus = Literal[
    "running",
    "clean",
    "findings_dispatched",
    "error",
    "skipped_overlap",
    "suppressed",
    "halted",
]

VALID_PATROL_STATUSES: Final[frozenset[QaPatrolStatus]] = frozenset(
    {
        "running",
        "clean",
        "findings_dispatched",
        "error",
        "skipped_overlap",
        "suppressed",
        "halted",
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


#: Gates that stop a patrol from dispatching its novel findings, most blocking first. An open
#: breaker or a full cap queue their findings for a later patrol; with no model they are not
#: dispatched and a later patrol rediscovers them. A cooldown or severity filter is a normal
#: suppression, not a halt.
HALT_REASONS: Final[tuple[str, ...]] = ("circuit_breaker", "no_model", "concurrency_cap")


def derive_halt_reason(reasons: Iterable[str]) -> str | None:
    """Name the gate that stopped dispatch, or ``None`` when no gate did.

    ``reasons`` are the dispatch outcomes of the findings that were not dispatched; the most
    blocking gate present wins so the status never understates why remediation stopped.
    """
    present = set(reasons)
    return next((reason for reason in HALT_REASONS if reason in present), None)
