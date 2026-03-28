"""Steam account management and playtime analytics endpoints for the dashboard.

Provides ``router`` at ``/api/steam``:

- ``POST   /api/steam/accounts``              — connect a new Steam account (validates API key)
- ``GET    /api/steam/accounts``              — list all connected Steam accounts
- ``DELETE /api/steam/accounts/{id}``         — disconnect a Steam account (soft-revoke)
- ``GET    /api/steam/playtime``              — playtime analytics from DB (top games)
- ``GET    /api/steam/playtime/{app_id}``     — per-game playtime history from DB

API key validation:
  The POST endpoint calls ``ISteamUser/GetPlayerSummaries`` with the provided
  API key and steam_id before storing credentials. If the API key is invalid
  (HTTP 403), or the steam_id is not found in the response, a 400 is returned.

Playtime analytics:
  Queries ``connectors.steam_play_history`` for aggregated playtime data.
  The ``days`` parameter limits the window (default: all-time). The
  ``account_id`` query parameter selects a specific account (default: primary).

Security notes:
  - API keys are stored in ``public.entity_info`` (secured=true) and never
    returned in any response.
  - Playtime data is served from the local database; no live Steam API calls.
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
    SteamGamePlaytimeHistory,
    SteamGamePlaytimeHistoryEntry,
    SteamPlaytimeAnalytics,
    SteamSetPrimaryResponse,
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
    set_primary_account,
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
        # HTTP 403 means invalid/unauthorized key; HTTP 429 means genuine rate-limiting.
        # SteamAPIClient raises SteamRateLimitError for both (see client._RATE_LIMIT_STATUSES).
        if exc.status_code == 403:
            raise _SteamValidationError(
                "Steam API key is invalid or unauthorized (HTTP 403). "
                "Check your Steam Web API key at https://steamcommunity.com/dev/apikey",
                category="invalid_api_key",
                http_status=400,
            ) from exc
        raise _SteamValidationError(
            f"Steam API is rate-limited; retry after {exc.retry_after_s:.0f}s. "
            "Wait before connecting the account.",
            category="api_error",
            http_status=429,
        ) from exc
    except SteamAPIError as exc:
        if exc.status_code == 401:
            raise _SteamValidationError(
                "Steam API key is invalid or unauthorized (HTTP 401). "
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
# PUT /api/steam/accounts/{id}/primary
# ---------------------------------------------------------------------------


@router.put("/accounts/{account_id}/primary", response_model=SteamSetPrimaryResponse)
async def set_primary_steam_account(
    account_id: uuid.UUID,
    db_manager: Any = Depends(_get_db_manager),
) -> SteamSetPrimaryResponse:
    """Set a Steam account as the primary account.

    Atomically clears the current primary (if any) and sets the specified
    account as primary within a single database transaction.  The partial
    unique index on ``public.steam_accounts`` enforces the singleton constraint
    at the DB level.

    Raises HTTP 404 when the account ID is not found.
    Raises HTTP 503 when the database is unavailable.
    """
    pool = _get_shared_pool(db_manager)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Database is unavailable. Ensure the database service is running. "
                "Cannot update the primary Steam account."
            ),
        )

    try:
        account = await set_primary_account(pool, account_id)
    except SteamAccountNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Steam account with id={account_id} was not found. "
                "Verify the account ID via GET /api/steam/accounts."
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to set primary Steam account %s", account_id, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to update the primary Steam account due to an internal error. "
                "Check the server logs and retry."
            ),
        ) from exc

    logger.info("Primary Steam account updated: id=%s steam_id=%s", account.id, account.steam_id)

    return SteamSetPrimaryResponse(
        success=True,
        message=f"Steam account '{account.display_name or account.steam_id}' set as primary",
        account=_account_to_response(account),
    )


# ---------------------------------------------------------------------------
# GET /api/steam/playtime
# ---------------------------------------------------------------------------

# Default playtime query window (days). 0 / None means all-time.
_DEFAULT_PLAYTIME_DAYS = 30
_MAX_PLAYTIME_DAYS = 3650  # ~10 years


@router.get("/playtime", response_model=SteamPlaytimeAnalytics)
async def get_steam_playtime(
    account_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "UUID of the Steam account to fetch analytics for. "
            "If omitted, uses the primary account."
        ),
    ),
    days: int | None = Query(
        default=_DEFAULT_PLAYTIME_DAYS,
        ge=1,
        le=_MAX_PLAYTIME_DAYS,
        description=(
            "Number of past days to include in the playtime window. "
            f"Default: {_DEFAULT_PLAYTIME_DAYS}. Set to null for all-time."
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
    """Fetch playtime analytics for a Steam account from the local database.

    Queries ``connectors.steam_play_history`` and returns aggregated playtime
    statistics for games played within the requested window.

    Returns:
    - ``total_games``: number of distinct games with playtime in the window
    - ``total_playtime_minutes``: sum of all playtime in the window
    - ``top_games``: top N games by total playtime (descending)
    - ``days``: the window size used (null = all-time)

    The ``account_id`` parameter selects a specific account; when omitted,
    the primary account is used.

    Raises HTTP 404 when the specified account_id is not found.
    Raises HTTP 400 when no Steam account is connected (no primary).
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

    queried_at = datetime.now(UTC)

    try:
        rows = await _query_playtime_aggregates(pool, account_id=account.id, days=days)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to query play history for steam_id=%s: %s", account.steam_id, exc, exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to fetch playtime data due to an internal error. "
                "Check the server logs and retry."
            ),
        ) from exc

    # Sort by total playtime descending.
    rows_sorted = sorted(rows, key=lambda r: r["total_playtime"], reverse=True)

    top_games = [
        SteamGamePlaytime(
            app_id=r["app_id"],
            name=r["app_name"],
            playtime_minutes=r["total_playtime"],
        )
        for r in rows_sorted[:top_n]
    ]

    total_games = len(rows_sorted)
    total_playtime = sum(r["total_playtime"] for r in rows_sorted)

    return SteamPlaytimeAnalytics(
        account_id=account.id,
        steam_id=account.steam_id,
        display_name=account.display_name,
        days=days,
        total_games=total_games,
        total_playtime_minutes=total_playtime,
        top_games=top_games,
        queried_at=queried_at,
    )


