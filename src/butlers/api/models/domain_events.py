"""Domain-event bus Pydantic models — dashboard subscription visibility.

bu-317s5 (domain-event bus slice 2). See ``src/butlers/core/domain_events.py``
for the reader/writer these model, and ``alembic/versions/core/
core_186_domain_events.py`` for ``public.butler_subscriptions``/``public.
domain_event_deliveries``.
"""

from __future__ import annotations

from pydantic import BaseModel


class SubscriptionEntry(BaseModel):
    """One row of ``public.butler_subscriptions`` — a standing
    ``(subscriber_butler, event_type)`` registration."""

    id: str
    subscriber_butler: str
    event_type: str
    active: bool
    created_at: str
    updated_at: str


class DeliveryEntry(BaseModel):
    """One ``public.domain_event_deliveries`` row joined with its event.

    ``event_type``/``source_butler``/``occurred_at`` come from the joined
    ``public.domain_events`` row (see
    ``butlers.core.domain_events.list_recent_deliveries``) so a caller does
    not need to already know the event id to make sense of a delivery.
    """

    id: str
    event_id: str
    subscriber_butler: str
    status: str
    task_id: str | None = None
    task_name: str | None = None
    error_message: str | None = None
    delivered_at: str | None = None
    created_at: str
    updated_at: str
    event_type: str
    source_butler: str
    occurred_at: str
