"""Google OAuth bootstrap endpoints.

Implements a two-leg OAuth 2.0 authorization-code flow for acquiring
Google OAuth refresh tokens for use by butler modules (Gmail connector,
Calendar module, etc.).

The bootstrap flow:
  1. GET /api/oauth/google/start
     - Generates a cryptographically random state token (CSRF protection).
     - Stores the state in an in-memory store (keyed by state value, TTL 10 min).
     - Returns the Google authorization URL as a redirect response.
     - Optional: account_hint passes login_hint to Google; force_consent adds prompt=consent.

  2. GET /api/oauth/google/callback
     - Validates the state parameter against the stored state token.
     - Exchanges the authorization code for tokens via Google's token endpoint.
     - Calls Google's userinfo endpoint to resolve the authenticated email.
     - Resolves or creates a google_accounts row via the registry.
     - Stores the refresh token on the companion entity.
     - Redirects to the dashboard URL on success (if OAUTH_DASHBOARD_URL is set),
       or returns a JSON success payload.

  3. GET /api/oauth/status
     - Reports whether Google credentials are present and usable.
     - Returns a machine-readable state (OAuthCredentialState) plus actionable
       remediation guidance for the dashboard UX.
     - Includes per-account status array when multi-account is configured.

  4. Account management endpoints (GET/PUT/DELETE /api/oauth/google/accounts/*)
     - List, set primary, disconnect, and check per-account credential status.

Environment variables:
  GOOGLE_OAUTH_REDIRECT_URI  — Callback URL registered with Google
                               (default: http://localhost:40200/api/oauth/google/callback)
  OAUTH_DASHBOARD_URL        — Where to redirect after a successful bootstrap
                               (default: not set; returns JSON payload instead)

Security notes:
  - State tokens are one-time-use: consumed on first callback validation.
  - State store entries expire after 10 minutes.
  - Client secrets are never echoed back in responses.
  - Error messages are sanitized to avoid leaking OAuth provider details.
  - The status endpoint never returns raw token values.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse, Response

from butlers.api.models.oauth import (
    DeleteCredentialsResponse,
    DisconnectAccountResponse,
    GoogleAccountResponse,
    GoogleAccountStatus,
    GoogleCredentialStatusResponse,
    OAuthCallbackError,
    OAuthCallbackSuccess,
    OAuthCredentialState,
    OAuthCredentialStatus,
    OAuthStartResponse,
    OAuthStatusResponse,
    SetPrimaryResponse,
    UpsertAppCredentialsRequest,
    UpsertAppCredentialsResponse,
)
from butlers.credential_store import CredentialStore
from butlers.google_account_registry import (
    GoogleAccountAlreadyExistsError,
    GoogleAccountLimitExceededError,
    GoogleAccountNotFoundError,
    create_google_account,
    disconnect_account,
    get_google_account,
    list_google_accounts,
    set_primary_account,
)
from butlers.google_credentials import (
    delete_google_credentials,
    load_app_credentials,
    store_app_credentials,
    store_google_credentials,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional DB manager dependency for credential persistence
# ---------------------------------------------------------------------------


def _get_db_manager() -> Any:
    """Stub replaced at startup by wire_db_dependencies().

    When not wired (e.g. in tests that don't boot the full app), returns None
    so the callback degrades gracefully to log-only mode.
    """
    return None


def _make_credential_store(db_manager: Any) -> CredentialStore | None:
    """Build a CredentialStore from the shared credential pool.

    Returns None when db_manager is None or no usable pool can be resolved.
    Resolution order:
    1. Dedicated shared credential pool from DatabaseManager.
    2. Compatibility fallback to first butler pool.
    """
    if db_manager is None:
        return None

    try:
        pool = db_manager.credential_shared_pool()
    except Exception:
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
        except Exception:
            logger.debug("Failed to obtain fallback DB pool; credential store unavailable.")
            return None

    return CredentialStore(pool)


def _get_shared_pool(db_manager: Any) -> Any:
    """Extract the shared credential pool from a DatabaseManager.

    Returns None when db_manager is None or no pool can be resolved.
    """
    if db_manager is None:
        return None
    try:
        return db_manager.credential_shared_pool()
    except Exception:
        return None


router = APIRouter(prefix="/api/oauth", tags=["oauth"])

# ---------------------------------------------------------------------------
# Google OAuth constants
# ---------------------------------------------------------------------------

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TOKEN_INFO_URL = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

_DEFAULT_REDIRECT_URI = "http://localhost:40200/api/oauth/google/callback"
_DEFAULT_SCOPES = " ".join(
    [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/contacts",
        "https://www.googleapis.com/auth/contacts.readonly",
        "https://www.googleapis.com/auth/contacts.other.readonly",
        "https://www.googleapis.com/auth/directory.readonly",
    ]
)

# Required scopes for full butler functionality.
_REQUIRED_SCOPES = frozenset(
    [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar",
    ]
)

# ---------------------------------------------------------------------------
# In-memory CSRF state store
# State entries expire after 10 minutes.
# ---------------------------------------------------------------------------

_STATE_TTL_SECONDS = 600  # 10 minutes


@dataclass
class _StateEntry:
    """CSRF state store entry carrying account context."""

    expiry: float
    """Monotonic clock timestamp when this entry expires."""

    account_hint: str | None = None
    """Optional Google account hint (email) passed via login_hint."""

    force_consent: bool = False
    """When True, prompt=consent was added to the authorization URL."""


# Maps state token → _StateEntry
# NOTE: This store is process-local. Do not run multiple worker processes
# (e.g. gunicorn -w N) — CSRF state validation will silently fail across workers.
_state_store: dict[str, _StateEntry] = {}


def _generate_state() -> str:
    """Generate a cryptographically random CSRF state token."""
    return secrets.token_urlsafe(32)


def _store_state(
    state: str,
    *,
    account_hint: str | None = None,
    force_consent: bool = False,
) -> None:
    """Store a state token with an expiry timestamp and optional account context."""
    _state_store[state] = _StateEntry(
        expiry=time.monotonic() + _STATE_TTL_SECONDS,
        account_hint=account_hint,
        force_consent=force_consent,
    )
    _evict_expired_states()


def _validate_and_consume_state(state: str) -> _StateEntry | None:
    """Validate a state token and consume it (one-time-use).

    Returns the _StateEntry if the state was valid and unexpired, None otherwise.
    """
    _evict_expired_states()
    entry = _state_store.pop(state, None)
    if entry is None:
        return None
    if time.monotonic() >= entry.expiry:
        return None
    return entry


def _evict_expired_states() -> None:
    """Remove all expired state tokens from the store."""
    now = time.monotonic()
    expired = [k for k, entry in _state_store.items() if now >= entry.expiry]
    for k in expired:
        del _state_store[k]


def _clear_state_store() -> None:
    """Clear all state entries. Used in tests."""
    _state_store.clear()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _get_redirect_uri() -> str:
    """Read GOOGLE_OAUTH_REDIRECT_URI or use the default."""
    return os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", _DEFAULT_REDIRECT_URI).strip()


def _get_scopes() -> str:
    """Return the fixed OAuth scope set required by Butler integrations."""
    return _DEFAULT_SCOPES


def _get_dashboard_url() -> str | None:
    """Read OAUTH_DASHBOARD_URL; returns None if not set."""
    val = os.environ.get("OAUTH_DASHBOARD_URL", "").strip()
    return val or None


async def _resolve_app_credentials(db_manager: Any = None) -> tuple[str, str]:
    """Resolve client_id and client_secret from DB-backed secret storage.

    Returns (client_id, client_secret). Raises HTTPException(503) when the
    shared credential store is unavailable or app credentials are missing.
    """
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        raise HTTPException(
            status_code=503,
            detail="Shared credential database is unavailable.",
        )

    app_creds = await load_app_credentials(cred_store)
    if app_creds is None or not app_creds.client_id or not app_creds.client_secret:
        raise HTTPException(
            status_code=503,
            detail=(
                "Google OAuth app credentials are not configured in DB. "
                "Configure client_id and client_secret on the Secrets page."
            ),
        )
    return app_creds.client_id, app_creds.client_secret


# ---------------------------------------------------------------------------
# Start endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/google/start",
    responses={
        200: {"model": OAuthStartResponse, "description": "JSON payload (redirect=false)"},
        302: {"description": "Redirect to Google authorization URL"},
        409: {"description": "Account limit reached"},
    },
)
async def oauth_google_start(
    redirect: bool = Query(
        default=True,
        description="If true (default), redirect to Google authorization URL. "
        "If false, return the URL as JSON for programmatic callers.",
    ),
    account_hint: str | None = Query(
        default=None,
        description="Optional Google account email to pre-select via login_hint. "
        "When provided, the hint is carried through the CSRF state token to the callback.",
    ),
    force_consent: bool = Query(
        default=False,
        description="When true, adds prompt=consent to the authorization URL to force "
        "Google to return a new refresh token (useful for scope upgrades or re-authorization).",
    ),
    db_manager: Any = Depends(_get_db_manager),
) -> Response:
    """Begin the Google OAuth authorization flow.

    Generates a CSRF state token, stores it in the in-memory state store,
    builds the Google authorization URL, and either redirects the browser
    or returns the URL as JSON (when ``?redirect=false``).

    Supports multi-account flows via ``account_hint`` (pre-selects account)
    and ``force_consent`` (forces refresh token re-issuance for scope upgrades).
    """
    # --- Account limit check ---
    # Only check if this would be a new account (not a re-auth of an existing one).
    shared_pool = _get_shared_pool(db_manager)
    if shared_pool is not None and account_hint:
        # Check if this email already exists — if it does, it's a re-auth, skip limit check.
        try:
            await get_google_account(shared_pool, account=account_hint)
            # Account exists — re-auth, no limit check needed.
        except GoogleAccountNotFoundError:
            # New account — check the limit.
            try:
                await _check_account_limit(shared_pool)
            except GoogleAccountLimitExceededError as exc:
                from butlers.google_account_registry import _max_accounts  # noqa: PLC0415

                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "account_limit_reached",
                        "max_accounts": _max_accounts(),
                        "message": str(exc),
                    },
                )
        except Exception:  # noqa: BLE001
            pass  # DB unavailable — proceed without limit check
    elif shared_pool is not None and not account_hint:
        # No hint provided — could be a new account. Check the limit.
        try:
            await _check_account_limit(shared_pool)
        except GoogleAccountLimitExceededError as exc:
            from butlers.google_account_registry import _max_accounts  # noqa: PLC0415

            return JSONResponse(
                status_code=409,
                content={
                    "error": "account_limit_reached",
                    "max_accounts": _max_accounts(),
                    "message": str(exc),
                },
            )
        except Exception:  # noqa: BLE001
            pass  # DB unavailable — proceed without limit check

    client_id, _ = await _resolve_app_credentials(db_manager)
    redirect_uri = _get_redirect_uri()
    scopes = _get_scopes()

    state = _generate_state()
    _store_state(state, account_hint=account_hint, force_consent=force_consent)

    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "access_type": "offline",
        "state": state,
    }

    # Add prompt=consent when explicitly requested (scope upgrades, forced re-auth).
    # When force_consent=False (default), omit the prompt parameter so Google decides
    # whether to show the consent screen (skips it for re-auths without scope changes).
    if force_consent:
        params["prompt"] = "consent"

    if account_hint:
        params["login_hint"] = account_hint

    authorization_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    logger.info(
        "Google OAuth flow started (state=%s..., account_hint=%s, force_consent=%s)",
        state[:8],
        account_hint,
        force_consent,
    )

    if redirect:
        return RedirectResponse(url=authorization_url, status_code=302)

    return JSONResponse(
        content=OAuthStartResponse(
            authorization_url=authorization_url,
            state=state,
        ).model_dump()
    )


async def _check_account_limit(pool: Any) -> None:
    """Check the active account count against the soft limit.

    Raises GoogleAccountLimitExceededError if the limit is reached.
    """
    from butlers.google_account_registry import (  # noqa: PLC0415
        _count_active_accounts,
        _max_accounts,
    )

    async with pool.acquire() as conn:
        active_count = await _count_active_accounts(conn)
        if active_count >= _max_accounts():
            raise GoogleAccountLimitExceededError(
                f"Google account limit reached ({active_count}/{_max_accounts()}). "
                "Disconnect an existing account before adding a new one, or raise "
                "GOOGLE_MAX_ACCOUNTS."
            )


# ---------------------------------------------------------------------------
# Callback endpoint
# ---------------------------------------------------------------------------


@router.get("/google/callback")
async def oauth_google_callback(
    code: str | None = Query(default=None, description="Authorization code from Google."),
    state: str | None = Query(default=None, description="CSRF state token."),
    error: str | None = Query(default=None, description="OAuth error code from Google."),
    error_description: str | None = Query(
        default=None, description="Human-readable error from Google."
    ),
    db_manager: Any = Depends(_get_db_manager),
) -> Response:
    """Handle the Google OAuth callback after user authorization.

    Validates state, exchanges the authorization code for tokens, calls
    Google's userinfo endpoint to resolve the authenticated account,
    and resolves or creates a google_accounts row via the registry.

    On success:
        - Returns ``OAuthCallbackSuccess`` JSON (or redirects to dashboard).
        - Includes the account email and whether it was new or re-authorized.

    On failure:
        - Returns ``OAuthCallbackError`` JSON with an actionable error message.
        - Does NOT leak client secrets or raw provider error strings.
    """
    dashboard_url = _get_dashboard_url()

    # --- Handle provider-side errors (e.g. user denied consent) ---
    if error:
        logger.warning("Google OAuth provider error: %s", error)
        if error_description:
            logger.debug("Google OAuth provider error_description: %s", error_description)
        # Consume the state token if provided to prevent reuse after a denied/cancelled flow.
        if state:
            _validate_and_consume_state(state)
        error_payload = OAuthCallbackError(
            error_code="provider_error",
            message=_sanitize_provider_error(error),
        )
        if dashboard_url:
            return RedirectResponse(
                url=f"{dashboard_url}?oauth_error={error_payload.error_code}",
                status_code=302,
            )
        return JSONResponse(status_code=400, content=error_payload.model_dump())

    # --- Validate required parameters ---
    if not code:
        error_payload = OAuthCallbackError(
            error_code="missing_code",
            message="Authorization code is missing from the callback.",
        )
        return JSONResponse(status_code=400, content=error_payload.model_dump())

    if not state:
        error_payload = OAuthCallbackError(
            error_code="missing_state",
            message="State parameter is missing from the callback. Possible CSRF attempt.",
        )
        return JSONResponse(status_code=400, content=error_payload.model_dump())

    # --- Validate CSRF state ---
    state_entry = _validate_and_consume_state(state)
    if state_entry is None:
        logger.warning("OAuth callback received invalid or expired state token")
        error_payload = OAuthCallbackError(
            error_code="invalid_state",
            message="State parameter is invalid or expired. Please restart the OAuth flow.",
        )
        return JSONResponse(status_code=400, content=error_payload.model_dump())

    # --- Exchange code for tokens ---
    client_id, client_secret = await _resolve_app_credentials(db_manager)
    redirect_uri = _get_redirect_uri()

    try:
        token_data = await _exchange_code_for_tokens(
            code=code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
    except _TokenExchangeError as exc:
        logger.warning("Google OAuth token exchange failed: %s", exc)
        error_payload = OAuthCallbackError(
            error_code="token_exchange_failed",
            message="Failed to exchange authorization code for tokens. "
            "The code may have expired or already been used. Please restart the OAuth flow.",
        )
        return JSONResponse(status_code=400, content=error_payload.model_dump())

    refresh_token = token_data.get("refresh_token")
    access_token = token_data.get("access_token")
    scope = token_data.get("scope")

    # --- Call Google userinfo to resolve account email ---
    # When access_token is available, call userinfo to get the authenticated email.
    # This is the authoritative source — ignore the account_hint from state.
    account_email: str | None = None
    account_display_name: str | None = None

    if access_token:
        try:
            userinfo = await _fetch_google_userinfo(access_token)
            account_email = userinfo.get("email")
            account_display_name = userinfo.get("name")
        except _UserinfoError as exc:
            logger.warning("Google userinfo call failed: %s", exc)
            error_payload = OAuthCallbackError(
                error_code="userinfo_failed",
                message="Failed to retrieve account information from Google. "
                "Please restart the OAuth flow.",
            )
            return JSONResponse(status_code=502, content=error_payload.model_dump())

    # --- Resolve or create account in registry ---
    shared_pool = _get_shared_pool(db_manager)
    is_new_account: bool | None = None

    if shared_pool is not None and account_email:
        # Try to find existing account.
        try:
            existing_account = await get_google_account(shared_pool, account=account_email)
            # Account exists — update credentials.
            is_new_account = False
            if refresh_token:
                # Update refresh token on existing companion entity.
                await _update_account_refresh_token(
                    shared_pool,
                    entity_id=existing_account.entity_id,
                    refresh_token=refresh_token,
                    scopes=scope,
                )
            # else: No new refresh_token — preserve existing one.
        except GoogleAccountNotFoundError:
            # New account — need a refresh_token to register it.
            is_new_account = True
            if not refresh_token:
                logger.warning(
                    "New Google account %r in callback but no refresh_token provided",
                    account_email,
                )
                error_payload = OAuthCallbackError(
                    error_code="no_refresh_token",
                    message="Google did not return a refresh token for a new account. "
                    "Please re-authorize using 'force_consent=true' to get a fresh token.",
                )
                return JSONResponse(status_code=400, content=error_payload.model_dump())

            scope_list = [s for s in scope.split() if s] if scope else []
            try:
                await create_google_account(
                    shared_pool,
                    email=account_email,
                    display_name=account_display_name,
                    scopes=scope_list,
                    refresh_token=refresh_token,
                )
            except GoogleAccountAlreadyExistsError:
                # Race condition — treat as re-auth.
                is_new_account = False
                existing_account = await get_google_account(shared_pool, account=account_email)
                if refresh_token:
                    await _update_account_refresh_token(
                        shared_pool,
                        entity_id=existing_account.entity_id,
                        refresh_token=refresh_token,
                        scopes=scope,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Google account registry error: %s", exc)
            # Fall through to legacy credential storage below.
    elif shared_pool is None:
        # No shared pool — fall back to legacy single-account credential storage.
        pass

    # --- Persist app credentials and legacy refresh token ---
    # Secret material (client_secret, refresh_token) is NEVER logged in plaintext.
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        raise HTTPException(
            status_code=503,
            detail="Shared credential DB unavailable; cannot persist OAuth credentials.",
        )

    # Store app credentials (client_id, client_secret) always.
    # For the refresh token: use registry (above) when pool is available and account resolved;
    # otherwise fall back to owner entity storage.
    if refresh_token and (shared_pool is None or not account_email):
        # Legacy path: store refresh token on owner entity.
        await store_google_credentials(
            cred_store,
            pool=shared_pool,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            scope=scope,
        )
    else:
        # Multi-account path: only store app credentials (refresh token stored by registry).
        await store_app_credentials(cred_store, client_id=client_id, client_secret=client_secret)

    logger.info(
        "Google OAuth COMPLETE (client_id=%s, account=%s, is_new=%s, persisted=true)",
        client_id,
        account_email,
        is_new_account,
    )
    logger.info("Scope granted: %s", scope)

    success_payload = OAuthCallbackSuccess(
        success=True,
        message="OAuth bootstrap complete. Credentials persisted to database.",
        provider="google",
        scope=scope,
        account_email=account_email,
        is_new_account=is_new_account,
    )

    if dashboard_url:
        return RedirectResponse(
            url=f"{dashboard_url}?oauth_success=true",
            status_code=302,
        )

    return JSONResponse(content=success_payload.model_dump())


async def _update_account_refresh_token(
    pool: Any,
    *,
    entity_id: uuid.UUID,
    refresh_token: str,
    scopes: str | None,
) -> None:
    """Update the refresh token and scopes on an existing google_accounts companion entity."""
    scope_list = [s for s in scopes.split() if s] if scopes else None

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Update refresh token in entity_info.
            await conn.execute(
                """
                INSERT INTO shared.entity_info (entity_id, type, value, secured, is_primary)
                VALUES ($1, 'google_oauth_refresh', $2, true, true)
                ON CONFLICT (entity_id, type) DO UPDATE SET
                    value = EXCLUDED.value,
                    secured = EXCLUDED.secured
                """,
                entity_id,
                refresh_token,
            )
            # Update granted_scopes and last_token_refresh_at on google_accounts row.
            if scope_list is not None:
                await conn.execute(
                    """
                    UPDATE shared.google_accounts
                    SET granted_scopes = $1::text[],
                        status = 'active',
                        last_token_refresh_at = now()
                    WHERE entity_id = $2
                    """,
                    scope_list,
                    entity_id,
                )
            else:
                await conn.execute(
                    """
                    UPDATE shared.google_accounts
                    SET status = 'active',
                        last_token_refresh_at = now()
                    WHERE entity_id = $1
                    """,
                    entity_id,
                )


# ---------------------------------------------------------------------------
# Credential management endpoints (for /secrets dashboard page)
# ---------------------------------------------------------------------------


@router.put(
    "/google/credentials",
    response_model=UpsertAppCredentialsResponse,
    summary="Store Google app credentials (client_id + client_secret)",
    description=(
        "Stores the Google OAuth app credentials (client_id and client_secret) in the database. "
        "An existing refresh token is preserved if already present. "
        "Secret values are never echoed back in responses."
    ),
)
async def upsert_google_credentials(
    body: UpsertAppCredentialsRequest,
    db_manager: Any = Depends(_get_db_manager),
) -> UpsertAppCredentialsResponse:
    """Store Google app credentials in the database.

    Stores client_id and client_secret. If a refresh token is already stored
    from a previous OAuth flow, it is preserved.

    Raises
    ------
    HTTPException 503
        If no database is available to store the credentials.
    HTTPException 422
        If client_id or client_secret are empty.
    """
    if db_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Database not available. Cannot persist credentials.",
        )

    client_id = body.client_id.strip()
    client_secret = body.client_secret.strip()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=422,
            detail="client_id and client_secret must be non-empty.",
        )

    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        raise HTTPException(
            status_code=503,
            detail="Shared credential database is unavailable. Cannot persist credentials.",
        )

    await store_app_credentials(cred_store, client_id=client_id, client_secret=client_secret)

    return UpsertAppCredentialsResponse(
        success=True,
        message="Google app credentials stored.",
    )


@router.delete(
    "/google/credentials",
    response_model=DeleteCredentialsResponse,
    summary="Delete stored Google credentials",
    description=(
        "Deletes all stored Google OAuth credentials from the database "
        "(client_id, client_secret, and refresh_token if present). "
        "A confirmation is expected before calling this endpoint."
    ),
)
async def delete_google_credentials_endpoint(
    db_manager: Any = Depends(_get_db_manager),
) -> DeleteCredentialsResponse:
    """Delete all stored Google OAuth credentials from the database.

    Raises
    ------
    HTTPException 503
        If no database is available.
    """
    if db_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Database not available. Cannot delete credentials.",
        )

    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        raise HTTPException(
            status_code=503,
            detail="Shared credential database is unavailable. Cannot delete credentials.",
        )

    deleted = await delete_google_credentials(
        cred_store, pool=_get_shared_pool(db_manager), delete_all=True
    )

    return DeleteCredentialsResponse(
        success=True,
        deleted=deleted,
        message="Credentials deleted." if deleted else "No credentials were stored.",
    )


@router.get(
    "/google/credentials",
    response_model=GoogleCredentialStatusResponse,
    summary="Get Google credential status (masked)",
    description=(
        "Returns presence indicators for stored Google credentials. "
        "Secret values are NEVER returned — only boolean presence flags. "
        "Also probes OAuth health via the status endpoint."
    ),
)
async def get_google_credential_status(
    db_manager: Any = Depends(_get_db_manager),
) -> GoogleCredentialStatusResponse:
    """Return masked status of stored Google credentials.

    Does not return any secret values — only presence indicators.
    Also probes OAuth health (same as /status endpoint).

    Raises
    ------
    HTTPException 503
        If no database is available.
    """
    if db_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Database not available.",
        )

    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        raise HTTPException(
            status_code=503,
            detail="Shared credential database is unavailable.",
        )

    shared_pool = _get_shared_pool(db_manager)
    app_creds = await load_app_credentials(cred_store, pool=shared_pool)

    client_id_configured = app_creds is not None and bool(app_creds.client_id)
    client_secret_configured = app_creds is not None and bool(app_creds.client_secret)
    refresh_token_present = app_creds is not None and bool(app_creds.refresh_token)
    scope = app_creds.scope if app_creds else None

    # Also probe the OAuth health
    health = await _check_google_credential_status(db_manager=db_manager)

    return GoogleCredentialStatusResponse(
        client_id_configured=client_id_configured,
        client_secret_configured=client_secret_configured,
        refresh_token_present=refresh_token_present,
        scope=scope,
        oauth_health=health.state,
        oauth_health_remediation=health.remediation,
        oauth_health_detail=health.detail,
    )


# ---------------------------------------------------------------------------
# Google Account management endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/google/accounts",
    response_model=list[GoogleAccountResponse],
    summary="List connected Google accounts",
    description=(
        "Returns all connected Google accounts ordered by is_primary DESC, connected_at ASC. "
        "No credential material (refresh tokens, client secrets) is included."
    ),
)
async def list_google_accounts_endpoint(
    db_manager: Any = Depends(_get_db_manager),
) -> list[GoogleAccountResponse]:
    """List all connected Google accounts."""
    shared_pool = _get_shared_pool(db_manager)
    if shared_pool is None:
        raise HTTPException(status_code=503, detail="Shared database is unavailable.")

    accounts = await list_google_accounts(shared_pool)
    return [_account_to_response(a) for a in accounts]


@router.put(
    "/google/accounts/{account_id}/primary",
    response_model=SetPrimaryResponse,
    summary="Set primary Google account",
    description="Atomically sets the specified account as primary; all others become non-primary.",
)
async def set_primary_google_account(
    account_id: uuid.UUID,
    db_manager: Any = Depends(_get_db_manager),
) -> SetPrimaryResponse:
    """Set a Google account as the primary account."""
    shared_pool = _get_shared_pool(db_manager)
    if shared_pool is None:
        raise HTTPException(status_code=503, detail="Shared database is unavailable.")

    try:
        account = await set_primary_account(shared_pool, account_id)
    except GoogleAccountNotFoundError:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found.")

    return SetPrimaryResponse(account=_account_to_response(account))


@router.delete(
    "/google/accounts/{account_id}",
    response_model=DisconnectAccountResponse,
    summary="Disconnect a Google account",
    description=(
        "Disconnects a Google account: revokes the token, cleans up entity_info, "
        "and updates the account status. If the account was primary, the oldest remaining "
        "active account is auto-promoted."
    ),
)
async def disconnect_google_account(
    account_id: uuid.UUID,
    hard_delete: bool = Query(
        default=False,
        description="When true, fully removes the google_accounts row and companion entity.",
    ),
    db_manager: Any = Depends(_get_db_manager),
) -> DisconnectAccountResponse:
    """Disconnect a Google account."""
    shared_pool = _get_shared_pool(db_manager)
    if shared_pool is None:
        raise HTTPException(status_code=503, detail="Shared database is unavailable.")

    # Capture primary status before disconnect to report auto-promotion.
    try:
        account_before = await get_google_account(shared_pool, account=account_id)
        was_primary = account_before.is_primary
    except GoogleAccountNotFoundError:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found.")

    await disconnect_account(shared_pool, account_id, hard_delete=hard_delete)

    # Detect auto-promoted account if this was primary.
    # This applies to both soft and hard-delete: the registry always auto-promotes
    # the next active account when a primary is removed.
    auto_promoted_id: uuid.UUID | None = None
    if was_primary:
        accounts_after = await list_google_accounts(shared_pool)
        primary_after = next((a for a in accounts_after if a.is_primary), None)
        if primary_after and primary_after.id != account_id:
            auto_promoted_id = primary_after.id

    msg = "Account disconnected (hard deleted)." if hard_delete else "Account disconnected."
    return DisconnectAccountResponse(
        message=msg,
        auto_promoted_id=auto_promoted_id,
    )


@router.get(
    "/google/accounts/{account_id}/status",
    response_model=GoogleAccountStatus,
    summary="Get per-account credential status",
    description=(
        "Returns per-account credential status including token validity and scope coverage."
    ),
)
async def get_google_account_status(
    account_id: uuid.UUID,
    db_manager: Any = Depends(_get_db_manager),
) -> GoogleAccountStatus:
    """Get per-account credential status."""
    shared_pool = _get_shared_pool(db_manager)
    cred_store = _make_credential_store(db_manager)

    if shared_pool is None or cred_store is None:
        raise HTTPException(status_code=503, detail="Shared database is unavailable.")

    try:
        account = await get_google_account(shared_pool, account=account_id)
    except GoogleAccountNotFoundError:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found.")

    # Check app credentials.
    app_creds = await load_app_credentials(cred_store)
    has_app_credentials = app_creds is not None and bool(app_creds.client_id)

    # Check refresh token on companion entity.
    has_refresh_token = False
    token_valid = False
    async with shared_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT value FROM shared.entity_info
            WHERE entity_id = $1 AND type = 'google_oauth_refresh'
            LIMIT 1
            """,
            account.entity_id,
        )
        if row is not None:
            has_refresh_token = True
            refresh_token_val = row["value"]

    # Probe token validity if we have everything needed.
    granted_scopes = list(account.granted_scopes)
    if has_refresh_token and has_app_credentials and app_creds is not None:
        probe_result = await _probe_google_token(
            client_id=app_creds.client_id,
            client_secret=app_creds.client_secret,
            refresh_token=refresh_token_val,  # type: ignore[possibly-undefined]
        )
        token_valid = probe_result.connected
        if probe_result.scopes_granted:
            granted_scopes = list(probe_result.scopes_granted)

    # Compute missing scopes.
    granted_scope_set = set(granted_scopes)
    missing_scopes = sorted(_REQUIRED_SCOPES - granted_scope_set)

    return GoogleAccountStatus(
        has_refresh_token=has_refresh_token,
        has_app_credentials=has_app_credentials,
        granted_scopes=granted_scopes,
        missing_scopes=missing_scopes,
        token_valid=token_valid,
        last_token_refresh_at=account.last_token_refresh_at,
    )


