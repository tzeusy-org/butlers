"""Google Health connector dashboard API endpoints.

Provides a FastAPI router for monitoring the Google Health connector's
operational state and performing scope-selective revocation without
disconnecting the underlying Google account (which would also kill
Calendar / Drive / Gmail access).

Endpoints:
  GET    /api/connectors/google-health/status      — connection state + metadata
  DELETE /api/connectors/google-health/disconnect  — scope-selective revocation

Scope-selective revocation contract
-----------------------------------
Google's OAuth revocation endpoint revokes the entire refresh token and
does NOT support per-scope revocation. Because the same refresh token is
used to mint access tokens for Calendar, Drive, and Gmail, we MUST NOT
call ``oauth2.googleapis.com/revoke`` from this endpoint — doing so would
break every other Google integration for the account.

The spec's intent (``openspec/changes/google-health-connector/specs/
google-multi-account-oauth/spec.md`` — "Scope-Selective Revocation") is
preserved by stripping the three Google Health scope URLs from
``public.google_accounts.granted_scopes`` locally:

  - The connector re-checks ``granted_scopes`` every 300 s and transitions
    to ``degraded`` as soon as any Google Health scope disappears.
  - Calendar / Drive / Gmail continue to work because their refresh token
    is untouched and their scopes remain in the column.
  - Re-granting Google Health is a normal OAuth flow with
    ``?scope_set=health&force_consent=true`` that augments the existing
    refresh token (Google issues a new token with the union of scopes).

Credential discipline (Tier-2 contract)
---------------------------------------
Per ``about/heart-and-soul/security.md``, any code path that touches
refresh tokens MUST use the shared ``resolve_owner_entity_info()``
pipeline — NOT ``CredentialStore.resolve()`` or ``os.environ.get``. This
router NEVER reads or echoes the refresh token value; it only mutates
the ``granted_scopes`` array on ``public.google_accounts`` via the
shared pool. The refresh-token material never flows through this router.

Observability
-------------
Every request emits a structured log line with:
  - ``request_id`` (derived from the incoming request's ``x-request-id``
    header, or a fresh UUID)
  - ``account_id`` (the primary Google account UUID when resolved)
  - ``action`` (``status`` / ``disconnect``)

The GET endpoint additionally bumps a ``dashboard_connector_status_requests_total``
Prometheus counter labelled by ``connector=google-health`` so Grafana
can track status-card poll load.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from prometheus_client import Counter

from butlers.api.models.google_health import (
    GoogleHealthConnectorState,
    GoogleHealthDisconnectResponse,
    GoogleHealthStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors/google-health", tags=["google-health"])


# ---------------------------------------------------------------------------
# Constants — keep in lockstep with:
#   src/butlers/api/routers/oauth.py ::GOOGLE_SCOPE_SETS["health"]
#   src/butlers/connectors/google_health.py ::GOOGLE_HEALTH_SCOPES
# ---------------------------------------------------------------------------

GOOGLE_HEALTH_SCOPE_URLS: frozenset[str] = frozenset(
    {
        "https://www.googleapis.com/auth/googlehealth.sleep",
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
    }
)

_CONNECTOR_TYPE = "google_health"

# Liveness threshold for deriving "connected" — keep in sync with the
# owntracks dashboard's window so the two connectors render comparable
# freshness semantics. 5 minutes is longer than the 30-min slowest Google
# Health poll interval on purpose: the heartbeat fires every 2 minutes
# independently of per-resource poll cadence.
_LIVENESS_THRESHOLD_SECONDS = 300


# ---------------------------------------------------------------------------
# Prometheus counter — bumped on every GET /status so Grafana can track
# dashboard poll load. Counter is module-scoped so the registry stays
# consistent across hot-reloads in dev.
# ---------------------------------------------------------------------------

dashboard_connector_status_requests_total = Counter(
    "dashboard_connector_status_requests_total",
    "Number of dashboard connector-status GETs, labelled by connector.",
    labelnames=["connector"],
)


# ---------------------------------------------------------------------------
# DB manager dependency — stubbed out here; overridden by wire_db_dependencies
# at app startup. Returns None when the shared DB pool is unavailable so
# endpoints degrade gracefully (returning not_configured state) instead of
# raising 500s during tests that don't boot the full application.
# ---------------------------------------------------------------------------


def _get_db_manager() -> Any:
    """Stub replaced at startup by ``wire_db_dependencies()``."""
    return None


def _make_shared_pool(db_manager: Any) -> Any | None:
    """Return the shared credential pool from the DatabaseManager, or None.

    Uses the same fallback chain as ``oauth.py`` / ``spotify.py``:
    shared credential pool first, then the first butler pool if the
    shared pool is not available (compatibility with older deployments).
    """
    if db_manager is None:
        return None
    try:
        return db_manager.credential_shared_pool()
    except Exception:
        butler_names = getattr(db_manager, "butler_names", [])
        if not butler_names:
            logger.debug("Shared credential pool unavailable and no butler pools are registered.")
            return None
        try:
            return db_manager.pool(butler_names[0])
        except Exception:
            logger.debug("Failed to obtain fallback DB pool.", exc_info=True)
            return None


def _make_switchboard_pool(db_manager: Any) -> Any | None:
    """Return the switchboard pool for connector_registry / heartbeat queries."""
    if db_manager is None:
        return None
    try:
        return db_manager.pool("switchboard")
    except Exception:
        logger.debug("Switchboard pool unavailable for Google Health status query.", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Helpers — shared by both endpoints. Kept small and pure so tests can
# exercise them directly without a full app.
# ---------------------------------------------------------------------------


def _derive_request_id(request: Request) -> str:
    """Read the request-id header or mint a fresh one for structured logs."""
    incoming = request.headers.get("x-request-id")
    if incoming:
        return incoming[:128]
    return uuid.uuid4().hex


async def _fetch_primary_google_account(shared_pool: Any) -> dict[str, Any] | None:
    """Fetch the primary Google account row or None.

    Returns a dict with ``id``, ``entity_id``, ``email``, ``granted_scopes``,
    ``status``, ``last_token_refresh_at``, and ``metadata``. The ``metadata``
    column is JSONB — asyncpg returns it as a dict already when the
    connection has JSONB codec set up (core_001 migration wires it).
    """
    if shared_pool is None:
        return None
    try:
        async with shared_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, entity_id, email, granted_scopes, status,
                       last_token_refresh_at, metadata
                FROM public.google_accounts
                WHERE is_primary = true
                LIMIT 1
                """
            )
    except Exception:  # noqa: BLE001
        logger.debug("Failed to query public.google_accounts", exc_info=True)
        return None
    if row is None:
        return None
    return dict(row)


