"""Deterministic job implementations for the Home butler's scheduled monitoring tasks.

NOTE: All functions in this module are **stubs**. They log a message and return
zeroed summary results without performing any real monitoring, memory writes, or
Telegram notifications. Full implementations will be added in the
home-deterministic-jobs feature work.

These handlers are intended to replace prompt-based LLM dispatch with
threshold-based classification, memory storage, and Telegram notifications —
eliminating LLM costs for formulaic monitoring work.

When fully implemented, jobs will read current entity state from the
connector-populated ``ha_entity_snapshot`` table (or its SPO successor) and load
monitoring thresholds from the state store (``home:thresholds:*``), falling back
to the HA REST API only for historical statistics.

Design reference: openspec/specs/home-deterministic-jobs/spec.md
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Device health check
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
# Environment report
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
# Energy digest
# ---------------------------------------------------------------------------


async def run_energy_digest(pool: asyncpg.Pool) -> dict[str, Any]:
    """Stub: weekly energy digest for the home butler (no-op).

    Full implementation pending (home-deterministic-jobs feature work).
    When implemented, this will discover energy-related sensor entities from the
    connector-populated snapshot, fetch weekly historical statistics via the HA
    REST API (``recorder/get_statistics_during_period``), compute top consumers
    and percentage deviation from stored baselines using configurable anomaly
    thresholds (``home:thresholds:energy``), update baseline memory facts, and
    send a structured weekly digest via Telegram.

    Returns a zeroed summary dict with keys: ``total_kwh``, ``devices_ranked``,
    ``anomalies_found``, ``baseline_updated``.
    """
    logger.info("energy_digest: stub — full implementation pending")
    return {
        "total_kwh": 0.0,
        "devices_ranked": 0,
        "anomalies_found": 0,
        "baseline_updated": False,
    }


# ---------------------------------------------------------------------------
# Maintenance schedule check
# ---------------------------------------------------------------------------


async def run_maintenance_schedule_check(pool: asyncpg.Pool) -> dict[str, Any]:
    """Stub: maintenance schedule check for the home butler (no-op).

    Full implementation pending (home-deterministic-jobs feature work).
    When implemented, this will query all home maintenance items (stored via the
    ``ha_maintenance_*`` tools), identify items that are due or overdue, and send
    a Telegram notification listing them with recommended actions.

    Returns a zeroed summary dict with keys: ``items_checked``, ``due_count``,
    ``overdue_count``.
    """
    logger.info("maintenance_schedule_check: stub — full implementation pending")
    return {
        "items_checked": 0,
        "due_count": 0,
        "overdue_count": 0,
    }
