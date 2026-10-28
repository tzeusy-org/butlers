"""Shared Google credential storage for butler modules.

Provides a single source of truth for Google OAuth credentials that can be
consumed by the Gmail, Calendar, and Google Health connectors.

**Storage split:**

- ``GOOGLE_OAUTH_CLIENT_ID``, ``GOOGLE_OAUTH_CLIENT_SECRET``,
  ``GOOGLE_OAUTH_SCOPES`` → ``butler_secrets`` table via
  :class:`~butlers.credential_store.CredentialStore` (app config).
- Refresh token → ``public.entity_info`` on the Google account's companion entity
  (type ``google_oauth_refresh``, ``secured=true``), resolved via
  ``public.google_accounts``.

Secret material (client_secret, refresh_token) is never logged in plaintext.

Usage — persisting after OAuth bootstrap::

    store = CredentialStore(pool)
    await store_google_credentials(
        store, pool=shared_pool,
        client_id="...",
        client_secret="...",
        refresh_token="...",
        scope="https://www.googleapis.com/auth/gmail.readonly ...",
        account="alice@gmail.com",  # None = primary
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

import asyncio
import hashlib
import logging
import time
import uuid
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from butlers.credential_store import CredentialStore
from butlers.oauth_token_payload import (
    OAuthTokenPayload,
    validate_oauth_token_payload,
)

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# butler_secrets key names for Google OAuth (app config only)
# ---------------------------------------------------------------------------

KEY_CLIENT_ID = "GOOGLE_OAUTH_CLIENT_ID"
KEY_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET"
KEY_SCOPES = "GOOGLE_OAUTH_SCOPES"

_GOOGLE_CATEGORY = "google"

# ---------------------------------------------------------------------------
# contact_info type for the refresh token
# ---------------------------------------------------------------------------

CONTACT_INFO_REFRESH_TOKEN = "google_oauth_refresh"


# ---------------------------------------------------------------------------
# OAuth token-endpoint error parsing
# ---------------------------------------------------------------------------

# These are the only Google OAuth token-endpoint error codes that require
# operator action.  ``invalid_grant`` additionally proves the shared refresh
# grant is gone; ``unauthorized_client`` is limited to the configured client.
_GOOGLE_OAUTH_REAUTH_ERROR_CODES = frozenset({"invalid_grant", "unauthorized_client"})


def google_oauth_error_code(response: Any) -> str | None:
    """Return Google's exact top-level OAuth token-endpoint error code.

    Google data APIs use a nested ``{"error": { ... }}`` response shape.  It
    can include OAuth-looking prose in ``message`` or ``reason``, but that is
    not evidence that the refresh grant failed.  Only the token endpoint's
    top-level string ``error`` field is suitable for credential state changes.
    """
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - callers may pass an HTTP test double
        return None

    if not isinstance(payload, dict):
        return None

    error = payload.get("error")
    return error if isinstance(error, str) and error else None


def google_oauth_requires_reauthorization(response: Any) -> bool:
    """Whether a token-endpoint response requires operator reauthorization."""
    return google_oauth_error_code(response) in _GOOGLE_OAUTH_REAUTH_ERROR_CODES


# ---------------------------------------------------------------------------
# Credential model
# ---------------------------------------------------------------------------


class GoogleCredentials(BaseModel):
    """Shared Google OAuth credential set for Gmail, Calendar, and Google Health."""

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
# Internal DB connection helper (mirrors credential_store._acquire_conn)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _pool_acquire(pool: asyncpg.Pool) -> AsyncIterator[Any]:
    """Acquire a DB connection, AsyncMock-friendly."""
    acquired = pool.acquire()
    if hasattr(acquired, "__aenter__"):
        async with acquired as conn:
            yield conn
        return
    if hasattr(acquired, "__await__"):
        acquired = await acquired
    if hasattr(acquired, "__aenter__"):
        async with acquired as conn:
            yield conn
        return
    yield acquired


# ---------------------------------------------------------------------------
# Account resolution helpers
# ---------------------------------------------------------------------------


async def resolve_google_account_entity(
    pool: asyncpg.Pool,
    *,
    email: str | None = None,
) -> uuid.UUID | None:
    """Resolve a Google account's companion entity_id from public.google_accounts.

    Parameters
    ----------
    pool:
        An asyncpg pool connected to the shared database.
    email:
        The Google account email to look up. When ``None``, the primary
        account is resolved.

    Returns
    -------
    uuid.UUID | None
        The companion entity_id, or ``None`` if no matching account exists.
    """
    try:
        async with _pool_acquire(pool) as conn:
            if email is None:
                row = await conn.fetchrow(
                    """
                    SELECT entity_id FROM public.google_accounts
                    WHERE is_primary = true
                    LIMIT 1
                    """
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT entity_id FROM public.google_accounts
                    WHERE email = $1
                    LIMIT 1
                    """,
                    email,
                )
        if row is None:
            return None
        return row["entity_id"]
    except Exception as exc:  # noqa: BLE001
        # Table may not exist yet (migration pending) — degrade gracefully.
        if _is_missing_table_or_schema_error(exc):
            logger.debug(
                "resolve_google_account_entity skipped; google_accounts table not available: %s",
                exc,
            )
            return None
        raise


