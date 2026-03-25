"""Deterministic job implementations for the Home butler's scheduled monitoring tasks.

These handlers replace prompt-based LLM dispatch with threshold-based
classification, memory storage, and Telegram notifications — eliminating LLM
costs for formulaic monitoring work.

Jobs read current entity state from the connector-populated ``ha_entity_snapshot``
table and load monitoring thresholds from the state store (``home:thresholds:*``),
falling back to direct HA REST API calls only for historical statistics queries.

The ``run_maintenance_schedule_check`` function is fully implemented: it queries
``home.maintenance_items`` for items that are due, overdue, or upcoming within 7
days; classifies each item by severity; builds a notification summary; and returns
a structured result.

Design reference: openspec/changes/archive/home-butler-enhancements/
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

import asyncpg

from butlers.core.state import state_get

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default thresholds (used when state store has no value)
# ---------------------------------------------------------------------------

_DEFAULT_ENERGY_THRESHOLDS: dict[str, float] = {
    "anomaly_pct": 20.0,
    "high_severity_pct": 100.0,
}

# Keywords that identify energy-related sensor entities
_ENERGY_KEYWORDS = ("energy", "power", "kwh", "consumption", "watt")

# Number of top consumers to rank and report
_TOP_N_CONSUMERS = 5


# ---------------------------------------------------------------------------
# Internal helpers
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


async def _load_energy_thresholds(pool: asyncpg.Pool) -> dict[str, float]:
    """Load energy anomaly thresholds from state store, falling back to defaults.

    Returns a dict with keys ``anomaly_pct`` and ``high_severity_pct``.
    """
    raw = await state_get(pool, "home:thresholds:energy")
    if raw is None:
        logger.warning(
            "home:thresholds:energy not found in state store — using default thresholds "
            "(anomaly_pct=%.0f%%, high_severity_pct=%.0f%%)",
            _DEFAULT_ENERGY_THRESHOLDS["anomaly_pct"],
            _DEFAULT_ENERGY_THRESHOLDS["high_severity_pct"],
        )
        return dict(_DEFAULT_ENERGY_THRESHOLDS)

    if not isinstance(raw, dict):
        logger.warning(
            "home:thresholds:energy is not a dict (got %r) — using defaults",
            type(raw).__name__,
        )
        return dict(_DEFAULT_ENERGY_THRESHOLDS)

    result = dict(_DEFAULT_ENERGY_THRESHOLDS)
    for key in ("anomaly_pct", "high_severity_pct"):
        if key in raw:
            try:
                result[key] = float(raw[key])
            except (TypeError, ValueError):
                logger.warning(
                    "home:thresholds:energy.%s is not numeric (%r) — using default %.0f",
                    key,
                    raw[key],
                    _DEFAULT_ENERGY_THRESHOLDS[key],
                )
    return result


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
            import json

            try:
                attrs = json.loads(attrs)
            except Exception:
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
    import httpx

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
            import re

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
                f"  • {item['friendly_name']}: {item['weekly_kwh']:.1f} kWh "
                f"({item['share_pct']:.0f}%)"
            )

    # Anomaly alerts
    if anomalies:
        lines.append("\n<b>⚠️ Anomaly alerts:</b>")
        for a in anomalies:
            sev = "🔴 HIGH" if a["severity"] == "high" else "🟡 Anomaly"
            lines.append(
                f"  {sev}: {a['friendly_name']} — "
                f"{a['weekly_kwh']:.1f} kWh (+{a['pct_above']:.0f}% above baseline)"
            )

    # Recommendations (up to 3)
    recs: list[str] = []
    high_severity_devices = [a for a in anomalies if a["severity"] == "high"]
    if high_severity_devices:
        recs.append(
            f"Check {high_severity_devices[0]['friendly_name']} — "
            f"consumption is more than double its baseline."
        )
    if top_consumers:
        top_name = top_consumers[0]["friendly_name"]
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
    """Send a Telegram message to the owner using credentials from the state store.

    Resolves ``telegram_bot_token`` and ``telegram_chat_id`` from the owner's
    entity_info/contact_info via ``resolve_owner_entity_info``. Silently skips
    if either credential is unavailable.
    """
    import httpx

    from butlers.credential_store import resolve_owner_entity_info

    token = await resolve_owner_entity_info(pool, "telegram_bot_token")
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
                except Exception:
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
    from butlers.credential_store import resolve_owner_entity_info

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
            import re

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
        try:
            from butlers.modules.memory.storage import store_fact

            # Sentinel embedding engine (no embeddings for deterministic jobs)
            class _NullEmbeddingEngine:
                async def embed(self, texts: list[str]) -> list[list[float]]:
                    return [[] for _ in texts]

            eng = _NullEmbeddingEngine()

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
            )
            baseline_updated = True
            logger.info(
                "run_energy_digest: stored energy_baseline fact (total_kwh=%.1f)", total_kwh
            )
        except Exception:
            logger.warning("run_energy_digest: failed to store energy_baseline fact", exc_info=True)

        # Store spike facts for anomalous devices
        for anomaly in anomalies:
            try:
                from butlers.modules.memory.storage import store_fact  # noqa: F811

                class _NullEmbeddingEngine:  # noqa: F811
                    async def embed(self, texts: list[str]) -> list[list[float]]:
                        return [[] for _ in texts]

                eng = _NullEmbeddingEngine()

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
# Device health check (stub — full implementation in a future issue)
# ---------------------------------------------------------------------------


async def run_device_health_check(pool: asyncpg.Pool) -> dict[str, Any]:
    """Stub: device health check for the home butler (no-op).

    Full implementation pending (home-deterministic-jobs feature work).
    When implemented, this will survey all HA entities for offline status and
    low battery levels, classify issues using configurable thresholds from the
    state store (``home:thresholds:battery``, ``home:thresholds:offline_hours``),
    store findings in memory, and send a Telegram notification with the results.

    Returns a zeroed summary dict with keys: ``devices_checked``,
    ``issues_found``, ``critical_count``, ``warning_count``.
    """
    logger.info("device_health_check: stub — full implementation pending")
    return {
        "devices_checked": 0,
        "issues_found": 0,
        "critical_count": 0,
        "warning_count": 0,
    }


# ---------------------------------------------------------------------------
# Environment report (stub — full implementation in a future issue)
# ---------------------------------------------------------------------------


async def run_environment_report(pool: asyncpg.Pool) -> dict[str, Any]:
    """Stub: environment report for the home butler (no-op).

    Full implementation pending (home-deterministic-jobs feature work).
    When implemented, this will read temperature, humidity, CO2, and illuminance
    sensor readings grouped by Home Assistant area from the connector-populated
    snapshot, compare against stored comfort preferences and configurable
    deviation thresholds (``home:thresholds:comfort_defaults``,
    ``home:thresholds:comfort_deviation``), store deviations in memory, and send
    a room-by-room Telegram notification.

    Returns a zeroed summary dict with keys: ``areas_checked``, ``sensors_read``,
    ``deviations_found``.
    """
    logger.info("environment_report: stub — full implementation pending")
    return {
        "areas_checked": 0,
        "sensors_read": 0,
        "deviations_found": 0,
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
