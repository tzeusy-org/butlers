"""Switchboard-owned runtime-attention delivery.

Vision Rule 3 and RFC 0003 make Switchboard the only boundary that may put a
runtime-attention episode in front of the operator.  This package holds the two
halves of that boundary: the fenced-claim repository over
``public.runtime_attention_outbox`` and the delivery worker that drives it.

``SwitchboardModule.on_startup`` constructs and schedules the delivery worker
at daemon startup (see ``roster/switchboard/modules/__init__.py``).
"""

from butlers.tools.switchboard.runtime_attention.outbox import (
    CLAIM_LEASE_SECONDS,
    DELIVERY_LEASE_NAME,
    MAX_TRANSPORT_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
    SERVICE_LEASE_TTL_SECONDS,
    STALE_SENDING_SECONDS,
    TRANSPORT_DEADLINE_SECONDS,
    OutboxEpisode,
    RuntimeAttentionOutbox,
    ServiceLease,
    StaleClaim,
)
from butlers.tools.switchboard.runtime_attention.worker import (
    DeliveryCycle,
    RuntimeAttentionDeliveryWorker,
    build_messenger_transport,
)

__all__ = [
    "CLAIM_LEASE_SECONDS",
    "DELIVERY_LEASE_NAME",
    "MAX_TRANSPORT_ATTEMPTS",
    "RETRY_BACKOFF_SECONDS",
    "SERVICE_LEASE_TTL_SECONDS",
    "STALE_SENDING_SECONDS",
    "TRANSPORT_DEADLINE_SECONDS",
    "DeliveryCycle",
    "OutboxEpisode",
    "RuntimeAttentionDeliveryWorker",
    "RuntimeAttentionOutbox",
    "ServiceLease",
    "StaleClaim",
    "build_messenger_transport",
]
