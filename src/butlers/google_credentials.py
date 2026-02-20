"""Shared Google credential storage for butler modules.

Provides a single source of truth for Google OAuth credentials (client_id,
client_secret, refresh_token, scope) that can be consumed by both the Gmail
connector and the Calendar module.

Credentials are stored as individual rows in the ``butler_secrets`` table via
:class:`~butlers.credential_store.CredentialStore`, using the following key
names:

- ``GOOGLE_OAUTH_CLIENT_ID``
- ``GOOGLE_OAUTH_CLIENT_SECRET``
- ``GOOGLE_REFRESH_TOKEN``
- ``GOOGLE_OAUTH_SCOPES`` (optional)

Secret material (client_secret, refresh_token) is never logged in plaintext.

Usage — persisting after OAuth bootstrap::

    store = CredentialStore(pool)
    await store_google_credentials(
        store,
        client_id="...",
        client_secret="...",
        refresh_token="...",
        scope="https://www.googleapis.com/auth/gmail.readonly ...",
    )

Usage — loading at module startup::

    store = CredentialStore(pool)
    creds = await load_google_credentials(store)
    if creds is None:
        raise MissingGoogleCredentialsError("...")

    client_id = creds.client_id
    refresh_token = creds.refresh_token

"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# butler_secrets key names for Google OAuth
# ---------------------------------------------------------------------------

KEY_CLIENT_ID = "GOOGLE_OAUTH_CLIENT_ID"
KEY_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET"
KEY_REFRESH_TOKEN = "GOOGLE_REFRESH_TOKEN"
KEY_SCOPES = "GOOGLE_OAUTH_SCOPES"

_GOOGLE_CATEGORY = "google"

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
# CredentialStore-based persistence helpers
# ---------------------------------------------------------------------------


async def store_google_credentials(
    store_or_conn: Any,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scope: str | None = None,
) -> None:
    """Persist Google OAuth credentials.

    When *store_or_conn* is a :class:`~butlers.credential_store.CredentialStore`
    instance the four fields are stored as individual rows in ``butler_secrets``.
    When it is a legacy asyncpg connection/pool the old ``google_oauth_credentials``
    JSONB-blob path is used for backward compatibility.

    Secret material (client_secret, refresh_token) is never logged.

    Parameters
    ----------
    store_or_conn:
        Either a :class:`~butlers.credential_store.CredentialStore` or an
        asyncpg connection/pool (legacy support).
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

    if isinstance(store_or_conn, CredentialStore):
        await store_or_conn.store(
            KEY_CLIENT_ID,
            validated.client_id,
            category=_GOOGLE_CATEGORY,
            description="Google OAuth client ID",
            is_sensitive=False,
        )
        await store_or_conn.store(
            KEY_CLIENT_SECRET,
            validated.client_secret,
            category=_GOOGLE_CATEGORY,
            description="Google OAuth client secret",
            is_sensitive=True,
        )
        await store_or_conn.store(
            KEY_REFRESH_TOKEN,
            validated.refresh_token,
            category=_GOOGLE_CATEGORY,
            description="Google OAuth refresh token",
            is_sensitive=True,
        )
        if validated.scope:
            await store_or_conn.store(
                KEY_SCOPES,
                validated.scope,
                category=_GOOGLE_CATEGORY,
                description="Google OAuth granted scopes",
                is_sensitive=False,
            )
        logger.info(
            "Google OAuth credentials stored in butler_secrets (client_id=%s, scope=%s)",
            client_id,
            scope,
        )
    else:
        # Legacy path: asyncpg connection/pool → google_oauth_credentials JSONB blob
        await _legacy_store_google_credentials(
            store_or_conn,
            client_id=validated.client_id,
            client_secret=validated.client_secret,
            refresh_token=validated.refresh_token,
            scope=validated.scope,
        )


async def load_google_credentials(store_or_conn: Any) -> GoogleCredentials | None:
    """Load Google OAuth credentials.

    When *store_or_conn* is a :class:`~butlers.credential_store.CredentialStore`
    the four keys are read from ``butler_secrets``.  When it is a legacy asyncpg
    connection/pool the old ``google_oauth_credentials`` table is consulted.

    Returns ``None`` if the required credentials (client_id, client_secret,
    refresh_token) are not fully present.

    Raises
    ------
    InvalidGoogleCredentialsError
        If the stored data is present but malformed/incomplete.
    """
    if isinstance(store_or_conn, CredentialStore):
        client_id = await store_or_conn.load(KEY_CLIENT_ID)
        client_secret = await store_or_conn.load(KEY_CLIENT_SECRET)
        refresh_token = await store_or_conn.load(KEY_REFRESH_TOKEN)
        scope = await store_or_conn.load(KEY_SCOPES)

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
    else:
        # Legacy path
        return await _legacy_load_google_credentials(store_or_conn)


