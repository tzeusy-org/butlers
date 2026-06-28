"""Spotify OAuth PKCE endpoints for the dashboard.

Implements the OAuth 2.0 PKCE (Proof Key for Code Exchange) flow for
authorizing the Spotify connector. Unlike traditional OAuth, PKCE does
not require a client_secret — only the client_id and a dynamically
generated code verifier/challenge pair.

The bootstrap flow:
  1. POST /api/connectors/spotify/config
     - Validates and stores the Spotify app client_id in CredentialStore.

  2. POST /api/connectors/spotify/oauth/start
     - Generates a PKCE code verifier (random 43–128-char string).
     - Derives the code challenge (S256 = base64url(SHA-256(verifier))).
     - Generates a CSRF state token and stores both in the in-memory state store.
     - Returns the Spotify authorization URL.

  3. GET /api/connectors/spotify/oauth/callback
     - Validates the CSRF state parameter against the stored entry.
     - Retrieves the associated code verifier from the state store.
     - Exchanges the code + verifier for tokens via Spotify's token endpoint.
     - Stores access_token, refresh_token, and expires_at in CredentialStore.
     - Redirects to OAUTH_DASHBOARD_URL if configured, else returns JSON.

  4. GET /api/connectors/spotify/status
     - Checks stored credentials.
     - If present, calls Spotify GET /me to verify connectivity.
     - Returns SpotifyConnectionState plus user info.

  5. POST /api/connectors/spotify/disconnect
     - Deletes all Spotify credential keys from CredentialStore.

Environment variables:
  SPOTIFY_OAUTH_REDIRECT_URI  — Callback URL registered with Spotify
                                (default: http://localhost:41200/api/connectors/spotify/oauth/callback)
  OAUTH_DASHBOARD_URL         — Where to redirect after a successful authorization
                                (default: not set; returns JSON payload instead)

Security notes:
  - PKCE code verifiers are one-time-use: consumed on callback.
  - CSRF state tokens are one-time-use: consumed on callback.
  - State store entries expire after 10 minutes.
  - Access tokens are never echoed back in responses.
  - The status endpoint never returns raw token values.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse, Response

from butlers.api.models.spotify import (
    SpotifyConfigRequest,
    SpotifyConfigResponse,
    SpotifyConnectionState,
    SpotifyDisconnectResponse,
    SpotifyOAuthStartResponse,
    SpotifyStatusResponse,
)
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors/spotify", tags=["spotify"])

# ---------------------------------------------------------------------------
# Spotify OAuth constants
# ---------------------------------------------------------------------------

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_ME_URL = "https://api.spotify.com/v1/me"

_DEFAULT_REDIRECT_URI = "http://localhost:41200/api/connectors/spotify/oauth/callback"

# Scopes required by the Spotify connector
_DEFAULT_SCOPES = " ".join(
    [
        "user-read-playback-state",
        "user-read-recently-played",
        "user-top-read",
        "playlist-read-private",
        "playlist-read-collaborative",
        "playlist-modify-public",
        "playlist-modify-private",
        "user-modify-playback-state",
        "user-library-read",
        "user-library-modify",
    ]
)

# Set of required scopes for fast membership checks
_REQUIRED_SCOPES: frozenset[str] = frozenset(_DEFAULT_SCOPES.split())

# Credential keys used in CredentialStore
_CRED_CLIENT_ID = "SPOTIFY_CLIENT_ID"
_CRED_ACCESS_TOKEN = "SPOTIFY_ACCESS_TOKEN"
_CRED_REFRESH_TOKEN = "SPOTIFY_REFRESH_TOKEN"
_CRED_TOKEN_EXPIRES_AT = "SPOTIFY_TOKEN_EXPIRES_AT"
_CRED_GRANTED_SCOPES = "SPOTIFY_GRANTED_SCOPES"

# All credential keys managed by this module (used for validation/cleanup).
_ALL_CRED_KEYS = (
    _CRED_CLIENT_ID,
    _CRED_ACCESS_TOKEN,
    _CRED_REFRESH_TOKEN,
    _CRED_TOKEN_EXPIRES_AT,
    _CRED_GRANTED_SCOPES,
)

# Token-only keys that are cleared on disconnect (client_id is preserved).
_TOKEN_CRED_KEYS = (
    _CRED_ACCESS_TOKEN,
    _CRED_REFRESH_TOKEN,
    _CRED_TOKEN_EXPIRES_AT,
    _CRED_GRANTED_SCOPES,
)

# ---------------------------------------------------------------------------
# In-memory CSRF + PKCE state store
# State entries expire after 10 minutes (single-worker process only).
# Upper bound: at most _STATE_MAX_ENTRIES live entries; oldest evicted on overflow.
# NOTE: process-local — not safe for multi-worker deployments.
# ---------------------------------------------------------------------------

_STATE_TTL_SECONDS = 600  # 10 minutes
_STATE_MAX_ENTRIES = 256  # hard cap; each entry ~few hundred bytes


@dataclass
class _SpotifyStateEntry:
    """CSRF state store entry carrying PKCE code verifier."""

    expiry: float
    """Monotonic clock timestamp when this entry expires."""

    code_verifier: str
    """PKCE code verifier associated with this authorization request."""

    redirect_uri: str = ""
    """The redirect_uri used when starting the flow (must match on exchange)."""


# Maps state token → _SpotifyStateEntry
# NOTE: process-local; do not use with multi-worker deployments.
_state_store: dict[str, _SpotifyStateEntry] = {}


def _generate_state() -> str:
    """Generate a cryptographically random CSRF state token."""
    return secrets.token_urlsafe(32)


def _generate_pkce_verifier() -> str:
    """Generate a PKCE code verifier.

    RFC 7636: 43-128 unreserved characters.
    We use 96 bytes of randomness encoded as base64url → 128-char string.
    """
    return base64.urlsafe_b64encode(secrets.token_bytes(96)).rstrip(b"=").decode()


def _derive_pkce_challenge(verifier: str) -> str:
    """Derive the S256 code challenge from a verifier.

    challenge = BASE64URL(SHA256(ASCII(verifier)))
    """
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _store_state(state: str, *, code_verifier: str, redirect_uri: str) -> None:
    """Store a CSRF state token with its associated PKCE verifier.

    Evicts expired entries first. If the store is still at capacity after
    eviction, the oldest entry (by insertion order) is removed to make room.
    """
    _evict_expired_states()
    if len(_state_store) >= _STATE_MAX_ENTRIES:
        # Evict the oldest entry (dicts preserve insertion order in Python 3.7+)
        oldest_key = next(iter(_state_store))
        del _state_store[oldest_key]
        logger.warning(
            "Spotify state store at capacity (%d); evicted oldest entry to make room.",
            _STATE_MAX_ENTRIES,
        )
    _state_store[state] = _SpotifyStateEntry(
        expiry=time.monotonic() + _STATE_TTL_SECONDS,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def _validate_and_consume_state(state: str) -> _SpotifyStateEntry | None:
    """Validate a state token and consume it (one-time-use).

    Returns the entry if valid and unexpired, None otherwise.
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
# Optional DB manager dependency for credential persistence
# ---------------------------------------------------------------------------


