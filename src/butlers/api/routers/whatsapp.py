"""Dashboard API routes for WhatsApp account management.

Provides a FastAPI router for managing WhatsApp connection state, QR pairing,
and session health monitoring. Bridges communication to the Go whatsapp-bridge
subprocess via a Unix domain socket.

Endpoints:
  GET  /api/connectors/whatsapp/status      — current connection state
  POST /api/connectors/whatsapp/pair/start  — initiate QR pairing, return QR data URI
  GET  /api/connectors/whatsapp/pair/poll   — poll pairing progress
  POST /api/connectors/whatsapp/disconnect  — gracefully disconnect session
  GET  /api/connectors/whatsapp/health      — proxy bridge /status for health badge

Bridge communication:
  All endpoints that need live data proxy requests to the Go bridge over a
  Unix socket. The socket path is configurable via ``_get_bridge_socket_path``
  (dependency-injectable for tests). When the bridge is unreachable, endpoints
  return appropriate degraded responses rather than hard 503 errors (except
  pair/start, which requires the bridge to generate a QR code).

Security:
  - No credential material is ever returned.
  - Phone numbers are masked for display ('+1 *** *** 7890').
  - The Unix socket is assumed to be accessible only within the container/host.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
from fastapi import APIRouter, Depends, HTTPException

from butlers.api.db import DatabaseManager
from butlers.api.models.whatsapp import (
    WhatsAppDisconnectResponse,
    WhatsAppHealthResponse,
    WhatsAppPairPollResponse,
    WhatsAppPairStartResponse,
    WhatsAppPairStatus,
    WhatsAppState,
    WhatsAppStatusResponse,
)
from butlers.connectors.bridge_manager import DEFAULT_INVALIDATED_SESSION_THRESHOLD_S

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors/whatsapp", tags=["whatsapp"])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_BRIDGE_SOCKET = "/tmp/wa-bridge.sock"
_BRIDGE_TIMEOUT = 5.0  # seconds

# One quick retry for /pair/start when the bridge is momentarily unreachable.
# bu-7sh43 removed the startup-readiness teardown that used to make the bridge
# disappear mid-respawn every ~60s, so this race is now rare — but a bridge
# process restart (e.g. an unrelated crash-and-respawn) can still take a
# moment to rebind its Unix socket. A short, single retry smooths that over
# without masking a genuinely-down bridge (which still 503s after the retry).
_PAIR_START_RETRY_DELAY_S = 0.5

# Matches bridge_manager.BridgeConfig.invalidated_session_threshold_s — the
# connector-side escalation this heuristic mirrors (bu-5ocmh). Kept as one
# constant imported from bridge_manager so the two never drift apart.
_INVALIDATED_SESSION_THRESHOLD_S = DEFAULT_INVALIDATED_SESSION_THRESHOLD_S

_WHATSAPP_CONNECTOR_TYPE = "whatsapp_user_client"


def _get_bridge_socket_path() -> str:
    """Return the path to the Go bridge Unix socket.

    Reads WHATSAPP_BRIDGE_SOCKET env var; falls back to /tmp/wa-bridge.sock.
    Override via app.dependency_overrides[_get_bridge_socket_path] in tests.
    """
    return os.environ.get("WHATSAPP_BRIDGE_SOCKET", _DEFAULT_BRIDGE_SOCKET)


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _get_switchboard_pool(db: DatabaseManager) -> asyncpg.Pool | None:
    """Return the switchboard pool, or ``None`` when it's unavailable."""
    try:
        return db.pool("switchboard")
    except KeyError:
        logger.warning("Switchboard DB pool unavailable; cannot request WhatsApp pair-reset")
        return None


# ---------------------------------------------------------------------------
# Bridge HTTP helpers
# ---------------------------------------------------------------------------


def _make_bridge_transport(socket_path: str) -> httpx.AsyncHTTPTransport:
    """Create an httpx transport for Unix socket communication."""
    return httpx.AsyncHTTPTransport(uds=socket_path)


async def _bridge_get(socket_path: str, path: str) -> dict | None:
    """Send a GET request to the bridge over the Unix socket.

    Returns the parsed JSON response dict, or None if the bridge is
    unreachable (connection error, timeout).
    """
    try:
        transport = _make_bridge_transport(socket_path)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://bridge",
            timeout=_BRIDGE_TIMEOUT,
        ) as client:
            response = await client.get(path)
            response.raise_for_status()
            return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        logger.debug("Bridge unreachable at %s (GET %s)", socket_path, path)
        return None
    except Exception:
        logger.warning("Unexpected error contacting bridge (GET %s)", path, exc_info=True)
        return None


