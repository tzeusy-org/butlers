"""Shared, liveness-aware truth for claims about expected observations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from butlers.core.liveness import is_liveness_stale

logger = logging.getLogger(__name__)


class ExpectedSignalState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNMEASURABLE = "unmeasurable"


@dataclass(frozen=True, slots=True)
class ExpectedSignalEvaluation:
    signal_key: str
    producer: str
    expected_cadence_seconds: int
    last_observed_at: datetime | None
    state: ExpectedSignalState
    unmeasurable_reason: str | None
    evaluated_at: datetime


def _normalize_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _connector_measurability(
    pool: Any,
    connector_type: str,
    *,
    now: datetime,
) -> tuple[bool, str | None]:
    try:
        rows = await pool.fetch(
            """
            SELECT state, last_heartbeat_at
            FROM public.v_qa_connector_state
            WHERE connector_type = $1
            """,
            connector_type,
        )
    except Exception:  # noqa: BLE001 -- unavailable evidence must fail closed
        logger.warning(
            "Expected signal liveness unavailable for connector %s",
            connector_type,
            exc_info=True,
        )
        return False, "liveness_unavailable"
    if not rows:
        return False, "producer_unregistered"

    for row in rows:
        if row["state"] == "healthy" and not is_liveness_stale(
            row["last_heartbeat_at"], ttl_seconds=300, now=now
        ):
            return True, None

    if any(row["state"] in {"error", "degraded", "paused"} for row in rows):
        return False, "producer_not_healthy"
    return False, "producer_stale_or_offline"


async def evaluate_expected_signal(
    pool: Any,
    *,
    signal_key: str,
    producer: str,
    expected_cadence: timedelta,
    last_observed_at: datetime | None,
    now: datetime | None = None,
) -> ExpectedSignalEvaluation:
    """Evaluate one signal without letting elapsed time outrank instrument health."""
    if not signal_key.strip():
        raise ValueError("signal_key must be non-empty")
    cadence_seconds = int(expected_cadence.total_seconds())
    if cadence_seconds <= 0:
        raise ValueError("expected_cadence must be positive")

    evaluated_at = _normalize_timestamp(now or datetime.now(UTC))
    assert evaluated_at is not None
    observed_at = _normalize_timestamp(last_observed_at)

    measurable = False
    reason: str | None = None
    if producer == "owner":
        measurable = True
    elif producer.startswith("connector:") and producer.removeprefix("connector:"):
        measurable, reason = await _connector_measurability(
            pool,
            producer.removeprefix("connector:"),
            now=evaluated_at,
        )
    else:
        reason = "producer_unknown"

    if not measurable:
        state = ExpectedSignalState.UNMEASURABLE
    elif observed_at is None or observed_at + expected_cadence <= evaluated_at:
        state = ExpectedSignalState.ABSENT
        reason = None
    else:
        state = ExpectedSignalState.PRESENT
        reason = None

    return ExpectedSignalEvaluation(
        signal_key=signal_key,
        producer=producer,
        expected_cadence_seconds=cadence_seconds,
        last_observed_at=observed_at,
        state=state,
        unmeasurable_reason=reason,
        evaluated_at=evaluated_at,
    )


async def upsert_expected_signal(
    pool: Any,
    *,
    signal_key: str,
    producer: str,
    expected_cadence: timedelta,
    last_observed_at: datetime | None,
    now: datetime | None = None,
) -> ExpectedSignalEvaluation:
    """Evaluate and idempotently persist a producer-owned expected signal."""
    evaluation = await evaluate_expected_signal(
        pool,
        signal_key=signal_key,
        producer=producer,
        expected_cadence=expected_cadence,
        last_observed_at=last_observed_at,
        now=now,
    )
    await pool.execute(
        """
        INSERT INTO public.expected_signals (
            signal_key, producer, expected_cadence_seconds, last_observed_at,
            measurability, unmeasurable_reason, evaluated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (signal_key) DO UPDATE
        SET producer = EXCLUDED.producer,
            expected_cadence_seconds = EXCLUDED.expected_cadence_seconds,
            last_observed_at = EXCLUDED.last_observed_at,
            measurability = EXCLUDED.measurability,
            unmeasurable_reason = EXCLUDED.unmeasurable_reason,
            evaluated_at = EXCLUDED.evaluated_at,
            updated_at = now()
        """,
        evaluation.signal_key,
        evaluation.producer,
        evaluation.expected_cadence_seconds,
        evaluation.last_observed_at,
        evaluation.state.value,
        evaluation.unmeasurable_reason,
        evaluation.evaluated_at,
    )
    return evaluation


def measurement_producer(sources: list[str | None]) -> str:
    """Choose the instrument whose liveness governs a measurement cadence.

    Connector provenance wins over manual observations because a dead automatic
    instrument can otherwise be misreported as owner behavior. Purely manual
    histories remain measurable as owner-entered signals. Unknown provenance is
    deliberately unmeasurable.
    """
    connector_vocabulary = {"google_health", "home_assistant"}
    manual_vocabulary = {"owner_log", "manual"}
    recognized_vocabulary = connector_vocabulary | manual_vocabulary
    normalized = [source.strip() for source in sources if isinstance(source, str)]
    if len(normalized) != len(sources):
        return "unknown"
    if any(not source or source not in recognized_vocabulary for source in normalized):
        return "unknown"

    connector_sources = {source for source in normalized if source in connector_vocabulary}
    if len(connector_sources) == 1:
        return f"connector:{next(iter(connector_sources))}"
    if len(connector_sources) > 1:
        return "unknown"
    if normalized and all(source in manual_vocabulary for source in normalized):
        return "owner"
    return "unknown"


__all__ = [
    "ExpectedSignalEvaluation",
    "ExpectedSignalState",
    "evaluate_expected_signal",
    "measurement_producer",
    "upsert_expected_signal",
]
