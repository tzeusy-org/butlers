"""Domain-event bus Pydantic models — dashboard subscription visibility.

bu-317s5 (domain-event bus slice 2). See ``src/butlers/core/domain_events.py``
for the reader/writer these model, and ``alembic/versions/core/
core_186_domain_events.py`` for ``public.butler_subscriptions``/``public.
domain_event_deliveries``.
"""

from __future__ import annotations

from pydantic import BaseModel


class ReactionSummary(BaseModel):
    """The latest reaction receipt for one wake, as shown beside a delivery."""

    status: str
    session_id: str | None = None
    note: str | None = None
    recorded_at: str


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
    attempt_count: int = 0
    delivered_at: str | None = None
    created_at: str
    updated_at: str
    event_type: str
    source_butler: str
    occurred_at: str
    reaction: ReactionSummary | None = None
    """The subscriber's own outcome, or ``None`` when no receipt exists yet.

    Deliberately a separate field from ``status``: ``status`` is transport
    (did the wake get scheduled), ``reaction`` is domain (did the subscriber
    do anything). A delivered wake with ``reaction=None`` means exactly that
    -- nobody has reported an outcome -- and must never be rendered as
    success.
    """


class ReactionEntry(BaseModel):
    """One ``public.domain_event_reactions`` row -- a step in the trace."""

    id: str
    event_id: str
    subscriber_butler: str
    status: str
    session_id: str | None = None
    task_name: str | None = None
    note: str | None = None
    evidence: list[dict[str, str]] = []
    recorded_at: str


class ContractEntry(BaseModel):
    """One ``public.domain_event_contracts`` row -- a publisher's declaration.

    A projection of ``roster/<butler>/domain_events.toml``, materialized at
    that butler's startup. Git is the source of truth; a namespace missing
    here means its butler has not booted since declaring, not that it may
    publish anything.
    """

    event_type: str
    publisher: str
    schema_version: int
    summary: str
    retention_policy: str
    reaction_expectation: str
    reaction_contract: str
    permitted_subscribers: list[str] = []
    required_fields: list[str] = []
    optional_fields: list[str] = []
    materialized_at: str