def _account_to_response(account: Any) -> GoogleAccountResponse:
    """Convert a GoogleAccount dataclass to a GoogleAccountResponse Pydantic model."""
    return GoogleAccountResponse(
        id=account.id,
        email=account.email,
        display_name=account.display_name,
        is_primary=account.is_primary,
        status=account.status,
        granted_scopes=list(account.granted_scopes),
        connected_at=account.connected_at,
        last_token_refresh_at=account.last_token_refresh_at,
    )


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    response_model=OAuthStatusResponse,
    summary="Get OAuth credential status",
    description=(
        "Returns the connectivity state of all OAuth credential sets. "
        "Use this endpoint to determine whether Google credentials are configured "
        "and to surface actionable remediation guidance in the dashboard UX."
    ),
)
async def oauth_status(
    db_manager: Any = Depends(_get_db_manager),
) -> OAuthStatusResponse:
    """Report the current state of Google OAuth credentials.

    Checks whether credentials are configured in DB and,
    when possible, probes Google's token-info endpoint to validate scope coverage.

    This endpoint is designed for dashboard polling (e.g. after completing the
    OAuth bootstrap flow) and for surfacing connection status badges in the UI.

    The top-level ``google`` status reflects the worst-case across all accounts.
    An ``accounts`` array is included when multi-account Google is configured,
    for backward compatibility with single-account setups the flat fields are
    preserved.

    Returns
    -------
    OAuthStatusResponse
        Aggregated status for all OAuth providers (Google only in v1).
    """
    google_status = await _check_google_credential_status(db_manager=db_manager)

    # Attach accounts list when shared pool is available.
    accounts_response: list[GoogleAccountResponse] | None = None
    shared_pool = _get_shared_pool(db_manager)
    if shared_pool is not None:
        try:
            accounts = await list_google_accounts(shared_pool)
            if accounts:
                accounts_response = [_account_to_response(a) for a in accounts]
        except Exception:  # noqa: BLE001
            pass  # Non-fatal — status still works without account list

    return OAuthStatusResponse(google=google_status, accounts=accounts_response)


