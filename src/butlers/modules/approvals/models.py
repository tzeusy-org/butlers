"""Data models for the approvals module.

Defines PendingAction and ApprovalRule dataclasses that map 1:1 to the
corresponding database tables. Includes JSON serialisation helpers for
MCP tool responses and database round-tripping.
"""

from __future__ import annotations

import enum
import json
import re
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
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"


EvidenceReference = dict[str, str]

GOAL_LINEAGE_COLUMNS = (
    "goal_id",
    "invocation_id",
    "goal_depth",
    "goal_exposed",
    "goal_origin",
    "goal_effect_id",
    "goal_capability",
    "goal_args_digest",
    "goal_approved_args_digest",
)


@dataclass(frozen=True, slots=True)
class GoalCallLineage:
    """Infrastructure lineage, not proof of an authenticated caller.

    Only the future verified domain ingress may supply this value for a live
    call. It carries no token/signature and grants no approval or replay right.
    """

    goal_id: uuid.UUID
    invocation_id: uuid.UUID
    goal_depth: int
    goal_exposed: bool
    goal_origin: str
    goal_effect_id: uuid.UUID
    goal_capability: str
    goal_args_digest: str
    goal_approved_args_digest: str | None = None

    def __post_init__(self) -> None:
        parts = self.goal_capability.split("__") if type(self.goal_capability) is str else []
        if (
            not all(
                isinstance(v, uuid.UUID)
                for v in (self.goal_id, self.invocation_id, self.goal_effect_id)
            )
            or type(self.goal_depth) is not int
            or self.goal_depth not in (0, 1)
            or type(self.goal_exposed) is not bool
            or type(self.goal_origin) is not str
            or self.goal_origin not in {"owner", "obligation", "third_party", "staffer"}
            or len(parts) != 2
            or any(not re.fullmatch(r"[a-zA-Z0-9_.-]+", part) for part in parts)
            or type(self.goal_args_digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.goal_args_digest) is None
            or (
                self.goal_approved_args_digest is not None
                and (
                    type(self.goal_approved_args_digest) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", self.goal_approved_args_digest) is None
                )
            )
        ):
            raise ValueError("invalid_goal_lineage")

    def columns(self) -> dict[str, Any]:
        """Typed database values in the explicit persisted column order."""
        return {name: getattr(self, name) for name in GOAL_LINEAGE_COLUMNS}


def _goal_fields(row: Any) -> dict[str, Any]:
    values = {}
    for name in GOAL_LINEAGE_COLUMNS:
        try:
            values[name] = row[name]
        except (KeyError, IndexError):
            values[name] = None
    for name in ("goal_id", "invocation_id", "goal_effect_id"):
        values[name] = _parse_optional_uuid(values[name])
    return values


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


def _parse_jsonb_list(value: Any) -> list[Any]:
    """Parse a JSONB array value (may be a raw string from asyncpg without a codec).

    asyncpg can return JSONB columns as raw JSON strings when no custom codec is
    registered.  ``list(string)`` would iterate over characters instead of JSON
    elements, so we must decode the string first.
    """
    if value is None:
        return []
    if isinstance(value, str):
        parsed = json.loads(value)
        return list(parsed) if parsed else []
    return list(value)


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
    evidence: list[EvidenceReference] = field(default_factory=list)
    blast_radius: str | None = None
    reversibility: str | None = None
    # 'prepared' for a proactive draft parked by an insight-scan producer
    # (bu-2jtfw.11); None for every other park path. A prepared action is
    # never pushed to the owner -- it surfaces only through the insight
    # digest's door.
    origin: str | None = None
    # Terminal outcome of this action's approval_push_emissions reservation
    # (delivered/deferred/collapsed/duplicate/failed), or None. Populated only
    # by queries that join approval_push_emissions -- None here means either
    # no push was ever attempted (no delivery plane wired) or the caller's
    # query does not populate it, NOT that the push succeeded. A still-pending
    # action with push_outcome in (None, "failed") means the owner was never
    # actually notified (bu-mda0r); callers must not read that combination as
    # a normal row.
    push_outcome: str | None = None
    goal_id: uuid.UUID | None = None
    invocation_id: uuid.UUID | None = None
    goal_depth: int | None = None
    goal_exposed: bool | None = None
    goal_origin: str | None = None
    goal_effect_id: uuid.UUID | None = None
    goal_capability: str | None = None
    goal_args_digest: str | None = None
    goal_approved_args_digest: str | None = None

    def __post_init__(self) -> None:
        self.goal_lineage()

    def goal_lineage(self) -> GoalCallLineage | None:
        """Refuse partial live lineage; preserve ordinary and explicit legacy rows."""
        values = {name: getattr(self, name) for name in GOAL_LINEAGE_COLUMNS}
        if all(value is None for value in values.values()):
            return None
        if values["goal_origin"] == "legacy" and all(
            value is None for name, value in values.items() if name != "goal_origin"
        ):
            return None
        return GoalCallLineage(**values)

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
            "blast_radius": self.blast_radius,
            "reversibility": self.reversibility,
            "origin": self.origin,
            "push_outcome": self.push_outcome,
        }
        for name in GOAL_LINEAGE_COLUMNS:
            value = getattr(self, name)
            d[name] = str(value) if isinstance(value, uuid.UUID) else value
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
            blast_radius=data.get("blast_radius"),
            reversibility=data.get("reversibility"),
            origin=data.get("origin"),
            push_outcome=data.get("push_outcome"),
            **_goal_fields(data),
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
            evidence=_parse_jsonb_list(_get("evidence")),
            blast_radius=_get("blast_radius"),
            reversibility=_get("reversibility"),
            origin=_get("origin"),
            push_outcome=_get("push_outcome"),
            **_goal_fields(row),
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
