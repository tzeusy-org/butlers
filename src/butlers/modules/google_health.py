"""Google Health module — read-only MCP tools for Health butler wellness queries.

Provides eight read-only tools that query the Health butler's SPO fact store
directly (raw SQL against ``facts``, the same surface ``health_jobs.py``'s
insight scan reads) and return daemon-computed numbers.  Tools do NOT call
``health.googleapis.com`` directly; that is the connector's responsibility, and
they never delegate arithmetic to the calling model — every returned dict is a
finished result, never an ``{'instruction': ...}`` recipe for the model to
execute.

Daily wellness metrics (resting HR, HRV, SpO2, breathing rate, steps, active
minutes) are grouped by UTC calendar day and aggregated (mean/min/max) in SQL
so a duplicate same-day ingestion converges instead of producing two rows —
the aggregation is a pure function of the underlying facts, so repeated reads
are naturally idempotent without a separate materialized rollup table.

Credential resolution follows the Tier-2 security contract in
``about/heart-and-soul/security.md``: the primary Google account is resolved
via ``resolve_google_credentials()`` from ``google_credentials.py`` — refresh
tokens are read from ``public.entity_info`` on the companion entity, never via
``CredentialStore.resolve()`` or ``os.environ.get()``.

Configured via ``[modules.google_health]`` in ``butler.toml``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from butlers.modules.base import Module

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel error messages
# ---------------------------------------------------------------------------

_NOT_CONNECTED_ERROR = (
    "Google Health is not connected. Visit dashboard settings to grant the Google Health scopes."
)

_NO_ACCOUNT_ERROR = (
    "Google Health is not connected. "
    "Link a Google account with Google Health scopes via dashboard settings."
)

_NO_SLEEP_DATA = (
    "No sleep data ingested yet. "
    "Google Health data appears after the device syncs — "
    "typically within 30 minutes of wearing the device overnight."
)

_NO_DATA_TEMPLATE = (
    "No {metric} data ingested yet. "
    "Google Health data appears after the device syncs and the connector has run."
)


# ---------------------------------------------------------------------------
# Deterministic aggregation helpers
#
# These query ``facts`` directly (unqualified name, resolved via search_path,
# matching health_jobs.py) and compute the returned numbers in Python/SQL —
# never leaving arithmetic for the calling model to perform.
# ---------------------------------------------------------------------------


async def _last_fact_at(pool: Any, predicate: str) -> datetime | None:
    """Return the ``valid_at`` of the most recent active fact for *predicate*, if any."""
    return await pool.fetchval(
        "SELECT valid_at FROM facts"
        " WHERE predicate = $1 AND scope = 'health' AND validity = 'active'"
        " ORDER BY valid_at DESC NULLS LAST LIMIT 1",
        predicate,
    )


async def _empty_metric_result(
    pool: Any, predicate: str, days: int, *, metric_label: str | None = None
) -> dict[str, Any]:
    """Build an explicit, honest empty result naming the metric and last known day.

    Never a fabricated zero or average — an absence of facts in the window is
    reported as such, with the last day data existed (if any).
    """
    label = metric_label or predicate
    last_at = await _last_fact_at(pool, predicate)
    return {
        "found": False,
        "metric": predicate,
        "days": days,
        "last_data_at": last_at,
        "message": _NO_DATA_TEMPLATE.format(metric=label),
    }


async def _daily_numeric_rollup(
    pool: Any, predicate: str, metadata_key: str, since: datetime
) -> list[dict[str, Any]]:
    """Group *predicate* facts by UTC calendar day and aggregate one metadata field.

    ``metadata_key`` is always a hardcoded literal supplied by call sites in
    this module (never derived from tool arguments), so it is safe to
    interpolate into the query text alongside the already-interpolated
    ``measurement_{type}`` predicate pattern used throughout this codebase.
    """
    rows = await pool.fetch(
        f"""
        SELECT DATE(valid_at AT TIME ZONE 'UTC') AS day,
               AVG((metadata->>'{metadata_key}')::numeric) AS mean_value,
               MIN((metadata->>'{metadata_key}')::numeric) AS min_value,
               MAX((metadata->>'{metadata_key}')::numeric) AS max_value,
               COUNT(*) AS n
        FROM facts
        WHERE predicate = $1 AND scope = 'health' AND validity = 'active'
          AND valid_at >= $2 AND metadata ? '{metadata_key}'
        GROUP BY day
        ORDER BY day ASC
        """,
        predicate,
        since,
    )
    return [
        {
            "date": row["day"].isoformat(),
            "mean": float(row["mean_value"]) if row["mean_value"] is not None else None,
            "min": float(row["min_value"]) if row["min_value"] is not None else None,
            "max": float(row["max_value"]) if row["max_value"] is not None else None,
            "n": row["n"],
        }
        for row in rows
    ]


def _summarize_daily(daily: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize a list of per-day {mean, min, max} rows into one min/max/avg."""
    means = [d["mean"] for d in daily if d["mean"] is not None]
    mins = [d["min"] for d in daily if d["min"] is not None]
    maxes = [d["max"] for d in daily if d["max"] is not None]
    return {
        "min": min(mins) if mins else None,
        "max": max(maxes) if maxes else None,
        "avg": round(sum(means) / len(means), 2) if means else None,
    }


