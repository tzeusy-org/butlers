"""Pydantic models for the audit log API.

``AuditEntry`` maps to the legacy ``dashboard_audit_log`` table in the
Switchboard database (used by ``log_audit_entry``).

``AuditLogEntry`` maps to the new ``public.audit_log`` primitive table
introduced in core_092.  This is the model returned by the
``GET /api/audit-log`` and ``GET /api/audit-log/{id}`` endpoints.
"""

from __future__ import annotations

from datetime import datetime
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

    @classmethod
    def from_record(cls, row: object) -> AuditLogEntry:
        """Build an AuditLogEntry from an asyncpg Record."""
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
