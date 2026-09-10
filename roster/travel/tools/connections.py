"""Journey connection derivation — verdicts, recompute, and the risk door.

A "connection" is the layover between two adjacent legs of the same trip: the
inbound leg's arrival airport equals the outbound leg's departure airport, and
the gap between them is short enough to be a same-journey transfer rather than
two unrelated segments (e.g. a round-trip's outbound and return, days apart).

``compute_connection_verdict`` is a pure function (no I/O) so the derivation
rules are unit-testable in isolation. ``recompute_trip_connections`` is the
impure driver: it re-derives every connection in a trip, upserts
``travel.connections`` with a ``computed_at`` fence so a stale write can never
overwrite a fresher one, and reacts to a verdict transitioning into or out of
``broken`` by raising/withdrawing the approval-spine door (bu-2jtfw.8 design,
"Non-goals": the door proposes, it never executes a rebooking).

Never raises: every entry point catches and logs so a derivation bug never
blocks booking ingestion or the flight-status poll that call it after every
leg mutation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# A gap longer than this is not a connection -- it's a separate segment of the
# same trip (e.g. the days between a round-trip's outbound and return).
_MAX_CONNECTION_GAP = timedelta(hours=24)

# Minutes of slack above the effective minimum before a connection is
# considered comfortably 'holds' rather than merely 'tight'.
_TIGHT_BUFFER_MINUTES = 30

# How long a connection-risk door stays open before the nightly orphan sweep
# expires it (src/butlers/modules/approvals/operations.py
# sweep_orphaned_prepared_actions).
_DOOR_EXPIRES_DAYS = 3

_INSIGHT_ALERT_PRIORITY = 88
_INSIGHT_ALERT_EXPIRES_DAYS = 3


def compute_connection_verdict(
    *,
    inbound_arrival_at: datetime,
    outbound_departure_at: datetime,
    connecting_airport: str | None,
    inbound_carrier: str | None = None,
    outbound_carrier: str | None = None,
    minimum_connect_minutes: int | None = None,
    interline_buffer_minutes: int = 0,
    inbound_arrival_terminal: str | None = None,
    outbound_departure_terminal: str | None = None,
) -> dict[str, Any]:
    """Derive a connection verdict from two adjacent legs' timing and reference data.

    Returns ``{"verdict", "available_minutes", "evidence"}``. ``verdict`` is
    one of ``holds``, ``tight``, ``broken``, ``unknown``. ``unknown`` means
    ``minimum_connect_minutes`` was not supplied (no reference data on file
    for ``connecting_airport``) -- the caller must never render this as a
    safety claim (bu-2jtfw.8 acceptance check 7).
    """
    available_minutes = int((outbound_departure_at - inbound_arrival_at).total_seconds() // 60)

    interline = (
        bool(inbound_carrier)
        and bool(outbound_carrier)
        and inbound_carrier.strip().lower() != outbound_carrier.strip().lower()
    )

    evidence: dict[str, Any] = {
        "connecting_airport": connecting_airport,
        "interline": interline,
        "inbound_carrier": inbound_carrier,
        "outbound_carrier": outbound_carrier,
    }

    if inbound_arrival_terminal is not None and outbound_departure_terminal is not None:
        evidence["terminal_change"] = inbound_arrival_terminal != outbound_departure_terminal
        evidence["inbound_arrival_terminal"] = inbound_arrival_terminal
        evidence["outbound_departure_terminal"] = outbound_departure_terminal

    if minimum_connect_minutes is None:
        evidence["reason"] = "no_minimum_on_file"
        return {
            "verdict": "unknown",
            "available_minutes": available_minutes,
            "evidence": evidence,
        }

    effective_minimum = minimum_connect_minutes + (interline_buffer_minutes if interline else 0)
    evidence["minimum_minutes"] = effective_minimum

    if available_minutes < effective_minimum:
        verdict = "broken"
    elif available_minutes < effective_minimum + _TIGHT_BUFFER_MINUTES:
        verdict = "tight"
    else:
        verdict = "holds"

    return {
        "verdict": verdict,
        "available_minutes": available_minutes,
        "evidence": evidence,
    }


def _find_connecting_pairs(
    legs: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return adjacent (inbound, outbound) leg pairs that share a connecting airport."""
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for inbound, outbound in zip(legs, legs[1:]):
        inbound_arrival_airport = inbound.get("arrival_airport_station")
        outbound_departure_airport = outbound.get("departure_airport_station")
        if not inbound_arrival_airport or not outbound_departure_airport:
            continue
        if inbound_arrival_airport != outbound_departure_airport:
            continue
        gap = outbound["departure_at"] - inbound["arrival_at"]
        if gap.total_seconds() < 0 or gap > _MAX_CONNECTION_GAP:
            continue
        pairs.append((inbound, outbound))
    return pairs


