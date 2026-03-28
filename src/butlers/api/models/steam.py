"""Pydantic models for Steam dashboard API endpoints.

Provides request/response models for:
- Account connection (POST /api/steam/accounts)
- Account listing (GET /api/steam/accounts)
- Account disconnection (DELETE /api/steam/accounts/{id})
- Playtime analytics (GET /api/steam/playtime)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Account models
# ---------------------------------------------------------------------------


class SteamConnectRequest(BaseModel):
    """Request body for POST /api/steam/accounts.

    Validates and registers a new Steam account with the provided API key.
    The API key is validated via a test call to the Steam Web API before
    the account is stored.
    """

    steam_id: int = Field(
        ...,
        description="Steam 64-bit account ID (SteamID64).",
        gt=0,
    )
    api_key: str = Field(
        ...,
        description="Steam Web API key (32-character hex string).",
        min_length=32,
        max_length=32,
        pattern=r"^[0-9A-Fa-f]{32}$",
    )
    display_name: str | None = Field(
        default=None,
        description="Optional display name override. If omitted, fetched from Steam API.",
    )


class SteamAccountResponse(BaseModel):
    """Representation of a connected Steam account.

    Returned by GET /api/steam/accounts (list) and POST /api/steam/accounts.
    API keys are never included in responses.
    """

    id: uuid.UUID
    """UUID primary key of the steam_accounts row."""

    steam_id: int
    """Steam 64-bit account ID."""

    display_name: str | None = None
    """Steam persona name, or null if not available."""

    profile_url: str | None = None
    """URL to the Steam profile page, or null."""

    avatar_url: str | None = None
    """URL to the Steam avatar image, or null."""

    is_primary: bool
    """Whether this is the active primary account."""

    status: str
    """Account status: one of 'active', 'suspended', 'revoked'."""

    connected_at: datetime
    """Timestamp when the account was first connected."""

    last_poll_at: datetime | None = None
    """Timestamp of the last successful poll, or null."""


class SteamConnectResponse(BaseModel):
    """Response for POST /api/steam/accounts.

    Returned after a Steam account has been validated and registered.
    """

    success: bool = True
    """Whether the account was successfully connected."""

    message: str
    """Human-readable status message."""

    account: SteamAccountResponse
    """The newly connected Steam account record."""


class SteamAccountListResponse(BaseModel):
    """Response for GET /api/steam/accounts."""

    accounts: list[SteamAccountResponse]
    """All connected Steam accounts, ordered by primary first, then connected_at."""


class SteamDisconnectResponse(BaseModel):
    """Response for DELETE /api/steam/accounts/{id}."""

    success: bool = True
    """Whether the account was successfully disconnected."""

    message: str = "Steam account disconnected"
    """Human-readable status message."""


class SteamSetPrimaryResponse(BaseModel):
    """Response for PUT /api/steam/accounts/{id}/primary."""

    success: bool = True
    """Whether the primary account was successfully updated."""

    message: str
    """Human-readable status message."""

    account: SteamAccountResponse
    """The account that was set as primary."""


# ---------------------------------------------------------------------------
# Playtime analytics models
# ---------------------------------------------------------------------------


class SteamGamePlaytime(BaseModel):
    """Playtime record for a single game."""

    app_id: int
    """Steam application ID."""

    name: str | None = None
    """Game name, or null if not available."""

    playtime_forever_minutes: int
    """Total playtime across all time in minutes."""

    playtime_2weeks_minutes: int | None = None
    """Playtime in the last 2 weeks in minutes, or null if none."""

    img_icon_url: str | None = None
    """Icon URL suffix for the game icon (relative to steam CDN), or null."""


class SteamPlaytimeAnalytics(BaseModel):
    """Aggregated playtime analytics for a Steam account.

    Returned by GET /api/steam/playtime.
    """

    account_id: uuid.UUID
    """UUID of the Steam account these analytics are for."""

    steam_id: int
    """Steam 64-bit account ID."""

    display_name: str | None = None
    """Steam persona name, or null."""

    total_games: int
    """Total number of games in the library."""

    total_playtime_minutes: int
    """Sum of playtime_forever across all games."""

    top_games: list[SteamGamePlaytime]
    """Top games by total playtime, limited to the requested count."""

    recently_played: list[SteamGamePlaytime]
    """Games played in the last 2 weeks with playtime > 0."""

    fetched_at: datetime
    """Timestamp when this data was fetched from the Steam API."""