def _parse_jsonb_metadata(raw: Any) -> dict[str, Any]:
    """Coerce a JSONB column value into a plain dict.

    asyncpg may return either a ``dict`` (when the JSONB codec is set) or
    a ``str`` (when it isn't — e.g. in unit tests using bare AsyncMock
    pools). Gracefully handles both.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            import json  # noqa: PLC0415

            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _filter_health_scopes(granted: list[str] | None) -> list[str]:
    """Return the full Google Health scope URLs that are in ``granted``.

    Preserves original order to make the response deterministic for tests.
    """
    if not granted:
        return []
    return [scope for scope in granted if scope in GOOGLE_HEALTH_SCOPE_URLS]


async def _fetch_last_ingest_at(shared_pool: Any) -> datetime | None:
    """Return the most-recent ``public.ingestion_events.received_at`` for Google Health."""
    if shared_pool is None:
        return None
    try:
        async with shared_pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT MAX(received_at) FROM public.ingestion_events
                WHERE source_channel = 'wellness'
                  AND source_provider = $1
                """,
                _CONNECTOR_TYPE,
            )
    except Exception:  # noqa: BLE001
        logger.debug("Failed to query public.ingestion_events for Google Health", exc_info=True)
        return None
    return value


async def _fetch_heartbeat_row(switchboard_pool: Any) -> dict[str, Any] | None:
    """Return the most-recent connector_registry row for Google Health, or None.

    Matches the OwnTracks pattern — selects ``state``, ``last_heartbeat_at``,
    and ``uptime_s`` so the derived state can be computed consistently with
    the owntracks status card.
    """
    if switchboard_pool is None:
        return None
    try:
        row = await switchboard_pool.fetchrow(
            "SELECT cr.state, cr.last_heartbeat_at, cr.uptime_s"
            " FROM connector_registry cr"
            f" WHERE cr.connector_type = '{_CONNECTOR_TYPE}'"
            " ORDER BY cr.last_heartbeat_at DESC NULLS LAST"
            " LIMIT 1"
        )
    except Exception:  # noqa: BLE001
        logger.debug("connector_registry query failed for Google Health", exc_info=True)
        return None
    if row is None:
        return None
    return dict(row)