async def _emit_unknown_minimum_signal(pool: Any, airport_code: str) -> None:
    """Record an honest 'we have no reference data for this airport' signal.

    Reuses the shared expected-signals ledger (``public.expected_signals``,
    ``src/butlers/core/expected_signals.py``) rather than inventing a new
    table: producer='owner' with no observation on file evaluates to
    ``absent`` -- "this static reference input is expected but has never been
    supplied" -- never ``present``, so a consumer can never mistake the gap
    for a verified-safe layover.
    """
    from butlers.core.expected_signals import upsert_expected_signal

    try:
        await upsert_expected_signal(
            pool,
            signal_key=f"travel:airport-minimum-connect:{airport_code}",
            producer="owner",
            expected_cadence=timedelta(days=3650),
            last_observed_at=None,
        )
    except Exception:
        logger.warning(
            "recompute_trip_connections: failed to record expected_signals row for %s",
            airport_code,
            exc_info=True,
        )


async def _raise_connection_door(
    pool: Any,
    *,
    trip_id: str,
    inbound_leg_id: str,
    outbound_leg_id: str,
    derivation: dict[str, Any],
    now: datetime,
) -> None:
    """Raise one alert and park one approval door for a newly-broken connection.

    Idempotent: both the insight candidate and the pending-action door are
    keyed on ``(inbound_leg_id, outbound_leg_id)`` so a repeat recompute of an
    already-broken connection is a no-op, not a re-notify.
    """
    from butlers.modules.approvals.park import park_prepared_action

    airport = derivation["evidence"].get("connecting_airport") or "this airport"
    available = derivation["available_minutes"]
    message = (
        f"Your connection at {airport} now looks broken -- only {available} minute(s) available."
    )
    dedup_key = f"travel:connection-risk:{inbound_leg_id}:{outbound_leg_id}"

    try:
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        result = await propose_insight_candidate(
            pool,
            origin_butler="travel",
            priority=_INSIGHT_ALERT_PRIORITY,
            category="connection-risk",
            dedup_key=f"{dedup_key}:alert",
            message=message,
            expires_at=now + timedelta(days=_INSIGHT_ALERT_EXPIRES_DAYS),
            metadata={
                "trip_id": trip_id,
                "inbound_leg_id": inbound_leg_id,
                "outbound_leg_id": outbound_leg_id,
            },
            now=now,
        )
        if result.get("status") == "error":
            logger.warning(
                "recompute_trip_connections: propose_insight_candidate error: %s",
                result.get("reason", "unknown"),
            )
    except Exception:
        logger.warning(
            "recompute_trip_connections: failed to raise connection alert", exc_info=True
        )

    existing_door = await pool.fetchrow(
        "SELECT id FROM pending_actions WHERE deduplication_key = $1 "
        "AND status IN ('pending', 'approved', 'rejected', 'abandoned')",
        dedup_key,
    )
    if existing_door is not None:
        return

    try:
        await park_prepared_action(
            pool,
            action_id=uuid.uuid4(),
            tool_name="acknowledge_connection_risk",
            tool_args={
                "trip_id": trip_id,
                "inbound_leg_id": inbound_leg_id,
                "outbound_leg_id": outbound_leg_id,
                "verdict": derivation["verdict"],
                "available_minutes": available,
            },
            agent_summary=f"Connection at risk: {message}",
            requested_at=now,
            expires_at=now + timedelta(days=_DOOR_EXPIRES_DAYS),
            why=message,
            deduplication_key=dedup_key,
        )
    except asyncpg.UniqueViolationError:
        # A concurrent recompute tick already parked the door for this pair.
        pass
    except Exception:
        logger.warning("recompute_trip_connections: failed to park connection door", exc_info=True)


async def _withdraw_connection_door(
    pool: Any, *, inbound_leg_id: str, outbound_leg_id: str, now: datetime
) -> None:
    """Withdraw a still-open connection-risk door once the layover recovers."""
    dedup_key = f"travel:connection-risk:{inbound_leg_id}:{outbound_leg_id}"
    try:
        await pool.execute(
            "UPDATE pending_actions SET status = 'rejected', "
            "decided_by = 'system:connection-recovery', decided_at = $2 "
            "WHERE deduplication_key = $1 AND status = 'pending'",
            dedup_key,
            now,
        )
    except Exception:
        logger.warning(
            "recompute_trip_connections: failed to withdraw connection door", exc_info=True
        )


