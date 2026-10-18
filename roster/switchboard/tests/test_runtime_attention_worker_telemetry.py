"""Runtime-attention worker telemetry is OpenTelemetry with closed attribute values.

bu-773cjf: the worker's counters were prometheus_client instruments in a daemon that
exposes no /metrics endpoint, so nothing ever scraped them. They are OTel counters now
(RFC 0005). These tests pin the emitted attributes and the cycle-control signals.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from butlers.tools.switchboard.routing import transport
from butlers.tools.switchboard.runtime_attention import worker as worker_module
from butlers.tools.switchboard.runtime_attention.outbox import OutboxEpisode, ServiceLease
from butlers.tools.switchboard.runtime_attention.worker import RuntimeAttentionDeliveryWorker

pytestmark = [pytest.mark.unit]


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[float, dict[str, str]]] = []

    def add(self, amount: float, attributes: dict[str, str] | None = None) -> None:
        self.calls.append((amount, dict(attributes or {})))


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


class _Outbox:
    def __init__(
        self,
        *,
        available: int = 1,
        renew: list[bool] | None = None,
        mark_error: Exception | None = None,
    ) -> None:
        self._available = available
        self._renew = renew or []
        self._mark_error = mark_error
        self.claims = 0
        self.renewals = 0

    async def acquire_service_lease(self) -> ServiceLease:
        return ServiceLease(
            token=uuid.uuid4(), epoch=1, holder="worker-a", expires_at=datetime.now(UTC)
        )

    async def release_service_lease(self, lease: ServiceLease) -> bool:
        return True

    async def renew_service_lease(self, lease: ServiceLease) -> bool:
        ok = self._renew[self.renewals] if self.renewals < len(self._renew) else True
        self.renewals += 1
        return ok

    async def list_recoverable(self, lease: ServiceLease) -> list[Any]:
        return []

    async def claim_next_pending(self, lease: ServiceLease) -> OutboxEpisode | None:
        if self.claims >= self._available:
            return None
        self.claims += 1
        return _episode()

    async def claim_is_current(self, episode: OutboxEpisode) -> bool:
        return True

    async def mark_sent(self, episode: OutboxEpisode, **_: Any) -> bool:
        if self._mark_error is not None:
            raise self._mark_error
        return True


@pytest.fixture
def counters(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Recorder]:
    recorders = {
        name: _Recorder()
        for name in (
            "_delivery_total",
            "_recovered_total",
            "_fenced_total",
            "_lease_lost_total",
            "_record_failed_total",
        )
    }
    for name, recorder in recorders.items():
        monkeypatch.setattr(worker_module, name, recorder)
    return recorders


def _worker(outbox: _Outbox, result: transport.TransportResult) -> RuntimeAttentionDeliveryWorker:
    async def send(episode: OutboxEpisode) -> transport.TransportResult:
        return result

    return RuntimeAttentionDeliveryWorker(outbox, send)  # type: ignore[arg-type]


async def test_confirmed_delivery_records_outcome_with_no_error_class(
    counters: dict[str, _Recorder],
) -> None:
    await _worker(_Outbox(), transport.CONFIRMED).run_once()

    assert counters["_delivery_total"].calls == [
        (1, {"outcome": "confirmed", "error_class": "none"})
    ]


async def test_rejected_delivery_records_the_closed_error_class(
    counters: dict[str, _Recorder],
) -> None:
    await _worker(_Outbox(), transport.PROVIDER_REJECTED).run_once()

    assert counters["_delivery_total"].calls == [
        (1, {"outcome": "rejected", "error_class": "transport_rejected"})
    ]


async def test_delivery_attributes_come_only_from_the_closed_vocabulary(
    counters: dict[str, _Recorder],
) -> None:
    allowed_outcomes = {str(o) for o in transport.TransportOutcome}
    allowed_classes = {str(c) for c in transport.TransportErrorClass} | {"none"}
    for result in (
        transport.CONFIRMED,
        transport.RECIPIENT_UNAVAILABLE,
        transport.POLICY_DENIED,
        transport.PROVIDER_REJECTED,
        transport.TRANSPORT_TIMEOUT,
    ):
        counters["_delivery_total"].calls.clear()
        await _worker(_Outbox(), result).run_once()
        for _, attributes in counters["_delivery_total"].calls:
            assert set(attributes) == {"outcome", "error_class"}
            assert attributes["outcome"] in allowed_outcomes
            assert attributes["error_class"] in allowed_classes


async def test_a_lost_service_lease_is_counted(counters: dict[str, _Recorder]) -> None:
    await _worker(_Outbox(available=5, renew=[False]), transport.CONFIRMED).run_once()

    assert counters["_lease_lost_total"].calls == [(1, {})]


async def test_a_failed_terminal_record_is_counted(counters: dict[str, _Recorder]) -> None:
    outbox = _Outbox(mark_error=RuntimeError("db down"))

    await _worker(outbox, transport.CONFIRMED).run_once()

    assert counters["_record_failed_total"].calls == [(1, {})]
