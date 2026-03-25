"""Deterministic job implementations for the Home butler's scheduled monitoring tasks.

NOTE: Most functions in this module are **stubs**. They log a message and return
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
# Constants
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
# TypedDicts
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