def _derive_state(
    *,
    account: dict[str, Any] | None,
    granted_health_scopes: list[str],
    heartbeat: dict[str, Any] | None,
) -> tuple[GoogleHealthConnectorState, bool]:
    """Derive the connector state + ``connected`` convenience boolean.

    Precedence:
      1. No primary account                        → not_configured
      2. Connector heartbeat says error            → error
      3. Not all three Google Health scopes        → degraded
      4. Heartbeat missing OR stale                → degraded
      5. Heartbeat state == 'degraded'             → degraded
      6. Otherwise                                 → healthy

    Returns (state, connected). ``connected`` is True only when state is
    ``healthy``.
    """
    if account is None:
        return GoogleHealthConnectorState.not_configured, False

    hb_state_raw = (heartbeat or {}).get("state")
    if hb_state_raw == "error":
        return GoogleHealthConnectorState.error, False

    if len(granted_health_scopes) < len(GOOGLE_HEALTH_SCOPE_URLS):
        return GoogleHealthConnectorState.degraded, False

    last_heartbeat_at = (heartbeat or {}).get("last_heartbeat_at")
    if isinstance(last_heartbeat_at, str):
        try:
            last_heartbeat_at = datetime.fromisoformat(last_heartbeat_at)
        except ValueError:
            last_heartbeat_at = None

    if not isinstance(last_heartbeat_at, datetime):
        return GoogleHealthConnectorState.degraded, False

    # Normalise to tz-aware before comparing.
    if last_heartbeat_at.tzinfo is None:
        last_heartbeat_at = last_heartbeat_at.replace(tzinfo=UTC)
    cutoff = datetime.now(UTC) - timedelta(seconds=_LIVENESS_THRESHOLD_SECONDS)
    if last_heartbeat_at < cutoff:
        return GoogleHealthConnectorState.degraded, False

    if hb_state_raw == "degraded":
        return GoogleHealthConnectorState.degraded, False

    return GoogleHealthConnectorState.healthy, True


def _extract_rate_limit_remaining(heartbeat: dict[str, Any] | None) -> int | None:
    """Pull ``rate_limit_remaining`` out of the heartbeat metadata when present.

    The connector emits per-resource rate-limit gauges to Prometheus, but the
    dashboard reads the heartbeat-level ``rate_limit_remaining`` mirror
    (carried in the heartbeat's ``metadata`` JSONB) so the API can stay out
    of the metrics scrape path. When the field is absent — e.g. because the
    connector has not yet observed a rate-limit header — this returns None
    and the UI hides the row.
    """
    if heartbeat is None:
        return None
    raw_meta = heartbeat.get("metadata")
    meta = _parse_jsonb_metadata(raw_meta)
    val = meta.get("rate_limit_remaining")
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=GoogleHealthStatusResponse)
async def get_google_health_status(
    request: Request,
    db_manager: Any = Depends(_get_db_manager),
) -> GoogleHealthStatusResponse:
    """Return the current Google Health connector state.

    Reads the primary Google account's ``granted_scopes``, the most recent
    ``ingestion_events`` row for the wellness channel, and the connector's
    heartbeat from ``switchboard.connector_registry`` to derive a composite
    state. Never echoes credential material.
    """
    dashboard_connector_status_requests_total.labels(connector="google-health").inc()

    request_id = _derive_request_id(request)

    shared_pool = _make_shared_pool(db_manager)
    switchboard_pool = _make_switchboard_pool(db_manager)

    account = await _fetch_primary_google_account(shared_pool)
    heartbeat = await _fetch_heartbeat_row(switchboard_pool)
    last_ingest_at = await _fetch_last_ingest_at(shared_pool)

    granted = list(account["granted_scopes"] or []) if account else []
    granted_health = _filter_health_scopes(granted)

    state, connected = _derive_state(
        account=account,
        granted_health_scopes=granted_health,
        heartbeat=heartbeat,
    )

    metadata = _parse_jsonb_metadata((account or {}).get("metadata"))
    test_mode = bool(metadata.get("google_health_test_mode"))

    last_token_refresh_at = (account or {}).get("last_token_refresh_at")
    if isinstance(last_token_refresh_at, str):
        try:
            last_token_refresh_at = datetime.fromisoformat(last_token_refresh_at)
        except ValueError:
            last_token_refresh_at = None

    rate_limit_remaining = _extract_rate_limit_remaining(heartbeat)

    account_id_str = str((account or {}).get("id")) if account else None
    logger.info(
        "google_health.status request_id=%s account_id=%s state=%s scopes=%d test_mode=%s",
        request_id,
        account_id_str,
        state.value,
        len(granted_health),
        test_mode,
    )

    return GoogleHealthStatusResponse(
        connected=connected,
        scopes_granted=granted_health,
        last_ingest_at=last_ingest_at,
        last_token_refresh_at=last_token_refresh_at,
        rate_limit_remaining=rate_limit_remaining,
        test_mode=test_mode,
        state=state,
    )


