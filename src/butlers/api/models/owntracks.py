"""Pydantic models for OwnTracks dashboard API endpoints.

Provides request/response models for:
- Token generation
- Connection status
- Connector config (webhook URL + setup metadata)
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class OwnTracksConnectionState(StrEnum):
    """Operational state of the OwnTracks connector.

    Values are stable identifiers for frontend conditional rendering.
    """

    connected = "connected"
    """Connector is running and events have been received recently."""

    no_events = "no_events"
    """Token configured and connector running but no events received yet."""

    stale = "stale"
    """Connector last heartbeat is older than the liveness threshold."""

    not_configured = "not_configured"
    """No webhook token has been generated — setup required."""

    offline = "offline"
    """Connector process is not running (no recent heartbeat)."""


class OwnTracksTokenResponse(BaseModel):
    """Response for POST /api/connectors/owntracks/token/generate.

    Returns the newly generated bearer token. The token is shown once;
    the caller must copy it and configure the OwnTracks app immediately.
    """

    token: str
    """The newly generated 64-character hex bearer token (32 bytes)."""

    message: str = "Token generated. Copy it now — it will not be shown again."


class OwnTracksStatusResponse(BaseModel):
    """Response for GET /api/connectors/owntracks/status.

    Reports connection state, last event timestamp, and event counts
    derived from connector heartbeat data.
    """

    state: OwnTracksConnectionState
    """Machine-readable connectivity state."""

    token_configured: bool
    """Whether a webhook token has been stored in CredentialStore."""

    last_event_at: datetime | None = None
    """ISO datetime of the last received event, from heartbeat data, or null."""

    events_today: int = 0
    """Number of events received today (from connector heartbeat counters)."""

    connector_running: bool = False
    """Whether the connector process is alive (recent heartbeat present)."""

    uptime_seconds: float | None = None
    """Connector uptime in seconds, if available."""


class OwnTracksConfigResponse(BaseModel):
    """Response for GET /api/connectors/owntracks/config.

    Returns the computed webhook URL and setup instructions metadata
    to help the user configure the OwnTracks mobile app.
    """

    webhook_url: str
    """Full webhook URL for the OwnTracks app (e.g. https://host:port/owntracks/webhook)."""

    token_masked: str | None = None
    """Masked token preview for confirmation display (e.g. 'ab12...ef90'), or null if not set."""

    setup_instructions: OwnTracksSetupInstructions
    """Inline app configuration instructions for iOS and Android."""


class OwnTracksSetupInstructions(BaseModel):
    """Inline setup instructions for the OwnTracks mobile app.

    Surfaces step-by-step guidance in the dashboard UI to minimize
    context-switching between the dashboard and the OwnTracks app.
    """

    mode: str = "HTTP"
    """Transport mode to configure in the OwnTracks app."""

    url_field: str = "URL"
    """Name of the URL field in the OwnTracks settings screen."""

    auth_type: str = "Bearer token"
    """Authentication method to select in the app settings."""

    steps_ios: list[str]
    """iOS-specific step-by-step configuration instructions."""

    steps_android: list[str]
    """Android-specific step-by-step configuration instructions."""

    troubleshooting_hint: str = (
        "If no events appear after 1 hour, verify the OwnTracks app has location permissions "
        "and the webhook URL is reachable from your device."
    )