async def recompute_trip_connections(
    pool: Any, trip_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Re-derive every connection verdict for one trip and upsert ``travel.connections``.

    Never raises: a failure anywhere in derivation, persistence, or the
    door/alert side effects is logged and swallowed, returning
    ``{"trip_id", "connections": [], "error": "recompute_failed"}`` rather
    than propagating into the booking-ingestion or flight-status call sites
    that trigger this after every leg mutation.
    """
    effective_now = now or datetime.now(UTC)
    try:
        leg_rows = await pool.fetch(
            "SELECT id, departure_at, arrival_at, departure_airport_station, "
            "arrival_airport_station, carrier, metadata "
            "FROM travel.legs WHERE trip_id = $1::uuid ORDER BY departure_at ASC",
            trip_id,
        )
        legs = [dict(row) for row in leg_rows]
        pairs = _find_connecting_pairs(legs)
        valid_pair_ids = {(pair[0]["id"], pair[1]["id"]) for pair in pairs}

        existing_rows = await pool.fetch(
            "SELECT id, inbound_leg_id, outbound_leg_id "
            "FROM travel.connections WHERE trip_id = $1::uuid",
            trip_id,
        )
        for row in existing_rows:
            if (row["inbound_leg_id"], row["outbound_leg_id"]) not in valid_pair_ids:
                await pool.execute("DELETE FROM travel.connections WHERE id = $1::uuid", row["id"])

        results: list[dict[str, Any]] = []
        for inbound, outbound in pairs:
            connecting_airport = inbound["arrival_airport_station"]
            minimum_row = await pool.fetchrow(
                "SELECT minimum_connect_minutes, interline_buffer_minutes "
                "FROM travel.airport_minimum_connect WHERE airport_code = $1",
                connecting_airport,
            )
            inbound_meta = inbound.get("metadata") or {}
            outbound_meta = outbound.get("metadata") or {}

            derivation = compute_connection_verdict(
                inbound_arrival_at=inbound["arrival_at"],
                outbound_departure_at=outbound["departure_at"],
                connecting_airport=connecting_airport,
                inbound_carrier=inbound.get("carrier"),
                outbound_carrier=outbound.get("carrier"),
                minimum_connect_minutes=(
                    minimum_row["minimum_connect_minutes"] if minimum_row else None
                ),
                interline_buffer_minutes=(
                    minimum_row["interline_buffer_minutes"] if minimum_row else 0
                ),
                inbound_arrival_terminal=inbound_meta.get("arrival_terminal"),
                outbound_departure_terminal=outbound_meta.get("departure_terminal"),
            )

            previous = await pool.fetchrow(
                "SELECT verdict FROM travel.connections "
                "WHERE inbound_leg_id = $1::uuid AND outbound_leg_id = $2::uuid",
                inbound["id"],
                outbound["id"],
            )
            previous_verdict = previous["verdict"] if previous is not None else None

            await pool.execute(
                """
                INSERT INTO travel.connections
                    (trip_id, inbound_leg_id, outbound_leg_id, verdict,
                     available_minutes, evidence, computed_at)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::jsonb, $7)
                ON CONFLICT (inbound_leg_id, outbound_leg_id) DO UPDATE SET
                    verdict = EXCLUDED.verdict,
                    available_minutes = EXCLUDED.available_minutes,
                    evidence = EXCLUDED.evidence,
                    computed_at = EXCLUDED.computed_at,
                    updated_at = now()
                WHERE EXCLUDED.computed_at >= travel.connections.computed_at
                """,
                trip_id,
                inbound["id"],
                outbound["id"],
                derivation["verdict"],
                derivation["available_minutes"],
                derivation["evidence"],
                effective_now,
            )

            if derivation["verdict"] == "unknown":
                await _emit_unknown_minimum_signal(pool, connecting_airport)

            if derivation["verdict"] == "broken" and previous_verdict != "broken":
                await _raise_connection_door(
                    pool,
                    trip_id=trip_id,
                    inbound_leg_id=str(inbound["id"]),
                    outbound_leg_id=str(outbound["id"]),
                    derivation=derivation,
                    now=effective_now,
                )
            elif derivation["verdict"] != "broken" and previous_verdict == "broken":
                await _withdraw_connection_door(
                    pool,
                    inbound_leg_id=str(inbound["id"]),
                    outbound_leg_id=str(outbound["id"]),
                    now=effective_now,
                )

            results.append(
                {
                    "inbound_leg_id": str(inbound["id"]),
                    "outbound_leg_id": str(outbound["id"]),
                    **derivation,
                }
            )

        return {"trip_id": trip_id, "connections": results}
    except Exception:
        logger.warning("recompute_trip_connections failed for trip_id=%s", trip_id, exc_info=True)
        return {"trip_id": trip_id, "connections": [], "error": "recompute_failed"}


__all__ = ["compute_connection_verdict", "recompute_trip_connections"]