# ---------------------------------------------------------------------------
# DELETE /disconnect
# ---------------------------------------------------------------------------


@router.delete("/disconnect", response_model=GoogleHealthDisconnectResponse)
async def disconnect_google_health(
    request: Request,
    db_manager: Any = Depends(_get_db_manager),
) -> GoogleHealthDisconnectResponse:
    """Scope-selectively revoke Google Health access for the primary account.

    Removes the three full Google Health scope URLs from
    ``public.google_accounts.granted_scopes`` while preserving every other
    scope. The refresh token is NOT revoked with Google — revoking it
    would kill Calendar / Drive / Gmail for the same account.

    The Google Health connector detects the scope removal on its next
    ``granted_scopes`` check (within 300 s) and transitions to ``degraded``.

    Idempotent: when no Google Health scopes are currently present, returns
    ``success=True`` with ``scopes_removed=[]`` so the dashboard can render
    a consistent confirmation modal regardless of the prior state.
    """
    request_id = _derive_request_id(request)
    shared_pool = _make_shared_pool(db_manager)

    if shared_pool is None:
        logger.warning("google_health.disconnect request_id=%s outcome=no_db", request_id)
        return GoogleHealthDisconnectResponse(
            success=True,
            message="Google Health disconnected (credential store was unavailable)",
            scopes_removed=[],
        )

    account = await _fetch_primary_google_account(shared_pool)
    if account is None:
        logger.info("google_health.disconnect request_id=%s outcome=no_primary_account", request_id)
        return GoogleHealthDisconnectResponse(
            success=True,
            message="No primary Google account connected — nothing to disconnect.",
            scopes_removed=[],
        )

    granted = list(account["granted_scopes"] or [])
    present_health = [scope for scope in granted if scope in GOOGLE_HEALTH_SCOPE_URLS]

    if not present_health:
        logger.info(
            "google_health.disconnect request_id=%s account_id=%s outcome=noop",
            request_id,
            account["id"],
        )
        return GoogleHealthDisconnectResponse(
            success=True,
            message="Google Health scopes are already absent.",
            scopes_removed=[],
        )

    remaining = [scope for scope in granted if scope not in GOOGLE_HEALTH_SCOPE_URLS]

    async with shared_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.google_accounts
            SET granted_scopes = $1::text[]
            WHERE id = $2
            """,
            remaining,
            account["id"],
        )

    logger.info(
        "google_health.disconnect request_id=%s account_id=%s "
        "action=scope_strip removed=%d remaining=%d",
        request_id,
        account["id"],
        len(present_health),
        len(remaining),
    )

    return GoogleHealthDisconnectResponse(
        success=True,
        message=f"Google Health disconnected ({len(present_health)} scope(s) removed).",
        scopes_removed=present_health,
    )
