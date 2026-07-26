"""Deterministic atmosphere (weather / AQI / pollen) context-feed refresh job.

Populates the shared cross-butler ``public.atmosphere_readings`` /
``public.atmosphere_feed_status`` tables from Open-Meteo's keyless forecast
and air-quality APIs, keyed off a single owner-configured home location.

This is a **zero-LLM, deterministic** job (RFC 0009-style producer, see
``butlers.jobs.context_producers``) rather than a message-ingestion
connector: there is no external "message" to classify or route, just an
ambient context feed to keep warm. It runs on the Home butler's scheduler
(``[[butler.schedule]] job_name = "atmosphere_feed_refresh"``), matching the
single-writer discipline already established for other shared context
producers.

Degraded-mode honesty (CLAUDE.md "Degraded-Mode Response Envelope"):

- No home location on file -> ``configured=false``. The job SHALL NOT
  attempt a fetch and SHALL NOT report this as an error.
- Home location configured but the upstream request fails/times out ->
  ``atmosphere_feed_status.last_error`` is set and
  ``consecutive_failures`` increments; no row is written to
  ``atmosphere_readings``. The job never raises -- a failed fetch is
  recorded, not crashed on.
- Pollen fields are NULL for non-European locations (Open-Meteo only
  forecasts pollen there). That is a legitimately-absent field, not a
  fetch failure -- see ``pollen_available``.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx

from butlers.credential_store import resolve_owner_entity_info

logger = logging.getLogger(__name__)

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

_ENV_LAT = "ATMOSPHERE_HOME_LAT"
_ENV_LON = "ATMOSPHERE_HOME_LON"
_ENTITY_INFO_COORDINATES = "home_coordinates"

_REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=10.0)

_FORECAST_CURRENT_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
)

_AIR_QUALITY_CURRENT_FIELDS = (
    "us_aqi",
    "european_aqi",
    "pm2_5",
    "pm10",
    "alder_pollen",
    "birch_pollen",
    "grass_pollen",
    "mugwort_pollen",
    "olive_pollen",
    "ragweed_pollen",
)


class AtmosphereFetchError(RuntimeError):
    """Raised internally when the upstream weather/AQI request fails."""


async def _resolve_home_coordinates(pool: asyncpg.Pool) -> tuple[float, float] | None:
    """Resolve the owner's home location.

    Env vars take precedence (dev/ops override), falling back to the
    owner-provisioned ``entity_info`` row -- mirrors the Home Assistant
    connector's ``HA_BASE_URL``/``resolve_owner_entity_info`` pattern.
    Returns ``None`` when neither source has a usable value (never raises).
    """
    env_lat = os.environ.get(_ENV_LAT)
    env_lon = os.environ.get(_ENV_LON)
    if env_lat and env_lon:
        try:
            return float(env_lat), float(env_lon)
        except ValueError:
            logger.warning(
                "atmosphere_feed_refresh: %s/%s env vars are not valid floats",
                _ENV_LAT,
                _ENV_LON,
            )

    stored = await resolve_owner_entity_info(pool, _ENTITY_INFO_COORDINATES)
    if not stored:
        return None
    return parse_home_coordinates(stored)


def parse_home_coordinates(value: str) -> tuple[float, float] | None:
    """Parse a stored ``"lat,lon"`` string. Returns ``None`` on malformed input."""
    parts = value.split(",", 1)
    if len(parts) != 2:
        logger.warning("atmosphere_feed_refresh: home_coordinates %r is not 'lat,lon'", value)
        return None
    try:
        return float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        logger.warning("atmosphere_feed_refresh: home_coordinates %r is not 'lat,lon'", value)
        return None


def _sanitize_error_message(exc: httpx.HTTPError) -> str:
    """Build a sanitized error message without leaking coordinates.

    HTTPStatusError embeds the full request URL including latitude/longitude
    query params. This function strips sensitive query params and returns
    a safe error message suitable for logging and DB storage.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        reason = getattr(exc.response, "reason_phrase", "")
        status_part = f"HTTP {exc.response.status_code}"
        if reason:
            status_part += f" {reason}"
        return f"open-meteo request failed: {status_part}"
    return f"open-meteo request failed: {type(exc).__name__}"


