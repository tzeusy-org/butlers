"""Pydantic models for WhatsApp dashboard API endpoints.

Provides request/response models for:
- Connection status
- QR pairing flow (start, poll)
- Session health
- Disconnect
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class WhatsAppState(StrEnum):
    """Operational state of the WhatsApp session.

    Values are stable identifiers for frontend conditional rendering.
    """

    connected = "connected"
    """Session active, bridge connected."""

    disconnected = "disconnected"
    """Session exists but bridge not connected."""

    pair_required = "pair_required"
    """No valid session; QR pairing needed."""

    not_configured = "not_configured"
    """No WhatsApp setup attempted."""


class WhatsAppPairStatus(StrEnum):
    """Status of a QR pairing attempt."""

    waiting = "waiting"
    """Pairing is still in progress — waiting for QR scan."""

    paired = "paired"
    """Pairing completed successfully."""

    expired = "expired"
    """QR code expired without being scanned."""


class WhatsAppStatusResponse(BaseModel):
    """Response for GET /api/connectors/whatsapp/status.

    Reports the current WhatsApp connection state for the settings page.
    """

    state: WhatsAppState
    """Machine-readable connectivity state."""

    phone: str | None = None
    """Connected phone number (masked for display, e.g. '+1 *** *** 7890'), or null."""

    paired_at: datetime | None = None
    """ISO datetime when the account was first paired, or null."""

    last_sync_at: datetime | None = None
    """ISO datetime of the last successful sync, or null."""

    bridge_running: bool
    """Whether the Go bridge subprocess is currently running."""


class WhatsAppPairStartResponse(BaseModel):
    """Response for POST /api/connectors/whatsapp/pair/start.

    Returns a QR code data URI and expiry for the pairing modal.
    """

    qr_data_uri: str
    """Base64-encoded PNG data URI: 'data:image/png;base64,...'"""

    expires_at: datetime
    """ISO datetime when this QR code expires (typically ~60 seconds)."""


class WhatsAppPairPollResponse(BaseModel):
    """Response for GET /api/connectors/whatsapp/pair/poll.

    Used by the frontend to poll pairing progress.
    """

    status: WhatsAppPairStatus
    """Current pairing status."""

    phone: str | None = None
    """Connected phone number when status == 'paired', otherwise null."""


class WhatsAppHealthResponse(BaseModel):
    """Response for GET /api/connectors/whatsapp/health.

    Proxies bridge /status and surfaces session health for the badge.
    """

    state: WhatsAppState
    """Current session state."""

    bridge_running: bool
    """Whether the bridge is reachable."""

    uptime_seconds: float | None = None
    """Bridge uptime in seconds, if available."""

    last_event_at: datetime | None = None
    """Timestamp of the last WhatsApp event, if available."""


class WhatsAppDisconnectResponse(BaseModel):
    """Response for POST /api/connectors/whatsapp/disconnect."""

    success: bool = True
    message: str = "WhatsApp disconnected"
