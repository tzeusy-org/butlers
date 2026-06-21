"""Tests for butlers.jobs.calendar_overlay — health overlay contribution job.

Covers the health ``calendar_overlay_contribution`` deterministic job:
- per-date envelopes written across the lookahead window
- ``appointment`` entries (temporal facts) bucketed onto their SGT date
- ``medication_reminder`` entries (active medication facts) recurring daily
- honest empty-state (``has_entries=false`` for dates with no events)
- idempotent upsert (re-run rewrites the same keys)
- prune of stale past-date entries (no-op when none)
- zero LLM (handler takes only ``(pool, job_args)``; no spawner)

No real database required — ``state_set`` / ``state_list`` / ``state_delete``
are patched to capture writes/deletes, and ``pool.fetch`` is routed by SQL text.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from datetime import date as date_cls
from typing import Any
from unittest.mock import patch

import pytest

from butlers.jobs.calendar_overlay import (
    HEALTH_OVERLAY_LOOKAHEAD_DAYS,
    OVERLAY_KEY_PREFIX,
    overlay_key,
    run_health_calendar_overlay_contribution,
)

pytestmark = pytest.mark.unit

_SGT = timezone(timedelta(hours=8), name="SGT")
_TODAY = date_cls(2026, 6, 21)
_TODAY_STR = "2026-06-21"


def _sgt_noon(d: date_cls) -> datetime:
    """A tz-aware timestamp clearly inside the SGT calendar date *d*."""
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=_SGT)


# ---------------------------------------------------------------------------
# Fake pool + state-store capture harness
# ---------------------------------------------------------------------------


class _FakePool:
    """Routes ``pool.fetch`` to appointment/medication row lists by SQL content."""

    def __init__(
        self,
        *,
        appointments: list[dict[str, Any]] | None = None,
        medications: list[dict[str, Any]] | None = None,
    ) -> None:
        self._appointments = appointments or []
        self._medications = medications or []
        self.fetch_calls: list[str] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append(sql)
        if "predicate = 'appointment'" in sql:
            return self._appointments
        if "predicate = 'medication'" in sql:
            return self._medications
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


def _appointment(
    *,
    valid_at: datetime,
    content: str | None = "Dental cleaning",
    metadata: dict[str, Any] | None = None,
    appt_id: str = "33333333-3333-3333-3333-333333333333",
) -> dict[str, Any]:
    return {
        "id": appt_id,
        "content": content,
        "valid_at": valid_at,
        "metadata": metadata or {"location": "Smile Clinic", "provider": "Dr. Tan"},
    }


def _medication(
    *,
    name: str = "Lisinopril",
    dosage: str | None = "10mg",
    frequency: str | None = "daily",
    schedule: list[str] | None = None,
    med_id: str = "44444444-4444-4444-4444-444444444444",
) -> dict[str, Any]:
    return {
        "id": med_id,
        "name": name,
        "dosage": dosage,
        "frequency": frequency,
        "schedule": schedule if schedule is not None else ["08:00"],
    }


async def _run(
    *,
    appointments: list[dict[str, Any]] | None = None,
    medications: list[dict[str, Any]] | None = None,
    seed: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], _StateCapture, _FakePool]:
    pool = _FakePool(appointments=appointments, medications=medications)
    cap = _StateCapture(seed=seed)
    with (
        patch("butlers.jobs.calendar_overlay.today_sgt", return_value=_TODAY),
        patch("butlers.jobs.calendar_overlay.state_set", new=cap.state_set),
        patch("butlers.jobs.calendar_overlay.state_list", new=cap.state_list),
        patch("butlers.jobs.calendar_overlay.state_delete", new=cap.state_delete),
    ):
        result = await run_health_calendar_overlay_contribution(pool, None)
    return result, cap, pool


# ---------------------------------------------------------------------------
# Envelope writing
# ---------------------------------------------------------------------------


async def test_writes_one_envelope_per_lookahead_date():
    result, cap, _ = await _run()
    expected = HEALTH_OVERLAY_LOOKAHEAD_DAYS + 1
    written_keys = [k for k in cap.store if k.startswith(OVERLAY_KEY_PREFIX)]
    assert len(written_keys) == expected
    assert result["dates_written"] == expected
    assert overlay_key(_TODAY_STR) in cap.store
    horizon = (_TODAY + timedelta(days=HEALTH_OVERLAY_LOOKAHEAD_DAYS)).isoformat()
    assert overlay_key(horizon) in cap.store


async def test_appointment_routed_to_its_sgt_date():
    appts = [_appointment(valid_at=_sgt_noon(date_cls(2026, 6, 25)))]
    result, cap, _ = await _run(appointments=appts)

    env = cap.store[overlay_key("2026-06-25")]
    assert env["has_entries"] is True
    assert env["entries"][0]["kind"] == "appointment"
    assert env["entries"][0]["label"] == "Dental cleaning"
    assert env["entries"][0]["meta"]["location"] == "Smile Clinic"
    assert env["entries"][0]["meta"]["provider"] == "Dr. Tan"
    assert env["entries"][0]["meta"]["appointment_id"]
    assert result["appointment_entries"] == 1


async def test_appointment_title_prefers_metadata_title():
    appts = [
        _appointment(
            valid_at=_sgt_noon(date_cls(2026, 6, 25)),
            content=None,
            metadata={"title": "Annual physical"},
        )
    ]
    _, cap, _ = await _run(appointments=appts)
    env = cap.store[overlay_key("2026-06-25")]
    assert env["entries"][0]["label"] == "Annual physical"


async def test_appointment_timezone_bucketing_uses_sgt_calendar_date():
    # 2026-06-24 23:30 UTC is 2026-06-25 07:30 SGT — must bucket onto the 25th.
    appt_at = datetime(2026, 6, 24, 23, 30, tzinfo=UTC)
    appts = [_appointment(valid_at=appt_at)]
    _, cap, _ = await _run(appointments=appts)
    assert cap.store[overlay_key("2026-06-25")]["has_entries"] is True


async def test_imminent_appointment_is_high_priority():
    appts = [_appointment(valid_at=_sgt_noon(_TODAY))]
    _, cap, _ = await _run(appointments=appts)
    assert cap.store[overlay_key(_TODAY_STR)]["entries"][0]["priority"] == "high"


async def test_far_appointment_is_low_priority():
    far = date_cls(2026, 6, 30)  # 9 days out
    appts = [_appointment(valid_at=_sgt_noon(far))]
    _, cap, _ = await _run(appointments=appts)
    assert cap.store[overlay_key("2026-06-30")]["entries"][0]["priority"] == "low"


# ---------------------------------------------------------------------------
# Medication reminders recur daily
# ---------------------------------------------------------------------------


async def test_medication_reminder_emitted_on_every_lookahead_date():
    meds = [_medication()]
    result, cap, _ = await _run(medications=meds)
    expected_dates = HEALTH_OVERLAY_LOOKAHEAD_DAYS + 1
    for offset in range(expected_dates):
        date_str = (_TODAY + timedelta(days=offset)).isoformat()
        env = cap.store[overlay_key(date_str)]
        assert env["has_entries"] is True
        kinds = [e["kind"] for e in env["entries"]]
        assert "medication_reminder" in kinds
    # one med * (lookahead+1) dates
    assert result["medication_reminder_medications"] == 1
    assert result["total_entries"] == expected_dates


async def test_medication_reminder_meta_passthrough():
    meds = [_medication(name="Metformin", dosage="500mg", frequency="twice daily")]
    _, cap, _ = await _run(medications=meds)
    env = cap.store[overlay_key(_TODAY_STR)]
    entry = next(e for e in env["entries"] if e["kind"] == "medication_reminder")
    assert entry["label"] == "Metformin"
    assert entry["priority"] == "low"
    assert entry["meta"]["dosage"] == "500mg"
    assert entry["meta"]["frequency"] == "twice daily"
    assert entry["meta"]["schedule"] == ["08:00"]


async def test_appointment_sorts_before_medication_on_shared_date():
    appts = [_appointment(valid_at=_sgt_noon(_TODAY))]  # high today
    meds = [_medication()]  # low daily
    _, cap, _ = await _run(appointments=appts, medications=meds)
    env = cap.store[overlay_key(_TODAY_STR)]
    assert env["entries"][0]["kind"] == "appointment"
    assert env["entries"][0]["priority"] == "high"
    assert env["entries"][-1]["kind"] == "medication_reminder"


# ---------------------------------------------------------------------------
# Honest empty-state
# ---------------------------------------------------------------------------


async def test_empty_domain_writes_has_entries_false_for_every_date():
    result, cap, _ = await _run()
    overlay_envs = [v for k, v in cap.store.items() if k.startswith(OVERLAY_KEY_PREFIX)]
    assert len(overlay_envs) == HEALTH_OVERLAY_LOOKAHEAD_DAYS + 1
    assert all(env["has_entries"] is False for env in overlay_envs)
    assert all(env["entries"] == [] for env in overlay_envs)
    assert all(env["butler"] == "health" for env in overlay_envs)
    assert result["total_entries"] == 0


async def test_appointment_outside_window_is_dropped():
    far = _TODAY + timedelta(days=60)  # beyond the 14-day window
    appts = [_appointment(valid_at=_sgt_noon(far))]
    result, cap, _ = await _run(appointments=appts)
    assert result["appointment_entries"] == 0
    overlay_envs = [v for k, v in cap.store.items() if k.startswith(OVERLAY_KEY_PREFIX)]
    assert len(overlay_envs) == HEALTH_OVERLAY_LOOKAHEAD_DAYS + 1
    assert all(env["has_entries"] is False for env in overlay_envs)


# ---------------------------------------------------------------------------
# Idempotent upsert
# ---------------------------------------------------------------------------


async def test_rerun_upserts_same_keys():
    appts = [_appointment(valid_at=_sgt_noon(date_cls(2026, 6, 23)))]
    meds = [_medication()]
    _, cap1, _ = await _run(appointments=appts, medications=meds)
    keys_after_first = sorted(k for k in cap1.store if k.startswith(OVERLAY_KEY_PREFIX))

    _, cap2, _ = await _run(appointments=appts, medications=meds, seed=cap1.store)
    keys_after_second = sorted(k for k in cap2.store if k.startswith(OVERLAY_KEY_PREFIX))
    assert keys_after_first == keys_after_second
    assert len(keys_after_second) == HEALTH_OVERLAY_LOOKAHEAD_DAYS + 1


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------


async def test_prune_removes_stale_past_dates():
    seed = {
        overlay_key("2026-06-19"): {"butler": "health", "date": "2026-06-19"},  # stale
        overlay_key("2026-06-20"): {"butler": "health", "date": "2026-06-20"},  # stale
        "briefing/daily/2026-06-19": {"unrelated": True},  # different prefix, untouched
    }
    result, cap, _ = await _run(seed=seed)
    assert overlay_key("2026-06-19") in cap.deleted
    assert overlay_key("2026-06-20") in cap.deleted
    assert overlay_key(_TODAY_STR) not in cap.deleted  # today retained
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

    health_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("health", {})
    assert "calendar_overlay_contribution" in health_jobs
    assert callable(health_jobs["calendar_overlay_contribution"])

    # Pre-existing health entries are untouched.
    assert "daily_briefing_contribution" in health_jobs
    assert "insight_scan" in health_jobs

    resolved = _resolve_deterministic_schedule_job_name(
        butler_name="health",
        trigger_source="schedule:calendar_overlay_contribution",
        job_name="calendar_overlay_contribution",
    )
    assert resolved == "calendar_overlay_contribution"


def test_handler_not_registered_for_unrelated_butlers():
    """Non-contributing butlers must NOT have an overlay handler bleed in."""
    from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

    for butler in ("general", "education", "home", "lifestyle"):
        jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get(butler, {})
        assert "calendar_overlay_contribution" not in jobs, butler


async def test_no_llm_session_spawned():
    """The job path must not construct or invoke any LLM spawner."""
    cap = _StateCapture()
    pool = _FakePool()
    with (
        patch("butlers.jobs.calendar_overlay.today_sgt", return_value=_TODAY),
        patch("butlers.jobs.calendar_overlay.state_set", new=cap.state_set),
        patch("butlers.jobs.calendar_overlay.state_list", new=cap.state_list),
        patch("butlers.jobs.calendar_overlay.state_delete", new=cap.state_delete),
    ):
        result = await run_health_calendar_overlay_contribution(pool, None)
    assert result["butler"] == "health"
    # Only appointment + medication queries were issued — no LLM, no other fan-out.
    assert len(pool.fetch_calls) == 2