async def _bridge_post(socket_path: str, path: str, body: dict | None = None) -> dict | None:
    """Send a POST request to the bridge over the Unix socket.

    Returns the parsed JSON response dict, or None if the bridge is
    unreachable (connection error, timeout).
    """
    try:
        transport = _make_bridge_transport(socket_path)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://bridge",
            timeout=_BRIDGE_TIMEOUT,
        ) as client:
            response = await client.post(path, json=body or {})
            response.raise_for_status()
            return response.json()
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        logger.debug("Bridge unreachable at %s (POST %s)", socket_path, path)
        return None
    except Exception:
        logger.warning("Unexpected error contacting bridge (POST %s)", path, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Phone number masking
# ---------------------------------------------------------------------------


def _mask_phone(phone: str | None) -> str | None:
    """Mask a phone number for display, e.g. '+12345677890' → '+1 *** *** 7890'.

    If the phone is None or empty, returns None.
    If the phone has fewer than 4 digits, returns it unchanged.
    Note: uses the first digit as the country calling code prefix; multi-digit
    country codes (e.g. +44) will show only the first digit of the prefix.
    """
    if not phone:
        return None

    # Strip leading '+'
    digits = phone.lstrip("+")
    if len(digits) < 4:
        return phone

    # Keep last 4 digits, mask the middle
    tail = digits[-4:]
    prefix = digits[0] if digits else ""
    return f"+{prefix} *** *** {tail}"


# ---------------------------------------------------------------------------
# State mapping helper
# ---------------------------------------------------------------------------


def _bridge_state_to_enum(raw_state: str | None) -> WhatsAppState:
    """Map a raw bridge state string to a WhatsAppState enum value."""
    mapping = {
        "connected": WhatsAppState.connected,
        "connecting": WhatsAppState.disconnected,
        "disconnected": WhatsAppState.disconnected,
        "pair_required": WhatsAppState.pair_required,
    }
    return mapping.get(raw_state or "", WhatsAppState.not_configured)


def _looks_like_invalidated_session(data: dict) -> bool:
    """Best-effort heuristic: does this bridge /status look like a
    persistently-invalidated session (bu-5ocmh) rather than a normal
    transient reconnect blip?

    The bridge itself never reports this distinctly — it only ever reports
    ``pair_required`` for a brand-new, *never-paired* device (set the moment
    it boots with no stored device); a device it can't reconnect with just
    cycles ``disconnected``/``connecting`` forever. This process (a separate
    container from the connector, reachable only via this Unix socket — see
    module docstring) has no memory of *when* that started, so it uses
    ``uptime_s`` as a proxy: a bridge holding a device reconnects within
    ~15s under normal conditions (see whatsapp-bridge spec), so one that has
    been running far longer than that while never reaching a live
    connected+logged-in link is almost certainly stuck on a dead device
    rather than still legitimately retrying.

    Mirrors ``bridge_manager.BridgeConfig.invalidated_session_threshold_s``
    on the connector side (imported as one constant so the two stay in sync).
    """
    raw_state = data.get("state")
    if raw_state not in ("disconnected", "connecting"):
        return False
    connected = data.get("connected")
    logged_in = data.get("logged_in")
    link_dead = connected is False or logged_in is False
    if not link_dead:
        return False
    uptime = data.get("uptime_s")
    return isinstance(uptime, (int, float)) and uptime >= _INVALIDATED_SESSION_THRESHOLD_S


async def _request_pair_reset(pool: asyncpg.Pool) -> bool:
    """Flag the whatsapp_user_client connector to clear its stale whatsmeow
    device store and restart into QR pairing (bu-5ocmh recovery path).

    The connector runs in a separate container reachable only via the
    shared bridge Unix socket (see module docstring) — this process cannot
    reach into its BridgeSubprocessManager directly. Instead this writes
    ``pair_reset_requested_at`` into ``connector_registry.settings``, the
    same dashboard-to-connector handoff already used for live-reloadable
    settings like ``flush_interval_s``; the connector's watchdog (running
    every ~60s) picks it up and performs the actual clear+restart.

    Returns True if a ``whatsapp_user_client`` connector_registry row was
    found and flagged, False if the connector has never started (no row to
    flag yet).
    """
    from butlers.connectors.cursor_store import save_connector_settings

    row = await pool.fetchrow(
        """
        SELECT endpoint_identity
        FROM switchboard.connector_registry
        WHERE connector_type = $1
        ORDER BY last_heartbeat_at DESC NULLS LAST
        LIMIT 1
        """,
        _WHATSAPP_CONNECTOR_TYPE,
    )
    if row is None:
        return False

    await save_connector_settings(
        pool,
        _WHATSAPP_CONNECTOR_TYPE,
        row["endpoint_identity"],
        {"pair_reset_requested_at": datetime.now(UTC).isoformat()},
    )
    return True


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=WhatsAppStatusResponse)
async def get_whatsapp_status(
    socket_path: str = Depends(_get_bridge_socket_path),
) -> WhatsAppStatusResponse:
    """Return the current WhatsApp connection state.

    Proxies the bridge's /status endpoint. Returns not_configured with
    bridge_running=False when the bridge is unreachable.
    """
    data = await _bridge_get(socket_path, "/status")

    if data is None:
        return WhatsAppStatusResponse(
            state=WhatsAppState.not_configured,
            bridge_running=False,
        )

    raw_state = data.get("state")
    state = _bridge_state_to_enum(raw_state)
    if state == WhatsAppState.disconnected and _looks_like_invalidated_session(data):
        # A device the bridge has been unable to reconnect for a long time
        # is functionally identical to needing a re-pair (bu-5ocmh) — showing
        # a bare "disconnected" forever hides that from the owner.
        state = WhatsAppState.pair_required

    phone = _mask_phone(data.get("phone"))

    paired_at: datetime | None = None
    raw_paired = data.get("paired_at")
    if raw_paired:
        try:
            paired_at = datetime.fromisoformat(raw_paired)
        except (ValueError, TypeError):
            pass

    last_sync_at: datetime | None = None
    raw_last = data.get("last_event_at")
    if raw_last:
        try:
            last_sync_at = datetime.fromisoformat(raw_last)
        except (ValueError, TypeError):
            pass

    return WhatsAppStatusResponse(
        state=state,
        phone=phone,
        paired_at=paired_at,
        last_sync_at=last_sync_at,
        bridge_running=True,
    )


