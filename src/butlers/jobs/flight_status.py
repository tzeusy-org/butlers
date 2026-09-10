"""Deterministic flight-status poll job for booked flight legs.

Polls AviationStack (https://aviationstack.com) for the flight numbers Travel
already extracts from booking-confirmation emails (``travel.legs.metadata->>
'flight_number'``) and notifies via the insight broker when the schedule
delta crosses a threshold, or the flight is cancelled/diverted.

This is a **zero-LLM, deterministic** job (RFC 0009-style producer, see
``butlers.jobs.context_producers``), mirroring ``butlers.jobs.atmosphere``'s
shape: no external "message" to classify, just a status poll to keep warm.

Degraded-mode honesty (CLAUDE.md "Degraded-Mode Response Envelope"):

- No ``AVIATIONSTACK_API_KEY`` provisioned in ``butler_secrets`` ->
  ``configured=false``. The job SHALL NOT attempt a fetch and SHALL NOT
  report this as an error -- see "Owner setup" below.
- Key configured but every poll fails (rate limit, outage, bad key) ->
  ``flight_status_feed_status.last_error`` is set (sanitized -- the
  AviationStack ``access_key`` query param is never logged or stored) and
  ``consecutive_failures`` increments. The job never raises.
- Key configured but there are zero selectable legs to poll (nothing booked
  in the poll window) -> a benign, not a failed, pass:
  ``flight_status_feed_status.last_error`` still reports
  ``"no selectable legs"`` as informational text (so a dashboard can tell
  "verified nothing to check" apart from "never polled"), but
  ``consecutive_failures`` resets to 0 exactly as a genuine success would --
  it is never counted toward outage detection.
- A single flight's poll failing does not abort the sweep -- the remaining
  legs are still checked, matching the atmosphere job's per-cycle resilience.

Owner setup: store an AviationStack API key (free tier: 100 req/month) via
the credential store --
``await CredentialStore(pool).store("AVIATIONSTACK_API_KEY", "<key>",
category="flight_status")`` -- or the dashboard Secrets panel once wired.
Until then the feed reports ``configured=false`` and never polls.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx

from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

_AVIATIONSTACK_URL = "https://api.aviationstack.com/v1/flights"
_API_KEY_SECRET = "AVIATIONSTACK_API_KEY"

_REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=10.0)

# Only poll flights departing within this window -- polling a flight booked
# months out wastes the free-tier request budget on a schedule that has not
# stabilized yet.
_POLL_WINDOW_HOURS = 72

# A schedule delta at or above this many minutes (or a cancelled/diverted
# status) is considered notify-worthy.
_DELAY_THRESHOLD_MINUTES = 30

_NOTIFY_STATUSES = frozenset({"cancelled", "diverted"})


class FlightStatusFetchError(RuntimeError):
    """Raised internally when the upstream AviationStack request fails."""


def _sanitize_error_message(exc: httpx.HTTPError) -> str:
    """Build a sanitized error message without leaking the API key.

    ``HTTPStatusError`` embeds the full request URL including the
    ``access_key`` query param. Mirrors ``atmosphere._sanitize_error_message``.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        reason = getattr(exc.response, "reason_phrase", "")
        status_part = f"HTTP {exc.response.status_code}"
        if reason:
            status_part += f" {reason}"
        return f"aviationstack request failed: {status_part}"
    return f"aviationstack request failed: {type(exc).__name__}"


