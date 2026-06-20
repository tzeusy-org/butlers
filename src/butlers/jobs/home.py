"""Deterministic job implementations for the Home butler's scheduled monitoring tasks.

These handlers replace prompt-based LLM dispatch with threshold-based
classification, memory storage, and Telegram notifications — eliminating LLM
costs for formulaic monitoring work.

Jobs read current entity state from the connector-populated ``ha_entity_snapshot``
table and load monitoring thresholds from the state store (``home:thresholds:*``),
falling back to direct HA REST API calls only for historical statistics queries
(energy digest) or hardcoded defaults (environment report).

The ``run_maintenance_schedule_check`` function is fully implemented: it queries
``home.maintenance_items`` for items that are due, overdue, or upcoming within 7
days; classifies each item by severity; builds a notification summary; and returns
a structured result.

The ``run_environment_report`` function discovers environmental sensors from
``ha_entity_snapshot``, loads comfort thresholds from the state store, classifies
deviations (ok/minor/moderate/critical), stores comfort_deviation facts, and sends
a room-by-room Telegram notification.

Shared helpers
--------------
``HomeJobContext``
    Defined in ``butlers.jobs.ha_context``.  Async context manager providing a
    short-lived ``httpx.AsyncClient`` with HA credentials pre-loaded.

``_load_thresholds(pool, key, defaults)``
    Generic state-store threshold reader.  Reads ``home:thresholds:<key>`` from
    the state store, validates that the value is a dict, and merges individual
    key overrides on top of *defaults*, logging a WARNING on fallback.

``_read_entity_snapshot(pool, domain_filter)``
    Generic ``ha_entity_snapshot`` reader.  Returns all rows (optionally filtered
    by domain prefix).  Raises ``EmptyEntitySnapshotError`` when the table has no
    matching rows.

``_send_notify(pool, message)``
    Convenience wrapper around ``_notify_owner_telegram`` with an explicit
    ``channel="telegram"`` / ``intent="send"`` semantic.

Design reference: openspec/changes/archive/home-butler-enhancements/
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

import asyncpg
import httpx

from butlers.core.state import state_get
from butlers.credential_store import CredentialStore, resolve_owner_entity_info
from butlers.modules.memory.storage import store_fact

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared errors
# ---------------------------------------------------------------------------


class EmptyEntitySnapshotError(Exception):
    """Raised by ``_read_entity_snapshot`` when ``ha_entity_snapshot`` has no rows.

    Job handlers catch this to send an owner alert and return early with
    ``{"error": "no_entity_snapshot"}``.
    """


# ---------------------------------------------------------------------------
# Generic threshold loader
# ---------------------------------------------------------------------------

_T = dict[str, Any]


async def _load_thresholds(
    pool: asyncpg.Pool,
    key: str,
    defaults: _T,
) -> _T:
    """Load monitoring thresholds from the state store with fallback to *defaults*.

    Reads ``home:thresholds:<key>`` from the state store.  The stored value
    MUST be a JSON dict.  Individual keys are cast to the same type as the
    corresponding *defaults* value (``int`` or ``float``).  If the key is
    absent, the stored value is not a dict, or a sub-key has an invalid
    value, *defaults* (or the default for that sub-key) are used and a
    WARNING is logged.

    Args:
        pool: asyncpg connection pool for the home butler's database.
        key: State-store key suffix (e.g. ``"battery"`` → looks up
            ``home:thresholds:battery``).
        defaults: Dict mapping threshold names to their default values.
            Values must be ``int`` or ``float`` — used both as fallback and
            to determine cast type.

    Returns:
        A dict with the same keys as *defaults*, populated from the state
        store where valid entries exist and falling back to *defaults*
        elsewhere.
    """
    full_key = f"home:thresholds:{key}"
    raw = await state_get(pool, full_key)

    if raw is None:
        logger.warning("%s not found in state store; using default thresholds", full_key)
        return dict(defaults)

    if not isinstance(raw, dict):
        logger.warning(
            "%s has unexpected type %r; using default thresholds",
            full_key,
            type(raw).__name__,
        )
        return dict(defaults)

    result: _T = {}
    for threshold_key, default_val in defaults.items():
        raw_val = raw.get(threshold_key, default_val)
        cast: type = type(default_val)  # int or float
        try:
            result[threshold_key] = cast(raw_val)
        except (TypeError, ValueError):
            logger.warning(
                "%s[%r] has invalid value %r; using default %r",
                full_key,
                threshold_key,
                raw_val,
                default_val,
            )
            result[threshold_key] = default_val
    return result


# ---------------------------------------------------------------------------
# Generic entity snapshot reader
# ---------------------------------------------------------------------------


async def _read_entity_snapshot(
    pool: asyncpg.Pool,
    domain_filter: str | None = None,
) -> list[Any]:
    """Query ``ha_entity_snapshot`` and return all matching rows.

    Args:
        pool: asyncpg connection pool.
        domain_filter: Optional HA domain prefix (e.g. ``"sensor"``,
            ``"binary_sensor"``).  When provided, only entities whose
            ``entity_id`` starts with ``"<domain_filter>."`` are returned.

    Returns:
        List of asyncpg ``Record`` objects with at least the columns
        ``entity_id``, ``state``, ``attributes``.

    Raises:
        EmptyEntitySnapshotError: If the filtered (or full) result set is
            empty — the connector has not yet populated the table or was
            recently reset.
    """
    if domain_filter is not None:
        rows = await pool.fetch(
            "SELECT entity_id, state, attributes, last_updated "
            "FROM ha_entity_snapshot "
            "WHERE entity_id LIKE $1 "
            "ORDER BY entity_id",
            f"{domain_filter}.%",
        )
    else:
        rows = await pool.fetch(
            "SELECT entity_id, state, attributes, last_updated "
            "FROM ha_entity_snapshot "
            "ORDER BY entity_id"
        )

    if not rows:
        raise EmptyEntitySnapshotError(
            "ha_entity_snapshot is empty"
            + (f" for domain '{domain_filter}'" if domain_filter else "")
        )

    return list(rows)


# ---------------------------------------------------------------------------
# Shared notify helper
# ---------------------------------------------------------------------------


async def _send_notify(pool: asyncpg.Pool, message: str) -> None:
    """Send a Telegram notification to the owner (channel=telegram, intent=send).

    This is the canonical notify helper for Home butler job handlers.
    It delegates to ``_notify_owner_telegram``, which resolves the bot token
    and chat ID from the owner's contact info.

    Args:
        pool: asyncpg connection pool for the home butler's database.
        message: Message text to deliver.  HTML parse mode is used, so
            ``<b>``, ``<i>``, and ``<code>`` tags are supported.
    """
    await _notify_owner_telegram(pool, message)


class _NullEmbeddingEngine:
    """Sentinel embedding engine that returns zero vectors for deterministic jobs.

    Matches the synchronous ``EmbeddingEngine.embed(text: str) -> list[float]``
    interface expected by ``store_fact``.  Returns an empty vector so that
    vector-similarity searches simply skip these facts.
    """

    def embed(self, text: str) -> list[float]:  # noqa: ARG002
        return []


class _NoOpEmbeddingEngine:
    """Minimal embedding engine stub for deterministic jobs.

    Returns zero vectors so that store_fact() can be called without loading
    sentence-transformers in a deterministic scheduled job context.
    Semantic search quality is degraded, but the fact is correctly stored.
    """

    _DIM = 384

    def embed(self, text: str) -> list[float]:  # noqa: ARG002
        return [0.0] * self._DIM

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._DIM for _ in texts]


# ---------------------------------------------------------------------------
# Default thresholds (used when state store has no value)
# ---------------------------------------------------------------------------

_DEFAULT_BATTERY_THRESHOLDS: dict[str, int] = {
    "critical": 10,
    "warning": 20,
    "info": 30,
}

_DEFAULT_OFFLINE_HOURS_THRESHOLDS: dict[str, int] = {
    "critical": 24,
    "warning": 1,
}

# Severity → memory importance mapping (device health check)
_SEVERITY_IMPORTANCE: dict[str, float] = {
    "critical": 8.0,
    "warning": 6.5,
    "info": 5.0,
}

_TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"

_DEFAULT_COMFORT_DEFAULTS: dict[str, float] = {
    "temp_min_f": 68.0,
    "temp_max_f": 76.0,
    "humidity_min": 30.0,
    "humidity_max": 60.0,
    "co2_max_ppm": 1000.0,
}

_DEFAULT_COMFORT_DEVIATION: dict[str, float] = {
    "minor_temp_f": 2.0,
    "moderate_temp_f": 5.0,
    "minor_humidity": 10.0,
    "moderate_humidity": 20.0,
    "critical_temp_low_f": 60.0,
    "critical_temp_high_f": 85.0,
    "critical_co2_ppm": 1500.0,
    "critical_humidity_low": 15.0,
    "critical_humidity_high": 80.0,
}
_DEFAULT_ENERGY_THRESHOLDS: dict[str, float] = {
    "anomaly_pct": 20.0,
    "high_severity_pct": 100.0,
}

# Keywords that identify energy-related sensor entities
_ENERGY_KEYWORDS = ("energy", "power", "kwh", "consumption", "watt")

# Number of top consumers to rank and report
_TOP_N_CONSUMERS = 5


# ---------------------------------------------------------------------------
# Battery / offline threshold loading helpers
# ---------------------------------------------------------------------------


async def _load_battery_thresholds(pool: asyncpg.Pool) -> dict[str, int]:
    """Load battery thresholds from state store; fall back to defaults."""
    return await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)  # type: ignore[return-value]


async def _load_offline_hours_thresholds(pool: asyncpg.Pool) -> dict[str, int]:
    """Load offline-hours thresholds from state store; fall back to defaults."""
    return await _load_thresholds(pool, "offline_hours", _DEFAULT_OFFLINE_HOURS_THRESHOLDS)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Battery / offline classification helpers (pure functions — easily unit-tested)
# ---------------------------------------------------------------------------


def classify_battery(value: float, thresholds: dict[str, int]) -> str | None:
    """Classify a battery level into a severity string.

    Args:
        value: Numeric battery percentage (e.g. 15.0).
        thresholds: Dict with keys ``critical``, ``warning``, ``info``.

    Returns:
        ``"critical"``, ``"warning"``, ``"info"``, or ``None`` if value
        exceeds the ``info`` threshold (device is healthy).
    """
    if value <= thresholds["critical"]:
        return "critical"
    if value <= thresholds["warning"]:
        return "warning"
    if value <= thresholds["info"]:
        return "info"
    return None


def classify_offline(last_changed: datetime | None, thresholds: dict[str, int]) -> str | None:
    """Classify an offline device by how long it has been unreachable.

    Args:
        last_changed: UTC datetime of the entity's last state change,
            or ``None`` if unknown.
        thresholds: Dict with keys ``critical`` (hours) and ``warning`` (hours).

    Returns:
        ``"critical"`` or ``"warning"`` if the device has been offline
        long enough, or ``None`` if below the warning threshold.
    """
    if last_changed is None:
        # Unknown last_changed — treat as critical (safe default)
        return "critical"
    now = datetime.now(UTC)
    # Ensure last_changed is timezone-aware
    if last_changed.tzinfo is None:
        last_changed = last_changed.replace(tzinfo=UTC)
    hours_offline = (now - last_changed).total_seconds() / 3600.0
    if hours_offline > thresholds["critical"]:
        return "critical"
    if hours_offline > thresholds["warning"]:
        return "warning"
    return None


# ---------------------------------------------------------------------------
# Device health check memory storage helper
# ---------------------------------------------------------------------------


async def _store_device_fact(
    pool: asyncpg.Pool,
    *,
    subject: str,
    content: str,
    importance: float,
    tags: list[str],
) -> None:
    """Store a device_issue fact in the home butler's memory.

    Uses a no-op embedding engine so the job runs without sentence-transformers.
    Logs warnings on failure and does not propagate exceptions.
    """
    try:
        await store_fact(
            pool,
            subject=subject,
            predicate="device_issue",
            content=content,
            embedding_engine=_NoOpEmbeddingEngine(),
            importance=importance,
            permanence="volatile",
            tags=tags,
            source_butler="home",
        )
    except Exception:
        logger.exception("device_health_check: failed to store memory fact for subject=%r", subject)


# ---------------------------------------------------------------------------
# Device health check notification helpers
# ---------------------------------------------------------------------------


def _entity_subject(entity_id: str) -> str:
    """Convert an HA entity_id to a slug suitable for memory subject.

    E.g. ``"sensor.basement_battery"`` → ``"basement-battery"``.
    """
    parts = entity_id.split(".", 1)
    name = parts[-1] if len(parts) > 1 else entity_id
    return name.replace("_", "-")


def _build_health_check_notification(
    *,
    issues: list[dict[str, Any]],
    devices_checked: int,
    critical_count: int,
    warning_count: int,
    info_count: int,
) -> str:
    """Build the Telegram notification message for device health check results.

    Args:
        issues: List of issue dicts (entity_id, friendly_name, issue_type,
            severity, value, description).
        devices_checked: Total number of entities surveyed.
        critical_count: Number of critical-severity issues.
        warning_count: Number of warning-severity issues.
        info_count: Number of info-severity issues.

    Returns:
        Formatted text message for Telegram.
    """

    def _esc(text: str) -> str:
        """HTML-escape a user-supplied string for Telegram HTML parse mode."""
        return html.escape(text)

    if not issues or (critical_count == 0 and warning_count == 0):
        # All-clear (may still have info-only issues)
        if info_count > 0:
            info_lines = [
                f"  \u2022 {_esc(i['friendly_name'])}: {_esc(i['description'])}"
                for i in issues
                if i["severity"] == "info"
            ]
            info_block = "\n".join(info_lines)
            return (
                f"\u2705 Device Health Check ({devices_checked} device(s) checked)\n\n"
                f"\u2139\ufe0f Low battery (info):\n{info_block}"
            )
        return (
            f"\u2705 Device Health Check: all {devices_checked} device(s) healthy"
            " \u2014 no issues found."
        )

    lines: list[str] = [f"\U0001f514 Device Health Check ({devices_checked} device(s) checked)\n"]

    # Critical issues first
    critical_issues = [i for i in issues if i["severity"] == "critical"]
    if critical_issues:
        lines.append("\U0001f534 Critical:")
        for issue in critical_issues:
            lines.append(f"  \u2022 {_esc(issue['friendly_name'])}: {_esc(issue['description'])}")
        lines.append("")

    # Warning issues
    warning_issues = [i for i in issues if i["severity"] == "warning"]
    if warning_issues:
        lines.append("\U0001f7e0 Warning:")
        for issue in warning_issues:
            lines.append(f"  \u2022 {_esc(issue['friendly_name'])}: {_esc(issue['description'])}")
        lines.append("")

    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Energy digest internal helpers
# ---------------------------------------------------------------------------


def _is_energy_entity(entity_id: str, friendly_name: str | None) -> bool:
    """Return True if this entity is energy-related by entity_id or friendly_name."""
    combined = f"{entity_id} {friendly_name or ''}".lower()
    return any(kw in combined for kw in _ENERGY_KEYWORDS)


def _extract_numeric_state(state: str | None) -> float | None:
    """Parse a numeric state string to float; return None for non-numeric/unavailable."""
    if state is None or state in ("unavailable", "unknown", ""):
        return None
    try:
        return float(state)
    except (ValueError, TypeError):
        return None


# Sensor domain/keyword filters for environment sensors
_TEMP_KEYWORDS = ("temperature", "temp")
_HUMIDITY_KEYWORDS = ("humidity", "humid")
_CO2_KEYWORDS = ("co2", "carbon_dioxide", "air_quality", "voc", "co2_ppm")
_ILLUMINANCE_KEYWORDS = ("illuminance", "lux", "light_level")

# At most 3 recommendations per report (per spec)
_MAX_RECOMMENDATIONS = 3


# ---------------------------------------------------------------------------
# Threshold loading helpers — environment report
# ---------------------------------------------------------------------------


async def _load_comfort_defaults(pool: asyncpg.Pool) -> dict[str, float]:
    """Load comfort defaults from state store, falling back to hardcoded defaults.

    Returns a dict with keys: ``temp_min_f``, ``temp_max_f``,
    ``humidity_min``, ``humidity_max``, ``co2_max_ppm``.
    """
    return await _load_thresholds(pool, "comfort_defaults", _DEFAULT_COMFORT_DEFAULTS)  # type: ignore[return-value]


async def _load_comfort_deviation(pool: asyncpg.Pool) -> dict[str, float]:
    """Load comfort deviation thresholds from state store, falling back to hardcoded defaults.

    Returns a dict with keys: ``minor_temp_f``, ``moderate_temp_f``,
    ``minor_humidity``, ``moderate_humidity``, ``critical_temp_low_f``,
    ``critical_temp_high_f``, ``critical_co2_ppm``, ``critical_humidity_low``,
    ``critical_humidity_high``.
    """
    return await _load_thresholds(pool, "comfort_deviation", _DEFAULT_COMFORT_DEVIATION)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Sensor classification helpers
# ---------------------------------------------------------------------------


def _classify_sensor_type(entity_id: str, friendly_name: str | None) -> str | None:
    """Classify a sensor entity as temperature/humidity/co2/illuminance or None.

    Returns one of ``"temperature"``, ``"humidity"``, ``"co2"``,
    ``"illuminance"``, or ``None`` if not classified.
    """
    combined = f"{entity_id} {friendly_name or ''}".lower()
    if any(kw in combined for kw in _TEMP_KEYWORDS):
        return "temperature"
    if any(kw in combined for kw in _HUMIDITY_KEYWORDS):
        return "humidity"
    if any(kw in combined for kw in _CO2_KEYWORDS):
        return "co2"
    if any(kw in combined for kw in _ILLUMINANCE_KEYWORDS):
        return "illuminance"
    return None


def _extract_area(attributes: dict[str, Any]) -> str | None:
    """Extract area name from entity attributes, trying common HA fields."""
    area = attributes.get("area_id") or attributes.get("area") or attributes.get("room")
    return str(area).strip() if area else None


# ---------------------------------------------------------------------------
# Deviation classification
# ---------------------------------------------------------------------------


def classify_deviation(
    sensor_type: str,
    value: float,
    *,
    comfort_defaults: dict[str, float],
    deviation_thresholds: dict[str, float],
    area_preference: dict[str, Any] | None = None,
) -> str:
    """Classify a sensor reading against comfort preferences and deviation thresholds.

    Args:
        sensor_type: One of ``"temperature"``, ``"humidity"``, ``"co2"``,
            ``"illuminance"``.
        value: The current sensor reading.
        comfort_defaults: Default comfort range thresholds (from state store or hardcoded).
        deviation_thresholds: Deviation severity thresholds (from state store or hardcoded).
        area_preference: Optional area-specific preference dict that may override defaults.
            Expected keys: ``temp_min_f``, ``temp_max_f``, ``humidity_min``,
            ``humidity_max``, ``co2_max_ppm`` (same as ``comfort_defaults``).

    Returns:
        One of ``"ok"``, ``"minor"``, ``"moderate"``, or ``"critical"``.
    """
    # Merge area preference on top of defaults
    prefs = dict(comfort_defaults)
    if area_preference:
        for key in comfort_defaults:
            if key in area_preference:
                try:
                    prefs[key] = float(area_preference[key])
                except (TypeError, ValueError):
                    pass

    if sensor_type == "temperature":
        temp_min = prefs["temp_min_f"]
        temp_max = prefs["temp_max_f"]
        crit_low = deviation_thresholds["critical_temp_low_f"]
        crit_high = deviation_thresholds["critical_temp_high_f"]
        minor = deviation_thresholds["minor_temp_f"]
        moderate = deviation_thresholds["moderate_temp_f"]

        # Critical first
        if value < crit_low or value > crit_high:
            return "critical"
        # Within range = ok
        if temp_min <= value <= temp_max:
            return "ok"
        # Below range
        distance = temp_min - value if value < temp_min else value - temp_max
        if distance > moderate:
            return "moderate"
        if distance > minor:
            return "moderate"
        if distance > 0:
            return "minor"
        return "ok"

    if sensor_type == "humidity":
        hum_min = prefs["humidity_min"]
        hum_max = prefs["humidity_max"]
        crit_low = deviation_thresholds["critical_humidity_low"]
        crit_high = deviation_thresholds["critical_humidity_high"]
        minor = deviation_thresholds["minor_humidity"]
        moderate = deviation_thresholds["moderate_humidity"]

        if value < crit_low or value > crit_high:
            return "critical"
        if hum_min <= value <= hum_max:
            return "ok"
        distance = hum_min - value if value < hum_min else value - hum_max
        if distance > moderate:
            return "moderate"
        if distance > minor:
            return "moderate"
        if distance > 0:
            return "minor"
        return "ok"

    if sensor_type == "co2":
        co2_max = prefs["co2_max_ppm"]
        crit_co2 = deviation_thresholds["critical_co2_ppm"]

        if value > crit_co2:
            return "critical"
        if value > co2_max:
            return "moderate"
        return "ok"

    # illuminance and unknown types: no classification, treat as ok
    return "ok"


# ---------------------------------------------------------------------------
# Area/sensor discovery
# ---------------------------------------------------------------------------


async def _discover_areas_and_sensors(
    pool: asyncpg.Pool,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Query ha_entity_snapshot to build area -> sensor type -> list of readings.

    Returns a nested dict:
    ``{area_name: {sensor_type: [{entity_id, friendly_name, value, state}, ...]}}``

    Only sensors classified as temperature/humidity/co2/illuminance are included.
    Sensors without a recognisable area go under ``"unknown"``.
    """
    rows = await pool.fetch("SELECT entity_id, state, attributes FROM ha_entity_snapshot")
    areas: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for row in rows:
        entity_id: str = row["entity_id"]
        state: str | None = row["state"]
        attrs: Any = row["attributes"] or {}
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs)
            except (ValueError, TypeError):
                attrs = {}
        if not isinstance(attrs, dict):
            attrs = {}

        friendly = attrs.get("friendly_name") or entity_id
        sensor_type = _classify_sensor_type(entity_id, friendly)
        if sensor_type is None:
            continue  # not an env sensor we care about

        value = _extract_numeric_state(state)
        if value is None:
            continue  # no numeric reading available

        area = _extract_area(attrs) or "unknown"

        areas.setdefault(area, {}).setdefault(sensor_type, []).append(
            {
                "entity_id": entity_id,
                "friendly_name": friendly,
                "value": value,
                "state": state,
            }
        )

    return areas


