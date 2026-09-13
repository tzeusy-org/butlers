"""Fail-closed guards keeping approval recovery out of generic notification reads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def notification_recovery_exclusion_sql(alias: str = "") -> str:
    """Return a SQL predicate excluding a recovery-bearing metadata envelope."""
    prefix = f"{alias}." if alias else ""
    metadata = f"{prefix}metadata"
    return (
        "NOT ("
        f"jsonb_typeof(COALESCE({metadata}, '{{}}'::jsonb)) = 'object' "
        f"AND jsonb_typeof({metadata} -> 'notify_request') = 'object' "
        f"AND ({metadata} -> 'notify_request') ? 'recovery'"
        ")"
    )


def notification_metadata_is_recovery(value: Any) -> bool:
    """Recognize the forbidden generic recovery marker without reading content."""
    if not isinstance(value, Mapping):
        return False
    notify_request = value.get("notify_request")
    return isinstance(notify_request, Mapping) and "recovery" in notify_request


def message_inbox_recovery_exclusion_sql(alias: str = "") -> str:
    """Return a SQL predicate excluding any forbidden recovery history row."""
    prefix = f"{alias}." if alias else ""
    raw_payload = f"{prefix}raw_payload"
    return (
        "NOT ("
        f"jsonb_typeof(COALESCE({raw_payload} -> 'metadata', '{{}}'::jsonb)) = 'object' "
        f"AND ({raw_payload} -> 'metadata') ? 'approval_recovery'"
        ")"
    )


__all__ = [
    "message_inbox_recovery_exclusion_sql",
    "notification_metadata_is_recovery",
    "notification_recovery_exclusion_sql",
]
