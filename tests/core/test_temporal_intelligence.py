"""Tests for temporal intelligence features — condensed.

Covers tasks 12.1–12.11:
- 12.1 Deadline validation
- 12.2 Deadline countdown and threshold firing
- 12.3 Deadline status state machine
- 12.4 Deadline dependency blocking
- 12.5 Event chain action schema and materialization
- 12.6 Event chain depth limit
- 12.7 Seasonal period active detection
- 12.8 Quiet hours enforcement
- 12.9 Deferred notification flush
- 12.10 Per-channel quiet hours overrides
- 12.11 tick() integration with all three new passes
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest

pytestmark = [pytest.mark.unit]

docker_available = shutil.which("docker") is not None


def _make_threshold(days_before: int, severity: str = "info") -> dict[str, Any]:
    return {"days_before": days_before, "severity": severity}


def _future_date(days: int = 30) -> date:
    return (datetime.now(UTC) + timedelta(days=days)).date()


def _past_date(days: int = 1) -> date:
    return (datetime.now(UTC) - timedelta(days=days)).date()


# ---------------------------------------------------------------------------
# 12.1 — Deadline validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target_date,lead_time_days,thresholds,match",
    [
        (lambda: _past_date(10), 7, [_make_threshold(7)], "future"),  # past date
        (lambda: datetime.now(UTC).date(), 1, [_make_threshold(1)], "future"),  # today
        (lambda: _future_date(30), 30, [], "threshold"),  # empty thresholds
        (lambda: _future_date(60), 14, [_make_threshold(30, "info")], "lead_time_days|days_before"),
        (lambda: _future_date(60), 0, [_make_threshold(1)], None),  # non-positive lead time
    ],
)
def test_deadline_validation_invalid_raises(target_date, lead_time_days, thresholds, match):
    from butlers.core.temporal.deadlines import validate_deadline_input

    with pytest.raises(ValueError, match=match) if match else pytest.raises(ValueError):
        validate_deadline_input(
            target_date=target_date(),
            lead_time_days=lead_time_days,
            alert_thresholds=thresholds,
        )


# ---------------------------------------------------------------------------
# 12.2 — Deadline countdown and threshold firing
# ---------------------------------------------------------------------------


def test_deadline_countdown_and_threshold_firing():
    """Valid deadline does not raise; days_remaining correct; threshold fires when due or past."""
    from butlers.core.temporal.deadlines import (
        compute_days_remaining,
        find_unfired_threshold,
        validate_deadline_input,
    )

    # Valid input does not raise
    validate_deadline_input(
        target_date=_future_date(60),
        lead_time_days=42,
        alert_thresholds=[
            _make_threshold(42, "info"),
            _make_threshold(14, "warning"),
            _make_threshold(3, "critical"),
        ],
    )

    target = _future_date(42)
    result = compute_days_remaining(target_date=target)
    assert 41 <= result <= 42

    thresholds = [_make_threshold(42, "info"), _make_threshold(14, "warning")]
    # Fires exactly at threshold
    match = find_unfired_threshold(42, thresholds, [])
    assert match is not None and match["days_before"] == 42

    # Fires past threshold (missed downtime window)
    match2 = find_unfired_threshold(12, [_make_threshold(14, "warning")], [])
    assert match2 is not None and match2["days_before"] == 14

    # Already-fired excluded
    assert find_unfired_threshold(42, thresholds, [_make_threshold(42, "info")]) is None

    # No threshold above current days_remaining
    assert find_unfired_threshold(30, thresholds, []) is None

    # Most urgent selected (smallest days_before not yet fired)
    all_t = [
        _make_threshold(42, "info"),
        _make_threshold(14, "warning"),
        _make_threshold(3, "critical"),
    ]  # noqa: E501
    fired = [_make_threshold(42, "info"), _make_threshold(14, "warning")]
    match3 = find_unfired_threshold(3, all_t, fired)
    assert match3 is not None and match3["days_before"] == 3


# ---------------------------------------------------------------------------
# 12.3 — Deadline status state machine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "current,severity,expected",
    [
        ("pending", "info", "alerted"),
        ("pending", "critical", "escalated"),
        ("alerted", "critical", "escalated"),
        ("completed", "warning", "completed"),  # terminal
        ("expired", "warning", "expired"),  # terminal
    ],
)
def test_deadline_status_transitions(current, severity, expected):
    from butlers.core.temporal.deadlines import compute_next_deadline_status

    assert compute_next_deadline_status(current, _make_threshold(7, severity)) == expected


@pytest.mark.parametrize(
    "current,target_fn,exp_status,exp_disable",
    [
        ("alerted", lambda: _past_date(1), "expired", True),
        ("pending", lambda: _past_date(1), "expired", True),
        ("completed", lambda: _past_date(5), "completed", False),
        ("alerted", lambda: _future_date(10), "alerted", False),
    ],
)
def test_deadline_expiry_transition(current, target_fn, exp_status, exp_disable):
    from butlers.core.temporal.deadlines import compute_expiry_transition

    status, disable = compute_expiry_transition(current, target_fn())
    assert status == exp_status
    assert disable is exp_disable


# ---------------------------------------------------------------------------
# 12.4 — Deadline dependency blocking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dep_statuses,expected_blocked",
    [
        ({"a": "pending"}, True),
        ({"a": "completed"}, False),
        ({}, False),  # no deps
        ({"a": "completed", "b": "alerted"}, True),
        ({"a": "alerted"}, True),
        ({"a": "expired"}, True),
    ],
)
def test_deadline_dependency_blocking(dep_statuses, expected_blocked):
    from butlers.core.temporal.deadlines import is_deadline_blocked

    deps = list(dep_statuses.keys())
    assert (
        is_deadline_blocked(depends_on=deps, dependency_statuses=dep_statuses) is expected_blocked
    )


# ---------------------------------------------------------------------------
# 12.5 — Event chain action schema and materialization
# ---------------------------------------------------------------------------


def test_event_chain_action_schema_valid():
    """Valid prompt and job actions pass validation."""
    from butlers.core.temporal.event_chains import validate_chain_actions

    validate_chain_actions([{"action_type": "prompt", "delay_minutes": 0, "prompt": "Log visit"}])
    validate_chain_actions([{"action_type": "job", "delay_minutes": 60, "job_name": "archive"}])


@pytest.mark.parametrize(
    "actions,match",
    [
        ([{"action_type": "webhook", "delay_minutes": 0, "url": "x"}], "action_type|webhook"),
        ([{"action_type": "prompt", "delay_minutes": 0}], "prompt"),
        ([{"action_type": "job", "delay_minutes": 0}], "job_name"),
        ([{"action_type": "prompt", "delay_minutes": -5, "prompt": "x"}], "delay_minutes"),
        ([], "action"),
    ],
)
def test_event_chain_action_schema_invalid_raises(actions, match):
    from butlers.core.temporal.event_chains import validate_chain_actions

    with pytest.raises(ValueError, match=match):
        validate_chain_actions(actions)


def test_event_chain_materialization():
    """Materialized tasks have correct names, dispatch_mode, timing, source, trigger_source."""
    from butlers.core.temporal.event_chains import materialize_chain_actions

    now = datetime.now(UTC)
    actions = [
        {"action_type": "prompt", "delay_minutes": 0, "prompt": "Immediate"},
        {
            "action_type": "job",
            "delay_minutes": 60,
            "job_name": "archive",
            "job_args": {"dry_run": False},
        },
    ]
    tasks = materialize_chain_actions(chain_name="post-visit", actions=actions, fired_at=now)

    assert len(tasks) == 2
    assert tasks[0]["name"] == "chain:post-visit:0"
    assert tasks[0]["source"] == "chain"
    assert tasks[0]["dispatch_mode"] == "prompt"
    assert tasks[0]["prompt"] == "Immediate"
    assert tasks[0].get("trigger_source") == "chain:post-visit"
    assert tasks[0]["until_at"] is not None and tasks[0]["until_at"] > tasks[0]["next_run_at"]

    assert tasks[1]["name"] == "chain:post-visit:1"
    assert tasks[1]["dispatch_mode"] == "job"
    assert tasks[1]["job_name"] == "archive"
    assert tasks[1]["job_args"] == {"dry_run": False}
    expected_delayed = now + timedelta(minutes=60)
    assert abs((tasks[1]["next_run_at"] - expected_delayed).total_seconds()) < 2


# ---------------------------------------------------------------------------
# 12.6 — Event chain depth limit
# ---------------------------------------------------------------------------


def test_event_chain_depth_limit_and_warning(caplog) -> None:
    """Depth > 2 blocks firing; exceeded depth logs a warning referencing the chain name."""
    import logging

    from butlers.core.temporal.event_chains import should_fire_chain

    # Depth-limit boundary cases
    assert should_fire_chain(chain_depth=0) is True
    assert should_fire_chain(chain_depth=2) is True
    assert should_fire_chain(chain_depth=3) is False
    assert should_fire_chain(chain_depth=4) is False

    # Warning logged with chain name when depth exceeded
    with caplog.at_level(logging.WARNING, logger="butlers.core.temporal.event_chains"):
        result = should_fire_chain(chain_depth=3, chain_name="cascade-chain")
    assert result is False
    assert "cascade-chain" in caplog.text or "depth" in caplog.text.lower()


# ---------------------------------------------------------------------------
# 12.7 — Seasonal period active detection
# ---------------------------------------------------------------------------


def _make_period(name, sm, sd, em, ed, enabled=True):
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "start_month": sm,
        "start_day": sd,
        "end_month": em,
        "end_day": ed,
        "enabled": enabled,
        "period_type": "annual",
        "butler_name": "test-butler",
    }


@pytest.mark.parametrize(
    "period_args,today,expected",
    [
        (("tax", 1, 1, 4, 15), date(2026, 3, 15), True),  # same-year active
        (("tax", 1, 1, 4, 15), date(2026, 6, 1), False),  # outside range
        (("winter", 11, 15, 1, 10), date(2026, 12, 20), True),  # cross-year active Dec
        (("winter", 11, 15, 1, 10), date(2026, 1, 5), True),  # cross-year active Jan
        (("winter", 11, 15, 1, 10), date(2026, 2, 1), False),  # cross-year inactive Feb
        (("disabled", 1, 1, 4, 15), date(2026, 3, 15), False),  # disabled
    ],
)
def test_is_period_active(period_args, today, expected):
    from butlers.core.temporal.seasonal import is_period_active

    name, *rest = period_args
    enabled = name != "disabled"
    period = _make_period(name, *rest, enabled=enabled)
    assert is_period_active(period, today=today) is expected


def test_filter_active_periods_and_date_validation():
    from butlers.core.temporal.seasonal import filter_active_periods, validate_period_dates

    # Multiple concurrent periods in Jan
    periods = [
        _make_period("tax", 1, 1, 4, 15),
        _make_period("winter", 11, 15, 1, 31),
        _make_period("dis", 1, 1, 12, 31, enabled=False),
    ]
    active = filter_active_periods(periods, today=date(2026, 1, 20))
    names = [p["name"] for p in active]
    assert "tax" in names and "winter" in names and "dis" not in names
    assert filter_active_periods([], today=date(2026, 6, 1)) == []

    # Date validation
    with pytest.raises(ValueError, match="February|invalid|date"):
        validate_period_dates(start_month=2, start_day=30, end_month=3, end_day=15)
    validate_period_dates(start_month=1, start_day=1, end_month=4, end_day=15)  # valid


# ---------------------------------------------------------------------------
# 12.8 — Quiet hours enforcement
# ---------------------------------------------------------------------------


def _make_prefs(qs="22:00", qe="07:00", tz="UTC", overrides=None):
    return {
        "quiet_hours_start": qs,
        "quiet_hours_end": qe,
        "timezone": tz,
        "batch_low_priority": True,
        "batch_delivery_time": "07:00",
        "override_channels": overrides or {},
    }


@pytest.mark.parametrize(
    "priority,t,expected_defer",
    [
        ("high", time(23, 30), False),  # high bypasses quiet hours
        ("medium", time(1, 0), True),  # medium deferred inside
        ("low", time(3, 30), True),  # low deferred inside
        ("medium", time(14, 0), False),  # outside quiet hours
        ("low", time(14, 0), False),  # outside quiet hours
    ],
)
def test_quiet_hours_priority_defer(priority, t, expected_defer):
    from butlers.core.temporal.delivery import should_defer_notification

    assert should_defer_notification(priority, t, _make_prefs()) is expected_defer
    # None prefs never defers regardless of time
    assert should_defer_notification("medium", time(2, 0), None) is False


@pytest.mark.parametrize(
    "t,qs,qe,expected",
    [
        (time(23, 30), time(22, 0), time(7, 0), True),  # overnight active
        (time(0, 30), time(22, 0), time(7, 0), True),
        (time(6, 59), time(22, 0), time(7, 0), True),
        (time(14, 0), time(22, 0), time(7, 0), False),  # overnight inactive
        (time(7, 1), time(22, 0), time(7, 0), False),
        (time(10, 0), time(8, 0), time(12, 0), True),  # same-day active
        (time(13, 0), time(8, 0), time(12, 0), False),  # same-day inactive
    ],
)
def test_is_in_quiet_hours(t, qs, qe, expected):
    from butlers.core.temporal.delivery import is_in_quiet_hours

    assert is_in_quiet_hours(t, qs, qe) is expected


@pytest.mark.parametrize(
    "now_dt,exp_date,exp_hour",
    [
        (datetime(2026, 1, 15, 23, 0, tzinfo=UTC), date(2026, 1, 16), 7),  # batch tomorrow
        (datetime(2026, 1, 15, 4, 0, tzinfo=UTC), date(2026, 1, 15), 7),  # batch today
    ],
)
def test_compute_deliver_at_utc(now_dt, exp_date, exp_hour):
    from butlers.core.temporal.delivery import compute_deliver_at

    result = compute_deliver_at(_make_prefs("UTC"), now_dt)
    assert result.date() == exp_date and result.hour == exp_hour
    assert result.utcoffset().total_seconds() == 0  # UTC-aware
    # Invalid tz and naive datetime raise ValueError
    with pytest.raises(ValueError, match="Unknown timezone"):
        compute_deliver_at(_make_prefs(tz="Invalid/Zone"), datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone-aware"):
        compute_deliver_at(_make_prefs(), datetime(2026, 1, 15, 12, 0))


# ---------------------------------------------------------------------------
# 12.9 — Deferred notification flush
# ---------------------------------------------------------------------------


def _make_deferred(status="pending", deliver_at=None, deferred_at=None):
    now = datetime.now(UTC)
    return {
        "id": str(uuid.uuid4()),
        "butler_name": "tb",
        "channel": "telegram",
        "message": "msg",
        "priority": "medium",
        "envelope": {"schema_version": "notify.v1"},
        "deferred_at": deferred_at or now - timedelta(hours=2),
        "deliver_at": deliver_at or now - timedelta(minutes=5),
        "status": status,
        "delivered_at": None,
    }


@pytest.mark.parametrize(
    "status,deliver_offset_min,expected_due",
    [
        ("pending", -1, True),  # past deliver_at → due
        ("pending", 120, False),  # future deliver_at → not due
        ("delivered", -1, False),
        ("expired", -120, False),
    ],
)
def test_is_notification_due(status, deliver_offset_min, expected_due):
    from butlers.core.temporal.delivery import is_notification_due

    now = datetime.now(UTC)
    notif = _make_deferred(status=status, deliver_at=now + timedelta(minutes=deliver_offset_min))
    assert is_notification_due(notif, now=now) is expected_due


def test_notification_expiry_and_delivery_result():
    from butlers.core.temporal.delivery import (
        compute_delivery_result,
        filter_due_notifications,
        filter_expired_notifications,
        is_notification_expired,
    )

    now = datetime.now(UTC)
    assert is_notification_expired(_make_deferred(deliver_at=now - timedelta(hours=25)), now=now)
    assert not is_notification_expired(
        _make_deferred(deliver_at=now - timedelta(hours=23)), now=now
    )

    assert compute_delivery_result(False)["status"] == "pending"
    result = compute_delivery_result(True, delivered_at=now)
    assert result["status"] == "delivered" and result["delivered_at"] == now

    notifications = [
        _make_deferred("pending", now - timedelta(minutes=5)),
        _make_deferred("pending", now + timedelta(hours=1)),
        _make_deferred("delivered", now - timedelta(minutes=1)),
        _make_deferred("expired", now - timedelta(hours=30)),
    ]
    assert len(filter_due_notifications(notifications, now=now)) == 1
    assert (
        len(
            filter_expired_notifications(
                [
                    _make_deferred("pending", now - timedelta(hours=25)),
                    _make_deferred("pending", now - timedelta(hours=10)),
                ],
                now=now,
            )
        )
        == 1
    )


# ---------------------------------------------------------------------------
# 12.10 — Per-channel quiet hours overrides
# ---------------------------------------------------------------------------


def test_per_channel_quiet_hours_overrides():
    from butlers.core.temporal.delivery import (
        resolve_effective_quiet_hours,
        should_defer_notification,
    )

    prefs = {
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
        "timezone": "UTC",
        "batch_low_priority": True,
        "batch_delivery_time": "07:00",
        "override_channels": {"email": {"quiet_hours_start": "20:00", "quiet_hours_end": "09:00"}},
    }
    # Email uses override
    eff = resolve_effective_quiet_hours("email", prefs)
    assert eff["quiet_hours_start"] == "20:00" and eff["quiet_hours_end"] == "09:00"

    # Telegram uses defaults
    eff2 = resolve_effective_quiet_hours("telegram", prefs)
    assert eff2["quiet_hours_start"] == "22:00" and eff2["quiet_hours_end"] == "07:00"

    # 21:00 inside email override, outside default → email deferred, telegram not
    assert should_defer_notification("medium", time(21, 0), prefs, channel="email")
    assert not should_defer_notification("medium", time(21, 0), prefs, channel="telegram")

    # Empty overrides → defaults
    prefs2 = {**prefs, "override_channels": {}}
    eff3 = resolve_effective_quiet_hours("email", prefs2)
    assert eff3["quiet_hours_start"] == "22:00"


# ---------------------------------------------------------------------------
# 12.11 — tick() integration
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestTickIntegration:
    """Integration tests for tick() with deadline, event chain, and deferred notification passes."""

    @pytest.fixture(autouse=True)
    def reset_otel_state(self):
        import unittest.mock

        from opentelemetry import trace

        with (
            unittest.mock.patch.object(trace, "_TRACER_PROVIDER_SET_ONCE", trace.Once()),
            unittest.mock.patch.object(trace, "_TRACER_PROVIDER", None),
        ):
            yield

    _SCHEDULED_TASKS_DDL = """
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT UNIQUE NOT NULL,
            cron TEXT NOT NULL DEFAULT '* * * * *',
            prompt TEXT,
            dispatch_mode TEXT NOT NULL DEFAULT 'prompt',
            job_name TEXT,
            job_args JSONB,
            complexity TEXT DEFAULT 'medium',
            timezone TEXT NOT NULL DEFAULT 'UTC',
            start_at TIMESTAMPTZ,
            end_at TIMESTAMPTZ,
            until_at TIMESTAMPTZ,
            display_title TEXT,
            calendar_event_id TEXT,
            source TEXT NOT NULL DEFAULT 'db',
            enabled BOOLEAN NOT NULL DEFAULT true,
            next_run_at TIMESTAMPTZ,
            last_run_at TIMESTAMPTZ,
            last_result JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            task_type TEXT NOT NULL DEFAULT 'cron',
            target_date DATE,
            lead_time_days INTEGER,
            alert_thresholds JSONB,
            deadline_status TEXT,
            fired_thresholds JSONB,
            depends_on JSONB
        )
    """

    _EVENT_CHAINS_DDL = """
        CREATE TABLE IF NOT EXISTS event_chains (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            trigger_reference TEXT NOT NULL,
            actions JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            butler_name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """

    _DEFERRED_NOTIFICATIONS_DDL = """
        CREATE TABLE IF NOT EXISTS deferred_notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name TEXT NOT NULL,
            channel TEXT NOT NULL,
            message TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'medium',
            envelope JSONB NOT NULL,
            deferred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deliver_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            delivered_at TIMESTAMPTZ
        )
    """

    _SEASONAL_PERIODS_DDL = """
        CREATE TABLE IF NOT EXISTS seasonal_periods (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            period_type TEXT NOT NULL DEFAULT 'annual',
            start_month INTEGER NOT NULL,
            start_day INTEGER NOT NULL,
            end_month INTEGER NOT NULL,
            end_day INTEGER NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            metadata JSONB,
            butler_name TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """

    @pytest.fixture
    async def pool(self, postgres_container):
        import asyncpg

        db_name = f"test_{uuid.uuid4().hex[:12]}"
        admin_conn = await asyncpg.connect(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database="postgres",
        )
        try:
            await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin_conn.close()

        p = await asyncpg.create_pool(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database=db_name,
            min_size=1,
            max_size=3,
        )
        await p.execute(self._SCHEDULED_TASKS_DDL)
        await p.execute(self._EVENT_CHAINS_DDL)
        await p.execute(self._DEFERRED_NOTIFICATIONS_DDL)
        await p.execute(self._SEASONAL_PERIODS_DDL)
        yield p
        await p.close()

    async def test_tick_deadline_pass_fires_due_threshold(self, pool):
        """tick() deadline pass dispatches deadline task and sets span attributes."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from butlers.core.scheduler import tick

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        now = datetime.now(UTC)
        target = _future_date(30)
        await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES
                ($1, '* * * * *', $2, 'prompt', 'deadline', $3, 30,
                 '[{"days_before": 30, "severity": "info"}]',
                 'pending', '[]', $4, true)
            RETURNING id
            """,
            "test-deadline",
            "Review visa renewal checklist",
            target,
            now,
        )

        dispatched_calls = []

        async def capture_dispatch(**kwargs):
            dispatched_calls.append(kwargs)

        await tick(pool, capture_dispatch)

        prompts = [c.get("prompt", "") for c in dispatched_calls]
        assert any("visa" in p.lower() or "Review" in p for p in prompts)

        spans = exporter.get_finished_spans()
        tick_span = next((s for s in spans if s.name == "butler.tick"), None)
        assert tick_span is not None
        attrs = dict(tick_span.attributes or {})
        assert "deadlines_evaluated" in attrs

    async def test_tick_deadline_pass_includes_deadline_status_in_prompt(self, pool):
        """tick() deadline pass includes deadline_status in dispatched prompt."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        target = _future_date(14)
        await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES
                ($1, '* * * * *', $2, 'prompt', 'deadline', $3, 30,
                 '[{"days_before": 14, "severity": "warning"}]',
                 'alerted', '[]', $4, true)
            RETURNING id
            """,
            "status-deadline",
            "Submit final report",
            target,
            now,
        )

        dispatched_calls = []

        async def capture_dispatch(**kwargs):
            dispatched_calls.append(kwargs)

        await tick(pool, capture_dispatch)
        prompts = [c.get("prompt", "") for c in dispatched_calls]
        assert any("deadline_status=alerted" in p for p in prompts)

    async def test_tick_deadline_span_attribute_dispatched(self, pool):
        """tick() sets 'deadline_dispatched' span attribute > 0 for due deadline."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from butlers.core.scheduler import tick

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        now = datetime.now(UTC)
        target = _future_date(7)
        alert_thresholds = json.dumps([{"days_before": 7, "severity": "info"}])
        await pool.execute(
            """
            INSERT INTO scheduled_tasks
            (name, task_type, dispatch_mode, prompt, target_date, lead_time_days,
             alert_thresholds, deadline_status, fired_thresholds, next_run_at, enabled)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            "test-deadline",
            "deadline",
            "prompt",
            "Test deadline dispatch",
            target,
            7,
            alert_thresholds,
            "pending",
            json.dumps([]),
            now,
            True,
        )

        dispatched_calls = []

        async def capture_dispatch(**kwargs):
            dispatched_calls.append(kwargs)

        await tick(pool, capture_dispatch)
        spans = exporter.get_finished_spans()
        tick_span = next((s for s in spans if s.name == "butler.tick"), None)
        assert tick_span is not None
        attrs = dict(tick_span.attributes or {})
        assert "deadline_dispatched" in attrs
        assert attrs["deadline_dispatched"] > 0

    async def test_tick_deferred_notification_pass_delivers_due_notifications(self, pool):
        """tick() delivers pending notifications past deliver_at via notify_fn (not dispatch_fn)."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        stored_envelope = {"schema_version": "notify.v1", "delivery": {"channel": "telegram"}}
        notif_id = await pool.fetchval(
            """
            INSERT INTO deferred_notifications
                (butler_name, channel, message, priority, envelope, deliver_at, status)
            VALUES
                ('test-butler', 'telegram', 'Queued message', 'medium',
                 $2::jsonb, $1, 'pending')
            RETURNING id
            """,
            now - timedelta(minutes=5),
            json.dumps(stored_envelope),
        )

        notify_calls = []
        dispatch_calls = []

        async def capture_notify_fn(envelope: dict) -> None:
            notify_calls.append(envelope)

        async def capture_dispatch(**kwargs):
            dispatch_calls.append(kwargs)

        await tick(pool, capture_dispatch, notify_fn=capture_notify_fn)

        row = await pool.fetchrow(
            "SELECT status, delivered_at FROM deferred_notifications WHERE id = $1", notif_id
        )
        assert row["status"] == "delivered" and row["delivered_at"] is not None
        assert len(notify_calls) == 1
        assert notify_calls[0]["schema_version"] == "notify.v1"
        assert dispatch_calls == []

    async def test_tick_deferred_notification_span_and_expiry(self, pool):
        """tick() sets 'deferred_flushed' span and marks old pending notifications expired."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from butlers.core.scheduler import tick

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        now = datetime.now(UTC)
        notif_id = await pool.fetchval(
            """
            INSERT INTO deferred_notifications
                (butler_name, channel, message, priority, envelope, deliver_at, status)
            VALUES
                ('test-butler', 'telegram', 'Very old message', 'low',
                 '{"schema_version": "notify.v1"}'::jsonb, $1, 'pending')
            RETURNING id
            """,
            now - timedelta(hours=25),
        )

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        row = await pool.fetchrow(
            "SELECT status FROM deferred_notifications WHERE id = $1", notif_id
        )
        assert row["status"] == "expired"

        spans = exporter.get_finished_spans()
        tick_span = next((s for s in spans if s.name == "butler.tick"), None)
        assert tick_span is not None
        assert "deferred_flushed" in dict(tick_span.attributes or {})

    async def test_tick_event_chain_pass_fires_calendar_triggered_chain(self, pool):
        """tick() fires chain when calendar event has ended; sets chains_fired span attribute."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from butlers.core.scheduler import tick

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        now = datetime.now(UTC)
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_projection (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                butler_name TEXT NOT NULL,
                event_id TEXT NOT NULL,
                title TEXT,
                start_at TIMESTAMPTZ,
                end_at TIMESTAMPTZ,
                chain_triggered BOOLEAN NOT NULL DEFAULT false
            )
            """
        )
        event_id = "google-event-abc123"
        await pool.execute(
            """
            INSERT INTO calendar_projection
                (butler_name, event_id, title, start_at, end_at, chain_triggered)
            VALUES ('test-butler', $1, 'Dentist', $2, $3, false)
            """,
            event_id,
            now - timedelta(hours=1),
            now - timedelta(minutes=2),
        )
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES
                ('post-dentist', 'calendar_event_end', $1,
                 '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Log visit"}]',
                 'active', 'test-butler')
            """,
            event_id,
        )

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        chain_row = await pool.fetchrow(
            "SELECT status FROM event_chains WHERE name = 'post-dentist'"
        )
        assert chain_row["status"] == "fired"

        spans = exporter.get_finished_spans()
        tick_span = next((s for s in spans if s.name == "butler.tick"), None)
        assert tick_span is not None
        assert "chains_fired" in dict(tick_span.attributes or {})

    async def test_tick_event_chain_pass_fires_deadline_passed_chain_on_expired(self, pool):
        """tick() fires deadline_passed chain when referenced deadline expires."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        past_target = _past_date(1)

        deadline_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES
                ('tax-filing', '0 0 * * *', 'File taxes', 'prompt', 'deadline', $1,
                 30, '[{"days_before": 30, "severity": "info"}]',
                 'alerted', '[{"days_before": 30, "severity": "info"}]', $2, true)
            RETURNING id
            """,
            past_target,
            now,
        )

        chain_name = "post-tax-filing-archive"
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES
                ($1, 'deadline_passed', $2,
                 '[{"action_type": "job", "delay_minutes": 0, "job_name": "archive_tax"}]'::jsonb,
                 'active', 'test-butler')
            """,
            chain_name,
            str(deadline_id),
        )

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        dl_row = await pool.fetchrow(
            "SELECT deadline_status FROM scheduled_tasks WHERE name = 'tax-filing'"
        )
        assert dl_row["deadline_status"] == "expired"

        chain_row = await pool.fetchrow(
            "SELECT status FROM event_chains WHERE name = $1", chain_name
        )
        assert chain_row["status"] == "fired"

        task_row = await pool.fetchrow(
            "SELECT name, source FROM scheduled_tasks WHERE name = $1",
            f"chain:{chain_name}:0",
        )
        assert task_row is not None and task_row["source"] == "chain"

    async def test_tick_event_chain_pass_fires_deadline_passed_chain_on_completed(self, pool):
        """tick() fires deadline_passed chain when referenced deadline is completed."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        deadline_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES
                ('visa-renewal-done', '0 0 * * *', 'Renew visa', 'prompt', 'deadline', $1,
                 30, '[{"days_before": 30, "severity": "info"}]',
                 'completed', '[{"days_before": 30, "severity": "info"}]', $2, false)
            RETURNING id
            """,
            _future_date(10),
            now,
        )

        chain_name = "post-visa-completed"
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ($1, 'deadline_passed', $2, $3::jsonb, 'active', 'test-butler')
            """,
            chain_name,
            str(deadline_id),
            '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Visa renewed"}]',
        )

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        chain_row = await pool.fetchrow(
            "SELECT status FROM event_chains WHERE name = $1", chain_name
        )
        assert chain_row["status"] == "fired"

    async def test_tick_event_chain_pass_does_not_refire_fired_chain(self, pool):
        """tick() does NOT re-fire a deadline_passed chain already status='fired'."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        deadline_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES
                ('already-expired-dl', '0 0 * * *', 'Some deadline', 'prompt', 'deadline', $1,
                 30, '[{"days_before": 30, "severity": "info"}]',
                 'expired', '[{"days_before": 30, "severity": "info"}]', $2, false)
            RETURNING id
            """,
            _past_date(1),
            now,
        )

        chain_name = "already-fired-chain"
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ($1, 'deadline_passed', $2,
                '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Already done"}]',
                'fired', 'test-butler')
            """,
            chain_name,
            str(deadline_id),
        )

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        chain_row = await pool.fetchrow(
            "SELECT status FROM event_chains WHERE name = $1", chain_name
        )
        assert chain_row["status"] == "fired"

        task_count = await pool.fetchval(
            "SELECT COUNT(*) FROM scheduled_tasks WHERE name = $1",
            f"chain:{chain_name}:0",
        )
        assert task_count == 0

    async def test_tick_event_chain_deadline_threshold_chain(self, pool):
        """tick() fires threshold chain when matching severity fired; skips unmatched."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)

        # Deadline with critical threshold already fired
        deadline_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES
                ('critical-deadline', '0 0 * * *', 'Critical task', 'prompt', 'deadline', $1,
                 30, '[{"days_before": 7, "severity": "critical"}]',
                 'escalated', '[{"days_before": 7, "severity": "critical"}]', $2, true)
            RETURNING id
            """,
            _future_date(5),
            now,
        )

        chain_name = "on-critical-escalation"
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ($1, 'deadline_threshold', $2, $3::jsonb, 'active', 'test-butler')
            """,
            chain_name,
            f"{deadline_id}:critical",
            '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Escalation alert"}]',
        )

        # Deadline with only info threshold fired — critical chain should NOT fire
        deadline_id2 = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES
                ('info-only-deadline', '0 0 * * *', 'Info only', 'prompt', 'deadline', $1,
                 30,
                 '[{"days_before": 30, "severity": "info"}, '
                 '{"days_before": 7, "severity": "critical"}]',
                 'alerted', '[{"days_before": 30, "severity": "info"}]', $2, true)
            RETURNING id
            """,
            _future_date(20),
            now,
        )

        chain_name2 = "critical-only-chain"
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ($1, 'deadline_threshold', $2,
                '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Critical alert"}]',
                'active', 'test-butler')
            """,
            chain_name2,
            f"{deadline_id2}:critical",
        )

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        row1 = await pool.fetchrow("SELECT status FROM event_chains WHERE name = $1", chain_name)
        assert row1["status"] == "fired"

        row2 = await pool.fetchrow("SELECT status FROM event_chains WHERE name = $1", chain_name2)
        assert row2["status"] == "active"

    async def test_tick_event_chain_fires_threshold_without_calendar_projection(self, pool):
        """tick() fires deadline_threshold chains even without calendar_projection table."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        deadline_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES
                ('nocal-deadline', '0 0 * * *', 'No-calendar task', 'prompt', 'deadline', $1,
                 30, '[{"days_before": 7, "severity": "warning"}]',
                 'alerted', '[{"days_before": 7, "severity": "warning"}]', $2, true)
            RETURNING id
            """,
            _future_date(5),
            now,
        )

        chain_name = "no-calendar-threshold-chain"
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ($1, 'deadline_threshold', $2, $3::jsonb, 'active', 'test-butler')
            """,
            chain_name,
            f"{deadline_id}:warning",
            '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Warning hit"}]',
        )

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        chain_row = await pool.fetchrow(
            "SELECT status FROM event_chains WHERE name = $1", chain_name
        )
        assert chain_row["status"] == "fired"

    async def test_tick_cron_tasks_unaffected_by_new_passes(self, pool):
        """Existing cron tasks still dispatch normally."""
        from butlers.core.scheduler import tick

        await pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, next_run_at, enabled)
            VALUES ('regular-cron-task', '* * * * *', 'Daily morning summary',
                    'prompt', 'cron', $1, true)
            """,
            datetime.now(UTC),
        )

        dispatch_calls = []

        async def capture_dispatch(**kwargs):
            dispatch_calls.append(kwargs)

        result = await tick(pool, capture_dispatch)
        assert result >= 1
        assert any("morning summary" in c.get("prompt", "") for c in dispatch_calls)

    async def test_tick_deadline_expiry_disables_task(self, pool):
        """tick() marks past-target_date deadline as expired and disabled."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        await pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES ('expired-deadline', '* * * * *', 'Renew passport', 'prompt', 'deadline', $1,
                    30, '[{"days_before": 30, "severity": "info"}]',
                    'alerted', '[{"days_before": 30, "severity": "info"}]', $2, true)
            """,
            _past_date(1),
            now,
        )

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        row = await pool.fetchrow(
            "SELECT deadline_status, enabled FROM scheduled_tasks WHERE name = 'expired-deadline'"
        )
        assert row["deadline_status"] == "expired" and row["enabled"] is False

    async def test_tick_deferred_notification_retry_and_no_notify_fn(self, pool):
        """notify_fn failure keeps notification pending; missing notify_fn also keeps pending."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        notif_id1 = await pool.fetchval(
            """
            INSERT INTO deferred_notifications
                (butler_name, channel, message, priority, envelope, deliver_at, status)
            VALUES ('test-butler', 'telegram', 'Will fail', 'medium',
                    '{"schema_version": "notify.v1"}'::jsonb, $1, 'pending')
            RETURNING id
            """,
            now - timedelta(minutes=5),
        )
        notif_id2 = await pool.fetchval(
            """
            INSERT INTO deferred_notifications
                (butler_name, channel, message, priority, envelope, deliver_at, status)
            VALUES ('test-butler', 'telegram', 'No notify_fn', 'medium',
                    '{"schema_version": "notify.v1"}'::jsonb, $1, 'pending')
            RETURNING id
            """,
            now - timedelta(minutes=5),
        )

        async def failing_notify_fn(envelope: dict) -> None:
            raise RuntimeError("Switchboard unavailable")

        async def noop_dispatch(**kwargs):
            pass

        # First tick with failing notify_fn
        await tick(pool, noop_dispatch, notify_fn=failing_notify_fn)
        row1 = await pool.fetchrow(
            "SELECT status FROM deferred_notifications WHERE id = $1", notif_id1
        )
        assert row1["status"] == "pending"

        # Second tick with no notify_fn
        await tick(pool, noop_dispatch)
        row2 = await pool.fetchrow(
            "SELECT status FROM deferred_notifications WHERE id = $1", notif_id2
        )
        assert row2["status"] == "pending"

    async def test_sync_schedules_deadline_and_cron(self, pool):
        """sync_schedules inserts deadline with temporal fields; validates required fields."""
        from butlers.core.scheduler import sync_schedules

        target = _future_date(60)
        await sync_schedules(
            pool,
            [
                {
                    "name": "visa-renewal",
                    "cron": "0 9 * * *",
                    "task_type": "deadline",
                    "prompt": "Check visa",
                    "dispatch_mode": "prompt",
                    "target_date": target.isoformat(),
                    "lead_time_days": 60,
                    "alert_thresholds": [
                        {"days_before": 60, "severity": "info"},
                        {"days_before": 30, "severity": "warning"},
                    ],
                },
                {
                    "name": "morning-summary",
                    "cron": "0 8 * * *",
                    "task_type": "cron",
                    "prompt": "Morning briefing",
                    "dispatch_mode": "prompt",
                },
            ],
        )

        dl_row = await pool.fetchrow(
            "SELECT task_type, target_date, lead_time_days, alert_thresholds "
            "FROM scheduled_tasks WHERE name = 'visa-renewal'"
        )
        assert dl_row["task_type"] == "deadline"
        assert dl_row["target_date"] == target
        assert dl_row["lead_time_days"] == 60
        thresholds = dl_row["alert_thresholds"]
        if isinstance(thresholds, str):
            thresholds = json.loads(thresholds)
        assert len(thresholds) == 2

        cron_row = await pool.fetchrow(
            "SELECT task_type FROM scheduled_tasks WHERE name = 'morning-summary'"
        )
        assert cron_row["task_type"] == "cron"

        with pytest.raises(ValueError, match="target_date"):
            await sync_schedules(
                pool,
                [
                    {
                        "name": "bad-dl",
                        "cron": "0 9 * * *",
                        "task_type": "deadline",
                        "prompt": "Missing target",
                        "dispatch_mode": "prompt",
                        "lead_time_days": 30,
                        "alert_thresholds": [{"days_before": 30, "severity": "info"}],
                    }
                ],
            )

    async def test_tick_deadline_seasonal_context_injection(self, pool):
        """tick() injects active_seasons into deadline dispatch but not without butler_name."""
        from butlers.core.scheduler import tick

        butler = "seasonal-deadline-butler"
        await pool.execute(
            """
            INSERT INTO seasonal_periods
                (name, period_type, start_month, start_day, end_month, end_day,
                 butler_name, enabled)
            VALUES ('peak-season', 'annual', 1, 1, 12, 31, $1, true)
            """,
            butler,
        )

        now = datetime.now(UTC)
        target = _future_date(30)
        await pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES ($1, '* * * * *', $2, 'prompt', 'deadline', $3, 30,
                    '[{"days_before": 30, "severity": "info"}]',
                    'pending', '[]', $4, true)
            """,
            "seasonal-deadline-task",
            "Check your deadlines",
            target,
            now,
        )

        # With butler_name → seasonal prefix injected
        dispatched_calls: list[dict] = []

        async def capture_dispatch(**kwargs):
            dispatched_calls.append(kwargs)

        await tick(pool, capture_dispatch, butler_name=butler)

        dl_calls = [c for c in dispatched_calls if "deadline:" in c.get("trigger_source", "")]
        assert dl_calls, f"No deadline dispatch; all: {dispatched_calls}"
        prompt = dl_calls[0]["prompt"]
        assert "Seasonal context" in prompt and "peak-season" in prompt
        assert "Check your deadlines" in prompt

        # Without butler_name → no seasonal prefix
        await pool.execute(
            "UPDATE scheduled_tasks "
            "SET fired_thresholds='[]', deadline_status='pending', next_run_at=$1 "
            "WHERE name=$2",
            now,
            "seasonal-deadline-task",
        )
        dispatched_calls.clear()

        await tick(pool, capture_dispatch)
        dl_calls2 = [c for c in dispatched_calls if "deadline:" in c.get("trigger_source", "")]
        if dl_calls2:
            assert "Seasonal context" not in dl_calls2[0]["prompt"]
