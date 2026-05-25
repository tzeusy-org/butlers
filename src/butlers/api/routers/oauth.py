"""OAuth bootstrap endpoints — Google (legacy) and generalised <provider> routes.

Implements a two-leg OAuth 2.0 authorization-code flow for acquiring OAuth
refresh tokens for use by butler modules.

Route surface
-------------
Legacy Google-specific routes (preserved unchanged for backward compatibility):
  GET /api/oauth/google/start
  GET /api/oauth/google/callback
  GET /api/oauth/status
  GET /api/oauth/google/accounts
  GET /api/oauth/google/accounts/{id}/status
  PUT /api/oauth/google/accounts/{id}/primary
  DELETE /api/oauth/google/accounts/{id}
  PUT /api/oauth/google/credentials
  DELETE /api/oauth/google/credentials
  GET /api/oauth/google/credentials

Generalised per-provider routes (RFC 0007 ApiResponse<T> envelope):
  GET /api/oauth/{provider}/start
      ?redirect_uri=<uri>&account_hint=<hint>&force_consent=<bool>
      &page_of_origin=<page>&scope_set=<sets>
  GET /api/oauth/{provider}/callback
      ?code=<code>&state=<state>[&error=<err>]

The bootstrap flow:
  1. GET /api/oauth/{provider}/start
     - Generates a cryptographically random CSRF state token.
     - Stores state in the in-memory store (TTL 10 min) carrying
       ``page_of_origin`` for cross-page reauth bookkeeping.
     - Writes an ``attempted`` audit row to ``public.audit_log`` BEFORE redirect.
     - Returns ApiResponse<{ authorization_url }> or 302.

  2. GET /api/oauth/{provider}/callback
     - Validates state, exchanges code for tokens.
     - Persists credentials; writes ``connected`` (success) or ``failed`` audit row.
     - Redirects based on ``state.page_of_origin``:
         "secrets"   → /secrets?focus=u:<provider>&toast=connected
         "ingestion" → /ingestion/connectors
         (default)   → /secrets?focus=u:<provider>&toast=connected

Provider registry
-----------------
Providers are registered in ``_PROVIDER_REGISTRY`` keyed by provider name.
Each entry is a ``_ProviderConfig`` dataclass describing auth/token URLs,
scope-sets, default scopes, and redirect-URI env-var name.

Currently registered: ``google``, ``spotify``.

Environment variables:
  GOOGLE_OAUTH_REDIRECT_URI  — Callback URL registered with Google
                               (default: http://localhost:41200/api/oauth/google/callback)
  SPOTIFY_OAUTH_REDIRECT_URI — Callback URL registered with Spotify
                               (default: http://localhost:41200/api/oauth/spotify/callback)
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
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse, Response

import butlers.api.routers.audit as _audit
from butlers.api.models import ApiResponse
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
from butlers.core.credential_keys import normalize_credential_key
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

_DEFAULT_REDIRECT_URI = "http://localhost:41200/api/oauth/google/callback"

# ---------------------------------------------------------------------------
# Named scope-set registry
# ---------------------------------------------------------------------------
#
# `scope_set` query param on /google/start selects one or more named sets.
# Each set maps to a list of fully-qualified Google OAuth scope URLs.
#
# Google Health scopes (in the 'health' set below) are classified RESTRICTED
# by Google and require a one-time privacy and security review of the OAuth
# client before they can be granted in production mode. Test mode is
# sufficient for single-developer / single-user self-hosting, subject to a
# 7-day refresh token expiry — the OAuth callback records a metadata flag
# on the google_accounts row so the dashboard can surface a warning banner.
# See: https://developers.google.com/health/about

GOOGLE_SCOPE_SETS: dict[str, list[str]] = {
    # Identity basics — always included implicitly so userinfo calls succeed.
    "base": [
        "openid",
        "email",
        "profile",
    ],
    "calendar": [
        "https://www.googleapis.com/auth/calendar",
    ],
    "drive": [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive",
    ],
    "gmail": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ],
    "contacts": [
        "https://www.googleapis.com/auth/contacts",
        "https://www.googleapis.com/auth/contacts.readonly",
        "https://www.googleapis.com/auth/contacts.other.readonly",
        "https://www.googleapis.com/auth/directory.readonly",
    ],
    # RESTRICTED scopes — require Google privacy/security review for
    # production mode. Test mode (developer-added users) does not require
    # verification but has a 7-day refresh token expiry.
    "health": [
        "https://www.googleapis.com/auth/googlehealth.sleep",
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
    ],
}

# Default scope composition when no scope_set query param is provided.
# Matches pre-change behaviour: gmail + calendar + contacts + drive + base.
# Existing callers (Calendar/Drive/Gmail bring-up) get the same scope string
# they got before the scope_set selector was introduced.
_DEFAULT_SCOPE_SETS: tuple[str, ...] = ("base", "gmail", "calendar", "contacts", "drive")
_DEFAULT_SCOPES = " ".join(
    dict.fromkeys(
        scope for set_name in _DEFAULT_SCOPE_SETS for scope in GOOGLE_SCOPE_SETS[set_name]
    )
)

# Required scopes for full butler functionality.
_REQUIRED_SCOPES = frozenset(
    [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar",
    ]
)

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
#
# Each provider entry describes the OAuth endpoints, scope-sets, default
# redirect-URI, and redirect-URI env-var override.  New providers are added
# here and picked up automatically by the generalised /{provider}/start and
# /{provider}/callback routes.


@dataclass
class _ProviderConfig:
    """Static configuration for one OAuth provider."""

    auth_url: str
    """Authorization endpoint URL."""

    token_url: str
    """Token exchange endpoint URL."""

    scope_sets: dict[str, list[str]]
    """Named scope-set registry for this provider."""

    default_scope_sets: tuple[str, ...]
    """Scope-set names used when no ``scope_set`` query param is supplied."""

    default_redirect_uri: str
    """Fallback redirect URI when the env-var override is absent."""

    redirect_uri_env_var: str
    """Environment variable name that overrides the default redirect URI."""

    client_id_key: str = "GOOGLE_OAUTH_CLIENT_ID"
    """butler_secrets key for the OAuth app client ID."""

    client_secret_key: str = "GOOGLE_OAUTH_CLIENT_SECRET"
    """butler_secrets key for the OAuth app client secret."""

    userinfo_url: str | None = None
    """Userinfo endpoint; None for providers that do not expose one (e.g. Spotify)."""

    # Spotify: user profile URL plays the role of a userinfo endpoint.
    profile_url: str | None = None
    """Optional profile endpoint for providers that use a different mechanism."""


# ---------------------------------------------------------------------------
# Spotify scope-set registry
# ---------------------------------------------------------------------------
#
# Spotify uses opaque scope strings (not URLs).  The ``base`` set provides
# minimal identity so the /me call succeeds; downstream butlers add music/
# listening-history scopes.
SPOTIFY_SCOPE_SETS: dict[str, list[str]] = {
    "base": [
        "user-read-email",
        "user-read-private",
    ],
    "listening_history": [
        "user-read-recently-played",
        "user-top-read",
    ],
    "playback": [
        "user-read-playback-state",
        "user-modify-playback-state",
        "user-read-currently-playing",
    ],
    "library": [
        "user-library-read",
        "user-library-modify",
    ],
    "playlists": [
        "playlist-read-private",
        "playlist-read-collaborative",
        "playlist-modify-public",
        "playlist-modify-private",
    ],
}

_SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SPOTIFY_PROFILE_URL = "https://api.spotify.com/v1/me"
_DEFAULT_SPOTIFY_REDIRECT_URI = "http://localhost:41200/api/oauth/spotify/callback"

_PROVIDER_REGISTRY: dict[str, _ProviderConfig] = {
    "google": _ProviderConfig(
        auth_url=GOOGLE_AUTH_URL,
        token_url=GOOGLE_TOKEN_URL,
        scope_sets=GOOGLE_SCOPE_SETS,
        default_scope_sets=_DEFAULT_SCOPE_SETS,
        default_redirect_uri=_DEFAULT_REDIRECT_URI,
        redirect_uri_env_var="GOOGLE_OAUTH_REDIRECT_URI",
        userinfo_url=GOOGLE_USERINFO_URL,
    ),
    "spotify": _ProviderConfig(
        auth_url=_SPOTIFY_AUTH_URL,
        token_url=_SPOTIFY_TOKEN_URL,
        scope_sets=SPOTIFY_SCOPE_SETS,
        default_scope_sets=("base",),
        default_redirect_uri=_DEFAULT_SPOTIFY_REDIRECT_URI,
        redirect_uri_env_var="SPOTIFY_OAUTH_REDIRECT_URI",
        client_id_key="SPOTIFY_OAUTH_CLIENT_ID",
        client_secret_key="SPOTIFY_OAUTH_CLIENT_SECRET",
        userinfo_url=None,
        profile_url=_SPOTIFY_PROFILE_URL,
    ),
}


def _get_provider_config(provider: str) -> _ProviderConfig | None:
    """Return the _ProviderConfig for *provider*, or None if unknown."""
    return _PROVIDER_REGISTRY.get(provider)


def _get_provider_redirect_uri(provider_cfg: _ProviderConfig) -> str:
    """Read the provider-specific redirect-URI env-var or use the default."""
    return os.environ.get(
        provider_cfg.redirect_uri_env_var, provider_cfg.default_redirect_uri
    ).strip()


async def _resolve_provider_credentials(
    provider_cfg: _ProviderConfig,
    db_manager: Any,
) -> tuple[str, str]:
    """Resolve client_id and client_secret for *provider_cfg* from DB-backed storage.

    Uses the provider's ``client_id_key`` and ``client_secret_key`` fields so
    that each provider reads its own credentials rather than Google's.

    Raises HTTPException(503) when the credential store is unavailable or the
    provider's credentials are not configured.
    """
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        raise HTTPException(
            status_code=503,
            detail="Shared credential database is unavailable.",
        )

    client_id = await cred_store.load(provider_cfg.client_id_key)
    client_secret = await cred_store.load(provider_cfg.client_secret_key)

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail=(
                f"OAuth app credentials for this provider are not configured in DB. "
                f"Configure {provider_cfg.client_id_key} and {provider_cfg.client_secret_key} "
                f"on the Secrets page."
            ),
        )
    return client_id, client_secret


def _compose_provider_default_scopes(provider_cfg: _ProviderConfig) -> str:
    """Build the default scope string for a provider from its default_scope_sets."""
    return " ".join(
        dict.fromkeys(
            scope
            for set_name in provider_cfg.default_scope_sets
            for scope in provider_cfg.scope_sets[set_name]
        )
    )


def _compose_provider_scopes_from_sets(provider_cfg: _ProviderConfig, set_names: list[str]) -> str:
    """Compose an OAuth scope string from the named sets for a given provider.

    Includes 'base' implicitly when it exists in the provider's scope_sets.
    Raises ValueError with the first unknown set name.
    """
    unknown = [name for name in set_names if name not in provider_cfg.scope_sets]
    if unknown:
        raise ValueError(unknown[0])

    # 'base' is always implicitly included when defined for the provider.
    has_base = "base" in provider_cfg.scope_sets
    if has_base:
        ordered_sets = ["base", *set_names] if "base" not in set_names else list(set_names)
    else:
        ordered_sets = list(set_names)

    scopes: dict[str, None] = {}
    for set_name in ordered_sets:
        for scope in provider_cfg.scope_sets[set_name]:
            scopes.setdefault(scope, None)
    return " ".join(scopes)


# ---------------------------------------------------------------------------
# Callback redirect helpers
# ---------------------------------------------------------------------------

_PAGE_OF_ORIGIN_DEFAULT = "secrets"


def _build_success_redirect_url(provider: str, page_of_origin: str | None) -> str:
    """Compute the post-OAuth-success redirect destination.

    Routing table:
      "secrets"    → /secrets?focus=u:<provider>&toast=connected
      "ingestion"  → /ingestion/connectors
      (None / any) → /secrets?focus=u:<provider>&toast=connected  (default)
    """
    resolved_page = page_of_origin or _PAGE_OF_ORIGIN_DEFAULT
    if resolved_page == "ingestion":
        return "/ingestion/connectors"
    cred_key = normalize_credential_key("user", provider)
    return f"/secrets?focus={cred_key}&toast=connected"


def _build_error_redirect_url(provider: str, page_of_origin: str | None, error_code: str) -> str:
    """Compute the post-OAuth-error redirect destination."""
    resolved_page = page_of_origin or _PAGE_OF_ORIGIN_DEFAULT
    if resolved_page == "ingestion":
        return f"/ingestion/connectors?oauth_error={error_code}"
    cred_key = normalize_credential_key("user", provider)
    return f"/secrets?focus={cred_key}&oauth_error={error_code}"


def _parse_scope_set_param(raw: str | None) -> list[str] | None:
    """Parse a `scope_set` query value into a list of set names.

    Accepts either a single name (``scope_set=health``) or a comma-separated
    list (``scope_set=calendar,drive,health``). Whitespace around names is
    trimmed. Empty entries are dropped.

    Returns ``None`` when the input is ``None`` or empty after trimming, which
    signals "no scope_set supplied — fall back to default scope composition"
    for backward compatibility with callers that do not use the selector.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    return [name for name in (part.strip() for part in stripped.split(",")) if name]