async def _check_google_credential_status(db_manager: Any = None) -> OAuthCredentialStatus:
    """Derive the operational status of the stored Google credentials.

    Performs the following checks in order:

    1. Whether client_id/client_secret are available in DB.
    2. Whether a refresh token is stored in DB.
    3. Probe Google's token-info endpoint to validate scope coverage.

    Parameters
    ----------
    db_manager:
        Optional DatabaseManager instance.  When provided, DB credentials
        are resolved from the shared credential store.

    Returns
    -------
    OAuthCredentialStatus
        Structured status including state, connected flag, and remediation text.
    """
    # --- Resolution: DB only ---
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        return OAuthCredentialStatus(
            state=OAuthCredentialState.unknown_error,
            remediation=(
                "Shared credential database is unavailable. Restore DB connectivity and retry."
            ),
            detail="Shared credential store unavailable.",
        )

    app_creds = await load_app_credentials(cred_store, pool=_get_shared_pool(db_manager))
    client_id = app_creds.client_id if app_creds is not None else ""
    client_secret = app_creds.client_secret if app_creds is not None else ""
    refresh_token = app_creds.refresh_token if app_creds is not None else None

    # --- Check 1: client credentials not configured ---
    if not client_id or not client_secret:
        return OAuthCredentialStatus(
            state=OAuthCredentialState.not_configured,
            remediation=(
                "Google OAuth client credentials are not configured. "
                "Add your client_id and client_secret on the Secrets page, "
                "then click 'Connect Google' to start the authorization flow."
            ),
            detail="client_id or client_secret is missing in DB.",
        )

    # --- Check 2: no refresh token stored ---
    if not refresh_token:
        return OAuthCredentialStatus(
            state=OAuthCredentialState.not_configured,
            remediation=(
                "Google credentials have not been connected yet. "
                "Click 'Connect Google' to start the OAuth authorization flow."
            ),
            detail="No refresh token found in DB.",
        )

    # --- Check 3: probe Google to validate the refresh token ---
    return await _probe_google_token(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )


