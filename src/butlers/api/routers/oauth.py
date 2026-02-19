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
     - Extracts and logs the refresh token (printed to stdout so the operator
       can capture it — no DB persistence here; this is a one-off bootstrap).
     - Redirects to the dashboard URL on success (if OAUTH_DASHBOARD_URL is set),
       or returns a JSON success payload.

Environment variables:
  GOOGLE_OAUTH_CLIENT_ID     — OAuth client ID (required)
  GOOGLE_OAUTH_CLIENT_SECRET — OAuth client secret (required)
  GOOGLE_OAUTH_REDIRECT_URI  — Callback URL registered with Google
                               (default: http://localhost:8200/api/oauth/google/callback)
  GOOGLE_OAUTH_SCOPES        — Space-separated scopes
                               (default: Gmail + Calendar read/write)
  OAUTH_DASHBOARD_URL        — Where to redirect after a successful bootstrap
                               (default: not set; returns JSON payload instead)

Security notes:
  - State tokens are one-time-use: consumed on first callback validation.
  - State store entries expire after 10 minutes.
  - Client secrets are never echoed back in responses.
  - Error messages are sanitized to avoid leaking OAuth provider details.
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
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse, Response

from butlers.api.models.oauth import OAuthCallbackError, OAuthCallbackSuccess, OAuthStartResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

# ---------------------------------------------------------------------------
# Google OAuth constants
# ---------------------------------------------------------------------------

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

_DEFAULT_REDIRECT_URI = "http://localhost:8200/api/oauth/google/callback"
_DEFAULT_SCOPES = " ".join(
    [
        "https://www.googleapis.com/auth/gmail.readonly",
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


def _get_client_id() -> str:
    """Read GOOGLE_OAUTH_CLIENT_ID from environment."""
    val = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    if not val:
        raise HTTPException(
            status_code=503,
            detail="GOOGLE_OAUTH_CLIENT_ID is not configured on the server.",
        )
    return val


def _get_client_secret() -> str:
    """Read GOOGLE_OAUTH_CLIENT_SECRET from environment."""
    val = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if not val:
        raise HTTPException(
            status_code=503,
            detail="GOOGLE_OAUTH_CLIENT_SECRET is not configured on the server.",
        )
    return val


def _get_redirect_uri() -> str:
    """Read GOOGLE_OAUTH_REDIRECT_URI or use the default."""
    return os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", _DEFAULT_REDIRECT_URI).strip()


def _get_scopes() -> str:
    """Read GOOGLE_OAUTH_SCOPES or use the default."""
    return os.environ.get("GOOGLE_OAUTH_SCOPES", _DEFAULT_SCOPES).strip()


def _get_dashboard_url() -> str | None:
    """Read OAUTH_DASHBOARD_URL; returns None if not set."""
    val = os.environ.get("OAUTH_DASHBOARD_URL", "").strip()
    return val or None


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
) -> Response:
    """Begin the Google OAuth authorization flow.

    Generates a CSRF state token, stores it in the in-memory state store,
    builds the Google authorization URL, and either redirects the browser
    or returns the URL as JSON (when ``?redirect=false``).
    """
    client_id = _get_client_id()
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
    client_secret = _get_client_secret()
    client_id = _get_client_id()
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

    # --- Emit refresh token to operator logs ---
    # This is the bootstrap mechanism: the operator reads the token from logs
    # and stores it in the appropriate environment variable.
    logger.info("=" * 60)
    logger.info("Google OAuth bootstrap COMPLETE")
    logger.info("Refresh token: %s", refresh_token)
    logger.info("Scope granted: %s", scope)
    logger.info("Store this token in GMAIL_REFRESH_TOKEN / GOOGLE_REFRESH_TOKEN as needed.")
    logger.info("=" * 60)

    # Also print to stdout for easy capture in dev environments
    print(f"\n{'=' * 60}")
    print("Google OAuth Bootstrap Complete")
    print(f"REFRESH_TOKEN={refresh_token}")
    print(f"SCOPE_GRANTED={scope}")
    print("Store this in your secrets file as GMAIL_REFRESH_TOKEN.")
    print(f"{'=' * 60}\n")

    success_payload = OAuthCallbackSuccess(
        success=True,
        message="OAuth bootstrap complete. Refresh token has been printed to server logs.",
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
