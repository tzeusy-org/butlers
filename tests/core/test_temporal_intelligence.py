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

from butlers.config import ScheduleSpec

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


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("SP Group", "SP Group"),
        ("Smith & Sons", "Smith & Sons"),
        ("O'Brien-Lee Insurance", "O'Brien-Lee Insurance"),
        ("Société Générale", "Société Générale"),
        # A contact channel (phone, link, email) never survives as something to act on.
        ("call 9123 4567 to avoid penalty", "call to avoid penalty"),
        ("+65-9123-4567", None),
        ("SP Group (6671 7000)", "SP Group"),
        ("pay at evil.com", "pay at evilcom"),
        ("https://x.io/pay", "httpsxiopay"),
        ("help@evil.example", "helpevilexample"),
        ("SP Group ９１２３ ٤٥٦٧", "SP Group"),
        ("<b>AXA</b>\n[pay](http://x.io)", "bAXAb payhttpxio"),
        # A long issuer is cut to 40 characters at a word boundary.
        (
            "Inland Revenue Authority of Singapore Collections",
            "Inland Revenue Authority of Singapore",
        ),
        ("A" * 80, "A" * 40),
        (None, None),
        (42, None),
        ("  ...  ", None),
    ],
)
def test_document_deadline_issuer_keeps_only_a_short_name(raw, shown):
    """Issuer text from an untrusted document is reduced to a name-like token set."""
    from butlers.core.temporal.document_deadlines import sanitize_issuer

    assert sanitize_issuer(raw) == shown


def test_document_deadline_plan_pins_the_template_and_refuses_bad_input():
    """A document deadline is a prompt-free job keyed on (document, kind); bad input refuses."""
    from butlers.core.temporal.document_deadlines import (
        NOTICE_JOB_NAME,
        DocumentDeadlineRefused,
        plan_document_deadline,
    )

    today = datetime.now(UTC).date()

    def plan(days: int | str = 30, **overrides: Any):
        target = days if isinstance(days, str) else (today + timedelta(days=days)).isoformat()
        args = {
            "document_id": "s3://bucket/general/bill.pdf",
            "kind": "due",
            "target_date": target,
            "preset": "two_weeks",
            "issuer": "SP Group",
        }
        return plan_document_deadline(**{**args, **overrides})

    bill = plan(issuer="SP Group <a href=x>\n[pay](http://evil.example)")
    assert bill.job_name == NOTICE_JOB_NAME and not hasattr(bill, "prompt")
    assert [t["days_before"] for t in bill.alert_thresholds] == [14, 3, 1]
    assert bill.job_args["taint"] == "untrusted_document"
    # The issuer is inert text: no markup, link or URL punctuation survives.
    issuer = bill.job_args["issuer"]
    assert issuer.startswith("SP Group ") and not set("<>[]()/:.=\n") & set(issuer)
    # One deadline per (document, kind): a newer date keeps the key, another kind does not.
    assert plan(60).name == bill.name != plan(kind="renewal").name
    # Created late, thresholds already passed collapse to one notice now, not a burst.
    assert [t["days_before"] for t in plan(2).alert_thresholds] == [3, 1]

    refusals = [
        ({"days": -3}, "past_date"),
        ({"days": 0}, "past_date"),
        ({"days": "28 Oct"}, "invalid_date"),
        ({"days": 4000}, "invalid_date"),
        ({"kind": "bogus"}, "invalid_kind"),
        ({"preset": "forever"}, "invalid_preset"),
        ({"document_id": " "}, "invalid_document_id"),
        ({"document_id": "x" * 600}, "invalid_document_id"),
    ]
    for overrides, reason in refusals:
        days = overrides.pop("days", 30)
        with pytest.raises(DocumentDeadlineRefused) as refused:
            plan(days, **overrides)
        assert refused.value.reason == reason, overrides