async def _fetch_flight_status(
    client: httpx.AsyncClient, *, flight_iata: str, api_key: str
) -> dict[str, Any]:
    """Fetch current status for one flight number from AviationStack.

    Raises :class:`FlightStatusFetchError` on any transport/HTTP-status
    failure; callers are responsible for recording the degraded state.
    """
    try:
        resp = await client.get(
            _AVIATIONSTACK_URL,
            params={"access_key": api_key, "flight_iata": flight_iata},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise FlightStatusFetchError(_sanitize_error_message(exc)) from exc

    return resp.json()


def parse_flight_status(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Parse an AviationStack ``/v1/flights`` response into a status summary.

    Returns ``None`` when the response has no matching flight data (e.g. the
    flight number is not recognized, or AviationStack has no data for it
    yet). Otherwise returns:

    - ``flight_status``: AviationStack's status string (``scheduled``,
      ``active``, ``landed``, ``cancelled``, ``incident``, ``diverted``).
    - ``delay_minutes``: departure delay in minutes, or ``None`` if not
      reported (e.g. flight has not yet been assigned a delay estimate).
    - ``scheduled_departure`` / ``estimated_departure``: ISO timestamps, or
      ``None``.
    - ``notify_worthy``: ``True`` when the status is cancelled/diverted, or
      the delay meets/exceeds :data:`_DELAY_THRESHOLD_MINUTES`.
    """
    data = raw.get("data")
    if not data:
        return None
    flight = data[0]
    departure = flight.get("departure") or {}

    status = flight.get("flight_status")
    delay_minutes = departure.get("delay")
    if delay_minutes is not None:
        try:
            delay_minutes = int(delay_minutes)
        except (TypeError, ValueError):
            delay_minutes = None

    notify_worthy = status in _NOTIFY_STATUSES or (
        delay_minutes is not None and delay_minutes >= _DELAY_THRESHOLD_MINUTES
    )

    return {
        "flight_status": status,
        "delay_minutes": delay_minutes,
        "scheduled_departure": departure.get("scheduled"),
        "estimated_departure": departure.get("estimated"),
        "notify_worthy": notify_worthy,
    }


async def _resolve_api_key(pool: asyncpg.Pool) -> str | None:
    store = CredentialStore(pool)
    return await store.resolve(_API_KEY_SECRET)


async def _mark_not_configured(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """
        INSERT INTO public.flight_status_feed_status (id, configured, updated_at)
        VALUES (1, false, now())
        ON CONFLICT (id) DO UPDATE SET
            configured = false,
            updated_at = now()
        """
    )


async def _record_attempt(
    pool: asyncpg.Pool,
    *,
    legs_checked: int,
    delays_detected: int,
    error: str | None,
    benign_empty: bool = False,
) -> None:
    """Persist the outcome of one poll cycle.

    ``benign_empty`` marks a zero-selectable-legs pass: it is counted as a
    success for ``consecutive_failures`` bookkeeping (reset to 0) even though
    ``error`` carries informational text ("no selectable legs") rather than
    ``NULL``. Only a real :class:`FlightStatusFetchError` increments
    ``consecutive_failures``.
    """
    now = datetime.now(UTC)
    if error is None or benign_empty:
        await pool.execute(
            """
            INSERT INTO public.flight_status_feed_status (
                id, configured, last_attempt_at, last_success_at, last_error,
                consecutive_failures, legs_checked, delays_detected, updated_at
            ) VALUES (1, true, $1, $1, $2, 0, $3, $4, $1)
            ON CONFLICT (id) DO UPDATE SET
                configured = true,
                last_attempt_at = $1,
                last_success_at = $1,
                last_error = $2,
                consecutive_failures = 0,
                legs_checked = $3,
                delays_detected = $4,
                updated_at = $1
            """,
            now,
            error,
            legs_checked,
            delays_detected,
        )
    else:
        await pool.execute(
            """
            INSERT INTO public.flight_status_feed_status (
                id, configured, last_attempt_at, last_error,
                consecutive_failures, legs_checked, delays_detected, updated_at
            ) VALUES (1, true, $1, $2, 1, $3, $4, $1)
            ON CONFLICT (id) DO UPDATE SET
                configured = true,
                last_attempt_at = $1,
                last_error = $2,
                consecutive_failures = public.flight_status_feed_status.consecutive_failures + 1,
                legs_checked = $3,
                delays_detected = $4,
                updated_at = $1
            """,
            now,
            error,
            legs_checked,
            delays_detected,
        )


async def _fetch_upcoming_flight_legs(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Return flight legs departing within the poll window with a flight number."""
    now = datetime.now(UTC)
    window_end = now + timedelta(hours=_POLL_WINDOW_HOURS)
    rows = await pool.fetch(
        """
        SELECT l.id, l.trip_id, l.departure_at, l.metadata, t.name AS trip_name,
               t.destination
        FROM travel.legs l
        JOIN travel.trips t ON t.id = l.trip_id
        WHERE l.type = 'flight'
          AND t.status IN ('planned', 'active')
          AND l.departure_at >= $1
          AND l.departure_at <= $2
          AND l.metadata ? 'flight_number'
        ORDER BY l.departure_at ASC
        """,
        now,
        window_end,
    )
    return [dict(row) for row in rows]


def _parse_estimated_departure(raw: str | None) -> datetime | None:
    """Best-effort ISO-8601 parse of AviationStack's ``estimated_departure``."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    # AviationStack normally supplies an offset. An offset-less provider value
    # has no safe relationship to PostgreSQL's UTC-aware departure_at, so keep
    # the status evidence but do not mutate the operational timestamp.
    return parsed if parsed.tzinfo is not None else None


async def _write_leg_status(
    pool: asyncpg.Pool,
    leg_id: Any,
    trip_id: Any,
    original_departure_at: datetime | None,
    status: dict[str, Any],
) -> None:
    """Merge the poll result onto ``travel.legs.metadata->'flight_status'``.

    Surfaces through the existing ``trip_summary`` tool without a new API
    surface -- mirrors the ``metadata.prior_values`` audit pattern already
    used by ``update_itinerary``.

    bu-2jtfw.8 repair: also repairs ``departure_at`` from the reported
    ``estimated_departure`` (previously parsed and stored in metadata but
    never applied to the actual column, so a schedule slip never moved the
    time the rest of the system reads) and shifts ``arrival_at`` by the same
    delta (AviationStack reports only a departure estimate; preserving flight
    duration is the honest default absent an independent arrival estimate),
    then bumps ``updated_at`` and triggers a best-effort connection recompute
    for the leg's trip so a delay that breaks a layover is reflected in
    ``travel.connections`` without waiting for the next unrelated write.
    """
    payload = {**status, "checked_at": datetime.now(UTC).isoformat()}
    estimated_departure = _parse_estimated_departure(status.get("estimated_departure"))

    delta = None
    if estimated_departure is not None and original_departure_at is not None:
        if original_departure_at.tzinfo is None:
            original_departure_at = original_departure_at.replace(tzinfo=UTC)
        delta = estimated_departure - original_departure_at

    await pool.execute(
        """
        UPDATE travel.legs
        SET metadata = metadata || jsonb_build_object('flight_status', $2::jsonb),
            departure_at = COALESCE($3::timestamptz, departure_at),
            arrival_at = CASE
                WHEN $4::interval IS NOT NULL THEN arrival_at + $4::interval
                ELSE arrival_at
            END,
            updated_at = now()
        WHERE id = $1
        """,
        leg_id,
        payload,
        estimated_departure,
        delta,
    )

    from butlers.tools.travel import connections as _connections

    await _connections.mark_connection_derivation_pending(pool, str(trip_id))
    try:
        await _connections.recompute_trip_connections(pool, str(trip_id))
    except Exception:
        logger.warning(
            "flight_status_check: connection recompute failed for trip_id=%s",
            trip_id,
            exc_info=True,
        )


async def run_flight_status_check(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Poll AviationStack for upcoming flight legs and notify on delay/cancellation.

    Skips honestly (``{"skipped": True, "reason": "not_configured"}``) when no
    ``AVIATIONSTACK_API_KEY`` secret is on file. Never raises on an upstream
    fetch failure -- records the failure in ``flight_status_feed_status`` and
    keeps checking remaining legs.
    """
    del job_args
    api_key = await _resolve_api_key(pool)
    if not api_key:
        await _mark_not_configured(pool)
        return {"skipped": True, "reason": "not_configured"}

    legs = await _fetch_upcoming_flight_legs(pool)

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)

    legs_checked = 0
    delays_detected = 0
    # Zero selectable legs is not a silent success -- distinguish "nothing to
    # check" from a genuine poll failure (overwritten below if the loop hits
    # a real fetch error) so `flight_status_feed_status.last_error` never
    # reports NULL over a pass that verified nothing.
    last_error: str | None = "no selectable legs" if not legs else None
    # Tracks whether the eventual `last_error` is still the benign
    # zero-legs marker set above, as opposed to a real fetch failure
    # recorded below -- only the former should be exempted from
    # `consecutive_failures` bookkeeping.
    benign_empty = not legs
    notified: list[dict[str, Any]] = []

    try:
        for leg in legs:
            flight_number = (leg.get("metadata") or {}).get("flight_number")
            if not flight_number:
                continue
            try:
                raw = await _fetch_flight_status(client, flight_iata=flight_number, api_key=api_key)
            except FlightStatusFetchError as exc:
                logger.warning("flight_status_check: fetch failed for %s: %s", flight_number, exc)
                last_error = str(exc)
                benign_empty = False
                continue

            legs_checked += 1
            status = parse_flight_status(raw)
            if status is None:
                continue

            await _write_leg_status(
                pool, leg["id"], leg["trip_id"], leg.get("departure_at"), status
            )

            if status["notify_worthy"]:
                delays_detected += 1
                notified.append(
                    {
                        "leg_id": str(leg["id"]),
                        "trip_id": str(leg["trip_id"]),
                        "flight_number": flight_number,
                        "flight_status": status["flight_status"],
                        "delay_minutes": status["delay_minutes"],
                    }
                )
                await _propose_flight_delay_insight(pool, leg=leg, status=status)
    finally:
        if owns_client:
            await client.aclose()

    await _record_attempt(
        pool,
        legs_checked=legs_checked,
        delays_detected=delays_detected,
        error=last_error,
        benign_empty=benign_empty,
    )

    return {
        "skipped": False,
        "legs_checked": legs_checked,
        "delays_detected": delays_detected,
        "notified": notified,
        "last_error": last_error,
    }


