"""Pydantic models for Steam dashboard API endpoints.

Provides request/response models for:
- Account connection (POST /api/steam/accounts)
- Account listing (GET /api/steam/accounts)
- Account disconnection (DELETE /api/steam/accounts/{id})
- Account status (GET /api/steam/accounts/{id}/status)
- Playtime analytics (GET /api/steam/playtime)
- Per-game playtime history (GET /api/steam/playtime/{app_id})
- Connector health proxy (GET /api/steam/connector/health)
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

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
# Account status model
# ---------------------------------------------------------------------------


class SteamAccountStatusResponse(BaseModel):
    """Per-account status response for GET /api/steam/accounts/{id}/status.

    Provides credential health, poll recency, and connector health summary
    for a single Steam account without exposing the API key.
    """

    id: uuid.UUID
    """UUID primary key of the steam_accounts row."""

    steam_id: int
    """Steam 64-bit account ID."""

    status: str
    """Account status: one of 'active', 'suspended', 'revoked'."""

    has_api_key: bool
    """Whether an API key is stored in entity_info for this account."""

    key_valid: bool | None = None
    """Whether the stored API key has been validated successfully.

    - ``True``  — most recent validation passed.
    - ``False`` — most recent validation failed.
    - ``None``  — no validation has been performed yet.
    """

    last_poll_at: datetime | None = None
    """Timestamp of the last successful connector poll, or null if never polled."""

    connector_health: str | None = None
    """Health status reported by the connector for this account.

    One of 'healthy', 'degraded', 'error', or null when the connector is
    not running or this account is not tracked.
    """


# ---------------------------------------------------------------------------
# Connector health proxy models
# ---------------------------------------------------------------------------


class SteamConnectorDataTypeHealth(BaseModel):
    """Health snapshot for a single data-type poller within an account."""

    status: str
    """Poller status: 'healthy', 'degraded', or 'error'."""

    last_poll_at: datetime | None = None
    """Timestamp of the last successful poll for this data type, or null."""


class SteamConnectorAccountHealth(BaseModel):
    """Health summary for a single Steam account tracked by the connector."""

    steam_id: str
    """Redacted Steam 64-bit account ID (last 4 digits only, prefixed with ****)."""

    endpoint_identity: str
    """Endpoint identity label used in connector metrics."""

    status: str
    """Effective health: 'healthy', 'degraded', or 'error'."""

    error: str | None = None
    """Latest error message for this account, or null."""

    data_types: dict[str, SteamConnectorDataTypeHealth]
    """Per-data-type health keyed by data type name."""


class SteamConnectorHealthResponse(BaseModel):
    """Response for GET /api/steam/connector/health.

    Proxied directly from the connector's ``/health`` HTTP endpoint.
    Returns a degraded response when the connector is not running.
    """

    status: str
    """Overall connector status: 'healthy', 'degraded', 'error', or 'not_running'."""

    uptime_seconds: int | None = None
    """Connector uptime in seconds, or null when not running."""

    active_accounts: int | None = None
    """Number of accounts actively polled, or null when not running."""

    account_health: list[SteamConnectorAccountHealth] = []
    """Per-account health snapshots (empty when not running)."""

    connector_url: str | None = None
    """URL that was probed for the health check, or null."""


# ---------------------------------------------------------------------------
# Playtime analytics models
# ---------------------------------------------------------------------------


class SteamDailyPlaytimeSummary(BaseModel):
    """Aggregated playtime for a single calendar day across all games.

    Used in the daily rollup array of SteamPlaytimeAnalytics.
    """

    date: date
    """Calendar date of the playtime record."""

    total_minutes: int
    """Total playtime across all games on this date, in minutes."""


class SteamGamePlaytime(BaseModel):
    """Playtime record for a single game.

    Used in the games list of SteamPlaytimeAnalytics.
    """

    app_id: int
    """Steam application ID."""

    app_name: str | None = None
    """Game name, or null if not available."""

    total_minutes: int
    """Total playtime in the requested window, in minutes."""


class SteamPlaytimeAnalytics(BaseModel):
    """Aggregated playtime analytics for a Steam account.

    Returned by GET /api/steam/playtime.
    Data is sourced from the connectors.steam_play_history table.
    """

    account_id: uuid.UUID
    """UUID of the Steam account these analytics are for."""

    steam_id: int
    """Steam 64-bit account ID."""

    display_name: str | None = None
    """Steam persona name, or null."""

    days: int | None = None
    """Number of days of history included, or null for all-time."""

    total_games: int
    """Total number of distinct games with playtime in the requested window."""

    total_minutes: int
    """Sum of playtime minutes across all games in the requested window."""

    games: list[SteamGamePlaytime]
    """Top games by total playtime in the requested window, limited to top_n."""

    daily: list[SteamDailyPlaytimeSummary]
    """Daily rollup of total playtime across all games, ordered by date ascending."""

    queried_at: datetime
    """Timestamp when this data was queried from the database."""


class SteamGamePlaytimeHistoryEntry(BaseModel):
    """A single daily playtime entry for a specific game.

    Used in SteamGamePlaytimeHistory.
    """

    date: date
    """Date of the playtime record."""

    playtime_minutes: int
    """Playtime recorded on this date, in minutes."""

    recorded_at: datetime
    """Timestamp when this record was written to the database."""


class SteamGamePlaytimeHistory(BaseModel):
    """Per-game playtime history from the connectors.steam_play_history table.

    Returned by GET /api/steam/playtime/{app_id}.
    """

    account_id: uuid.UUID
    """UUID of the Steam account these analytics are for."""

    steam_id: int
    """Steam 64-bit account ID."""

    display_name: str | None = None
    """Steam persona name, or null."""

    app_id: int
    """Steam application ID."""

    app_name: str | None = None
    """Game name from the play history table, or null if not recorded."""

    days: int | None = None
    """Number of days of history included, or null for all-time."""

    total_minutes: int
    """Sum of playtime minutes across all rows in the requested window."""

    history: list[SteamGamePlaytimeHistoryEntry]
    """Individual daily playtime records, ordered by date descending."""

    queried_at: datetime
    """Timestamp when this data was queried from the database."""