async def _probe_google_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> OAuthCredentialStatus:
    """Attempt to refresh an access token and introspect the resulting scopes.

    This makes a real HTTP call to Google's token endpoint. On failure the
    error is classified into a specific ``OAuthCredentialState`` with an
    actionable ``remediation`` message for the dashboard.

    Parameters
    ----------
    client_id:
        Google OAuth client ID.
    client_secret:
        Google OAuth client secret.
    refresh_token:
        Stored refresh token to validate.

    Returns
    -------
    OAuthCredentialStatus
        Derived status based on the token probe result.
    """
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            response = await http_client.post(GOOGLE_TOKEN_URL, data=payload)
    except httpx.TransportError as exc:
        logger.warning("OAuth status probe: network error contacting Google: %s", exc)
        return OAuthCredentialStatus(
            state=OAuthCredentialState.unknown_error,
            remediation=(
                "Unable to reach Google's authorization server. "
                "Check your network connectivity and try again."
            ),
            detail=f"Network error: {exc}",
        )

    if response.status_code != 200:
        return _classify_token_refresh_error(response)

    try:
        token_data = response.json()
    except json.JSONDecodeError as exc:
        logger.warning("OAuth status probe: invalid JSON from Google token endpoint: %s", exc)
        return OAuthCredentialStatus(
            state=OAuthCredentialState.unknown_error,
            remediation=("Received an unexpected response from Google. Please try again later."),
            detail=f"JSON decode error: {exc}",
        )

    # --- Token refresh succeeded — check scopes ---
    # Google may omit the `scope` field on refresh responses when scopes are unchanged.
    # When absent, we cannot verify scope coverage so we treat the token as connected
    # rather than incorrectly flagging healthy credentials as missing_scope.
    granted_scope_str = token_data.get("scope")
    if granted_scope_str is None:
        # Scope field absent — assume token is valid; cannot verify scope coverage.
        return OAuthCredentialStatus(
            state=OAuthCredentialState.connected,
            scopes_granted=None,
            remediation=None,
            detail=None,
        )

    granted_scopes = [s for s in granted_scope_str.split() if s]
    granted_scope_set = set(granted_scopes)

    missing = _REQUIRED_SCOPES - granted_scope_set
    if missing:
        return OAuthCredentialStatus(
            state=OAuthCredentialState.missing_scope,
            scopes_granted=granted_scopes,
            remediation=(
                "Your Google credentials are missing required permissions. "
                "Re-run the OAuth flow and ensure you grant access to Gmail and Calendar. "
                "If prompted, click 'Allow' for all requested permissions."
            ),
            detail=f"Missing required scopes: {', '.join(sorted(missing))}",
        )

    return OAuthCredentialStatus(
        state=OAuthCredentialState.connected,
        scopes_granted=granted_scopes,
        remediation=None,
        detail=None,
    )