def test_supersession_fields_expose_only_well_formed_markers():
    """A supersession reads from job_args as a task id and a timestamp, nothing else;
    a tampered or missing marker reads as not superseded."""
    from butlers.core.temporal.deadlines_db import supersession_fields

    task_id = str(uuid.uuid4())
    at = "2026-11-02T10:15:00.123456+00:00"
    assert supersession_fields(
        {"superseded_by": task_id, "superseded_at": at, "issuer": "AXA", "taint": "x"}
    ) == {"superseded_by": task_id, "superseded_at": datetime.fromisoformat(at)}
    none = {"superseded_by": None, "superseded_at": None}
    for args in (
        None,
        "not-an-object",
        {},
        {"superseded_by": "<b>AXA</b>", "superseded_at": at},
        {"superseded_by": 7, "superseded_at": at},
    ):
        assert supersession_fields(args) == none, args
    # A garbled or naive stamp drops only the timestamp.
    for bad_at in ("yesterday", "2026-11-02T10:15:00", 1730540100):
        assert supersession_fields({"superseded_by": task_id, "superseded_at": bad_at}) == {
            "superseded_by": task_id,
            "superseded_at": None,
        }


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

    assert active("tax", 1, 1, 4, 15, date(2026, 3, 15)) is True  # same-year active
    assert active("tax", 1, 1, 4, 15, date(2026, 6, 1)) is False  # outside range
    assert active("winter", 11, 15, 1, 10, date(2026, 12, 20)) is True  # cross-year Dec
    assert active("winter", 11, 15, 1, 10, date(2026, 1, 5)) is True  # cross-year Jan
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
    assert (
        should_defer_notification(priority="high", current_time=time(23, 30), prefs=prefs) is False
    )
    assert (
        should_defer_notification(priority="medium", current_time=time(1, 0), prefs=prefs) is True
    )
    assert should_defer_notification(priority="low", current_time=time(3, 30), prefs=prefs) is True
    assert (
        should_defer_notification(priority="medium", current_time=time(14, 0), prefs=prefs) is False
    )
    assert (
        should_defer_notification(priority="medium", current_time=time(2, 0), prefs=None) is False
    )

    # Per-channel overrides
    prefs_ov = {
        **prefs,
        "override_channels": {"email": {"quiet_hours_start": "20:00", "quiet_hours_end": "09:00"}},
    }
    eff = resolve_effective_quiet_hours(channel="email", prefs=prefs_ov)
    assert eff["quiet_hours_start"] == "20:00"
    eff2 = resolve_effective_quiet_hours(channel="telegram", prefs=prefs_ov)
    assert eff2["quiet_hours_start"] == "22:00"
    assert should_defer_notification(
        priority="medium", current_time=time(21, 0), prefs=prefs_ov, channel="email"
    )
    assert not should_defer_notification(
        priority="medium", current_time=time(21, 0), prefs=prefs_ov, channel="telegram"
    )

    # compute_deliver_at: batch today/tomorrow; invalid tz and naive raise
    r1 = compute_deliver_at(prefs=_make_prefs("UTC"), now=datetime(2026, 1, 15, 23, 0, tzinfo=UTC))
    assert r1.date() == date(2026, 1, 16) and r1.hour == 7
    r2 = compute_deliver_at(prefs=_make_prefs("UTC"), now=datetime(2026, 1, 15, 4, 0, tzinfo=UTC))
    assert r2.date() == date(2026, 1, 15) and r2.hour == 7
    with pytest.raises(ValueError, match="Unknown timezone"):
        compute_deliver_at(
            prefs=_make_prefs(tz="Invalid/Zone"), now=datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        )
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

    # Every deadline below is expressed as an offset from this instant, so the
    # test names it rather than reading the wall clock.
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    # is_notification_due: past pending → due; future/delivered/expired → not due
    assert is_notification_due(
        _make_deferred("pending", deliver_at=now - timedelta(minutes=1)), now=now
    )
    assert not is_notification_due(
        _make_deferred("pending", deliver_at=now + timedelta(hours=2)), now=now
    )
    assert not is_notification_due(
        _make_deferred("delivered", deliver_at=now - timedelta(minutes=1)), now=now
    )
    assert not is_notification_due(
        _make_deferred("expired", deliver_at=now - timedelta(hours=2)), now=now
    )

    # is_notification_expired: >24h past → expired; <24h → not
    assert is_notification_expired(_make_deferred(deliver_at=now - timedelta(hours=25)), now=now)
    assert not is_notification_expired(
        _make_deferred(deliver_at=now - timedelta(hours=23)), now=now
    )

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


@pytest.mark.asyncio
async def test_event_chain_pass_skips_calendar_projection_when_event_chains_table_missing():
    """A missing migrated event-chain table faults without querying legacy calendar data."""
    from butlers.core.scheduler.chain_pass import _tick_event_chain_pass

    class FakePool:
        def __init__(self) -> None:
            self.fetch_calls: list[str] = []

        async def fetchval(self, sql: str, *args):
            if "information_schema.tables" in sql and args == ("event_chains",):
                return False
            if "information_schema.columns" in sql and args == (
                "scheduled_tasks",
                "deadline_status",
            ):
                return False
            raise AssertionError(f"Unexpected fetchval: {sql!r} {args!r}")

        async def fetch(self, sql: str, *args):
            import asyncpg

            self.fetch_calls.append(sql)
            if "FROM event_chains" in sql:
                raise asyncpg.UndefinedTableError("event_chains")
            raise AssertionError(f"Unexpected fetch: {sql!r} {args!r}")

        async def execute(self, sql: str, *args):
            raise AssertionError(f"Unexpected execute: {sql!r} {args!r}")

    pool = FakePool()

    async def noop(**kwargs):
        pass

    import asyncpg

    with pytest.raises(asyncpg.UndefinedTableError):
        await _tick_event_chain_pass(pool, noop, datetime.now(UTC))
    assert not any("FROM calendar_projection" in call for call in pool.fetch_calls)


async def test_event_chain_pass_skips_malformed_deadline_trigger_references(caplog):
    """Malformed deadline trigger refs must not abort the scheduler tick."""
    import logging

    from butlers.core.scheduler.chain_pass import _tick_event_chain_pass

    class FakePool:
        def __init__(self) -> None:
            self.fetch_calls: list[str] = []

        async def fetchval(self, sql: str, *args):
            if "information_schema.tables" in sql and args == ("event_chains",):
                return True
            if "information_schema.columns" in sql and args == (
                "scheduled_tasks",
                "deadline_status",
            ):
                return True
            raise AssertionError(f"Unexpected fetchval: {sql!r} {args!r}")

        async def fetch(self, sql: str, *args):
            self.fetch_calls.append(sql)
            if "information_schema.columns" in sql and args[0] == "calendar_projection":
                return []
            if "FROM event_chains" in sql and "deadline_passed" in sql:
                return [
                    {
                        "chain_id": "bad-passed",
                        "chain_name": "bad-passed",
                        "actions": [],
                        "trigger_reference": "not-a-uuid",
                    }
                ]
            if "FROM event_chains" in sql and "deadline_threshold" in sql:
                return [
                    {
                        "chain_id": "bad-threshold",
                        "chain_name": "bad-threshold",
                        "actions": [],
                        "trigger_reference": "also-not-valid",
                    }
                ]
            raise AssertionError(f"Unexpected fetch: {sql!r} {args!r}")

        async def execute(self, sql: str, *args):
            raise AssertionError(f"Unexpected execute: {sql!r} {args!r}")

    pool = FakePool()

    async def noop(**kwargs):
        pass

    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        assert await _tick_event_chain_pass(pool, noop, datetime.now(UTC)) == 0

    assert "bad-passed" in caplog.text
    assert "bad-threshold" in caplog.text
    assert not any("trigger_reference::uuid" in call for call in pool.fetch_calls)
    assert not any("id::text" in call for call in pool.fetch_calls)
    assert not any("split_part(ec.trigger_reference" in call for call in pool.fetch_calls)


