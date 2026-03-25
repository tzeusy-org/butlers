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

import logging
import os
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException

from butlers.api.models.whatsapp import (
    WhatsAppDisconnectResponse,
    WhatsAppHealthResponse,
    WhatsAppPairPollResponse,
    WhatsAppPairStartResponse,
    WhatsAppPairStatus,
    WhatsAppState,
    WhatsAppStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors/whatsapp", tags=["whatsapp"])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_BRIDGE_SOCKET = "/tmp/wa-bridge.sock"
_BRIDGE_TIMEOUT = 5.0  # seconds


def _get_bridge_socket_path() -> str:
    """Return the path to the Go bridge Unix socket.

    Reads WHATSAPP_BRIDGE_SOCKET env var; falls back to /tmp/wa-bridge.sock.
    Override via app.dependency_overrides[_get_bridge_socket_path] in tests.
    """
    return os.environ.get("WHATSAPP_BRIDGE_SOCKET", _DEFAULT_BRIDGE_SOCKET)


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
) -> WhatsAppPairStartResponse:
    """Instruct the bridge to generate a new QR code for pairing.

    Returns the QR code as a base64 PNG data URI plus expiry timestamp.
    Raises HTTP 503 if the bridge is not running (cannot generate QR).
    """
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

    uptime: float | None = data.get("uptime_seconds")

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