# ---------------------------------------------------------------------------
# Comfort preference lookup from memory
# ---------------------------------------------------------------------------


async def _load_area_comfort_preference(
    pool: asyncpg.Pool,
    area_name: str,
) -> dict[str, Any] | None:
    """Query memory facts for a comfort_preference fact for the given area.

    Returns the most recent matching fact's content parsed as dict,
    or None if no stored preference exists.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT content FROM facts
            WHERE predicate = 'comfort_preference'
              AND subject = $1
              AND validity = 'active'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            area_name,
        )
    except Exception:
        logger.debug(
            "_load_area_comfort_preference: could not query facts for area=%r",
            area_name,
            exc_info=True,
        )
        return None

    if row is None:
        return None

    content = row["content"]
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            pass
    # Content exists but is not a parseable dict — log and ignore
    logger.debug(
        "_load_area_comfort_preference: area=%r fact content is not a JSON dict (%r)",
        area_name,
        type(content).__name__,
    )
    return None


# ---------------------------------------------------------------------------
# Report message builder
# ---------------------------------------------------------------------------

_DEVIATION_RECS: dict[str, dict[str, str]] = {
    "temperature": {
        "low": "temperature is low — consider raising the thermostat",
        "high": "temperature is high — consider lowering the thermostat or improving ventilation",
    },
    "humidity": {
        "low": "humidity is low — consider running a humidifier",
        "high": "humidity is high — consider running a dehumidifier or improving ventilation",
    },
    "co2": {
        "high": "CO\u2082 is elevated — open a window or increase ventilation",
    },
}


def _recommendation_for(
    sensor_type: str,
    value: float,
    comfort_defaults: dict[str, float],
) -> str | None:
    """Return a human-readable recommendation string for a deviation, or None."""
    recs = _DEVIATION_RECS.get(sensor_type)
    if not recs:
        return None

    if sensor_type == "temperature":
        midpoint = (comfort_defaults["temp_min_f"] + comfort_defaults["temp_max_f"]) / 2
        direction = "low" if value < midpoint else "high"
    elif sensor_type == "humidity":
        midpoint = (comfort_defaults["humidity_min"] + comfort_defaults["humidity_max"]) / 2
        direction = "low" if value < midpoint else "high"
    elif sensor_type == "co2":
        direction = "high"
    else:
        return None

    return recs.get(direction)


def _build_environment_report_message(
    area_results: list[dict[str, Any]],
    comfort_defaults: dict[str, float],
) -> str:
    """Build the room-by-room environment report Telegram message.

    Args:
        area_results: List of per-area result dicts, each containing:
            ``area``, ``readings`` (dict sensor_type -> value),
            ``deviations`` (dict sensor_type -> severity).
        comfort_defaults: Default comfort thresholds (for recommendation hints).

    Returns:
        HTML-formatted message string.
    """
    lines: list[str] = ["<b>Daily Environment Report</b>"]

    all_recommendations: list[str] = []

    for area_info in area_results:
        area = area_info["area"]
        readings: dict[str, float] = area_info["readings"]
        deviations: dict[str, str] = area_info["deviations"]

        if not readings:
            continue

        status_parts: list[str] = []
        for sensor_type, value in readings.items():
            sev = deviations.get(sensor_type, "ok")
            if sensor_type == "temperature":
                label = f"{value:.1f}\u00b0F"
            elif sensor_type == "humidity":
                label = f"{value:.0f}% RH"
            elif sensor_type == "co2":
                label = f"{value:.0f} ppm CO\u2082"
            elif sensor_type == "illuminance":
                label = f"{value:.0f} lux"
            else:
                label = f"{value:.1f}"

            status_icon = ""
            if sev == "ok":
                status_icon = "\u2705"
            elif sev == "minor":
                status_icon = "\U0001f535"
            elif sev == "moderate":
                status_icon = "\U0001f7e1"
            elif sev == "critical":
                status_icon = "\U0001f534"

            status_parts.append(f"{status_icon} {label}")

            if sev not in ("ok", "minor") and len(all_recommendations) < _MAX_RECOMMENDATIONS:
                rec = _recommendation_for(sensor_type, value, comfort_defaults)
                if rec:
                    rec_text = f"{html.escape(area.replace('_', ' ').title())}: {rec}"
                    if rec_text not in all_recommendations:
                        all_recommendations.append(rec_text)

        area_label = html.escape(area.replace("_", " ").title())
        lines.append(f"\n<b>{area_label}</b>: {', '.join(status_parts)}")

    if all_recommendations:
        lines.append("\n<b>Recommendations:</b>")
        for rec in all_recommendations[:_MAX_RECOMMENDATIONS]:
            lines.append(f"  \u2022 {rec}")

    return "\n".join(lines)


async def _load_energy_thresholds(pool: asyncpg.Pool) -> dict[str, float]:
    """Load energy anomaly thresholds from state store, falling back to defaults.

    Returns a dict with keys ``anomaly_pct`` and ``high_severity_pct``.
    """
    return await _load_thresholds(pool, "energy", _DEFAULT_ENERGY_THRESHOLDS)  # type: ignore[return-value]


async def _discover_energy_sensors(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Query ha_entity_snapshot for energy-related sensor entities.

    Returns a list of rows (entity_id, state, attributes) for sensors whose
    entity_id or friendly_name matches any energy keyword.
    """
    rows = await pool.fetch("SELECT entity_id, state, attributes FROM ha_entity_snapshot")
    sensors = []
    for row in rows:
        entity_id: str = row["entity_id"]
        attrs = row["attributes"] or {}
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs)
            except (ValueError, TypeError):
                attrs = {}
        friendly = attrs.get("friendly_name") or ""
        if _is_energy_entity(entity_id, friendly):
            sensors.append(
                {
                    "entity_id": entity_id,
                    "state": row["state"],
                    "attributes": attrs,
                    "friendly_name": friendly or entity_id,
                }
            )
    return sensors


