"""Concrete HaEnvironmentReader factory for the health butler's insight-scan job.

Builds an async callable that fetches 14 days of environmental sensor readings
from the health butler's own Home Assistant integration (credentials stored in
``public.entity_info``, never ``home.*`` schema) and classifies each reading as
adverse against built-in comfort thresholds.

The returned callable satisfies the ``HaEnvironmentReader`` type alias defined in
``roster/health/jobs/health_jobs.py``::

    HaEnvironmentReader = Callable[[], Awaitable[list[dict[str, Any]]]]

Each dict in the returned list contains:
- ``captured_at`` (datetime, UTC-aware): when the reading was taken
- ``metric`` (str): one of ``"temperature"``, ``"humidity"``, ``"co2"``, ``"air_quality"``
- ``adverse`` (bool): True when the reading is outside the butler's comfort thresholds

Usage in the scheduled job::

    from butlers.jobs.health_ha_reader import build_ha_environment_reader

    ha_reader = await build_ha_environment_reader(pool)
    result = await run_insight_scan(pool, ha_environment_reader=ha_reader)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.jobs.home import HomeJobContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environmental sensor classification — entity_id keyword matching
# ---------------------------------------------------------------------------

# Ordered keyword lists; first match wins for each metric bucket.
_TEMP_KEYWORDS: tuple[str, ...] = ("temperature", "temp")
_HUMIDITY_KEYWORDS: tuple[str, ...] = ("humidity", "humid")
_CO2_KEYWORDS: tuple[str, ...] = ("co2", "carbon_dioxide", "co_2")
_AIR_KEYWORDS: tuple[str, ...] = ("air_quality", "air_quality_index", "aqi", "pm25", "pm2_5")

# ---------------------------------------------------------------------------
# Default comfort thresholds (mirrors home butler defaults in home/api/router.py)
# ---------------------------------------------------------------------------

# Temperature in degrees Fahrenheit.  Sensor values below 50 are assumed to
# be Celsius and are converted before comparison.
_DEFAULT_TEMP_MIN_F: float = 68.0
_DEFAULT_TEMP_MAX_F: float = 76.0

_DEFAULT_HUMIDITY_MIN: float = 30.0
_DEFAULT_HUMIDITY_MAX: float = 60.0

_DEFAULT_CO2_MAX_PPM: float = 1000.0

# AQI numeric threshold; string states below are always adverse.
_DEFAULT_AQI_MAX: float = 50.0
_BAD_AIR_STATES: frozenset[str] = frozenset(
    {"poor", "very_poor", "hazardous", "unhealthy", "very_unhealthy", "moderate"}
)


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


async def build_ha_environment_reader(
    pool: asyncpg.Pool,
) -> Callable[[], Awaitable[list[dict[str, Any]]]] | None:
    """Build an HaEnvironmentReader for the health butler's insight-scan job.

    Reads HA credentials from ``public.entity_info`` (the cross-butler identity
    table, accessible to any butler role) — never from ``home.*`` schema tables.

    When credentials are absent the function returns ``None`` so the caller can
    skip environment correlation cleanly rather than raising.

    Args:
        pool: asyncpg connection pool bound to the health butler's schema.

    Returns:
        An async no-argument callable that returns a list of environment reading
        dicts on each invocation, or ``None`` if HA credentials are not
        configured.
    """
    ctx = await HomeJobContext.create(pool)
    if not ctx.ha_url or not ctx.ha_token:
        logger.info(
            "health insight scan: no HA credentials in entity_info; "
            "environment correlation will be skipped"
        )
        return None

    ha_url: str = ctx.ha_url
    ha_token: str = ctx.ha_token

    async def reader() -> list[dict[str, Any]]:
        return await _fetch_environment_readings(pool, ha_url, ha_token)

    return reader


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


async def _fetch_environment_readings(
    pool: asyncpg.Pool,
    ha_url: str,
    ha_token: str,
) -> list[dict[str, Any]]:
    """Fetch 14 days of environmental sensor readings from the HA history API.

    Discovers relevant entity IDs from ``ha_entity_snapshot`` (health schema),
    then calls ``GET /api/history/period/<start>`` with ``no_attributes=true``
    for efficiency.  Each state-entry is classified as adverse using the
    built-in comfort thresholds.

    Returns an empty list on any transient failure so the caller can degrade
    gracefully rather than raising.
    """
    entity_map = await _discover_env_entities(pool)
    if not entity_map:
        logger.debug(
            "health insight scan: no environmental entities in ha_entity_snapshot; "
            "environment correlation will be skipped"
        )
        return []

    now = datetime.now(UTC)
    window_start = now - timedelta(days=14)

    try:
        async with HomeJobContext(ha_url, ha_token) as ctx:
            assert ctx.client is not None
            resp = await ctx.client.get(
                f"{ha_url.rstrip('/')}/api/history/period/{window_start.isoformat()}",
                params={
                    "end_time": now.isoformat(),
                    "filter_entity_id": ",".join(entity_map),
                    "no_attributes": "true",
                },
            )
    except Exception:
        logger.warning(
            "health insight scan: HA history API call failed; "
            "environment correlation will be skipped",
            exc_info=True,
        )
        return []

    if resp.status_code != 200:
        logger.warning(
            "health insight scan: HA history API returned %d; "
            "environment correlation will be skipped",
            resp.status_code,
        )
        return []

    try:
        history_data: list[list[dict[str, Any]]] = resp.json()
    except Exception:
        logger.warning(
            "health insight scan: HA history API returned non-JSON; "
            "environment correlation will be skipped",
            exc_info=True,
        )
        return []

    if not isinstance(history_data, list):
        logger.warning(
            "health insight scan: HA history API returned invalid type: expected list, got %s; "
            "environment correlation will be skipped",
            type(history_data).__name__,
        )
        return []

    readings: list[dict[str, Any]] = []
    for entity_history in history_data:
        if not isinstance(entity_history, list):
            continue
        for state_entry in entity_history:
            entity_id = state_entry.get("entity_id", "")
            metric = entity_map.get(entity_id)
            if not metric:
                continue
            state_str = state_entry.get("state") or ""
            captured = _parse_ha_datetime(
                state_entry.get("last_changed") or state_entry.get("last_updated")
            )
            if captured is None:
                continue
            readings.append(
                {
                    "captured_at": captured,
                    "metric": metric,
                    "adverse": _is_adverse(metric, state_str),
                }
            )

    return readings


async def _discover_env_entities(pool: asyncpg.Pool) -> dict[str, str]:
    """Return ``{entity_id: metric_name}`` for environmental sensor entities.

    Queries ``ha_entity_snapshot`` (health butler's own table, no cross-schema
    access) and classifies each ``sensor.*`` entity_id by keyword match.
    Returns an empty dict when the table is absent or empty — not a hard error.
    """
    try:
        rows = await pool.fetch(
            "SELECT entity_id FROM ha_entity_snapshot WHERE entity_id LIKE 'sensor.%'"
        )
    except Exception:
        logger.debug(
            "health insight scan: could not query ha_entity_snapshot; "
            "environment correlation will be skipped",
            exc_info=True,
        )
        return {}

    result: dict[str, str] = {}
    for row in rows:
        eid = row["entity_id"]
        metric = _classify_entity(eid)
        if metric:
            result[eid] = metric
    return result


def _classify_entity(entity_id: str) -> str | None:
    """Return the metric bucket for an entity_id, or None if not environmental."""
    eid_lower = entity_id.lower()
    for kw in _TEMP_KEYWORDS:
        if kw in eid_lower:
            return "temperature"
    for kw in _HUMIDITY_KEYWORDS:
        if kw in eid_lower:
            return "humidity"
    for kw in _CO2_KEYWORDS:
        if kw in eid_lower:
            return "co2"
    for kw in _AIR_KEYWORDS:
        if kw in eid_lower:
            return "air_quality"
    return None


def _is_adverse(metric: str, state_str: str) -> bool:
    """Return True when the state value is outside the comfort range for ``metric``.

    Temperature values below 50 are assumed to be in Celsius and are converted
    to Fahrenheit before comparison.  Non-numeric states are never adverse
    unless the metric is ``air_quality`` (where named states like ``"poor"``
    are classified adversely).
    """
    if metric == "temperature":
        try:
            val = float(state_str)
            if val < 50:
                # Assume Celsius; convert to Fahrenheit
                val = val * 9 / 5 + 32
            return val < _DEFAULT_TEMP_MIN_F or val > _DEFAULT_TEMP_MAX_F
        except (ValueError, TypeError):
            return False

    if metric == "humidity":
        try:
            val = float(state_str)
            return val < _DEFAULT_HUMIDITY_MIN or val > _DEFAULT_HUMIDITY_MAX
        except (ValueError, TypeError):
            return False

    if metric == "co2":
        try:
            val = float(state_str)
            return val > _DEFAULT_CO2_MAX_PPM
        except (ValueError, TypeError):
            return False

    if metric == "air_quality":
        state_lower = state_str.lower().replace(" ", "_")
        if state_lower in _BAD_AIR_STATES:
            return True
        try:
            return float(state_str) > _DEFAULT_AQI_MAX
        except (ValueError, TypeError):
            return False

    return False


def _parse_ha_datetime(value: Any) -> datetime | None:
    """Parse a Home Assistant datetime string to a UTC-aware ``datetime``.

    Accepts ISO 8601 strings (with or without trailing ``Z``), existing
    ``datetime`` objects, and returns ``None`` for any unparseable value.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None