# ---------------------------------------------------------------------------
# GET /api/steam/playtime/{app_id}
# ---------------------------------------------------------------------------


@router.get("/playtime/{app_id}", response_model=SteamGamePlaytimeHistory)
async def get_steam_game_playtime(
    app_id: int,
    account_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "UUID of the Steam account to fetch history for. If omitted, uses the primary account."
        ),
    ),
    days: int | None = Query(
        default=_DEFAULT_PLAYTIME_DAYS,
        ge=1,
        le=_MAX_PLAYTIME_DAYS,
        description=(
            "Number of past days to include in the history window. "
            f"Default: {_DEFAULT_PLAYTIME_DAYS}. Set to null for all-time."
        ),
    ),
    db_manager: Any = Depends(_get_db_manager),
) -> SteamGamePlaytimeHistory:
    """Fetch per-game playtime history from the local database.

    Returns individual ``connectors.steam_play_history`` rows for the
    specified ``app_id``, ordered by date descending.

    Raises HTTP 404 when the specified account_id is not found, or when the
    game has no recorded playtime in the requested window.
    Raises HTTP 400 when no Steam account is connected (no primary).
    Raises HTTP 503 when the database is unavailable.
    """
    pool = _get_shared_pool(db_manager)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Database is unavailable. Ensure the database service is running. "
                "Cannot fetch Steam game playtime history."
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

    queried_at = datetime.now(UTC)

    try:
        rows = await _query_game_play_history(pool, account_id=account.id, app_id=app_id, days=days)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to query play history for steam_id=%s app_id=%s: %s",
            account.steam_id,
            app_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to fetch game playtime history due to an internal error. "
                "Check the server logs and retry."
            ),
        ) from exc

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No playtime history found for app_id={app_id} "
                f"and account_id={account.id} in the requested window. "
                "Ensure the connector has polled this account and the game has been played."
            ),
        )

    # Derive app_name from the most recent non-null entry.
    app_name: str | None = next((r["app_name"] for r in rows if r["app_name"]), None)

    history = [
        SteamGamePlaytimeHistoryEntry(
            date=r["date"],
            playtime_minutes=r["playtime_minutes"],
            recorded_at=r["recorded_at"],
        )
        for r in rows
    ]

    total_playtime = sum(r["playtime_minutes"] for r in rows)

    return SteamGamePlaytimeHistory(
        account_id=account.id,
        steam_id=account.steam_id,
        display_name=account.display_name,
        app_id=app_id,
        app_name=app_name,
        days=days,
        total_playtime_minutes=total_playtime,
        history=history,
        queried_at=queried_at,
    )


