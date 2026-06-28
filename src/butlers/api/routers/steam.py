"""Steam account management and playtime analytics endpoints for the dashboard.

Provides ``router`` at ``/api/steam``:

- ``POST   /api/steam/accounts``              — connect a new Steam account (validates API key)
- ``GET    /api/steam/accounts``              — list all connected Steam accounts
- ``DELETE /api/steam/accounts/{id}``         — disconnect a Steam account
  (soft-revoke by default; ``?hard_delete=true`` to permanently purge)
- ``GET    /api/steam/accounts/{id}/status``  — per-account credential + poll health status
- ``GET    /api/steam/connector/health``      — proxy the Steam connector health endpoint
- ``GET    /api/steam/playtime``              — playtime analytics from DB (games list)
- ``GET    /api/steam/playtime/{app_id}``     — per-game playtime history from DB

API key validation:
  The POST endpoint calls ``ISteamUser/GetPlayerSummaries`` with the provided
  API key and steam_id before storing credentials. If the API key is invalid
  (HTTP 403), or the steam_id is not found in the response, a 400 is returned.

Account status (GET /api/steam/accounts/{id}/status):
  Returns ``has_api_key`` (key present in entity_info), ``key_valid`` (result of a
  live Steam Web API test call against the stored key: true = authenticated,
  false = rejected with 401/403, null = no key or transient/network error),
  ``last_poll_at`` (from the steam_accounts table), and ``connector_health``
  (effective health string for this account from the connector's health report,
  null if connector unreachable).

Connector health proxy (GET /api/steam/connector/health):
  Proxies the Steam connector's ``/health`` HTTP endpoint (port configured via
  ``STEAM_CONNECTOR_HEALTH_PORT``, default 40089).  Returns a structured response
  with ``status='not_running'`` when the connector is unreachable.

Playtime analytics:
  Queries ``connectors.steam_play_history`` for aggregated playtime data.
  The ``days`` parameter limits the window (default: all-time). The
  ``account_id`` query parameter selects a specific account (default: primary).

Security notes:
  - API keys are stored in ``public.entity_info`` (secured=true) and never
    returned in any response.
  - Playtime data is served from the local database; no live Steam API calls.
  - The DELETE endpoint soft-revokes (status='revoked') by default; the account
    row and its credentials are retained for audit purposes.  Pass
    ``?hard_delete=true`` to permanently delete the account and its credentials.

Connection validation error categories:
  - ``invalid_api_key``   — Steam returned 403 (bad or unauthorized key)
  - ``steam_id_not_found`` — API call succeeded but the steam_id was not found
  - ``api_error``         — unexpected error from Steam
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.models.steam import (
    SteamAccountListResponse,
    SteamAccountResponse,
    SteamAccountStatusResponse,
    SteamConnectorAccountHealth,
    SteamConnectorDataTypeHealth,
    SteamConnectorHealthResponse,
    SteamConnectRequest,
    SteamConnectResponse,
    SteamDailyPlaytimeSummary,
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

# Default connector health endpoint port (matches _DEFAULT_HEALTH_PORT in steam connector).
_DEFAULT_CONNECTOR_HEALTH_PORT = 40089
_CONNECTOR_HEALTH_TIMEOUT_S = 3.0

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


async def _check_api_key_valid(api_key: str, steam_id: int) -> bool | None:
    """Probe whether a stored Steam Web API key still authenticates.

    Performs a single lightweight ``ISteamUser/GetPlayerSummaries`` test call
    (the same call used at connect time) and maps the outcome to a tri-state:

    - ``True``  — Steam authenticated the key (HTTP 200).
    - ``False`` — Steam rejected the key as invalid/unauthorized (HTTP 401/403).
    - ``None``  — the result is unknown because of a transient/network error or
      rate-limiting. We deliberately do **not** report ``False`` here, since a
      transient failure says nothing about whether the key is valid.

    The API key is never logged or returned; only the boolean verdict escapes.
    """
    try:
        await _validate_steam_credentials(api_key, steam_id)
    except _SteamValidationError as exc:
        if exc.category == "invalid_api_key":
            return False
        if exc.category == "steam_id_not_found":
            # Auth succeeded (HTTP 200); the steam_id lookup merely returned no
            # players. The key itself is valid.
            return True
        # api_error → transient/network/rate-limit; verdict is unknown.
        return None
    return True


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
    hard_delete: bool = Query(
        default=False,
        description=(
            "When true, permanently deletes the account row and its credentials. "
            "When false (default), soft-revokes by setting status to 'revoked' "
            "and retains credentials for audit purposes."
        ),
    ),
    db_manager: Any = Depends(_get_db_manager),
) -> SteamDisconnectResponse:
    """Disconnect a Steam account.

    By default (``?hard_delete=false``) soft-revokes the account: sets the
    status to 'revoked'. The connector stops polling this account on its next
    discovery cycle. Credentials are retained for audit purposes.

    When ``?hard_delete=true``, the account row and companion entity are
    permanently deleted (CASCADE removes credentials from entity_info).

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
        await disconnect_account(pool, account_id, hard_delete=hard_delete)
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

    if hard_delete:
        logger.info("Steam account hard-deleted via API: id=%s", account_id)
        return SteamDisconnectResponse(
            success=True,
            message=f"Steam account {account_id} permanently deleted",
        )

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
# GET /api/steam/accounts/{id}/status
# ---------------------------------------------------------------------------


