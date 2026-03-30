"""Home Assistant connection settings endpoints for the dashboard.

Provides ``router`` at ``/api/settings/home-assistant``:

- ``GET    /api/settings/home-assistant``  — retrieve current HA connection status
- ``POST   /api/settings/home-assistant``  — validate and save HA URL + access token
- ``DELETE /api/settings/home-assistant``  — remove stored HA credentials

The POST endpoint validates the connection by testing ``GET /api/`` against the
provided HA URL with the bearer token before storing credentials.

Connection validation error categories:
- ``unreachable``    — network error or timeout contacting HA
- ``auth_failure``   — HA returned HTTP 401/403 (bad token)
- ``unexpected``     — HA returned an unexpected HTTP status code

Security notes:
  - Access tokens are stored with ``is_sensitive=True`` and never echoed back.
  - The GET status endpoint only returns the base URL origin (masked), not the full URL.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException

from butlers.api.models.home_assistant import (
    HAConfigRequest,
    HAConfigResponse,
    HAConnectionState,
    HADeleteResponse,
    HAStatusResponse,
)
from butlers.credential_store import (
    delete_owner_entity_info,
    resolve_owner_entity_info,
    upsert_owner_entity_info,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings/home-assistant", tags=["home-assistant-settings"])

# ---------------------------------------------------------------------------
# Credential key constants
# ---------------------------------------------------------------------------

_EI_HA_URL = "home_assistant_url"
_EI_HA_TOKEN = "home_assistant_token"

_ALL_EI_TYPES = (_EI_HA_URL, _EI_HA_TOKEN)

# ---------------------------------------------------------------------------
# Dependency injection stub
# ---------------------------------------------------------------------------


def _get_db_manager() -> Any:
    """Stub replaced at startup by wire_db_dependencies().

    When not wired (e.g. in tests that don't boot the full app), returns None
    so endpoints degrade gracefully.
    """
    return None


def _resolve_pool(db_manager: Any):
    """Resolve an asyncpg pool from the DatabaseManager.

    Returns None when db_manager is None or no usable pool can be resolved.
    Resolution order:
    1. Dedicated shared credential pool from DatabaseManager.
    2. Compatibility fallback to first butler pool.
    """
    if db_manager is None:
        return None

    try:
        return db_manager.credential_shared_pool()
    except Exception:  # noqa: BLE001 — pool API is dynamic; exception type is unknown
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
        except Exception:  # noqa: BLE001 — pool API is dynamic; exception type is unknown
            logger.debug(
                "Failed to obtain fallback DB pool; pool unavailable.", exc_info=True
            )
            return None


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _mask_url(url: str) -> str:
    """Return only the base origin (scheme + host + port) of a URL.

    Strips path, query, and fragment to avoid leaking sensitive URL components.

    Examples
    --------
    >>> _mask_url("http://homeassistant.local:8123/api/states?x=1")
    'http://homeassistant.local:8123'
    """
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin


# ---------------------------------------------------------------------------
# Connection validation
# ---------------------------------------------------------------------------


class _HAValidationError(Exception):
    """Raised when HA connection validation fails."""

    def __init__(self, message: str, category: str) -> None:
        super().__init__(message)
        self.category = category
        """One of: 'unreachable', 'auth_failure', 'unexpected'."""


async def _validate_ha_connection(url: str, token: str) -> None:
    """Test the HA connection by calling GET /api/ with the bearer token.

    Parameters
    ----------
    url:
        Home Assistant base URL (e.g. ``http://homeassistant.local:8123``).
    token:
        Long-lived access token.

    Raises
    ------
    _HAValidationError
        When the connection cannot be validated. The ``category`` attribute
        describes the failure type: ``'unreachable'``, ``'auth_failure'``,
        or ``'unexpected'``.
    """
    base = url.rstrip("/")
    probe_url = f"{base}/api/"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                probe_url,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.TimeoutException as exc:
        raise _HAValidationError(
            f"Connection to Home Assistant timed out: {exc}",
            category="unreachable",
        ) from exc
    except httpx.RequestError as exc:
        raise _HAValidationError(
            f"Could not reach Home Assistant at {url}: {exc}",
            category="unreachable",
        ) from exc

    if resp.status_code in (401, 403):
        raise _HAValidationError(
            f"Authentication failed (HTTP {resp.status_code}). "
            "Check that the long-lived access token is valid.",
            category="auth_failure",
        )

    if resp.status_code != 200:
        raise _HAValidationError(
            f"Unexpected response from Home Assistant (HTTP {resp.status_code}). "
            f"Expected 200 from GET /api/.",
            category="unexpected",
        )

    logger.debug("HA connection validated: GET %s returned HTTP 200", probe_url)


# ---------------------------------------------------------------------------
# GET /api/settings/home-assistant
# ---------------------------------------------------------------------------


@router.get("", response_model=HAStatusResponse)
async def get_ha_status(
    db_manager: Any = Depends(_get_db_manager),
) -> HAStatusResponse:
    """Return the current Home Assistant connection state.

    Checks stored credentials in CredentialStore. Returns the connection
    state and a masked URL (base origin only) without making any network call.

    Returns ``not_configured`` when no URL or token has been stored,
    or when the credential store is unavailable.
    Returns ``connected`` when both URL and token are present in the store
    (credentials were validated on the last successful POST).

    Note: This endpoint does not perform a live connectivity check. The
    ``connected`` state reflects that credentials are stored and were
    validated at configuration time, not that HA is reachable right now.
    """
    pool = _resolve_pool(db_manager)
    if pool is None:
        return HAStatusResponse(
            state=HAConnectionState.not_configured,
            url_configured=False,
            token_configured=False,
        )

    ha_url = await resolve_owner_entity_info(pool, _EI_HA_URL)
    ha_token = await resolve_owner_entity_info(pool, _EI_HA_TOKEN)

    url_configured = bool(ha_url)
    token_configured = bool(ha_token)

    if not url_configured or not token_configured:
        return HAStatusResponse(
            state=HAConnectionState.not_configured,
            url_configured=url_configured,
            token_configured=token_configured,
        )

    masked_url = _mask_url(ha_url)  # type: ignore[arg-type]
    return HAStatusResponse(
        state=HAConnectionState.connected,
        url_configured=True,
        token_configured=True,
        masked_url=masked_url,
    )


# ---------------------------------------------------------------------------
# POST /api/settings/home-assistant
# ---------------------------------------------------------------------------


@router.post("", response_model=HAConfigResponse)
async def configure_ha(
    body: HAConfigRequest,
    db_manager: Any = Depends(_get_db_manager),
) -> HAConfigResponse:
    """Validate and store Home Assistant URL + access token.

    Validates the connection by issuing ``GET /api/`` against the provided
    HA URL with the bearer token before persisting credentials. Returns
    specific error messages for unreachable / auth-failure / unexpected errors.

    Raises HTTP 503 when the credential database is unavailable.
    Raises HTTP 502 when HA cannot be reached or authentication fails.
    """
    pool = _resolve_pool(db_manager)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail=("Credential database is unavailable. Ensure the database service is running."),
        )

    # Validate connection before storing credentials
    try:
        await _validate_ha_connection(body.url, body.token)
    except _HAValidationError as exc:
        logger.warning("HA connection validation failed (category=%s): %s", exc.category, exc)
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    # Persist validated credentials to entity_info
    await upsert_owner_entity_info(pool, _EI_HA_URL, body.url, secured=False)
    await upsert_owner_entity_info(pool, _EI_HA_TOKEN, body.token, secured=True)

    masked_url = _mask_url(body.url)
    logger.info("Home Assistant credentials stored (url=%s)", masked_url)

    return HAConfigResponse(
        success=True,
        message="Home Assistant connection validated and credentials saved",
        masked_url=masked_url,
    )


# ---------------------------------------------------------------------------
# DELETE /api/settings/home-assistant
# ---------------------------------------------------------------------------


@router.delete("", response_model=HADeleteResponse)
async def delete_ha_config(
    db_manager: Any = Depends(_get_db_manager),
) -> HADeleteResponse:
    """Remove stored Home Assistant credentials from entity_info.

    Deletes both ``home_assistant_url`` and ``home_assistant_token``.
    Returns success=True even when no credentials were stored (idempotent).
    """
    pool = _resolve_pool(db_manager)
    if pool is None:
        # No DB — nothing to delete; treat as success
        logger.info("HA delete requested but credential store is unavailable; treating as success")
        return HADeleteResponse(
            success=True,
            message="Home Assistant credentials removed (credential store was unavailable)",
        )

    deleted_count = 0
    for ei_type in _ALL_EI_TYPES:
        if await delete_owner_entity_info(pool, ei_type):
            deleted_count += 1

    logger.info("Home Assistant credentials deleted: %d key(s) removed", deleted_count)
    return HADeleteResponse(
        success=True,
        message=f"Home Assistant credentials removed ({deleted_count} credential(s) deleted)",
    )
