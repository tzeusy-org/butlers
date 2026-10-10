"""Bounded review correlation from a committed Messenger recipient gate.

This reference locates a dossier; it grants no approval or execution authority.
Only the routed Messenger refusal and the dedicated notification writer use it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ApprovalReview:
    action_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, UUID):
            raise ValueError("Review reference requires a typed action UUID")

    def as_dict(self) -> dict[str, str]:
        return {"action_id": str(self.action_id), "butler": "messenger"}

    @classmethod
    def parse(cls, value: Any) -> ApprovalReview | None:
        if not isinstance(value, dict) or set(value) != {"action_id", "butler"}:
            return None
        raw = value["action_id"]
        if value["butler"] != "messenger" or not isinstance(raw, str) or len(raw) != 36:
            return None
        try:
            parsed = UUID(raw)
        except ValueError:
            return None
        return cls(parsed) if str(parsed) == raw else None


class ParkedApprovalRefusal(ValueError):
    """A recipient refusal issued only after the owning gate committed a park."""

    def __init__(self, message: str, *, decision: Any, butler: str) -> None:
        super().__init__(message)
        self.approval_review = (
            ApprovalReview(decision.action_id)
            if butler == "messenger"
            and decision.allowed is False
            and decision.reason == "parked"
            and isinstance(decision.action_id, UUID)
            else None
        )


def review_from_notify_refusal(
    route_response: Any, *, request_id: str, channel: str
) -> ApprovalReview | None:
    """Admit only the same request/channel typed response from routed Messenger.

    The caller routes exclusively to the registry-resolved Messenger target.
    Metadata and human-readable errors are never inspected for a reference.
    """
    if not isinstance(route_response, dict):
        return None
    if route_response.get("schema_version") != "route_response.v1":
        return None
    if route_response.get("status") != "error":
        return None
    context = route_response.get("request_context")
    if not isinstance(context, dict) or context.get("request_id") != request_id:
        return None
    result = route_response.get("result")
    response = result.get("notify_response") if isinstance(result, dict) else None
    if not isinstance(response, dict) or response.get("schema_version") != "notify_response.v1":
        return None
    if response.get("status") != "error":
        return None
    route_error = route_response.get("error")
    notify_error = response.get("error")
    if (
        not isinstance(route_error, dict)
        or route_error.get("class") != "validation_error"
        or not isinstance(notify_error, dict)
        or notify_error.get("class") != "validation_error"
    ):
        return None
    context = response.get("request_context")
    delivery = response.get("delivery")
    if not isinstance(context, dict) or context.get("request_id") != request_id:
        return None
    if not isinstance(delivery, dict) or delivery.get("channel") != channel:
        return None
    return ApprovalReview.parse(response.get("approval_review"))
