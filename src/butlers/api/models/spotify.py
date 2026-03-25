"""Pydantic models for Spotify dashboard API endpoints.

Provides request/response models for:
- Connection status
- OAuth PKCE flow (start, callback)
- Disconnect
- Connector config
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SpotifyConnectionState(StrEnum):
    """Operational state of the Spotify connection.

    Values are stable identifiers for frontend conditional rendering.
    """

    connected = "connected"
    """OAuth tokens present and verified against Spotify /me."""

    disconnected = "disconnected"
    """Credentials partially present but unverified or expired."""

    not_configured = "not_configured"
    """No Spotify client_id configured — setup required."""

    needs_auth = "needs_auth"
    """Client ID configured but no OAuth tokens — authorization required."""


class SpotifyStatusResponse(BaseModel):
    """Response for GET /api/connectors/spotify/status.

    Reports the current Spotify connection state for the settings page.
    """

    state: SpotifyConnectionState
    """Machine-readable connectivity state."""

    display_name: str | None = None
    """Spotify display name of the authenticated user, or null."""

    email: str | None = None
    """Spotify account email, or null."""

    product: str | None = None
    """Spotify product type (e.g. 'premium', 'free'), or null."""

    last_verified_at: datetime | None = None
    """ISO datetime of the last successful /me verification, or null."""

    client_id_configured: bool = False
    """Whether a client_id is stored in CredentialStore."""


class SpotifyOAuthStartResponse(BaseModel):
    """Response for POST /api/connectors/spotify/oauth/start.

    Returns the Spotify authorization URL to redirect the user to.
    """

    authorization_url: str
    """Full Spotify authorization URL including all query params."""

    state: str
    """CSRF state token (opaque to the client — included in callback for validation)."""


class SpotifyConfigRequest(BaseModel):
    """Request body for POST /api/connectors/spotify/config.

    Stores the Spotify app's client_id in CredentialStore.
    The client_secret is not needed for PKCE flows.
    """

    client_id: str = Field(
        ...,
        min_length=32,
        max_length=32,
        pattern=r"^[0-9a-f]{32}$",
        description="Spotify app client_id (32-character lowercase hex string).",
    )


class SpotifyConfigResponse(BaseModel):
    """Response for POST /api/connectors/spotify/config."""

    success: bool = True
    message: str = "Spotify client_id saved"


class SpotifyDisconnectResponse(BaseModel):
    """Response for POST /api/connectors/spotify/disconnect."""

    success: bool = True
    message: str = "Spotify disconnected"