async def _fetch_conditions(
    client: httpx.AsyncClient, *, latitude: float, longitude: float
) -> dict[str, Any]:
    """Fetch current weather + air-quality/pollen from Open-Meteo (keyless).

    Raises :class:`AtmosphereFetchError` on any transport or HTTP-status
    failure; callers are responsible for recording the degraded state.
    """
    try:
        forecast_resp = await client.get(
            _FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": ",".join(_FORECAST_CURRENT_FIELDS),
                "timezone": "UTC",
            },
        )
        forecast_resp.raise_for_status()
        air_quality_resp = await client.get(
            _AIR_QUALITY_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": ",".join(_AIR_QUALITY_CURRENT_FIELDS),
                "timezone": "UTC",
            },
        )
        air_quality_resp.raise_for_status()
    except httpx.HTTPError as exc:
        sanitized_msg = _sanitize_error_message(exc)
        raise AtmosphereFetchError(sanitized_msg) from exc

    return {"forecast": forecast_resp.json(), "air_quality": air_quality_resp.json()}


def _first_present(current: dict[str, Any], *keys: str) -> float | None:
    """Return the max of the given fields that are present (non-None)."""
    values = [current.get(key) for key in keys]
    values = [v for v in values if v is not None]
    return max(values) if values else None


def parse_reading(raw: dict[str, Any], *, latitude: float, longitude: float) -> dict[str, Any]:
    """Parse the raw Open-Meteo forecast + air-quality payloads into a reading row.

    Pollen buckets: ``pollen_tree`` = max(alder, birch); ``pollen_grass`` =
    grass; ``pollen_weed`` = max(mugwort, olive, ragweed). ``pollen_available``
    is ``False`` only when every pollen field is absent (non-European
    location) -- that is a legitimate absence, not an error.
    """
    forecast_current = raw.get("forecast", {}).get("current", {}) or {}
    air_current = raw.get("air_quality", {}).get("current", {}) or {}

    pollen_tree = _first_present(air_current, "alder_pollen", "birch_pollen")
    pollen_grass = air_current.get("grass_pollen")
    pollen_weed = _first_present(air_current, "mugwort_pollen", "olive_pollen", "ragweed_pollen")
    pollen_available = any(v is not None for v in (pollen_tree, pollen_grass, pollen_weed))

    observed_at_raw = forecast_current.get("time")
    if observed_at_raw:
        observed_at = datetime.fromisoformat(observed_at_raw)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
    else:
        observed_at = datetime.now(UTC)

    return {
        "latitude": latitude,
        "longitude": longitude,
        "observed_at": observed_at,
        "temperature_c": forecast_current.get("temperature_2m"),
        "apparent_temperature_c": forecast_current.get("apparent_temperature"),
        "relative_humidity_pct": forecast_current.get("relative_humidity_2m"),
        "precipitation_mm": forecast_current.get("precipitation"),
        "weather_code": forecast_current.get("weather_code"),
        "wind_speed_kph": forecast_current.get("wind_speed_10m"),
        "aqi_us": air_current.get("us_aqi"),
        "aqi_european": air_current.get("european_aqi"),
        "pm2_5": air_current.get("pm2_5"),
        "pm10": air_current.get("pm10"),
        "pollen_tree": pollen_tree,
        "pollen_grass": pollen_grass,
        "pollen_weed": pollen_weed,
        "pollen_available": pollen_available,
        "raw": raw,
    }


async def _mark_not_configured(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """
        INSERT INTO public.atmosphere_feed_status (id, configured, updated_at)
        VALUES (1, false, now())
        ON CONFLICT (id) DO UPDATE SET
            configured = false,
            updated_at = now()
        """
    )


