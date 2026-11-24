"""GET /api/health/baselines (bu-q7vx1q.77); synthetic rows, mocked pool."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.core import baselines as baselines_core
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_METRICS = ("resting_hr", "hrv", "sleep_duration")


def _router_module():
    return importlib.import_module("butlers.api._roster.health.router")


def _app(pool: Any = None, *, pool_error: Exception | None = None):
    db = MagicMock(spec=DatabaseManager)
    if pool_error is not None:
        db.pool.side_effect = pool_error
    else:
        db.pool.return_value = pool
    app = create_app(api_key="")
    app.dependency_overrides[get_db] = lambda: db
    return app


def _row(key: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "metric_key": key,
        "bucket_value": "all",
        "method_version": "mad-v1",
        "status": "ready",
        "measurability": "measurable",
        "unit": "bpm",
        "center": 56.0,
        "dispersion": 2.0,
        "k_mad": 2.6,
        "n_observed": 54,
        "n_expected": 60,
        "coverage": 0.9,
        "input_digest": "d",
        "computed_at": datetime.now(UTC) - timedelta(hours=4),
    }
    row.update(overrides)
    return row


def _pool(*rows: dict[str, Any]) -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=list(rows))
    return pool


async def _get(app) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/health/baselines")


async def test_ready_baseline_carries_band_counts_and_plaque_inputs() -> None:
    response = await _get(_app(_pool(_row("resting_hr"))))
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True and body["degraded_reason"] is None
    by_key = {b["metric_key"]: b for b in body["baselines"]}
    assert set(by_key) == set(_METRICS)
    hr = by_key["resting_hr"]
    assert hr["state"] == "ready"
    assert hr["band"] == {"low": pytest.approx(50.8), "high": pytest.approx(61.2)}
    assert (hr["n_observed"], hr["n_expected"], hr["coverage"]) == (54, 60, 0.9)
    assert hr["unit"] == "bpm" and hr["stale"] is False


async def test_every_health_metric_is_listed_and_missing_ones_are_no_baseline() -> None:
    response = await _get(_app(_pool(_row("resting_hr"))))
    by_key = {b["metric_key"]: b for b in response.json()["baselines"]}
    for key in ("hrv", "sleep_duration"):
        assert by_key[key]["state"] == "no_baseline"
        assert by_key[key]["band"] is None
        assert by_key[key]["computed_at"] is None


async def test_no_rows_at_all_is_available_with_three_no_baseline_entries() -> None:
    body = (await _get(_app(_pool()))).json()
    assert body["available"] is True
    assert [b["state"] for b in body["baselines"]] == ["no_baseline"] * 3
    assert [b["metric_key"] for b in body["baselines"]] == list(_METRICS)


async def test_insufficient_history_has_the_counts_but_no_band() -> None:
    row = _row(
        "hrv", status="insufficient_history", center=None, dispersion=None, n_observed=9, unit="ms"
    )
    body = (await _get(_app(_pool(row)))).json()
    hrv = {b["metric_key"]: b for b in body["baselines"]}["hrv"]
    assert hrv["state"] == "insufficient_history"
    assert hrv["band"] is None and hrv["center"] is None
    assert (hrv["n_observed"], hrv["n_expected"]) == (9, 60)


async def test_unmeasurable_has_no_band() -> None:
    row = _row("resting_hr", status="insufficient_history", measurability="unmeasurable")
    body = (await _get(_app(_pool(row)))).json()
    hr = {b["metric_key"]: b for b in body["baselines"]}["resting_hr"]
    assert hr["state"] == "unmeasurable" and hr["band"] is None


async def test_stale_row_reads_stale_with_its_computed_date_and_no_band() -> None:
    computed = datetime.now(UTC) - timedelta(days=5)
    body = (await _get(_app(_pool(_row("sleep_duration", computed_at=computed, unit="h"))))).json()
    sleep = {b["metric_key"]: b for b in body["baselines"]}["sleep_duration"]
    assert sleep["state"] == "stale" and sleep["stale"] is True
    assert sleep["band"] is None
    assert datetime.fromisoformat(sleep["computed_at"]) == computed


async def test_read_failure_is_a_degraded_envelope_not_an_empty_list() -> None:
    pool = _pool()
    pool.fetch.side_effect = RuntimeError("relation metric_baselines does not exist")
    response = await _get(_app(pool))
    assert response.status_code == 200
    assert response.json() == {
        "baselines": None,
        "available": False,
        "degraded_reason": "baselines_unavailable",
    }


async def test_unavailable_pool_is_a_degraded_envelope_too() -> None:
    response = await _get(_app(pool_error=KeyError("health")))
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["baselines"] is None


async def test_the_route_only_reads_the_callers_pool_with_one_select() -> None:
    pool = _pool(_row("resting_hr"))
    app = _app(pool)
    await _get(app)
    app.dependency_overrides[get_db]().pool.assert_called_once_with("health")
    pool.fetch.assert_awaited_once()
    pool.execute.assert_not_called()
    sql, keys, version = pool.fetch.await_args.args
    assert version == baselines_core.METHOD_VERSION
    assert "FROM metric_baselines" in sql
    assert keys == list(_METRICS)


async def test_each_metrics_own_refresh_interval_decides_stale(monkeypatch) -> None:
    from dataclasses import replace

    module = _router_module()
    slow = tuple(replace(m, refresh_interval=timedelta(days=7)) for m in module.BASELINE_METRICS)
    monkeypatch.setattr(module, "BASELINE_METRICS", slow)
    old = _row("resting_hr", computed_at=datetime.now(UTC) - timedelta(days=5))
    body = (await _get(_app(_pool(old)))).json()
    hr = {b["metric_key"]: b for b in body["baselines"]}["resting_hr"]
    assert hr["state"] == "ready" and hr["stale"] is False