def _classify_token_refresh_error(response: httpx.Response) -> OAuthCredentialStatus:
    """Map a failed token-refresh HTTP response to an OAuthCredentialStatus.

    Interprets Google's error codes (from the JSON body where available)
    and returns a structured status with actionable remediation text.

    Parameters
    ----------
    response:
        The failed HTTP response from Google's token endpoint.

    Returns
    -------
    OAuthCredentialStatus
        Classified status with remediation guidance.
    """
    error_code: str | None = None
    error_description: str | None = None

    try:
        body = response.json()
        if isinstance(body, dict):
            error_code = body.get("error")
            error_description = body.get("error_description")
    except json.JSONDecodeError:
        pass

    logger.warning(
        "OAuth status probe: token refresh failed HTTP %d error=%s",
        response.status_code,
        error_code,
    )

    # invalid_grant — token revoked, expired, or never valid
    if error_code == "invalid_grant":
        return OAuthCredentialStatus(
            state=OAuthCredentialState.expired,
            remediation=(
                "Your Google authorization has expired or been revoked. "
                "Click 'Connect Google' to re-run the OAuth flow and obtain a new token."
            ),
            detail=(
                f"Google error: invalid_grant — {error_description or 'token revoked or expired'}"
            ),
        )

    # invalid_client — client ID/secret mismatch or redirect URI mismatch
    if error_code == "invalid_client":
        # Heuristic: redirect URI mismatch often surfaces as invalid_client
        return OAuthCredentialStatus(
            state=OAuthCredentialState.redirect_uri_mismatch,
            remediation=(
                "OAuth client credentials are invalid or the redirect URI does not match "
                "the one registered in the Google Cloud Console. "
                "Verify app credentials on the Secrets page and "
                "GOOGLE_OAUTH_REDIRECT_URI, then re-run the OAuth flow."
            ),
            detail=(
                f"Google error: invalid_client — "
                f"{error_description or 'client credentials invalid'}"
            ),
        )

    # access_denied — typically the tester approval case
    if error_code == "access_denied":
        return OAuthCredentialStatus(
            state=OAuthCredentialState.unapproved_tester,
            remediation=(
                "Access was denied. If your Google OAuth app is in testing mode, "
                "add your Google account as an approved tester in the Google Cloud Console "
                "under OAuth consent screen > Test users, then retry the OAuth flow."
            ),
            detail=f"Google error: access_denied — {error_description or 'tester not approved'}",
        )

    # Catch-all for other Google errors
    return OAuthCredentialStatus(
        state=OAuthCredentialState.unknown_error,
        remediation=(
            "An unexpected error occurred while validating your Google credentials. "
            "Check the server logs for details and try re-running the OAuth flow."
        ),
        detail=(
            f"Google HTTP {response.status_code}: {error_code} — "
            f"{error_description or 'no description'}"
        ),
    )


