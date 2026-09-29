"""Safe owner-facing vocabulary for runtime-attention delivery evidence.

The outbox persists only a fixed ``(delivery_error_class, delivery_error_detail)``
pair set (``ck_runtime_attention_outbox_delivery_evidence``); this module maps
those pairs, and a linked condition's episode, to fixed display copy. Nothing
here echoes a recipient, provider response, or raw error.

Condition attention status (REQ-butler-control-plane-liveness-007):

``pending``             appended, not yet claimed, and a delivery worker is live
``worker_unavailable``  appended but no live delivery lease: nothing is sending it
``sending``             claimed; the outcome is not yet known (never "delivered")
``sent``                transport confirmed
``failed``              definitively not delivered (pre-transport or rejected)
``uncertain``           may or may not have arrived; never automatically resent
``unavailable``         the attention projection itself could not be read
"""

from __future__ import annotations

from typing import Any, Final

ATTENTION_REASON_COPY: Final[dict[tuple[str, str], str]] = {
    ("pre_transport", "recipient_unavailable"): "Recipient unavailable before delivery",
    ("pre_transport", "policy_denied"): "Delivery policy denied the alert",
    ("transport_rejected", "provider_rejected"): "Transport rejected the alert",
    ("transport_uncertain", "transport_timeout"): "Delivery timed out; outcome is uncertain",
    (
        "transport_uncertain",
        "transport_connection_lost",
    ): "Delivery connection was lost; outcome is uncertain",
    ("transport_uncertain", "worker_recovery"): "A dead delivery claim was fenced as uncertain",
}

CONDITION_ATTENTION_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "worker_unavailable", "sending", "sent", "failed", "uncertain", "unavailable"}
)


def safe_reason(error_class: str | None, error_detail: str | None) -> str | None:
    if error_class is None or error_detail is None:
        return None
    return ATTENTION_REASON_COPY.get((error_class, error_detail))


def condition_attention_status(row: Any) -> str:
    """Derive the owner-facing status from one ``observe_runtime_attention_conditions`` row."""
    state = row["lifecycle_state"]
    if state == "pending" and not row["delivery_worker_live"]:
        return "worker_unavailable"
    return state