def _get_db_manager() -> Any:
    """Stub replaced at startup by wire_db_dependencies().

    When not wired (e.g. in tests that don't boot the full app), returns None
    so endpoints degrade gracefully.
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


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _get_redirect_uri() -> str:
    """Read SPOTIFY_OAUTH_REDIRECT_URI or use the default."""
    return os.environ.get("SPOTIFY_OAUTH_REDIRECT_URI", _DEFAULT_REDIRECT_URI).strip()


def _get_dashboard_url() -> str | None:
    """Read OAUTH_DASHBOARD_URL; returns None if not set."""
    val = os.environ.get("OAUTH_DASHBOARD_URL", "").strip()
    return val or None


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _build_redirect_url(base_url: str, **params: str) -> str:
    """Build a redirect URL by safely merging query parameters into *base_url*.

    Handles the case where *base_url* already contains query parameters by
    parsing and merging (not concatenating), and URL-encodes all param values
    to prevent query-string injection or malformed redirects.
    """
    parsed = urlparse(base_url)
    existing = parse_qs(parsed.query, keep_blank_values=True)
    # Merge: new params overwrite existing ones with the same name
    merged = {k: v[0] if len(v) == 1 else v for k, v in existing.items()}
    merged.update(params)
    new_query = urlencode(merged, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------


class _TokenExchangeError(Exception):
    """Raised when Spotify token exchange fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