def _trend_slope(values: list[float]) -> float | None:
    """Ordinary-least-squares slope of *values* against their chronological index."""
    n = len(values)
    if n < 2:
        return None
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(values) / n
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values, strict=True))
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _trend_direction(values: list[float], *, threshold: float = 0.05) -> str:
    """Classify chronologically-ordered *values* as improving/stable/declining.

    Compares the mean of the first half against the mean of the second half;
    a relative change beyond *threshold* in either direction is not "stable".
    """
    half = len(values) // 2
    if half == 0:
        return "stable"
    first = sum(values[:half]) / half
    second = sum(values[-half:]) / half
    if first == 0:
        return "stable"
    change = (second - first) / abs(first)
    if change > threshold:
        return "improving"
    if change < -threshold:
        return "declining"
    return "stable"


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


class GoogleHealthConfig(BaseModel):
    """Configuration for the Google Health module (v1 — no config keys required)."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Module implementation
# ---------------------------------------------------------------------------


class GoogleHealthModule(Module):
    """Google Health module providing eight read-only MCP tools for wellness queries.

    All tools query the Health butler's SPO fact store via ``memory_search`` with
    ``scope='health'`` and a predicate filter.  When Google Health scopes are not
    granted, tools return actionable error strings rather than raising.
    """

    def __init__(self) -> None:
        self._config: GoogleHealthConfig = GoogleHealthConfig()
        self._scopes_ok: bool = False
        self._entity_id: str | None = None

    @property
    def name(self) -> str:
        return "google_health"

    @property
    def config_schema(self) -> type[BaseModel]:
        return GoogleHealthConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
        """Resolve the primary Google account and verify Google Health scopes.

        Implements the Tier-2 security contract: credentials are resolved via
        ``resolve_google_credentials()`` which reads the refresh token from
        ``public.entity_info`` on the companion entity — never via
        ``CredentialStore.resolve()`` or ``os.environ.get()``.

        When scopes are absent or no primary account exists, the module starts in
        degraded mode: all tools are still registered but return actionable errors.

        Parameters
        ----------
        config:
            Module configuration (``GoogleHealthConfig`` or raw dict).
        db:
            Butler database instance (provides ``db.pool``).
        credential_store:
            ``CredentialStore`` for OAuth credential resolution.
        blob_store:
            Unused by this module.
        """
        self._config = (
            config
            if isinstance(config, GoogleHealthConfig)
            else GoogleHealthConfig(**(config or {}))
        )
        self._scopes_ok = False
        self._entity_id = None

        if credential_store is None or db is None:
            logger.warning(
                "GoogleHealthModule: no credential_store or db provided — "
                "tools will return errors when invoked."
            )
            return

        pool = getattr(db, "pool", None)
        if pool is None:
            logger.warning(
                "GoogleHealthModule: db.pool is None — tools will return errors when invoked."
            )
            return

        # Resolve primary Google account via Tier-2 compliant pathway.
        # resolve_google_credentials() fetches the refresh token from
        # public.entity_info on the companion entity, not CredentialStore.resolve().
        try:
            from butlers.google_credentials import (  # noqa: PLC0415
                MissingGoogleCredentialsError,
                resolve_google_credentials,
            )

            await resolve_google_credentials(
                credential_store,
                pool=pool,
                caller="google_health",
                account=None,  # primary account
            )
        except MissingGoogleCredentialsError as exc:
            logger.warning(
                "GoogleHealthModule: no primary Google account — %s. "
                "Connect a Google account with Health scopes via dashboard settings.",
                exc,
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GoogleHealthModule: credential resolution failed — %s. "
                "Tools will return errors when invoked.",
                exc,
            )
            return

        # Resolve entity_id for later scope checks.
        try:
            from butlers.google_credentials import (  # noqa: PLC0415
                resolve_google_account_entity,
            )

            entity_id = await resolve_google_account_entity(pool, email=None)
            if entity_id is not None:
                self._entity_id = str(entity_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("GoogleHealthModule: could not resolve entity_id — %s", exc)

        # Verify required Google Health scopes against the account registry.
        # `creds.scope` is the static app scope secret and can lag behind the
        # account-specific OAuth grants stored in public.google_accounts.
        try:
            from butlers.google_account_registry import get_google_account  # noqa: PLC0415

            account = await get_google_account(pool, account=None)
            granted = set(account.granted_scopes or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GoogleHealthModule: failed to verify Google Health account scopes — %s. "
                "Tools will return errors when invoked.",
                exc,
            )
            return

        from butlers.google_account_registry import (  # noqa: PLC0415
            GOOGLE_HEALTH_SCOPE_FAMILIES,
            granted_health_scope_families,
        )

        missing = GOOGLE_HEALTH_SCOPE_FAMILIES - granted_health_scope_families(granted)
        if missing:
            logger.warning(
                "GoogleHealthModule: missing Google Health scope families: %s. "
                "Re-authorize at /api/oauth/google/start with the Health scope-set.",
                sorted(missing),
            )
            return

        self._scopes_ok = True
        logger.info(
            "GoogleHealthModule: started successfully (entity_id=%s)",
            self._entity_id,
        )

    async def on_shutdown(self) -> None:
        """No-op — this module holds no open connections."""
        pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _not_connected(self) -> dict[str, Any]:
        """Return the sentinel error for missing scopes."""
        return {"error": _NOT_CONNECTED_ERROR}

    def _no_account(self) -> dict[str, Any]:
        """Return the sentinel error for missing account."""
        return {"error": _NO_ACCOUNT_ERROR}

    # ------------------------------------------------------------------
    # register_tools
    # ------------------------------------------------------------------

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register all eight Google Health read-only MCP tools on the FastMCP server."""
        self._config = (
            config
            if isinstance(config, GoogleHealthConfig)
            else GoogleHealthConfig(**(config or {}))
        )
        module = self  # captured for closures
        pool = getattr(db, "pool", None)

        # ----------------------------------------------------------------
        # Group 1: Sleep
        # ----------------------------------------------------------------

        async def health_sleep_latest() -> dict[str, Any]:
            """Return the most recent sleep session for the owner.

            Queries the Health butler's SPO fact store for the latest
            ``sleep_session`` fact with ``scope='health'``.

            Returns a dict with: session_start, duration_minutes, efficiency,
            stages (deep, light, rem, wake), and summary text.
            Returns an empty result with explanation when no data exists.
            """
            if not module._scopes_ok or pool is None:
                return module._not_connected()
            row = await pool.fetchrow(
                "SELECT valid_at, content, metadata FROM facts"
                " WHERE predicate = 'sleep_session' AND scope = 'health'"
                " AND validity = 'active' ORDER BY valid_at DESC NULLS LAST LIMIT 1"
            )
            if row is None:
                return {
                    "found": False,
                    "metric": "sleep_session",
                    "message": _NO_SLEEP_DATA,
                }
            meta = row["metadata"] or {}
            stages = meta.get("stages") or {}
            duration_ms = meta.get("duration_ms")
            return {
                "found": True,
                "session_start": row["valid_at"],
                "duration_minutes": (
                    round(duration_ms / 60000, 1) if duration_ms is not None else None
                ),
                "efficiency": meta.get("efficiency"),
                "stages": {
                    "deep": stages.get("deep"),
                    "light": stages.get("light"),
                    "rem": stages.get("rem"),
                    "wake": stages.get("wake"),
                },
                "summary": row["content"],
            }

        async def health_sleep_history(days: int = 7) -> dict[str, Any]:
            """Return sleep session history over the requested window.

            Args:
                days: Number of days to look back (1-90, default 7).

            Queries ``sleep_session`` facts within the last *days* days.
            Returns a list of sessions in reverse chronological order with the
            same fields as ``health_sleep_latest``, plus aggregate stats:
            avg_duration_minutes, avg_efficiency, avg_deep_minutes, avg_rem_minutes.
            """
            if not module._scopes_ok or pool is None:
                return module._not_connected()
            days = max(1, min(days, 90))
            time_from = datetime.now(tz=UTC) - timedelta(days=days)
            rows = await pool.fetch(
                "SELECT valid_at, content, metadata FROM facts"
                " WHERE predicate = 'sleep_session' AND scope = 'health'"
                " AND validity = 'active' AND valid_at >= $1 ORDER BY valid_at DESC",
                time_from,
            )
            if not rows:
                return await _empty_metric_result(pool, "sleep_session", days, metric_label="sleep")
            sessions: list[dict[str, Any]] = []
            durations: list[float] = []
            efficiencies: list[float] = []
            deeps: list[float] = []
            rems: list[float] = []
            for row in rows:
                meta = row["metadata"] or {}
                stages = meta.get("stages") or {}
                duration_ms = meta.get("duration_ms")
                duration_minutes = duration_ms / 60000 if duration_ms is not None else None
                efficiency = meta.get("efficiency")
                deep = stages.get("deep")
                rem = stages.get("rem")
                sessions.append(
                    {
                        "session_start": row["valid_at"],
                        "duration_minutes": (
                            round(duration_minutes, 1) if duration_minutes is not None else None
                        ),
                        "efficiency": efficiency,
                        "stages": stages,
                        "summary": row["content"],
                    }
                )
                if duration_minutes is not None:
                    durations.append(duration_minutes)
                if efficiency is not None:
                    efficiencies.append(efficiency)
                if deep is not None:
                    deeps.append(deep)
                if rem is not None:
                    rems.append(rem)
            return {
                "found": True,
                "days": days,
                "sessions": sessions,
                "avg_duration_minutes": (
                    round(sum(durations) / len(durations), 1) if durations else None
                ),
                "avg_efficiency": (
                    round(sum(efficiencies) / len(efficiencies), 1) if efficiencies else None
                ),
                "avg_deep_minutes": round(sum(deeps) / len(deeps), 1) if deeps else None,
                "avg_rem_minutes": round(sum(rems) / len(rems), 1) if rems else None,
            }

        mcp.tool()(health_sleep_latest)
        mcp.tool()(health_sleep_history)

        # ----------------------------------------------------------------
        # Group 2: Heart rate and HRV
        # ----------------------------------------------------------------

        async def health_hr_history(days: int = 30) -> dict[str, Any]:
            """Return resting heart rate history over the requested window.

            Args:
                days: Number of days to look back (1-365, default 30).

            Queries ``measurement_resting_hr`` facts. Returns daily resting HR values
            plus a summary with min, max, avg, and a linear trend slope.
            """
            if not module._scopes_ok or pool is None:
                return module._not_connected()
            days = max(1, min(days, 365))
            time_from = datetime.now(tz=UTC) - timedelta(days=days)
            daily = await _daily_numeric_rollup(pool, "measurement_resting_hr", "value", time_from)
            if not daily:
                return await _empty_metric_result(
                    pool, "measurement_resting_hr", days, metric_label="heart rate"
                )
            values = [d["mean"] for d in daily if d["mean"] is not None]
            summary = _summarize_daily(daily)
            summary["trend_slope"] = _trend_slope(values)
            return {"found": True, "days": days, "daily": daily, "summary": summary}

        async def health_hrv_history(days: int = 30) -> dict[str, Any]:
            """Return heart rate variability (HRV) history over the requested window.

            Args:
                days: Number of days to look back (1-365, default 30).

            Queries ``measurement_hrv`` facts. Returns daily RMSSD values plus a
            summary with avg_rmssd, coverage, and trend direction.
            """
            if not module._scopes_ok or pool is None:
                return module._not_connected()
            days = max(1, min(days, 365))
            time_from = datetime.now(tz=UTC) - timedelta(days=days)
            daily = await _daily_numeric_rollup(pool, "measurement_hrv", "daily_rmssd", time_from)
            if not daily:
                return await _empty_metric_result(pool, "measurement_hrv", days, metric_label="HRV")
            values = [d["mean"] for d in daily if d["mean"] is not None]
            return {
                "found": True,
                "days": days,
                "daily": daily,
                "avg_rmssd": round(sum(values) / len(values), 2) if values else None,
                "coverage": round(len(daily) / days, 2) if days else None,
                "trend": _trend_direction(values),
            }

        mcp.tool()(health_hr_history)
        mcp.tool()(health_hrv_history)

        # ----------------------------------------------------------------
        # Group 3: Oxygen and breathing
        # ----------------------------------------------------------------

        async def health_spo2_history(days: int = 30) -> dict[str, Any]:
            """Return blood oxygen saturation (SpO2) history over the requested window.

            Args:
                days: Number of days to look back (1-365, default 30).

            Queries ``measurement_spo2`` facts. Returns daily average SpO2 values.
            """
            if not module._scopes_ok or pool is None:
                return module._not_connected()
            days = max(1, min(days, 365))
            time_from = datetime.now(tz=UTC) - timedelta(days=days)
            rows = await pool.fetch(
                """
                SELECT DATE(valid_at AT TIME ZONE 'UTC') AS day,
                       AVG((metadata->>'avg')::numeric) AS avg_value,
                       MIN((metadata->>'min')::numeric) AS min_value,
                       MAX((metadata->>'max')::numeric) AS max_value
                FROM facts
                WHERE predicate = 'measurement_spo2' AND scope = 'health'
                  AND validity = 'active' AND valid_at >= $1
                GROUP BY day
                ORDER BY day ASC
                """,
                time_from,
            )
            if not rows:
                return await _empty_metric_result(
                    pool, "measurement_spo2", days, metric_label="SpO2"
                )
            daily = [
                {
                    "date": row["day"].isoformat(),
                    "avg": float(row["avg_value"]) if row["avg_value"] is not None else None,
                    "min": float(row["min_value"]) if row["min_value"] is not None else None,
                    "max": float(row["max_value"]) if row["max_value"] is not None else None,
                }
                for row in rows
            ]
            avgs = [d["avg"] for d in daily if d["avg"] is not None]
            mins = [d["min"] for d in daily if d["min"] is not None]
            maxes = [d["max"] for d in daily if d["max"] is not None]
            return {
                "found": True,
                "days": days,
                "daily": daily,
                "summary": {
                    "avg": round(sum(avgs) / len(avgs), 1) if avgs else None,
                    "min": min(mins) if mins else None,
                    "max": max(maxes) if maxes else None,
                },
            }

        async def health_breathing_rate_history(days: int = 30) -> dict[str, Any]:
            """Return breathing rate history over the requested window.

            Args:
                days: Number of days to look back (1-365, default 30).

            Queries ``measurement_breathing_rate`` facts. Returns daily breathing rate values.
            """
            if not module._scopes_ok or pool is None:
                return module._not_connected()
            days = max(1, min(days, 365))
            time_from = datetime.now(tz=UTC) - timedelta(days=days)
            daily = await _daily_numeric_rollup(
                pool, "measurement_breathing_rate", "value", time_from
            )
            if not daily:
                return await _empty_metric_result(
                    pool, "measurement_breathing_rate", days, metric_label="breathing rate"
                )
            return {
                "found": True,
                "days": days,
                "daily": daily,
                "summary": _summarize_daily(daily),
            }

        mcp.tool()(health_spo2_history)
        mcp.tool()(health_breathing_rate_history)

        # ----------------------------------------------------------------
        # Group 4: Activity
        # ----------------------------------------------------------------

        async def health_activity_summary(days: int = 7) -> dict[str, Any]:
            """Return activity summary combining steps and active minutes.

            Args:
                days: Number of days to look back (1-90, default 7).

            Queries ``measurement_steps`` and ``measurement_active_minutes`` facts in the range.
            Returns per-day: steps, distance_km, floors, very_active_minutes,
            fairly_active_minutes, lightly_active_minutes, sedentary_minutes.
            Aggregate: average steps, average active minutes, days meeting 10 000 steps.
            """
            if not module._scopes_ok or pool is None:
                return module._not_connected()
            days = max(1, min(days, 90))
            time_from = datetime.now(tz=UTC) - timedelta(days=days)
            steps_rows = await pool.fetch(
                """
                SELECT DATE(valid_at AT TIME ZONE 'UTC') AS day,
                       SUM((metadata->>'value')::numeric) AS steps,
                       SUM((metadata->>'distance_km')::numeric) AS distance_km,
                       SUM((metadata->>'floors')::numeric) AS floors
                FROM facts
                WHERE predicate = 'measurement_steps' AND scope = 'health'
                  AND validity = 'active' AND valid_at >= $1
                GROUP BY day
                """,
                time_from,
            )
            active_rows = await pool.fetch(
                """
                SELECT DATE(valid_at AT TIME ZONE 'UTC') AS day,
                       SUM((metadata->>'very_active')::numeric) AS very_active,
                       SUM((metadata->>'fairly_active')::numeric) AS fairly_active,
                       SUM((metadata->>'lightly_active')::numeric) AS lightly_active,
                       SUM((metadata->>'sedentary')::numeric) AS sedentary
                FROM facts
                WHERE predicate = 'measurement_active_minutes' AND scope = 'health'
                  AND validity = 'active' AND valid_at >= $1
                GROUP BY day
                """,
                time_from,
            )
            by_day: dict[Any, dict[str, Any]] = {}
            for row in steps_rows:
                entry = by_day.setdefault(row["day"], {})
                entry["steps"] = float(row["steps"]) if row["steps"] is not None else None
                entry["distance_km"] = (
                    float(row["distance_km"]) if row["distance_km"] is not None else None
                )
                entry["floors"] = float(row["floors"]) if row["floors"] is not None else None
            for row in active_rows:
                entry = by_day.setdefault(row["day"], {})
                entry["very_active_minutes"] = (
                    float(row["very_active"]) if row["very_active"] is not None else None
                )
                entry["fairly_active_minutes"] = (
                    float(row["fairly_active"]) if row["fairly_active"] is not None else None
                )
                entry["lightly_active_minutes"] = (
                    float(row["lightly_active"]) if row["lightly_active"] is not None else None
                )
                entry["sedentary_minutes"] = (
                    float(row["sedentary"]) if row["sedentary"] is not None else None
                )
            if not by_day:
                return await _empty_metric_result(
                    pool, "measurement_steps", days, metric_label="activity"
                )
            daily = []
            for day in sorted(by_day):
                entry = dict(by_day[day])
                entry["date"] = day.isoformat()
                daily.append(entry)
            steps_values = [d["steps"] for d in daily if d.get("steps") is not None]
            active_minutes_values = [
                (d.get("very_active_minutes") or 0)
                + (d.get("fairly_active_minutes") or 0)
                + (d.get("lightly_active_minutes") or 0)
                for d in daily
            ]
            return {
                "found": True,
                "days": days,
                "daily": daily,
                "avg_steps": (
                    round(sum(steps_values) / len(steps_values), 1) if steps_values else None
                ),
                "avg_active_minutes": (
                    round(sum(active_minutes_values) / len(active_minutes_values), 1)
                    if active_minutes_values
                    else None
                ),
                "days_meeting_10k_steps": sum(1 for v in steps_values if v >= 10000),
            }

        mcp.tool()(health_activity_summary)

        # ----------------------------------------------------------------
        # Group 5: VO2 max
        # ----------------------------------------------------------------

        async def health_vo2_max_latest() -> dict[str, Any]:
            """Return the most recent VO2 max measurement.

            Queries the ``measurement_vo2_max`` fact for the owner entity.
            Returns: value, range_low, range_high, midpoint, and measurement date.
            """
            if not module._scopes_ok or pool is None:
                return module._not_connected()
            row = await pool.fetchrow(
                "SELECT valid_at, metadata FROM facts"
                " WHERE predicate = 'measurement_vo2_max' AND scope = 'health'"
                " AND validity = 'active' ORDER BY valid_at DESC LIMIT 1"
            )
            if row is None:
                return {
                    "found": False,
                    "metric": "measurement_vo2_max",
                    "message": _NO_DATA_TEMPLATE.format(metric="VO2 max"),
                }
            meta = row["metadata"] or {}
            return {
                "found": True,
                "value": meta.get("midpoint"),
                "range_low": meta.get("range_low"),
                "range_high": meta.get("range_high"),
                "midpoint": meta.get("midpoint"),
                "measured_at": row["valid_at"],
            }

        mcp.tool()(health_vo2_max_latest)
