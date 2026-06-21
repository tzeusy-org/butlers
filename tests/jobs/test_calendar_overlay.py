"""Tests for butlers.jobs.calendar_overlay — finance overlay contribution job.

Covers the finance ``calendar_overlay_contribution`` deterministic job:
- per-date envelopes written across the lookahead window
- ``bill_due`` + ``subscription_renewal`` entries, priority-descending
- honest empty-state (``has_entries=false`` for dates with no events)
- idempotent upsert (re-run rewrites the same keys)
- prune of stale past-date entries (no-op when none)
- zero LLM (handler takes only ``(pool, job_args)``; no spawner)

No real database required — ``state_set`` / ``state_list`` / ``state_delete``
are patched to capture writes/deletes, and ``pool.fetch`` is routed by SQL text.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import patch

import pytest

from butlers.jobs.calendar_overlay import (
    FINANCE_OVERLAY_LOOKAHEAD_DAYS,
    OVERLAY_KEY_PREFIX,
    overlay_key,
    run_finance_calendar_overlay_contribution,
    sort_entries_by_priority,
)

pytestmark = pytest.mark.unit

_TODAY = date(2026, 6, 21)
_TODAY_STR = "2026-06-21"


# ---------------------------------------------------------------------------
# Fake pool + state-store capture harness
# ---------------------------------------------------------------------------


class _FakePool:
    """Routes ``pool.fetch`` to bills/subscriptions row lists by SQL content."""

    def __init__(
        self,
        *,
        bills: list[dict[str, Any]] | None = None,
        subs: list[dict[str, Any]] | None = None,
    ) -> None:
        self._bills = bills or []
        self._subs = subs or []
        self.fetch_calls: list[str] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append(sql)
        if "FROM bills" in sql:
            return self._bills
        if "FROM subscriptions" in sql:
            return self._subs
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


def _bill(payee: str, due: date, *, status: str = "pending", amount: float = 100.0):
    return {
        "payee": payee,
        "amount": amount,
        "currency": "SGD",
        "due_date": due,
        "status": status,
        "payment_method": "giro",
    }


def _sub(service: str, renew: date, *, amount: float = 9.99, auto_renew: bool = True):
    return {
        "service": service,
        "amount": amount,
        "currency": "USD",
        "next_renewal": renew,
        "auto_renew": auto_renew,
        "frequency": "monthly",
    }


async def _run(
    *,
    bills: list[dict[str, Any]] | None = None,
    subs: list[dict[str, Any]] | None = None,
    seed: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], _StateCapture, _FakePool]:
    pool = _FakePool(bills=bills, subs=subs)
    cap = _StateCapture(seed=seed)
    with (
        patch("butlers.jobs.calendar_overlay.today_sgt", return_value=_TODAY),
        patch("butlers.jobs.calendar_overlay.state_set", new=cap.state_set),
        patch("butlers.jobs.calendar_overlay.state_list", new=cap.state_list),
        patch("butlers.jobs.calendar_overlay.state_delete", new=cap.state_delete),
    ):
        result = await run_finance_calendar_overlay_contribution(pool, None)
    return result, cap, pool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_sort_entries_by_priority_is_descending_and_stable():
    entries = [
        {"kind": "a", "label": "low1", "priority": "low"},
        {"kind": "b", "label": "high1", "priority": "high"},
        {"kind": "c", "label": "med1", "priority": "medium"},
        {"kind": "d", "label": "high2", "priority": "high"},
    ]
    ordered = sort_entries_by_priority(entries)  # type: ignore[arg-type]
    assert [e["priority"] for e in ordered] == ["high", "high", "medium", "low"]
    # Stable within equal priority: high1 stays before high2.
    assert [e["label"] for e in ordered if e["priority"] == "high"] == ["high1", "high2"]


def test_overlay_key():
    assert overlay_key(_TODAY_STR) == f"{OVERLAY_KEY_PREFIX}{_TODAY_STR}"


# ---------------------------------------------------------------------------
# Envelope writing
# ---------------------------------------------------------------------------


async def test_writes_one_envelope_per_lookahead_date():
    result, cap, _ = await _run()
    expected = FINANCE_OVERLAY_LOOKAHEAD_DAYS + 1
    written_keys = [k for k in cap.store if k.startswith(OVERLAY_KEY_PREFIX)]
    assert len(written_keys) == expected
    assert result["dates_written"] == expected
    # Today and the horizon date are both present.
    assert overlay_key(_TODAY_STR) in cap.store
    assert overlay_key("2026-07-05") in cap.store  # today + 14


async def test_bill_and_subscription_entries_routed_to_their_dates():
    bills = [_bill("Credit Card", date(2026, 6, 23), amount=450.0)]
    subs = [_sub("Spotify", date(2026, 6, 28))]
    result, cap, _ = await _run(bills=bills, subs=subs)

    bill_env = cap.store[overlay_key("2026-06-23")]
    assert bill_env["has_entries"] is True
    assert bill_env["entries"][0]["kind"] == "bill_due"
    assert bill_env["entries"][0]["label"] == "Credit Card"
    assert bill_env["entries"][0]["meta"]["amount"] == 450.0
    assert bill_env["entries"][0]["meta"]["currency"] == "SGD"

    sub_env = cap.store[overlay_key("2026-06-28")]
    assert sub_env["entries"][0]["kind"] == "subscription_renewal"
    assert sub_env["entries"][0]["label"] == "Spotify"
    assert sub_env["entries"][0]["meta"]["auto_renew"] is True

    assert result["bill_due_entries"] == 1
    assert result["subscription_renewal_entries"] == 1
    assert result["total_entries"] == 2


async def test_entries_ordered_priority_descending_on_shared_date():
    # A bill due today (high) and a subscription renewing today (medium) on the
    # same date — the bill must sort first.
    bills = [_bill("Electric", _TODAY, amount=80.0)]
    subs = [_sub("iCloud", _TODAY)]
    _, cap, _ = await _run(bills=bills, subs=subs)

    env = cap.store[overlay_key(_TODAY_STR)]
    kinds = [e["kind"] for e in env["entries"]]
    priorities = [e["priority"] for e in env["entries"]]
    assert kinds == ["bill_due", "subscription_renewal"]
    assert priorities == ["high", "medium"]


async def test_overdue_bill_is_high_priority():
    bills = [_bill("Late Rent", _TODAY, status="overdue")]
    _, cap, _ = await _run(bills=bills)
    env = cap.store[overlay_key(_TODAY_STR)]
    assert env["entries"][0]["priority"] == "high"
    assert env["entries"][0]["meta"]["status"] == "overdue"


async def test_far_future_bill_is_low_priority():
    bills = [_bill("Insurance", date(2026, 7, 2))]  # 11 days out
    _, cap, _ = await _run(bills=bills)
    env = cap.store[overlay_key("2026-07-02")]
    assert env["entries"][0]["priority"] == "low"


# ---------------------------------------------------------------------------
# Honest empty-state
# ---------------------------------------------------------------------------


async def test_empty_domain_writes_has_entries_false_for_every_date():
    result, cap, _ = await _run()
    overlay_envs = [v for k, v in cap.store.items() if k.startswith(OVERLAY_KEY_PREFIX)]
    assert all(env["has_entries"] is False for env in overlay_envs)
    assert all(env["entries"] == [] for env in overlay_envs)
    assert all(env["butler"] == "finance" for env in overlay_envs)
    assert result["total_entries"] == 0


# ---------------------------------------------------------------------------
# Idempotent upsert
# ---------------------------------------------------------------------------


async def test_rerun_upserts_same_keys():
    bills = [_bill("Credit Card", date(2026, 6, 23))]
    _, cap1, _ = await _run(bills=bills)
    keys_after_first = sorted(k for k in cap1.store if k.startswith(OVERLAY_KEY_PREFIX))

    # Re-run with the prior store seeded — keys must be overwritten, not duplicated.
    _, cap2, _ = await _run(bills=bills, seed=cap1.store)
    keys_after_second = sorted(k for k in cap2.store if k.startswith(OVERLAY_KEY_PREFIX))
    assert keys_after_first == keys_after_second
    # state_set was called once per lookahead date (upsert), no extra keys appeared.
    assert len(keys_after_second) == FINANCE_OVERLAY_LOOKAHEAD_DAYS + 1


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------


async def test_prune_removes_stale_past_dates():
    seed = {
        overlay_key("2026-06-19"): {"butler": "finance", "date": "2026-06-19"},  # stale
        overlay_key("2026-06-20"): {"butler": "finance", "date": "2026-06-20"},  # stale
        "briefing/daily/2026-06-19": {"unrelated": True},  # different prefix, untouched
    }
    result, cap, _ = await _run(seed=seed)
    assert overlay_key("2026-06-19") in cap.deleted
    assert overlay_key("2026-06-20") in cap.deleted
    assert overlay_key(_TODAY_STR) not in cap.deleted  # today is retained
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

    finance_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("finance", {})
    assert "calendar_overlay_contribution" in finance_jobs
    assert callable(finance_jobs["calendar_overlay_contribution"])

    # Pre-existing finance entries are untouched.
    assert "daily_briefing_contribution" in finance_jobs
    assert "insight_scan" in finance_jobs

    resolved = _resolve_deterministic_schedule_job_name(
        butler_name="finance",
        trigger_source="schedule:calendar_overlay_contribution",
        job_name="calendar_overlay_contribution",
    )
    assert resolved == "calendar_overlay_contribution"


def test_handler_only_registered_for_finance():
    """Non-contributing butlers must NOT have a finance overlay handler bleed in."""
    from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

    for butler in ("general", "education", "home", "lifestyle"):
        jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get(butler, {})
        assert "calendar_overlay_contribution" not in jobs, butler


async def test_no_llm_session_spawned():
    """The job path must not construct or invoke any LLM spawner."""
    with patch("butlers.jobs.calendar_overlay.today_sgt", return_value=_TODAY):
        # If the job tried to spawn an LLM it would need a spawner argument; the
        # signature only accepts (pool, job_args). Running it through with the
        # state helpers patched proves it completes with pure SQL/Python.
        cap = _StateCapture()
        pool = _FakePool()
        with (
            patch("butlers.jobs.calendar_overlay.state_set", new=cap.state_set),
            patch("butlers.jobs.calendar_overlay.state_list", new=cap.state_list),
            patch("butlers.jobs.calendar_overlay.state_delete", new=cap.state_delete),
        ):
            result = await run_finance_calendar_overlay_contribution(pool, None)
    assert result["butler"] == "finance"
    # Only bills + subscriptions queries were issued — no LLM, no other fan-out.
    assert len(pool.fetch_calls) == 2