def _compose_scopes_from_sets(set_names: list[str]) -> str:
    """Compose an OAuth scope string from the named sets, always including 'base'.

    Deduplicates while preserving first-occurrence order. Raises ``ValueError``
    when any requested set name is unknown — the caller converts that into a
    400 response with actionable JSON.
    """
    unknown = [name for name in set_names if name not in GOOGLE_SCOPE_SETS]
    if unknown:
        raise ValueError(unknown[0])

    # 'base' is always implicitly included so userinfo calls succeed.
    ordered_sets = ["base", *set_names] if "base" not in set_names else list(set_names)

    # dict.fromkeys preserves first-occurrence order across sets while dropping duplicates.
    scopes: dict[str, None] = {}
    for set_name in ordered_sets:
        for scope in GOOGLE_SCOPE_SETS[set_name]:
            scopes.setdefault(scope, None)
    return " ".join(scopes)


def _widen_scopes(scope_str: str, granted_scopes: list[str]) -> str:
    """Union ``granted_scopes`` into ``scope_str`` (scope-widening, never scope-replacement).

    Preserves the original scope order and appends any previously-granted scopes
    that are not yet present in the requested scope string.  The result is always
    a superset of ``scope_str`` — scopes are never removed.

    Parameters
    ----------
    scope_str:
        Space-separated OAuth scope string derived from the requested scope_set.
    granted_scopes:
        Scopes already stored in ``public.google_accounts.granted_scopes`` for
        the hinted account.  Only this account's scopes are unioned — cross-account
        scope leakage is prevented by the caller.

    Returns
    -------
    str
        Widened space-separated OAuth scope string.
    """
    # dict.fromkeys preserves insertion order while deduplicating.
    merged: dict[str, None] = dict.fromkeys(scope_str.split())
    for scope in granted_scopes:
        merged.setdefault(scope, None)
    return " ".join(merged)


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

    page_of_origin: str | None = None
    """Page that initiated the OAuth dance; used by callback to route the redirect.

    Known values: ``"secrets"`` → /secrets page, ``"ingestion"`` → /ingestion/connectors.
    Absent/None defaults to the ``"secrets"`` return path.
    """

    provider: str = field(default="google")
    """OAuth provider identifier (e.g. ``"google"``, ``"spotify"``)."""

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
    page_of_origin: str | None = None,
    provider: str = "google",
) -> None:
    """Store a state token with an expiry timestamp and optional account context."""
    _state_store[state] = _StateEntry(
        expiry=time.monotonic() + _STATE_TTL_SECONDS,
        account_hint=account_hint,
        force_consent=force_consent,
        page_of_origin=page_of_origin,
        provider=provider,
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


def _is_google_health_test_mode() -> bool:
    """Return True when the OAuth client is configured in test mode.

    Detection strategy: explicit config flag GOOGLE_OAUTH_CLIENT_TEST_MODE.

    This is option (a) from the design choices — a simple, explicit environment
    variable that self-hosted deployments set when they register an OAuth client
    under a project still in Google's "Testing" publishing status.  The
    alternative approaches (Cloud Console API probe or refresh-token TTL
    heuristic) were rejected:
      - Cloud Console API adds an extra authenticated HTTP round-trip and
        requires additional IAM permissions not part of the standard OAuth flow.
      - TTL heuristics are fragile because Google does not expose token
        expiry deterministically in the token-exchange response.
    """
    val = os.environ.get("GOOGLE_OAUTH_CLIENT_TEST_MODE", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _has_health_scope(scope_str: str | None) -> bool:
    """Return True when the granted scope list contains any Google Health scope.

    Google Health scopes share the URL prefix ``https://www.googleapis.com/auth/fitness``
    or the ``https://www.googleapis.com/auth/health.*`` / ``googlehealth.*`` family.
    We match any scope that contains ``googlehealth`` or starts with the fitness
    API prefix, covering both the legacy Fitness REST API and the newer Health
    Connect scopes.
    """
    if not scope_str:
        return False
    for scope in scope_str.split():
        s = scope.lower()
        if "googlehealth" in s or s.startswith("https://www.googleapis.com/auth/fitness"):
            return True
    return False


async def _set_account_health_test_mode(
    pool: Any,
    *,
    entity_id: uuid.UUID,
) -> None:
    """Set metadata.google_health_test_mode = true on the google_accounts row.

    Uses jsonb_set() so other metadata keys are preserved.  The operation is
    idempotent — running the callback a second time leaves the row unchanged.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.google_accounts
            SET metadata = jsonb_set(
                metadata,
                '{google_health_test_mode}',
                'true'::jsonb,
                true
            )
            WHERE entity_id = $1
            """,
            entity_id,
        )


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
    scope_set: str | None = Query(
        default=None,
        description="Optional named scope set(s) to include in the authorization URL. "
        "Accepts a single name (e.g. 'health') or a comma-separated list "
        "(e.g. 'calendar,drive,health'). The 'base' set (openid/email/profile) is "
        "always included implicitly. When omitted, falls back to the pre-existing "
        "default scope composition (base+gmail+calendar+contacts+drive) for "
        "backward compatibility with callers that do not use the selector.",
    ),
    page_of_origin: str | None = Query(
        default=None,
        description="Optional page that initiated the OAuth flow. "
        "Known values: 'secrets' and 'ingestion'. "
        "When present, the value is carried in the CSRF state token so the callback "
        "can route the user back to the originating page. "
        "Missing or empty is treated as the 'secrets' default at callback time.",
    ),
    db_manager: Any = Depends(_get_db_manager),
) -> Response:
    """Begin the Google OAuth authorization flow.

    Generates a CSRF state token, stores it in the in-memory state store,
    builds the Google authorization URL, and either redirects the browser
    or returns the URL as JSON (when ``?redirect=false``).

    Supports multi-account flows via ``account_hint`` (pre-selects account)
    and ``force_consent`` (forces refresh token re-issuance for scope upgrades).

    The ``scope_set`` parameter selects one or more named scope sets from
    ``GOOGLE_SCOPE_SETS``. Unknown set names return HTTP 400 with an
    actionable JSON error. Omitting ``scope_set`` is identical to the
    pre-change behaviour so existing Calendar/Drive/Gmail callers are
    not broken.
    """
    # --- Resolve scope composition ---
    # scope_set is parsed BEFORE the account limit check so unknown-set errors
    # do not get masked by a 409 account-limit response.
    requested_sets = _parse_scope_set_param(scope_set)
    if requested_sets is not None:
        try:
            scopes = _compose_scopes_from_sets(requested_sets)
        except ValueError as exc:
            unknown_name = str(exc)
            return JSONResponse(
                status_code=400,
                content={
                    "error": "unknown_scope_set",
                    "scope_set": unknown_name,
                    "known": sorted(GOOGLE_SCOPE_SETS.keys()),
                },
            )
    else:
        scopes = _get_scopes()
    # --- Account limit check ---
    # Only check if this would be a new account (not a re-auth of an existing one).
    # Also capture the existing account's granted_scopes for scope-widening below.
    shared_pool = _get_shared_pool(db_manager)
    _hinted_account_granted_scopes: list[str] | None = None
    if shared_pool is not None and account_hint:
        # Check if this email already exists — if it does, it's a re-auth, skip limit check.
        try:
            existing = await get_google_account(shared_pool, account=account_hint)
            # Account exists — re-auth, no limit check needed.
            # Capture granted_scopes for scope-widening (scope-set requests only).
            _hinted_account_granted_scopes = list(existing.granted_scopes)
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

    # --- Scope-widening: union granted_scopes from the hinted account ---
    # When a scope_set is explicitly requested and the hinted account already has
    # granted scopes, union those into the requested scope set so that re-auth to
    # add a new scope set never downgrades previously-granted scopes for other sets.
    # Only applies when scope_set was provided; backward-compat (no scope_set) path
    # is left unchanged.  Cross-account scope leakage is prevented because we only
    # union the *hinted account's* own granted_scopes.
    if requested_sets is not None and _hinted_account_granted_scopes:
        scopes = _widen_scopes(scopes, _hinted_account_granted_scopes)

    client_id, _ = await _resolve_app_credentials(db_manager)
    redirect_uri = _get_redirect_uri()

    state = _generate_state()
    page_of_origin = (page_of_origin or "").strip() or None
    _store_state(
        state,
        account_hint=account_hint,
        force_consent=force_consent,
        page_of_origin=page_of_origin,
    )

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
        "Google OAuth flow started (state=%s..., account_hint=%s, force_consent=%s, "
        "scope_set=%s, page_of_origin=%s)",
        state[:8],
        account_hint,
        force_consent,
        requested_sets,
        page_of_origin,
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
    resolved_entity_id: uuid.UUID | None = None

    if shared_pool is not None and account_email:
        # Try to find existing account.
        try:
            existing_account = await get_google_account(shared_pool, account=account_email)
            # Account exists — update credentials.
            is_new_account = False
            resolved_entity_id = existing_account.entity_id
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
                new_account = await create_google_account(
                    shared_pool,
                    email=account_email,
                    display_name=account_display_name,
                    scopes=scope_list,
                    refresh_token=refresh_token,
                )
                resolved_entity_id = new_account.entity_id
            except GoogleAccountAlreadyExistsError:
                # Race condition — treat as re-auth.
                is_new_account = False
                existing_account = await get_google_account(shared_pool, account=account_email)
                resolved_entity_id = existing_account.entity_id
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

    # --- Google Health test-mode metadata flag ---
    # When the OAuth client is in test mode (GOOGLE_OAUTH_CLIENT_TEST_MODE=true) AND
    # the granted scope list includes a Google Health scope, record this on the account
    # row so the dashboard can surface an expiry warning (refresh tokens expire in 7 days
    # for unverified apps).  The write is idempotent and best-effort — failures are
    # logged but do not abort the callback.
    if shared_pool is not None and resolved_entity_id is not None:
        if _is_google_health_test_mode() and _has_health_scope(scope):
            try:
                await _set_account_health_test_mode(shared_pool, entity_id=resolved_entity_id)
                logger.info("Google Health test-mode flag set on entity %s", resolved_entity_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to set google_health_test_mode metadata on entity %s: %s",
                    resolved_entity_id,
                    exc,
                )

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

    # If the grant included Google Health RESTRICTED scopes, pre-register a
    # public.contact_info(type='google_health') row linked to the owner entity
    # so the Switchboard can resolve `sender.identity` on wellness envelopes
    # via a known contact instead of creating a temp contact. Idempotent —
    # re-runs produce no duplicate row. See openspec requirement "Owner
    # Contact Info Registration" in connector-google-health/spec.md.
    if shared_pool is not None and account_email and scope and "googlehealth." in scope:
        try:
            await _register_google_health_contact_info(shared_pool, google_user_id=account_email)
        except Exception as exc:  # noqa: BLE001
            # Non-fatal: the connector will keep running in degraded mode
            # until the contact_info row is present.
            logger.warning("Failed to upsert google_health contact_info (non-fatal): %s", exc)

    # Notify the Gmail connector to reload accounts immediately so it picks up the
    # new/updated refresh token without waiting for the next periodic rescan.
    gmail_health_port = int(os.environ.get("GMAIL_CONNECTOR_HEALTH_PORT", "40082"))
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(f"http://127.0.0.1:{gmail_health_port}/reload")
        logger.info("Gmail connector reload triggered on port %s", gmail_health_port)
    except Exception:  # noqa: BLE001
        logger.debug(
            "Gmail connector reload ping failed (port %s) — may not be running yet",
            gmail_health_port,
        )

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


