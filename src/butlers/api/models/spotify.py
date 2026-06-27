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

    needs_reauth = "needs_reauth"
    """Tokens present but granted scopes are insufficient — re-authorization required."""


class SpotifyStatusResponse(BaseModel):
    """Response for GET /api/connectors/spotify/status.

    Reports the current Spotify connection state for the settings page.
    Field shape conforms to the ``dashboard-spotify-setup`` spec and the
    frontend ``SpotifyStatusResponse`` interface consumed by the settings
    drawer (``SpotifyDrawerContent``).
    """

    connected: bool = False
    """True when tokens are present and verified against Spotify /me."""

    state: SpotifyConnectionState
    """Machine-readable connectivity state."""

    spotify_user_id: str | None = None
    """Spotify user id of the authenticated user, or null."""

    display_name: str | None = None
    """Spotify display name of the authenticated user, or null."""

    account_type: str | None = None
    """Spotify product/account type (e.g. 'premium', 'free'), or null."""

    last_sync_at: datetime | None = None
    """ISO datetime of the last successful /me verification, or null."""

    error: str | None = None
    """Human-readable error description when the connection is unhealthy."""

    needs_reauth: bool = False
    """True when stored scopes are insufficient for current requirements."""

    missing_scopes: list[str] = []
    """List of OAuth scopes that are required but were not granted."""


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

    configured: bool = True
    """True when the client_id was stored in CredentialStore."""


class SpotifyDisconnectResponse(BaseModel):
    """Response for POST /api/connectors/spotify/disconnect."""

    disconnected: bool = True
    """True when Spotify credentials were cleared from CredentialStore."""
