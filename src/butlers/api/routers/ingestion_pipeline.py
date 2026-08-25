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

"Failure" includes a well-formed response we cannot read: an unparseable or
non-finite scalar, and an unusable sparkline matrix, both degrade the whole
envelope (bu-0m31b).  A zero meaning "Prometheus said zero" and a zero meaning
"we could not read Prometheus" must not be the same zero on the wire, so the
only zeros published with ``aggregates_available: true`` are ones Prometheus
actually reported — including an empty result set, which is a real observation
of "no series, therefore no events".

Spec: openspec/changes/restore-ingestion-console-spec-coverage/specs/
      connector-state-aggregates/spec.md  (the capability; the archived
      redesign-ingestion-dispatch-console path this used to cite no longer
      exists)
      openspec/changes/honest-ingestion-aggregate-availability/specs/
      connector-state-aggregates/spec.md  (degraded-mode contract, bu-0m31b)
      openspec/specs/ingestion-event-registry/spec.md  (Pipeline Stats Endpoint)
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db_manager
from butlers.modules.metrics.prometheus import async_query, async_query_range

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingestion/pipeline", tags=["ingestion"])


def _get_db_manager_optional() -> DatabaseManager | None:
    """Return the DatabaseManager singleton, or None when not yet initialized.

    Mirrors ``_get_pricing_optional`` in ``ingestion_events.py``: the backlog
    fields below are best-effort (degraded-envelope pattern, see CLAUDE.md).
    A daemon or test harness that hasn't wired the DatabaseManager yet must
    not turn this endpoint into a 500 — the funnel stats above already
    tolerate a missing ``PROMETHEUS_URL`` the same way.
    """
    try:
        return get_db_manager()
    except RuntimeError:
        return None


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

    Returns a list of exactly 24 ints (oldest bucket first, most-recent last)
    when the matrix is readable — including ``[0] * 24`` for an empty result
    set, because Prometheus answering "no series" for a
    ``sum(increase(...))`` is a real observation of zero.  Those zeros stay
    published under ``aggregates_available: true`` even when the instant
    ``ingested`` query over the same window reported a non-zero total: the flag
    is contracted as "every published value is one Prometheus actually
    reported", not "the published values agree with each other", so lowering it
    here would report an unavailability that did not happen — and it is the
    single authority the connectors cross-summary reads too (bu-468ck).  The
    caller logs the disagreement instead; see :func:`_fetch_pipeline_stats`.

    Returns ``None`` when the matrix is *unreadable*: a query error, an
    unexpected result shape, a series carrying no points, or a bucket value
    that will not parse as a finite number.  The caller degrades the whole
    envelope in that case.  It used to spread the ingested total evenly across
    24 buckets instead, which drew a flat line Prometheus never reported and
    left ``aggregates_available: true`` over it (bu-0m31b).
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

    if results and "error" in results[0]:
        logger.warning("spark24h range query failed: %s", results[0].get("error", "unknown error"))
        return None

    if not results:
        # No series at all: the counter has no samples in the window. That is a
        # truthful zero, not an unreadable answer.
        logger.debug("spark24h range query returned an empty matrix — reading as zero")
        return [0] * 24

    # The first (and only, because the query is a sum) series contains the values.
    try:
        raw_values: list[list] = results[0]["values"]
    except (KeyError, IndexError, TypeError):
        logger.warning("spark24h range query: unexpected result shape")
        return None

    if not raw_values:
        logger.warning("spark24h range query: series carried no points")
        return None

    # Convert string values to ints.  Prometheus may return 24 or 25 points
    # depending on boundary alignment; take the last 24 to stay within window.
    try:
        buckets = [int(_finite(v)) for _, v in raw_values]
    except (TypeError, ValueError):
        logger.warning("spark24h range query: unparseable bucket value")
        return None
    if len(buckets) > 24:
        buckets = buckets[-24:]
    elif len(buckets) < 24:
        # Pad the front with zeros so the caller always gets exactly 24 buckets.
        buckets = [0] * (24 - len(buckets)) + buckets

    return buckets