# ---------------------------------------------------------------------------
# 12.11 — tick() integration
# ---------------------------------------------------------------------------


async def _create_historical_calendar_projection(pool) -> None:
    """Deliberate legacy pre-state: no current chain creates calendar_projection.

    The tests that call this prove tick() leaves historical calendar rows inert.
    """
    await pool.execute("""
        CREATE TABLE calendar_projection (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name TEXT NOT NULL,
            event_id TEXT NOT NULL,
            title TEXT,
            start_at TIMESTAMPTZ,
            end_at TIMESTAMPTZ,
            chain_triggered BOOLEAN NOT NULL DEFAULT false
        )
    """)


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

    @pytest.fixture
    async def tick_pool(self, db):
        """The complete current core chain in a real butler schema, per case."""
        return await db("core", schemas={"core": "general"}, schema="general")

    async def test_tick_deadline_fires_threshold_with_spans(self, tick_pool):
        """tick() dispatches due deadline, sets span attributes; expired → disabled+expired."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from butlers.core.scheduler.dispatch import tick

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        now = datetime.now(UTC)
        target = _future_date(30)
        await tick_pool.fetchval(
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

        await tick(tick_pool, capture_dispatch)
        prompts = [c.get("prompt", "") for c in dispatched_calls]
        assert any("visa" in p.lower() or "Review" in p for p in prompts)

        spans = exporter.get_finished_spans()
        tick_span = next((s for s in spans if s.name == "butler.tick"), None)
        assert tick_span is not None
        attrs = dict(tick_span.attributes or {})
        assert "deadlines_evaluated" in attrs and "deadline_dispatched" in attrs
        assert attrs["deadline_dispatched"] > 0

        # Expired deadline → disabled + expired status
        async def noop(**kwargs):
            pass

        await tick_pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES ('expired-dl', '* * * * *', 'Renew', 'prompt', 'deadline', $1,
                    30, '[{"days_before": 30, "severity": "info"}]',
                    'alerted', '[{"days_before": 30, "severity": "info"}]', $2, true)
            """,
            _past_date(1),
            now,
        )
        await tick(tick_pool, noop)
        row = await tick_pool.fetchrow(
            "SELECT deadline_status, enabled FROM scheduled_tasks WHERE name = 'expired-dl'"
        )
        assert row["deadline_status"] == "expired" and row["enabled"] is False

    async def test_document_deadline_is_set_idempotently_and_fires_as_a_job(self, tick_pool):
        """paperwork_deadline_set upserts on (document, kind) without overriding an owner
        disable; a due threshold runs the fixed notice job with threshold context and no
        prompt, and stays unfired until the notice is delivered; prompt deadlines are untouched."""
        from butlers.core.scheduler.dispatch import tick
        from butlers.core.temporal.deadlines import DeadlineDispatchDeferred
        from butlers.core.temporal.deadlines_db import deadline_create
        from butlers.core.temporal.document_deadlines import (
            NOTICE_JOB_NAME,
            DocumentDeadlineRefused,
            document_deadline_set,
            plan_document_deadline,
        )

        def plan(days: int, kind: str = "due"):
            return plan_document_deadline(
                document_id="s3://bucket/general/bill.pdf",
                kind=kind,
                target_date=_future_date(days).isoformat(),
                preset="two_weeks",
                issuer="SP Group",
            )

        first = await document_deadline_set(tick_pool, plan(30))
        assert first["status"] == "created"
        assert await document_deadline_set(tick_pool, plan(30)) == {**first, "status": "unchanged"}
        name = plan(30).name
        row = await tick_pool.fetchrow("SELECT * FROM scheduled_tasks WHERE name = $1", name)
        assert (row["task_type"], row["dispatch_mode"], row["job_name"], row["prompt"]) == (
            "deadline",
            "job",
            NOTICE_JOB_NAME,
            None,
        )
        assert row["job_args"]["taint"] == "untrusted_document"

        # A newer date supersedes in place and re-arms thresholds that had fired,
        # but never re-enables a deadline the owner disabled.
        await tick_pool.execute(
            """
            UPDATE scheduled_tasks SET deadline_status = 'alerted', enabled = false,
                fired_thresholds = '[{"days_before": 14, "severity": "info"}]'
            WHERE name = $1
            """,
            name,
        )
        assert await document_deadline_set(tick_pool, plan(2)) == {**first, "status": "superseded"}
        assert (
            await tick_pool.fetchval("SELECT enabled FROM scheduled_tasks WHERE name = $1", name)
            is False
        )
        # Expiry is a system disable, not an owner's: a later date re-enables and re-arms it.
        await tick_pool.execute(
            "UPDATE scheduled_tasks SET deadline_status = 'expired' WHERE name = $1", name
        )
        assert await document_deadline_set(tick_pool, plan(3)) == {**first, "status": "superseded"}
        row = await tick_pool.fetchrow("SELECT * FROM scheduled_tasks WHERE name = $1", name)
        assert (row["enabled"], row["deadline_status"]) == (True, "pending")
        assert await document_deadline_set(tick_pool, plan(2)) == {**first, "status": "superseded"}

        # A notice that was not delivered (deferred or failed) leaves the threshold unfired.
        async def not_delivered(**kwargs):
            raise DeadlineDispatchDeferred("quiet_hours")

        await tick(tick_pool, not_delivered)
        row = await tick_pool.fetchrow("SELECT * FROM scheduled_tasks WHERE name = $1", name)
        assert (row["deadline_status"], row["fired_thresholds"]) == ("pending", [])

        calls: list[dict[str, Any]] = []

        async def capture(**kwargs):
            calls.append(kwargs)

        await tick(tick_pool, capture)
        assert len(calls) == 1 and "prompt" not in calls[0]
        assert calls[0]["job_name"] == NOTICE_JOB_NAME
        assert calls[0]["trigger_source"] == f"deadline:{name}"
        job_args = calls[0]["job_args"]
        assert (job_args["kind"], job_args["issuer"]) == ("due", "SP Group")
        assert job_args["deadline"]["days_remaining"] == 2
        assert job_args["deadline"]["fired_threshold"] == {"days_before": 3, "severity": "warning"}
        row = await tick_pool.fetchrow("SELECT * FROM scheduled_tasks WHERE name = $1", name)
        assert row["deadline_status"] == "alerted"
        assert row["fired_thresholds"] == [{"days_before": 3, "severity": "warning"}]

        # A name held by a prompt-bearing deadline is never rewritten into a job.
        renewal = plan(30, kind="renewal")
        await deadline_create(
            tick_pool,
            name=renewal.name,
            prompt="Renew",
            target_date=_future_date(30),
            lead_time_days=7,
            alert_thresholds=[_make_threshold(7)],
        )
        with pytest.raises(DocumentDeadlineRefused) as refused:
            await document_deadline_set(tick_pool, renewal)
        assert refused.value.reason == "name_conflict"

    async def test_failed_document_notice_backs_off_then_fires_on_recovery(
        self, tick_pool, monkeypatch
    ):
        """A Switchboard outage ledgers one failed row per backoff step, not per tick; the
        threshold stays unfired and fires once delivery recovers."""
        from unittest.mock import AsyncMock, MagicMock

        from butlers.core.scheduler.dispatch import tick
        from butlers.core.temporal.document_deadlines import (
            document_deadline_set,
            plan_document_deadline,
        )
        from butlers.jobs import owner_notify
        from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

        plan = plan_document_deadline(
            document_id="s3://bucket/general/outage.pdf",
            kind="due",
            target_date=_future_date(2).isoformat(),
            issuer="SP Group",
        )
        await document_deadline_set(tick_pool, plan)
        monkeypatch.setattr(
            owner_notify, "check_owner_notify_suppression", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            owner_notify, "resolve_owner_telegram_recipient", AsyncMock(return_value="123")
        )
        client = MagicMock()
        client.call_tool = AsyncMock(
            return_value=MagicMock(is_error=False, data={"notification_id": "n-1"})
        )
        switchboard: list[Any] = [None]
        monkeypatch.setattr(owner_notify, "get_current_switchboard_client", lambda: switchboard[0])
        job = get_deterministic_schedule_job_registry()["general"]["paperwork_deadline_notify"]

        async def run_job(**kwargs):
            await job(tick_pool, kwargs["job_args"])

        async def ledger() -> list[tuple[str, str]]:
            rows = await tick_pool.fetch(
                "SELECT outcome, dedup_key FROM public.attention_ledger "
                "WHERE origin_butler = 'general' ORDER BY occurred_at"
            )
            return [(r["outcome"], r["dedup_key"]) for r in rows]

        async def fired() -> list[Any]:
            return await tick_pool.fetchval(
                "SELECT fired_thresholds FROM scheduled_tasks WHERE name = $1", plan.name
            )

        for _ in range(3):
            await tick(tick_pool, run_job)
        failed = await ledger()
        assert [outcome for outcome, _ in failed] == ["failed"]
        assert failed[0][1].startswith("paperwork_deadline_notify:")
        assert await fired() == []

        # Past the 5-minute backoff the next attempt goes out; once delivery recovers,
        # the threshold fires under the same key.
        await tick_pool.execute(
            "UPDATE public.attention_ledger SET occurred_at = occurred_at - interval '6 minutes'"
        )
        switchboard[0] = client
        await tick(tick_pool, run_job)
        assert await ledger() == [failed[0], ("delivered", failed[0][1])]
        assert len(await fired()) == 1

    async def test_document_deadlines_supersede_across_documents_and_cap_per_issuer(
        self, tick_pool, monkeypatch
    ):
        """A later notice from the same issuer and kind disables older ones but keeps them,
        and keeps an owner's disable; an earlier or far-later date keeps both; caps refuse."""
        from butlers.core.scheduler.dispatch import tick
        from butlers.core.temporal import document_deadlines as dd
        from butlers.core.temporal.deadlines_db import deadline_list

        def plan(doc: str, days: int, issuer: str | None = "AXA", kind: str = "due"):
            return dd.plan_document_deadline(
                document_id=f"s3://bucket/general/{doc}.pdf",
                kind=kind,
                target_date=_future_date(days).isoformat(),
                issuer=issuer,
            )

        async def put(doc: str, days: int, issuer: str | None = "AXA", kind: str = "due"):
            return await dd.document_deadline_set(tick_pool, plan(doc, days, issuer, kind))

        def doc_of(job_args: dict[str, Any]) -> str:
            return job_args["document_id"].rsplit("/", 1)[1][:-4]

        async def rows() -> dict[str, tuple[bool, int, str | None]]:
            """document -> (enabled, days ahead, document that superseded it)."""
            found = await tick_pool.fetch(
                "SELECT id, job_args, enabled, target_date FROM scheduled_tasks "
                "WHERE job_name = $1",
                dd.NOTICE_JOB_NAME,
            )
            docs = {str(r["id"]): doc_of(r["job_args"]) for r in found}
            today = datetime.now(UTC).date()
            return {
                doc_of(r["job_args"]): (
                    r["enabled"],
                    (r["target_date"] - today).days,
                    docs.get(r["job_args"].get("superseded_by")),
                )
                for r in found
            }

        older = await put("axa-1", 30)
        await tick_pool.execute(
            "UPDATE scheduled_tasks SET enabled = false WHERE id = $1::uuid", older["id"]
        )
        # Same issuer (case-insensitive) and kind, later date: the older row is disabled
        # and kept with a pointer, and the owner's disable carries over to the newer one.
        newer = await put("axa-2", 40, issuer="axa")
        assert (newer["status"], newer["replaced"]) == ("created", [older["id"]])
        # deadline_list tells the superseded row from an owner-disabled one, without job_args.
        listed = {r["id"]: r for r in await deadline_list(tick_pool)}
        assert "job_args" not in listed[older["id"]]
        assert listed[older["id"]]["superseded_by"] == newer["id"]
        assert listed[older["id"]]["superseded_at"] is not None
        assert (listed[newer["id"]]["superseded_by"], listed[newer["id"]]["superseded_at"]) == (
            None,
            None,
        )
        # An earlier date, or one over 400 days past the one it would replace, keeps both.
        assert (await put("axa-3", 20))["replaced"] == []
        assert (await put("axa-4", 50, kind="renewal"))["replaced"] == []
        assert (await put("axa-5", 500, kind="renewal"))["replaced"] == []
        # Documents without an issuer never supersede.
        await put("anon-1", 10, issuer=None)
        assert (await put("anon-2", 20, issuer=None))["replaced"] == []
        assert await rows() == {
            "axa-1": (False, 30, "axa-2"),
            "axa-2": (False, 40, None),
            "axa-3": (True, 20, None),
            "axa-4": (True, 50, None),
            "axa-5": (True, 500, None),
            "anon-1": (True, 10, None),
            "anon-2": (True, 20, None),
        }

        # A superseded row never fires, is not superseded again and carries no disable;
        # a completed predecessor is finished business, not an owner's disable either.
        sp1 = await put("sp-1", 10, issuer="SP Group")
        sp2 = await put("sp-2", 20, issuer="SP Group")
        assert sp2["replaced"] == [sp1["id"]]
        await tick_pool.execute(
            "UPDATE scheduled_tasks SET deadline_status = 'completed', enabled = false "
            "WHERE id = $1::uuid",
            sp2["id"],
        )
        assert (await put("sp-3", 30, issuer="SP Group"))["replaced"] == [sp2["id"]]
        assert (await rows())["sp-3"] == (True, 30, None)
        calls: list[dict[str, Any]] = []

        async def capture(**kwargs):
            calls.append(kwargs)

        await tick(tick_pool, capture)
        fired = {c["trigger_source"] for c in calls}
        assert f"deadline:{plan('anon-1', 10, None).name}" in fired
        assert f"deadline:{plan('sp-1', 10, 'SP Group').name}" not in fired

        # Caps count active, unsuperseded rows; a refusal writes nothing.
        monkeypatch.setattr(dd, "MAX_ACTIVE_PER_ISSUER", 3)
        before = await rows()
        with pytest.raises(dd.DocumentDeadlineRefused) as refused:
            await put("axa-6", 15)
        assert refused.value.reason == "issuer_cap_reached"
        # Moving an existing document into a full issuer bucket is capped too.
        with pytest.raises(dd.DocumentDeadlineRefused) as refused:
            await put("anon-1", 10, issuer="AXA")
        assert refused.value.reason == "issuer_cap_reached"
        assert await rows() == before
        # Re-dating an existing document in its own bucket adds no row: not capped.
        assert (await put("axa-3", 25))["status"] == "superseded"
        # Replacing both active AXA due rows fits, since superseded axa-1 is not counted.
        assert len((await put("axa-7", 60))["replaced"]) == 2
        monkeypatch.setattr(dd, "MAX_ACTIVE_DOCUMENT_DEADLINES", 1)
        with pytest.raises(dd.DocumentDeadlineRefused) as refused:
            await put("new-issuer", 15, issuer="Acme")
        assert refused.value.reason == "total_cap_reached"

    async def test_redating_a_superseded_document_restores_only_an_untouched_flag(self, tick_pool):
        """Re-dating a superseded document restores its pre-supersession flag only while
        the owner has not acted on it since; otherwise the owner's current choice stands."""
        from butlers.core.temporal import document_deadlines as dd
        from butlers.core.temporal.deadlines_db import deadline_update

        async def put(doc: str, days: int, issuer: str):
            plan = dd.plan_document_deadline(
                document_id=f"s3://bucket/general/{doc}.pdf",
                kind="due",
                target_date=_future_date(days).isoformat(),
                issuer=issuer,
            )
            return await dd.document_deadline_set(tick_pool, plan)

        async def enabled(task_id: str) -> bool:
            return await tick_pool.fetchval(
                "SELECT enabled FROM scheduled_tasks WHERE id = $1::uuid", task_id
            )

        # Untouched since supersession: the earlier flag comes back.
        plain = await put("plain-1", 10, "Plain Co")
        await put("plain-2", 20, "Plain Co")
        assert await enabled(plain["id"]) is False
        assert (await put("plain-1", 30, "Plain Co"))["status"] == "superseded"
        assert await enabled(plain["id"]) is True

        # The owner re-enabled, then disabled it again: document text keeps that disable.
        touched = await put("touched-1", 10, "Touched Co")
        await put("touched-2", 20, "Touched Co")
        await deadline_update(tick_pool, touched["id"], enabled=True)
        await deadline_update(tick_pool, touched["id"], enabled=False)
        assert (await put("touched-1", 30, "Touched Co"))["status"] == "superseded"
        assert await enabled(touched["id"]) is False

    async def test_tick_deferred_notification_delivery_and_expiry(self, tick_pool):
        """tick() delivers pending notifications via notify_fn; failure/missing → pending;
        expired (>24h) → expired; deferred_flushed span set."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from butlers.core.scheduler.dispatch import tick

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        now = datetime.now(UTC)
        env_json = json.dumps({"schema_version": "notify.v1", "delivery": {"channel": "telegram"}})

        async def noop(**kwargs):
            pass

        notif_id = await tick_pool.fetchval(
            """
            INSERT INTO deferred_notifications
                (butler_name, channel, message, priority, envelope, deliver_at, status)
            VALUES ('test-butler', 'telegram', 'Queued', 'medium', $2::jsonb, $1, 'pending')
            RETURNING id
            """,
            now - timedelta(minutes=5),
            env_json,
        )

        notify_calls = []

        async def capture_notify(env: dict):
            notify_calls.append(env)

        await tick(tick_pool, noop, notify_fn=capture_notify)
        row = await tick_pool.fetchrow(
            "SELECT status, delivered_at FROM deferred_notifications WHERE id = $1", notif_id
        )
        assert row["status"] == "delivered" and row["delivered_at"] is not None
        assert len(notify_calls) == 1 and notify_calls[0]["schema_version"] == "notify.v1"

        spans = exporter.get_finished_spans()
        tick_span = next((s for s in spans if s.name == "butler.tick"), None)
        assert tick_span is not None
        assert "deferred_flushed" in dict(tick_span.attributes or {})

        # Expired (>24h) → expired status
        old_id = await tick_pool.fetchval(
            """
            INSERT INTO deferred_notifications
                (butler_name, channel, message, priority, envelope, deliver_at, status)
            VALUES ('test-butler', 'telegram', 'Old', 'low',
                    '{"schema_version": "notify.v1"}'::jsonb, $1, 'pending')
            RETURNING id
            """,
            now - timedelta(hours=25),
        )
        await tick(tick_pool, noop)
        assert (
            await tick_pool.fetchrow(
                "SELECT status FROM deferred_notifications WHERE id = $1", old_id
            )
        )["status"] == "expired"

        # notify_fn failure → pending
        fail_id = await tick_pool.fetchval(
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

        await tick(tick_pool, noop, notify_fn=failing_notify)
        assert (
            await tick_pool.fetchrow(
                "SELECT status FROM deferred_notifications WHERE id = $1", fail_id
            )
        )["status"] == "pending"

    # -----------------------------------------------------------------------
    # Same-window coalescing (bu-o8233, JARVIS pursuit move 8 slice 4)
    # -----------------------------------------------------------------------

    @staticmethod
    async def _insert_deferred(
        tick_pool,
        *,
        butler_name: str,
        message: str,
        deliver_at: datetime,
        recipient: str | None = None,
        channel: str = "telegram",
        priority: str = "medium",
    ):
        envelope = {
            "schema_version": "notify.v1",
            "origin_butler": butler_name,
            "delivery": {"intent": "send", "channel": channel, "message": message},
        }
        if recipient is not None:
            envelope["delivery"]["recipient"] = recipient
        return await tick_pool.fetchval(
            """
            INSERT INTO deferred_notifications
                (butler_name, channel, message, priority, envelope, deliver_at, status)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, 'pending')
            RETURNING id
            """,
            butler_name,
            channel,
            message,
            priority,
            json.dumps(envelope),
            deliver_at,
        )

    async def test_tick_deferred_notifications_coalesce_same_target_into_one_digest(
        self, tick_pool
    ):
        """N due notifications for the same (channel, recipient) compose into
        ONE notify_fn call instead of N separate sends, and all N rows are
        marked delivered together."""
        from butlers.core.scheduler.dispatch import tick

        now = datetime.now(UTC)
        due = now - timedelta(minutes=5)
        ids = [
            await self._insert_deferred(
                tick_pool,
                butler_name="finance",
                message=f"Bill {i} is due",
                deliver_at=due,
                recipient="owner-123",
            )
            for i in range(3)
        ]

        notify_calls: list[dict] = []

        async def capture_notify(env: dict):
            notify_calls.append(env)

        async def noop(**kwargs):
            pass

        await tick(tick_pool, noop, notify_fn=capture_notify)

        assert len(notify_calls) == 1, "3 same-target due notifications must compose into 1 send"
        composed = notify_calls[0]
        assert composed["schema_version"] == "notify.v1"
        assert composed["delivery"]["recipient"] == "owner-123"
        assert composed["delivery"]["channel"] == "telegram"
        assert "3" in composed["delivery"]["message"]
        for i in range(3):
            assert f"Bill {i} is due" in composed["delivery"]["message"]

        rows = await tick_pool.fetch(
            "SELECT status, delivered_at FROM deferred_notifications WHERE id = ANY($1::uuid[])",
            ids,
        )
        assert all(r["status"] == "delivered" for r in rows)
        assert all(r["delivered_at"] is not None for r in rows)

    async def test_tick_deferred_notifications_do_not_coalesce_different_recipients(
        self, tick_pool
    ):
        """Two due notifications for DIFFERENT recipients must never be folded
        into one composed message — each is delivered on its own."""
        from butlers.core.scheduler.dispatch import tick

        now = datetime.now(UTC)
        due = now - timedelta(minutes=5)
        await self._insert_deferred(
            tick_pool, butler_name="finance", message="For Alice", deliver_at=due, recipient="alice"
        )
        await self._insert_deferred(
            tick_pool, butler_name="finance", message="For Bob", deliver_at=due, recipient="bob"
        )

        notify_calls: list[dict] = []

        async def capture_notify(env: dict):
            notify_calls.append(env)

        async def noop(**kwargs):
            pass

        await tick(tick_pool, noop, notify_fn=capture_notify)

        assert len(notify_calls) == 2, "different recipients must not be coalesced"
        recipients = {c["delivery"]["recipient"] for c in notify_calls}
        assert recipients == {"alice", "bob"}
        messages = {c["delivery"]["message"] for c in notify_calls}
        assert messages == {"For Alice", "For Bob"}, (
            "solo-target sends must be delivered verbatim, not digest-formatted"
        )

    async def test_tick_deferred_notification_coalesced_failure_keeps_all_pending(self, tick_pool):
        """A failed composed-digest send must leave EVERY row in the group
        pending for retry — no partial delivery within a coalesced group."""
        from butlers.core.scheduler.dispatch import tick

        now = datetime.now(UTC)
        due = now - timedelta(minutes=5)
        ids = [
            await self._insert_deferred(
                tick_pool,
                butler_name="finance",
                message=f"Item {i}",
                deliver_at=due,
                recipient="owner-456",
            )
            for i in range(2)
        ]

        async def failing_notify(env: dict):
            raise RuntimeError("Switchboard unavailable")

        async def noop(**kwargs):
            pass

        await tick(tick_pool, noop, notify_fn=failing_notify)

        rows = await tick_pool.fetch(
            "SELECT status FROM deferred_notifications WHERE id = ANY($1::uuid[])", ids
        )
        assert all(r["status"] == "pending" for r in rows), (
            "a failed composed send must not partially mark the group delivered"
        )

    async def test_tick_event_chains(self, tick_pool):
        """Historical calendar stays inert; migrated deadline/threshold positives,
        no-refire of fired chains and chains_fired telemetry remain."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from butlers.core.scheduler.dispatch import tick

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        now = datetime.now(UTC)

        async def noop(**kwargs):
            pass

        # A historical calendar-end chain stays readable, unmarked and inert.
        await _create_historical_calendar_projection(tick_pool)
        event_id = "google-event-abc123"
        await tick_pool.execute(
            """
            INSERT INTO calendar_projection
                (butler_name, event_id, title, start_at, end_at, chain_triggered)
            VALUES ('test-butler', $1, 'Dentist', $2, $3, false)
            """,
            event_id,
            now - timedelta(hours=1),
            now - timedelta(minutes=2),
        )
        await tick_pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ('post-dentist', 'calendar_event_end', $1,
                    '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Log visit"}]',
                    'active', 'test-butler')
            """,
            event_id,
        )
        await tick(tick_pool, noop)
        assert (
            await tick_pool.fetchrow("SELECT status FROM event_chains WHERE name = 'post-dentist'")
        )["status"] == "active"
        assert (
            await tick_pool.fetchval(
                "SELECT chain_triggered FROM calendar_projection WHERE event_id=$1", event_id
            )
            is False
        )
        assert (
            await tick_pool.fetchval(
                "SELECT COUNT(*) FROM scheduled_tasks WHERE name='chain:post-dentist:0'"
            )
            == 0
        )
        spans = exporter.get_finished_spans()
        tick_span = next((s for s in spans if s.name == "butler.tick"), None)
        assert tick_span is not None and "chains_fired" in dict(tick_span.attributes or {})

        # deadline_passed: expired deadline → chain fired + task created
        past_dl_id = await tick_pool.fetchval(
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
            _past_date(1),
            now,
        )
        await tick_pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ('post-tax', 'deadline_passed', $1,
                    '[{"action_type": "job", "delay_minutes": 0, "job_name": "archive_tax"}]'::jsonb,
                    'active', 'test-butler')
            """,
            str(past_dl_id),
        )
        await tick(tick_pool, noop)
        assert (
            await tick_pool.fetchrow(
                "SELECT deadline_status FROM scheduled_tasks WHERE name = 'tax-filing'"
            )
        )["deadline_status"] == "expired"
        assert (
            await tick_pool.fetchrow("SELECT status FROM event_chains WHERE name = 'post-tax'")
        )["status"] == "fired"
        task_row = await tick_pool.fetchrow(
            "SELECT source FROM scheduled_tasks WHERE name = 'chain:post-tax:0'"
        )
        assert task_row is not None and task_row["source"] == "chain"

        # deadline_passed: completed deadline → chain fired
        comp_dl_id = await tick_pool.fetchval(
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
            _future_date(10),
            now,
        )
        await tick_pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ('post-visa', 'deadline_passed', $1,
                    '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Visa done"}]'::jsonb,
                    'active', 'test-butler')
            """,
            str(comp_dl_id),
        )
        await tick(tick_pool, noop)
        assert (
            await tick_pool.fetchrow("SELECT status FROM event_chains WHERE name = 'post-visa'")
        )["status"] == "fired"

        # Already-fired chain NOT refired
        already_id = await tick_pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES ('already-expired', '0 0 * * *', 'Some', 'prompt', 'deadline', $1,
                    30, '[]', 'expired', '[]', $2, false)
            RETURNING id
            """,
            _past_date(1),
            now,
        )
        await tick_pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ('already-fired', 'deadline_passed', $1,
                    '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Done"}]',
                    'fired', 'test-butler')
            """,
            str(already_id),
        )
        await tick(tick_pool, noop)
        assert (
            await tick_pool.fetchrow("SELECT status FROM event_chains WHERE name = 'already-fired'")
        )["status"] == "fired"
        assert (
            await tick_pool.fetchval(
                "SELECT COUNT(*) FROM scheduled_tasks WHERE name = 'chain:already-fired:0'"
            )
            == 0
        )

        # deadline_threshold chain fires for matching severity
        threshold_dl_id = await tick_pool.fetchval(
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
            _future_date(5),
            now,
        )
        await tick_pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES ('on-critical', 'deadline_threshold', $1,
                    '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Escalation"}]'::jsonb,
                    'active', 'test-butler')
            """,
            f"{threshold_dl_id}:critical",
        )
        await tick(tick_pool, noop)
        assert (
            await tick_pool.fetchrow("SELECT status FROM event_chains WHERE name = 'on-critical'")
        )["status"] == "fired"

    async def test_tick_skips_calendar_event_chain_pass_without_event_chains_table(self, tick_pool):
        """A missing migrated event-chain table is visible; historical rows stay untouched."""
        import asyncpg

        from butlers.core.scheduler.dispatch import tick

        now = datetime.now(UTC)

        async def noop(**kwargs):
            pass

        await tick_pool.execute("DROP TABLE event_chains")
        await _create_historical_calendar_projection(tick_pool)
        await tick_pool.execute(
            """
            INSERT INTO calendar_projection
                (butler_name, event_id, title, start_at, end_at, chain_triggered)
            VALUES ('test-butler', 'event-without-chain-table', 'Done', $1, $2, false)
            """,
            now - timedelta(hours=1),
            now - timedelta(minutes=2),
        )

        with pytest.raises(asyncpg.UndefinedTableError):
            await tick(tick_pool, noop)
        row = await tick_pool.fetchrow(
            "SELECT chain_triggered FROM calendar_projection WHERE event_id = $1",
            "event-without-chain-table",
        )
        assert row["chain_triggered"] is False

    async def test_tick_cron_and_sync_schedules(self, tick_pool):
        """Cron tasks dispatch; sync_schedules inserts deadline with temporal fields; the spec requires them."""
        from butlers.core.scheduler.dispatch import tick
        from butlers.core.scheduler.sync import sync_schedules

        await tick_pool.execute(
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

        result = await tick(tick_pool, capture)
        assert result >= 1 and any("morning summary" in c.get("prompt", "") for c in dispatch_calls)

        target = _future_date(60)
        await sync_schedules(
            tick_pool,
            [
                ScheduleSpec(
                    name="visa-renewal",
                    cron="0 9 * * *",
                    task_type="deadline",
                    prompt="Check visa",
                    target_date=target,
                    lead_time_days=60,
                    alert_thresholds=[
                        {"days_before": 60, "severity": "info"},
                        {"days_before": 30, "severity": "warning"},
                    ],
                ),
                ScheduleSpec(name="morning-summary", cron="0 8 * * *", prompt="Morning briefing"),
            ],
        )
        dl_row = await tick_pool.fetchrow(
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

        assert (
            await tick_pool.fetchrow(
                "SELECT task_type FROM scheduled_tasks WHERE name = 'morning-summary'"
            )
        )["task_type"] == "cron"

        with pytest.raises(ValueError, match="target_date"):
            ScheduleSpec(
                name="bad-dl",
                cron="0 9 * * *",
                task_type="deadline",
                prompt="Missing target",
                lead_time_days=30,
                alert_thresholds=[{"days_before": 30, "severity": "info"}],
            )

    async def test_tick_deadline_seasonal_context_injection(self, tick_pool):
        """tick() injects active_seasons into deadline prompt when butler_name provided;
        no seasons in prompt without butler_name."""
        from butlers.core.scheduler.dispatch import tick

        butler = "seasonal-deadline-butler"
        await tick_pool.execute(
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
        await tick_pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES ($1, '* * * * *', $2, 'prompt', 'deadline', $3, 30,
                    '[{"days_before": 30, "severity": "info"}]',
                    'pending', '[]', $4, true)
            """,
            "seasonal-task",
            "Check deadlines",
            target,
            now,
        )

        dispatched_calls: list[dict] = []

        async def capture_dispatch(**kwargs):
            dispatched_calls.append(kwargs)

        await tick(tick_pool, capture_dispatch, butler_name=butler)
        dl_calls = [c for c in dispatched_calls if "deadline:" in c.get("trigger_source", "")]
        assert dl_calls, f"No deadline dispatch; all: {dispatched_calls}"
        prompt = dl_calls[0]["prompt"]
        assert "Seasonal context" in prompt and "peak-season" in prompt

        # Without butler_name → no seasonal prefix
        await tick_pool.execute(
            "UPDATE scheduled_tasks "
            "SET fired_thresholds='[]', deadline_status='pending', next_run_at=$1 "
            "WHERE name=$2",
            now,
            "seasonal-task",
        )
        dispatched_calls.clear()
        await tick(tick_pool, capture_dispatch)
        dl_calls2 = [c for c in dispatched_calls if "deadline:" in c.get("trigger_source", "")]
        if dl_calls2:
            assert "Seasonal context" not in dl_calls2[0]["prompt"]