@router.get("/accounts/{account_id}/status", response_model=SteamAccountStatusResponse)
async def get_steam_account_status(
    account_id: uuid.UUID,
    db_manager: Any = Depends(_get_db_manager),
) -> SteamAccountStatusResponse:
    """Return credential and poll health status for a single Steam account.

    Inspects the account row and its stored API key without making any live
    Steam API call. Also probes the Steam connector health endpoint (if running)
    to report the connector-side health for this account.

    Fields returned:
    - ``has_api_key``      — whether an API key is stored in entity_info
    - ``key_valid``        — result of a live Steam Web API test call against the
                             stored key: True (authenticated), False (Steam
                             rejected it with 401/403), or None (no key stored,
                             or a transient/network error made the result unknown)
    - ``last_poll_at``     — timestamp of the last successful connector poll
    - ``connector_health`` — connector-reported health ('healthy', 'degraded',
                             'error') or null when connector is unreachable

    Raises HTTP 404 when the account ID is not found.
    Raises HTTP 503 when the database is unavailable.
    """
    pool = _get_shared_pool(db_manager)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Database is unavailable. Ensure the database service is running. "
                "Cannot retrieve Steam account status."
            ),
        )

    # Fetch the account row.
    try:
        account = await resolve_steam_account(pool, account=account_id)
    except SteamAccountNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Steam account with id={account_id} was not found. "
                "Verify the account ID via GET /api/steam/accounts."
            ),
        ) from exc

    # Check whether an API key is stored for this account.
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
    has_api_key = key_row is not None

    # Validate the stored key with a real (lightweight) Steam Web API test call.
    # Tri-state: True = key authenticated, False = Steam rejected it (401/403),
    # None = unknown (no key stored, or a transient/network error occurred).
    key_valid: bool | None = None
    if has_api_key:
        key_valid = await _check_api_key_valid(key_row["value"], account.steam_id)

    # Probe the connector health to get per-account health status.
    connector_health: str | None = await _probe_connector_account_health(account.steam_id)

    return SteamAccountStatusResponse(
        id=account.id,
        steam_id=account.steam_id,
        status=account.status,
        has_api_key=has_api_key,
        key_valid=key_valid,
        last_poll_at=account.last_poll_at,
        connector_health=connector_health,
    )


# ---------------------------------------------------------------------------
# GET /api/steam/connector/health
# ---------------------------------------------------------------------------


@router.get("/connector/health", response_model=SteamConnectorHealthResponse)
async def get_steam_connector_health() -> SteamConnectorHealthResponse:
    """Proxy the Steam connector's /health endpoint.

    Fetches the health report from the connector process running on
    ``STEAM_CONNECTOR_HEALTH_PORT`` (default: 40089).  Returns a structured
    response with ``status='not_running'`` when the connector is unreachable
    (e.g. not started, crashing).

    No database access is required for this endpoint.

    Returns HTTP 200 in all cases — callers should inspect ``status`` to
    determine connector health.
    """
    connector_url = _build_connector_health_url()

    raw = await _fetch_connector_health(connector_url)
    if raw is None:
        return SteamConnectorHealthResponse(
            status="not_running",
            connector_url=connector_url,
        )

    # Parse the connector health payload into the response model.
    overall_status = raw.get("status", "unknown")
    uptime_seconds = raw.get("uptime_seconds")
    active_accounts = raw.get("active_accounts")

    account_health_list: list[SteamConnectorAccountHealth] = []
    for acct in raw.get("account_health", []):
        data_types: dict[str, SteamConnectorDataTypeHealth] = {}
        for dt_name, dt_info in acct.get("data_types", {}).items():
            last_poll_raw = dt_info.get("last_poll_at")
            last_poll: datetime | None = None
            if last_poll_raw:
                try:
                    last_poll = datetime.fromisoformat(last_poll_raw)
                except (ValueError, TypeError):
                    logger.warning(
                        "Failed to parse last_poll_at from connector health: %r",
                        last_poll_raw,
                    )
            data_types[dt_name] = SteamConnectorDataTypeHealth(
                status=dt_info.get("status", "unknown"),
                last_poll_at=last_poll,
            )

        account_health_list.append(
            SteamConnectorAccountHealth(
                steam_id=acct.get("steam_id", ""),
                endpoint_identity=acct.get("endpoint_identity", ""),
                status=acct.get("status", "unknown"),
                error=acct.get("error"),
                data_types=data_types,
            )
        )

    return SteamConnectorHealthResponse(
        status=overall_status,
        uptime_seconds=uptime_seconds,
        active_accounts=active_accounts,
        account_health=account_health_list,
        connector_url=connector_url,
    )