async def _query_scalar(prom_url: str, query: str, *, label: str, window: str) -> float | None:
    """Run one instant query and return its scalar, or ``None`` if unreadable.

    ``None`` is the "we could not observe this" signal every caller must route
    to :func:`_degraded_response`; it covers both a Prometheus-reported error
    and a response whose value will not parse.
    """
    results = await async_query(prom_url, query)
    if results and "error" in results[0]:
        logger.warning(
            "pipeline_stats: Prometheus error for %s [%s]: %s",
            label,
            window,
            results[0]["error"],
        )
        return None
    value = _extract_scalar(results)
    if value is None:
        logger.warning("pipeline_stats: unreadable %s value [%s]: %r", label, window, results[0])
    return value


async def _query_routed_by_butler(
    prom_url: str, prom_window: str, *, window: str
) -> dict[str, int] | None:
    """Return the per-butler routed breakdown, or ``None`` if it is unreadable.

    A series whose value will not parse is a butler whose routed count is
    unknown. Dropping it and returning the rest understates ``routed_pct``
    without saying so, so the whole breakdown reads as unreadable instead.
    """
    results = await async_query(
        prom_url,
        f"sum by (butler_name) (increase(ingestion_events_routed_total[{prom_window}]))",
    )
    if results and "error" in results[0]:
        logger.warning(
            "pipeline_stats: Prometheus error for routed [%s]: %s", window, results[0]["error"]
        )
        return None

    breakdown: dict[str, int] = {}
    for series in results:
        butler_name = series.get("metric", {}).get("butler_name", "unknown")
        try:
            breakdown[butler_name] = int(_finite(series["value"][1]))
        except (KeyError, ValueError, IndexError, TypeError):
            logger.warning(
                "pipeline_stats: unreadable routed series for %s [%s]", butler_name, window
            )
            return None
    return breakdown


async def _fetch_pipeline_stats(prom_url: str, window: str) -> dict:
    """Fetch pipeline funnel stats from Prometheus.

    Returns the stats dict on success, or the degraded-mode dict on any failure.
    A failure is any value the handler could not actually read — an errored
    query, an unparseable scalar, or an unusable sparkline matrix — never a
    substituted zero (bu-0m31b).

    Prometheus metric names expected:
    - ``ingestion_events_ingested_total``   — counter of ingested events
    - ``ingestion_events_filtered_total``   — counter of filtered events
    - ``ingestion_events_errored_total``    — counter of errored events
    - ``ingestion_events_routed_total``     — counter of routed events (label: butler_name)

    The 24-bucket ``spark24h`` is derived from a range query;
    ``rate1h`` and ``filtered24h`` from instant queries.
    """
    prom_window = {"1h": "1h", "24h": "24h", "7d": "7d"}[window]

    # ---- funnel scalars, in query order ----
    # Any value we cannot read degrades the whole envelope, and does so
    # immediately: a partially-observed funnel published under
    # aggregates_available=true is exactly the confident zero this endpoint
    # must not emit, and a Prometheus that failed one query is rarely about to
    # answer the next six.
    ingested = await _query_scalar(
        prom_url,
        f"sum(increase(ingestion_events_ingested_total[{prom_window}]))",
        label="ingested",
        window=window,
    )
    if ingested is None:
        return _degraded_response(window)

    filtered = await _query_scalar(
        prom_url,
        f"sum(increase(ingestion_events_filtered_total[{prom_window}]))",
        label="filtered",
        window=window,
    )
    if filtered is None:
        return _degraded_response(window)

    errored = await _query_scalar(
        prom_url,
        f"sum(increase(ingestion_events_errored_total[{prom_window}]))",
        label="errored",
        window=window,
    )
    if errored is None:
        return _degraded_response(window)

    # ---- per-butler routed breakdown ----
    routed_by_butler = await _query_routed_by_butler(prom_url, prom_window, window=window)
    if routed_by_butler is None:
        return _degraded_response(window)

    # ---- rate1h (events per minute over trailing 60 min) ----
    rate1h = await _query_scalar(
        prom_url,
        "sum(rate(ingestion_events_ingested_total[1h])) * 60",
        label="rate1h",
        window=window,
    )
    if rate1h is None:
        return _degraded_response(window)

    # ---- filtered24h (filtered events in last 24h) ----
    filtered24h = await _query_scalar(
        prom_url,
        "sum(increase(ingestion_events_filtered_total[24h]))",
        label="filtered24h",
        window=window,
    )
    if filtered24h is None:
        return _degraded_response(window)

    # ---- spark24h — 24 hourly buckets (always 24h, regardless of window) ----
    spark24h = await _query_spark24h_buckets(prom_url)
    if spark24h is None:
        return _degraded_response(window)

    # A flat-zero sparkline beside a non-zero ingested total is a real
    # contradiction — but only when the sparkline's fixed 24h actually covers
    # the requested window. At window=7d, events last week and none since is an
    # ordinary truthful shape, and warning on it would train operators to
    # ignore the warning that matters (bu-468ck).
    if window in ("1h", "24h") and ingested > 0 and not any(spark24h):
        logger.warning(
            "pipeline_stats: spark24h is flat zero over 24h while ingested[%s]=%d — "
            "Prometheus answered both queries, so the envelope stays available, "
            "but the sparkline and the total disagree",
            window,
            int(ingested),
        )

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
        "filtered24h": int(filtered24h),
    }