async def store_app_credentials(
    store_or_conn: Any,
    *,
    client_id: str,
    client_secret: str,
) -> None:
    """Persist Google OAuth app credentials (client_id + client_secret).

    This is a partial upsert that stores only the app credentials.  If a
    refresh token already exists in the store it is preserved.

    Secret material (client_secret) is never logged.

    Parameters
    ----------
    store_or_conn:
        Either a :class:`~butlers.credential_store.CredentialStore` or an
        asyncpg connection/pool (legacy support).
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

    if isinstance(store_or_conn, CredentialStore):
        await store_or_conn.store(
            KEY_CLIENT_ID,
            client_id,
            category=_GOOGLE_CATEGORY,
            description="Google OAuth client ID",
            is_sensitive=False,
        )
        await store_or_conn.store(
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
    else:
        # Legacy path
        await _legacy_store_app_credentials(
            store_or_conn, client_id=client_id, client_secret=client_secret
        )


async def load_app_credentials(store_or_conn: Any) -> GoogleAppCredentials | None:
    """Load Google app credentials (client_id + client_secret).

    Returns ``None`` if no credentials have been stored yet.  Unlike
    ``load_google_credentials``, this does NOT require a refresh_token.

    Parameters
    ----------
    store_or_conn:
        Either a :class:`~butlers.credential_store.CredentialStore` or an
        asyncpg connection/pool (legacy support).
    """
    if isinstance(store_or_conn, CredentialStore):
        client_id = await store_or_conn.load(KEY_CLIENT_ID)
        client_secret = await store_or_conn.load(KEY_CLIENT_SECRET)

        if not client_id or not client_secret:
            return None

        refresh_token = await store_or_conn.load(KEY_REFRESH_TOKEN)
        scope = await store_or_conn.load(KEY_SCOPES)

        return GoogleAppCredentials(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token or None,
            scope=scope or None,
        )
    else:
        # Legacy path
        return await _legacy_load_app_credentials(store_or_conn)


async def delete_google_credentials(store_or_conn: Any) -> bool:
    """Delete stored Google OAuth credentials.

    Removes all four Google credential keys from ``butler_secrets``
    (or the old ``google_oauth_credentials`` row when a legacy connection is
    provided).

    Parameters
    ----------
    store_or_conn:
        Either a :class:`~butlers.credential_store.CredentialStore` or an
        asyncpg connection/pool (legacy support).

    Returns
    -------
    bool
        ``True`` if at least one credential was deleted.
    """
    if isinstance(store_or_conn, CredentialStore):
        results = [
            await store_or_conn.delete(KEY_CLIENT_ID),
            await store_or_conn.delete(KEY_CLIENT_SECRET),
            await store_or_conn.delete(KEY_REFRESH_TOKEN),
            await store_or_conn.delete(KEY_SCOPES),
        ]
        deleted = any(results)
        if deleted:
            logger.info("Google OAuth credentials deleted from butler_secrets")
        else:
            logger.info("No Google OAuth credentials to delete")
        return deleted
    else:
        # Legacy path
        return await _legacy_delete_google_credentials(store_or_conn)


# ---------------------------------------------------------------------------
# Resolution helper (DB-only)
# ---------------------------------------------------------------------------


async def resolve_google_credentials(
    store_or_conn: Any,
    *,
    caller: str = "unknown",
) -> GoogleCredentials:
    """Resolve Google OAuth credentials from DB-backed secret storage.

    Resolution:
    1. Database (``butler_secrets`` via ``load_google_credentials``).

    Parameters
    ----------
    store_or_conn:
        Either a :class:`~butlers.credential_store.CredentialStore` or an
        asyncpg connection/pool (legacy support).
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
    # 1. Try DB
    try:
        creds = await load_google_credentials(store_or_conn)
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


# ---------------------------------------------------------------------------
# Legacy asyncpg helpers (kept for backward compatibility)
# These are used when callers pass a raw asyncpg connection/pool instead
# of a CredentialStore instance.  They will remain until all callers are
# migrated to CredentialStore.
# ---------------------------------------------------------------------------

_LEGACY_TABLE = "google_oauth_credentials"
_SINGLETON_KEY = "google"


