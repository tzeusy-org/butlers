"""Delivery-cycle control flow for the runtime-attention worker.

REQ-runtime-attention-outbox-002.

The at-most-once guarantee has two halves. The database half — fenced claims,
terminal states, recovery that never replays — is proven against real Postgres
in ``tests/integration/test_runtime_attention_delivery_worker.py``. This file
covers the half that lives entirely in the worker's own loop and needs no
substrate: what the cycle does when it loses the sole service lease, and what
it does when a terminal transition raises *after* transport already returned an
outcome.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from butlers.tools.switchboard.routing.transport import (
    CONFIRMED,
    TransportOutcome,
    TransportResult,
)
from butlers.tools.switchboard.runtime_attention.outbox import OutboxEpisode, ServiceLease
from butlers.tools.switchboard.runtime_attention.worker import RuntimeAttentionDeliveryWorker

pytestmark = [pytest.mark.unit]


def _episode() -> OutboxEpisode:
    now = datetime.now(UTC)
    return OutboxEpisode(
        id=uuid.uuid4(),
        source="model_breaker",
        source_snapshot={},
        payload={},
        lifecycle_state="sending",
        claim_token=uuid.uuid4(),
        claim_epoch=1,
        delivery_lease_epoch=1,
        claimed_at=now,
        claim_expires_at=now,
    )


class _FakeOutbox:
    """A repository double that records the cycle's calls in order.

    It hands out ``pending`` episodes on demand so the loop is bounded by the
    worker's own decisions rather than by an empty queue.
    """

    def __init__(
        self,
        *,
        available: int,
        renew_results: list[bool] | None = None,
        mark_sent_error: Exception | None = None,
    ) -> None:
        self._available = available
        self._renew_results = renew_results or []
        self._mark_sent_error = mark_sent_error
        self.claims = 0
        self.renewals = 0
        self.released = False
        self.marked_sent = 0

    async def acquire_service_lease(self) -> ServiceLease:
        now = datetime.now(UTC)
        return ServiceLease(token=uuid.uuid4(), epoch=1, holder="worker-a", expires_at=now)

    async def release_service_lease(self, lease: ServiceLease) -> bool:
        self.released = True
        return True

    async def renew_service_lease(self, lease: ServiceLease) -> bool:
        result = (
            self._renew_results[self.renewals] if self.renewals < len(self._renew_results) else True
        )
        self.renewals += 1
        return result

    async def list_recoverable(self, lease: ServiceLease) -> list[Any]:
        return []

    async def claim_next_pending(self, lease: ServiceLease) -> OutboxEpisode | None:
        if self.claims >= self._available:
            return None
        self.claims += 1
        return _episode()

    async def claim_is_current(self, episode: OutboxEpisode) -> bool:
        return True

    async def mark_sent(self, episode: OutboxEpisode) -> bool:
        if self._mark_sent_error is not None:
            raise self._mark_sent_error
        self.marked_sent += 1
        return True


async def _confirmed(episode: OutboxEpisode) -> TransportResult:
    return CONFIRMED


def _worker(repository: _FakeOutbox) -> RuntimeAttentionDeliveryWorker:
    return RuntimeAttentionDeliveryWorker(repository, _confirmed)  # type: ignore[arg-type]


async def test_a_lost_service_lease_stops_the_cycle_before_the_next_claim() -> None:
    """A successor holds the lease; continuing would be a second delivery service."""
    repository = _FakeOutbox(available=5, renew_results=[False])

    cycle = await _worker(repository).run_once()

    # One episode was already in flight when the lease went; the cycle must not
    # reach for another.
    assert repository.claims == 1
    assert cycle.outcomes == (TransportOutcome.CONFIRMED,)
    assert cycle.lease_acquired is True
    assert repository.released is True


async def test_a_held_service_lease_lets_the_cycle_drain_the_queue() -> None:
    """The control case: renewal succeeding must not cut the cycle short."""
    repository = _FakeOutbox(available=3)

    cycle = await _worker(repository).run_once()

    assert repository.claims == 3
    assert len(cycle.outcomes) == 3
    assert repository.renewals == 3


async def test_a_failing_terminal_transition_neither_aborts_the_cycle_nor_reclassifies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Bookkeeping that raises after a confirmed send must not strand the queue.

    Transport has already spoken. Letting the exception escape would abandon
    every remaining episode and turn one bookkeeping fault into a fleet-wide
    delivery stall.
    """
    repository = _FakeOutbox(available=2, mark_sent_error=RuntimeError("sentinel-outbox-detail"))

    with caplog.at_level("WARNING"):
        cycle = await _worker(repository).run_once()

    assert repository.claims == 2, "the cycle must keep going"
    assert cycle.outcomes == (TransportOutcome.CONFIRMED, TransportOutcome.CONFIRMED), (
        "the transport outcome stays truthful even when it could not be recorded"
    )

    text = caplog.text
    assert "RuntimeError" in text, "the failure is reported by exception type"
    assert "sentinel-outbox-detail" not in text, "no exception message reaches the log"