async def _exchange_code_for_tokens(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
) -> dict:
    """Exchange an authorization code for Spotify tokens using PKCE.

    Parameters
    ----------
    code:
        The authorization code from Spotify's callback.
    code_verifier:
        The PKCE verifier that matches the challenge sent during authorization.
    redirect_uri:
        Must exactly match the URI used in the authorization request.
    client_id:
        The Spotify app's client_id.

    Returns
    -------
    dict
        Parsed JSON response from Spotify token endpoint, containing
        access_token, refresh_token, expires_in, scope, token_type.

    Raises
    ------
    _TokenExchangeError
        On HTTP or network errors from Spotify.
    """
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                SPOTIFY_TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.RequestError as exc:
        raise _TokenExchangeError(f"Network error contacting Spotify: {exc}") from exc

    if resp.status_code != 200:
        body = resp.text[:200]
        raise _TokenExchangeError(
            f"Spotify token exchange failed (HTTP {resp.status_code}): {body}",
            status_code=resp.status_code,
        )

    try:
        return resp.json()
    except Exception as exc:
        raise _TokenExchangeError(f"Spotify token response is not valid JSON: {exc}") from exc


async def _fetch_spotify_me(access_token: str) -> dict | None:
    """Call Spotify GET /me with the given access token.

    Returns the parsed JSON dict, or None on any error.
    """
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                SPOTIFY_ME_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code == 200:
            return resp.json()
        logger.debug("Spotify /me returned HTTP %d", resp.status_code)
        return None
    except Exception:
        logger.debug("Failed to contact Spotify /me", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# POST /config
# ---------------------------------------------------------------------------


@router.post("/config", response_model=SpotifyConfigResponse)
async def update_spotify_config(
    body: SpotifyConfigRequest,
    db_manager: Any = Depends(_get_db_manager),
) -> SpotifyConfigResponse:
    """Store the Spotify app client_id in CredentialStore.

    The client_id must be a 32-character lowercase hex string as shown in
    the Spotify Developer Dashboard. A client_secret is not required for
    PKCE flows.

    Raises HTTP 503 when the credential database is unavailable.
    """
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        raise HTTPException(
            status_code=503,
            detail=("Credential database is unavailable. Ensure the database service is running."),
        )

    await cred_store.store(
        _CRED_CLIENT_ID,
        body.client_id,
        category="spotify",
        description="Spotify app client_id for OAuth PKCE flow",
        is_sensitive=False,
    )
    logger.info("Spotify client_id stored in CredentialStore")
    return SpotifyConfigResponse()


# ---------------------------------------------------------------------------
# POST /oauth/start
# ---------------------------------------------------------------------------


@router.post("/oauth/start", response_model=SpotifyOAuthStartResponse)
async def start_spotify_oauth(
    db_manager: Any = Depends(_get_db_manager),
) -> SpotifyOAuthStartResponse:
    """Initiate the Spotify OAuth PKCE authorization flow.

    Generates a PKCE code verifier + S256 challenge, stores them in the
    server-side state store alongside a CSRF token, and returns the full
    Spotify authorization URL.

    Raises HTTP 503 when the credential database is unavailable, and
    HTTP 400 when the Spotify client_id has not been configured yet.
    """
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        raise HTTPException(
            status_code=503,
            detail=("Credential database is unavailable. Ensure the database service is running."),
        )

    client_id = await cred_store.resolve(_CRED_CLIENT_ID)
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Spotify client_id is not configured. "
                "Submit POST /api/connectors/spotify/config first."
            ),
        )

    redirect_uri = _get_redirect_uri()
    code_verifier = _generate_pkce_verifier()
    code_challenge = _derive_pkce_challenge(code_verifier)
    state = _generate_state()

    _store_state(state, code_verifier=code_verifier, redirect_uri=redirect_uri)

    params: dict[str, str] = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": _DEFAULT_SCOPES,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }

    authorization_url = f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"

    logger.info(
        "Spotify OAuth PKCE flow started (state=%s...)",
        state[:8],
    )

    return SpotifyOAuthStartResponse(
        authorization_url=authorization_url,
        state=state,
    )


