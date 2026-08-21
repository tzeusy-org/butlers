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
     - Stores access_token, refresh_token, and expires_at as secured owner entity_info.
     - Redirects to OAUTH_DASHBOARD_URL if configured, else returns JSON.

  4. GET /api/connectors/spotify/status
     - Checks stored credentials.
     - If present, calls Spotify GET /me to verify connectivity.
     - Returns SpotifyConnectionState plus user info.

  5. POST /api/connectors/spotify/disconnect
     - Clears locally stored OAuth tokens and recorded scopes while preserving
       client_id for a later reconnect; it does not revoke access at Spotify.

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
from butlers.core.credential_keys import normalize_credential_key
from butlers.credential_store import (
    CredentialStore,
    resolve_owner_entity_info,
    upsert_owner_entity_info_on_connection,
)
from butlers.spotify_credentials import (
    SPOTIFY_OAUTH_ACCESS,
    SPOTIFY_OAUTH_EXPIRES_AT,
    SPOTIFY_OAUTH_REFRESH,
    SpotifyTokenResponseError,
    parse_spotify_token_response,
)

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

# Tier 1 application configuration key used in CredentialStore
_CRED_CLIENT_ID = "SPOTIFY_CLIENT_ID"

# Token-only keys that are cleared on disconnect (client_id is preserved).
_TOKEN_INFO_TYPES = (
    SPOTIFY_OAUTH_ACCESS,
    SPOTIFY_OAUTH_REFRESH,
    SPOTIFY_OAUTH_EXPIRES_AT,
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


def _secrets_redirect(base_url: str, **params: str) -> str:
    """Build the post-callback redirect to the Spotify credential card.

    The dance is driven from that card's drawer, so it is also where the user
    is returned — with the same ``?toast=`` / ``?oauth_error=`` params the
    generalized OAuth callback uses, which the Secrets page already surfaces as
    a toast and strips. The dashboard root used to be the destination, flagged
    with a ``spotify_connected=1`` param no frontend code ever read, which left
    an authorization landing away from the surface that started it.

    *base_url* is the frontend base (``OAUTH_DASHBOARD_URL``); any query it
    already carries is preserved by merging rather than concatenating, and all
    values are URL-encoded to prevent query-string injection.
    """
    focus = normalize_credential_key("user", "spotify")
    parsed = urlparse(base_url)
    existing = parse_qs(parsed.query, keep_blank_values=True)
    merged: dict[str, Any] = {k: v[0] if len(v) == 1 else v for k, v in existing.items()}
    merged["focus"] = focus
    merged.update(params)
    return urlunparse(
        parsed._replace(
            path=parsed.path.rstrip("/") + "/secrets",
            query=urlencode(merged, doseq=True),
        )
    )


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------


class _TokenExchangeError(Exception):
    """Raised when Spotify token exchange fails."""

    def __init__(self, reason: str, status_code: int | None = None) -> None:
        messages = {
            "network": "Spotify token exchange transport failed.",
            "http": "Spotify token exchange was rejected.",
            "malformed": "Spotify token exchange returned an unexpected response.",
        }
        self.reason = reason if reason in messages else "unknown"
        super().__init__(messages.get(self.reason, "Spotify token exchange failed."))
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
    except httpx.RequestError:
        raise _TokenExchangeError("network") from None

    if resp.status_code != 200:
        raise _TokenExchangeError(
            "http",
            status_code=resp.status_code,
        )

    try:
        return resp.json()
    except Exception:
        raise _TokenExchangeError("malformed", status_code=resp.status_code) from None


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


async def _store_granted_scopes_on_connection(
    conn: Any, endpoint_identity: str, scopes: set[str]
) -> None:
    """Persist derived scope state in the same transaction as OAuth authority."""
    await conn.execute(
        """
        INSERT INTO switchboard.connector_registry (
            connector_type, endpoint_identity, state, registered_via,
            observed_scopes, observed_scopes_fetched_at, required_scopes_version
        ) VALUES ('spotify', $1, 'unknown', 'dashboard', $2::text[], now(), 1)
        ON CONFLICT (connector_type, endpoint_identity) DO UPDATE SET
            observed_scopes = EXCLUDED.observed_scopes,
            observed_scopes_fetched_at = EXCLUDED.observed_scopes_fetched_at,
            required_scopes_version = EXCLUDED.required_scopes_version
        """,
        endpoint_identity,
        sorted(scopes),
    )


async def _load_granted_scopes(pool: Any) -> set[str] | None:
    """Read the newest derived Spotify scope observation, if one exists."""
    try:
        row = await pool.fetchrow(
            """
            SELECT observed_scopes
            FROM switchboard.connector_registry
            WHERE connector_type = 'spotify'
              AND observed_scopes IS NOT NULL
            ORDER BY observed_scopes_fetched_at DESC NULLS LAST
            LIMIT 1
            """
        )
    except (AttributeError, TypeError):
        # Compatibility for deployments/tests whose shared pool predates the
        # derived connector-registry surface. Token authority remains Tier 2.
        return None
    if row is None:
        return None
    return set(row["observed_scopes"] or [])


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
    verifier for tokens, stores them in secured owner entity_info, and redirects to the
    Spotify credential card on /secrets — the surface whose drawer starts this
    dance — carrying the same ``?toast=connected`` / ``?oauth_error=<code>``
    params the generalized OAuth callback uses (see ``_secrets_redirect``).
    Returns JSON instead if OAUTH_DASHBOARD_URL is not set.

    Spotify sends ``?error=access_denied`` if the user cancels authorization.
    """
    dashboard_url = _get_dashboard_url()

    # Handle user denial or provider error
    if error:
        logger.warning("Spotify OAuth returned a provider error")
        if dashboard_url:
            return RedirectResponse(
                url=_secrets_redirect(dashboard_url, toast="connection_failed"),
                status_code=302,
            )
        raise HTTPException(
            status_code=400,
            detail="spotify_authorization_failed",
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
            detail="spotify_state_invalid",
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
        token_response = parse_spotify_token_response(
            token_data,
            require_refresh_token=True,
            require_scope=True,
            require_token_type=True,
        )
    except SpotifyTokenResponseError:
        exc = _TokenExchangeError("malformed")
        logger.error(
            "Spotify token exchange failed (reason=%s, status=%s)",
            exc.reason,
            exc.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to exchange authorization code for tokens. "
                "Retry POST /api/connectors/spotify/oauth/start."
            ),
        ) from None
    except _TokenExchangeError as exc:
        logger.error(
            "Spotify token exchange failed (reason=%s, status=%s)",
            exc.reason,
            exc.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to exchange authorization code for tokens. "
                "Retry POST /api/connectors/spotify/oauth/start."
            ),
        ) from exc

    access_token = token_response.access_token
    refresh_token = token_response.refresh_token
    expires_in = token_response.expires_in
    granted_scope = token_response.scope
    assert refresh_token is not None
    assert granted_scope is not None

    profile = await _fetch_spotify_me(access_token)
    spotify_user_id = profile.get("id") if isinstance(profile, dict) else None
    if not isinstance(spotify_user_id, str) or not spotify_user_id:
        raise HTTPException(status_code=502, detail="spotify_token_verification_failed")

    # Calculate absolute expiry timestamp
    expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))
    expires_at_iso = expires_at.isoformat()

    # OAuth token authority is the owner entity, never CredentialStore.
    pool = cred_store.pool
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                stored = [
                    await upsert_owner_entity_info_on_connection(
                        conn, SPOTIFY_OAUTH_ACCESS, access_token, secured=True
                    ),
                    await upsert_owner_entity_info_on_connection(
                        conn, SPOTIFY_OAUTH_REFRESH, refresh_token, secured=True
                    ),
                    await upsert_owner_entity_info_on_connection(
                        conn, SPOTIFY_OAUTH_EXPIRES_AT, expires_at_iso, secured=True
                    ),
                ]
                if not all(stored):
                    raise RuntimeError("owner credential upsert failed")
                await _store_granted_scopes_on_connection(
                    conn, f"spotify:{spotify_user_id}", set(granted_scope.split())
                )
    except Exception as exc:
        logger.error("Spotify OAuth authority transaction failed", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Owner credential authority is unavailable.",
        ) from exc

    logger.info(
        "Spotify OAuth state stored (expires_at=%s, has_refresh=%s, scopes_granted=%s)",
        expires_at_iso,
        bool(refresh_token),
        bool(granted_scope),
    )

    if dashboard_url:
        return RedirectResponse(
            url=_secrets_redirect(dashboard_url, toast="connected"),
            status_code=302,
        )

    return JSONResponse(
        content={
            "success": True,
            "message": "Spotify authorization complete.",
        }
    )


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=SpotifyStatusResponse)
async def get_spotify_status(
    db_manager: Any = Depends(_get_db_manager),
) -> SpotifyStatusResponse:
    """Return a fixed, non-sensitive projection of connector-owned state."""
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        return SpotifyStatusResponse(
            connected=False,
            state=SpotifyConnectionState.unconfigured,
        )

    client_id = await cred_store.resolve(_CRED_CLIENT_ID)
    if not client_id:
        return SpotifyStatusResponse(
            connected=False,
            state=SpotifyConnectionState.unconfigured,
        )

    access_token = await resolve_owner_entity_info(cred_store.pool, SPOTIFY_OAUTH_ACCESS)
    refresh_token = await resolve_owner_entity_info(cred_store.pool, SPOTIFY_OAUTH_REFRESH)
    expires_at_raw = await resolve_owner_entity_info(cred_store.pool, SPOTIFY_OAUTH_EXPIRES_AT)
    if not access_token and not refresh_token and not expires_at_raw:
        return SpotifyStatusResponse(
            connected=False,
            state=SpotifyConnectionState.authorization_needed,
        )
    if not access_token or not refresh_token or not expires_at_raw:
        return SpotifyStatusResponse(
            connected=False,
            state=SpotifyConnectionState.error,
        )
    try:
        expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
    except ValueError:
        return SpotifyStatusResponse(
            connected=False,
            state=SpotifyConnectionState.error,
        )
    if expires_at <= datetime.now(UTC):
        return SpotifyStatusResponse(
            connected=False,
            state=SpotifyConnectionState.needs_reauth,
        )
    granted_scopes = await _load_granted_scopes(cred_store.pool)
    if granted_scopes is not None and not set(_DEFAULT_SCOPES.split()).issubset(granted_scopes):
        return SpotifyStatusResponse(
            connected=False,
            state=SpotifyConnectionState.needs_reauth,
        )
    if await _fetch_spotify_me(access_token) is None:
        return SpotifyStatusResponse(
            connected=False,
            state=SpotifyConnectionState.error,
        )

    return SpotifyStatusResponse(
        connected=True,
        state=SpotifyConnectionState.connected,
    )


# ---------------------------------------------------------------------------
# POST /disconnect
# ---------------------------------------------------------------------------


@router.post("/disconnect", response_model=SpotifyDisconnectResponse)
async def disconnect_spotify(
    db_manager: Any = Depends(_get_db_manager),
) -> SpotifyDisconnectResponse:
    """Clear connector-owned Spotify OAuth state from owner entity_info.

    Preserves SPOTIFY_CLIENT_ID so the user does not need to re-enter it when reconnecting.
    Does not call Spotify or revoke provider-side authorization.

    Returns success only after the reachable owner authority executes the
    atomic delete, including when it finds no stored credentials.
    """
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        logger.error("Spotify disconnect failed: owner credential authority unavailable")
        raise HTTPException(
            status_code=503,
            detail="Owner credential authority is unavailable.",
        )

    try:
        deleted_count = await _delete_spotify_oauth_rows(cred_store.pool)
    except Exception:
        logger.error("Spotify disconnect failed: owner credential authority transaction failed")
        raise HTTPException(
            status_code=503,
            detail="Owner credential authority is unavailable.",
        ) from None

    logger.info("Spotify disconnect: deleted %d credential key(s)", deleted_count)
    return SpotifyDisconnectResponse()


async def _delete_spotify_oauth_rows(pool: Any) -> int:
    """Delete the exact connector-managed Spotify owner rows atomically."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                DELETE FROM public.entity_info ei
                USING public.entities e
                WHERE ei.entity_id = e.id
                  AND 'owner' = ANY(e.roles)
                  AND ei.type = ANY($1::text[])
                RETURNING ei.type
                """,
                list(_TOKEN_INFO_TYPES),
            )
            await conn.execute(
                """
                UPDATE switchboard.connector_registry
                SET observed_scopes = NULL,
                    observed_scopes_fetched_at = NULL,
                    required_scopes_version = NULL,
                    auth_status = NULL
                WHERE connector_type = 'spotify'
                """
            )
    return len(rows)
