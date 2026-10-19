"""Wiring of the daily finance anomaly insight scan onto the spend baselines (bu-q7vx1q.79)."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import pytest

import butlers.jobs._roster.finance_jobs  # noqa: F401  (registers the module in sys.modules)


def _fj():
    return sys.modules["butlers.jobs._roster.finance_jobs"]


class _Conn:
    async def execute(self, *args: Any) -> None:
        return None


class _Pool:
    @asynccontextmanager
    async def acquire(self):
        yield _Conn()


@pytest.fixture
def calls(monkeypatch) -> list[Any]:
    fj = _fj()
    log: list[Any] = []

    async def _zone(conn):
        return ZoneInfo("UTC")

    async def _refresh(conn, *, now, zone):
        log.append("refresh")
        return []

    async def _retire(conn):
        log.append("retire")
        return 0

    async def _scan(conn, days_back=1, sensitivity="medium"):
        log.append("scan")
        return {"status": "ok", "anomalies": ANOMALIES}

    async def _propose(pool, **kwargs):
        log.append(kwargs)
        return "accepted"

    monkeypatch.setattr(fj, "resolve_budget_zone", _zone)
    monkeypatch.setattr(fj, "refresh_category_baselines", _refresh)
    monkeypatch.setattr(fj, "retire_spending_baseline_facts", _retire)
    monkeypatch.setattr(fj, "anomaly_scan", _scan)
    monkeypatch.setattr(fj, "_propose", _propose)
    return log


ANOMALIES: list[dict[str, Any]] = [
    {
        "transaction_id": None,
        "type": "category_velocity_anomaly",
        "severity": "medium",
        "category": "dining",
        "currency": "SGD",
        "explanation": "above",
        "baseline_evidence": {"n_observed": 10, "n_expected": 26},
    },
    {
        "transaction_id": None,
        "type": "category_velocity_anomaly",
        "severity": "medium",
        "category": "dining",
        "currency": "USD",
        "explanation": "above",
        "baseline_evidence": {"n_observed": 6, "n_expected": 26},
    },
    {
        "transaction_id": "t1",
        "type": "amount_anomaly",
        "severity": "low",
        "merchant": "Cafe",
        "explanation": "big",
    },
]


async def test_bands_are_produced_and_facts_retired_before_the_scan(calls) -> None:
    await _fj().run_anomaly_insight_scan(_Pool())
    assert calls[:3] == ["refresh", "retire", "scan"]


async def test_a_failed_band_refresh_does_not_blind_the_merchant_checks(calls, monkeypatch) -> None:
    async def _boom(conn, *, now, zone):
        raise asyncpg.UndefinedTableError("metric_baselines")

    monkeypatch.setattr(_fj(), "refresh_category_baselines", _boom)
    result = await _fj().run_anomaly_insight_scan(_Pool())
    assert result["anomalies_found"] == 3
    assert result["accepted"] == 3


async def test_category_candidates_are_keyed_by_currency_and_carry_evidence(calls) -> None:
    await _fj().run_anomaly_insight_scan(_Pool())
    proposals = [c for c in calls if isinstance(c, dict)]
    keys = sorted(p["dedup_key"] for p in proposals)
    assert any(":category-dining-SGD:" in k for k in keys)
    assert any(":category-dining-USD:" in k for k in keys)
    by_key = {p["dedup_key"]: p["metadata"] for p in proposals}
    sgd = next(m for k, m in by_key.items() if "category-dining-SGD" in k)
    assert sgd["baseline_evidence"] == {"n_observed": 10, "n_expected": 26}
    merchant = next(m for k, m in by_key.items() if ":t1:" in k)
    assert "baseline_evidence" not in merchant
