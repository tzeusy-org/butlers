"""Pydantic models for the audit log API.

``AuditEntry`` maps to the legacy ``dashboard_audit_log`` table in the
Switchboard database (used by ``log_audit_entry``).

``AuditLogEntry`` maps to the new ``public.audit_log`` primitive table
introduced in core_092.  This is the model returned by the
``GET /api/audit-log`` and ``GET /api/audit-log/{id}`` endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AuditEntry(BaseModel):
    """Single audit log entry from the legacy dashboard_audit_log table."""

    id: UUID
    butler: str
    operation: str
    request_summary: dict = Field(default_factory=dict)
    result: str
    error: str | None = None
    user_context: dict = Field(default_factory=dict)
    created_at: datetime


class AuditLogEntry(BaseModel):
    """Single entry from the ``public.audit_log`` primitive table (core_092).

    This is the canonical audit primitive used by every mutation endpoint
    that changes system state.
    """

    id: int
    ts: datetime
    actor: str
    action: str
    target: str | None = None
    note: str | None = None
    ip: str | None = None
    request_id: UUID | None = None
    # Added in core_122 to support unifying the richer dashboard_audit_log
    # writers into public.audit_log.  All three are optional so existing rows
    # (and callers) that never populate them deserialise unchanged.
    metadata: dict[str, Any] | None = None
    result: str | None = None
    error: str | None = None

    @classmethod
    def from_record(cls, row: object) -> AuditLogEntry:
        """Build an AuditLogEntry from an asyncpg Record.

        The ``metadata``/``result``/``error`` columns (core_122) are NOT read
        here: the existing read queries do not project them, so they default to
        ``None``.  Wiring the reader to surface them is intentionally left to a
        later PR in the audit-writer unification (this change is schema + write
        capability only).
        """
        raw_ip = row["ip"]  # type: ignore[index]
        ip_str = str(raw_ip) if raw_ip is not None else None
        return cls(
            id=row["id"],  # type: ignore[index]
            ts=row["ts"],  # type: ignore[index]
            actor=row["actor"],  # type: ignore[index]
            action=row["action"],  # type: ignore[index]
            target=row["target"],  # type: ignore[index]
            note=row["note"],  # type: ignore[index]
            ip=ip_str,
            request_id=row["request_id"],  # type: ignore[index]
        )