async def _legacy_store_google_credentials(
    conn: Any,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scope: str | None = None,
) -> None:
    """Persist credentials to the legacy google_oauth_credentials table."""
    import json
    from datetime import UTC, datetime

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "scope": scope,
        "stored_at": datetime.now(UTC).isoformat(),
    }

    await conn.execute(
        f"""
        INSERT INTO {_LEGACY_TABLE} (credential_key, credentials)
        VALUES ($1, $2::jsonb)
        ON CONFLICT (credential_key)
        DO UPDATE SET
            credentials = EXCLUDED.credentials,
            updated_at = now()
        """,
        _SINGLETON_KEY,
        json.dumps(payload),
    )
    logger.info(
        "Google OAuth credentials stored in DB (client_id=%s, scope=%s)",
        client_id,
        scope,
    )


async def _legacy_load_google_credentials(conn: Any) -> GoogleCredentials | None:
    """Load credentials from the legacy google_oauth_credentials table."""
    import json

    row = await conn.fetchrow(
        f"SELECT credentials FROM {_LEGACY_TABLE} WHERE credential_key = $1",
        _SINGLETON_KEY,
    )
    if row is None:
        return None

    raw = row["credentials"]
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidGoogleCredentialsError(
                f"Stored Google credentials JSON is malformed: {exc}"
            ) from exc
    elif isinstance(raw, dict):
        data = raw
    else:
        raise InvalidGoogleCredentialsError(
            f"Stored Google credentials has unexpected type: {type(raw).__name__}"
        )

    missing = [
        field for field in ("client_id", "client_secret", "refresh_token") if not data.get(field)
    ]
    if missing:
        raise InvalidGoogleCredentialsError(
            f"Stored Google credentials are missing required field(s): "
            f"{', '.join(missing)}. "
            f"Re-run the OAuth bootstrap to replace the stored credentials."
        )

    return GoogleCredentials(
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        refresh_token=data["refresh_token"],
        scope=data.get("scope"),
    )


async def _legacy_store_app_credentials(
    conn: Any,
    *,
    client_id: str,
    client_secret: str,
) -> None:
    """Persist app credentials to the legacy google_oauth_credentials table."""
    import json
    from datetime import UTC, datetime

    # Load existing credentials to preserve refresh_token if present
    existing: dict = {}
    row = await conn.fetchrow(
        f"SELECT credentials FROM {_LEGACY_TABLE} WHERE credential_key = $1",
        _SINGLETON_KEY,
    )
    if row is not None:
        raw = row["credentials"]
        if isinstance(raw, str):
            try:
                existing = json.loads(raw)
            except json.JSONDecodeError:
                existing = {}
        elif isinstance(raw, dict):
            existing = raw

    # Build the new payload — preserve any existing refresh_token/scope
    payload: dict = {
        "client_id": client_id,
        "client_secret": client_secret,
        "stored_at": datetime.now(UTC).isoformat(),
    }
    if existing.get("refresh_token"):
        payload["refresh_token"] = existing["refresh_token"]
    if existing.get("scope"):
        payload["scope"] = existing["scope"]

    await conn.execute(
        f"""
        INSERT INTO {_LEGACY_TABLE} (credential_key, credentials)
        VALUES ($1, $2::jsonb)
        ON CONFLICT (credential_key)
        DO UPDATE SET
            credentials = EXCLUDED.credentials,
            updated_at = now()
        """,
        _SINGLETON_KEY,
        json.dumps(payload),
    )
    logger.info(
        "Google app credentials (client_id + client_secret) stored in DB (client_id=%s)",
        client_id,
    )


async def _legacy_load_app_credentials(conn: Any) -> GoogleAppCredentials | None:
    """Load app credentials from the legacy google_oauth_credentials table."""
    import json

    row = await conn.fetchrow(
        f"SELECT credentials FROM {_LEGACY_TABLE} WHERE credential_key = $1",
        _SINGLETON_KEY,
    )
    if row is None:
        return None

    raw = row["credentials"]
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidGoogleCredentialsError(
                f"Stored Google credentials JSON is malformed: {exc}"
            ) from exc
    elif isinstance(raw, dict):
        data = raw
    else:
        raise InvalidGoogleCredentialsError(
            f"Stored Google credentials has unexpected type: {type(raw).__name__}"
        )

    client_id = data.get("client_id", "").strip()
    client_secret = data.get("client_secret", "").strip()
    if not client_id or not client_secret:
        return None

    return GoogleAppCredentials(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=data.get("refresh_token") or None,
        scope=data.get("scope") or None,
    )


async def _legacy_delete_google_credentials(conn: Any) -> bool:
    """Delete credentials from the legacy google_oauth_credentials table."""
    result = await conn.execute(
        f"DELETE FROM {_LEGACY_TABLE} WHERE credential_key = $1",
        _SINGLETON_KEY,
    )
    deleted = result.split()[-1] != "0" if result else False
    if deleted:
        logger.info("Google OAuth credentials deleted from DB")
    else:
        logger.info("No Google OAuth credentials to delete")
    return deleted