# ---------------------------------------------------------------------------
# DB query helpers
# ---------------------------------------------------------------------------


async def _query_playtime_aggregates(
    pool: Any,
    *,
    account_id: uuid.UUID,
    days: int | None,
) -> list[dict[str, Any]]:
    """Query connectors.steam_play_history for per-game playtime aggregates.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    account_id:
        UUID of the steam_accounts row.
    days:
        Number of past days to include, or None for all-time.

    Returns
    -------
    list of dicts with keys: app_id, app_name, total_playtime
    """
    if days is not None:
        sql = """
            SELECT app_id,
                   MAX(app_name) AS app_name,
                   SUM(playtime_minutes) AS total_playtime
            FROM connectors.steam_play_history
            WHERE steam_account_id = $1
              AND date >= CURRENT_DATE - ($2::int - 1)
            GROUP BY app_id
        """
        async with pool.acquire() as conn:
            records = await conn.fetch(sql, account_id, days)
    else:
        sql = """
            SELECT app_id,
                   MAX(app_name) AS app_name,
                   SUM(playtime_minutes) AS total_playtime
            FROM connectors.steam_play_history
            WHERE steam_account_id = $1
            GROUP BY app_id
        """
        async with pool.acquire() as conn:
            records = await conn.fetch(sql, account_id)

    return [
        {
            "app_id": r["app_id"],
            "app_name": r["app_name"],
            "total_playtime": r["total_playtime"] or 0,
        }
        for r in records
    ]


async def _query_game_play_history(
    pool: Any,
    *,
    account_id: uuid.UUID,
    app_id: int,
    days: int | None,
) -> list[dict[str, Any]]:
    """Query connectors.steam_play_history for individual rows of a specific game.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    account_id:
        UUID of the steam_accounts row.
    app_id:
        Steam application ID.
    days:
        Number of past days to include, or None for all-time.

    Returns
    -------
    list of dicts with keys: date, playtime_minutes, app_name, recorded_at
        Ordered by date descending.
    """
    if days is not None:
        sql = """
            SELECT date, playtime_minutes, app_name, recorded_at
            FROM connectors.steam_play_history
            WHERE steam_account_id = $1
              AND app_id = $2
              AND date >= CURRENT_DATE - ($3::int - 1)
            ORDER BY date DESC
        """
        async with pool.acquire() as conn:
            records = await conn.fetch(sql, account_id, app_id, days)
    else:
        sql = """
            SELECT date, playtime_minutes, app_name, recorded_at
            FROM connectors.steam_play_history
            WHERE steam_account_id = $1
              AND app_id = $2
            ORDER BY date DESC
        """
        async with pool.acquire() as conn:
            records = await conn.fetch(sql, account_id, app_id)

    return [
        {
            "date": r["date"],
            "playtime_minutes": r["playtime_minutes"],
            "app_name": r["app_name"],
            "recorded_at": r["recorded_at"],
        }
        for r in records
    ]
