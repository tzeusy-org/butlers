"""Tests for butlers.jobs.home — maintenance check and device health check.

Covers classify_item, build_notification_text, run_maintenance_schedule_check,
classify_battery, classify_offline, threshold loading, _build_health_check_notification,
run_device_health_check, and daemon registry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.jobs.home import (
    _DEFAULT_BATTERY_THRESHOLDS,
    _DEFAULT_OFFLINE_HOURS_THRESHOLDS,
    SEVERITY_CRITICAL,
    SEVERITY_DUE,
    SEVERITY_NEVER_COMPLETED,
    SEVERITY_OVERDUE,
    SEVERITY_UPCOMING,
    MaintenanceItemRow,
    _build_health_check_notification,
    _load_battery_thresholds,
    _load_offline_hours_thresholds,
    _NoOpEmbeddingEngine,
    build_notification_text,
    classify_battery,
    classify_item,
    classify_offline,
    run_device_health_check,
    run_maintenance_schedule_check,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)
_THRESHOLDS_BATTERY = _DEFAULT_BATTERY_THRESHOLDS
_THRESHOLDS_OFFLINE = _DEFAULT_OFFLINE_HOURS_THRESHOLDS


def _make_item(
    *,
    next_due_at=None,
    last_completed_at=None,
    name="Air Filter",
    category="filter",
    interval_days=90,
) -> MaintenanceItemRow:
    return MaintenanceItemRow(
        id="test-id-1",
        name=name,
        category=category,
        interval_days=interval_days,
        last_completed_at=last_completed_at,
        next_due_at=next_due_at,
        notes=None,
    )


def _make_pool(*, fetch_rows=None) -> MagicMock:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.execute = AsyncMock()
    return pool


def _make_db_row(
    *,
    next_due_at=None,
    last_completed_at=None,
    name="Air Filter",
    category="filter",
    interval_days=90,
) -> dict:
    return {
        "id": "test-id-1",
        "name": name,
        "category": category,
        "interval_days": interval_days,
        "last_completed_at": last_completed_at,
        "next_due_at": next_due_at,
        "notes": None,
    }


def _make_entity_row(
    entity_id="sensor.living_room_temp", state="22.5", attributes=None, last_updated=None
) -> MagicMock:
    if last_updated is None:
        last_updated = datetime.now(UTC) - timedelta(minutes=5)
    row = MagicMock()
    fn = entity_id.replace(".", " ").replace("_", " ")
    row.__getitem__ = lambda self, key: {
        "entity_id": entity_id,
        "state": state,
        "attributes": attributes or {"friendly_name": fn},
        "last_updated": last_updated,
    }[key]
    return row


def _make_health_pool(*, state_value=None, entity_rows=None) -> MagicMock:
    pool = MagicMock()
    # The asyncpg JSONB codec returns Python objects directly (no JSON string).
    pool.fetchval = AsyncMock(return_value=state_value)
    pool.fetch = AsyncMock(return_value=entity_rows or [])
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=conn)
    return pool


# ---------------------------------------------------------------------------
# classify_item
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "days_offset, expected_severity",
    [
        (None, SEVERITY_NEVER_COMPLETED),
        (-7, SEVERITY_DUE),  # boundary: 7 days still DUE
        (-8, SEVERITY_OVERDUE),  # boundary: 8 days = OVERDUE
        (-31, SEVERITY_CRITICAL),  # boundary: 31 days = CRITICAL
        (7, SEVERITY_UPCOMING),  # boundary: 7 days = UPCOMING
        (8, None),  # >7 days out → not surfaced
    ],
)
def test_classify_item_severity_branches(days_offset, expected_severity):
    """classify_item returns correct severity at key boundary conditions; items due
    >7 days out (and completed items with no next_due_at) return None."""
    if days_offset is None:
        item = _make_item(next_due_at=None)
    else:
        item = _make_item(next_due_at=_NOW + timedelta(days=days_offset))
    result = classify_item(item, now=_NOW)
    if expected_severity is None:
        assert result is None
    else:
        assert result is not None
        assert result["severity"] == expected_severity
    # Completed item with no next_due_at also returns None.
    if days_offset == 8:
        completed = _make_item(last_completed_at=_NOW - timedelta(days=10), next_due_at=None)
        assert classify_item(completed, now=_NOW) is None


def test_classify_item_preserves_metadata():
    """Classified item preserves name, category, interval_days, and days_delta."""
    item = _make_item(
        name="HVAC Service",
        category="hvac",
        interval_days=365,
        next_due_at=_NOW - timedelta(days=5),
    )
    result = classify_item(item, now=_NOW)
    assert result is not None
    assert result["name"] == "HVAC Service" and result["category"] == "hvac"
    assert result["days_delta"] == -5


# ---------------------------------------------------------------------------
# build_notification_text
# ---------------------------------------------------------------------------


def _classified(severity, name, days_delta=0):
    from butlers.jobs.home import ClassifiedItem

    return ClassifiedItem(
        id="t",
        name=name,
        category="filter",
        interval_days=90,
        severity=severity,
        days_delta=days_delta,
    )


def test_build_notification_text():
    """Empty → ""; groups ordered critical > overdue > due > upcoming; empty groups omitted."""
    assert build_notification_text([]) == ""

    items = [
        _classified(SEVERITY_UPCOMING, "U", 3),
        _classified(SEVERITY_DUE, "D", -2),
        _classified(SEVERITY_CRITICAL, "C", -40),
        _classified(SEVERITY_OVERDUE, "O", -15),
    ]
    text = build_notification_text(items)
    assert (
        text.index("CRITICAL")
        < text.index("OVERDUE")
        < text.index("DUE (")
        < text.index("UPCOMING")
    )

    text2 = build_notification_text([_classified(SEVERITY_CRITICAL, "X", -50)])
    assert "OVERDUE" not in text2 and "UPCOMING" not in text2


# ---------------------------------------------------------------------------
# run_maintenance_schedule_check
# ---------------------------------------------------------------------------


async def test_maintenance_check_no_items():
    """Empty DB: all counts zero, notify_fn not called."""
    pool = _make_pool(fetch_rows=[])
    notify_fn = AsyncMock()
    result = await run_maintenance_schedule_check(pool, None, notify_fn=notify_fn, _now=_NOW)
    assert result["items_checked"] == 0
    notify_fn.assert_not_called()


async def test_maintenance_check_all_severities_counted():
    """Multiple items are counted in their respective severity buckets."""
    rows = [
        _make_db_row(name="Critical", next_due_at=_NOW - timedelta(days=45)),
        _make_db_row(name="Overdue", next_due_at=_NOW - timedelta(days=15)),
        _make_db_row(name="Due", next_due_at=_NOW - timedelta(days=3)),
        _make_db_row(name="Upcoming", next_due_at=_NOW + timedelta(days=4)),
        _make_db_row(name="NeverDone"),
    ]
    pool = _make_pool(fetch_rows=rows)
    notify_fn = AsyncMock()
    result = await run_maintenance_schedule_check(pool, None, notify_fn=notify_fn, _now=_NOW)
    assert result["critical_count"] == 1 and result["overdue_count"] == 1
    assert result["due_count"] == 1 and result["upcoming_count"] == 1
    assert result["never_completed_count"] == 1 and result["items_checked"] == 5
    assert result["reminders_sent"] == 1


async def test_maintenance_check_error_propagation_and_notify_failure():
    """DB errors propagate; notify_fn failure is non-fatal."""
    pool_fail = MagicMock()
    pool_fail.fetch = AsyncMock(side_effect=Exception("connection refused"))
    with pytest.raises(Exception, match="connection refused"):
        await run_maintenance_schedule_check(pool_fail, None, _now=_NOW)

    row = _make_db_row(next_due_at=_NOW - timedelta(days=5))
    pool_ok = _make_pool(fetch_rows=[row])

    async def broken_notify(msg):
        raise RuntimeError("Telegram unavailable")

    result = await run_maintenance_schedule_check(pool_ok, None, notify_fn=broken_notify, _now=_NOW)
    assert result["due_count"] == 1 and result["reminders_sent"] == 0


# ---------------------------------------------------------------------------
# _run_home_maintenance_schedule_check_job (daemon wrapper — delivery wiring)
# ---------------------------------------------------------------------------


async def test_maintenance_wrapper_delivers_when_items_due():
    """The scheduled wrapper routes through _notify_owner_telegram and DELIVERS
    a Telegram reminder when items are due/overdue/upcoming (mirrors siblings)."""
    from butlers.scheduled_jobs import _run_home_maintenance_schedule_check_job

    pool = _make_pool(fetch_rows=[_make_db_row(next_due_at=datetime.now(UTC) - timedelta(days=5))])
    with patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock) as mock_notify:
        result = await _run_home_maintenance_schedule_check_job(pool, None)

    mock_notify.assert_awaited_once()
    # The owner pool and composed notification text are forwarded to the helper.
    sent_pool, sent_text = mock_notify.await_args.args
    assert sent_pool is pool
    assert isinstance(sent_text, str) and sent_text
    assert result["reminders_sent"] == 1 and result["due_count"] == 1


async def test_maintenance_wrapper_no_notify_when_nothing_due():
    """The wrapper does NOT notify when no items require attention."""
    from butlers.scheduled_jobs import _run_home_maintenance_schedule_check_job

    pool = _make_pool(fetch_rows=[])
    with patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock) as mock_notify:
        result = await _run_home_maintenance_schedule_check_job(pool, None)

    mock_notify.assert_not_awaited()
    assert result["reminders_sent"] == 0 and result["items_checked"] == 0


# ---------------------------------------------------------------------------
# classify_battery / classify_offline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level, expected",
    [
        (10.0, "critical"),  # boundary: ≤10 critical
        (11.0, "warning"),  # boundary: 11 warning
        (30.0, "info"),  # boundary: ≤30 info
        (31.0, None),  # above all thresholds
    ],
)
def test_classify_battery(level, expected):
    """classify_battery returns correct severity at threshold boundaries."""
    assert classify_battery(level, _THRESHOLDS_BATTERY) == expected


@pytest.mark.parametrize(
    "hours_ago, expected",
    [
        (None, "critical"),  # no timestamp = critical
        (25.0, "critical"),  # > 24h critical
        (23.0, "warning"),  # > 1h warning
        (0.5, None),  # recent = ok
    ],
)
def test_classify_offline(hours_ago, expected):
    """classify_offline returns correct severity based on hours since last change."""
    ts = None if hours_ago is None else datetime.now(UTC) - timedelta(hours=hours_ago)
    assert classify_offline(ts, _THRESHOLDS_OFFLINE) == expected


# ---------------------------------------------------------------------------
# _load_battery_thresholds / _load_offline_hours_thresholds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state_value, expected_uses_defaults",
    [
        (None, True),
        ({"critical": 5, "warning": 15, "info": 25}, False),
    ],
)
async def test_load_battery_thresholds(state_value, expected_uses_defaults):
    """_load_battery_thresholds returns defaults or parses stored values."""
    pool = _make_health_pool(state_value=state_value)
    result = await _load_battery_thresholds(pool)
    if expected_uses_defaults:
        assert result == _DEFAULT_BATTERY_THRESHOLDS
    else:
        assert isinstance(result, dict) and "critical" in result


async def test_load_thresholds_invalid_per_key_falls_back():
    """Invalid per-key values fall back to defaults for that key only."""
    pool = _make_health_pool(state_value={"critical": None, "warning": 15, "info": 25})
    result = await _load_battery_thresholds(pool)
    assert result["critical"] == _DEFAULT_BATTERY_THRESHOLDS["critical"]
    assert result["warning"] == 15

    pool2 = _make_health_pool(state_value={"critical": 48, "warning": None})
    result2 = await _load_offline_hours_thresholds(pool2)
    assert result2["warning"] == _DEFAULT_OFFLINE_HOURS_THRESHOLDS["warning"]
    assert result2["critical"] == 48


# ---------------------------------------------------------------------------
# _build_health_check_notification
# ---------------------------------------------------------------------------


def _issue(name, issue_type, severity, description):
    return {
        "entity_id": f"sensor.{name}",
        "friendly_name": name,
        "issue_type": issue_type,
        "severity": severity,
        "value": "unavailable" if issue_type == "offline" else 5.0,
        "description": description,
    }


def test_build_health_check_notification():
    """All-clear shows checkmark; critical listed before warning."""
    msg = _build_health_check_notification(
        issues=[], devices_checked=10, critical_count=0, warning_count=0, info_count=0
    )
    assert "\u2705" in msg and "10" in msg

    crit = _issue("basement_sensor", "offline", "critical", "offline")
    warn = _issue("garage_battery", "battery", "warning", "battery at 15%")
    msg2 = _build_health_check_notification(
        issues=[warn, crit], devices_checked=5, critical_count=1, warning_count=1, info_count=0
    )
    assert "\U0001f534" in msg2 and "\U0001f7e0" in msg2
    assert msg2.index("\U0001f534") < msg2.index("\U0001f7e0")


# ---------------------------------------------------------------------------
# run_device_health_check
# ---------------------------------------------------------------------------


async def test_run_device_health_check_empty_and_healthy():
    """Empty snapshot returns error; healthy devices have 0 issues."""
    pool = _make_health_pool(entity_rows=[])
    with patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock):
        result = await run_device_health_check(pool, None)
    assert result == {"error": "no_entity_snapshot"}

    rows = [
        _make_entity_row("sensor.temp", state="22"),
        _make_entity_row("light.kitchen", state="off"),
    ]
    pool2 = _make_health_pool(entity_rows=rows)
    with (
        patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
        patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock),
    ):
        result2 = await run_device_health_check(pool2, None)
    assert result2["devices_checked"] == 2 and result2["issues_found"] == 0


async def test_run_device_health_check_battery_and_non_battery():
    """Critical battery sensor raises critical_count; non-battery with low state not flagged."""
    rows = [
        _make_entity_row(
            "sensor.basement_battery", state="8", attributes={"friendly_name": "Basement Battery"}
        )
    ]
    pool = _make_health_pool(entity_rows=rows)
    with (
        patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
        patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock),
    ):
        result = await run_device_health_check(pool, None)
    assert result["critical_count"] == 1

    rows_nb = [_make_entity_row("sensor.living_room_temp", state="5")]
    pool_nb = _make_health_pool(entity_rows=rows_nb)
    with (
        patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
        patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock),
    ):
        result_nb = await run_device_health_check(pool_nb, None)
    assert result_nb["issues_found"] == 0


async def test_store_device_fact_sets_home_source_butler():
    """Device health facts are tagged with the home butler provenance."""
    pool = MagicMock()
    with patch("butlers.jobs.home.store_fact", new_callable=AsyncMock) as mock_store:
        from butlers.jobs.home import _store_device_fact

        await _store_device_fact(
            pool,
            subject="sensor-garage-battery",
            content="Garage Battery: battery at 8% — critical severity",
            importance=8.0,
            tags=["maintenance", "battery"],
        )

    assert mock_store.await_args.kwargs["source_butler"] == "home"
    assert mock_store.await_args.kwargs["embedding_engine"].model_name == "deterministic-noop"


def test_noop_embedding_engine_matches_store_fact_interface():
    """_NoOpEmbeddingEngine exposes the fields store_fact reads from real engines."""
    eng = _NoOpEmbeddingEngine()

    assert eng.model_name == "deterministic-noop"
    assert eng.embed("hello") == [0.0] * 384
    assert eng.embed_batch(["a", "b"]) == [[0.0] * 384, [0.0] * 384]


# ---------------------------------------------------------------------------
# Daemon registry
# ---------------------------------------------------------------------------


# device_health_check registration is asserted by the canonical
# test_all_home_deterministic_jobs_registered in test_home_energy_digest.py.
