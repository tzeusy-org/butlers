"""Content-free failure codes for an owner-approved outbound send (bu-dhloi1.184).

An approved send that the provider or transport refuses must reach the owner as a distinct
outcome, never as silence and never carrying message text, a recipient or a provider
description. The code is chosen from a closed vocabulary; the label is fixed prose for it.
The vocabulary mirrors Switchboard's ``transport`` fragment (``error_detail``), which is
already a closed, non-secret description of one send attempt.
"""

from __future__ import annotations

from typing import Any

#: Terminal result status for a send the provider proved it rejected (never delivered).
DELIVERY_REJECTED_STATUS = "delivery_rejected"

#: ``code -> owner-facing label``. Fixed prose: no recipient, body or provider text.
SEND_FAILURE_LABELS: dict[str, str] = {
    "provider_rejected": "The provider rejected the send (for example a blocked or unknown chat)",
    "recipient_unavailable": "No deliverable recipient was available",
    "policy_denied": "Delivery policy refused the send",
    "transport_timeout": "The provider did not answer in time",
    "transport_connection_lost": "The connection to the provider was lost",
    "worker_recovery": "Delivery was interrupted and could not be confirmed",
    "send_failed": "The send failed before the provider confirmed it",
}

GENERIC_SEND_FAILURE = "send_failed"

_WRAPPER_KEYS = ("result", "data", "error", "notify_response")

#: Approved tools whose failure is an outbound send the owner must be told about.
_EGRESS_PREFIXES = (
    "telegram_send",
    "telegram_reply",
    "email_send",
    "email_reply",
    "whatsapp_send",
)


def is_egress_tool(tool_name: str) -> bool:
    """Whether a failed run of this approved tool is an outbound send."""
    return tool_name == "notify" or tool_name.startswith(_EGRESS_PREFIXES)


class SendFailure(RuntimeError):
    """An approved send failed with a known, content-free code."""

    def __init__(self, code: str) -> None:
        self.code = code if code in SEND_FAILURE_LABELS else GENERIC_SEND_FAILURE
        super().__init__(f"send_failed:{self.code}")


def _transport(value: Any, *, _depth: int = 0) -> dict[str, Any] | None:
    if _depth > 8 or not isinstance(value, dict):
        return None
    transport = value.get("transport")
    if isinstance(transport, dict):
        return transport
    for key in _WRAPPER_KEYS:
        found = _transport(value.get(key), _depth=_depth + 1)
        if found is not None:
            return found
    return None


def send_failure_code(value: Any) -> str:
    """The closed failure code a send failure carries; generic when it names none."""
    if isinstance(value, SendFailure):
        return value.code
    transport = _transport(value)
    detail = transport.get("error_detail") if transport is not None else None
    return (
        detail
        if isinstance(detail, str) and detail in SEND_FAILURE_LABELS
        else (GENERIC_SEND_FAILURE)
    )


def provider_rejected(value: Any) -> bool:
    """Whether a result proves the provider rejected the send (terminal, never delivered)."""
    transport = _transport(value)
    return transport is not None and transport.get("outcome") == "rejected"


def delivery_rejected_result(code: str = "provider_rejected") -> dict[str, Any]:
    """What an approved send records when the provider proved it rejected the send."""
    return {
        "status": DELIVERY_REJECTED_STATUS,
        "delivery_confirmed": False,
        "failure_code": code if code in SEND_FAILURE_LABELS else "provider_rejected",
    }


def failure_from_execution_result(result: Any) -> dict[str, str] | None:
    """The ``{code, label}`` of a recorded rejected send, read from its execution result."""
    if not isinstance(result, dict):
        return None
    inner = result.get("result") if isinstance(result.get("result"), dict) else result
    if inner.get("status") != DELIVERY_REJECTED_STATUS:
        return None
    return failure_payload(inner.get("failure_code"))


def failure_payload(code: Any) -> dict[str, str] | None:
    """``{code, label}`` for a known code, else ``None``; unknown text never passes."""
    if not isinstance(code, str) or code not in SEND_FAILURE_LABELS:
        return None
    return {"code": code, "label": SEND_FAILURE_LABELS[code]}


__all__ = [
    "DELIVERY_REJECTED_STATUS",
    "GENERIC_SEND_FAILURE",
    "SEND_FAILURE_LABELS",
    "SendFailure",
    "delivery_rejected_result",
    "failure_from_execution_result",
    "failure_payload",
    "is_egress_tool",
    "provider_rejected",
    "send_failure_code",
]