# ---------------------------------------------------------------------------
# POST /pair/start
# ---------------------------------------------------------------------------


@router.post("/pair/start", response_model=WhatsAppPairStartResponse)
async def start_whatsapp_pairing(
    socket_path: str = Depends(_get_bridge_socket_path),
    db: DatabaseManager = Depends(_get_db_manager),
) -> WhatsAppPairStartResponse:
    """Instruct the bridge to generate a new QR code for pairing.

    Returns the QR code as a base64 PNG data URI plus expiry timestamp.
    Raises HTTP 503 if the bridge is not running (cannot generate QR).

    bu-5ocmh: a bridge holding a device it can no longer reconnect with
    never enters its own QR-pairing flow, so it never has a QR code to
    offer here either — the *only* way in is the connector clearing that
    dead device and restarting into pairing mode. If that's what this looks
    like, flag it for the connector (see ``_request_pair_reset``) instead of
    just reporting the same "no QR code" error forever.
    """
    data = await _bridge_post(socket_path, "/pair/start")
    if data is None:
        # One quick retry: a bridge process restart can leave the Unix
        # socket briefly unbound even though the connector is otherwise
        # healthy — see _PAIR_START_RETRY_DELAY_S.
        await asyncio.sleep(_PAIR_START_RETRY_DELAY_S)
        data = await _bridge_post(socket_path, "/pair/start")

    if data is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not connect to WhatsApp bridge. Ensure the connector service is running."
            ),
        )

    qr_data_uri = data.get("qr_data_uri", "")
    if not qr_data_uri:
        status_data = await _bridge_get(socket_path, "/status")
        if status_data is not None and _looks_like_invalidated_session(status_data):
            flagged = False
            try:
                pool = _get_switchboard_pool(db)
                if pool is not None:
                    flagged = await _request_pair_reset(pool)
            except Exception:
                logger.exception("Failed to request WhatsApp pair-reset")
            if flagged:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "WhatsApp session appears invalidated (a previously-paired device "
                        "cannot reconnect). Clearing the stale session and restarting into "
                        "QR pairing mode — this can take up to a minute. Please retry shortly."
                    ),
                )
        raise HTTPException(
            status_code=502,
            detail="Bridge returned an empty QR code. Check bridge logs.",
        )

    # Parse expiry from bridge response; default to 60 seconds from now
    expires_at: datetime
    raw_expires = data.get("expires_at")
    if raw_expires:
        try:
            expires_at = datetime.fromisoformat(raw_expires)
        except (ValueError, TypeError):
            expires_at = datetime.now(UTC) + timedelta(seconds=60)
    else:
        expires_at = datetime.now(UTC) + timedelta(seconds=60)

    return WhatsAppPairStartResponse(
        qr_data_uri=qr_data_uri,
        expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# GET /pair/poll
# ---------------------------------------------------------------------------


@router.get("/pair/poll", response_model=WhatsAppPairPollResponse)
async def poll_whatsapp_pairing(
    socket_path: str = Depends(_get_bridge_socket_path),
) -> WhatsAppPairPollResponse:
    """Poll the current pairing progress.

    Returns waiting/paired/expired. Falls back to 'waiting' if the bridge
    is unreachable (avoids crashing the polling loop).
    """
    data = await _bridge_get(socket_path, "/pair/poll")

    if data is None:
        # Bridge unreachable during polling — return waiting so the frontend
        # retries rather than showing a hard error
        return WhatsAppPairPollResponse(status=WhatsAppPairStatus.waiting)

    raw_status = data.get("status", "waiting")
    status_map = {
        "waiting": WhatsAppPairStatus.waiting,
        "paired": WhatsAppPairStatus.paired,
        "expired": WhatsAppPairStatus.expired,
    }
    status = status_map.get(raw_status, WhatsAppPairStatus.waiting)

    phone: str | None = None
    if status == WhatsAppPairStatus.paired:
        phone = _mask_phone(data.get("phone"))

    return WhatsAppPairPollResponse(status=status, phone=phone)


# ---------------------------------------------------------------------------
# POST /disconnect
# ---------------------------------------------------------------------------


@router.post("/disconnect", response_model=WhatsAppDisconnectResponse)
async def disconnect_whatsapp(
    socket_path: str = Depends(_get_bridge_socket_path),
) -> WhatsAppDisconnectResponse:
    """Instruct the bridge to gracefully disconnect and mark the session inactive.

    Returns success=True even when the bridge is already unreachable
    (idempotent disconnect semantics).
    """
    data = await _bridge_post(socket_path, "/disconnect")

    if data is None:
        # Bridge not running — session is already effectively disconnected
        logger.info("Disconnect requested but bridge is not running; treating as success")
        return WhatsAppDisconnectResponse(
            success=True,
            message="WhatsApp disconnected (bridge was not running)",
        )

    return WhatsAppDisconnectResponse(
        success=data.get("success", True),
        message=data.get("message", "WhatsApp disconnected"),
    )


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@router.get("/health", response_model=WhatsAppHealthResponse)
async def get_whatsapp_health(
    socket_path: str = Depends(_get_bridge_socket_path),
) -> WhatsAppHealthResponse:
    """Proxy the bridge /status endpoint and return session health.

    Returns not_configured with bridge_running=False when the bridge is
    unreachable, suitable for displaying the amber "not running" badge.
    """
    data = await _bridge_get(socket_path, "/status")

    if data is None:
        return WhatsAppHealthResponse(
            state=WhatsAppState.not_configured,
            bridge_running=False,
        )

    raw_state = data.get("state")
    state = _bridge_state_to_enum(raw_state)
    if state == WhatsAppState.disconnected and _looks_like_invalidated_session(data):
        state = WhatsAppState.pair_required

    uptime: float | None = data.get("uptime_s")

    last_event_at: datetime | None = None
    raw_last = data.get("last_event_at")
    if raw_last:
        try:
            last_event_at = datetime.fromisoformat(raw_last)
        except (ValueError, TypeError):
            pass

    return WhatsAppHealthResponse(
        state=state,
        bridge_running=True,
        uptime_seconds=uptime,
        last_event_at=last_event_at,
    )