async def _register_google_health_contact_info(
    pool: Any,
    *,
    google_user_id: str,
) -> None:
    """Upsert a ``public.contact_info(type='google_health')`` row on the owner contact.

    This is called from the OAuth callback when ``scope_set=health`` is
    granted, satisfying the ``connector-google-health`` spec requirement
    'Owner Contact Info Registration' — the Switchboard resolves
    ``sender.identity = <google_user_id>`` on wellness envelopes via this
    row, avoiding the temp-contact path used for unknown senders.

    The function is idempotent — re-running pairing for the same account
    produces no duplicate row (``ON CONFLICT (type, value) DO NOTHING``).
    """
    insert_status: str | None = None
    owner_contact_id_for_shim: uuid.UUID | None = None

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Locate the owner entity by role.
            owner_entity_id = await conn.fetchval(
                """
                SELECT id FROM public.entities
                WHERE 'owner' = ANY(roles)
                LIMIT 1
                """
            )
            if owner_entity_id is None:
                # Owner entity not bootstrapped yet — skip. The daemon
                # bootstraps this on startup; if it's absent here it will
                # be created before the connector's first poll.
                logger.debug("Skipping google_health contact_info upsert — no owner entity")
                return

            # Find an existing contact linked to the owner entity. If none,
            # create a minimal one. In a fresh install the owner contact
            # row is bootstrapped by the daemon; this fallback guarantees
            # the upsert succeeds even on partial installs.
            owner_contact_id = await conn.fetchval(
                """
                SELECT id FROM public.contacts
                WHERE entity_id = $1
                ORDER BY created_at ASC
                LIMIT 1
                """,
                owner_entity_id,
            )
            if owner_contact_id is None:
                owner_contact_id = await conn.fetchval(
                    """
                    INSERT INTO public.contacts (name, entity_id, metadata)
                    VALUES ('Owner', $1, '{}'::jsonb)
                    RETURNING id
                    """,
                    owner_entity_id,
                )

            insert_status = await conn.execute(
                """
                INSERT INTO public.contact_info (contact_id, type, value, secured)
                VALUES ($1, 'google_health', $2, false)
                ON CONFLICT (type, value) DO NOTHING
                """,
                owner_contact_id,
                google_user_id,
            )
            owner_contact_id_for_shim = owner_contact_id

    # Dual-write shim (Group E): best-effort post-commit triple emission (Amendment 14).
    # Only emit when the INSERT actually created a row (asyncpg status == "INSERT 0 1").
    # When ON CONFLICT DO NOTHING silently skips because the (type, value) pair is already
    # claimed by a different contact, we must not assert a triple for the owner entity —
    # that would contradict the authoritative SQL state.
    # Note: google_health is currently unmapped in _CI_TYPE_TO_PREDICATE, so emit_contact_info_fact
    # will no-op internally. The gate is kept as a correctness safeguard so that if the
    # predicate mapping is added in the future, spurious triples on conflict paths are prevented.
    if insert_status == "INSERT 0 1" and owner_contact_id_for_shim is not None:
        try:
            from butlers.tools.relationship.dual_write import emit_contact_info_fact

            await emit_contact_info_fact(
                pool,
                contact_id=owner_contact_id_for_shim,
                ci_type="google_health",
                value=google_user_id,
                is_primary=False,
                src="dual-write",
            )
        except Exception:  # noqa: BLE001 — best-effort: never block the legacy commit
            logger.warning(
                "_register_google_health_contact_info: emit_contact_info_fact failed for "
                "contact %s (ci_type='google_health', value=%r) — dual-write failure swallowed",
                owner_contact_id_for_shim,
                google_user_id,
                exc_info=True,
            )


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
                INSERT INTO public.entity_info (entity_id, type, value, secured, is_primary)
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
                    UPDATE public.google_accounts
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
                    UPDATE public.google_accounts
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
            SELECT value FROM public.entity_info
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
    token_url: str = GOOGLE_TOKEN_URL,
) -> dict[str, Any]:
    """Exchange an authorization code for OAuth tokens.

    Parameters
    ----------
    code:
        Authorization code returned by the provider in the callback.
    client_id:
        OAuth client ID.
    client_secret:
        OAuth client secret.
    redirect_uri:
        The redirect URI registered with the provider (must match exactly).
    token_url:
        Token endpoint URL.  Defaults to Google's token URL for backward
        compatibility with existing Google-only call sites.

    Returns
    -------
    dict
        The full token response (access_token, refresh_token, scope, etc.).

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
            response = await client.post(token_url, data=payload)
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


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


async def _emit_oauth_audit(
    shared_pool: Any,
    *,
    actor: str = "owner",
    action: str,
    provider: str,
    note: str | None = None,
) -> None:
    """Best-effort append to ``public.audit_log`` for OAuth lifecycle events.

    Swallows all errors (including AuditTableNotAvailableError) so that
    missing migrations or DB downtime never block the OAuth flow.

    Parameters
    ----------
    shared_pool:
        asyncpg connection pool pointed at the public schema.  When None,
        the call is a silent no-op.
    actor:
        Principal triggering the event.
    action:
        Audit action value (e.g. ``"attempted"``, ``"connected"``, ``"failed"``).
    provider:
        OAuth provider identifier (e.g. ``"google"``).
    note:
        Optional human-readable note stored alongside the audit row.
    """
    if shared_pool is None:
        return
    target = normalize_credential_key("user", provider)
    try:
        await _audit.append(
            shared_pool,
            actor,
            action,
            target=target,
            note=note,
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "OAuth audit write swallowed (action=%s, provider=%s)", action, provider, exc_info=True
        )


# ---------------------------------------------------------------------------
# Generalised /{provider}/start endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/{provider}/start",
    summary="Begin OAuth authorization flow for any registered provider",
    description=(
        "Generalized OAuth start endpoint. "
        "Returns ApiResponse<{authorization_url}> when redirect=false, "
        "or 302 to the provider's authorization URL. "
        "Writes an 'attempted' audit row to public.audit_log BEFORE redirecting. "
        "page_of_origin is threaded through the CSRF state token so the callback "
        "can route the user back to the originating page. "
        "For provider=google the behavior is identical to /api/oauth/google/start."
    ),
)
async def oauth_provider_start(
    provider: str,
    redirect: bool = Query(
        default=True,
        description="If true (default), redirect to the provider authorization URL. "
        "If false, return the URL as JSON.",
    ),
    account_hint: str | None = Query(
        default=None,
        description="Optional account email to pre-select (passed as login_hint where supported).",
    ),
    force_consent: bool = Query(
        default=False,
        description="When true, adds prompt=consent / show_dialog=true to the URL.",
    ),
    scope_set: str | None = Query(
        default=None,
        description="Named scope set(s) for this provider. Comma-separated. "
        "When omitted, falls back to the provider's default scope composition.",
    ),
    page_of_origin: str | None = Query(
        default=None,
        description="Page that initiated the OAuth dance. "
        "Known values: 'secrets' (default), 'ingestion'. "
        "Threaded through state token; callback uses it for return routing.",
    ),
    db_manager: Any = Depends(_get_db_manager),
) -> Response:
    """Begin the OAuth authorization flow for *provider*.

    Resolves scope-sets from the provider registry, checks account limits
    (Google only), stores the CSRF state token carrying ``page_of_origin``,
    writes an ``attempted`` audit row, and returns a redirect or JSON response.
    """
    provider_cfg = _get_provider_config(provider)
    if provider_cfg is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "unknown_provider",
                "provider": provider,
                "known": sorted(_PROVIDER_REGISTRY.keys()),
            },
        )

    # --- Resolve scope composition ---
    requested_sets = _parse_scope_set_param(scope_set)
    if requested_sets is not None:
        try:
            scopes = _compose_provider_scopes_from_sets(provider_cfg, requested_sets)
        except ValueError as exc:
            unknown_name = str(exc)
            return JSONResponse(
                status_code=400,
                content={
                    "error": "unknown_scope_set",
                    "scope_set": unknown_name,
                    "known": sorted(provider_cfg.scope_sets.keys()),
                },
            )
    else:
        scopes = _compose_provider_default_scopes(provider_cfg)

    # --- Google-specific: account limit check + scope-widening ---
    shared_pool = _get_shared_pool(db_manager)
    _hinted_account_granted_scopes: list[str] | None = None
    if provider == "google" and shared_pool is not None:
        if account_hint:
            try:
                existing = await get_google_account(shared_pool, account=account_hint)
                _hinted_account_granted_scopes = list(existing.granted_scopes)
            except GoogleAccountNotFoundError:
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
                pass
        else:
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
                pass

        # Scope-widening for Google re-auth flows.
        if requested_sets is not None and _hinted_account_granted_scopes:
            scopes = _widen_scopes(scopes, _hinted_account_granted_scopes)

    # --- Resolve app credentials ---
    client_id, _ = await _resolve_provider_credentials(provider_cfg, db_manager)
    redirect_uri = _get_provider_redirect_uri(provider_cfg)

    # --- Build authorization URL ---
    state = _generate_state()
    _store_state(
        state,
        account_hint=account_hint,
        force_consent=force_consent,
        page_of_origin=page_of_origin,
        provider=provider,
    )

    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
    }

    if provider == "google":
        params["access_type"] = "offline"
        if force_consent:
            params["prompt"] = "consent"
        if account_hint:
            params["login_hint"] = account_hint
    elif provider == "spotify":
        if force_consent:
            params["show_dialog"] = "true"

    authorization_url = f"{provider_cfg.auth_url}?{urlencode(params)}"

    logger.info(
        "OAuth flow started (provider=%s, state=%s..., account_hint=%s, "
        "force_consent=%s, scope_set=%s, page_of_origin=%s)",
        provider,
        state[:8],
        account_hint,
        force_consent,
        requested_sets,
        page_of_origin,
    )

    # --- Audit: attempted BEFORE redirect ---
    await _emit_oauth_audit(
        shared_pool,
        action="attempted",
        provider=provider,
        note=f"OAuth flow started (page_of_origin={page_of_origin or 'default'})",
    )

    if redirect:
        return RedirectResponse(url=authorization_url, status_code=302)

    return JSONResponse(
        content=ApiResponse(
            data={"authorization_url": authorization_url, "state": state}
        ).model_dump()
    )


# ---------------------------------------------------------------------------
# Generalised /{provider}/callback endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/{provider}/callback",
    summary="OAuth callback for any registered provider",
    description=(
        "Generalised OAuth callback endpoint. Validates CSRF state, exchanges "
        "the authorization code for tokens, persists credentials, writes a "
        "'connected' or 'failed' audit row, and redirects based on state.page_of_origin."
    ),
)
async def oauth_provider_callback(
    provider: str,
    code: str | None = Query(default=None, description="Authorization code from the provider."),
    state: str | None = Query(default=None, description="CSRF state token."),
    error: str | None = Query(default=None, description="OAuth error code from the provider."),
    error_description: str | None = Query(
        default=None, description="Human-readable error from the provider."
    ),
    db_manager: Any = Depends(_get_db_manager),
) -> Response:
    """Handle the OAuth callback for *provider*.

    For ``provider=google`` the full Google-specific credential persistence
    logic (registry, health-scope metadata, gmail-reload) is reused.
    For other providers (e.g. ``spotify``), a lightweight generic path
    stores the refresh token in the shared credential store.

    On success, redirects based on ``state.page_of_origin``:
      "secrets"   → /secrets?focus=u:<provider>&toast=connected
      "ingestion" → /ingestion/connectors
      (default)   → /secrets?focus=u:<provider>&toast=connected
    """
    provider_cfg = _get_provider_config(provider)
    if provider_cfg is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "unknown_provider",
                "provider": provider,
                "known": sorted(_PROVIDER_REGISTRY.keys()),
            },
        )

    shared_pool = _get_shared_pool(db_manager)
    dashboard_url = _get_dashboard_url()

    # --- Handle provider-side errors ---
    if error:
        logger.warning("OAuth provider error (provider=%s): %s", provider, error)
        if error_description:
            logger.debug("OAuth provider error_description: %s", error_description)
        if state:
            state_entry = _validate_and_consume_state(state)
            _page_of_origin = state_entry.page_of_origin if state_entry else None
        else:
            _page_of_origin = None

        await _emit_oauth_audit(
            shared_pool,
            action="failed",
            provider=provider,
            note=f"Provider error: {_sanitize_provider_error(error)}",
        )

        safe_error = _sanitize_provider_error(error)
        if dashboard_url:
            return RedirectResponse(
                url=f"{dashboard_url}?oauth_error=provider_error",
                status_code=302,
            )
        error_redirect = _build_error_redirect_url(provider, _page_of_origin, "provider_error")
        if _page_of_origin:
            return RedirectResponse(url=error_redirect, status_code=302)
        return JSONResponse(
            status_code=400,
            content=ApiResponse(
                data={"success": False, "error_code": "provider_error", "message": safe_error}
            ).model_dump(),
        )

    # --- Validate required parameters ---
    if not code:
        return JSONResponse(
            status_code=400,
            content=ApiResponse(
                data={
                    "success": False,
                    "error_code": "missing_code",
                    "message": "Authorization code is missing from the callback.",
                }
            ).model_dump(),
        )

    if not state:
        return JSONResponse(
            status_code=400,
            content=ApiResponse(
                data={
                    "success": False,
                    "error_code": "missing_state",
                    "message": "State parameter is missing. Possible CSRF attempt.",
                }
            ).model_dump(),
        )

    # --- Validate CSRF state ---
    state_entry = _validate_and_consume_state(state)
    if state_entry is None:
        logger.warning(
            "OAuth callback received invalid/expired state token (provider=%s)", provider
        )
        return JSONResponse(
            status_code=400,
            content=ApiResponse(
                data={
                    "success": False,
                    "error_code": "invalid_state",
                    "message": "State parameter is invalid or expired. Please restart the flow.",
                }
            ).model_dump(),
        )

    page_of_origin = state_entry.page_of_origin

    # --- For provider=google, delegate to the existing callback logic ---
    if provider == "google":
        # Re-use the full Google callback implementation by delegating.
        # We pass the state_entry directly to avoid re-validating state.
        return await _google_callback_from_state(
            code=code,
            state_entry=state_entry,
            db_manager=db_manager,
            page_of_origin=page_of_origin,
        )

    # --- Generic provider path ---
    client_id, client_secret = await _resolve_provider_credentials(provider_cfg, db_manager)
    redirect_uri = _get_provider_redirect_uri(provider_cfg)

    try:
        token_data = await _exchange_code_for_tokens(
            code=code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            token_url=provider_cfg.token_url,
        )
    except _TokenExchangeError as exc:
        logger.warning("OAuth token exchange failed (provider=%s): %s", provider, exc)
        await _emit_oauth_audit(
            shared_pool,
            action="failed",
            provider=provider,
            note="Token exchange failed",
        )
        return JSONResponse(
            status_code=400,
            content=ApiResponse(
                data={
                    "success": False,
                    "error_code": "token_exchange_failed",
                    "message": "Failed to exchange authorization code for tokens. Please restart.",
                }
            ).model_dump(),
        )

    refresh_token = token_data.get("refresh_token")
    access_token = token_data.get("access_token")
    scope = token_data.get("scope")

    # --- Fetch account identity via profile URL if available ---
    account_email: str | None = None
    if access_token and provider_cfg.profile_url:
        try:
            headers = {"Authorization": f"Bearer {access_token}"}
            async with httpx.AsyncClient(timeout=10.0) as http_client:
                profile_resp = await http_client.get(provider_cfg.profile_url, headers=headers)
            if profile_resp.status_code == 200:
                profile_data = profile_resp.json()
                account_email = profile_data.get("email") or profile_data.get("id")
        except Exception:  # noqa: BLE001
            logger.debug("Profile fetch failed for provider=%s (non-fatal)", provider)

    # --- Persist credentials in shared credential store ---
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        await _emit_oauth_audit(
            shared_pool,
            action="failed",
            provider=provider,
            note="Credential store unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail="Shared credential DB unavailable; cannot persist OAuth credentials.",
        )

    if refresh_token:
        # Store the refresh token using the provider-namespaced key.
        await cred_store.store(
            f"oauth_{provider}_refresh_token",
            refresh_token,
            category=provider,
            description=f"{provider} OAuth refresh token",
            is_sensitive=True,
        )

    logger.info(
        "OAuth COMPLETE (provider=%s, account=%s, persisted=true)",
        provider,
        account_email,
    )

    # --- Audit: connected ---
    await _emit_oauth_audit(
        shared_pool,
        action="connected",
        provider=provider,
        note=(
            f"OAuth dance complete (account={account_email})"
            if account_email
            else "OAuth dance complete"
        ),
    )

    # --- Redirect ---
    success_url = _build_success_redirect_url(provider, page_of_origin)
    if dashboard_url:
        return RedirectResponse(url=f"{dashboard_url}?oauth_success=true", status_code=302)
    if page_of_origin is not None:
        return RedirectResponse(url=success_url, status_code=302)

    return JSONResponse(
        content=ApiResponse(
            data={
                "success": True,
                "message": "OAuth complete. Credentials persisted.",
                "provider": provider,
                "scope": scope,
                "account_email": account_email,
            }
        ).model_dump()
    )


# ---------------------------------------------------------------------------
# _google_callback_from_state — used by the generalised /{provider}/callback
# ---------------------------------------------------------------------------


async def _google_callback_from_state(
    *,
    code: str,
    state_entry: _StateEntry,
    db_manager: Any,
    page_of_origin: str | None,
) -> Response:
    """Run the full Google OAuth callback using an already-validated state entry.

    Called by ``oauth_provider_callback`` when ``provider=google`` so that the
    generalised route reuses the existing credential persistence logic without
    duplicating it.  The CSRF state has already been validated and consumed by
    the caller.
    """
    shared_pool = _get_shared_pool(db_manager)
    dashboard_url = _get_dashboard_url()

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
        await _emit_oauth_audit(
            shared_pool,
            action="failed",
            provider="google",
            note="Token exchange failed",
        )
        return JSONResponse(
            status_code=400,
            content=ApiResponse(
                data={
                    "success": False,
                    "error_code": "token_exchange_failed",
                    "message": (
                        "Failed to exchange authorization code for tokens. "
                        "The code may have expired or already been used."
                    ),
                }
            ).model_dump(),
        )

    refresh_token = token_data.get("refresh_token")
    access_token = token_data.get("access_token")
    scope = token_data.get("scope")

    account_email: str | None = None
    account_display_name: str | None = None

    if access_token:
        try:
            userinfo = await _fetch_google_userinfo(access_token)
            account_email = userinfo.get("email")
            account_display_name = userinfo.get("name")
        except _UserinfoError as exc:
            logger.warning("Google userinfo call failed: %s", exc)
            await _emit_oauth_audit(
                shared_pool,
                action="failed",
                provider="google",
                note="Userinfo call failed",
            )
            return JSONResponse(
                status_code=502,
                content=ApiResponse(
                    data={
                        "success": False,
                        "error_code": "userinfo_failed",
                        "message": "Failed to retrieve account information. Please restart.",
                    }
                ).model_dump(),
            )

    # Reuse the full account-registry + credential persistence path.
    is_new_account: bool | None = None
    resolved_entity_id: uuid.UUID | None = None

    if shared_pool is not None and account_email:
        try:
            existing_account = await get_google_account(shared_pool, account=account_email)
            is_new_account = False
            resolved_entity_id = existing_account.entity_id
            if refresh_token:
                await _update_account_refresh_token(
                    shared_pool,
                    entity_id=existing_account.entity_id,
                    refresh_token=refresh_token,
                    scopes=scope,
                )
        except GoogleAccountNotFoundError:
            is_new_account = True
            if not refresh_token:
                await _emit_oauth_audit(
                    shared_pool,
                    action="failed",
                    provider="google",
                    note="No refresh token for new account",
                )
                return JSONResponse(
                    status_code=400,
                    content=ApiResponse(
                        data={
                            "success": False,
                            "error_code": "no_refresh_token",
                            "message": (
                                "Google did not return a refresh token. "
                                "Re-authorize using force_consent=true."
                            ),
                        }
                    ).model_dump(),
                )
            scope_list = [s for s in scope.split() if s] if scope else []
            try:
                new_account = await create_google_account(
                    shared_pool,
                    email=account_email,
                    display_name=account_display_name,
                    scopes=scope_list,
                    refresh_token=refresh_token,
                )
                resolved_entity_id = new_account.entity_id
            except GoogleAccountAlreadyExistsError:
                is_new_account = False
                existing_account = await get_google_account(shared_pool, account=account_email)
                resolved_entity_id = existing_account.entity_id
                if refresh_token:
                    await _update_account_refresh_token(
                        shared_pool,
                        entity_id=existing_account.entity_id,
                        refresh_token=refresh_token,
                        scopes=scope,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Google account registry error: %s", exc)

    # Health test-mode metadata.
    if shared_pool is not None and resolved_entity_id is not None:
        if _is_google_health_test_mode() and _has_health_scope(scope):
            try:
                await _set_account_health_test_mode(shared_pool, entity_id=resolved_entity_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to set google_health_test_mode: %s", exc)

    # Persist app credentials + legacy refresh token path.
    cred_store = _make_credential_store(db_manager)
    if cred_store is None:
        await _emit_oauth_audit(
            shared_pool,
            action="failed",
            provider="google",
            note="Credential store unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail="Shared credential DB unavailable; cannot persist OAuth credentials.",
        )

    if refresh_token and (shared_pool is None or not account_email):
        await store_google_credentials(
            cred_store,
            pool=shared_pool,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            scope=scope,
        )
    else:
        await store_app_credentials(cred_store, client_id=client_id, client_secret=client_secret)

    logger.info(
        "Google OAuth COMPLETE (client_id=%s, account=%s, is_new=%s, persisted=true)",
        client_id,
        account_email,
        is_new_account,
    )

    # Register google_health contact_info if health scopes granted.
    if shared_pool is not None and account_email and scope and "googlehealth." in scope:
        try:
            await _register_google_health_contact_info(shared_pool, google_user_id=account_email)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to upsert google_health contact_info: %s", exc)

    # Notify Gmail connector to reload.
    gmail_health_port = int(os.environ.get("GMAIL_CONNECTOR_HEALTH_PORT", "40082"))
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(f"http://127.0.0.1:{gmail_health_port}/reload")
    except Exception:  # noqa: BLE001
        logger.debug("Gmail connector reload ping failed (port %s)", gmail_health_port)

    # --- Audit: connected ---
    await _emit_oauth_audit(
        shared_pool,
        action="connected",
        provider="google",
        note=(
            f"Google OAuth complete (account={account_email})"
            if account_email
            else "Google OAuth complete"
        ),
    )

    success_url = _build_success_redirect_url("google", page_of_origin)
    if dashboard_url:
        return RedirectResponse(url=f"{dashboard_url}?oauth_success=true", status_code=302)
    if page_of_origin is not None:
        return RedirectResponse(url=success_url, status_code=302)

    return JSONResponse(
        content=ApiResponse(
            data={
                "success": True,
                "message": "OAuth bootstrap complete. Credentials persisted to database.",
                "provider": "google",
                "scope": scope,
                "account_email": account_email,
                "is_new_account": is_new_account,
            }
        ).model_dump()
    )
