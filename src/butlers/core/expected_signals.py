"""Shared, liveness-aware truth for claims about expected observations."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from butlers.core.liveness import is_liveness_stale

logger = logging.getLogger(__name__)

#: Verbatim wording the blind-spot preamble uses when the declared-signal
#: query itself fails. Deliberately named so a session is told what it
#: cannot see rather than the layer silently vanishing (see
#: :func:`evaluate_declared_signals` / :func:`format_blind_spot_preamble`).
BLIND_SPOT_QUERY_FAILED_TEXT = "source health could not be evaluated"


class ExpectedSignalState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNMEASURABLE = "unmeasurable"


@dataclass(frozen=True, slots=True)
class ExpectedSignalEvaluation:
    signal_key: str
    producer: str
    producer_endpoint_identity: str | None
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
    endpoint_identity: str,
    *,
    now: datetime,
) -> tuple[bool, str | None]:
    try:
        rows = await pool.fetch(
            """
            SELECT state, last_heartbeat_at
            FROM public.v_qa_connector_state
            WHERE connector_type = $1
              AND endpoint_identity = $2
            """,
            connector_type,
            endpoint_identity,
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
    producer_endpoint_identity: str | None = None,
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
    endpoint_identity = (
        producer_endpoint_identity.strip()
        if isinstance(producer_endpoint_identity, str) and producer_endpoint_identity.strip()
        else None
    )
    if producer == "owner":
        if endpoint_identity is None:
            measurable = True
        else:
            reason = "owner_endpoint_forbidden"
    elif producer.startswith("connector:") and producer.removeprefix("connector:"):
        if endpoint_identity is None:
            reason = "producer_endpoint_missing"
        else:
            measurable, reason = await _connector_measurability(
                pool,
                producer.removeprefix("connector:"),
                endpoint_identity,
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
        producer_endpoint_identity=endpoint_identity,
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
    producer_endpoint_identity: str | None = None,
    expected_cadence: timedelta,
    last_observed_at: datetime | None,
    now: datetime | None = None,
) -> ExpectedSignalEvaluation:
    """Evaluate and idempotently persist a producer-owned expected signal."""
    evaluation = await evaluate_expected_signal(
        pool,
        signal_key=signal_key,
        producer=producer,
        producer_endpoint_identity=producer_endpoint_identity,
        expected_cadence=expected_cadence,
        last_observed_at=last_observed_at,
        now=now,
    )
    await pool.execute(
        """
        INSERT INTO public.expected_signals (
            signal_key, producer, producer_endpoint_identity,
            expected_cadence_seconds, last_observed_at,
            measurability, unmeasurable_reason, evaluated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (signal_key) DO UPDATE
        SET producer = EXCLUDED.producer,
            producer_endpoint_identity = EXCLUDED.producer_endpoint_identity,
            expected_cadence_seconds = EXCLUDED.expected_cadence_seconds,
            last_observed_at = EXCLUDED.last_observed_at,
            measurability = EXCLUDED.measurability,
            unmeasurable_reason = EXCLUDED.unmeasurable_reason,
            evaluated_at = EXCLUDED.evaluated_at,
            updated_at = now()
        """,
        evaluation.signal_key,
        evaluation.producer,
        evaluation.producer_endpoint_identity,
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


def measurement_producer_identity(
    sources: list[tuple[str | None, str | None]],
) -> tuple[str, str | None]:
    """Resolve one exact Health producer identity without guessing legacy endpoints."""
    if not sources:
        return "unknown", None

    normalized: list[tuple[str, str | None]] = []
    for raw_source, raw_endpoint in sources:
        if not isinstance(raw_source, str) or not raw_source.strip():
            return "unknown", None
        source = raw_source.strip()
        endpoint = raw_endpoint.strip() if isinstance(raw_endpoint, str) else None
        normalized.append((source, endpoint or None))

    producer = measurement_producer([source for source, _endpoint in normalized])
    if producer == "owner":
        if any(endpoint is not None for _source, endpoint in normalized):
            return "unknown", None
        return "owner", None
    if not producer.startswith("connector:"):
        return "unknown", None

    endpoints = {endpoint for _source, endpoint in normalized}
    if None in endpoints or len(endpoints) != 1:
        return "unknown", None
    return producer, next(iter(endpoints))


@dataclass(frozen=True, slots=True)
class BlindSpotSnapshot:
    """Result of evaluating one butler's declared expected signals at spawn time.

    ``evaluated_at`` is the evaluator's clock (not any individual signal's
    ``last_observed_at``) so the preamble can name the instant the claim was
    made. ``signals`` holds only the non-PRESENT evaluations — a butler with
    every declared signal PRESENT gets an empty tuple, which is what makes the
    happy-path preamble byte-identical to today's (see
    :func:`format_blind_spot_preamble`). ``query_failed`` distinguishes "we
    checked and everything is fine" from "we could not check" -- the two must
    never look the same, or a query failure would silently present as an
    all-clear.
    """

    evaluated_at: datetime
    signals: tuple[ExpectedSignalEvaluation, ...]
    query_failed: bool = False


async def evaluate_declared_signals(
    pool: Any,
    *,
    signal_key_like_patterns: Sequence[str],
    now: datetime | None = None,
) -> BlindSpotSnapshot:
    """Evaluate every declared signal a butler depends on, fresh, at ``now``.

    ``signal_key_like_patterns`` are SQL ``LIKE`` patterns (e.g.
    ``"health:measurement-gap:%"``) naming the signal namespaces this butler
    has declared a dependency on -- see
    :mod:`butlers.core.blind_spot_declarations`. An empty sequence means the
    butler has declared no dependencies eligible for this preamble; that is
    not a failure and returns an empty, non-failed snapshot.

    Each matching row is re-evaluated against ``now`` via
    :func:`evaluate_expected_signal` rather than trusting its stored
    ``measurability`` -- a row can be stale (last written well before its
    cadence lapsed) without ever being re-touched, and staleness must be
    judged at the spawn clock, not at the last write.

    Unlike every other fetch helper in this package, this function is
    deliberately **fail-closed**: a query error is caught and reported via
    ``query_failed=True`` instead of degrading to an empty (falsely
    reassuring) snapshot. Silently omitting this layer on error would be
    indistinguishable from "every declared signal is present".
    """
    evaluated_at = _normalize_timestamp(now or datetime.now(UTC))
    assert evaluated_at is not None

    if not signal_key_like_patterns:
        return BlindSpotSnapshot(evaluated_at=evaluated_at, signals=(), query_failed=False)

    try:
        rows = await pool.fetch(
            """
            SELECT signal_key, producer, producer_endpoint_identity,
                   expected_cadence_seconds, last_observed_at
            FROM public.expected_signals
            WHERE signal_key LIKE ANY($1::text[])
            ORDER BY signal_key
            """,
            list(signal_key_like_patterns),
        )
    except Exception:  # noqa: BLE001 -- a failed check must never look like a clean one
        logger.warning(
            "Blind-spot declared-signal query failed for patterns=%s",
            signal_key_like_patterns,
            exc_info=True,
        )
        return BlindSpotSnapshot(evaluated_at=evaluated_at, signals=(), query_failed=True)

    evaluations: list[ExpectedSignalEvaluation] = []
    for row in rows:
        evaluation = await evaluate_expected_signal(
            pool,
            signal_key=row["signal_key"],
            producer=row["producer"],
            producer_endpoint_identity=row["producer_endpoint_identity"],
            expected_cadence=timedelta(seconds=row["expected_cadence_seconds"]),
            last_observed_at=row["last_observed_at"],
            now=evaluated_at,
        )
        if evaluation.state is not ExpectedSignalState.PRESENT:
            evaluations.append(evaluation)

    return BlindSpotSnapshot(
        evaluated_at=evaluated_at,
        signals=tuple(evaluations),
        query_failed=False,
    )


def format_blind_spot_preamble(snapshot: BlindSpotSnapshot) -> str | None:
    """Render a :class:`BlindSpotSnapshot` into a system-prompt block, or ``None``.

    Returns ``None`` (no layer at all) exactly when there is nothing to say:
    the query succeeded and every declared signal is PRESENT. This is what
    keeps the composed system prompt byte-identical to today's whenever
    nothing is actually blind. Any other outcome returns a typed block naming
    the affected signal(s), their producer, ``last_observed_at``, and the
    evaluator's own clock -- or, on a query failure, the fixed
    :data:`BLIND_SPOT_QUERY_FAILED_TEXT` wording rather than silence.
    """
    if snapshot.query_failed:
        return (
            "## Source Health\n\n"
            f"{BLIND_SPOT_QUERY_FAILED_TEXT.capitalize()} as of "
            f"{snapshot.evaluated_at.isoformat()}. Treat every claim that would "
            "depend on a declared signal as unconfirmed until this can be "
            "re-checked."
        )

    if not snapshot.signals:
        return None

    lines = [
        "## Source Health — Declared Blind Spots",
        "",
        f"Evaluated at {snapshot.evaluated_at.isoformat()} (evaluator clock). "
        "The following declared signals are not confirmed current. State any "
        "claim that depends on them as unmeasured, not as fact:",
        "",
    ]
    for signal in snapshot.signals:
        last_observed = (
            signal.last_observed_at.isoformat() if signal.last_observed_at is not None else "never"
        )
        reason = f"; reason: {signal.unmeasurable_reason}" if signal.unmeasurable_reason else ""
        lines.append(
            f"- signal={signal.signal_key} producer={signal.producer} "
            f"last_observed_at={last_observed} state={signal.state.value}{reason}"
        )
    return "\n".join(lines)


__all__ = [
    "BLIND_SPOT_QUERY_FAILED_TEXT",
    "BlindSpotSnapshot",
    "ExpectedSignalEvaluation",
    "ExpectedSignalState",
    "evaluate_declared_signals",
    "evaluate_expected_signal",
    "format_blind_spot_preamble",
    "measurement_producer",
    "measurement_producer_identity",
    "upsert_expected_signal",
]
