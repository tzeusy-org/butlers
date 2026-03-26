"""Pydantic models for Home Assistant dashboard settings endpoints.

Provides request/response models for:
- Connection status (GET /api/settings/home-assistant)
- Connection configuration and validation (POST /api/settings/home-assistant)
- Connection removal (DELETE /api/settings/home-assistant)
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class HAConnectionState(StrEnum):
    """Operational state of the Home Assistant connection.

    Values are stable identifiers for frontend conditional rendering.
    """

    connected = "connected"
    """HA URL and token present and verified against GET /api/."""

    disconnected = "disconnected"
    """HA URL and token present but failed verification."""

    not_configured = "not_configured"
    """No HA URL or token configured — setup required."""


class HAStatusResponse(BaseModel):
    """Response for GET /api/settings/home-assistant.

    Reports the current HA connection state for the settings page.
    URL is masked (only the base origin is shown) to avoid leaking credentials.
    """

    state: HAConnectionState
    """Machine-readable connectivity state."""

    url_configured: bool = False
    """Whether an HA URL is stored in CredentialStore."""

    token_configured: bool = False
    """Whether an HA access token is stored in CredentialStore."""

    masked_url: str | None = None
    """Base origin of the HA URL (e.g. 'http://homeassistant.local:8123'), or null."""


class HAConfigRequest(BaseModel):
    """Request body for POST /api/settings/home-assistant.

    Validates the HA connection and stores the URL + access token in CredentialStore.
    """

    url: str = Field(
        ...,
        description="Home Assistant base URL (e.g. http://homeassistant.local:8123).",
        min_length=1,
    )
    token: str = Field(
        ...,
        description="Long-lived access token from Home Assistant.",
        min_length=1,
    )


class HAConfigResponse(BaseModel):
    """Response for POST /api/settings/home-assistant.

    Includes connection validation result and masked URL.
    """

    success: bool
    """Whether the connection was validated and credentials were stored."""

    message: str
    """Human-readable status message."""

    masked_url: str | None = None
    """Base origin of the stored HA URL, or null on failure."""


class HADeleteResponse(BaseModel):
    """Response for DELETE /api/settings/home-assistant."""

    success: bool = True
    message: str = "Home Assistant credentials removed"
