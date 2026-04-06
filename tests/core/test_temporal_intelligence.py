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


def test_deadline_validation_invalid_raises():
    """Various invalid deadline inputs raise ValueError with appropriate messages."""
    from butlers.core.temporal.deadlines import validate_deadline_input

    cases = [
        (_past_date(10), 7, [_make_threshold(7)], "future"),
        (datetime.now(UTC).date(), 1, [_make_threshold(1)], "future"),
        (_future_date(30), 30, [], "threshold"),
        (_future_date(60), 14, [_make_threshold(30, "info")], "lead_time_days|days_before"),
        (_future_date(60), 0, [_make_threshold(1)], None),
    ]
    for target, lead_time, thresholds, match in cases:
        with pytest.raises(ValueError, match=match) if match else pytest.raises(ValueError):
            validate_deadline_input(
                target_date=target,
                lead_time_days=lead_time,
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
    match = find_unfired_threshold(
        days_remaining=42, alert_thresholds=thresholds, fired_thresholds=[]
    )
    assert match is not None and match["days_before"] == 42

    # Fires past threshold (missed downtime window)
    match2 = find_unfired_threshold(
        days_remaining=12, alert_thresholds=[_make_threshold(14, "warning")], fired_thresholds=[]
    )
    assert match2 is not None and match2["days_before"] == 14

    # Already-fired excluded
    assert (
        find_unfired_threshold(
            days_remaining=42,
            alert_thresholds=thresholds,
            fired_thresholds=[_make_threshold(42, "info")],
        )
        is None
    )

    # 50 days remaining: both thresholds are in the future (50 > 42 > 14) — none eligible
    assert (
        find_unfired_threshold(days_remaining=50, alert_thresholds=thresholds, fired_thresholds=[])
        is None
    )

    # Most urgent selected (smallest days_before not yet fired)
    all_t = [
        _make_threshold(42, "info"),
        _make_threshold(14, "warning"),
        _make_threshold(3, "critical"),
    ]  # noqa: E501
    fired = [_make_threshold(42, "info"), _make_threshold(14, "warning")]
    match3 = find_unfired_threshold(
        days_remaining=3, alert_thresholds=all_t, fired_thresholds=fired
    )
    assert match3 is not None and match3["days_before"] == 3


# ---------------------------------------------------------------------------
# 12.3 — Deadline status state machine
# ---------------------------------------------------------------------------


def test_deadline_status_transitions():
    """Status transitions from pending/alerted/terminal states."""
    from butlers.core.temporal.deadlines import compute_next_deadline_status

    def t(current: str, severity: str) -> str:
        return compute_next_deadline_status(
            current_status=current, fired_threshold=_make_threshold(7, severity)
        )

    assert t("pending", "info") == "alerted"
    assert t("pending", "critical") == "escalated"
    assert t("alerted", "critical") == "escalated"
    assert t("completed", "warning") == "completed"  # terminal
    assert t("expired", "warning") == "expired"  # terminal


def test_deadline_expiry_transition():
    """Past dates expire active statuses; terminal and future statuses unchanged."""
    from butlers.core.temporal.deadlines import compute_expiry_transition

    def e(current: str, target_date) -> tuple:
        return compute_expiry_transition(current_status=current, target_date=target_date)

    assert e("alerted", _past_date(1)) == ("expired", True)
    assert e("pending", _past_date(1)) == ("expired", True)
    assert e("completed", _past_date(5)) == ("completed", False)
    assert e("alerted", _future_date(10)) == ("alerted", False)


# ---------------------------------------------------------------------------
# 12.4 — Deadline dependency blocking
# ---------------------------------------------------------------------------


def test_deadline_dependency_blocking():
    """Dependency blocking: incomplete dep statuses block; completed or no deps don't."""
    from butlers.core.temporal.deadlines import is_deadline_blocked

    def blocked(dep_statuses: dict) -> bool:
        return is_deadline_blocked(
            depends_on=list(dep_statuses.keys()), dependency_statuses=dep_statuses
        )

    assert blocked({"a": "pending"}) is True
    assert blocked({"a": "completed"}) is False
    assert blocked({}) is False
    assert blocked({"a": "completed", "b": "alerted"}) is True
    assert blocked({"a": "alerted"}) is True
    assert blocked({"a": "expired"}) is True


# ---------------------------------------------------------------------------
# 12.5 — Event chain action schema and materialization
# ---------------------------------------------------------------------------


def test_event_chain_action_schema():
    """Valid prompt/job actions pass; invalid actions raise ValueError with expected match."""
    from butlers.core.temporal.event_chains import validate_chain_actions

    # Valid schema doesn't raise
    validate_chain_actions([{"action_type": "prompt", "delay_minutes": 0, "prompt": "Log visit"}])
    validate_chain_actions([{"action_type": "job", "delay_minutes": 60, "job_name": "archive"}])

    invalid_cases = [
        ([{"action_type": "webhook", "delay_minutes": 0, "url": "x"}], "action_type|webhook"),
        ([{"action_type": "prompt", "delay_minutes": 0}], "prompt"),
        ([{"action_type": "job", "delay_minutes": 0}], "job_name"),
        ([{"action_type": "prompt", "delay_minutes": -5, "prompt": "x"}], "delay_minutes"),
        ([], "action"),
    ]
    for actions, match in invalid_cases:
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


def test_is_period_active():
    """Period active detection: same-year, cross-year, and disabled cases."""
    from butlers.core.temporal.seasonal import is_period_active

    def active(name, sm, sd, em, ed, today, enabled=True) -> bool:
        return is_period_active(_make_period(name, sm, sd, em, ed, enabled=enabled), today=today)

    assert active("tax", 1, 1, 4, 15, date(2026, 3, 15)) is True   # same-year active
    assert active("tax", 1, 1, 4, 15, date(2026, 6, 1)) is False   # outside range
    assert active("winter", 11, 15, 1, 10, date(2026, 12, 20)) is True  # cross-year Dec
    assert active("winter", 11, 15, 1, 10, date(2026, 1, 5)) is True   # cross-year Jan
    assert active("winter", 11, 15, 1, 10, date(2026, 2, 1)) is False  # cross-year inactive
    assert active("disabled", 1, 1, 4, 15, date(2026, 3, 15), enabled=False) is False


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


def test_quiet_hours_is_in_and_priority_defer():
    """is_in_quiet_hours correctness; priority deferral; per-channel overrides; compute_deliver_at."""
    from butlers.core.temporal.delivery import (
        compute_deliver_at,
        is_in_quiet_hours,
        resolve_effective_quiet_hours,
        should_defer_notification,
    )

    def is_in(t, qs, qe) -> bool:
        return is_in_quiet_hours(current_time=t, quiet_start=qs, quiet_end=qe)

    # Overnight quiet hours (22:00-07:00)
    assert is_in(time(23, 30), time(22, 0), time(7, 0)) is True
    assert is_in(time(0, 30), time(22, 0), time(7, 0)) is True
    assert is_in(time(6, 59), time(22, 0), time(7, 0)) is True
    assert is_in(time(14, 0), time(22, 0), time(7, 0)) is False
    assert is_in(time(7, 1), time(22, 0), time(7, 0)) is False
    # Same-day quiet hours (08:00-12:00)
    assert is_in(time(10, 0), time(8, 0), time(12, 0)) is True
    assert is_in(time(13, 0), time(8, 0), time(12, 0)) is False

    # Priority: high bypasses; medium/low deferred inside quiet hours; None prefs never defers
    prefs = _make_prefs()
    assert should_defer_notification(priority="high", current_time=time(23, 30), prefs=prefs) is False
    assert should_defer_notification(priority="medium", current_time=time(1, 0), prefs=prefs) is True
    assert should_defer_notification(priority="low", current_time=time(3, 30), prefs=prefs) is True
    assert should_defer_notification(priority="medium", current_time=time(14, 0), prefs=prefs) is False
    assert should_defer_notification(priority="medium", current_time=time(2, 0), prefs=None) is False

    # Per-channel overrides
    prefs_ov = {**prefs, "override_channels": {"email": {"quiet_hours_start": "20:00", "quiet_hours_end": "09:00"}}}
    eff = resolve_effective_quiet_hours(channel="email", prefs=prefs_ov)
    assert eff["quiet_hours_start"] == "20:00"
    eff2 = resolve_effective_quiet_hours(channel="telegram", prefs=prefs_ov)
    assert eff2["quiet_hours_start"] == "22:00"
    assert should_defer_notification(priority="medium", current_time=time(21, 0), prefs=prefs_ov, channel="email")
    assert not should_defer_notification(priority="medium", current_time=time(21, 0), prefs=prefs_ov, channel="telegram")

    # compute_deliver_at: batch today/tomorrow; invalid tz and naive raise
    r1 = compute_deliver_at(prefs=_make_prefs("UTC"), now=datetime(2026, 1, 15, 23, 0, tzinfo=UTC))
    assert r1.date() == date(2026, 1, 16) and r1.hour == 7
    r2 = compute_deliver_at(prefs=_make_prefs("UTC"), now=datetime(2026, 1, 15, 4, 0, tzinfo=UTC))
    assert r2.date() == date(2026, 1, 15) and r2.hour == 7
    with pytest.raises(ValueError, match="Unknown timezone"):
        compute_deliver_at(prefs=_make_prefs(tz="Invalid/Zone"), now=datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone-aware"):
        compute_deliver_at(prefs=_make_prefs(), now=datetime(2026, 1, 15, 12, 0))


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


def test_notification_due_expiry_and_delivery_result():
    """is_notification_due, is_notification_expired, compute_delivery_result, filter functions."""
    from butlers.core.temporal.delivery import (
        compute_delivery_result,
        filter_due_notifications,
        filter_expired_notifications,
        is_notification_due,
        is_notification_expired,
    )

    now = datetime.now(UTC)

    # is_notification_due: past pending → due; future/delivered/expired → not due
    assert is_notification_due(_make_deferred("pending", deliver_at=now - timedelta(minutes=1)), now=now)
    assert not is_notification_due(_make_deferred("pending", deliver_at=now + timedelta(hours=2)), now=now)
    assert not is_notification_due(_make_deferred("delivered", deliver_at=now - timedelta(minutes=1)), now=now)
    assert not is_notification_due(_make_deferred("expired", deliver_at=now - timedelta(hours=2)), now=now)

    # is_notification_expired: >24h past → expired; <24h → not
    assert is_notification_expired(_make_deferred(deliver_at=now - timedelta(hours=25)), now=now)
    assert not is_notification_expired(_make_deferred(deliver_at=now - timedelta(hours=23)), now=now)

    # compute_delivery_result
    assert compute_delivery_result(delivery_succeeded=False)["status"] == "pending"
    result = compute_delivery_result(delivery_succeeded=True, delivered_at=now)
    assert result["status"] == "delivered" and result["delivered_at"] == now

    # filter functions
    notifications = [
        _make_deferred("pending", now - timedelta(minutes=5)),
        _make_deferred("pending", now + timedelta(hours=1)),
        _make_deferred("delivered", now - timedelta(minutes=1)),
        _make_deferred("expired", now - timedelta(hours=30)),
    ]
    assert len(filter_due_notifications(notifications, now=now)) == 1
    expired_list = [
        _make_deferred("pending", now - timedelta(hours=25)),
        _make_deferred("pending", now - timedelta(hours=10)),
    ]
    assert len(filter_expired_notifications(expired_list, now=now)) == 1


# ---------------------------------------------------------------------------
# 12.11 — tick() integration
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestTickIntegration:
    """Integration tests for tick() with deadline, event chain, and deferred notification passes.

    Condensed: 16 methods → 5. Merged per-behavior groups to eliminate redundancy while
    preserving all unique behavioral contracts.
    """

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
        await p.execute("""
            CREATE TABLE IF NOT EXISTS calendar_projection (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                butler_name TEXT NOT NULL,
                event_id TEXT NOT NULL,
                title TEXT,
                start_at TIMESTAMPTZ,
                end_at TIMESTAMPTZ,
                chain_triggered BOOLEAN NOT NULL DEFAULT false
            )
        """)
        yield p
        await p.close()

    async def test_tick_deadline_fires_threshold_with_spans(self, pool):
        """tick() dispatches due deadline, sets span attributes; expired → disabled+expired."""
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
            "test-deadline", "Review visa renewal checklist", target, now,
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
        assert "deadlines_evaluated" in attrs and "deadline_dispatched" in attrs
        assert attrs["deadline_dispatched"] > 0

        # Expired deadline → disabled + expired status
        async def noop(**kwargs): pass
        await pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES ('expired-dl', '* * * * *', 'Renew', 'prompt', 'deadline', $1,
                    30, '[{"days_before": 30, "severity": "info"}]',
                    'alerted', '[{"days_before": 30, "severity": "info"}]', $2, true)
            """,
            _past_date(1), now,
        )
        await tick(pool, noop)
        row = await pool.fetchrow(
            "SELECT deadline_status, enabled FROM scheduled_tasks WHERE name = 'expired-dl'"
        )
        assert row["deadline_status"] == "expired" and row["enabled"] is False

    async def test_tick_deferred_notification_delivery_and_expiry(self, pool):
        """tick() delivers pending notifications via notify_fn; failure/missing → pending;
        expired (>24h) → expired; deferred_flushed span set."""
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
        env_json = json.dumps({"schema_version": "notify.v1", "delivery": {"channel": "telegram"}})
        async def noop(**kwargs): pass

        notif_id = await pool.fetchval(
            """
            INSERT INTO deferred_notifications
                (butler_name, channel, message, priority, envelope, deliver_at, status)
            VALUES ('test-butler', 'telegram', 'Queued', 'medium', $2::jsonb, $1, 'pending')
            RETURNING id
            """,
            now - timedelta(minutes=5), env_json,
        )

        notify_calls = []

        async def capture_notify(env: dict):
            notify_calls.append(env)

        await tick(pool, noop, notify_fn=capture_notify)
        row = await pool.fetchrow(
            "SELECT status, delivered_at FROM deferred_notifications WHERE id = $1", notif_id
        )
        assert row["status"] == "delivered" and row["delivered_at"] is not None
        assert len(notify_calls) == 1 and notify_calls[0]["schema_version"] == "notify.v1"

        spans = exporter.get_finished_spans()
        tick_span = next((s for s in spans if s.name == "butler.tick"), None)
        assert tick_span is not None
        assert "deferred_flushed" in dict(tick_span.attributes or {})

        # Expired (>24h) → expired status
        old_id = await pool.fetchval(
            """
            INSERT INTO deferred_notifications
                (butler_name, channel, message, priority, envelope, deliver_at, status)
            VALUES ('test-butler', 'telegram', 'Old', 'low',
                    '{"schema_version": "notify.v1"}'::jsonb, $1, 'pending')
            RETURNING id
            """,
            now - timedelta(hours=25),
        )
        await tick(pool, noop)
        assert (await pool.fetchrow(
            "SELECT status FROM deferred_notifications WHERE id = $1", old_id
        ))["status"] == "expired"

        # notify_fn failure → pending
        fail_id = await pool.fetchval(
            """
            INSERT INTO deferred_notifications
                (butler_name, channel, message, priority, envelope, deliver_at, status)
            VALUES ('test-butler', 'telegram', 'Fail', 'medium',
                    '{"schema_version": "notify.v1"}'::jsonb, $1, 'pending')
            RETURNING id
            """,
            now - timedelta(minutes=5),
        )

        async def failing_notify(env: dict):
            raise RuntimeError("DB down")

        await tick(pool, noop, notify_fn=failing_notify)
        assert (await pool.fetchrow(
            "SELECT status FROM deferred_notifications WHERE id = $1", fail_id
        ))["status"] == "pending"

    async def test_tick_event_chains(self, pool):
        """tick() fires: calendar_event_end, deadline_passed (expired+completed),
        deadline_threshold; no-refire of fired chains; chains_fired span set."""
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

        async def noop(**kwargs):
            pass

        # calendar_event_end chain fires
        event_id = "google-event-abc123"
        await pool.execute(
            """
            INSERT INTO calendar_projection
                (butler_name, event_id, title, start_at, end_at, chain_triggered)
            VALUES ('test-butler', $1, 'Dentist', $2, $3, false)
            """,
            event_id, now - timedelta(hours=1), now - timedelta(minutes=2),
        )
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ('post-dentist', 'calendar_event_end', $1,
                    '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Log visit"}]',
                    'active', 'test-butler')
            """,
            event_id,
        )
        await tick(pool, noop)
        assert (await pool.fetchrow(
            "SELECT status FROM event_chains WHERE name = 'post-dentist'"
        ))["status"] == "fired"
        spans = exporter.get_finished_spans()
        tick_span = next((s for s in spans if s.name == "butler.tick"), None)
        assert tick_span is not None and "chains_fired" in dict(tick_span.attributes or {})

        # deadline_passed: expired deadline → chain fired + task created
        past_dl_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES ('tax-filing', '0 0 * * *', 'File taxes', 'prompt', 'deadline', $1,
                    30, '[{"days_before": 30, "severity": "info"}]',
                    'alerted', '[{"days_before": 30, "severity": "info"}]', $2, true)
            RETURNING id
            """,
            _past_date(1), now,
        )
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ('post-tax', 'deadline_passed', $1,
                    '[{"action_type": "job", "delay_minutes": 0, "job_name": "archive_tax"}]'::jsonb,
                    'active', 'test-butler')
            """,
            str(past_dl_id),
        )
        await tick(pool, noop)
        assert (await pool.fetchrow(
            "SELECT deadline_status FROM scheduled_tasks WHERE name = 'tax-filing'"
        ))["deadline_status"] == "expired"
        assert (await pool.fetchrow(
            "SELECT status FROM event_chains WHERE name = 'post-tax'"
        ))["status"] == "fired"
        task_row = await pool.fetchrow(
            "SELECT source FROM scheduled_tasks WHERE name = 'chain:post-tax:0'"
        )
        assert task_row is not None and task_row["source"] == "chain"

        # deadline_passed: completed deadline → chain fired
        comp_dl_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES ('visa-done', '0 0 * * *', 'Renew visa', 'prompt', 'deadline', $1,
                    30, '[{"days_before": 30, "severity": "info"}]',
                    'completed', '[{"days_before": 30, "severity": "info"}]', $2, false)
            RETURNING id
            """,
            _future_date(10), now,
        )
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ('post-visa', 'deadline_passed', $1,
                    '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Visa done"}]'::jsonb,
                    'active', 'test-butler')
            """,
            str(comp_dl_id),
        )
        await tick(pool, noop)
        assert (await pool.fetchrow(
            "SELECT status FROM event_chains WHERE name = 'post-visa'"
        ))["status"] == "fired"

        # Already-fired chain NOT refired
        already_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES ('already-expired', '0 0 * * *', 'Some', 'prompt', 'deadline', $1,
                    30, '[]', 'expired', '[]', $2, false)
            RETURNING id
            """,
            _past_date(1), now,
        )
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ('already-fired', 'deadline_passed', $1,
                    '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Done"}]',
                    'fired', 'test-butler')
            """,
            str(already_id),
        )
        await tick(pool, noop)
        assert (await pool.fetchrow(
            "SELECT status FROM event_chains WHERE name = 'already-fired'"
        ))["status"] == "fired"
        assert await pool.fetchval(
            "SELECT COUNT(*) FROM scheduled_tasks WHERE name = 'chain:already-fired:0'"
        ) == 0

        # deadline_threshold chain fires for matching severity
        threshold_dl_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES ('critical-dl', '0 0 * * *', 'Critical', 'prompt', 'deadline', $1,
                    30, '[{"days_before": 7, "severity": "critical"}]',
                    'escalated', '[{"days_before": 7, "severity": "critical"}]', $2, true)
            RETURNING id
            """,
            _future_date(5), now,
        )
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ('on-critical', 'deadline_threshold', $1,
                    '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Escalation"}]'::jsonb,
                    'active', 'test-butler')
            """,
            f"{threshold_dl_id}:critical",
        )
        await tick(pool, noop)
        assert (await pool.fetchrow(
            "SELECT status FROM event_chains WHERE name = 'on-critical'"
        ))["status"] == "fired"

    async def test_tick_cron_and_sync_schedules(self, pool):
        """Cron tasks dispatch; sync_schedules inserts deadline with temporal fields; validates required fields."""
        from butlers.core.scheduler import sync_schedules, tick

        await pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, next_run_at, enabled)
            VALUES ('cron-task', '* * * * *', 'Daily morning summary', 'prompt', 'cron', $1, true)
            """,
            datetime.now(UTC),
        )
        dispatch_calls = []

        async def capture(**kwargs):
            dispatch_calls.append(kwargs)

        result = await tick(pool, capture)
        assert result >= 1 and any("morning summary" in c.get("prompt", "") for c in dispatch_calls)

        target = _future_date(60)
        await sync_schedules(pool, [
            {
                "name": "visa-renewal", "cron": "0 9 * * *", "task_type": "deadline",
                "prompt": "Check visa", "dispatch_mode": "prompt",
                "target_date": target.isoformat(), "lead_time_days": 60,
                "alert_thresholds": [
                    {"days_before": 60, "severity": "info"},
                    {"days_before": 30, "severity": "warning"},
                ],
            },
            {
                "name": "morning-summary", "cron": "0 8 * * *", "task_type": "cron",
                "prompt": "Morning briefing", "dispatch_mode": "prompt",
            },
        ])
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

        assert (await pool.fetchrow(
            "SELECT task_type FROM scheduled_tasks WHERE name = 'morning-summary'"
        ))["task_type"] == "cron"

        with pytest.raises(ValueError, match="target_date"):
            await sync_schedules(pool, [
                {
                    "name": "bad-dl", "cron": "0 9 * * *", "task_type": "deadline",
                    "prompt": "Missing target", "dispatch_mode": "prompt",
                    "lead_time_days": 30,
                    "alert_thresholds": [{"days_before": 30, "severity": "info"}],
                }
            ])

    async def test_tick_deadline_seasonal_context_injection(self, pool):
        """tick() injects active_seasons into deadline prompt when butler_name provided;
        no seasons in prompt without butler_name."""
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
            "seasonal-task", "Check deadlines", target, now,
        )

        dispatched_calls: list[dict] = []

        async def capture_dispatch(**kwargs):
            dispatched_calls.append(kwargs)

        await tick(pool, capture_dispatch, butler_name=butler)
        dl_calls = [c for c in dispatched_calls if "deadline:" in c.get("trigger_source", "")]
        assert dl_calls, f"No deadline dispatch; all: {dispatched_calls}"
        prompt = dl_calls[0]["prompt"]
        assert "Seasonal context" in prompt and "peak-season" in prompt

        # Without butler_name → no seasonal prefix
        await pool.execute(
            "UPDATE scheduled_tasks "
            "SET fired_thresholds='[]', deadline_status='pending', next_run_at=$1 "
            "WHERE name=$2",
            now, "seasonal-task",
        )
        dispatched_calls.clear()
        await tick(pool, capture_dispatch)
        dl_calls2 = [c for c in dispatched_calls if "deadline:" in c.get("trigger_source", "")]
        if dl_calls2:
            assert "Seasonal context" not in dl_calls2[0]["prompt"]
