"""Data models for the approvals module.

Defines PendingAction and ApprovalRule dataclasses that map 1:1 to the
corresponding database tables. Includes JSON serialisation helpers for
MCP tool responses and database round-tripping.
"""

from __future__ import annotations

import enum
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ActionStatus(enum.StrEnum):
    """Valid statuses for a pending action."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"


def _parse_uuid(value: Any) -> uuid.UUID:
    """Parse a UUID from a string or UUID object."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _parse_datetime(value: Any) -> datetime:
    """Parse a datetime from a string or datetime object."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _parse_optional_uuid(value: Any) -> uuid.UUID | None:
    """Parse an optional UUID."""
    if value is None:
        return None
    return _parse_uuid(value)


def _parse_optional_datetime(value: Any) -> datetime | None:
    """Parse an optional datetime."""
    if value is None:
        return None
    return _parse_datetime(value)


def _parse_jsonb(value: Any) -> dict[str, Any]:
    """Parse a JSONB value (may be a string or already a dict)."""
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, dict):
        return value
    return dict(value)


def _parse_optional_jsonb(value: Any) -> dict[str, Any] | None:
    """Parse an optional JSONB value."""
    if value is None:
        return None
    return _parse_jsonb(value)


@dataclass
class PendingAction:
    """A tool invocation awaiting human approval.

    Maps 1:1 to the ``pending_actions`` database table.
    """

    id: uuid.UUID
    tool_name: str
    tool_args: dict[str, Any]
    status: ActionStatus
    requested_at: datetime
    agent_summary: str | None = None
    session_id: uuid.UUID | None = None
    expires_at: datetime | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    execution_result: dict[str, Any] | None = None
    approval_rule_id: uuid.UUID | None = None
    why: str | None = None
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        d: dict[str, Any] = {
            "id": str(self.id),
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "status": self.status.value,
            "requested_at": self.requested_at.isoformat(),
            "agent_summary": self.agent_summary,
            "session_id": str(self.session_id) if self.session_id else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "execution_result": self.execution_result,
            "approval_rule_id": str(self.approval_rule_id) if self.approval_rule_id else None,
            "why": self.why,
            "evidence": self.evidence,
        }
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingAction:
        """Reconstruct a PendingAction from a dictionary (e.g. from to_dict())."""
        return cls(
            id=_parse_uuid(data["id"]),
            tool_name=data["tool_name"],
            tool_args=_parse_jsonb(data["tool_args"]),
            status=ActionStatus(data["status"]),
            requested_at=_parse_datetime(data["requested_at"]),
            agent_summary=data.get("agent_summary"),
            session_id=_parse_optional_uuid(data.get("session_id")),
            expires_at=_parse_optional_datetime(data.get("expires_at")),
            decided_by=data.get("decided_by"),
            decided_at=_parse_optional_datetime(data.get("decided_at")),
            execution_result=_parse_optional_jsonb(data.get("execution_result")),
            approval_rule_id=_parse_optional_uuid(data.get("approval_rule_id")),
            why=data.get("why"),
            evidence=list(data.get("evidence") or []),
        )

    @classmethod
    def from_row(cls, row: Any) -> PendingAction:
        """Reconstruct a PendingAction from a database row (asyncpg Record or mapping)."""
        _get = lambda key, default=None: (  # noqa: E731
            row.get(key, default) if hasattr(row, "get") else row[key]
        )
        return cls(
            id=_parse_uuid(row["id"]),
            tool_name=row["tool_name"],
            tool_args=_parse_jsonb(row["tool_args"]),
            status=ActionStatus(row["status"]),
            requested_at=_parse_datetime(row["requested_at"]),
            agent_summary=_get("agent_summary"),
            session_id=_parse_optional_uuid(_get("session_id")),
            expires_at=_parse_optional_datetime(_get("expires_at")),
            decided_by=_get("decided_by"),
            decided_at=_parse_optional_datetime(_get("decided_at")),
            execution_result=_parse_optional_jsonb(_get("execution_result")),
            approval_rule_id=_parse_optional_uuid(_get("approval_rule_id")),
            why=_get("why"),
            evidence=list(_get("evidence") or []),
        )


@dataclass
class ApprovalRule:
    """A reusable rule for auto-approving tool invocations.

    Maps 1:1 to the ``approval_rules`` database table.
    """

    id: uuid.UUID
    tool_name: str
    arg_constraints: dict[str, Any]
    description: str
    created_at: datetime
    created_from: uuid.UUID | None = None
    expires_at: datetime | None = None
    max_uses: int | None = None
    use_count: int = field(default=0)
    active: bool = field(default=True)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "id": str(self.id),
            "tool_name": self.tool_name,
            "arg_constraints": self.arg_constraints,
            "description": self.description,
            "created_from": str(self.created_from) if self.created_from else None,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "max_uses": self.max_uses,
            "use_count": self.use_count,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalRule:
        """Reconstruct an ApprovalRule from a dictionary (e.g. from to_dict())."""
        return cls(
            id=_parse_uuid(data["id"]),
            tool_name=data["tool_name"],
            arg_constraints=_parse_jsonb(data["arg_constraints"]),
            description=data["description"],
            created_from=_parse_optional_uuid(data.get("created_from")),
            created_at=_parse_datetime(data["created_at"]),
            expires_at=_parse_optional_datetime(data.get("expires_at")),
            max_uses=data.get("max_uses"),
            use_count=data.get("use_count", 0),
            active=data.get("active", True),
        )

    @classmethod
    def from_row(cls, row: Any) -> ApprovalRule:
        """Reconstruct an ApprovalRule from a database row (asyncpg Record or mapping)."""
        return cls(
            id=_parse_uuid(row["id"]),
            tool_name=row["tool_name"],
            arg_constraints=_parse_jsonb(row["arg_constraints"]),
            description=row["description"],
            created_from=_parse_optional_uuid(
                row.get("created_from") if hasattr(row, "get") else row["created_from"]
            ),
            created_at=_parse_datetime(row["created_at"]),
            expires_at=_parse_optional_datetime(
                row.get("expires_at") if hasattr(row, "get") else row["expires_at"]
            ),
            max_uses=row.get("max_uses") if hasattr(row, "get") else row["max_uses"],
            use_count=row.get("use_count", 0) if hasattr(row, "get") else row["use_count"],
            active=row.get("active", True) if hasattr(row, "get") else row["active"],
        )