def _finite(raw: object) -> float:
    """Parse a PromQL sample value, rejecting ``NaN``/``Inf``.

    Prometheus renders "no samples in range" as ``NaN``, which ``float()``
    accepts. Coercing that to a number would publish an absence as a
    measurement, so it raises here and the caller degrades instead.
    """
    value = float(raw)  # type: ignore[arg-type]
    if not math.isfinite(value):
        raise ValueError(f"non-finite PromQL sample value: {raw!r}")
    return value


def _extract_scalar(results: list) -> float | None:
    """Extract the first scalar value from an instant PromQL result.

    An empty result set returns ``0.0``: Prometheus answering "no series" for a
    ``sum(increase(...))`` is a real observation that nothing happened.

    A result that IS present but cannot be read returns ``None`` — unknown, not
    zero. This used to return ``0.0`` for both, which put a value on the wire
    with an authority the code was never positioned to give (bu-0m31b).
    """
    if not results:
        return 0.0
    try:
        return _finite(results[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Failed/replay_pending backlog counts (bu-g4oiu) — DB truth, not Prometheus
# ---------------------------------------------------------------------------

# Statuses that represent an ingestion event stuck outside the normal
# ingested/skipped happy path. Growth here (especially 'failed') is exactly
# the silent-loss pattern bu-g4oiu found: 123 events sat unreplayed for up to
# three months because nothing surfaced the count anywhere.
_BACKLOG_STATUSES: tuple[str, ...] = ("failed", "replay_pending")

# error_detail prefix marking a 'failed' row as a deliberate write-off (payload
# confirmed recoverable but intentionally not re-triaged — e.g. stale content
# where re-triage would spawn a fresh LLM session/notification over months-old
# messages) rather than a genuine unresolved failure. Established by bu-g4oiu's
# live write-off of 99 events that were never actually processed by any butler
# (see PR #2974). Written-off rows must not masquerade as pending losses on
# this endpoint — they are a deliberate, reviewed disposition, not a silent gap.
_WRITTEN_OFF_PREFIX = "written-off"


async def _fetch_backlog_counts(db: DatabaseManager | None) -> dict:
    """Return current failed/replay_pending/written-off totals from public.ingestion_events.

    DB-truth counterpart to the Prometheus-backed funnel counters above:
    surfaces a growing ingestion-failure backlog directly on this endpoint so
    it is caught within a dashboard refresh instead of only by a manual audit
    (bu-g4oiu). Queried independently of the Prometheus fetch so it stays
    available even when Prometheus itself is degraded — this is the more
    important signal of the two during a Prometheus outage, not less.

    ``failed_total`` counts only genuine, unresolved failures — rows whose
    ``error_detail`` carries the ``_WRITTEN_OFF_PREFIX`` marker are split out
    into ``written_off_total`` instead, so a deliberate write-off never
    masquerades as (or hides behind) a live pending-loss count.

    Fails open per the degraded-envelope convention (CLAUDE.md): a missing
    DatabaseManager or a genuine query error yields ``backlog_available:
    False`` and ``None`` counts — never a fabricated ``0``, which would read
    as "no backlog" when the truth is "we couldn't check."
    """
    if db is None:
        return {
            "backlog_available": False,
            "failed_total": None,
            "replay_pending_total": None,
            "written_off_total": None,
        }

    try:
        pool = db.credential_shared_pool()
        rows = await pool.fetch(
            "SELECT status, (error_detail LIKE $2) AS is_written_off, COUNT(*) AS cnt "
            "FROM public.ingestion_events "
            "WHERE status = ANY($1::text[]) GROUP BY status, is_written_off",
            list(_BACKLOG_STATUSES),
            f"{_WRITTEN_OFF_PREFIX}%",
        )
    except Exception:
        logger.warning("pipeline_stats: backlog count query failed (non-fatal)", exc_info=True)
        return {
            "backlog_available": False,
            "failed_total": None,
            "replay_pending_total": None,
            "written_off_total": None,
        }

    failed_total = 0
    written_off_total = 0
    replay_pending_total = 0
    for row in rows:
        cnt = int(row["cnt"])
        if row["status"] == "failed":
            if row["is_written_off"]:
                written_off_total += cnt
            else:
                failed_total += cnt
        elif row["status"] == "replay_pending":
            replay_pending_total += cnt

    return {
        "backlog_available": True,
        "failed_total": failed_total,
        "replay_pending_total": replay_pending_total,
        "written_off_total": written_off_total,
    }


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


async def prometheus_aggregates_available(window: str = "24h") -> bool:
    """Return whether Prometheus actually answered the funnel queries for ``window``.

    The single authority for ``aggregates_available`` across the ingestion
    console, so every surface that publishes the flag publishes the same
    answer, earned the same way. It resolves through the 60-second TTL cache
    above: a warm entry costs nothing, and a cold one issues the real queries
    rather than assuming an answer that was never requested.

    Callers outside this module used to read ``_pipeline_cache`` directly and
    fall back to "``PROMETHEUS_URL`` is set" on a cold cache, which credited a
    down or unreachable Prometheus with the same ``true`` as one that answered
    (bu-avkvr).
    """
    stats = await _get_cached_pipeline_stats(window)
    return bool(stats.get("aggregates_available", False))


# ---------------------------------------------------------------------------
# GET /api/ingestion/pipeline
# ---------------------------------------------------------------------------


@router.get("")
async def get_pipeline_stats(
    window: WindowLiteral = Query(
        "24h",
        description="Time window for aggregate counters. One of: 1h, 24h, 7d.",
    ),
    db: DatabaseManager | None = Depends(_get_db_manager_optional),
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
    - ``aggregates_available``: false when any of the values above could not be
      observed — Prometheus unreachable or unconfigured, a query error, an
      unparseable or non-finite scalar, or an unusable sparkline matrix. When
      it is true, every zero above is a zero Prometheus actually reported
      (an empty result set included); the endpoint never substitutes one
      (bu-0m31b)

    Plus, independent of Prometheus (bu-g4oiu — DB truth, not windowed/derived):
    - ``failed_total``: current count of ``public.ingestion_events`` rows with
      status='failed' that are genuine, unresolved failures (excludes
      deliberate write-offs, see ``written_off_total``)
    - ``replay_pending_total``: current count with status='replay_pending'
      (replay requested but not yet reconciled)
    - ``written_off_total``: current count of status='failed' rows explicitly
      annotated as a reviewed write-off (``error_detail`` starts with
      ``"written-off"``) — payload confirmed recoverable but intentionally
      not re-triaged (e.g. stale content). Tracked separately so a deliberate
      write-off never masquerades as a live pending loss.
    - ``backlog_available``: false when the DatabaseManager or the backlog
      query itself is unavailable — ``failed_total``/``replay_pending_total``/
      ``written_off_total`` are ``None`` in that case, not ``0``

    Results are served from a 60-second TTL cache (Prometheus fields only —
    the backlog fields are queried fresh every request; the underlying query
    is a cheap indexed COUNT(*) grouped by status).

    Supported ``window`` values: ``1h``, ``24h``, ``7d``.
    Returns HTTP 400 for unsupported window values (FastAPI validates the Literal).
    NEVER returns HTTP 500 — Prometheus failures produce a degraded-mode 200,
    and backlog-count failures degrade only the backlog fields.
    """
    data = await _get_cached_pipeline_stats(window)
    backlog = await _fetch_backlog_counts(db)
    return {**data, **backlog}