# ---------------------------------------------------------------------------
# Token exchange and userinfo helpers
# ---------------------------------------------------------------------------


class _TokenExchangeError(Exception):
    """Raised when the authorization code → token exchange fails."""


class _UserinfoError(Exception):
    """Raised when the Google userinfo endpoint call fails."""


async def _exchange_code_for_tokens(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Exchange an authorization code for OAuth tokens.

    Parameters
    ----------
    code:
        Authorization code returned by Google in the callback.
    client_id:
        Google OAuth client ID.
    client_secret:
        Google OAuth client secret.
    redirect_uri:
        The redirect URI registered with Google (must match exactly).

    Returns
    -------
    dict
        The full token response from Google (access_token, refresh_token, scope, etc.).

    Raises
    ------
    _TokenExchangeError
        If the exchange fails for any reason (HTTP error, invalid code, network error).
    """
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(GOOGLE_TOKEN_URL, data=payload)
    except httpx.TransportError as exc:
        raise _TokenExchangeError(f"Network error during token exchange: {exc}") from exc

    if response.status_code != 200:
        # Log status code but not the raw body (may contain sensitive details)
        raise _TokenExchangeError(f"Token endpoint returned HTTP {response.status_code}")

    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise _TokenExchangeError(f"Invalid JSON in token response: {exc}") from exc


async def _fetch_google_userinfo(access_token: str) -> dict[str, Any]:
    """Fetch the authenticated user's profile from Google's userinfo endpoint.

    Parameters
    ----------
    access_token:
        A valid Google OAuth access token.

    Returns
    -------
    dict
        Userinfo payload from Google (includes ``email``, ``name``, etc.).

    Raises
    ------
    _UserinfoError
        If the request fails for any reason (HTTP error, network error, JSON error).
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(GOOGLE_USERINFO_URL, headers=headers)
    except httpx.TransportError as exc:
        raise _UserinfoError(f"Network error during userinfo call: {exc}") from exc

    if response.status_code != 200:
        raise _UserinfoError(f"Userinfo endpoint returned HTTP {response.status_code}")

    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise _UserinfoError(f"Invalid JSON in userinfo response: {exc}") from exc


# ---------------------------------------------------------------------------
# Error sanitization
# ---------------------------------------------------------------------------

_KNOWN_PROVIDER_ERRORS: dict[str, str] = {
    "access_denied": "The user denied access. OAuth flow cancelled.",
    "invalid_request": "The OAuth request was malformed. Please restart the flow.",
    "unauthorized_client": "This application is not authorized to use Google OAuth. "
    "Check your OAuth app configuration.",
    "unsupported_response_type": "Unsupported response type. Please restart the flow.",
    "invalid_scope": "One or more requested OAuth scopes are invalid or not permitted.",
    "server_error": "Google encountered an internal error. Please try again.",
    "temporarily_unavailable": "Google OAuth is temporarily unavailable. Please try again later.",
}


def _sanitize_provider_error(error: str) -> str:
    """Convert a provider error code into a safe, actionable user message.

    Unknown error codes are replaced with a generic message to avoid
    leaking internal provider state.
    """
    return _KNOWN_PROVIDER_ERRORS.get(
        error,
        "The OAuth authorization failed. Please restart the flow.",
    )
