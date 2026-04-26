"""Google Health module — read-only MCP tools for Health butler wellness queries.

Provides eight read-only tools that query the Health butler's SPO fact store via
the memory module's ``memory_search`` primitive.  Tools do NOT call
``health.googleapis.com`` directly; that is the connector's responsibility.

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
# Required Google Health OAuth scopes (full URLs as stored in granted_scopes)
# ---------------------------------------------------------------------------

_HEALTH_SCOPES: frozenset[str] = frozenset(
    {
        "https://www.googleapis.com/auth/googlehealth.sleep",
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
    }
)

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

        missing = _HEALTH_SCOPES - granted
        if missing:
            logger.warning(
                "GoogleHealthModule: missing Google Health scopes: %s. "
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
            if not module._scopes_ok:
                return module._not_connected()
            return {
                "query": "latest sleep session",
                "predicate": "sleep_session",
                "scope": "health",
                "instruction": (
                    "Call memory_search with query='sleep session', "
                    "types=['fact'], scope='health', "
                    "filters={'predicate': 'sleep_session'}, limit=1 "
                    "to retrieve the most recent sleep session fact. "
                    "Return session_start, duration_minutes, efficiency, "
                    "stages (deep, light, rem, wake), and the summary text. "
                    f"If no results, return: {_NO_SLEEP_DATA!r}"
                ),
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
            if not module._scopes_ok:
                return module._not_connected()
            days = max(1, min(days, 90))
            time_from = (datetime.now(tz=UTC) - timedelta(days=days)).isoformat()
            return {
                "query": f"sleep sessions last {days} days",
                "predicate": "sleep_session",
                "scope": "health",
                "days": days,
                "time_from": time_from,
                "instruction": (
                    f"Call memory_search with query='sleep session', "
                    f"types=['fact'], scope='health', "
                    f"filters={{'predicate': 'sleep_session', 'time_from': {time_from!r}}}, "
                    f"limit=90 to retrieve sleep facts for the last {days} days. "
                    "Sort results reverse-chronologically. "
                    "Compute aggregate: avg_duration_minutes, avg_efficiency, "
                    "avg_deep_minutes, avg_rem_minutes. "
                    f"If no results, return: {_NO_SLEEP_DATA!r}"
                ),
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
            if not module._scopes_ok:
                return module._not_connected()
            days = max(1, min(days, 365))
            time_from = (datetime.now(tz=UTC) - timedelta(days=days)).isoformat()
            return {
                "query": f"resting heart rate last {days} days",
                "predicate": "measurement_resting_hr",
                "scope": "health",
                "days": days,
                "time_from": time_from,
                "instruction": (
                    f"Call memory_search with query='resting heart rate', "
                    f"types=['fact'], scope='health', "
                    f"filters={{'predicate': 'measurement_resting_hr', "
                    f"'time_from': {time_from!r}}}, "
                    f"limit=365 to retrieve resting HR facts for the last {days} days. "
                    "Compute summary: min, max, avg, and linear trend slope. "
                    f"If no results, return: {_NO_DATA_TEMPLATE.format(metric='heart rate')!r}"
                ),
            }

        async def health_hrv_history(days: int = 30) -> dict[str, Any]:
            """Return heart rate variability (HRV) history over the requested window.

            Args:
                days: Number of days to look back (1-365, default 30).

            Queries ``measurement_hrv`` facts. Returns daily RMSSD values plus a
            summary with avg_rmssd, coverage, and trend direction.
            """
            if not module._scopes_ok:
                return module._not_connected()
            days = max(1, min(days, 365))
            time_from = (datetime.now(tz=UTC) - timedelta(days=days)).isoformat()
            return {
                "query": f"HRV history last {days} days",
                "predicate": "measurement_hrv",
                "scope": "health",
                "days": days,
                "time_from": time_from,
                "instruction": (
                    f"Call memory_search with query='heart rate variability HRV RMSSD', "
                    f"types=['fact'], scope='health', "
                    f"filters={{'predicate': 'measurement_hrv', 'time_from': {time_from!r}}}, "
                    f"limit=365 to retrieve HRV facts for the last {days} days. "
                    "Compute summary: avg_rmssd, coverage (days with data / total days), "
                    "and trend direction (improving / stable / declining). "
                    f"If no results, return: {_NO_DATA_TEMPLATE.format(metric='HRV')!r}"
                ),
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
            if not module._scopes_ok:
                return module._not_connected()
            days = max(1, min(days, 365))
            time_from = (datetime.now(tz=UTC) - timedelta(days=days)).isoformat()
            return {
                "query": f"SpO2 blood oxygen last {days} days",
                "predicate": "measurement_spo2",
                "scope": "health",
                "days": days,
                "time_from": time_from,
                "instruction": (
                    f"Call memory_search with query='blood oxygen SpO2 saturation', "
                    f"types=['fact'], scope='health', "
                    f"filters={{'predicate': 'measurement_spo2', 'time_from': {time_from!r}}}, "
                    f"limit=365 to retrieve SpO2 facts for the last {days} days. "
                    f"If no results, return: {_NO_DATA_TEMPLATE.format(metric='SpO2')!r}"
                ),
            }

        async def health_breathing_rate_history(days: int = 30) -> dict[str, Any]:
            """Return breathing rate history over the requested window.

            Args:
                days: Number of days to look back (1-365, default 30).

            Queries ``measurement_breathing_rate`` facts. Returns daily breathing rate values.
            """
            if not module._scopes_ok:
                return module._not_connected()
            days = max(1, min(days, 365))
            time_from = (datetime.now(tz=UTC) - timedelta(days=days)).isoformat()
            return {
                "query": f"breathing rate last {days} days",
                "predicate": "measurement_breathing_rate",
                "scope": "health",
                "days": days,
                "time_from": time_from,
                "instruction": (
                    f"Call memory_search with query='breathing rate respiratory', "
                    f"types=['fact'], scope='health', "
                    f"filters={{'predicate': 'measurement_breathing_rate', "
                    f"'time_from': {time_from!r}}}, "
                    f"limit=365 to retrieve breathing rate facts for the last {days} days. "
                    f"If no results, return: {_NO_DATA_TEMPLATE.format(metric='breathing rate')!r}"
                ),
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
            if not module._scopes_ok:
                return module._not_connected()
            days = max(1, min(days, 90))
            time_from = (datetime.now(tz=UTC) - timedelta(days=days)).isoformat()
            return {
                "query": f"activity steps active minutes last {days} days",
                "predicates": ["measurement_steps", "measurement_active_minutes"],
                "scope": "health",
                "days": days,
                "time_from": time_from,
                "instruction": (
                    f"Call memory_search twice: "
                    f"(1) query='daily steps activity', types=['fact'], scope='health', "
                    f"filters={{'predicate': 'measurement_steps', 'time_from': {time_from!r}}}, "
                    f"limit=90; "
                    f"(2) query='active minutes exercise', types=['fact'], scope='health', "
                    f"filters={{'predicate': 'measurement_active_minutes', "
                    f"'time_from': {time_from!r}}}, "
                    f"limit=90. "
                    "Join results by date. Per-day return: steps, distance_km, floors, "
                    "very_active_minutes, fairly_active_minutes, lightly_active_minutes, "
                    "sedentary_minutes. "
                    "Aggregate: avg_steps, avg_active_minutes, "
                    "days_meeting_10k_steps (count of days with steps >= 10000). "
                    f"If no results, return: {_NO_DATA_TEMPLATE.format(metric='activity')!r}"
                ),
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
            if not module._scopes_ok:
                return module._not_connected()
            return {
                "query": "latest VO2 max",
                "predicate": "measurement_vo2_max",
                "scope": "health",
                "instruction": (
                    "Call memory_search with query='VO2 max cardiorespiratory fitness', "
                    "types=['fact'], scope='health', "
                    "filters={'predicate': 'measurement_vo2_max'}, limit=1 "
                    "to retrieve the most recent VO2 max fact. "
                    "Return: value, range_low, range_high, midpoint, and measurement date. "
                    f"If no results, return: {_NO_DATA_TEMPLATE.format(metric='VO2 max')!r}"
                ),
            }

        mcp.tool()(health_vo2_max_latest)
