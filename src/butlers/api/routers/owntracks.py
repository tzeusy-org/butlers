"""Dashboard API routes for OwnTracks connector management.

Provides a FastAPI router for managing OwnTracks webhook authentication
and monitoring connector status. The OwnTracks connector is a webhook
server that receives HTTP POSTs from the OwnTracks mobile app.

Endpoints:
  POST /api/connectors/owntracks/token/generate — generate webhook auth token
  GET  /api/connectors/owntracks/status         — connection status
  GET  /api/connectors/owntracks/config         — webhook URL and setup metadata

Token management:
  Tokens are 32 bytes of random data hex-encoded to 64 characters.
  Tokens are stored in CredentialStore under key ``owntracks_webhook_token``
  and are sensitive (never returned after initial generation).
  Calling generate again creates a new token and immediately invalidates
  the previous one.

Status:
  Connection state is derived by checking:
  1. Whether a token is configured (CredentialStore lookup).
  2. Whether the connector process is alive (connector_registry heartbeat).
  3. Whether events have been received (heartbeat event counters).

Config:
  The webhook URL is computed from env vars:
  - ``OWNTRACKS_CONNECTOR_HOST`` (default: localhost)
  - ``OWNTRACKS_CONNECTOR_PORT`` (default: 40083)

Security:
  - Generated tokens are returned once and never again.
  - The config endpoint returns only a masked token preview.
  - No raw token material is ever stored in API responses.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from butlers.api.models.owntracks import (
    OwnTracksConfigResponse,
    OwnTracksConnectionState,
    OwnTracksSetupInstructions,
    OwnTracksStatusResponse,
    OwnTracksTokenResponse,
)
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors/owntracks", tags=["owntracks"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CRED_KEY_TOKEN = "owntracks_webhook_token"
_DEFAULT_CONNECTOR_HOST = "localhost"
_DEFAULT_CONNECTOR_PORT = 40083

# A connector is considered "running" if its last heartbeat is within this window.
_LIVENESS_THRESHOLD_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Optional DB manager dependency for credential persistence
# ---------------------------------------------------------------------------


def _get_db_manager() -> Any:
    """Stub replaced at startup by wire_db_dependencies().

    When not wired (e.g. in tests that don't boot the full app), returns None
    so endpoints degrade gracefully.
    """
    return None


def _make_credential_store(db_manager: Any) -> CredentialStore | None:
    """Build a CredentialStore from the shared credential pool.

    Returns None when db_manager is None or no usable pool can be resolved.
    Resolution order:
    1. Dedicated shared credential pool from DatabaseManager.
    2. Compatibility fallback to first butler pool.
    """
    if db_manager is None:
        return None

    try:
        pool = db_manager.credential_shared_pool()
    except Exception:
        butler_names = getattr(db_manager, "butler_names", [])
        if not butler_names:
            logger.debug("Shared credential pool unavailable and no butler pools are registered.")
            return None
        try:
            pool = db_manager.pool(butler_names[0])
            logger.warning(
                "Shared credential pool unavailable; using fallback pool from %s",
                butler_names[0],
            )
        except Exception:
            logger.debug("Failed to obtain fallback DB pool; credential store unavailable.")
            return None

    return CredentialStore(pool)


def _make_switchboard_pool(db_manager: Any) -> Any | None:
    """Obtain the switchboard database pool for connector_registry queries.

    Returns None when db_manager is None or the switchboard pool is unavailable.
    """
    if db_manager is None:
        return None
    try:
        return db_manager.pool("switchboard")
    except Exception:
        logger.debug("Switchboard pool unavailable for OwnTracks status query", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_connector_host() -> str:
    """Read OWNTRACKS_CONNECTOR_HOST env var or return default.

    Whitespace-only values are treated as unset and fall back to the default.
    """
    raw = os.environ.get("OWNTRACKS_CONNECTOR_HOST")
    if raw is None:
        return _DEFAULT_CONNECTOR_HOST
    host = raw.strip()
    return host or _DEFAULT_CONNECTOR_HOST


def _get_connector_port() -> int:
    """Read OWNTRACKS_CONNECTOR_PORT env var or return default.

    Values that are not valid integers or are outside the range 1–65535 fall
    back to the default.
    """
    raw = os.environ.get("OWNTRACKS_CONNECTOR_PORT", str(_DEFAULT_CONNECTOR_PORT)).strip()
    try:
        port = int(raw)
        if 1 <= port <= 65535:
            return port
        logger.warning(
            "Out-of-range OWNTRACKS_CONNECTOR_PORT=%r, must be 1-65535; using default %d",
            raw,
            _DEFAULT_CONNECTOR_PORT,
        )
        return _DEFAULT_CONNECTOR_PORT
    except (ValueError, TypeError):
        logger.warning(
            "Invalid OWNTRACKS_CONNECTOR_PORT=%r, using default %d", raw, _DEFAULT_CONNECTOR_PORT
        )
        return _DEFAULT_CONNECTOR_PORT


def _get_webhook_base_path() -> str:
    """Read OWNTRACKS_WEBHOOK_BASE_PATH env var or return default ``/owntracks``."""
    raw = os.environ.get("OWNTRACKS_WEBHOOK_BASE_PATH")
    if raw is None:
        return "/owntracks"
    stripped = raw.strip().rstrip("/")
    return stripped or "/owntracks"


def _build_webhook_url(host: str, port: int) -> str:
    """Construct the full webhook URL from host and port.

    Uses https for non-localhost hosts; http for localhost/127.0.0.1/::1.
    IPv6 literals are wrapped in brackets as required by RFC 2732.
    """
    _LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1"}
    scheme = "http" if host in _LOCALHOST_HOSTS else "https"
    # Wrap bare IPv6 literals in brackets (e.g. "::1" → "[::1]")
    host_part = f"[{host}]" if ":" in host else host
    # Omit port when it matches the scheme default (80 for http, 443 for https)
    default_port = 80 if scheme == "http" else 443
    port_suffix = "" if port == default_port else f":{port}"
    base_path = _get_webhook_base_path()
    return f"{scheme}://{host_part}{port_suffix}{base_path}/webhook"


def _mask_token(token: str | None) -> str | None:
    """Return a masked preview of the token for display.

    Shows the first 4 and last 4 hex characters: 'ab12...ef90'.
    Returns None if token is None or shorter than 8 characters.
    """
    if not token or len(token) < 8:
        return None
    return f"{token[:4]}...{token[-4:]}"


def _generate_token() -> str:
    """Generate a cryptographically random 32-byte hex token (64 chars)."""
    return secrets.token_hex(32)


async def _query_connector_heartbeat(pool: Any) -> dict | None:
    """Query the switchboard connector_registry for OwnTracks heartbeat data.

    Returns a dict with state, last_heartbeat_at, counter_messages_ingested,
    today_messages_ingested, and uptime_s — or None if not registered.
    """
    try:
        row = await pool.fetchrow(
            "SELECT cr.state, cr.last_heartbeat_at, cr.uptime_s,"
            " cr.counter_messages_ingested,"
            " COALESCE(ts.today_ingested, 0) AS today_messages_ingested"
            " FROM connector_registry cr"
            " LEFT JOIN ("
            "   SELECT connector_type, endpoint_identity,"
            "     SUM(delta_ingested) AS today_ingested"
            "   FROM ("
            "     SELECT connector_type, endpoint_identity, instance_id,"
            "       GREATEST(0, MAX(counter_messages_ingested)"
            "         - MIN(NULLIF(counter_messages_ingested, 0))) AS delta_ingested"
            "     FROM connector_heartbeat_log"
            "     WHERE received_at >= CURRENT_DATE"
            "     GROUP BY connector_type, endpoint_identity, instance_id"
            "   ) per_instance"
            "   GROUP BY connector_type, endpoint_identity"
            " ) ts ON cr.connector_type = ts.connector_type"
            "   AND cr.endpoint_identity = ts.endpoint_identity"
            " WHERE cr.connector_type = 'owntracks'"
            " ORDER BY cr.last_heartbeat_at DESC NULLS LAST"
            " LIMIT 1",
        )
    except Exception:
        logger.debug("connector_registry query failed for OwnTracks status", exc_info=True)
        return None

    if row is None:
        return None

    return dict(row)


def _derive_connection_state(
    *,
    token_configured: bool,
    heartbeat_row: dict | None,
) -> tuple[OwnTracksConnectionState, bool, datetime | None, int, float | None]:
    """Derive connection state from token and heartbeat data.

    Returns:
        (state, connector_running, last_event_at, events_today, uptime_seconds)
    """
    if not token_configured:
        return OwnTracksConnectionState.not_configured, False, None, 0, None

    if heartbeat_row is None:
        # Token configured, no heartbeat ever received → connector offline (not started yet)
        return OwnTracksConnectionState.offline, False, None, 0, None

    last_heartbeat_raw = heartbeat_row.get("last_heartbeat_at")
    uptime_s = heartbeat_row.get("uptime_s")

    last_heartbeat_at: datetime | None = None
    if last_heartbeat_raw is not None:
        if isinstance(last_heartbeat_raw, datetime):
            last_heartbeat_at = last_heartbeat_raw
        else:
            try:
                last_heartbeat_at = datetime.fromisoformat(str(last_heartbeat_raw))
            except (ValueError, TypeError):
                pass

    # Liveness: heartbeat must be recent
    connector_running = False
    if last_heartbeat_at is not None:
        cutoff = datetime.now(UTC) - timedelta(seconds=_LIVENESS_THRESHOLD_SECONDS)
        # Ensure both are tz-aware for comparison
        if last_heartbeat_at.tzinfo is None:
            last_heartbeat_at = last_heartbeat_at.replace(tzinfo=UTC)
        connector_running = last_heartbeat_at >= cutoff

    events_today = int(heartbeat_row.get("today_messages_ingested") or 0)
    total_events = int(heartbeat_row.get("counter_messages_ingested") or 0)

    uptime_seconds: float | None = None
    if uptime_s is not None:
        try:
            uptime_seconds = float(uptime_s)
        except (ValueError, TypeError):
            pass

    if not connector_running:
        if last_heartbeat_at:
            state = OwnTracksConnectionState.stale
        else:
            state = OwnTracksConnectionState.offline
    elif total_events == 0:
        state = OwnTracksConnectionState.no_events
    else:
        state = OwnTracksConnectionState.connected

    # Use last_heartbeat_at as the best available proxy for last_event_at
    # (the connector updates its heartbeat when it processes events)
    last_event_at = last_heartbeat_at if total_events > 0 else None

    return state, connector_running, last_event_at, events_today, uptime_seconds


# ---------------------------------------------------------------------------
# POST /token/generate
# ---------------------------------------------------------------------------


@router.post("/token/generate", response_model=OwnTracksTokenResponse)
async def generate_owntracks_token(
    db_manager: Any = Depends(_get_db_manager),
) -> OwnTracksTokenResponse:
    """Generate a new webhook authentication token for OwnTracks.

    Creates a cryptographically random 32-byte hex token, stores it in
    CredentialStore under ``owntracks_webhook_token``, and returns it once.
    If a token already exists, it is immediately replaced (old token
    is invalidated).

    Raises HTTP 503 when the credential database is unavailable.
    """
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        raise HTTPException(
            status_code=503,
            detail=("Credential database is unavailable. Ensure the database service is running."),
        )

    token = _generate_token()

    await cred_store.store(
        _CRED_KEY_TOKEN,
        token,
        category="owntracks",
        description="OwnTracks webhook bearer token for HTTP POST authentication",
        is_sensitive=True,
    )

    logger.info("OwnTracks webhook token generated and stored in CredentialStore")

    return OwnTracksTokenResponse(token=token)


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=OwnTracksStatusResponse)
async def get_owntracks_status(
    db_manager: Any = Depends(_get_db_manager),
) -> OwnTracksStatusResponse:
    """Return the current OwnTracks connector connection state.

    Checks CredentialStore for a configured token and queries the
    switchboard connector_registry for heartbeat data. Returns not_configured
    when no token has been generated.
    """
    cred_store = _make_credential_store(db_manager)

    token_configured = False
    if cred_store is not None:
        token = await cred_store.resolve(_CRED_KEY_TOKEN)
        token_configured = bool(token)

    # Query switchboard DB for connector heartbeat data
    switchboard_pool = _make_switchboard_pool(db_manager)
    heartbeat_row: dict | None = None
    if switchboard_pool is not None:
        heartbeat_row = await _query_connector_heartbeat(switchboard_pool)

    derived = _derive_connection_state(
        token_configured=token_configured,
        heartbeat_row=heartbeat_row,
    )
    state, connector_running, last_event_at, events_today, uptime_seconds = derived

    return OwnTracksStatusResponse(
        state=state,
        token_configured=token_configured,
        last_event_at=last_event_at,
        events_today=events_today,
        connector_running=connector_running,
        uptime_seconds=uptime_seconds,
    )


# ---------------------------------------------------------------------------
# GET /config
# ---------------------------------------------------------------------------


@router.get("/config", response_model=OwnTracksConfigResponse)
async def get_owntracks_config(
    db_manager: Any = Depends(_get_db_manager),
) -> OwnTracksConfigResponse:
    """Return the computed webhook URL and setup instructions metadata.

    Computes the webhook URL from OWNTRACKS_CONNECTOR_HOST and
    OWNTRACKS_CONNECTOR_PORT env vars. Returns a masked token preview
    if a token is configured (to confirm setup without exposing the secret).
    """
    host = _get_connector_host()
    port = _get_connector_port()
    webhook_url = _build_webhook_url(host, port)

    # Retrieve token for masked preview only — never return the raw value
    token_masked: str | None = None
    cred_store = _make_credential_store(db_manager)
    if cred_store is not None:
        token = await cred_store.resolve(_CRED_KEY_TOKEN)
        token_masked = _mask_token(token)

    instructions = OwnTracksSetupInstructions(
        steps_ios=[
            "Open the OwnTracks app and tap the i icon (top-left) → Settings.",
            "Under Connection, set Mode to HTTP.",
            "Set URL to the webhook URL shown above.",
            "Tap Authentication, select Bearer token, and paste your generated token.",
            "Tap Back to save. The app will begin sending location updates.",
        ],
        steps_android=[
            "Open the OwnTracks app and tap the menu icon (⋮) → Preferences.",
            "Tap Connection, then set Mode to HTTP.",
            "Set Host/URL to the webhook URL shown above.",
            "Set Authentication to Bearer Token and paste your generated token.",
            "Tap the back button to save. The app will begin sending location updates.",
        ],
    )

    return OwnTracksConfigResponse(
        webhook_url=webhook_url,
        token_masked=token_masked,
        setup_instructions=instructions,
    )