async def _fetch_weekly_statistics(
    pool: asyncpg.Pool,
    entity_ids: list[str],
    *,
    ha_url: str,
    ha_token: str,
) -> dict[str, Any]:
    """Fetch weekly energy statistics via HA REST API.

    Calls ``recorder/get_statistics_during_period`` with ``period="week"``
    for aggregate totals and ``period="day"`` for daily breakdowns.

    Returns a dict mapping entity_id → ``{"weekly_sum": float, "daily": [...]}``
    or an empty dict if HA is unreachable.
    """
    end_dt = datetime.now(tz=UTC)
    start_dt = end_dt - timedelta(days=7)
    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat()

    base_url = ha_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }

    result: dict[str, Any] = {}

    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(30.0, connect=10.0),
        verify=False,  # noqa: S501 — local HA instances often use self-signed certs
    ) as client:
        # Fetch weekly aggregate totals
        try:
            resp = await client.post(
                f"{base_url}/api/recorder/get_statistics_during_period",
                json={
                    "start_time": start_iso,
                    "end_time": end_iso,
                    "statistic_ids": entity_ids,
                    "period": "week",
                    "types": ["sum", "mean"],
                },
            )
            if resp.status_code >= 400:
                logger.error(
                    "HA API error fetching weekly stats: status=%d body=%r",
                    resp.status_code,
                    resp.text[:200],
                )
            else:
                weekly_data = resp.json()
                for eid, stats_list in weekly_data.items():
                    if not isinstance(stats_list, list) or not stats_list:
                        continue
                    # Sum the 'sum' values across all returned entries
                    total = sum(float(s.get("sum") or 0) for s in stats_list if isinstance(s, dict))
                    result.setdefault(eid, {})["weekly_sum"] = total
        except httpx.RequestError as exc:
            logger.warning(
                "HA REST API unreachable for weekly stats — skipping historical data: %s", exc
            )
            return {}

        # Fetch daily breakdown
        try:
            resp = await client.post(
                f"{base_url}/api/recorder/get_statistics_during_period",
                json={
                    "start_time": start_iso,
                    "end_time": end_iso,
                    "statistic_ids": entity_ids,
                    "period": "day",
                    "types": ["sum", "mean"],
                },
            )
            if resp.status_code >= 400:
                logger.error(
                    "HA API error fetching daily stats: status=%d body=%r",
                    resp.status_code,
                    resp.text[:200],
                )
            else:
                daily_data = resp.json()
                for eid, stats_list in daily_data.items():
                    if not isinstance(stats_list, list):
                        continue
                    result.setdefault(eid, {})["daily"] = stats_list
        except httpx.RequestError as exc:
            logger.warning("HA REST API unreachable for daily stats: %s", exc)

    return result


