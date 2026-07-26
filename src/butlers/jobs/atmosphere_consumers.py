"""Cross-butler consumers of the shared atmosphere (weather/AQI/pollen) feed.

Three deterministic, zero-LLM jobs (RFC 0009-style producers) that read
``public.atmosphere_readings``/``atmosphere_feed_status`` (populated by
``butlers.jobs.atmosphere.run_atmosphere_feed_refresh``, bu-ep4ks.16 slice 1)
and propose insight candidates via the shared insight broker -- the same
dedup/cooldown/quiet-hours-aware surface ``roster/travel/jobs/travel_jobs.py``
already uses, rather than a raw ``notify()`` call that would bypass it:

- ``run_home_atmosphere_preconditioning`` -- extreme apparent temperature or
  poor outdoor air quality at the home location -> suggest pre-cooling /
  pre-heating / closing windows before it becomes uncomfortable.
- ``run_health_atmosphere_advisory`` -- unhealthy AQI or elevated pollen at
  the home location -> a health advisory (limit outdoor exertion, allergy
  meds).
- ``run_travel_destination_outlook`` -- for trips departing within a short
  window, geocode the destination (Open-Meteo's keyless geocoding API) and
  fetch its current conditions, so the pre-trip outlook is destination
  weather, not home weather.

Degraded-mode honesty: all three read the *latest* ``atmosphere_readings``
row and check ``atmosphere_feed_status.configured``. When the feed is not
configured (no home location on file) or has no reading yet, each job exits
silently with ``{"skipped": True, "reason": ...}`` -- never fabricates a
"clear skies" advisory from absent data. The destination-outlook job never
raises on a per-trip geocoding/fetch failure; it logs a sanitized warning and
continues with the remaining trips (mirrors ``atmosphere``'s per-cycle
resilience).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg
import httpx

from butlers.jobs.atmosphere import (
    AtmosphereFetchError,
    _fetch_conditions,
    _sanitize_error_message,
    parse_reading,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

# --- Home pre-conditioning thresholds ---------------------------------------
_HOME_HOT_APPARENT_C = 32.0
_HOME_COLD_APPARENT_C = 2.0
_HOME_UNHEALTHY_AQI_US = 150  # EPA "Unhealthy" breakpoint

# --- Health advisory thresholds ---------------------------------------------
_HEALTH_UNHEALTHY_AQI_US = 100  # EPA "Unhealthy for Sensitive Groups" breakpoint
_HEALTH_ELEVATED_POLLEN = 50.0  # grains/m3 -- rough "high" bucket for tree/grass/weed

# --- Travel destination outlook ---------------------------------------------
_TRAVEL_OUTLOOK_WINDOW_DAYS = 3


async def _latest_reading(pool: asyncpg.Pool) -> dict[str, Any] | None:
    """Return the most recent atmosphere reading, or ``None`` if never fetched."""
    row = await pool.fetchrow(
        "SELECT * FROM public.atmosphere_readings ORDER BY fetched_at DESC LIMIT 1"
    )
    return dict(row) if row else None


async def _feed_configured(pool: asyncpg.Pool) -> bool:
    row = await pool.fetchrow("SELECT configured FROM public.atmosphere_feed_status WHERE id = 1")
    return bool(row and row["configured"])


async def _propose(
    pool: asyncpg.Pool,
    *,
    origin_butler: str,
    priority: int,
    category: str,
    dedup_key: str,
    message: str,
    expires_at: datetime,
) -> None:
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    result = await propose_insight_candidate(
        pool,
        origin_butler=origin_butler,
        priority=priority,
        category=category,
        dedup_key=dedup_key,
        message=message,
        expires_at=expires_at,
        cooldown_days=1,
    )
    if result.get("status") == "error":
        logger.warning(
            "%s atmosphere consumer: propose_insight_candidate error: %s",
            origin_butler,
            result.get("reason", "unknown"),
        )


# ---------------------------------------------------------------------------
# Home pre-conditioning
# ---------------------------------------------------------------------------


async def run_home_atmosphere_preconditioning(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Suggest pre-cooling/pre-heating/closing windows ahead of extreme conditions."""
    del job_args

    if not await _feed_configured(pool):
        return {"skipped": True, "reason": "not_configured"}

    reading = await _latest_reading(pool)
    if reading is None:
        return {"skipped": True, "reason": "no_reading"}

    apparent_c = reading.get("apparent_temperature_c")
    aqi_us = reading.get("aqi_us")
    today = datetime.now(UTC).date().isoformat()
    proposed = 0

    if apparent_c is not None and apparent_c >= _HOME_HOT_APPARENT_C:
        await _propose(
            pool,
            origin_butler="home",
            priority=60,
            category="atmosphere-preconditioning",
            dedup_key=f"home:atmosphere:heat:{today}",
            message=(
                f"Outdoor conditions feel like {apparent_c:.0f}°C — "
                "consider pre-cooling before it peaks."
            ),
            expires_at=datetime.now(UTC) + timedelta(hours=6),
        )
        proposed += 1
    elif apparent_c is not None and apparent_c <= _HOME_COLD_APPARENT_C:
        await _propose(
            pool,
            origin_butler="home",
            priority=60,
            category="atmosphere-preconditioning",
            dedup_key=f"home:atmosphere:cold:{today}",
            message=(
                f"Outdoor conditions feel like {apparent_c:.0f}°C — "
                "consider pre-heating before it gets colder."
            ),
            expires_at=datetime.now(UTC) + timedelta(hours=6),
        )
        proposed += 1

    if aqi_us is not None and aqi_us >= _HOME_UNHEALTHY_AQI_US:
        await _propose(
            pool,
            origin_butler="home",
            priority=65,
            category="atmosphere-preconditioning",
            dedup_key=f"home:atmosphere:aqi:{today}",
            message=(
                f"Outdoor air quality is unhealthy (US AQI {aqi_us}) — "
                "consider closing windows and running an air purifier."
            ),
            expires_at=datetime.now(UTC) + timedelta(hours=6),
        )
        proposed += 1

    return {"skipped": False, "candidates_proposed": proposed}