async def list_google_account_entities(
    pool: asyncpg.Pool,
) -> list[tuple[uuid.UUID, str | None, uuid.UUID, bool]]:
    """Return all active Google account rows as (account_id, email, entity_id, is_primary).

    Parameters
    ----------
    pool:
        An asyncpg pool connected to the shared database.

    Returns
    -------
    list of (account_id, email, entity_id, is_primary) tuples for all accounts.
    """
    try:
        async with _pool_acquire(pool) as conn:
            rows = await conn.fetch(
                """
                SELECT id, email, entity_id, is_primary
                FROM public.google_accounts
                ORDER BY is_primary DESC, connected_at ASC
                """
            )
        return [(row["id"], row["email"], row["entity_id"], row["is_primary"]) for row in rows]
    except Exception as exc:  # noqa: BLE001
        if _is_missing_table_or_schema_error(exc):
            logger.debug(
                "list_google_account_entities skipped; google_accounts table not available: %s",
                exc,
            )
            return []
        raise


def _is_missing_table_or_schema_error(exc: Exception) -> bool:
    """Return True for table/schema not found errors."""
    cls = exc.__class__.__name__
    if cls in ("UndefinedTableError", "InvalidSchemaNameError", "UndefinedColumnError"):
        return True
    msg = str(exc).lower()
    return "does not exist" in msg


# ---------------------------------------------------------------------------
# Internal: entity_info helpers for arbitrary entity
# ---------------------------------------------------------------------------


async def _resolve_entity_refresh_token(pool: asyncpg.Pool, entity_id: uuid.UUID) -> str | None:
    """Fetch the refresh token from entity_info for the given companion entity."""
    try:
        async with _pool_acquire(pool) as conn:
            row = await conn.fetchrow(
                """
                SELECT value FROM public.entity_info
                WHERE entity_id = $1 AND type = $2
                LIMIT 1
                """,
                entity_id,
                CONTACT_INFO_REFRESH_TOKEN,
            )
        if row is None:
            return None
        value = row["value"]
        return value.strip() if value else None
    except Exception as exc:  # noqa: BLE001
        if _is_missing_table_or_schema_error(exc):
            return None
        raise


async def _upsert_entity_refresh_token(
    pool: asyncpg.Pool, entity_id: uuid.UUID, refresh_token: str
) -> None:
    """Upsert a refresh token in entity_info for the given companion entity."""
    async with _pool_acquire(pool) as conn:
        await conn.execute(
            """
            INSERT INTO public.entity_info (entity_id, type, value, secured, is_primary)
            VALUES ($1, $2, $3, true, true)
            ON CONFLICT (entity_id, type) DO UPDATE SET
                value = EXCLUDED.value,
                secured = EXCLUDED.secured
            """,
            entity_id,
            CONTACT_INFO_REFRESH_TOKEN,
            refresh_token,
        )


