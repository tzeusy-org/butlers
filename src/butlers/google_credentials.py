"""Shared Google credential storage for butler modules.

Provides a single source of truth for Google OAuth credentials that can be
consumed by both the Gmail connector and the Calendar module.

**Storage split:**

- ``GOOGLE_OAUTH_CLIENT_ID``, ``GOOGLE_OAUTH_CLIENT_SECRET``,
  ``GOOGLE_OAUTH_SCOPES`` → ``butler_secrets`` table via
  :class:`~butlers.credential_store.CredentialStore` (app config).
- ``GOOGLE_REFRESH_TOKEN`` → ``shared.contact_info`` on the owner contact
  (type ``google_oauth_refresh``, ``secured=true``).

Secret material (client_secret, refresh_token) is never logged in plaintext.

Usage — persisting after OAuth bootstrap::

    store = CredentialStore(pool)
    await store_google_credentials(
        store, pool=shared_pool,
        client_id="...",
        client_secret="...",
        refresh_token="...",
        scope="https://www.googleapis.com/auth/gmail.readonly ...",
    )

Usage — loading at module startup::

    store = CredentialStore(pool)
    creds = await load_google_credentials(store, pool=shared_pool)
    if creds is None:
        raise MissingGoogleCredentialsError("...")

    client_id = creds.client_id
    refresh_token = creds.refresh_token

"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

from butlers.credential_store import (
    CredentialStore,
    delete_owner_contact_info,
    resolve_owner_contact_info,
    upsert_owner_contact_info,
)

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# butler_secrets key names for Google OAuth (app config only)
# ---------------------------------------------------------------------------

KEY_CLIENT_ID = "GOOGLE_OAUTH_CLIENT_ID"
KEY_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET"
KEY_REFRESH_TOKEN = "GOOGLE_REFRESH_TOKEN"  # kept for backward compat references
KEY_SCOPES = "GOOGLE_OAUTH_SCOPES"

_GOOGLE_CATEGORY = "google"

# ---------------------------------------------------------------------------
# contact_info type for the refresh token
# ---------------------------------------------------------------------------

CONTACT_INFO_REFRESH_TOKEN = "google_oauth_refresh"

# ---------------------------------------------------------------------------
# Credential model
# ---------------------------------------------------------------------------


class GoogleCredentials(BaseModel):
    """Shared Google OAuth credential set for Gmail and Calendar."""

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    scope: str | None = None

    @field_validator("client_id", "client_secret", "refresh_token")
    @classmethod
    def _normalize_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be a non-empty string")
        return normalized

    @field_validator("scope")
    @classmethod
    def _normalize_scope(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    # ------------------------------------------------------------------
    # Safe repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"GoogleCredentials("
            f"client_id={self.client_id!r}, "
            f"client_secret=<REDACTED>, "
            f"refresh_token=<REDACTED>, "
            f"scope={self.scope!r})"
        )

    # Alias __str__ to __repr__ so that str() also redacts secrets.
    # Pydantic's default __str__ would expose field values verbatim.
    __str__ = __repr__


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MissingGoogleCredentialsError(Exception):
    """Raised when Google credentials cannot be resolved.

    The error message is safe to log: it names the missing fields but
    never includes secret values.
    """


class InvalidGoogleCredentialsError(Exception):
    """Raised when stored credential data is malformed or unparseable.

    The error message is safe to log.
    """


# ---------------------------------------------------------------------------
# Persistence helpers — dual-source (butler_secrets + contact_info)
# ---------------------------------------------------------------------------


async def store_google_credentials(
    store: CredentialStore,
    *,
    pool: asyncpg.Pool | None = None,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scope: str | None = None,
) -> None:
    """Persist Google OAuth credentials.

    App credentials (client_id, client_secret, scopes) are stored in
    ``butler_secrets`` via *store*.  The refresh token is stored in
    ``shared.contact_info`` on the owner contact via *pool*.

    Secret material (client_secret, refresh_token) is never logged.

    Parameters
    ----------
    store:
        A :class:`~butlers.credential_store.CredentialStore`.
    pool:
        An asyncpg pool for the shared database (for contact_info writes).
        When ``None``, the refresh token is not persisted.
    client_id:
        OAuth client ID (non-secret).
    client_secret:
        OAuth client secret (secret — never logged).
    refresh_token:
        OAuth refresh token (secret — never logged).
    scope:
        Space-separated OAuth scopes granted (optional).
    """
    # Validate and normalise via the model (raises ValueError on empty/whitespace fields).
    validated = GoogleCredentials(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        scope=scope or None,
    )

    # App credentials → butler_secrets
    await store.store(
        KEY_CLIENT_ID,
        validated.client_id,
        category=_GOOGLE_CATEGORY,
        description="Google OAuth client ID",
        is_sensitive=False,
    )
    await store.store(
        KEY_CLIENT_SECRET,
        validated.client_secret,
        category=_GOOGLE_CATEGORY,
        description="Google OAuth client secret",
        is_sensitive=True,
    )
    if validated.scope:
        await store.store(
            KEY_SCOPES,
            validated.scope,
            category=_GOOGLE_CATEGORY,
            description="Google OAuth granted scopes",
            is_sensitive=False,
        )

    # Refresh token → shared.contact_info
    if pool is not None:
        await upsert_owner_contact_info(pool, CONTACT_INFO_REFRESH_TOKEN, validated.refresh_token)

    logger.info(
        "Google OAuth credentials stored in database (client_id=%s, scope=%s)",
        client_id,
        scope,
    )


async def load_google_credentials(
    store: CredentialStore,
    *,
    pool: asyncpg.Pool | None = None,
) -> GoogleCredentials | None:
    """Load Google OAuth credentials.

    App credentials are read from ``butler_secrets`` via *store*.  The refresh
    token is read from ``shared.contact_info`` via *pool*.

    Returns ``None`` if the required credentials (client_id, client_secret,
    refresh_token) are not fully present.

    Raises
    ------
    InvalidGoogleCredentialsError
        If the stored data is present but malformed/incomplete.
    """
    client_id = await store.load(KEY_CLIENT_ID)
    client_secret = await store.load(KEY_CLIENT_SECRET)
    scope = await store.load(KEY_SCOPES)

    # Refresh token from contact_info (primary) or butler_secrets (fallback)
    refresh_token: str | None = None
    if pool is not None:
        refresh_token = await resolve_owner_contact_info(pool, CONTACT_INFO_REFRESH_TOKEN)
    if not refresh_token:
        refresh_token = await store.load(KEY_REFRESH_TOKEN)

    missing = [
        field
        for field, val in [
            ("client_id", client_id),
            ("client_secret", client_secret),
            ("refresh_token", refresh_token),
        ]
        if not val
    ]
    # All three required fields absent → credentials not stored yet
    if len(missing) == 3:
        return None

    # Some fields missing → stored data is incomplete
    if missing:
        raise InvalidGoogleCredentialsError(
            f"Stored Google credentials are missing required field(s): "
            f"{', '.join(missing)}. "
            f"Re-run the OAuth bootstrap to replace the stored credentials."
        )

    return GoogleCredentials(
        client_id=client_id,  # type: ignore[arg-type]
        client_secret=client_secret,  # type: ignore[arg-type]
        refresh_token=refresh_token,  # type: ignore[arg-type]
        scope=scope,
    )


async def store_app_credentials(
    store: CredentialStore,
    *,
    client_id: str,
    client_secret: str,
) -> None:
    """Persist Google OAuth app credentials (client_id + client_secret).

    This is a partial upsert that stores only the app credentials.  If a
    refresh token already exists it is preserved (it lives in contact_info).

    Secret material (client_secret) is never logged.

    Parameters
    ----------
    store:
        A :class:`~butlers.credential_store.CredentialStore`.
    client_id:
        OAuth client ID (non-secret).
    client_secret:
        OAuth client secret (secret — never logged).
    """
    client_id = client_id.strip()
    client_secret = client_secret.strip()
    if not client_id:
        raise ValueError("client_id must be a non-empty string")
    if not client_secret:
        raise ValueError("client_secret must be a non-empty string")

    await store.store(
        KEY_CLIENT_ID,
        client_id,
        category=_GOOGLE_CATEGORY,
        description="Google OAuth client ID",
        is_sensitive=False,
    )
    await store.store(
        KEY_CLIENT_SECRET,
        client_secret,
        category=_GOOGLE_CATEGORY,
        description="Google OAuth client secret",
        is_sensitive=True,
    )
    logger.info(
        "Google app credentials (client_id + client_secret) stored in butler_secrets"
        " (client_id=%s)",
        client_id,
    )


async def load_app_credentials(
    store: CredentialStore,
    *,
    pool: asyncpg.Pool | None = None,
) -> GoogleAppCredentials | None:
    """Load Google app credentials (client_id + client_secret + optional refresh_token).

    Returns ``None`` if no app credentials have been stored yet.  Unlike
    ``load_google_credentials``, this does NOT require a refresh_token.

    Parameters
    ----------
    store:
        A :class:`~butlers.credential_store.CredentialStore`.
    pool:
        An asyncpg pool for the shared database (for contact_info reads).
        When ``None``, refresh_token is read from butler_secrets only.
    """
    client_id = await store.load(KEY_CLIENT_ID)
    client_secret = await store.load(KEY_CLIENT_SECRET)

    if not client_id or not client_secret:
        return None

    # Refresh token from contact_info (primary) or butler_secrets (fallback)
    refresh_token: str | None = None
    if pool is not None:
        refresh_token = await resolve_owner_contact_info(pool, CONTACT_INFO_REFRESH_TOKEN)
    if not refresh_token:
        refresh_token = await store.load(KEY_REFRESH_TOKEN)

    scope = await store.load(KEY_SCOPES)

    return GoogleAppCredentials(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token or None,
        scope=scope or None,
    )


async def delete_google_credentials(
    store: CredentialStore,
    *,
    pool: asyncpg.Pool | None = None,
) -> bool:
    """Delete stored Google OAuth credentials.

    Removes app credential keys from ``butler_secrets`` and the refresh token
    from ``shared.contact_info``.

    Parameters
    ----------
    store:
        A :class:`~butlers.credential_store.CredentialStore`.
    pool:
        An asyncpg pool for the shared database (for contact_info deletes).

    Returns
    -------
    bool
        ``True`` if at least one credential was deleted.
    """
    results = [
        await store.delete(KEY_CLIENT_ID),
        await store.delete(KEY_CLIENT_SECRET),
        await store.delete(KEY_REFRESH_TOKEN),
        await store.delete(KEY_SCOPES),
    ]

    # Also delete from contact_info
    if pool is not None:
        ci_deleted = await delete_owner_contact_info(pool, CONTACT_INFO_REFRESH_TOKEN)
        results.append(ci_deleted)

    deleted = any(results)
    if deleted:
        logger.info("Google OAuth credentials deleted from database")
    else:
        logger.info("No Google OAuth credentials to delete")
    return deleted


# ---------------------------------------------------------------------------
# Resolution helper (DB-only)
# ---------------------------------------------------------------------------


async def resolve_google_credentials(
    store: CredentialStore,
    *,
    pool: asyncpg.Pool | None = None,
    caller: str = "unknown",
) -> GoogleCredentials:
    """Resolve Google OAuth credentials from DB-backed secret storage.

    Resolution:
    1. App credentials from ``butler_secrets``.
    2. Refresh token from ``shared.contact_info``.

    Parameters
    ----------
    store:
        A :class:`~butlers.credential_store.CredentialStore`.
    pool:
        An asyncpg pool for the shared database.
    caller:
        Name of the calling component (used in log messages for traceability).

    Returns
    -------
    GoogleCredentials
        Resolved DB credentials.

    Raises
    ------
    MissingGoogleCredentialsError
        If credentials cannot be found in DB.
    """
    try:
        creds = await load_google_credentials(store, pool=pool)
    except InvalidGoogleCredentialsError as exc:
        raise MissingGoogleCredentialsError(
            f"[{caller}] Stored Google credentials are invalid. "
            f"Re-run OAuth bootstrap to replace credentials in butler_secrets. "
            f"Details: {exc}"
        ) from exc

    if creds is not None:
        logger.debug("[%s] Resolved Google credentials from database", caller)
        return creds

    raise MissingGoogleCredentialsError(
        f"[{caller}] Google OAuth credentials are not available in butler_secrets. "
        "Bootstrap via GET /api/oauth/google/start and persist credentials in DB."
    )


# ---------------------------------------------------------------------------
# Partial credential model (app credentials only, no refresh token)
# ---------------------------------------------------------------------------


class GoogleAppCredentials(BaseModel):
    """Partial Google credential set — app credentials only (client_id + client_secret).

    Used when the operator has entered app credentials but has not yet run the
    OAuth flow to obtain a refresh token.
    """

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    refresh_token: str | None = None
    scope: str | None = None

    @field_validator("client_id", "client_secret")
    @classmethod
    def _normalize_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be a non-empty string")
        return normalized

    def __repr__(self) -> str:
        return (
            f"GoogleAppCredentials("
            f"client_id={self.client_id!r}, "
            f"client_secret=<REDACTED>, "
            f"refresh_token={'<REDACTED>' if self.refresh_token else None}, "
            f"scope={self.scope!r})"
        )

    __str__ = __repr__