# ---------------------------------------------------------------------------
# GET /oauth/callback
# ---------------------------------------------------------------------------


@router.get("/oauth/callback")
async def spotify_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db_manager: Any = Depends(_get_db_manager),
) -> Response:
    """Handle Spotify's OAuth callback.

    Validates the CSRF state, exchanges the authorization code + PKCE
    verifier for tokens, stores them in CredentialStore, and redirects
    to the dashboard (or returns JSON if OAUTH_DASHBOARD_URL is not set).

    Spotify sends ``?error=access_denied`` if the user cancels authorization.
    """
    dashboard_url = _get_dashboard_url()

    # Handle user denial or provider error
    if error:
        logger.warning("Spotify OAuth returned error: %s", error)
        if dashboard_url:
            return RedirectResponse(
                url=_build_redirect_url(dashboard_url, spotify_error=error),
                status_code=302,
            )
        raise HTTPException(
            status_code=400,
            detail=f"Spotify authorization denied: {error}",
        )

    # Validate required parameters
    if not code or not state:
        raise HTTPException(
            status_code=400,
            detail="Missing required callback parameters: code and state are required.",
        )

    # Validate and consume CSRF state (one-time-use)
    state_entry = _validate_and_consume_state(state)
    if state_entry is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "Invalid or expired state token. "
                "The authorization session may have timed out. "
                "Retry POST /api/connectors/spotify/oauth/start."
            ),
        )

    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        raise HTTPException(
            status_code=503,
            detail="Credential database is unavailable during token exchange.",
        )

    client_id = await cred_store.resolve(_CRED_CLIENT_ID)
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Spotify client_id is not configured. "
                "Submit POST /api/connectors/spotify/config first."
            ),
        )

    # Exchange code for tokens
    try:
        token_data = await _exchange_code_for_tokens(
            code=code,
            code_verifier=state_entry.code_verifier,
            redirect_uri=state_entry.redirect_uri,
            client_id=client_id,
        )
    except _TokenExchangeError as exc:
        logger.error("Spotify token exchange failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to exchange authorization code for tokens. "
                "Retry POST /api/connectors/spotify/oauth/start."
            ),
        ) from exc

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    granted_scope = token_data.get("scope", "")

    if not access_token:
        raise HTTPException(
            status_code=502,
            detail="Spotify token response did not include an access_token.",
        )

    # Calculate absolute expiry timestamp
    expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))
    expires_at_iso = expires_at.isoformat()

    # Persist tokens
    await cred_store.store(
        _CRED_ACCESS_TOKEN,
        access_token,
        category="spotify",
        description="Spotify OAuth access token",
        is_sensitive=True,
    )
    if refresh_token:
        await cred_store.store(
            _CRED_REFRESH_TOKEN,
            refresh_token,
            category="spotify",
            description="Spotify OAuth refresh token",
            is_sensitive=True,
        )
    await cred_store.store(
        _CRED_TOKEN_EXPIRES_AT,
        expires_at_iso,
        category="spotify",
        description="Spotify access token expiry (ISO 8601 UTC)",
        is_sensitive=False,
    )
    if granted_scope:
        await cred_store.store(
            _CRED_GRANTED_SCOPES,
            granted_scope,
            category="spotify",
            description="Spotify OAuth granted scopes (space-separated)",
            is_sensitive=False,
        )

    logger.info(
        "Spotify OAuth tokens stored (expires_at=%s, has_refresh=%s, scopes=%r)",
        expires_at_iso,
        bool(refresh_token),
        granted_scope,
    )

    if dashboard_url:
        return RedirectResponse(
            url=_build_redirect_url(dashboard_url, spotify_connected="1"),
            status_code=302,
        )

    return JSONResponse(
        content={
            "success": True,
            "message": "Spotify authorization complete. Tokens stored.",
            "expires_at": expires_at_iso,
        }
    )


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=SpotifyStatusResponse)
async def get_spotify_status(
    db_manager: Any = Depends(_get_db_manager),
) -> SpotifyStatusResponse:
    """Return the current Spotify connection state.

    Checks stored credentials in CredentialStore. If an access token is
    present, calls Spotify GET /me to verify it is still valid and to
    surface the user's display name and product tier.

    Returns not_configured when no client_id has been stored.
    Returns needs_auth when a client_id is stored but no tokens exist.
    Returns needs_reauth when tokens exist but granted scopes are insufficient.
    Returns connected when GET /me succeeds and all required scopes are granted.
    Returns error when tokens are present but GET /me fails (token refresh /
    verification failure requiring re-authorization).
    """
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        return SpotifyStatusResponse(
            connected=False,
            state=SpotifyConnectionState.not_configured,
        )

    client_id = await cred_store.resolve(_CRED_CLIENT_ID)
    if not client_id:
        return SpotifyStatusResponse(
            connected=False,
            state=SpotifyConnectionState.not_configured,
        )

    access_token = await cred_store.resolve(_CRED_ACCESS_TOKEN)
    if not access_token:
        return SpotifyStatusResponse(
            connected=False,
            state=SpotifyConnectionState.needs_auth,
        )

    # Check for scope mismatch before verifying connectivity
    granted_scopes_str = await cred_store.resolve(_CRED_GRANTED_SCOPES)
    if granted_scopes_str is not None:
        granted_scopes = frozenset(granted_scopes_str.split())
        missing_scopes = sorted(_REQUIRED_SCOPES - granted_scopes)
        if missing_scopes:
            return SpotifyStatusResponse(
                connected=False,
                state=SpotifyConnectionState.needs_reauth,
                needs_reauth=True,
                missing_scopes=missing_scopes,
                error="Spotify authorization is missing required permissions. Re-authorize.",
            )

    # Verify token against Spotify API
    me_data = await _fetch_spotify_me(access_token)
    if me_data is None:
        return SpotifyStatusResponse(
            connected=False,
            state=SpotifyConnectionState.error,
            needs_reauth=True,
            error="Spotify token verification failed. Re-connect your account.",
        )

    return SpotifyStatusResponse(
        connected=True,
        state=SpotifyConnectionState.connected,
        spotify_user_id=me_data.get("id"),
        display_name=me_data.get("display_name"),
        account_type=me_data.get("product"),
        last_sync_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# POST /disconnect
# ---------------------------------------------------------------------------


@router.post("/disconnect", response_model=SpotifyDisconnectResponse)
async def disconnect_spotify(
    db_manager: Any = Depends(_get_db_manager),
) -> SpotifyDisconnectResponse:
    """Revoke Spotify credentials and delete all tokens from CredentialStore.

    Deletes SPOTIFY_ACCESS_TOKEN, SPOTIFY_REFRESH_TOKEN, and
    SPOTIFY_TOKEN_EXPIRES_AT. Preserves SPOTIFY_CLIENT_ID so the user
    does not need to re-enter it when reconnecting.

    Returns success=True even when no credentials were stored (idempotent).
    """
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        # No DB — nothing to delete; treat as success
        logger.info("Disconnect requested but credential store is unavailable; treating as success")
        return SpotifyDisconnectResponse()

    deleted_count = 0
    for key in _TOKEN_CRED_KEYS:
        if await cred_store.delete(key):
            deleted_count += 1

    logger.info("Spotify disconnect: deleted %d credential key(s)", deleted_count)
    return SpotifyDisconnectResponse()
