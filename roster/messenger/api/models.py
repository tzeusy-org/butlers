"""Pydantic models for messenger butler API endpoints."""

from __future__ import annotations

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# GET /api/messenger/delivery-stats
# ---------------------------------------------------------------------------


class DeliveryStats(BaseModel):
    """Aggregated delivery statistics over a time window."""

    window_hours: int
    delivered: int
    failed: int
    pending: int
    retried: int
    dead_letter: int
    dispatched_at: str | None = None


# ---------------------------------------------------------------------------
# GET /api/messenger/circuit-status
# ---------------------------------------------------------------------------


class CircuitChannelEntry(BaseModel):
    """Circuit breaker state for a single channel."""

    name: str
    state: str  # 'closed' | 'open' | 'half_open'
    last_state_change: str | None = None
    failure_rate_15m: float | None = None


class CircuitStatus(BaseModel):
    """Circuit breaker state per channel.

    ``source`` is always ``"db_approximation"``: the real in-memory
    CircuitBreaker state is not persisted to the DB, so this endpoint
    derives an approximation from recent delivery outcomes.
    """

    channels: list[CircuitChannelEntry]
    source: str = "db_approximation"


# ---------------------------------------------------------------------------
# GET /api/messenger/queue-depth
# ---------------------------------------------------------------------------


class QueueDepth(BaseModel):
    """Outbound queue depth by channel and priority."""

    total: int
    by_channel: dict[str, int]
    by_priority: dict[str, int]


# ---------------------------------------------------------------------------
# GET /api/messenger/dead-letters
# ---------------------------------------------------------------------------


class DeadLetterEntry(BaseModel):
    """A single dead-letter row."""

    id: str
    channel: str
    recipient_id: str | None = None
    error_message: str | None = None
    attempted_at: str | None = None
    retry_count: int


class DeadLetterSummary(BaseModel):
    """List of recent dead-letter entries."""

    letters: list[DeadLetterEntry]