# ---------------------------------------------------------------------------
# Connector health helpers
# ---------------------------------------------------------------------------


def _build_connector_health_url() -> str:
    """Return the Steam connector health URL for the configured port.

    Uses ``STEAM_CONNECTOR_HEALTH_PORT`` env var (default: 40089).
    Centralised here to avoid repeating the port-resolution logic in each caller.
    """
    health_port = int(
        os.environ.get("STEAM_CONNECTOR_HEALTH_PORT", str(_DEFAULT_CONNECTOR_HEALTH_PORT))
    )
    return f"http://127.0.0.1:{health_port}/health"


async def _fetch_connector_health(url: str) -> dict[str, Any] | None:
    """Fetch the raw health JSON from the connector HTTP endpoint.

    Returns the parsed dict on success, or None when the connector is
    unreachable (connection refused, timeout, or unexpected response).
    """
    try:
        async with httpx.AsyncClient(timeout=_CONNECTOR_HEALTH_TIMEOUT_S) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
    except Exception:  # noqa: BLE001
        logger.debug("Steam connector health probe failed at %s", url, exc_info=True)
        return None


async def _probe_connector_account_health(steam_id: int) -> str | None:
    """Probe the connector health endpoint and return health for a specific steam_id.

    The connector redacts steam IDs in the health report (last 4 digits), so
    matching is done by the suffix of the steam_id string representation.

    Returns the effective health string for the account, or None when the
    connector is unreachable or the account is not tracked.
    """
    connector_url = _build_connector_health_url()

    raw = await _fetch_connector_health(connector_url)
    if raw is None:
        return None

    steam_id_str = str(steam_id)
    for acct in raw.get("account_health", []):
        # The connector redacts steam IDs to "****<last4>". Match by suffix.
        reported_id: str = acct.get("steam_id", "")
        if reported_id.endswith(steam_id_str[-4:]):
            return acct.get("status")

    return None


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
    - ``total_minutes``: sum of all playtime in the window
    - ``games``: top N games by total playtime (descending)
    - ``daily``: per-day rollup of total playtime across all games (ascending date)
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
        daily_rows = await _query_daily_playtime_totals(pool, account_id=account.id, days=days)
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

    games = [
        SteamGamePlaytime(
            app_id=r["app_id"],
            app_name=r["app_name"],
            total_minutes=r["total_playtime"],
        )
        for r in rows_sorted[:top_n]
    ]

    total_games = len(rows_sorted)
    total_playtime = sum(r["total_playtime"] for r in rows_sorted)

    daily = [
        SteamDailyPlaytimeSummary(
            date=r["date"],
            total_minutes=r["total_minutes"],
        )
        for r in daily_rows
    ]

    return SteamPlaytimeAnalytics(
        account_id=account.id,
        steam_id=account.steam_id,
        display_name=account.display_name,
        days=days,
        total_games=total_games,
        total_minutes=total_playtime,
        games=games,
        daily=daily,
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
        total_minutes=total_playtime,
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


async def _query_daily_playtime_totals(
    pool: Any,
    *,
    account_id: uuid.UUID,
    days: int | None,
) -> list[dict[str, Any]]:
    """Query connectors.steam_play_history for per-day playtime totals across all games.

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
    list of dicts with keys: date, total_minutes
        Ordered by date ascending.
    """
    if days is not None:
        sql = """
            SELECT date,
                   SUM(playtime_minutes) AS total_minutes
            FROM connectors.steam_play_history
            WHERE steam_account_id = $1
              AND date >= CURRENT_DATE - ($2::int - 1)
            GROUP BY date
            ORDER BY date ASC
        """
        async with pool.acquire() as conn:
            records = await conn.fetch(sql, account_id, days)
    else:
        sql = """
            SELECT date,
                   SUM(playtime_minutes) AS total_minutes
            FROM connectors.steam_play_history
            WHERE steam_account_id = $1
            GROUP BY date
            ORDER BY date ASC
        """
        async with pool.acquire() as conn:
            records = await conn.fetch(sql, account_id)

    return [
        {
            "date": r["date"],
            "total_minutes": r["total_minutes"] or 0,
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