async def _propose_flight_delay_insight(
    pool: asyncpg.Pool, *, leg: dict[str, Any], status: dict[str, Any]
) -> None:
    """Submit a dedup'd insight candidate for a delayed/cancelled flight.

    Import is deferred (matches ``roster/travel/jobs/travel_jobs.py``'s
    pattern) to avoid a hard import-time dependency between the core jobs
    package and the switchboard insight broker.
    """
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    trip_name = leg.get("trip_name") or leg.get("destination") or "your trip"
    flight_status = status["flight_status"]
    delay_minutes = status["delay_minutes"]

    if flight_status in _NOTIFY_STATUSES:
        message = f"Flight for {trip_name} is {flight_status} — check your itinerary."
        priority = 90
    else:
        message = f"Flight for {trip_name} is delayed ~{delay_minutes} min from schedule."
        priority = 70

    departure_at = leg.get("departure_at")
    expires_at = (
        departure_at + timedelta(hours=6)
        if isinstance(departure_at, datetime)
        else datetime.now(UTC) + timedelta(hours=6)
    )

    # Bucket the dedup key by status/delay so a status flip re-notifies but a
    # repeat poll reporting the same delay within the cooldown does not spam.
    dedup_bucket = flight_status if flight_status in _NOTIFY_STATUSES else f"delay-{delay_minutes}"
    dedup_key = f"travel:flight-status:{leg['id']}:{dedup_bucket}"

    result = await propose_insight_candidate(
        pool,
        origin_butler="travel",
        priority=priority,
        category="flight-status",
        dedup_key=dedup_key,
        message=message,
        expires_at=expires_at,
        cooldown_days=1,
    )
    if result.get("status") == "error":
        logger.warning(
            "flight_status_check: propose_insight_candidate error: %s",
            result.get("reason", "unknown"),
        )


__all__ = [
    "FlightStatusFetchError",
    "parse_flight_status",
    "run_flight_status_check",
]
