"""Pydantic models for Spotify dashboard API endpoints.

Provides request/response models for:
- Connection status
- OAuth PKCE flow (start, callback)
- Disconnect
- Connector config
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class SpotifyConnectionState(StrEnum):
    """Operational state of the Spotify connection.

    Values are stable identifiers for frontend conditional rendering.
    """

    connected = "connected"
    """OAuth tokens present and verified against Spotify /me."""

    error = "error"
    """Stored connector state is malformed."""

    unconfigured = "unconfigured"
    """No Spotify client_id configured — setup required."""

    authorization_needed = "authorization_needed"
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

    capability_categories: list[Literal["listening-history"]] = Field(
        default_factory=lambda: ["listening-history"]
    )
    """Fixed non-sensitive capability projection for the connector."""


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
    """True when Spotify OAuth rows were cleared from owner entity_info."""