async def _load_energy_baselines(pool: asyncpg.Pool) -> dict[str, Any]:
    """Query the facts table for energy_baseline facts.

    Returns a dict with ``total_kwh`` baseline and per-device baselines
    keyed by entity_id or subject.
    """
    try:
        rows = await pool.fetch(
            """
            SELECT subject, content
            FROM facts
            WHERE predicate = 'energy_baseline'
              AND validity = 'active'
            ORDER BY created_at DESC
            """,
        )
    except Exception:
        logger.debug("Could not query energy_baseline facts", exc_info=True)
        return {}

    baselines: dict[str, Any] = {}
    for row in rows:
        subject: str = row["subject"]
        baselines[subject] = {"content": row["content"]}
    return baselines


def _compute_device_totals(
    stats: dict[str, Any],
    sensors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a ranked list of device energy totals from statistics.

    Returns a list of dicts (entity_id, friendly_name, weekly_kwh, share_pct)
    sorted by weekly_kwh descending.
    """
    friendly_map = {s["entity_id"]: s["friendly_name"] for s in sensors}

    totals: list[dict[str, Any]] = []
    for entity_id, data in stats.items():
        kwh = float(data.get("weekly_sum") or 0.0)
        if kwh <= 0:
            continue
        totals.append(
            {
                "entity_id": entity_id,
                "friendly_name": friendly_map.get(entity_id, entity_id),
                "weekly_kwh": kwh,
            }
        )

    totals.sort(key=lambda x: x["weekly_kwh"], reverse=True)

    grand_total = sum(d["weekly_kwh"] for d in totals)
    for item in totals:
        item["share_pct"] = (
            round(item["weekly_kwh"] / grand_total * 100, 1) if grand_total > 0 else 0.0
        )

    return totals


def detect_anomalies(
    device_totals: list[dict[str, Any]],
    baselines: dict[str, Any],
    *,
    anomaly_pct: float,
    high_severity_pct: float,
) -> list[dict[str, Any]]:
    """Detect energy anomalies by comparing device totals against baselines.

    An anomaly is triggered when a device's weekly_kwh exceeds its baseline by
    ``anomaly_pct`` percent or more (default 20%).  If it exceeds by
    ``high_severity_pct`` percent or more (default 100%), it is high severity.

    Args:
        device_totals: Ranked list from ``_compute_device_totals``.
        baselines: Mapping from subject/entity_id to baseline data.
        anomaly_pct: Minimum percentage above baseline to flag as anomaly.
        high_severity_pct: Percentage above baseline for high-severity flag.

    Returns:
        List of anomaly dicts with keys: ``entity_id``, ``friendly_name``,
        ``weekly_kwh``, ``baseline_kwh``, ``pct_above``, ``severity``
        (``"high"`` | ``"anomaly"``).
    """
    anomalies: list[dict[str, Any]] = []
    for item in device_totals:
        entity_id = item["entity_id"]
        weekly_kwh = item["weekly_kwh"]

        # Look up baseline by entity_id or friendly_name
        baseline_entry = baselines.get(entity_id) or baselines.get(item["friendly_name"])
        if baseline_entry is None:
            continue

        # Parse baseline kWh from content string or numeric field
        baseline_kwh: float | None = None
        content = baseline_entry.get("content", "")
        if isinstance(content, (int, float)):
            baseline_kwh = float(content)
        elif isinstance(content, str):
            # Try to extract a leading numeric value
            m = re.search(r"(\d+(?:\.\d+)?)\s*(?:kwh|kw|watt)?", content, re.IGNORECASE)
            if m:
                try:
                    baseline_kwh = float(m.group(1))
                except ValueError:
                    pass

        if baseline_kwh is None or baseline_kwh <= 0:
            continue

        pct_above = (weekly_kwh - baseline_kwh) / baseline_kwh * 100

        if pct_above >= high_severity_pct:
            anomalies.append(
                {
                    "entity_id": entity_id,
                    "friendly_name": item["friendly_name"],
                    "weekly_kwh": weekly_kwh,
                    "baseline_kwh": baseline_kwh,
                    "pct_above": round(pct_above, 1),
                    "severity": "high",
                }
            )
        elif pct_above >= anomaly_pct:
            anomalies.append(
                {
                    "entity_id": entity_id,
                    "friendly_name": item["friendly_name"],
                    "weekly_kwh": weekly_kwh,
                    "baseline_kwh": baseline_kwh,
                    "pct_above": round(pct_above, 1),
                    "severity": "anomaly",
                }
            )

    return anomalies


def _build_digest_message(
    total_kwh: float,
    top_consumers: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    baseline_total: float | None,
) -> str:
    """Compose the weekly energy digest Telegram message."""
    lines: list[str] = ["<b>Weekly Energy Digest</b>"]

    # Total with trend
    trend_str = ""
    if baseline_total is not None and baseline_total > 0:
        delta_pct = (total_kwh - baseline_total) / baseline_total * 100
        sign = "+" if delta_pct >= 0 else ""
        trend_str = f" ({sign}{delta_pct:.1f}% vs baseline)"
    lines.append(f"\nTotal: {total_kwh:.1f} kWh{trend_str}")

    # Top consumers
    if top_consumers:
        lines.append("\n<b>Top consumers:</b>")
        for item in top_consumers[:_TOP_N_CONSUMERS]:
            lines.append(
                f"  • {html.escape(item['friendly_name'])}: {item['weekly_kwh']:.1f} kWh "
                f"({item['share_pct']:.0f}%)"
            )

    # Anomaly alerts
    if anomalies:
        lines.append("\n<b>⚠️ Anomaly alerts:</b>")
        for a in anomalies:
            sev = "🔴 HIGH" if a["severity"] == "high" else "🟡 Anomaly"
            lines.append(
                f"  {sev}: {html.escape(a['friendly_name'])} — "
                f"{a['weekly_kwh']:.1f} kWh (+{a['pct_above']:.0f}% above baseline)"
            )

    # Recommendations (up to 3)
    recs: list[str] = []
    high_severity_devices = [a for a in anomalies if a["severity"] == "high"]
    if high_severity_devices:
        recs.append(
            f"Check {html.escape(high_severity_devices[0]['friendly_name'])} — "
            f"consumption is more than double its baseline."
        )
    if top_consumers:
        top_name = html.escape(top_consumers[0]["friendly_name"])
        recs.append(
            f"Review {top_name} usage patterns — it accounts for the most energy this week."
        )
    if total_kwh > 0 and not anomalies:
        recs.append("Energy usage within normal range this week.")

    if recs:
        lines.append("\n<b>Recommendations:</b>")
        for rec in recs[:3]:
            lines.append(f"  • {rec}")

    return "\n".join(lines)


async def _notify_owner_telegram(
    pool: asyncpg.Pool,
    message: str,
) -> None:
    """Send a Telegram message to the owner using the bot token and owner chat ID.

    Resolves ``BUTLER_TELEGRAM_TOKEN`` from ``butler_secrets`` via
    ``CredentialStore`` (ecosystem-wide Tier 1 credential) and
    ``telegram_chat_id`` from the owner's entity_info/contact_info via
    ``resolve_owner_entity_info`` (user-specific Tier 2). Silently skips if
    either credential is unavailable.
    """
    token = await CredentialStore(pool).resolve("BUTLER_TELEGRAM_TOKEN")
    chat_id = await resolve_owner_entity_info(pool, "telegram_chat_id")

    if not token or not chat_id:
        logger.warning(
            "_notify_owner_telegram: telegram_bot_token or telegram_chat_id not configured "
            "— skipping notification"
        )
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("description", resp.text[:200])
                except (ValueError, KeyError):
                    detail = resp.text[:200]
                logger.error(
                    "_notify_owner_telegram: Telegram sendMessage failed: status=%d detail=%s",
                    resp.status_code,
                    detail,
                )
            else:
                logger.info("_notify_owner_telegram: notification sent to chat_id=%r", chat_id)
    except httpx.RequestError as exc:
        logger.error("_notify_owner_telegram: request error — %s", exc)


# ---------------------------------------------------------------------------
# Energy digest — main entry point
# ---------------------------------------------------------------------------


async def run_energy_digest(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the weekly energy digest for the Home butler.

    Steps:
    1. Discover energy sensors from ha_entity_snapshot.
    2. Load energy thresholds from state store (``home:thresholds:energy``).
    3. Resolve HA credentials (URL, token) for REST API calls.
    4. Fetch weekly historical statistics via HA REST API.
    5. Compute top consumers and percentage shares.
    6. Compare vs baselines (from ``energy_baseline`` memory facts).
    7. Detect anomalies using configurable thresholds.
    8. Store updated baseline and spike facts in memory.
    9. Send structured digest notification via Telegram.

    Args:
        pool: asyncpg connection pool for the Home butler's database.
        job_args: Optional job arguments (currently unused; reserved for future use).

    Returns:
        ``{"total_kwh": float, "devices_ranked": int, "anomalies_found": int,
        "baseline_updated": bool}`` on success, or
        ``{"error": "no_energy_sensors"}`` / ``{"error": "no_entity_snapshot"}``
        on early-exit conditions.
    """
    del job_args  # reserved for future parameterisation

    # ------------------------------------------------------------------
    # 1. Check entity snapshot is populated
    # ------------------------------------------------------------------
    try:
        snapshot_count = await pool.fetchval("SELECT count(*) FROM ha_entity_snapshot") or 0
    except Exception:
        logger.exception("run_energy_digest: failed to query ha_entity_snapshot")
        snapshot_count = 0

    if snapshot_count == 0:
        logger.warning("run_energy_digest: ha_entity_snapshot is empty — skipping")
        await _notify_owner_telegram(
            pool,
            "⚠️ Energy digest skipped: Home Assistant entity data is unavailable. "
            "Check that the HA connector is running.",
        )
        return {"error": "no_entity_snapshot"}

    # ------------------------------------------------------------------
    # 2. Discover energy sensors
    # ------------------------------------------------------------------
    sensors = await _discover_energy_sensors(pool)
    if not sensors:
        logger.info("run_energy_digest: no energy sensors found in snapshot")
        await _notify_owner_telegram(
            pool,
            "Energy monitoring is not configured. "
            "No energy, power, or kWh sensors were found in Home Assistant.",
        )
        return {"error": "no_energy_sensors"}

    logger.info("run_energy_digest: discovered %d energy sensor(s)", len(sensors))
    entity_ids = [s["entity_id"] for s in sensors]

    # ------------------------------------------------------------------
    # 3. Load thresholds
    # ------------------------------------------------------------------
    thresholds = await _load_energy_thresholds(pool)
    anomaly_pct = thresholds["anomaly_pct"]
    high_severity_pct = thresholds["high_severity_pct"]

    # ------------------------------------------------------------------
    # 4. Resolve HA credentials and fetch statistics
    # ------------------------------------------------------------------
    ha_url = await resolve_owner_entity_info(pool, "home_assistant_url")
    ha_token = await resolve_owner_entity_info(pool, "home_assistant_token")

    stats: dict[str, Any] = {}
    ha_unreachable = False

    if ha_url and ha_token:
        stats = await _fetch_weekly_statistics(
            pool,
            entity_ids,
            ha_url=ha_url,
            ha_token=ha_token,
        )
        if not stats:
            ha_unreachable = True
            logger.warning("run_energy_digest: HA REST API returned no data")
    else:
        ha_unreachable = True
        logger.warning(
            "run_energy_digest: HA credentials not configured "
            "(home_assistant_url or home_assistant_token missing) — "
            "historical statistics unavailable"
        )

    # ------------------------------------------------------------------
    # 5. Compute device totals and rank
    # ------------------------------------------------------------------
    device_totals = _compute_device_totals(stats, sensors)
    total_kwh = float(sum(d["weekly_kwh"] for d in device_totals))
    top_consumers = device_totals[:_TOP_N_CONSUMERS]

    # ------------------------------------------------------------------
    # 6. Load baselines and compute anomalies
    # ------------------------------------------------------------------
    baselines = await _load_energy_baselines(pool)
    anomalies = detect_anomalies(
        device_totals,
        baselines,
        anomaly_pct=anomaly_pct,
        high_severity_pct=high_severity_pct,
    )

    # Baseline total from the "overall" energy_baseline fact, if present
    baseline_total_kwh: float | None = None
    for key, bval in baselines.items():
        content = bval.get("content", "")
        if "overall" in key.lower() or "total" in key.lower():
            m = re.search(r"(\d+(?:\.\d+)?)\s*kwh", content, re.IGNORECASE)
            if m:
                try:
                    baseline_total_kwh = float(m.group(1))
                except ValueError:
                    pass
            break

    # ------------------------------------------------------------------
    # 7. Store baseline and spike facts in memory
    # ------------------------------------------------------------------
    baseline_updated = False
    if total_kwh > 0:
        eng = _NullEmbeddingEngine()

        # Store overall energy baseline fact
        try:
            top_summary = ", ".join(
                f"{d['friendly_name']}={d['weekly_kwh']:.1f}kWh" for d in top_consumers
            )
            await store_fact(
                pool,
                subject="overall",
                predicate="energy_baseline",
                content=(
                    f"Weekly energy total: {total_kwh:.1f} kWh. Top consumers: {top_summary}."
                ),
                embedding_engine=eng,
                importance=5.0,
                permanence="standard",
                tags=["energy", "baseline", "weekly"],
                source_butler="home",
            )
            baseline_updated = True
            logger.info(
                "run_energy_digest: stored energy_baseline fact (total_kwh=%.1f)", total_kwh
            )
        except Exception:
            logger.warning("run_energy_digest: failed to store energy_baseline fact", exc_info=True)

        # Store per-device energy baseline facts so anomaly detection can compare on next run
        for device in device_totals:
            try:
                await store_fact(
                    pool,
                    subject=device["entity_id"],
                    predicate="energy_baseline",
                    content=f"{device['weekly_kwh']:.2f} kWh weekly baseline",
                    embedding_engine=eng,
                    importance=4.0,
                    permanence="standard",
                    tags=["energy", "baseline", "weekly", "per-device"],
                    source_butler="home",
                )
            except Exception:
                logger.warning(
                    "run_energy_digest: failed to store per-device energy_baseline for %s",
                    device["entity_id"],
                    exc_info=True,
                )

        # Store spike facts for anomalous devices
        for anomaly in anomalies:
            try:
                sev_label = "high severity" if anomaly["severity"] == "high" else "anomaly"
                await store_fact(
                    pool,
                    subject=anomaly["entity_id"],
                    predicate="energy_spike",
                    content=(
                        f"{anomaly['friendly_name']}: {anomaly['weekly_kwh']:.1f} kWh this week "
                        f"({anomaly['pct_above']:.0f}% above baseline "
                        f"{anomaly['baseline_kwh']:.1f} kWh) — {sev_label}"
                    ),
                    embedding_engine=eng,
                    importance=7.0 if anomaly["severity"] == "high" else 6.0,
                    permanence="volatile",
                    tags=["energy", "spike", anomaly["severity"]],
                    source_butler="home",
                )
            except Exception:
                logger.warning(
                    "run_energy_digest: failed to store energy_spike fact for %s",
                    anomaly["entity_id"],
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # 8. Send Telegram digest notification
    # ------------------------------------------------------------------
    if ha_unreachable and not device_totals:
        message = (
            "⚠️ Weekly energy digest: Home Assistant REST API was unreachable. "
            "Historical statistics are unavailable this week."
        )
    else:
        message = _build_digest_message(
            total_kwh=total_kwh,
            top_consumers=top_consumers,
            anomalies=anomalies,
            baseline_total=baseline_total_kwh,
        )
        if ha_unreachable:
            message += "\n\n⚠️ Note: HA REST API unreachable — statistics may be incomplete."

    await _notify_owner_telegram(pool, message)

    result = {
        "total_kwh": float(round(total_kwh, 3)),
        "devices_ranked": len(device_totals),
        "anomalies_found": len(anomalies),
        "baseline_updated": baseline_updated,
    }
    logger.info("run_energy_digest: completed — %s", result)
    return result


# ---------------------------------------------------------------------------
# Device health check — full implementation
# ---------------------------------------------------------------------------


async def run_device_health_check(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check battery levels and offline status for all HA entity snapshots.

    Steps:
    1. Load battery and offline thresholds from state store.
    2. Query ha_entity_snapshot for all entities.
    3. Classify battery issues (entity_id/friendly_name contains "battery").
    4. Classify offline devices (state "unavailable" or "unknown").
    5. Store memory facts for each issue found.
    6. Send a Telegram notification with the summary.

    Args:
        pool: asyncpg connection pool for the home butler's database.
        job_args: Optional job arguments (currently unused).

    Returns:
        Dict with keys:
        - ``devices_checked`` (int): Total number of entity rows checked.
        - ``issues_found`` (int): Total issues (battery + offline).
        - ``critical_count`` (int): Issues classified as critical.
        - ``warning_count`` (int): Issues classified as warning.
    """
    del job_args  # reserved for future parameterisation

    # ------------------------------------------------------------------
    # 1. Load thresholds
    # ------------------------------------------------------------------
    battery_thresholds = await _load_battery_thresholds(pool)
    offline_thresholds = await _load_offline_hours_thresholds(pool)

    # ------------------------------------------------------------------
    # 2. Query entity snapshot
    # ------------------------------------------------------------------
    rows = await pool.fetch(
        """
        SELECT entity_id, state, attributes, last_updated
        FROM ha_entity_snapshot
        ORDER BY entity_id
        """
    )

    if not rows:
        logger.info("device_health_check: ha_entity_snapshot is empty; sending alert")
        await _notify_owner_telegram(
            pool,
            "\u26a0\ufe0f Device Health Check: Home Assistant entity data is unavailable. "
            "The connector may not have run yet or was recently reset.",
        )
        return {"error": "no_entity_snapshot"}

    devices_checked = len(rows)

    # ------------------------------------------------------------------
    # 3 & 4. Classify battery and offline issues
    # ------------------------------------------------------------------
    issues: list[dict[str, Any]] = []

    for row in rows:
        entity_id: str = row["entity_id"]
        state: str = row["state"] or ""
        attributes = row["attributes"]
        last_updated = row["last_updated"]

        # Decode attributes if stored as string
        if isinstance(attributes, str):
            try:
                attributes = json.loads(attributes)
            except (json.JSONDecodeError, ValueError):
                attributes = {}
        if not isinstance(attributes, dict):
            attributes = {}

        friendly_name: str = attributes.get("friendly_name", entity_id)

        # ---- Battery classification ----------------------------------------
        is_battery = "battery" in entity_id.lower() or "battery" in friendly_name.lower()
        if is_battery:
            try:
                battery_pct = float(state)
            except (ValueError, TypeError):
                battery_pct = None

            if battery_pct is not None and battery_pct <= battery_thresholds["info"]:
                severity = classify_battery(battery_pct, battery_thresholds)
                if severity is not None:
                    issues.append(
                        {
                            "entity_id": entity_id,
                            "friendly_name": friendly_name,
                            "issue_type": "battery",
                            "severity": severity,
                            "value": battery_pct,
                            "description": f"battery at {battery_pct:.0f}%",
                        }
                    )

        # ---- Offline classification -----------------------------------------
        if state in ("unavailable", "unknown"):
            # Parse last_updated to datetime
            last_changed_dt: datetime | None = None
            if last_updated is not None:
                if isinstance(last_updated, datetime):
                    last_changed_dt = last_updated
                elif isinstance(last_updated, str):
                    try:
                        last_changed_dt = datetime.fromisoformat(last_updated)
                    except ValueError:
                        last_changed_dt = None

            severity = classify_offline(last_changed_dt, offline_thresholds)
            if severity is not None:
                issues.append(
                    {
                        "entity_id": entity_id,
                        "friendly_name": friendly_name,
                        "issue_type": "offline",
                        "severity": severity,
                        "value": state,
                        "description": f"offline (state: {state})",
                    }
                )

    # ------------------------------------------------------------------
    # 5. Store memory facts
    # ------------------------------------------------------------------
    critical_count = sum(1 for i in issues if i["severity"] == "critical")
    warning_count = sum(1 for i in issues if i["severity"] == "warning")
    info_count = sum(1 for i in issues if i["severity"] == "info")
    issues_found = len(issues)

    if issues:
        for issue in issues:
            subject = _entity_subject(issue["entity_id"])
            content = (
                f"{issue['friendly_name']}: {issue['description']}"
                f" \u2014 {issue['severity']} severity"
            )
            importance = _SEVERITY_IMPORTANCE[issue["severity"]]
            tags = ["maintenance", issue["issue_type"]]
            await _store_device_fact(
                pool,
                subject=subject,
                content=content,
                importance=importance,
                tags=tags,
            )
    else:
        # All-clear: store a healthy-fleet fact
        await _store_device_fact(
            pool,
            subject="device-fleet",
            content=(
                f"All {devices_checked} device(s) healthy \u2014 no battery or connectivity issues."
            ),
            importance=3.0,
            tags=["maintenance"],
        )

    # ------------------------------------------------------------------
    # 6. Send notification
    # ------------------------------------------------------------------
    notification = _build_health_check_notification(
        issues=issues,
        devices_checked=devices_checked,
        critical_count=critical_count,
        warning_count=warning_count,
        info_count=info_count,
    )
    await _notify_owner_telegram(pool, notification)

    logger.info(
        "device_health_check: devices_checked=%d issues_found=%d critical=%d warning=%d",
        devices_checked,
        issues_found,
        critical_count,
        warning_count,
    )

    return {
        "devices_checked": devices_checked,
        "issues_found": issues_found,
        "critical_count": critical_count,
        "warning_count": warning_count,
    }


# ---------------------------------------------------------------------------
# Environment report — main entry point
# ---------------------------------------------------------------------------


async def run_environment_report(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the daily environment report for the Home butler.

    Steps:
    1. Verify ha_entity_snapshot is populated; alert owner if empty.
    2. Discover areas and environmental sensors from ha_entity_snapshot.
    3. Load comfort defaults from state store (``home:thresholds:comfort_defaults``).
    4. Load deviation thresholds from state store (``home:thresholds:comfort_deviation``).
    5. For each area, retrieve stored comfort preferences from memory (facts table).
    6. Classify each sensor reading as ok/minor/moderate/critical.
    7. Store comfort_deviation facts for moderate or critical deviations.
    8. Build and send room-by-room Telegram notification.

    Args:
        pool: asyncpg connection pool for the Home butler's database.
        job_args: Optional job arguments (currently unused; reserved for future use).

    Returns:
        ``{"areas_checked": int, "sensors_read": int, "deviations_found": int}`` on success,
        or ``{"error": "no_entity_snapshot"}`` when the snapshot table is empty.
    """
    del job_args  # reserved for future parameterisation

    # ------------------------------------------------------------------
    # 1. Verify snapshot is populated
    # ------------------------------------------------------------------
    try:
        snapshot_count = await pool.fetchval("SELECT count(*) FROM ha_entity_snapshot") or 0
    except Exception:
        logger.exception("run_environment_report: failed to query ha_entity_snapshot")
        snapshot_count = 0

    if snapshot_count == 0:
        logger.warning("run_environment_report: ha_entity_snapshot is empty — skipping")
        await _notify_owner_telegram(
            pool,
            "\u26a0\ufe0f Environment report skipped: Home Assistant entity data is unavailable. "
            "Check that the HA connector is running.",
        )
        return {"error": "no_entity_snapshot"}

    # ------------------------------------------------------------------
    # 2. Discover areas and sensors
    # ------------------------------------------------------------------
    areas_map = await _discover_areas_and_sensors(pool)
    if not areas_map:
        logger.info("run_environment_report: no environment sensors found in snapshot")
        await _notify_owner_telegram(
            pool,
            "Environment report: no temperature, humidity, CO\u2082, or illuminance sensors found "
            "in Home Assistant.",
        )
        return {"areas_checked": 0, "sensors_read": 0, "deviations_found": 0}

    # ------------------------------------------------------------------
    # 3 & 4. Load thresholds once
    # ------------------------------------------------------------------
    comfort_defaults = await _load_comfort_defaults(pool)
    deviation_thresholds = await _load_comfort_deviation(pool)

    # ------------------------------------------------------------------
    # 5-6. Per-area: load preferences, classify deviations
    # ------------------------------------------------------------------
    eng = _NullEmbeddingEngine()
    total_sensors = 0
    total_deviations = 0
    area_results: list[dict[str, Any]] = []

    for area_name, sensor_types in sorted(areas_map.items()):
        area_preference = await _load_area_comfort_preference(pool, area_name)

        readings: dict[str, float] = {}
        deviations: dict[str, str] = {}
        area_sensor_count = 0

        for sensor_type, sensor_list in sensor_types.items():
            if not sensor_list:
                continue
            # Use the first sensor of each type per area (most relevant reading)
            sensor = sensor_list[0]
            value = sensor["value"]
            readings[sensor_type] = value
            area_sensor_count += 1

            severity = classify_deviation(
                sensor_type,
                value,
                comfort_defaults=comfort_defaults,
                deviation_thresholds=deviation_thresholds,
                area_preference=area_preference,
            )
            deviations[sensor_type] = severity

            # ------------------------------------------------------------------
            # 7. Store comfort_deviation facts for moderate or critical
            # ------------------------------------------------------------------
            if severity in ("moderate", "critical"):
                total_deviations += 1
                importance = 8.0 if severity == "critical" else 6.0
                try:
                    await store_fact(
                        pool,
                        subject=area_name,
                        predicate="comfort_deviation",
                        content=(
                            f"{area_name} {sensor_type} deviation: "
                            f"value={value:.1f}, severity={severity}"
                        ),
                        embedding_engine=eng,
                        importance=importance,
                        permanence="volatile",
                        tags=["comfort", "deviation", sensor_type, area_name],
                        source_butler="home",
                    )
                    logger.info(
                        "run_environment_report: stored comfort_deviation fact "
                        "area=%r type=%r severity=%r",
                        area_name,
                        sensor_type,
                        severity,
                    )
                except Exception:
                    logger.warning(
                        "run_environment_report: failed to store comfort_deviation fact "
                        "area=%r type=%r",
                        area_name,
                        sensor_type,
                        exc_info=True,
                    )

        total_sensors += area_sensor_count
        area_results.append(
            {
                "area": area_name,
                "readings": readings,
                "deviations": deviations,
            }
        )

    # ------------------------------------------------------------------
    # 8. Build and send report
    # ------------------------------------------------------------------
    message = _build_environment_report_message(area_results, comfort_defaults)
    await _notify_owner_telegram(pool, message)

    areas_checked = len(area_results)
    logger.info(
        "run_environment_report: complete — areas=%d sensors=%d deviations=%d",
        areas_checked,
        total_sensors,
        total_deviations,
    )

    return {
        "areas_checked": areas_checked,
        "sensors_read": total_sensors,
        "deviations_found": total_deviations,
    }


# ---------------------------------------------------------------------------
# Maintenance schedule check constants and TypedDicts
# ---------------------------------------------------------------------------

# Number of days used to look ahead for "upcoming" items.
UPCOMING_LOOKAHEAD_DAYS = 7

# Overdue severity thresholds (in days past due).
DUE_MAX_DAYS = 7  # 0-7 days past due → "due"
OVERDUE_MAX_DAYS = 30  # 8-30 days past due → "overdue"; >30 → "critical"

# Severity labels (ordered from most to least urgent for display).
SEVERITY_CRITICAL = "critical"
SEVERITY_OVERDUE = "overdue"
SEVERITY_DUE = "due"
SEVERITY_UPCOMING = "upcoming"
SEVERITY_NEVER_COMPLETED = "never_completed"

_SEVERITY_ORDER = [
    SEVERITY_CRITICAL,
    SEVERITY_NEVER_COMPLETED,
    SEVERITY_OVERDUE,
    SEVERITY_DUE,
    SEVERITY_UPCOMING,
]

# ---------------------------------------------------------------------------
# TypedDicts for maintenance schedule check
# ---------------------------------------------------------------------------


class MaintenanceItemRow(TypedDict):
    """Row shape returned from the home.maintenance_items query."""

    id: str
    name: str
    category: str
    interval_days: int
    last_completed_at: datetime | None
    next_due_at: datetime | None
    notes: str | None


class ClassifiedItem(TypedDict):
    """A maintenance item with its computed classification."""

    id: str
    name: str
    category: str
    interval_days: int
    severity: str
    # negative = days overdue (e.g. -3 = 3 days past due); positive = days until due (upcoming)
    days_delta: int


class MaintenanceCheckResult(TypedDict):
    """Return value of run_maintenance_schedule_check."""

    items_checked: int
    due_count: int
    overdue_count: int
    critical_count: int
    upcoming_count: int
    never_completed_count: int
    reminders_sent: int
    notification_text: str | None


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def classify_item(item: MaintenanceItemRow, *, now: datetime) -> ClassifiedItem | None:
    """Classify a maintenance item by its due status relative to *now*.

    Returns a ``ClassifiedItem`` if the item is due, overdue, upcoming, or
    never-completed; returns ``None`` if the item is not yet due and has been
    completed.

    Classification rules:
    - ``next_due_at`` is NULL and ``last_completed_at`` is NULL → never_completed
    - ``next_due_at`` is in the future within ``UPCOMING_LOOKAHEAD_DAYS`` → upcoming
    - ``next_due_at <= now`` → due / overdue / critical depending on days_overdue:
        - 0-7 days overdue → "due"
        - 8-30 days overdue → "overdue"
        - >30 days overdue → "critical"
    """
    next_due_at: datetime | None = item.get("next_due_at")
    last_completed_at: datetime | None = item.get("last_completed_at")

    # Never started: no completion and no computed due date.
    if next_due_at is None and last_completed_at is None:
        return ClassifiedItem(
            id=item["id"],
            name=item["name"],
            category=item["category"],
            interval_days=item["interval_days"],
            severity=SEVERITY_NEVER_COMPLETED,
            days_delta=0,
        )

    # Item has been completed but next_due_at is NULL — skip (no schedule data).
    if next_due_at is None:
        return None

    # Ensure timezone-aware comparison.
    if next_due_at.tzinfo is None:
        next_due_at = next_due_at.replace(tzinfo=UTC)

    delta = next_due_at - now  # positive = future, negative = past
    # Use timedelta.days for consistent floor-division behaviour on negative deltas.
    # Python's timedelta.days floors for negative values (e.g. -7h → days=-1),
    # giving correct threshold crossings without partial-day truncation errors.
    days_delta = delta.days

    if delta > timedelta(0):
        # Item is in the future.
        if delta <= timedelta(days=UPCOMING_LOOKAHEAD_DAYS):
            return ClassifiedItem(
                id=item["id"],
                name=item["name"],
                category=item["category"],
                interval_days=item["interval_days"],
                severity=SEVERITY_UPCOMING,
                days_delta=days_delta,  # positive: days remaining until due (delta > 0)
            )
        # Not yet due and beyond lookahead window — ignore.
        return None

    # Item is past due (delta <= timedelta(0)).
    days_overdue = abs(days_delta)
    if days_overdue <= DUE_MAX_DAYS:
        severity = SEVERITY_DUE
    elif days_overdue <= OVERDUE_MAX_DAYS:
        severity = SEVERITY_OVERDUE
    else:
        severity = SEVERITY_CRITICAL

    return ClassifiedItem(
        id=item["id"],
        name=item["name"],
        category=item["category"],
        interval_days=item["interval_days"],
        severity=severity,
        days_delta=-days_overdue,  # negative = overdue by N days
    )


# ---------------------------------------------------------------------------
# Notification text builder
# ---------------------------------------------------------------------------


def build_notification_text(classified: list[ClassifiedItem]) -> str:
    """Build a human-readable notification message from classified items.

    Items are grouped by severity in descending urgency order:
    critical → never_completed → overdue → due → upcoming.

    Each item shows: name, category, and days overdue / days until due.
    """
    if not classified:
        return ""

    # Group by severity.
    grouped: dict[str, list[ClassifiedItem]] = {s: [] for s in _SEVERITY_ORDER}
    for item in classified:
        grouped[item["severity"]].append(item)

    lines: list[str] = ["Home Maintenance Reminder"]
    lines.append("=" * 30)

    severity_labels: dict[str, str] = {
        SEVERITY_CRITICAL: "CRITICAL (>30 days overdue)",
        SEVERITY_NEVER_COMPLETED: "NEVER COMPLETED (initial setup needed)",
        SEVERITY_OVERDUE: "OVERDUE (8-30 days)",
        SEVERITY_DUE: "DUE (within 7 days)",
        SEVERITY_UPCOMING: "UPCOMING (next 7 days)",
    }

    for severity in _SEVERITY_ORDER:
        items = grouped[severity]
        if not items:
            continue
        lines.append(f"\n{severity_labels[severity]}:")
        for item in sorted(items, key=lambda i: i["days_delta"]):
            if severity == SEVERITY_UPCOMING:
                days_remaining = item["days_delta"]
                lines.append(
                    f"  - {item['name']} [{item['category']}] — due in {days_remaining} day(s)"
                )
            elif severity == SEVERITY_NEVER_COMPLETED:
                lines.append(f"  - {item['name']} [{item['category']}] — never completed")
            else:
                days_overdue = abs(item["days_delta"])
                lines.append(
                    f"  - {item['name']} [{item['category']}] — {days_overdue} day(s) overdue"
                )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core job implementation
# ---------------------------------------------------------------------------

# Type alias for an optional notify callable (e.g. a Telegram send function).
# Signature: async (message: str) -> None
NotifyFn = Callable[[str], Coroutine[Any, Any, None]]


async def run_maintenance_schedule_check(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
    *,
    notify_fn: NotifyFn | None = None,
    _now: datetime | None = None,
) -> MaintenanceCheckResult:
    """Check maintenance items for due/overdue/upcoming status and send reminders.

    Queries ``home.maintenance_items`` for:
    - Items where ``next_due_at <= now()`` (due or overdue)
    - Items where ``next_due_at IS NULL AND last_completed_at IS NULL`` (never started)
    - Items where ``next_due_at <= now + 7 days`` (upcoming)

    Classifies each item by overdue severity:
    - ``due``            — 0-7 days past due
    - ``overdue``        — 8-30 days past due
    - ``critical``       — more than 30 days past due
    - ``never_completed`` — no completion record, no due date
    - ``upcoming``       — due within the next 7 days

    Args:
        pool: asyncpg connection pool for the home butler's database.
        job_args: Optional job arguments (currently unused; reserved for future use).
        notify_fn: Optional async callable that delivers a notification message.
            When provided and items are found, it is called with the formatted
            notification text. When None, the notification text is logged only.
        _now: Optional override for the current time (used in unit tests).

    Returns:
        A dict with keys: ``items_checked``, ``due_count``, ``overdue_count``,
        ``critical_count``, ``upcoming_count``, ``never_completed_count``,
        ``reminders_sent``, and ``notification_text``.
    """
    del job_args  # reserved for future parameterisation

    now = _now if _now is not None else datetime.now(tz=UTC)
    lookahead = now + timedelta(days=UPCOMING_LOOKAHEAD_DAYS)

    # -------------------------------------------------------------------------
    # Query: items that are past due, never completed, or upcoming within 7 days.
    # -------------------------------------------------------------------------
    try:
        rows = await pool.fetch(
            """
            SELECT
                id::text AS id,
                name,
                category,
                interval_days,
                last_completed_at,
                next_due_at,
                notes
            FROM home.maintenance_items
            WHERE
                (next_due_at <= $1)
                OR (next_due_at IS NULL AND last_completed_at IS NULL)
                OR (next_due_at > $1 AND next_due_at <= $2)
            ORDER BY next_due_at ASC NULLS FIRST
            """,
            now,
            lookahead,
        )
    except Exception:
        logger.exception(
            "Failed to query home.maintenance_items; "
            "check that the table exists and the home schema migration has run"
        )
        raise

    items_checked = len(rows)

    # -------------------------------------------------------------------------
    # Classify each item.
    # -------------------------------------------------------------------------
    classified: list[ClassifiedItem] = []
    for row in rows:
        item = MaintenanceItemRow(
            id=row["id"],
            name=row["name"],
            category=row["category"],
            interval_days=row["interval_days"],
            last_completed_at=row["last_completed_at"],
            next_due_at=row["next_due_at"],
            notes=row["notes"],
        )
        result = classify_item(item, now=now)
        if result is not None:
            classified.append(result)

    # -------------------------------------------------------------------------
    # Count by severity.
    # -------------------------------------------------------------------------
    due_count = sum(1 for i in classified if i["severity"] == SEVERITY_DUE)
    overdue_count = sum(1 for i in classified if i["severity"] == SEVERITY_OVERDUE)
    critical_count = sum(1 for i in classified if i["severity"] == SEVERITY_CRITICAL)
    upcoming_count = sum(1 for i in classified if i["severity"] == SEVERITY_UPCOMING)
    never_completed_count = sum(1 for i in classified if i["severity"] == SEVERITY_NEVER_COMPLETED)

    # -------------------------------------------------------------------------
    # Build and send notification if there are items to report.
    # -------------------------------------------------------------------------
    reminders_sent = 0
    notification_text: str | None = None

    if classified:
        notification_text = build_notification_text(classified)

        if notify_fn is not None:
            try:
                await notify_fn(notification_text)
                reminders_sent = 1
                logger.info(
                    "Maintenance schedule check: notification sent "
                    "(%d due, %d overdue, %d critical, %d upcoming, %d never-completed)",
                    due_count,
                    overdue_count,
                    critical_count,
                    upcoming_count,
                    never_completed_count,
                )
            except Exception:
                logger.exception("Failed to send maintenance schedule notification")
        else:
            logger.info(
                "Maintenance schedule check: %d item(s) need attention "
                "(no notify_fn configured — notification text not sent)",
                len(classified),
            )
            logger.debug(
                "Maintenance schedule notification text (no notify_fn configured):\n%s",
                notification_text,
            )
    else:
        logger.info(
            "Maintenance schedule check: %d item(s) checked, none require attention",
            items_checked,
        )

    return MaintenanceCheckResult(
        items_checked=items_checked,
        due_count=due_count,
        overdue_count=overdue_count,
        critical_count=critical_count,
        upcoming_count=upcoming_count,
        never_completed_count=never_completed_count,
        reminders_sent=reminders_sent,
        notification_text=notification_text,
    )