# ---------------------------------------------------------------------------
# Health advisories
# ---------------------------------------------------------------------------


async def run_health_atmosphere_advisory(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Surface a health advisory when outdoor AQI or pollen is elevated."""
    del job_args

    if not await _feed_configured(pool):
        return {"skipped": True, "reason": "not_configured"}

    reading = await _latest_reading(pool)
    if reading is None:
        return {"skipped": True, "reason": "no_reading"}

    aqi_us = reading.get("aqi_us")
    today = datetime.now(UTC).date().isoformat()
    proposed = 0

    if aqi_us is not None and aqi_us >= _HEALTH_UNHEALTHY_AQI_US:
        await _propose(
            pool,
            origin_butler="health",
            priority=65,
            category="atmosphere-advisory",
            dedup_key=f"health:atmosphere:aqi:{today}",
            message=(
                f"Outdoor air quality is unhealthy for sensitive groups "
                f"(US AQI {aqi_us}) — consider limiting outdoor exertion today."
            ),
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        )
        proposed += 1

    if reading.get("pollen_available"):
        pollen_values = [
            v
            for v in (
                reading.get("pollen_tree"),
                reading.get("pollen_grass"),
                reading.get("pollen_weed"),
            )
            if v is not None
        ]
        max_pollen = max(pollen_values) if pollen_values else None
        if max_pollen is not None and max_pollen >= _HEALTH_ELEVATED_POLLEN:
            await _propose(
                pool,
                origin_butler="health",
                priority=55,
                category="atmosphere-advisory",
                dedup_key=f"health:atmosphere:pollen:{today}",
                message=(
                    f"Pollen levels are elevated today ({max_pollen:.0f} grains/m³) — "
                    "consider allergy medication if you're sensitive."
                ),
                expires_at=datetime.now(UTC) + timedelta(hours=12),
            )
            proposed += 1

    return {"skipped": False, "candidates_proposed": proposed}


# ---------------------------------------------------------------------------
# Travel destination outlook
# ---------------------------------------------------------------------------


async def _geocode_destination(
    client: httpx.AsyncClient, destination: str
) -> tuple[float, float] | None:
    """Resolve a free-text destination name to (lat, lon) via Open-Meteo geocoding.

    Returns ``None`` on no match or a transport failure -- callers treat this
    as "skip this trip", never as a hard error.
    """
    try:
        resp = await client.get(
            _GEOCODING_URL,
            params={"name": destination, "count": 1, "language": "en", "format": "json"},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "travel_destination_outlook: geocoding failed for %r: %s",
            destination,
            _sanitize_error_message(exc) if isinstance(exc, httpx.HTTPError) else exc,
        )
        return None

    results = resp.json().get("results") or []
    if not results:
        return None
    top = results[0]
    return top.get("latitude"), top.get("longitude")


def _weather_code_label(code: int | None) -> str:
    """Coarse human-readable label for Open-Meteo WMO weather codes."""
    if code is None:
        return "unknown conditions"
    if code == 0:
        return "clear skies"
    if code in (1, 2, 3):
        return "partly cloudy"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67:
        return "rain"
    if 71 <= code <= 77:
        return "snow"
    if 80 <= code <= 82:
        return "showers"
    if 95 <= code <= 99:
        return "thunderstorms"
    return "mixed conditions"


async def run_travel_destination_outlook(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Propose a destination-weather outlook for trips departing soon.

    Unlike the home/health consumers, this does not depend on the home
    atmosphere feed being configured -- it geocodes each trip's destination
    and fetches conditions there directly, reusing
    ``butlers.jobs.atmosphere``'s fetch/parse helpers.
    """
    del job_args

    today = datetime.now(UTC).date()
    window_end = today + timedelta(days=_TRAVEL_OUTLOOK_WINDOW_DAYS)
    rows = await pool.fetch(
        """
        SELECT id, name, destination, start_date
        FROM travel.trips
        WHERE status IN ('planned', 'active')
          AND start_date >= $1
          AND start_date <= $2
        ORDER BY start_date ASC
        """,
        today,
        window_end,
    )

    if not rows:
        return {"skipped": True, "reason": "no_upcoming_trips"}

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)

    proposed = 0
    checked = 0
    try:
        for row in rows:
            destination = row["destination"] or row["name"]
            if not destination:
                continue

            coordinates = await _geocode_destination(client, destination)
            if coordinates is None:
                continue
            latitude, longitude = coordinates

            try:
                raw = await _fetch_conditions(client, latitude=latitude, longitude=longitude)
            except AtmosphereFetchError as exc:
                logger.warning(
                    "travel_destination_outlook: fetch failed for %r: %s", destination, exc
                )
                continue

            checked += 1
            reading = parse_reading(raw, latitude=latitude, longitude=longitude)
            start_date_val = row["start_date"]
            start_d = (
                start_date_val
                if isinstance(start_date_val, date)
                else date.fromisoformat(str(start_date_val))
            )
            label = _weather_code_label(reading.get("weather_code"))
            temp = reading.get("temperature_c")
            temp_text = f"{temp:.0f}°C" if temp is not None else "unknown temperature"

            trip_id = str(row["id"])
            await _propose(
                pool,
                origin_butler="travel",
                priority=45,
                category="destination-outlook",
                dedup_key=f"travel:destination-outlook:{trip_id}:{today.isoformat()}",
                message=(
                    f"Outlook for {destination} ({row['name']}, departs "
                    f"{start_d.isoformat()}): {label}, {temp_text}."
                ),
                expires_at=datetime.combine(start_d, datetime.min.time(), tzinfo=UTC)
                + timedelta(days=1),
            )
            proposed += 1
    finally:
        if owns_client:
            await client.aclose()

    return {"skipped": False, "trips_checked": checked, "candidates_proposed": proposed}


__all__ = [
    "run_health_atmosphere_advisory",
    "run_home_atmosphere_preconditioning",
    "run_travel_destination_outlook",
]
