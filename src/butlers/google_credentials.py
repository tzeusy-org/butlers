"""Shared Google credential storage for butler modules.

Provides a single source of truth for Google OAuth credentials (client_id,
client_secret, refresh_token, scope) that can be consumed by both the Gmail
connector and the Calendar module.

Credentials are stored in a JSONB column in a ``google_oauth_credentials``
table in the butler's PostgreSQL database. Secret material (client_secret,
refresh_token) is never logged in plaintext.

Usage — persisting after OAuth bootstrap::

    async with pool.acquire() as conn:
        await store_google_credentials(
            conn,
            client_id="...",
            client_secret="...",
            refresh_token="...",
            scope="https://www.googleapis.com/auth/gmail.readonly ...",
        )

Usage — loading at module startup::

    async with pool.acquire() as conn:
        creds = await load_google_credentials(conn)
    if creds is None:
        raise MissingGoogleCredentialsError("...")

    client_id = creds.client_id
    refresh_token = creds.refresh_token

Environment variable fallback (for backward compatibility)::

    creds = GoogleCredentials.from_env()

Where the following env vars are consulted:
- ``GOOGLE_OAUTH_CLIENT_ID`` or ``GMAIL_CLIENT_ID`` (client_id)
- ``GOOGLE_OAUTH_CLIENT_SECRET`` or ``GMAIL_CLIENT_SECRET`` (client_secret)
- ``GOOGLE_REFRESH_TOKEN`` or ``GMAIL_REFRESH_TOKEN`` (refresh_token)
- ``GOOGLE_OAUTH_SCOPES`` (scope, optional)
- ``BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON`` (JSON blob, Calendar-style)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

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
    # Env-var factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> GoogleCredentials:
        """Build credentials from environment variables.

        Checks several well-known variable names for maximum backward
        compatibility with both the Gmail connector and Calendar module:

        - client_id: ``GOOGLE_OAUTH_CLIENT_ID`` | ``GMAIL_CLIENT_ID``
        - client_secret: ``GOOGLE_OAUTH_CLIENT_SECRET`` | ``GMAIL_CLIENT_SECRET``
        - refresh_token: ``GOOGLE_REFRESH_TOKEN`` | ``GMAIL_REFRESH_TOKEN``
        - scope: ``GOOGLE_OAUTH_SCOPES`` (optional)

        Also accepts the Calendar-style JSON blob via
        ``BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON``, which is parsed and
        merged into any present individual variables.

        Raises
        ------
        MissingGoogleCredentialsError
            If required fields cannot be resolved from the environment.
        """
        # Try Calendar-style JSON blob first
        cal_json_raw = os.environ.get("BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON", "").strip()
        cal_data: dict[str, str] = {}
        if cal_json_raw:
            try:
                parsed = json.loads(cal_json_raw)
                if isinstance(parsed, dict):
                    cal_data = {k: v for k, v in parsed.items() if isinstance(v, str)}
            except json.JSONDecodeError:
                pass  # Ignore malformed JSON — fall through to individual vars

        def _pick(*env_vars: str, fallback: str = "") -> str:
            """Return the first non-empty value among env vars."""
            for var in env_vars:
                val = os.environ.get(var, "").strip()
                if val:
                    return val
            return fallback

        client_id = _pick("GOOGLE_OAUTH_CLIENT_ID", "GMAIL_CLIENT_ID") or cal_data.get(
            "client_id", ""
        )
        client_secret = _pick("GOOGLE_OAUTH_CLIENT_SECRET", "GMAIL_CLIENT_SECRET") or cal_data.get(
            "client_secret", ""
        )
        refresh_token = _pick("GOOGLE_REFRESH_TOKEN", "GMAIL_REFRESH_TOKEN") or cal_data.get(
            "refresh_token", ""
        )
        scope = _pick("GOOGLE_OAUTH_SCOPES") or None

        missing = [
            name
            for name, val in [
                ("client_id", client_id),
                ("client_secret", client_secret),
                ("refresh_token", refresh_token),
            ]
            if not val
        ]
        if missing:
            raise MissingGoogleCredentialsError(
                f"Missing required Google credential field(s) from environment: "
                f"{', '.join(missing)}. "
                f"Set GOOGLE_OAUTH_CLIENT_ID / GMAIL_CLIENT_ID, "
                f"GOOGLE_OAUTH_CLIENT_SECRET / GMAIL_CLIENT_SECRET, and "
                f"GOOGLE_REFRESH_TOKEN / GMAIL_REFRESH_TOKEN."
            )

        return cls(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            scope=scope,
        )

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
# DB persistence helpers
# ---------------------------------------------------------------------------

_TABLE = "google_oauth_credentials"
_SINGLETON_KEY = "google"


async def store_google_credentials(
    conn: Any,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scope: str | None = None,
) -> None:
    """Persist Google OAuth credentials to the database.

    Uses an UPSERT (INSERT ... ON CONFLICT DO UPDATE) so this is
    idempotent — calling it again after a re-bootstrap overwrites the
    previous record.

    Secret material (client_secret, refresh_token) is never logged.

    Parameters
    ----------
    conn:
        An asyncpg connection or pool.
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
    payload = {
        "client_id": validated.client_id,
        "client_secret": validated.client_secret,
        "refresh_token": validated.refresh_token,
        "scope": validated.scope,
        "stored_at": datetime.now(UTC).isoformat(),
    }

    await conn.execute(
        f"""
        INSERT INTO {_TABLE} (credential_key, credentials)
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
    # Do NOT log client_secret or refresh_token.


async def load_google_credentials(conn: Any) -> GoogleCredentials | None:
    """Load Google OAuth credentials from the database.

    Returns ``None`` if no credentials have been stored yet.

    Parameters
    ----------
    conn:
        An asyncpg connection or pool.

    Returns
    -------
    GoogleCredentials | None
        The stored credentials, or None if the table is empty.

    Raises
    ------
    InvalidGoogleCredentialsError
        If the stored data is present but malformed/incomplete.
    """
    row = await conn.fetchrow(
        f"SELECT credentials FROM {_TABLE} WHERE credential_key = $1",
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


# ---------------------------------------------------------------------------
# Resolution helper (DB-first, env fallback)
# ---------------------------------------------------------------------------


async def resolve_google_credentials(
    conn: Any,
    *,
    caller: str = "unknown",
) -> GoogleCredentials:
    """Resolve Google OAuth credentials from DB, falling back to env vars.

    Resolution order:
    1. Database (``google_oauth_credentials`` table via ``load_google_credentials``).
    2. Environment variables (via ``GoogleCredentials.from_env``).

    This allows a single OAuth bootstrap (via the ``/api/oauth/google/callback``
    endpoint) to satisfy both Gmail connector and Calendar module startup
    requirements.

    Parameters
    ----------
    conn:
        An asyncpg connection or pool.
    caller:
        Name of the calling component (used in log messages for traceability).

    Returns
    -------
    GoogleCredentials
        Resolved credentials (DB or env).

    Raises
    ------
    MissingGoogleCredentialsError
        If credentials cannot be found in either source, with a clear
        actionable message describing how to bootstrap.
    """
    # 1. Try DB
    try:
        creds = await load_google_credentials(conn)
    except InvalidGoogleCredentialsError as exc:
        logger.warning(
            "[%s] Stored Google credentials are invalid, falling back to env vars: %s",
            caller,
            exc,
        )
        creds = None

    if creds is not None:
        logger.debug("[%s] Resolved Google credentials from database", caller)
        return creds

    # 2. Try env vars
    try:
        creds = GoogleCredentials.from_env()
        logger.debug("[%s] Resolved Google credentials from environment variables", caller)
        return creds
    except MissingGoogleCredentialsError as env_exc:
        raise MissingGoogleCredentialsError(
            f"[{caller}] Google OAuth credentials are not available. "
            f"Bootstrap via GET /api/oauth/google/start, or set the environment "
            f"variables GOOGLE_OAUTH_CLIENT_ID / GMAIL_CLIENT_ID, "
            f"GOOGLE_OAUTH_CLIENT_SECRET / GMAIL_CLIENT_SECRET, and "
            f"GOOGLE_REFRESH_TOKEN / GMAIL_REFRESH_TOKEN. "
            f"Details: {env_exc}"
        ) from env_exc
