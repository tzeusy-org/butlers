"""Server-held rollout authority for durable approval delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ApprovalDeliveryRollout:
    """Fail-closed writer and worker activation state."""

    admission_enabled: bool = False
    worker_enabled: bool = False


async def read_approval_delivery_rollout(
    connection: Any,
    *,
    lock: bool = False,
) -> ApprovalDeliveryRollout:
    """Read the schema-local rollout row; absence or invalidity disables both paths."""
    lock_clause = " FOR SHARE" if lock else ""
    try:
        row = await connection.fetchrow(
            "SELECT admission_enabled, worker_enabled "
            "FROM approval_delivery_rollout WHERE singleton IS TRUE" + lock_clause
        )
    except Exception:  # Rolling/pre-migration schemas must remain inert.
        return ApprovalDeliveryRollout()
    if row is None:
        return ApprovalDeliveryRollout()
    try:
        admission_enabled = row["admission_enabled"]
        worker_enabled = row["worker_enabled"]
    except (KeyError, IndexError, TypeError):
        return ApprovalDeliveryRollout()
    return ApprovalDeliveryRollout(
        admission_enabled=admission_enabled is True,
        worker_enabled=worker_enabled is True,
    )


__all__ = ["ApprovalDeliveryRollout", "read_approval_delivery_rollout"]
