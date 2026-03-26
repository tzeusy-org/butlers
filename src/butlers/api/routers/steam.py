"""Steam account management and playtime analytics endpoints for the dashboard.

Provides ``router`` at ``/api/steam``:

- ``POST   /api/steam/accounts``         — connect a new Steam account (validates API key)
- ``GET    /api/steam/accounts``         — list all connected Steam accounts
- ``DELETE /api/steam/accounts/{id}``    — disconnect a Steam account (soft-revoke)
- ``GET    /api/steam/playtime``         — playtime analytics (top games, recently played)

API key validation:
  The POST endpoint calls ``ISteamUser/GetPlayerSummaries`` with the provided
  API key and steam_id before storing credentials. If the API key is invalid
  (HTTP 403), or the steam_id is not found in the response, a 400 is returned.

Playtime analytics:
  Fetches ``IPlayerService/GetOwnedGames`` and ``IPlayerService/GetRecentlyPlayedGames``
  from the Steam Web API for the primary account (or a specific account via
  the ``account_id`` query parameter).

Security notes:
  - API keys are stored in ``public.entity_info`` (secured=true) and never
    returned in any response.
  - Playtime data is fetched live from the Steam API on each request.
  - The DELETE endpoint soft-revokes (status='revoked') by default; the account
    row and its credentials are retained for audit purposes.

Connection validation error categories:
  - ``invalid_api_key``   — Steam returned 403 (bad or unauthorized key)
  - ``steam_id_not_found`` — API call succeeded but the steam_id was not found
  - ``api_error``         — unexpected error from Steam
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.models.steam import (
    SteamAccountListResponse,
    SteamAccountResponse,
    SteamConnectRequest,
    SteamConnectResponse,
    SteamDisconnectResponse,
    SteamGamePlaytime,
    SteamPlaytimeAnalytics,
)
from butlers.steam.client import SteamAPIClient, SteamAPIError, SteamRateLimitError
from butlers.steam_account_registry import (
    MissingSteamCredentialsError,
    SteamAccount,
    SteamAccountAlreadyExistsError,
    SteamAccountNotFoundError,
    create_steam_account,
    disconnect_account,
    list_steam_accounts,
    resolve_steam_account,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/steam", tags=["steam"])

# ---------------------------------------------------------------------------
# Credential key for entity_info
# ---------------------------------------------------------------------------

_ENTITY_INFO_TYPE_API_KEY = "steam_api_key"

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

_VALIDATION_TIMEOUT_S = 10.0
_PLAYTIME_TIMEOUT_S = 15.0

# ---------------------------------------------------------------------------
# Dependency injection stub
# ---------------------------------------------------------------------------


def _get_db_manager() -> Any:
    """Stub replaced at startup by wire_db_dependencies().

    When not wired (e.g. in tests that don't boot the full app), returns None
    so endpoints degrade gracefully.
    """
    return None


def _get_shared_pool(db_manager: Any) -> Any | None:
    """Resolve the shared asyncpg pool from a DatabaseManager.

    Returns None when db_manager is None or no usable pool can be resolved.
    Resolution order:
    1. Dedicated shared credential pool from DatabaseManager.
    2. Compatibility fallback to first butler pool.
    """
    if db_manager is None:
        return None

    try:
        pool = db_manager.credential_shared_pool()
        return pool
    except Exception:  # noqa: BLE001
        logger.debug("Shared credential pool unavailable", exc_info=True)

    butler_names = getattr(db_manager, "butler_names", [])
    if not butler_names:
        logger.debug("Shared credential pool unavailable and no butler pools are registered.")
        return None

    try:
        pool = db_manager.pool(butler_names[0])
        logger.warning(
            "Shared credential pool unavailable; using fallback pool from %s",
            butler_names[0],
        )
        return pool
    except Exception:  # noqa: BLE001
        logger.debug("Failed to obtain fallback DB pool; shared pool unavailable.", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class _SteamValidationError(Exception):
    """Raised when Steam API key or steam_id validation fails."""

    def __init__(self, message: str, category: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.category = category
        self.http_status = http_status


async def _validate_steam_credentials(
    api_key: str,
    steam_id: int,
) -> dict[str, Any]:
    """Validate a Steam API key and steam_id by calling GetPlayerSummaries.

    Parameters
    ----------
    api_key:
        Steam Web API key to validate.
    steam_id:
        Steam 64-bit account ID to look up.

    Returns
    -------
    dict
        The player summary dict from Steam (with ``steamid``, ``personaname``, etc.)

    Raises
    ------
    _SteamValidationError
        When the API key is invalid (category='invalid_api_key'), the steam_id
        is not found (category='steam_id_not_found'), or a Steam API error occurs
        (category='api_error').
    """
    try:
        async with SteamAPIClient(api_key=api_key) as client:
            data = await client.request(
                "ISteamUser",
                "GetPlayerSummaries",
                params={"steamids": str(steam_id)},
                version=2,
            )
    except SteamRateLimitError as exc:
        raise _SteamValidationError(
            f"Steam API is rate-limited; retry after {exc.retry_after_s:.0f}s. "
            "Wait before connecting the account.",
            category="api_error",
            http_status=429,
        ) from exc
    except SteamAPIError as exc:
        if exc.status_code in (401, 403):
            raise _SteamValidationError(
                "Steam API key is invalid or unauthorized (HTTP 403). "
                "Check your Steam Web API key at https://steamcommunity.com/dev/apikey",
                category="invalid_api_key",
                http_status=400,
            ) from exc
        raise _SteamValidationError(
            f"Steam API returned HTTP {exc.status_code}: {exc.body[:200]}. "
            "Check Steam API status at https://steamstat.us/ and retry.",
            category="api_error",
            http_status=502,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise _SteamValidationError(
            f"Network error contacting Steam API: {exc}. Ensure network connectivity and retry.",
            category="api_error",
            http_status=502,
        ) from exc

    players = data.get("players", [])
    if not players:
        raise _SteamValidationError(
            f"Steam account with steam_id={steam_id} was not found. "
            "Verify the SteamID64 is correct.",
            category="steam_id_not_found",
            http_status=400,
        )

    return players[0]


# ---------------------------------------------------------------------------
# Model conversion helper
# ---------------------------------------------------------------------------


def _account_to_response(account: SteamAccount) -> SteamAccountResponse:
    """Convert a SteamAccount dataclass to the API response model."""
    return SteamAccountResponse(
        id=account.id,
        steam_id=account.steam_id,
        display_name=account.display_name,
        profile_url=account.profile_url,
        avatar_url=account.avatar_url,
        is_primary=account.is_primary,
        status=account.status,
        connected_at=account.connected_at,
        last_poll_at=account.last_poll_at,
    )


# ---------------------------------------------------------------------------
# POST /api/steam/accounts
# ---------------------------------------------------------------------------


@router.post("/accounts", response_model=SteamConnectResponse)
async def connect_steam_account(
    body: SteamConnectRequest,
    db_manager: Any = Depends(_get_db_manager),
) -> SteamConnectResponse:
    """Connect a new Steam account by validating the API key and registering it.

    Validates the Steam Web API key by calling ``ISteamUser/GetPlayerSummaries``
    with the provided ``steam_id``. If validation succeeds, a new account row is
    inserted and the API key is stored securely in ``public.entity_info``.

    If no other accounts are connected, the new account is automatically set as
    the primary account.

    Raises HTTP 400 when the API key is invalid or the steam_id is not found.
    Raises HTTP 409 when the steam_id is already connected.
    Raises HTTP 429 when Steam rate-limits the validation call.
    Raises HTTP 502 on unexpected Steam API errors.
    Raises HTTP 503 when the database is unavailable.
    """
    pool = _get_shared_pool(db_manager)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Database is unavailable. Ensure the database service is running. "
                "The Steam account could not be registered."
            ),
        )

    # Validate API key + steam_id against the Steam API.
    try:
        player_summary = await _validate_steam_credentials(body.api_key, body.steam_id)
    except _SteamValidationError as exc:
        logger.warning("Steam credentials validation failed (category=%s): %s", exc.category, exc)
        raise HTTPException(
            status_code=exc.http_status,
            detail=str(exc),
        ) from exc

    # Use display name from the request body or fall back to Steam API persona name.
    display_name = body.display_name or player_summary.get("personaname")
    profile_url = player_summary.get("profileurl")
    avatar_url = player_summary.get("avatarfull") or player_summary.get("avatar")

    # Register the account (idempotent duplicate check).
    try:
        account = await create_steam_account(
            pool,
            steam_id=body.steam_id,
            display_name=display_name,
            profile_url=profile_url,
            avatar_url=avatar_url,
            api_key=body.api_key,
        )
    except SteamAccountAlreadyExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Steam account with steam_id={body.steam_id} is already connected. "
                "Use DELETE /api/steam/accounts/{id} to disconnect it first, "
                "or update the API key by disconnecting and reconnecting."
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to create Steam account: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to register the Steam account due to an internal error. "
                "Check the server logs and retry."
            ),
        ) from exc

    logger.info(
        "Steam account connected: steam_id=%s display_name=%r is_primary=%s",
        account.steam_id,
        account.display_name,
        account.is_primary,
    )

    return SteamConnectResponse(
        success=True,
        message=(
            f"Steam account '{display_name or body.steam_id}' connected successfully"
            + (" (set as primary)" if account.is_primary else "")
        ),
        account=_account_to_response(account),
    )


# ---------------------------------------------------------------------------
# GET /api/steam/accounts
# ---------------------------------------------------------------------------


@router.get("/accounts", response_model=SteamAccountListResponse)
async def list_connected_steam_accounts(
    db_manager: Any = Depends(_get_db_manager),
) -> SteamAccountListResponse:
    """List all connected Steam accounts.

    Returns accounts ordered by primary first, then by connected_at ascending.
    API keys are never included in the response.

    Returns an empty list when no accounts are connected.
    Raises HTTP 503 when the database is unavailable.
    """
    pool = _get_shared_pool(db_manager)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Database is unavailable. Ensure the database service is running. "
                "Cannot retrieve Steam accounts."
            ),
        )

    try:
        accounts = await list_steam_accounts(pool)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to list Steam accounts: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to retrieve Steam accounts due to an internal error. "
                "Check the server logs and retry."
            ),
        ) from exc

    return SteamAccountListResponse(
        accounts=[_account_to_response(a) for a in accounts],
    )


# ---------------------------------------------------------------------------
# DELETE /api/steam/accounts/{id}
# ---------------------------------------------------------------------------


@router.delete("/accounts/{account_id}", response_model=SteamDisconnectResponse)
async def disconnect_steam_account(
    account_id: uuid.UUID,
    db_manager: Any = Depends(_get_db_manager),
) -> SteamDisconnectResponse:
    """Disconnect a Steam account by soft-revoking it.

    Sets the account status to 'revoked'. The connector stops polling this
    account on its next discovery cycle. Credentials are retained for audit
    purposes (no hard-delete).

    Raises HTTP 404 when the account ID is not found.
    Raises HTTP 503 when the database is unavailable.
    """
    pool = _get_shared_pool(db_manager)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Database is unavailable. Ensure the database service is running. "
                "Cannot disconnect the Steam account."
            ),
        )

    try:
        await disconnect_account(pool, account_id, hard_delete=False)
    except SteamAccountNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Steam account with id={account_id} was not found. "
                "Verify the account ID via GET /api/steam/accounts."
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to disconnect Steam account %s: %s", account_id, exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to disconnect the Steam account due to an internal error. "
                "Check the server logs and retry."
            ),
        ) from exc

    logger.info("Steam account disconnected (soft-revoke): id=%s", account_id)

    return SteamDisconnectResponse(
        success=True,
        message=f"Steam account {account_id} disconnected (status set to 'revoked')",
    )


# ---------------------------------------------------------------------------
# GET /api/steam/playtime
# ---------------------------------------------------------------------------


@router.get("/playtime", response_model=SteamPlaytimeAnalytics)
async def get_steam_playtime(
    account_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "UUID of the Steam account to fetch analytics for. "
            "If omitted, uses the primary account."
        ),
    ),
    top_n: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Number of top games to return (by total playtime). Default: 10.",
    ),
    db_manager: Any = Depends(_get_db_manager),
) -> SteamPlaytimeAnalytics:
    """Fetch playtime analytics for a Steam account.

    Returns:
    - ``total_games``: number of games in the library
    - ``total_playtime_minutes``: sum of all playtime across games
    - ``top_games``: top N games by total playtime (descending)
    - ``recently_played``: games played in the last 2 weeks

    The ``account_id`` parameter selects a specific account; when omitted,
    the primary account is used.

    Data is fetched live from the Steam API on each request.

    Raises HTTP 404 when the specified account_id is not found.
    Raises HTTP 400 when no Steam account is connected (no primary).
    Raises HTTP 502 on Steam API errors.
    Raises HTTP 503 when the database is unavailable.
    """
    pool = _get_shared_pool(db_manager)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Database is unavailable. Ensure the database service is running. "
                "Cannot fetch Steam playtime analytics."
            ),
        )

    # Resolve the target account.
    try:
        account = await resolve_steam_account(pool, account=account_id)
    except MissingSteamCredentialsError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "No primary Steam account is configured. "
                "Connect a Steam account via POST /api/steam/accounts first."
            ),
        ) from exc
    except SteamAccountNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Steam account with id={account_id} was not found. "
                "Verify the account ID via GET /api/steam/accounts."
            ),
        ) from exc

    # Fetch the API key for this account from entity_info.
    async with pool.acquire() as conn:
        key_row = await conn.fetchrow(
            """
            SELECT value FROM public.entity_info
            WHERE entity_id = $1 AND type = $2
            LIMIT 1
            """,
            account.entity_id,
            _ENTITY_INFO_TYPE_API_KEY,
        )

    if not key_row:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No API key found for Steam account steam_id={account.steam_id}. "
                "Reconnect the account via POST /api/steam/accounts to store the API key."
            ),
        )

    api_key: str = key_row["value"]
    fetched_at = datetime.now(UTC)

    # Fetch owned games + recently played in parallel via the Steam API.
    try:
        async with SteamAPIClient(api_key=api_key) as client:
            owned_data, recent_data = await _fetch_playtime_data(client, account.steam_id)
    except _SteamPlaytimeError as exc:
        logger.warning("Steam playtime fetch failed for steam_id=%s: %s", account.steam_id, exc)
        raise HTTPException(
            status_code=exc.http_status,
            detail=str(exc),
        ) from exc

    # Build the top-games list.
    owned_games = owned_data.get("games", [])
    total_games = owned_data.get("game_count", len(owned_games))

    # Sort all owned games by playtime_forever descending to get top_n.
    sorted_games = sorted(
        owned_games,
        key=lambda g: g.get("playtime_forever", 0),
        reverse=True,
    )

    top_games = [
        SteamGamePlaytime(
            app_id=g["appid"],
            name=g.get("name"),
            playtime_forever_minutes=g.get("playtime_forever", 0),
            playtime_2weeks_minutes=g.get("playtime_2weeks") or None,
            img_icon_url=g.get("img_icon_url") or None,
        )
        for g in sorted_games[:top_n]
    ]

    total_playtime = sum(g.get("playtime_forever", 0) for g in owned_games)

    # Build recently played list (only games with recent playtime > 0).
    recent_games_raw = recent_data.get("games", [])
    recently_played = [
        SteamGamePlaytime(
            app_id=g["appid"],
            name=g.get("name"),
            playtime_forever_minutes=g.get("playtime_forever", 0),
            playtime_2weeks_minutes=g.get("playtime_2weeks") or None,
            img_icon_url=g.get("img_icon_url") or None,
        )
        for g in recent_games_raw
        if g.get("playtime_2weeks", 0) > 0
    ]

    return SteamPlaytimeAnalytics(
        account_id=account.id,
        steam_id=account.steam_id,
        display_name=account.display_name,
        total_games=total_games,
        total_playtime_minutes=total_playtime,
        top_games=top_games,
        recently_played=recently_played,
        fetched_at=fetched_at,
    )


# ---------------------------------------------------------------------------
# Playtime fetch helpers
# ---------------------------------------------------------------------------


class _SteamPlaytimeError(Exception):
    """Raised when Steam playtime fetch fails."""

    def __init__(self, message: str, http_status: int = 502) -> None:
        super().__init__(message)
        self.http_status = http_status


async def _fetch_playtime_data(
    client: SteamAPIClient,
    steam_id: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch owned games and recently played games from Steam in parallel.

    Parameters
    ----------
    client:
        Open SteamAPIClient instance.
    steam_id:
        Steam 64-bit account ID.

    Returns
    -------
    tuple[dict, dict]
        (owned_games_response, recently_played_response)
        Each dict contains a ``games`` list and optionally ``game_count``.

    Raises
    ------
    _SteamPlaytimeError
        On Steam API errors.
    """
    import asyncio

    owned_params = {
        "steamid": str(steam_id),
        "include_appinfo": "1",
        "include_played_free_games": "1",
    }
    recent_params = {
        "steamid": str(steam_id),
        "count": "0",  # 0 = all recently played games
    }

    try:
        owned_task = client.request("IPlayerService", "GetOwnedGames", params=owned_params)
        recent_task = client.request(
            "IPlayerService", "GetRecentlyPlayedGames", params=recent_params
        )
        owned_data, recent_data = await asyncio.gather(owned_task, recent_task)
    except SteamRateLimitError as exc:
        raise _SteamPlaytimeError(
            f"Steam API is rate-limited; retry after {exc.retry_after_s:.0f}s. "
            "Wait before retrying.",
            http_status=429,
        ) from exc
    except SteamAPIError as exc:
        if exc.status_code in (401, 403):
            raise _SteamPlaytimeError(
                f"Steam API returned HTTP {exc.status_code}: access denied. "
                "The profile may be private, or the API key may be invalid. "
                "Reconnect the account via POST /api/steam/accounts.",
                http_status=400,
            ) from exc
        raise _SteamPlaytimeError(
            f"Steam API returned HTTP {exc.status_code}: {exc.body[:200]}. "
            "Check Steam API status at https://steamstat.us/ and retry.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise _SteamPlaytimeError(
            f"Network error contacting Steam API: {exc}. Ensure network connectivity and retry.",
        ) from exc

    return owned_data, recent_data