async def _delete_entity_refresh_token(pool: asyncpg.Pool, entity_id: uuid.UUID) -> bool:
    """Delete the refresh token entity_info row for the given companion entity."""
    async with _pool_acquire(pool) as conn:
        result = await conn.execute(
            "DELETE FROM public.entity_info WHERE entity_id = $1 AND type = $2",
            entity_id,
            CONTACT_INFO_REFRESH_TOKEN,
        )
    return result.split()[-1] != "0" if result else False


# ---------------------------------------------------------------------------
# Persistence helpers — dual-source (butler_secrets + entity_info)
# ---------------------------------------------------------------------------


async def store_google_credentials(
    store: CredentialStore,
    *,
    pool: asyncpg.Pool | None = None,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scope: str | None = None,
    account: str | uuid.UUID | None = None,
) -> None:
    """Persist Google OAuth credentials.

    App credentials (client_id, client_secret, scopes) are stored in
    ``butler_secrets`` via *store*.  The refresh token is stored in
    ``public.entity_info`` on the Google account's companion entity via *pool*.

    Secret material (client_secret, refresh_token) is never logged.

    Parameters
    ----------
    store:
        A :class:`~butlers.credential_store.CredentialStore`.
    pool:
        An asyncpg pool for the shared database (for entity_info writes).
        When ``None``, the refresh token is not persisted.
    client_id:
        OAuth client ID (non-secret).
    client_secret:
        OAuth client secret (secret — never logged).
    refresh_token:
        OAuth refresh token (secret — never logged).
    scope:
        Space-separated OAuth scopes granted (optional).
    account:
        Google account selector. May be an email string, a UUID (account id),
        or ``None`` (default: use the primary account).
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

    # Refresh token → public.entity_info on the companion entity
    if pool is not None:
        entity_id = await _resolve_account_entity_id(pool, account)
        if entity_id is not None:
            await _upsert_entity_refresh_token(pool, entity_id, validated.refresh_token)
        else:
            logger.warning(
                "Cannot store refresh token: no google_accounts row found for account=%r. "
                "App credentials were stored; refresh token was skipped.",
                account,
            )

    logger.info(
        "Google OAuth credentials stored in database (client_id=%s, scope=%s)",
        client_id,
        scope,
    )


async def load_google_credentials(
    store: CredentialStore,
    *,
    pool: asyncpg.Pool | None = None,
    account: str | uuid.UUID | None = None,
) -> GoogleCredentials | None:
    """Load Google OAuth credentials.

    App credentials are read from ``butler_secrets`` via *store*.  The refresh
    token is read from ``public.entity_info`` on the account's companion entity
    via *pool*.

    Returns ``None`` if the required credentials (client_id, client_secret,
    refresh_token) are not fully present.

    Parameters
    ----------
    store:
        A :class:`~butlers.credential_store.CredentialStore`.
    pool:
        An asyncpg pool for the shared database (for entity_info reads).
        When ``None``, the refresh token is not loaded.
    account:
        Google account selector. May be an email string, a UUID (account id),
        or ``None`` (default: use the primary account).

    Raises
    ------
    InvalidGoogleCredentialsError
        If the stored data is present but malformed/incomplete.
    """
    client_id = await store.load(KEY_CLIENT_ID)
    client_secret = await store.load(KEY_CLIENT_SECRET)
    scope = await store.load(KEY_SCOPES)

    # Refresh token from companion entity_info
    refresh_token: str | None = None
    if pool is not None:
        entity_id = await _resolve_account_entity_id(pool, account)
        if entity_id is not None:
            refresh_token = await _resolve_entity_refresh_token(pool, entity_id)

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
    refresh token already exists it is preserved (it lives in entity_info).

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
    account: str | uuid.UUID | None = None,
) -> GoogleAppCredentials | None:
    """Load Google app credentials (client_id + client_secret + optional refresh_token).

    Returns ``None`` if no app credentials have been stored yet.  Unlike
    ``load_google_credentials``, this does NOT require a refresh_token.

    Parameters
    ----------
    store:
        A :class:`~butlers.credential_store.CredentialStore`.
    pool:
        An asyncpg pool for the shared database (for entity_info reads).
        When ``None``, refresh_token will be ``None``.
    account:
        Google account selector (email, UUID, or ``None`` for primary).
    """
    client_id = await store.load(KEY_CLIENT_ID)
    client_secret = await store.load(KEY_CLIENT_SECRET)

    if not client_id or not client_secret:
        return None

    # Refresh token from companion entity_info
    refresh_token: str | None = None
    if pool is not None:
        entity_id = await _resolve_account_entity_id(pool, account)
        if entity_id is not None:
            refresh_token = await _resolve_entity_refresh_token(pool, entity_id)

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
    account: str | uuid.UUID | None = None,
    delete_all: bool = False,
) -> bool:
    """Delete stored Google OAuth credentials.

    When *delete_all* is ``True``, removes all refresh tokens across all account
    companion entities AND the app credentials from ``butler_secrets``.

    When *delete_all* is ``False``, removes only the refresh token for the
    specified (or primary) account. App credentials in ``butler_secrets`` are
    preserved because they are shared across accounts.

    Parameters
    ----------
    store:
        A :class:`~butlers.credential_store.CredentialStore`.
    pool:
        An asyncpg pool for the shared database (for entity_info deletes).
    account:
        Google account selector (email, UUID, or ``None`` for primary).
        Ignored when *delete_all* is ``True``.
    delete_all:
        When ``True``, delete all refresh tokens and the shared app credentials.

    Returns
    -------
    bool
        ``True`` if at least one credential was deleted.
    """
    results: list[bool] = []

    if delete_all:
        # Delete app credentials from butler_secrets.
        results.extend(
            [
                await store.delete(KEY_CLIENT_ID),
                await store.delete(KEY_CLIENT_SECRET),
                await store.delete(KEY_SCOPES),
            ]
        )

        # Delete ALL refresh tokens across all companion entities (bulk DELETE).
        if pool is not None:
            async with _pool_acquire(pool) as conn:
                rows = await conn.fetch("SELECT entity_id FROM public.google_accounts")
                entity_ids = [row["entity_id"] for row in rows]
                if entity_ids:
                    delete_result = await conn.execute(
                        "DELETE FROM public.entity_info WHERE entity_id = ANY($1) AND type = $2",
                        entity_ids,
                        CONTACT_INFO_REFRESH_TOKEN,
                    )
                    # asyncpg returns e.g. "DELETE 3"; check count > 0
                    deleted_count = int(delete_result.split()[-1]) if delete_result else 0
                    results.append(deleted_count > 0)
                # Update all accounts to revoked.
                await conn.execute("UPDATE public.google_accounts SET status = 'revoked'")
    else:
        # Only delete the refresh token for the specified (or primary) account.
        if pool is not None:
            entity_id = await _resolve_account_entity_id(pool, account)
            if entity_id is not None:
                deleted = await _delete_entity_refresh_token(pool, entity_id)
                results.append(deleted)
                # Update account status to revoked.
                try:
                    await _mark_account_revoked(pool, entity_id)
                except Exception as exc:  # noqa: BLE001
                    if not _is_missing_table_or_schema_error(exc):
                        raise

    deleted = any(results)
    if deleted:
        logger.info("Google OAuth credentials deleted from database")
    else:
        logger.info("No Google OAuth credentials to delete")
    return deleted


async def _mark_account_revoked(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID | None = None,
    *,
    account_id: uuid.UUID | None = None,
    email: str | None = None,
    primary: bool = False,
) -> None:
    """Persist explicit deletion or a caller-qualified invalid grant for one account.

    The caller owns the grant provenance and stale-token check. Pure probes and
    authorization-code errors never call this transition.
    """
    if sum((entity_id is not None, account_id is not None, email is not None, primary)) != 1:
        raise ValueError("exactly one Google account selector is required")
    if email is not None:
        await pool.execute(
            "UPDATE public.google_accounts SET status = 'revoked' WHERE email = $1",
            email,
        )
    elif primary:
        await pool.execute(
            "UPDATE public.google_accounts SET status = 'revoked' WHERE is_primary = true",
        )
    else:
        async with _pool_acquire(pool) as conn:
            if entity_id is not None:
                await conn.execute(
                    "UPDATE public.google_accounts SET status = 'revoked' WHERE entity_id = $1",
                    entity_id,
                )
            else:
                await conn.execute(
                    "UPDATE public.google_accounts SET status = 'revoked' WHERE id = $1",
                    account_id,
                )


# ---------------------------------------------------------------------------
# Resolution helper (DB-only)
# ---------------------------------------------------------------------------


async def resolve_google_credentials(
    store: CredentialStore,
    *,
    pool: asyncpg.Pool | None = None,
    caller: str = "unknown",
    account: str | uuid.UUID | None = None,
) -> GoogleCredentials:
    """Resolve Google OAuth credentials from DB-backed secret storage.

    Resolution:
    1. App credentials from ``butler_secrets``.
    2. Refresh token from ``public.entity_info`` on the account's companion entity.

    Parameters
    ----------
    store:
        A :class:`~butlers.credential_store.CredentialStore`.
    pool:
        An asyncpg pool for the shared database.
    caller:
        Name of the calling component (used in log messages for traceability).
    account:
        Google account selector (email, UUID, or ``None`` for primary account).

    Returns
    -------
    GoogleCredentials
        Resolved DB credentials.

    Raises
    ------
    MissingGoogleCredentialsError
        If credentials cannot be found in DB, or if the specified account does
        not exist, or if no primary account exists when account=None.
    """
    # Validate account exists when pool is provided.
    if pool is not None:
        entity_id = await _resolve_account_entity_id(pool, account)
        if entity_id is None:
            if account is None:
                raise MissingGoogleCredentialsError(
                    f"[{caller}] No primary Google account is configured. "
                    "Connect a Google account via GET /api/oauth/google/start."
                )
            else:
                raise MissingGoogleCredentialsError(
                    f"[{caller}] Google account {account!r} is not connected. "
                    "Connect the account via GET /api/oauth/google/start."
                )

    try:
        creds = await load_google_credentials(store, pool=pool, account=account)
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


async def _resolve_account_entity_id(
    pool: asyncpg.Pool,
    account: str | uuid.UUID | None,
) -> uuid.UUID | None:
    """Resolve an account selector to a companion entity_id.

    Returns ``None`` when:
    - google_accounts table does not exist (legacy deployment).
    - No account matches the selector.
    - No primary account exists when account=None.
    """
    try:
        async with _pool_acquire(pool) as conn:
            if account is None:
                row = await conn.fetchrow(
                    "SELECT entity_id FROM public.google_accounts WHERE is_primary = true LIMIT 1"
                )
            elif isinstance(account, uuid.UUID):
                row = await conn.fetchrow(
                    "SELECT entity_id FROM public.google_accounts WHERE id = $1 LIMIT 1",
                    account,
                )
            else:
                # Try UUID parse first, then treat as email.
                try:
                    account_uuid = uuid.UUID(str(account))
                    row = await conn.fetchrow(
                        "SELECT entity_id FROM public.google_accounts WHERE id = $1 LIMIT 1",
                        account_uuid,
                    )
                except ValueError:
                    row = await conn.fetchrow(
                        "SELECT entity_id FROM public.google_accounts WHERE email = $1 LIMIT 1",
                        str(account),
                    )
        return row["entity_id"] if row else None
    except Exception as exc:  # noqa: BLE001
        if _is_missing_table_or_schema_error(exc):
            return None
        raise


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


# One transport for Google token grants. Status probes intentionally keep their
# scope-only verdict adapter; only consumers of minted tokens use strict parsing.
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TOKEN_INFO_URL = GOOGLE_TOKEN_URL + "info"
TOKEN_REFRESH_MARGIN_S = 60


async def exchange_google_token(
    client: httpx.AsyncClient,
    data: Mapping[str, str],
    **request_options: Any,
) -> httpx.Response:
    """Send one Google token grant without logging credentials or changing state."""
    return await client.post(GOOGLE_TOKEN_URL, data=dict(data), **request_options)


async def exchange_google_refresh(
    client: httpx.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scopes: frozenset[str] = frozenset(),
    **request_options: Any,
) -> httpx.Response:
    """Single refresh request builder; the caller retains its status adapter."""
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    if scopes:
        data["scope"] = " ".join(sorted(scopes))
    return await exchange_google_token(client, data, **request_options)


async def mint_access_token(
    client: httpx.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scopes: frozenset[str] = frozenset(),
    **request_options: Any,
) -> OAuthTokenPayload:
    """Mint and validate a refresh grant before returning any mutable state.

    HTTP errors keep their actual response for the existing source adapters.
    Only an exact top-level invalid_grant is revocation evidence; callers that
    own a persistent account transition apply that evidence to their own account.
    """
    response = await exchange_google_refresh(
        client,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        scopes=scopes,
        **request_options,
    )
    response.raise_for_status()
    return validate_oauth_token_payload(response.json())


@dataclass
class _GoogleTokenEntry:
    token: str | None = field(default=None, repr=False)
    expires_at: float = 0.0
    generation: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Weak ownership gives one record per live event loop/account/scope/generation,
# without retaining closed clients, credentials or event loops indefinitely.
_GOOGLE_TOKEN_ENTRIES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


class GoogleTokenCache:
    """One in-process cache per credential generation and requested scope set.

    Source fields remain the invalidation boundary (including Health's API401).
    Separate clients for the same account share the token and refresh lock.
    Only validated mints publish state; records and credential fingerprints are
    memory-only and live no longer than their source cache owners.
    """

    def __init__(
        self,
        state: Any,
        token_field: str = "_access_token",
        expiry_field: str = "_token_expires_at",
        clock: Callable[[], Any] | None = None,
    ) -> None:
        try:
            self._state_ref = weakref.ref(state)
        except TypeError:
            # Non-weak-referenceable explicit state containers have no cache
            # backreference in production; the cache still owns no global state.
            self._state_ref = lambda: state
        self._token_field = token_field
        self._expiry_field = expiry_field
        self._clock = clock or (lambda: datetime.now(UTC))
        # Source fields may use monotonic floats or UTC datetimes. Production
        # records always use the same monotonic clock; an injected test clock
        # gets its own domain and cannot seed a production record.
        self._record_clock = clock if clock is not None else time.monotonic
        self._clock_domain = clock if clock not in (None, time.monotonic) else None
        self._credential_key: bytes | None = None
        self._entry: _GoogleTokenEntry | None = None
        self._last_state: tuple[Any, Any] | None = None

    def _read_state(self) -> tuple[Any, Any]:
        state = self._state_ref()
        if state is None:
            raise RuntimeError("Google token cache owner has closed")
        return (getattr(state, self._token_field, None), getattr(state, self._expiry_field, None))

    def _publish(self, token: str, remaining: float) -> None:
        now = self._clock()
        expiry = now + (timedelta(seconds=remaining) if isinstance(now, datetime) else remaining)
        state = self._state_ref()
        if state is None:
            raise RuntimeError("Google token cache owner has closed")
        setattr(state, self._token_field, token)
        setattr(state, self._expiry_field, expiry)
        self._last_state = (token, expiry)

    async def get_token(
        self,
        client: httpx.AsyncClient,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        scopes: frozenset[str] = frozenset(),
        force_refresh: bool = False,
        on_refreshed: Callable[[], Awaitable[None]] | None = None,
        **request_options: Any,
    ) -> str:
        key = hashlib.sha256(
            repr((client_id, client_secret, refresh_token, sorted(scopes))).encode()
        ).digest()
        loop = asyncio.get_running_loop()
        entries = _GOOGLE_TOKEN_ENTRIES.setdefault(loop, weakref.WeakValueDictionary())
        record_key = (key, self._clock_domain)
        entry = entries.get(record_key)
        if entry is None:
            entry = _GoogleTokenEntry()
            entries[record_key] = entry
        self._entry = entry
        observed_generation = entry.generation
        current_state = self._read_state()
        invalidated = self._last_state is not None and current_state != self._last_state
        async with entry.lock:
            now = self._record_clock()
            # A queued caller's invalidation is already satisfied when another
            # caller minted a newer generation while it waited for the lock.
            must_refresh = (
                force_refresh or invalidated
            ) and entry.generation == observed_generation
            # Preserve an already-live local token without promoting mutable
            # legacy fields into a shared minted-token record. Only the strict
            # mint below publishes cross-client state.
            if entry.token is None and self._credential_key in (None, key) and not must_refresh:
                token, expiry = current_state
                if token and expiry:
                    remaining = expiry - self._clock()
                    if isinstance(remaining, timedelta):
                        remaining = remaining.total_seconds()
                    if remaining > TOKEN_REFRESH_MARGIN_S:
                        self._last_state = current_state
                        self._credential_key = key
                        self._entry = None
                        return token
            if not must_refresh and entry.token and now < entry.expires_at - TOKEN_REFRESH_MARGIN_S:
                if self._read_state()[0] != entry.token:
                    self._publish(entry.token, entry.expires_at - now)
                else:
                    self._last_state = self._read_state()
                self._credential_key = key
                return entry.token
            token = await mint_access_token(
                client,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                scopes=scopes,
                **request_options,
            )
            entry.generation += 1
            entry.token = token.access_token
            entry.expires_at = self._record_clock() + token.expires_in
            self._publish(token.access_token, token.expires_in)
            self._credential_key = key
            if on_refreshed is not None:
                await on_refreshed()
            return token.access_token


class GoogleOAuthClient:
    """Module adapter for the shared cache, retaining bounded refresh retries."""

    def __init__(
        self,
        credentials: Any,
        http_client: httpx.AsyncClient,
        on_token_refreshed: Callable[[], Awaitable[None]] | None = None,
        on_token_revoked: Callable[[], Awaitable[None]] | None = None,
        *,
        error_type: type[Exception] = RuntimeError,
    ) -> None:
        self._credentials = credentials
        self._http_client = http_client
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None
        self._cache = GoogleTokenCache(self, expiry_field="_access_token_expires_at")
        self._on_token_refreshed = on_token_refreshed
        self._on_token_revoked = on_token_revoked
        self._error_type = error_type

    async def _notify_refreshed(self) -> None:
        if self._on_token_refreshed is not None:
            try:
                await self._on_token_refreshed()
            except Exception:
                logger.debug("Google token refresh callback failed (best-effort)")

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        for attempt in range(3):
            try:
                return await self._cache.get_token(
                    self._http_client,
                    client_id=self._credentials.client_id,
                    client_secret=self._credentials.client_secret,
                    refresh_token=self._credentials.refresh_token,
                    force_refresh=force_refresh,
                    on_refreshed=self._notify_refreshed,
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPStatusError as exc:
                code = google_oauth_error_code(exc.response)
                if code == "invalid_grant":
                    if self._on_token_revoked is not None:
                        try:
                            await self._on_token_revoked()
                        except Exception:
                            logger.debug("Google revocation callback failed (best-effort)")
                    raise self._error_type(
                        "Google OAuth token refresh failed (invalid_grant)"
                    ) from exc
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                raise self._error_type(
                    f"Google OAuth token refresh failed ({exc.response.status_code})"
                ) from exc
            except httpx.HTTPError as exc:
                raise self._error_type("Google OAuth token refresh request failed") from exc
            except ValueError as exc:
                raise self._error_type(
                    "Google OAuth token endpoint returned an invalid token payload"
                ) from exc
        raise AssertionError("unreachable refresh retry state")
