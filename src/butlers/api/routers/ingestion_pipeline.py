"""Ingestion pipeline stats endpoint — funnel aggregates for the /ingestion dashboard.

Provides:

- ``router`` — endpoints under ``/api/ingestion/pipeline``

Endpoints
---------
GET /api/ingestion/pipeline?window=24h — pipeline funnel stats (ingested, filtered, errored, etc.)

Stats are sourced from Prometheus via PromQL through
``src/butlers/modules/metrics/prometheus.py``.  Results are cached for 60
seconds per (window) key.  On any Prometheus failure the endpoint returns
zeros with ``aggregates_available: false`` — it NEVER returns HTTP 500.

Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
      connector-state-aggregates/spec.md
      ingestion-event-registry/spec.md  (Pipeline Stats Endpoint requirement)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Literal

from fastapi import APIRouter, Query

from butlers.modules.metrics.prometheus import async_query, async_query_range

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingestion/pipeline", tags=["ingestion"])

# ---------------------------------------------------------------------------
# TTL cache — 60-second window per query key
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 60.0
_pipeline_cache: dict[str, tuple[float, dict]] = {}
_pipeline_cache_lock = asyncio.Lock()


def _get_prometheus_url() -> str | None:
    """Return the configured Prometheus base URL from the environment, or None if unset/empty."""
    return os.environ.get("PROMETHEUS_URL") or None


# ---------------------------------------------------------------------------
# Degraded-mode envelope
# ---------------------------------------------------------------------------

_DEGRADED_WINDOWS: dict[str, list[int]] = {
    "1h": [0] * 1,
    "24h": [0] * 24,
    "7d": [0] * 7,
}

WindowLiteral = Literal["1h", "24h", "7d"]


def _degraded_response(window: str) -> dict:
    """Return zeros with aggregates_available=false for degraded mode."""
    return {
        "window": window,
        "aggregates_available": False,
        "ingested": 0,
        "filtered": 0,
        "errored": 0,
        "routed_by_butler": {},
        "spark24h": [0] * 24,
        "rate1h": 0.0,
        "routed_pct": 0.0,
        "filtered24h": 0,
    }


# ---------------------------------------------------------------------------
# PromQL helpers
# ---------------------------------------------------------------------------

_SPARK24H_QUERY = "sum(increase(ingestion_events_ingested_total[1h]))"
_SPARK24H_STEP = "3600"  # 1 hour in seconds


async def _query_spark24h_buckets(prom_url: str) -> list[int] | None:
    """Fetch true hourly buckets for the 24h sparkline via a Prometheus range query.

    Returns a list of exactly 24 ints (oldest bucket first, most-recent last),
    or ``None`` on any Prometheus error / empty matrix (caller falls back to
    uniform distribution via ``_build_spark24h``).
    """
    now = int(time.time())
    start = str(now - 24 * 3600)
    end = str(now)

    results = await async_query_range(
        prom_url,
        _SPARK24H_QUERY,
        start=start,
        end=end,
        step=_SPARK24H_STEP,
    )

    if not results or "error" in results[0]:
        if results:
            logger.warning(
                "spark24h range query failed: %s", results[0].get("error", "unknown error")
            )
        else:
            logger.debug("spark24h range query returned empty matrix")
        return None

    # The first (and only, because the query is a sum) series contains the values.
    try:
        raw_values: list[list] = results[0]["values"]
    except (KeyError, IndexError):
        logger.warning("spark24h range query: unexpected result shape")
        return None

    if not raw_values:
        return None

    # Convert string values to ints.  Prometheus may return 24 or 25 points
    # depending on boundary alignment; take the last 24 to stay within window.
    buckets = [int(float(v)) for _, v in raw_values]
    if len(buckets) > 24:
        buckets = buckets[-24:]
    elif len(buckets) < 24:
        # Pad the front with zeros so the caller always gets exactly 24 buckets.
        buckets = [0] * (24 - len(buckets)) + buckets

    return buckets


async def _fetch_pipeline_stats(prom_url: str, window: str) -> dict:
    """Fetch pipeline funnel stats from Prometheus.

    Returns the stats dict on success, or the degraded-mode dict on any failure.

    Prometheus metric names expected:
    - ``ingestion_events_ingested_total``   — counter of ingested events
    - ``ingestion_events_filtered_total``   — counter of filtered events
    - ``ingestion_events_errored_total``    — counter of errored events
    - ``ingestion_events_routed_total``     — counter of routed events (label: butler_name)

    The 24-bucket ``spark24h`` is derived from a range query;
    ``rate1h`` and ``filtered24h`` from instant queries.
    """
    prom_window = {"1h": "1h", "24h": "24h", "7d": "7d"}[window]

    # ---- ingested total for the window ----
    ingested_results = await async_query(
        prom_url,
        f"sum(increase(ingestion_events_ingested_total[{prom_window}]))",
    )
    if ingested_results and "error" in ingested_results[0]:
        logger.warning(
            "pipeline_stats: Prometheus error for ingested [%s]: %s",
            window,
            ingested_results[0]["error"],
        )
        return _degraded_response(window)
    ingested = _extract_scalar(ingested_results)

    # ---- filtered total ----
    filtered_results = await async_query(
        prom_url,
        f"sum(increase(ingestion_events_filtered_total[{prom_window}]))",
    )
    if filtered_results and "error" in filtered_results[0]:
        logger.warning("pipeline_stats: Prometheus error for filtered [%s]", window)
        return _degraded_response(window)
    filtered = _extract_scalar(filtered_results)

    # ---- errored total ----
    errored_results = await async_query(
        prom_url,
        f"sum(increase(ingestion_events_errored_total[{prom_window}]))",
    )
    if errored_results and "error" in errored_results[0]:
        logger.warning("pipeline_stats: Prometheus error for errored [%s]", window)
        return _degraded_response(window)
    errored = _extract_scalar(errored_results)

    # ---- per-butler routed breakdown ----
    routed_results = await async_query(
        prom_url,
        f"sum by (butler_name) (increase(ingestion_events_routed_total[{prom_window}]))",
    )
    routed_by_butler: dict[str, int] = {}
    if routed_results and "error" not in routed_results[0]:
        for series in routed_results:
            butler_name = series.get("metric", {}).get("butler_name", "unknown")
            try:
                routed_by_butler[butler_name] = int(float(series["value"][1]))
            except (KeyError, ValueError, IndexError):
                pass

    # ---- rate1h (events per minute over trailing 60 min) ----
    rate_results = await async_query(
        prom_url,
        "sum(rate(ingestion_events_ingested_total[1h])) * 60",
    )
    rate1h = 0.0
    if rate_results and "error" not in rate_results[0]:
        rate1h = _extract_float(rate_results)

    # ---- filtered24h (filtered events in last 24h) ----
    filtered24h_results = await async_query(
        prom_url,
        "sum(increase(ingestion_events_filtered_total[24h]))",
    )
    filtered24h = 0
    if filtered24h_results and "error" not in filtered24h_results[0]:
        filtered24h = int(_extract_scalar(filtered24h_results))

    # ---- spark24h — 24 hourly buckets (always 24h, regardless of window) ----
    # Attempt a true per-hour range query; fall back to uniform distribution when
    # Prometheus is unavailable or returns no data.
    spark24h_buckets = await _query_spark24h_buckets(prom_url)
    if spark24h_buckets is None:
        spark24h: list[int] = _build_spark24h(int(ingested))
    else:
        spark24h = spark24h_buckets

    # ---- routed_pct ----
    total_events = ingested + filtered + errored
    routed_total = sum(routed_by_butler.values())
    routed_pct = (routed_total / total_events * 100.0) if total_events > 0 else 0.0

    return {
        "window": window,
        "aggregates_available": True,
        "ingested": int(ingested),
        "filtered": int(filtered),
        "errored": int(errored),
        "routed_by_butler": routed_by_butler,
        "spark24h": spark24h,
        "rate1h": round(rate1h, 4),
        "routed_pct": round(routed_pct, 2),
        "filtered24h": filtered24h,
    }


def _extract_scalar(results: list) -> float:
    """Extract first scalar value from an instant PromQL result."""
    if not results:
        return 0.0
    try:
        return float(results[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return 0.0


def _extract_float(results: list) -> float:
    """Extract first float value from an instant PromQL result."""
    return _extract_scalar(results)


def _build_spark24h(ingested_24h: int) -> list[int]:
    """Build a 24-bucket spark array.

    In degraded mode or when we only have a total (not a range breakdown),
    distribute the total evenly across 24 hourly buckets.  Callers that
    have a proper range result should build their own bucket array.
    """
    if ingested_24h <= 0:
        return [0] * 24
    base = ingested_24h // 24
    remainder = ingested_24h % 24
    return [base + (1 if i < remainder else 0) for i in range(24)]


# ---------------------------------------------------------------------------
# Cached fetch
# ---------------------------------------------------------------------------


async def _get_cached_pipeline_stats(window: str) -> dict:
    """Return cached pipeline stats for the given window (60s TTL).

    Fetches fresh data on cache miss or TTL expiry.  Falls back to degraded
    mode (zeros, aggregates_available=false) when Prometheus is unreachable.
    """
    async with _pipeline_cache_lock:
        now = time.monotonic()
        cached = _pipeline_cache.get(window)
        if cached is not None:
            ts, data = cached
            if now - ts < _CACHE_TTL_SECONDS:
                logger.debug("pipeline_stats: cache hit for window=%s", window)
                return data

    # Outside the lock for the slow Prometheus call
    prom_url = _get_prometheus_url()
    if not prom_url:
        logger.debug("pipeline_stats: PROMETHEUS_URL not set — degraded mode")
        data = _degraded_response(window)
    else:
        try:
            data = await _fetch_pipeline_stats(prom_url, window)
        except Exception:
            logger.warning(
                "pipeline_stats: unexpected error fetching from Prometheus", exc_info=True
            )
            data = _degraded_response(window)

    # Update cache
    async with _pipeline_cache_lock:
        _pipeline_cache[window] = (time.monotonic(), data)
        logger.debug(
            "pipeline_stats: cache updated for window=%s aggregates_available=%s",
            window,
            data.get("aggregates_available"),
        )

    return data


# ---------------------------------------------------------------------------
# GET /api/ingestion/pipeline
# ---------------------------------------------------------------------------


@router.get("")
async def get_pipeline_stats(
    window: WindowLiteral = Query(
        "24h",
        description="Time window for aggregate counters. One of: 1h, 24h, 7d.",
    ),
) -> dict:
    """Return aggregate pipeline funnel statistics.

    Counters cover the requested time window:
    - ``ingested``: total events ingested
    - ``filtered``: total events filtered
    - ``errored``: total events errored
    - ``routed_by_butler``: per-butler routing breakdown
    - ``spark24h``: 24-bucket hourly sparkline of accepted events (always 24h)
    - ``rate1h``: events per minute over the trailing 60 minutes
    - ``routed_pct``: percentage of events routed vs. total
    - ``filtered24h``: count of filtered events in the last 24 hours
    - ``aggregates_available``: false when Prometheus is unreachable

    Results are served from a 60-second TTL cache.

    Supported ``window`` values: ``1h``, ``24h``, ``7d``.
    Returns HTTP 400 for unsupported window values (FastAPI validates the Literal).
    NEVER returns HTTP 500 — Prometheus failures produce a degraded-mode 200.
    """
    data = await _get_cached_pipeline_stats(window)
    return data
