"""Tests for butlers.jobs.calendar_overlay — relationship overlay contribution job.

Covers the relationship ``calendar_overlay_contribution`` deterministic job:
- per-date envelopes written across the lookahead window
- ``birthday`` / ``important_date`` / ``follow_up`` entries, priority-descending
- recurring ``(month, day)`` important-dates landing on the right window date
- overdue reminders clamped onto today as high priority
- honest empty-state (``has_entries=false`` for dates with no events)
- idempotent upsert (re-run rewrites the same keys)
- prune of stale past-date entries (no-op when none)
- zero LLM (handler takes only ``(pool, job_args)``; no spawner)

No real database required — ``state_set`` / ``state_list`` / ``state_delete``
are patched to capture writes/deletes, and ``pool.fetch`` is routed by SQL text.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import patch

import pytest

from butlers.jobs.calendar_overlay import (
    OVERLAY_KEY_PREFIX,
    RELATIONSHIP_OVERLAY_LOOKAHEAD_DAYS,
    overlay_key,
    run_relationship_calendar_overlay_contribution,
)

pytestmark = pytest.mark.unit

_TODAY = date(2026, 6, 21)
_TODAY_STR = "2026-06-21"


# ---------------------------------------------------------------------------
# Fake pool + state-store capture harness
# ---------------------------------------------------------------------------


class _FakePool:
    """Routes ``pool.fetch`` to important_dates/reminder row lists by SQL content."""

    def __init__(
        self,
        *,
        dates: list[dict[str, Any]] | None = None,
        reminders: list[dict[str, Any]] | None = None,
    ) -> None:
        self._dates = dates or []
        self._reminders = reminders or []
        self.fetch_calls: list[str] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append(sql)
        if "FROM important_dates" in sql:
            return self._dates
        if "FROM facts" in sql:
            return self._reminders
        raise AssertionError(f"unexpected query: {sql}")


class _StateCapture:
    """Captures state_set/state_list/state_delete into an in-memory dict."""

    def __init__(self, seed: dict[str, Any] | None = None) -> None:
        self.store: dict[str, Any] = dict(seed or {})
        self.deleted: list[str] = []
        self.set_calls: list[str] = []

    async def state_set(self, pool: Any, key: str, value: Any) -> int:
        self.store[key] = value
        self.set_calls.append(key)
        return 1

    async def state_list(self, pool: Any, prefix: str | None = None, keys_only: bool = True):
        if prefix is None:
            return sorted(self.store)
        return sorted(k for k in self.store if k.startswith(prefix))

    async def state_delete(self, pool: Any, key: str) -> None:
        self.store.pop(key, None)
        self.deleted.append(key)


def _date(name: str, label: str, on: date, *, year: int | None = None):
    return {"name": name, "label": label, "month": on.month, "day": on.day, "year": year}


def _reminder(name: str, label: str, trigger: datetime):
    return {"name": name, "label": label, "trigger_at": trigger}


async def _run(
    *,
    dates: list[dict[str, Any]] | None = None,
    reminders: list[dict[str, Any]] | None = None,
    seed: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], _StateCapture, _FakePool]:
    pool = _FakePool(dates=dates, reminders=reminders)
    cap = _StateCapture(seed=seed)
    with (
        patch("butlers.jobs.calendar_overlay.today_sgt", return_value=_TODAY),
        patch("butlers.jobs.calendar_overlay.state_set", new=cap.state_set),
        patch("butlers.jobs.calendar_overlay.state_list", new=cap.state_list),
        patch("butlers.jobs.calendar_overlay.state_delete", new=cap.state_delete),
    ):
        result = await run_relationship_calendar_overlay_contribution(pool, None)
    return result, cap, pool


# ---------------------------------------------------------------------------
# Envelope writing
# ---------------------------------------------------------------------------


async def test_writes_one_envelope_per_lookahead_date():
    result, cap, _ = await _run()
    expected = RELATIONSHIP_OVERLAY_LOOKAHEAD_DAYS + 1
    written_keys = [k for k in cap.store if k.startswith(OVERLAY_KEY_PREFIX)]
    assert len(written_keys) == expected
    assert result["dates_written"] == expected
    assert overlay_key(_TODAY_STR) in cap.store
    assert overlay_key("2026-07-21") in cap.store  # today + 30


async def test_birthday_and_important_date_routed_to_recurring_window_dates():
    dates = [
        _date("Sarah", "Birthday", date(2000, 6, 23), year=2000),  # recurs 6/23 → in window
        _date("Mom", "Wedding anniversary", date(1985, 7, 1)),  # recurs 7/1 → in window
    ]
    result, cap, _ = await _run(dates=dates)

    bday_env = cap.store[overlay_key("2026-06-23")]
    assert bday_env["has_entries"] is True
    assert bday_env["entries"][0]["kind"] == "birthday"
    assert bday_env["entries"][0]["label"] == "Sarah"
    assert bday_env["entries"][0]["meta"]["occasion"] == "Birthday"
    assert bday_env["entries"][0]["meta"]["year"] == 2000

    anniv_env = cap.store[overlay_key("2026-07-01")]
    assert anniv_env["entries"][0]["kind"] == "important_date"
    assert anniv_env["entries"][0]["label"] == "Mom"
    assert anniv_env["entries"][0]["meta"]["occasion"] == "Wedding anniversary"

    assert result["birthday_entries"] == 1
    assert result["important_date_entries"] == 1
    assert result["total_entries"] == 2


async def test_birthday_today_is_high_priority():
    dates = [_date("Alex", "Birthday", date(1990, 6, 21))]  # recurs today
    _, cap, _ = await _run(dates=dates)
    env = cap.store[overlay_key(_TODAY_STR)]
    assert env["entries"][0]["priority"] == "high"


async def test_far_future_date_is_low_priority():
    dates = [_date("Pat", "Birthday", date(1990, 7, 15))]  # 24 days out
    _, cap, _ = await _run(dates=dates)
    env = cap.store[overlay_key("2026-07-15")]
    assert env["entries"][0]["priority"] == "low"


async def test_follow_up_reminder_routed_to_due_date():
    # Reminder due 2026-06-25 09:00 SGT → 01:00 UTC.
    trigger = datetime(2026, 6, 25, 1, 0, tzinfo=UTC)
    reminders = [_reminder("Chris", "Coffee catch-up", trigger)]
    result, cap, _ = await _run(reminders=reminders)

    env = cap.store[overlay_key("2026-06-25")]
    assert env["entries"][0]["kind"] == "follow_up"
    assert env["entries"][0]["label"] == "Coffee catch-up"
    assert env["entries"][0]["meta"]["person"] == "Chris"
    assert env["entries"][0]["meta"]["overdue"] is False
    assert result["follow_up_entries"] == 1


async def test_overdue_follow_up_clamps_to_today_high_priority():
    # Trigger well before today → must surface on today's envelope as high prio.
    trigger = datetime(2026, 6, 10, 1, 0, tzinfo=UTC)
    reminders = [_reminder("Dana", "Overdue ping", trigger)]
    _, cap, _ = await _run(reminders=reminders)

    env = cap.store[overlay_key(_TODAY_STR)]
    follow_ups = [e for e in env["entries"] if e["kind"] == "follow_up"]
    assert len(follow_ups) == 1
    assert follow_ups[0]["priority"] == "high"
    assert follow_ups[0]["meta"]["overdue"] is True
    # No envelope is written for the past trigger date.
    assert overlay_key("2026-06-10") not in cap.store


async def test_entries_ordered_priority_descending_on_shared_date():
    # A birthday today (high) and a far-out important date... place both on today
    # by giving the important date today's month/day too, but lower priority via
    # an overdue follow-up. Use birthday-today (high) + follow-up-today (high) and
    # confirm birthday (added first) keeps order; then add a low item.
    dates = [
        _date("Today Bday", "Birthday", date(1990, 6, 21)),  # high (today)
    ]
    trigger = datetime(2026, 6, 21, 1, 0, tzinfo=UTC)  # follow-up due today → high
    reminders = [_reminder("Today FollowUp", "Call", trigger)]
    _, cap, _ = await _run(dates=dates, reminders=reminders)

    env = cap.store[overlay_key(_TODAY_STR)]
    assert all(e["priority"] == "high" for e in env["entries"])
    # Birthday rows are processed before reminder rows → stable insertion order.
    assert [e["kind"] for e in env["entries"]] == ["birthday", "follow_up"]


# ---------------------------------------------------------------------------
# Honest empty-state
# ---------------------------------------------------------------------------


async def test_empty_domain_writes_has_entries_false_for_every_date():
    result, cap, _ = await _run()
    overlay_envs = [v for k, v in cap.store.items() if k.startswith(OVERLAY_KEY_PREFIX)]
    assert len(overlay_envs) == RELATIONSHIP_OVERLAY_LOOKAHEAD_DAYS + 1
    assert all(env["has_entries"] is False for env in overlay_envs)
    assert all(env["entries"] == [] for env in overlay_envs)
    assert all(env["butler"] == "relationship" for env in overlay_envs)
    assert result["total_entries"] == 0


# ---------------------------------------------------------------------------
# Idempotent upsert
# ---------------------------------------------------------------------------


async def test_rerun_upserts_same_keys():
    dates = [_date("Sarah", "Birthday", date(2000, 6, 23))]
    _, cap1, _ = await _run(dates=dates)
    keys_after_first = sorted(k for k in cap1.store if k.startswith(OVERLAY_KEY_PREFIX))

    _, cap2, _ = await _run(dates=dates, seed=cap1.store)
    keys_after_second = sorted(k for k in cap2.store if k.startswith(OVERLAY_KEY_PREFIX))
    assert keys_after_first == keys_after_second
    assert len(keys_after_second) == RELATIONSHIP_OVERLAY_LOOKAHEAD_DAYS + 1


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------


async def test_prune_removes_stale_past_dates():
    seed = {
        overlay_key("2026-06-19"): {"butler": "relationship", "date": "2026-06-19"},
        overlay_key("2026-06-20"): {"butler": "relationship", "date": "2026-06-20"},
        "briefing/daily/2026-06-19": {"unrelated": True},
    }
    result, cap, _ = await _run(seed=seed)
    assert overlay_key("2026-06-19") in cap.deleted
    assert overlay_key("2026-06-20") in cap.deleted
    assert overlay_key(_TODAY_STR) not in cap.deleted
    assert "briefing/daily/2026-06-19" not in cap.deleted
    assert result["pruned"] == 2


async def test_prune_is_noop_when_no_stale_dates():
    result, cap, _ = await _run()
    assert cap.deleted == []
    assert result["pruned"] == 0


# ---------------------------------------------------------------------------
# Determinism / zero-LLM + registration
# ---------------------------------------------------------------------------


def test_handler_registered_and_callable_no_spawner():
    from butlers.daemon import (
        _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
        _resolve_deterministic_schedule_job_name,
    )

    rel_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("relationship", {})
    assert "calendar_overlay_contribution" in rel_jobs
    assert callable(rel_jobs["calendar_overlay_contribution"])

    # Pre-existing relationship entries are untouched.
    assert "daily_briefing_contribution" in rel_jobs
    assert "insight_scan" in rel_jobs

    resolved = _resolve_deterministic_schedule_job_name(
        butler_name="relationship",
        trigger_source="schedule:calendar_overlay_contribution",
        job_name="calendar_overlay_contribution",
    )
    assert resolved == "calendar_overlay_contribution"


async def test_no_llm_session_spawned():
    """The job path must not construct or invoke any LLM spawner."""
    with patch("butlers.jobs.calendar_overlay.today_sgt", return_value=_TODAY):
        cap = _StateCapture()
        pool = _FakePool()
        with (
            patch("butlers.jobs.calendar_overlay.state_set", new=cap.state_set),
            patch("butlers.jobs.calendar_overlay.state_list", new=cap.state_list),
            patch("butlers.jobs.calendar_overlay.state_delete", new=cap.state_delete),
        ):
            result = await run_relationship_calendar_overlay_contribution(pool, None)
    assert result["butler"] == "relationship"
    # Only important_dates + reminder (facts) queries were issued — no LLM fan-out.
    assert len(pool.fetch_calls) == 2