async def _record_success(
    pool: asyncpg.Pool, *, latitude: float, longitude: float, reading: dict[str, Any]
) -> None:
    now = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO public.atmosphere_readings (
            latitude, longitude, observed_at, temperature_c, apparent_temperature_c,
            relative_humidity_pct, precipitation_mm, weather_code, wind_speed_kph,
            aqi_us, aqi_european, pm2_5, pm10, pollen_tree, pollen_grass, pollen_weed,
            pollen_available, raw
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18
        )
        """,
        reading["latitude"],
        reading["longitude"],
        reading["observed_at"],
        reading["temperature_c"],
        reading["apparent_temperature_c"],
        reading["relative_humidity_pct"],
        reading["precipitation_mm"],
        reading["weather_code"],
        reading["wind_speed_kph"],
        reading["aqi_us"],
        reading["aqi_european"],
        reading["pm2_5"],
        reading["pm10"],
        reading["pollen_tree"],
        reading["pollen_grass"],
        reading["pollen_weed"],
        reading["pollen_available"],
        reading["raw"],
    )
    await pool.execute(
        """
        INSERT INTO public.atmosphere_feed_status (
            id, configured, latitude, longitude, last_attempt_at, last_success_at,
            last_error, consecutive_failures, updated_at
        ) VALUES (1, true, $1, $2, $3, $3, NULL, 0, $3)
        ON CONFLICT (id) DO UPDATE SET
            configured = true,
            latitude = $1,
            longitude = $2,
            last_attempt_at = $3,
            last_success_at = $3,
            last_error = NULL,
            consecutive_failures = 0,
            updated_at = $3
        """,
        latitude,
        longitude,
        now,
    )


async def _record_failure(
    pool: asyncpg.Pool, *, latitude: float, longitude: float, error: str
) -> None:
    now = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO public.atmosphere_feed_status (
            id, configured, latitude, longitude, last_attempt_at, last_error,
            consecutive_failures, updated_at
        ) VALUES (1, true, $1, $2, $3, $4, 1, $3)
        ON CONFLICT (id) DO UPDATE SET
            configured = true,
            latitude = $1,
            longitude = $2,
            last_attempt_at = $3,
            last_error = $4,
            consecutive_failures = public.atmosphere_feed_status.consecutive_failures + 1,
            updated_at = $3
        """,
        latitude,
        longitude,
        now,
        error,
    )


async def run_atmosphere_feed_refresh(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Refresh the shared weather/AQI/pollen context feed for the home location.

    Skips honestly (``{"skipped": True, "reason": "not_configured"}``) when no
    home location is on file. Never raises on an upstream fetch failure --
    records the failure in ``atmosphere_feed_status`` and returns a result
    dict describing it, matching the sibling deterministic job handlers'
    contract (accept ``pool``/``job_args``, return ``dict[str, Any]``).
    """
    del job_args
    coordinates = await _resolve_home_coordinates(pool)
    if coordinates is None:
        await _mark_not_configured(pool)
        return {"skipped": True, "reason": "not_configured"}

    latitude, longitude = coordinates
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)
    try:
        raw = await _fetch_conditions(client, latitude=latitude, longitude=longitude)
    except AtmosphereFetchError as exc:
        logger.warning("atmosphere_feed_refresh: fetch failed: %s", exc)
        await _record_failure(pool, latitude=latitude, longitude=longitude, error=str(exc))
        return {"skipped": False, "error": str(exc)}
    finally:
        if owns_client:
            await client.aclose()

    reading = parse_reading(raw, latitude=latitude, longitude=longitude)
    await _record_success(pool, latitude=latitude, longitude=longitude, reading=reading)
    return {
        "skipped": False,
        "observed_at": reading["observed_at"].isoformat(),
        "pollen_available": reading["pollen_available"],
    }


__all__ = [
    "AtmosphereFetchError",
    "parse_home_coordinates",
    "parse_reading",
    "run_atmosphere_feed_refresh",
]
