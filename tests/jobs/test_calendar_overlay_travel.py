"""Tests for butlers.jobs.calendar_overlay — travel overlay contribution job.

Covers the travel ``calendar_overlay_contribution`` deterministic job:
- per-date envelopes written across the lookahead window
- ``departure`` / ``arrival`` / ``check_in`` / ``check_out`` entries
- SGT-date bucketing of TIMESTAMPTZ events (timezone-correct)
- honest empty-state (``has_entries=false`` for dates with no events)
- idempotent upsert (re-run rewrites the same keys)
- prune of stale past-date entries (no-op when none)
- zero LLM (handler takes only ``(pool, job_args)``; no spawner)

No real database required — ``state_set`` / ``state_list`` / ``state_delete``
are patched to capture writes/deletes, and the job's two ``pool.fetch`` reads
answer queued row sets (``tests.jobs.calendar_contribution_harness``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from datetime import date as date_cls
from typing import Any

import pytest

from butlers.jobs.calendar_overlay import (
    OVERLAY_KEY_PREFIX,
    TRAVEL_OVERLAY_LOOKAHEAD_DAYS,
    overlay_key,
    run_travel_calendar_overlay_contribution,
)
from butlers.testing.fake_pool import FakePool
from tests.jobs.calendar_contribution_harness import ContributionStateStore, run_overlay_job

pytestmark = pytest.mark.unit

_SGT = timezone(timedelta(hours=8), name="SGT")
_TODAY = date_cls(2026, 6, 21)
_TODAY_STR = "2026-06-21"


def _sgt_noon(d: date_cls) -> datetime:
    """A tz-aware timestamp clearly inside the SGT calendar date *d*."""
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=_SGT)


def _leg(
    *,
    departure_at: datetime,
    arrival_at: datetime,
    departure_city: str = "SIN",
    arrival_city: str = "NRT",
    type_: str = "flight",
    carrier: str | None = "SQ",
    pnr: str | None = "ABC123",
    seat: str | None = "12A",
    trip_name: str = "Tokyo",
    trip_id: str = "11111111-1111-1111-1111-111111111111",
) -> dict[str, Any]:
    return {
        "type": type_,
        "carrier": carrier,
        "departure_city": departure_city,
        "arrival_city": arrival_city,
        "departure_at": departure_at,
        "arrival_at": arrival_at,
        "pnr": pnr,
        "seat": seat,
        "confirmation_number": "CONF9",
        "trip_name": trip_name,
        "trip_id": trip_id,
    }


def _accom(
    *,
    check_in: datetime | None,
    check_out: datetime | None,
    name: str | None = "Granbell Hotel",
    type_: str = "hotel",
    trip_name: str = "Tokyo",
    trip_id: str = "22222222-2222-2222-2222-222222222222",
) -> dict[str, Any]:
    return {
        "type": type_,
        "name": name,
        "address": "Shinjuku",
        "check_in": check_in,
        "check_out": check_out,
        "confirmation_number": "HOTEL9",
        "trip_name": trip_name,
        "trip_id": trip_id,
    }


async def _run(
    *,
    legs: list[dict[str, Any]] | None = None,
    accommodations: list[dict[str, Any]] | None = None,
    seed: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], ContributionStateStore, FakePool]:
    return await run_overlay_job(
        run_travel_calendar_overlay_contribution,
        first=legs,
        second=accommodations,
        seed=seed,
        today=_TODAY,
    )


# ---------------------------------------------------------------------------
# Envelope writing
# ---------------------------------------------------------------------------


async def test_writes_one_envelope_per_lookahead_date():
    result, cap, _ = await _run()
    expected = TRAVEL_OVERLAY_LOOKAHEAD_DAYS + 1
    written_keys = [k for k in cap.store if k.startswith(OVERLAY_KEY_PREFIX)]
    assert len(written_keys) == expected
    assert result["dates_written"] == expected
    assert overlay_key(_TODAY_STR) in cap.store
    horizon = (_TODAY + timedelta(days=TRAVEL_OVERLAY_LOOKAHEAD_DAYS)).isoformat()
    assert overlay_key(horizon) in cap.store


async def test_departure_and_arrival_routed_to_their_dates():
    legs = [
        _leg(
            departure_at=_sgt_noon(date_cls(2026, 6, 23)),
            arrival_at=_sgt_noon(date_cls(2026, 6, 24)),
        )
    ]
    result, cap, _ = await _run(legs=legs)

    dep_env = cap.store[overlay_key("2026-06-23")]
    assert dep_env["has_entries"] is True
    assert dep_env["entries"][0]["kind"] == "departure"
    assert dep_env["entries"][0]["label"] == "SIN → NRT"
    assert dep_env["entries"][0]["meta"]["carrier"] == "SQ"
    assert dep_env["entries"][0]["meta"]["pnr"] == "ABC123"
    assert dep_env["entries"][0]["meta"]["trip_name"] == "Tokyo"

    arr_env = cap.store[overlay_key("2026-06-24")]
    assert arr_env["entries"][0]["kind"] == "arrival"
    assert arr_env["entries"][0]["label"] == "Arrive NRT"

    assert result["departure_entries"] == 1
    assert result["arrival_entries"] == 1
    assert result["total_entries"] == 2


async def test_check_in_and_check_out_routed_to_their_dates():
    accom = [
        _accom(
            check_in=_sgt_noon(date_cls(2026, 6, 24)),
            check_out=_sgt_noon(date_cls(2026, 6, 28)),
        )
    ]
    result, cap, _ = await _run(accommodations=accom)

    ci_env = cap.store[overlay_key("2026-06-24")]
    assert ci_env["entries"][0]["kind"] == "check_in"
    assert ci_env["entries"][0]["label"] == "Check-in: Granbell Hotel"
    assert ci_env["entries"][0]["meta"]["confirmation_number"] == "HOTEL9"

    co_env = cap.store[overlay_key("2026-06-28")]
    assert co_env["entries"][0]["kind"] == "check_out"
    assert co_env["entries"][0]["label"] == "Check-out: Granbell Hotel"

    assert result["check_in_entries"] == 1
    assert result["check_out_entries"] == 1


async def test_timezone_bucketing_uses_sgt_calendar_date():
    # 2026-06-22 23:30 UTC is 2026-06-23 07:30 SGT — must bucket onto the 23rd.
    dep = datetime(2026, 6, 22, 23, 30, tzinfo=UTC)
    legs = [_leg(departure_at=dep, arrival_at=dep + timedelta(hours=6))]
    _, cap, _ = await _run(legs=legs)
    assert cap.store[overlay_key("2026-06-23")]["has_entries"] is True
    assert cap.store[overlay_key("2026-06-22")]["has_entries"] is False


async def test_entries_ordered_priority_descending_on_shared_date():
    # A departure today (high) and a far-future arrival landing today (low not
    # possible) — use a departure today (high) plus a check-out today (medium).
    legs = [
        _leg(
            departure_at=_sgt_noon(_TODAY),
            arrival_at=_sgt_noon(_TODAY) + timedelta(hours=3),
        )
    ]
    accom = [
        _accom(
            check_in=_sgt_noon(date_cls(2026, 6, 10)),  # outside window, dropped
            check_out=_sgt_noon(_TODAY),
        )
    ]
    _, cap, _ = await _run(legs=legs, accommodations=accom)
    env = cap.store[overlay_key(_TODAY_STR)]
    priorities = [e["priority"] for e in env["entries"]]
    # departure today is high, check-out today is medium → high sorts first.
    assert priorities[0] == "high"
    assert "medium" in priorities


async def test_imminent_departure_is_high_priority():
    legs = [_leg(departure_at=_sgt_noon(_TODAY), arrival_at=_sgt_noon(_TODAY))]
    _, cap, _ = await _run(legs=legs)
    assert cap.store[overlay_key(_TODAY_STR)]["entries"][0]["priority"] == "high"


async def test_far_future_departure_is_low_priority():
    far = date_cls(2026, 7, 10)  # 19 days out, within 30-day window
    legs = [_leg(departure_at=_sgt_noon(far), arrival_at=_sgt_noon(far))]
    _, cap, _ = await _run(legs=legs)
    assert cap.store[overlay_key("2026-07-10")]["entries"][0]["priority"] == "low"


# ---------------------------------------------------------------------------
# Honest empty-state
# ---------------------------------------------------------------------------


async def test_empty_domain_writes_has_entries_false_for_every_date():
    result, cap, _ = await _run()
    overlay_envs = [v for k, v in cap.store.items() if k.startswith(OVERLAY_KEY_PREFIX)]
    assert all(env["has_entries"] is False for env in overlay_envs)
    assert all(env["entries"] == [] for env in overlay_envs)
    assert all(env["butler"] == "travel" for env in overlay_envs)
    assert result["total_entries"] == 0


async def test_event_outside_window_is_dropped():
    # Departure 60 days out — beyond the 30-day window; no entry written.
    far = _TODAY + timedelta(days=60)
    legs = [_leg(departure_at=_sgt_noon(far), arrival_at=_sgt_noon(far))]
    result, cap, _ = await _run(legs=legs)
    assert result["departure_entries"] == 0
    assert all(
        v["has_entries"] is False for k, v in cap.store.items() if k.startswith(OVERLAY_KEY_PREFIX)
    )


# ---------------------------------------------------------------------------
# Idempotent upsert
# ---------------------------------------------------------------------------


async def test_rerun_upserts_same_keys():
    legs = [
        _leg(
            departure_at=_sgt_noon(date_cls(2026, 6, 23)),
            arrival_at=_sgt_noon(date_cls(2026, 6, 23)),
        )
    ]
    _, cap1, _ = await _run(legs=legs)
    keys_after_first = sorted(k for k in cap1.store if k.startswith(OVERLAY_KEY_PREFIX))

    _, cap2, _ = await _run(legs=legs, seed=cap1.store)
    keys_after_second = sorted(k for k in cap2.store if k.startswith(OVERLAY_KEY_PREFIX))
    assert keys_after_first == keys_after_second
    assert len(keys_after_second) == TRAVEL_OVERLAY_LOOKAHEAD_DAYS + 1


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------


async def test_prune_removes_stale_past_dates():
    seed = {
        overlay_key("2026-06-19"): {"butler": "travel", "date": "2026-06-19"},  # stale
        overlay_key("2026-06-20"): {"butler": "travel", "date": "2026-06-20"},  # stale
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
    from butlers.scheduled_jobs import (
        _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
        _resolve_deterministic_schedule_job_name,
    )

    travel_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("travel", {})
    assert "calendar_overlay_contribution" in travel_jobs
    assert callable(travel_jobs["calendar_overlay_contribution"])

    # Pre-existing travel entries are untouched.
    assert "daily_briefing_contribution" in travel_jobs
    assert "insight_scan" in travel_jobs

    resolved = _resolve_deterministic_schedule_job_name(
        butler_name="travel",
        trigger_source="schedule:calendar_overlay_contribution",
        job_name="calendar_overlay_contribution",
    )
    assert resolved == "calendar_overlay_contribution"


async def test_no_llm_session_spawned():
    """The job path must not construct or invoke any LLM spawner."""
    # The handler takes only (pool, job_args); the strict pool refuses any read
    # or write beyond the two queued source fetches.
    result, _, pool = await _run()
    assert result["butler"] == "travel"
    # Only legs + accommodations queries were issued — no LLM, no other fan-out.
    assert pool.fetch.await_count == 2
