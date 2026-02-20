"""Google OAuth bootstrap endpoints.

Implements a two-leg OAuth 2.0 authorization-code flow for acquiring
Google OAuth refresh tokens for use by butler modules (Gmail connector,
Calendar module, etc.).

The bootstrap flow:
  1. GET /api/oauth/google/start
     - Generates a cryptographically random state token (CSRF protection).
     - Stores the state in an in-memory store (keyed by state value, TTL 10 min).
     - Returns the Google authorization URL as a redirect response.

  2. GET /api/oauth/google/callback
     - Validates the state parameter against the stored state token.
     - Exchanges the authorization code for tokens via Google's token endpoint.
     - Extracts and logs the refresh token (redacted) and persists credentials
       to the shared credential store. Secret material is never printed or
       logged in plaintext.
     - Redirects to the dashboard URL on success (if OAUTH_DASHBOARD_URL is set),
       or returns a JSON success payload.

  3. GET /api/oauth/status
     - Reports whether Google credentials are present and usable.
     - Returns a machine-readable state (OAuthCredentialState) plus actionable
       remediation guidance for the dashboard UX.

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
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse, Response

from butlers.api.models.oauth import (
    DeleteCredentialsResponse,
    GoogleCredentialStatusResponse,
    OAuthCallbackError,
    OAuthCallbackSuccess,
    OAuthCredentialState,
    OAuthCredentialStatus,
    OAuthStartResponse,
    OAuthStatusResponse,
    UpsertAppCredentialsRequest,
    UpsertAppCredentialsResponse,
)
from butlers.credential_store import CredentialStore
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


router = APIRouter(prefix="/api/oauth", tags=["oauth"])

# ---------------------------------------------------------------------------
# Google OAuth constants
# ---------------------------------------------------------------------------

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TOKEN_INFO_URL = "https://oauth2.googleapis.com/tokeninfo"

_DEFAULT_REDIRECT_URI = "http://localhost:40200/api/oauth/google/callback"
_DEFAULT_SCOPES = " ".join(
    [
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

# Maps state token → expiry timestamp (monotonic)
# NOTE: This store is process-local. Do not run multiple worker processes
# (e.g. gunicorn -w N) — CSRF state validation will silently fail across workers.
_state_store: dict[str, float] = {}


def _generate_state() -> str:
    """Generate a cryptographically random CSRF state token."""
    return secrets.token_urlsafe(32)


def _store_state(state: str) -> None:
    """Store a state token with an expiry timestamp."""
    _state_store[state] = time.monotonic() + _STATE_TTL_SECONDS
    _evict_expired_states()


def _validate_and_consume_state(state: str) -> bool:
    """Validate a state token and consume it (one-time-use).

    Returns True if the state was valid and unexpired, False otherwise.
    """
    _evict_expired_states()
    expiry = _state_store.pop(state, None)
    if expiry is None:
        return False
    return time.monotonic() < expiry


def _evict_expired_states() -> None:
    """Remove all expired state tokens from the store."""
    now = time.monotonic()
    expired = [k for k, exp in _state_store.items() if now >= exp]
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
    },
)
async def oauth_google_start(
    redirect: bool = Query(
        default=True,
        description="If true (default), redirect to Google authorization URL. "
        "If false, return the URL as JSON for programmatic callers.",
    ),
    db_manager: Any = Depends(_get_db_manager),
) -> Response:
    """Begin the Google OAuth authorization flow.

    Generates a CSRF state token, stores it in the in-memory state store,
    builds the Google authorization URL, and either redirects the browser
    or returns the URL as JSON (when ``?redirect=false``).
    """
    client_id, _ = await _resolve_app_credentials(db_manager)
    redirect_uri = _get_redirect_uri()
    scopes = _get_scopes()

    state = _generate_state()
    _store_state(state)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "access_type": "offline",
        "prompt": "consent",  # Force refresh token to be returned
        "state": state,
    }

    authorization_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    logger.info("Google OAuth bootstrap started (state=%s...)", state[:8])

    if redirect:
        return RedirectResponse(url=authorization_url, status_code=302)

    return JSONResponse(
        content=OAuthStartResponse(
            authorization_url=authorization_url,
            state=state,
        ).model_dump()
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

    Validates state, exchanges the authorization code for tokens, extracts
    the refresh token, and either redirects to the dashboard or returns a
    structured JSON payload.

    On success:
        - Logs the refresh token so the operator can capture it.
        - Returns ``OAuthCallbackSuccess`` JSON (or redirects to dashboard).

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
    if not _validate_and_consume_state(state):
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

    # --- Extract refresh token ---
    refresh_token = token_data.get("refresh_token")
    scope = token_data.get("scope")

    if not refresh_token:
        logger.warning("Google OAuth token response did not include a refresh token")
        error_payload = OAuthCallbackError(
            error_code="no_refresh_token",
            message="Google did not return a refresh token. "
            "Ensure your OAuth app requests 'offline' access and 'prompt=consent' "
            "and that the user has not previously granted access.",
        )
        return JSONResponse(status_code=400, content=error_payload.model_dump())

    # --- Persist credentials to DB ---
    # Secret material (client_secret, refresh_token) is NEVER logged in plaintext.
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        raise HTTPException(
            status_code=503,
            detail="Shared credential DB unavailable; cannot persist OAuth credentials.",
        )

    await store_google_credentials(
        cred_store,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        scope=scope,
    )
    logger.info(
        "Google OAuth credentials persisted to butler_secrets (client_id=%s)",
        client_id,
    )

    logger.info(
        "Google OAuth bootstrap COMPLETE (client_id=%s, persisted=true)",
        client_id,
    )
    logger.info("Scope granted: %s", scope)

    success_payload = OAuthCallbackSuccess(
        success=True,
        message="OAuth bootstrap complete. Credentials persisted to database.",
        provider="google",
        scope=scope,
    )

    if dashboard_url:
        return RedirectResponse(
            url=f"{dashboard_url}?oauth_success=true",
            status_code=302,
        )

    return JSONResponse(content=success_payload.model_dump())


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

    deleted = await delete_google_credentials(cred_store)

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

    app_creds = await load_app_credentials(cred_store)

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

    Returns
    -------
    OAuthStatusResponse
        Aggregated status for all OAuth providers (Google only in v1).
    """
    google_status = await _check_google_credential_status(db_manager=db_manager)
    return OAuthStatusResponse(google=google_status)


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

    app_creds = await load_app_credentials(cred_store)
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
# Token exchange helper
# ---------------------------------------------------------------------------


class _TokenExchangeError(Exception):
    """Raised when the authorization code → token exchange fails."""


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
