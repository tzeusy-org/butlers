"""Publish a reduction through ``perception_write``'s generation compare-and-set.

The writer needs no prior read. It writes with generation 0 on first contact; a
mismatch returns the current generation, and the single retry uses it. Each
attempt has its own event key, so a replay of the *same* attempt is idempotent
while a retry at a new generation is a new event. Storage faults are reported
as a closed reason, never as exception text.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from butlers.capability_health.model import MAX_SOURCE_AGE, Reduction
from butlers.core.capability_dependencies import dependency_evidence_ref, dependency_subject
from butlers.perception.model import EventRequest

SOURCE = "health.dependencies"
EVIDENCE_SOURCE = "capability_health"
#: The profile's freshness maximum is 900s; republishing every 300s keeps a healthy
#: slot fresh through two missed ticks before it reads as stale (and so unknown).
HEARTBEAT = timedelta(seconds=300)
FRESHNESS = MAX_SOURCE_AGE
_LABELS = {"version": 1, "label_instr": ["world"], "label_data": ["world"], "label_sens": []}


class HealthWriter(Protocol):
    async def write(self, request: EventRequest) -> Any: ...


@dataclass(frozen=True)
class PublishState:
    """What the publisher last put in the slot, kept by the caller between ticks."""

    state: str | None
    cause: str | None
    published_at: datetime


@dataclass(frozen=True)
class PublishResult:
    published: bool
    generation: int | None = None
    reason: str | None = None


def should_publish(last: PublishState | None, reduction: Reduction, now: datetime) -> bool:
    """Write on first contact, on a change of state or cause, and on the heartbeat."""
    if last is None:
        return True
    if (last.state, last.cause) != (reduction.state, reduction.cause):
        return True
    return now.astimezone(UTC) - last.published_at >= HEARTBEAT


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _event_key(reduction: Reduction, now: datetime, generation: int) -> str:
    digest = hashlib.sha256(
        json.dumps(
            [
                reduction.state,
                reduction.cause,
                reduction.observed_at and _iso(reduction.observed_at),
                _iso(now),
                generation,
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:40]
    return f"{reduction.dependency}:{generation}:{digest}"


def _request(reduction: Reduction, now: datetime, generation: int) -> EventRequest:
    now = now.astimezone(UTC)
    measurable = not reduction.unmeasurable
    return EventRequest(
        {
            "version": 1,
            "kind": "system.dep",
            "subject": str(dependency_subject(reduction.dependency)),
            "source": SOURCE,
            "event_key": _event_key(reduction, now, generation),
            "expected_generation": generation,
            "state": "known" if measurable else "unmeasurable",
            "observed_at": _iso(now),
            "effective_span": {"start": _iso(now), "end": _iso(now + FRESHNESS)},
            "typed_value_or_ref": reduction.value() if measurable else None,
            "evidence_refs": [
                {
                    "ref": str(dependency_evidence_ref(reduction.dependency)),
                    "source": EVIDENCE_SOURCE,
                    "expires_at": _iso(now + FRESHNESS),
                }
            ],
            "labels": _LABELS,
            "at": _iso(now),
            "confirmed_at": _iso(now),
            "retracts_event_id": None,
        }
    )


class HealthPublisher:
    """Holds a perception writer connection the caller has already put in role."""

    def __init__(self, store: HealthWriter) -> None:
        self.store = store

    async def publish(self, reduction: Reduction, now: datetime) -> PublishResult:
        generation = 0
        for attempt in range(2):
            try:
                outcome = await self.store.write(_request(reduction, now, generation))
            except Exception:
                return PublishResult(False, reason="unavailable")
            status = outcome.get("status") if isinstance(outcome, dict) else None
            if status == "committed":
                return PublishResult(True, generation=outcome.get("generation"))
            current = outcome.get("generation") if isinstance(outcome, dict) else None
            if status != "conflict" or type(current) is not int or attempt == 1:
                return PublishResult(
                    False, reason="conflict" if status == "conflict" else "refused"
                )
            generation = current
        return PublishResult(False, reason="conflict")  # pragma: no cover
