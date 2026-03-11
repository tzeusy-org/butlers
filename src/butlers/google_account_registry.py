"""Google Account Registry — CRUD for connected Google accounts.

Provides the single source of truth for multi-account Google OAuth identities.
Each Google account has:
  - A row in ``shared.google_accounts`` (email, scopes, primary flag, status).
  - A companion entity in ``shared.entities`` (roles=['google_account']) that
    anchors the refresh token in ``shared.entity_info``.

Public API
----------
- :func:`create_google_account` — register a new Google account after OAuth
- :func:`list_google_accounts` — list all connected accounts
- :func:`get_google_account` — look up by email, UUID, or get primary
- :func:`set_primary_account` — atomic swap of the primary flag
- :func:`disconnect_account` — revoke token, cleanup, auto-promote primary

Environment variables
---------------------
``GOOGLE_MAX_ACCOUNTS``
    Soft limit on the number of active accounts (default 10).  When the
    count reaches this limit, :func:`create_google_account` raises
    :exc:`GoogleAccountLimitExceededError`.

Design notes
------------
- ``shared.google_accounts.entity_id`` references a *companion* entity, not
  the owner entity.  The companion entity is created automatically by
  :func:`create_google_account`.
- The partial unique index ``ix_google_accounts_primary_singleton`` enforces
  at most one primary account at the DB level.  :func:`set_primary_account`
  uses a single transaction to clear the old primary before setting the new one.
- Token revocation calls ``https://oauth2.googleapis.com/revoke``.  Network
  failures are logged but do NOT block local cleanup.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_MAX_ACCOUNTS = 10
_GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def _max_accounts() -> int:
    """Return the configured soft limit for Google accounts."""
    raw = os.environ.get("GOOGLE_MAX_ACCOUNTS", "").strip()
    if raw.isdigit():
        return int(raw)
    return _DEFAULT_MAX_ACCOUNTS


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GoogleAccountNotFoundError(Exception):
    """Raised when a requested Google account does not exist."""


class GoogleAccountAlreadyExistsError(Exception):
    """Raised when attempting to create an account with an already-registered email."""


class GoogleAccountLimitExceededError(Exception):
    """Raised when the soft limit on active Google accounts would be exceeded."""


class MissingGoogleCredentialsError(Exception):
    """Raised when no Google account is available (e.g. no primary set)."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class GoogleAccount:
    """Represents a connected Google account row.

    Attributes
    ----------
    id:
        UUID primary key of the google_accounts row.
    entity_id:
        UUID of the companion entity in shared.entities.
    email:
        Authenticated Google email address.
    display_name:
        Display name from the Google profile.
    is_primary:
        Whether this is the active primary account.
    granted_scopes:
        OAuth scopes granted at last connect.
    status:
        One of 'active', 'revoked', 'expired'.
    connected_at:
        Timestamp when the account was first connected.
    last_token_refresh_at:
        Timestamp of the last token refresh (may be None).
    """

    id: uuid.UUID
    entity_id: uuid.UUID
    email: str | None
    display_name: str | None
    is_primary: bool
    granted_scopes: list[str]
    status: str
    connected_at: datetime
    last_token_refresh_at: datetime | None

    @classmethod
    def _from_row(cls, row: Any) -> GoogleAccount:
        return cls(
            id=row["id"],
            entity_id=row["entity_id"],
            email=row["email"],
            display_name=row["display_name"],
            is_primary=row["is_primary"],
            granted_scopes=list(row["granted_scopes"] or []),
            status=row["status"],
            connected_at=row["connected_at"],
            last_token_refresh_at=row["last_token_refresh_at"],
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _count_active_accounts(conn: Any) -> int:
    """Return the number of active google_accounts rows."""
    row = await conn.fetchrow(
        "SELECT COUNT(*) AS cnt FROM shared.google_accounts WHERE status = 'active'"
    )
    return int(row["cnt"]) if row else 0


async def _create_companion_entity(conn: Any, email: str | None) -> uuid.UUID:
    """Create a companion entity for a Google account.

    Returns the UUID of the created (or pre-existing) companion entity.
    """
    canonical_name = f"google-account:{email}" if email else f"google-account:{uuid.uuid4()}"
    row = await conn.fetchrow(
        """
        INSERT INTO shared.entities (tenant_id, canonical_name, entity_type, roles)
        VALUES ('shared', $1, 'other', ARRAY['google_account'])
        ON CONFLICT (tenant_id, canonical_name, entity_type) DO UPDATE
            SET roles = ARRAY['google_account']
        RETURNING id
        """,
        canonical_name,
    )
    return row["id"]


async def _has_primary_account(conn: Any) -> bool:
    """Return True if any account currently has is_primary=true."""
    row = await conn.fetchrow(
        "SELECT 1 FROM shared.google_accounts WHERE is_primary = true LIMIT 1"
    )
    return row is not None


async def _get_oldest_active_account_id(conn: Any, *, exclude_id: uuid.UUID) -> uuid.UUID | None:
    """Return the id of the oldest active account (by connected_at), excluding exclude_id."""
    row = await conn.fetchrow(
        """
        SELECT id FROM shared.google_accounts
        WHERE status = 'active' AND id != $1
        ORDER BY connected_at ASC
        LIMIT 1
        """,
        exclude_id,
    )
    return row["id"] if row else None


async def _revoke_token_with_google(refresh_token: str) -> None:
    """Attempt to revoke a refresh token with Google.

    Network failures and already-revoked tokens are logged and swallowed —
    they must not block local cleanup.
    """
    try:
        import aiohttp  # noqa: PLC0415

        async with aiohttp.ClientSession() as session:
            async with session.post(
                _GOOGLE_REVOKE_URL,
                params={"token": refresh_token},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    logger.info("Google token revocation succeeded")
                else:
                    body = await resp.text()
                    logger.warning(
                        "Google token revocation returned HTTP %s: %s",
                        resp.status,
                        body[:200],
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Google token revocation failed (non-blocking): %s", exc)


async def _get_refresh_token(conn: Any, entity_id: uuid.UUID) -> str | None:
    """Fetch the refresh token value from entity_info for the given entity."""
    row = await conn.fetchrow(
        """
        SELECT value FROM shared.entity_info
        WHERE entity_id = $1 AND type = 'google_oauth_refresh'
        LIMIT 1
        """,
        entity_id,
    )
    return row["value"] if row else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_google_account(
    pool: asyncpg.Pool,
    *,
    email: str | None,
    display_name: str | None = None,
    scopes: list[str] | None = None,
    refresh_token: str | None = None,
) -> GoogleAccount:
    """Register a new Google account after OAuth callback.

    Creates a companion entity in shared.entities and inserts a row in
    shared.google_accounts.  If no other accounts exist, the new account
    is automatically set as primary.

    If refresh_token is provided, it is stored in shared.entity_info on the
    companion entity (type='google_oauth_refresh', secured=true).

    Parameters
    ----------
    pool:
        asyncpg pool connected to the shared database.
    email:
        The authenticated Google email address.
    display_name:
        Display name from the Google profile (optional).
    scopes:
        List of granted OAuth scopes (optional).
    refresh_token:
        The OAuth refresh token to persist on the companion entity (optional).

    Returns
    -------
    GoogleAccount
        The newly created account record.

    Raises
    ------
    GoogleAccountAlreadyExistsError
        If an account with the given email already exists.
    GoogleAccountLimitExceededError
        If the active account count is at or above the soft limit.
    """
    granted_scopes = scopes or []

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Soft limit check.
            active_count = await _count_active_accounts(conn)
            if active_count >= _max_accounts():
                raise GoogleAccountLimitExceededError(
                    f"Google account limit reached ({active_count}/{_max_accounts()}). "
                    "Disconnect an existing account before adding a new one, or raise "
                    "GOOGLE_MAX_ACCOUNTS."
                )

            # Duplicate email check.
            if email:
                existing = await conn.fetchrow(
                    "SELECT id FROM shared.google_accounts WHERE email = $1",
                    email,
                )
                if existing is not None:
                    raise GoogleAccountAlreadyExistsError(
                        f"A Google account with email {email!r} is already connected."
                    )

            # Determine primary flag: first account wins.
            is_primary = not await _has_primary_account(conn)

            # Create companion entity.
            entity_id = await _create_companion_entity(conn, email)

            # Insert google_accounts row.
            row = await conn.fetchrow(
                """
                INSERT INTO shared.google_accounts (
                    entity_id, email, display_name, is_primary, granted_scopes, status
                )
                VALUES ($1, $2, $3, $4, $5::text[], 'active')
                RETURNING
                    id, entity_id, email, display_name, is_primary,
                    granted_scopes, status, connected_at, last_token_refresh_at
                """,
                entity_id,
                email,
                display_name,
                is_primary,
                granted_scopes,
            )

            # Persist refresh token if provided.
            if refresh_token:
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

            account = GoogleAccount._from_row(row)

    logger.info(
        "Google account created: email=%r is_primary=%s scopes=%s",
        email,
        is_primary,
        granted_scopes,
    )
    return account


async def list_google_accounts(pool: asyncpg.Pool) -> list[GoogleAccount]:
    """List all connected Google accounts.

    Returns accounts ordered by is_primary DESC, connected_at ASC.

    Parameters
    ----------
    pool:
        asyncpg pool connected to the shared database.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, entity_id, email, display_name, is_primary,
                   granted_scopes, status, connected_at, last_token_refresh_at
            FROM shared.google_accounts
            ORDER BY is_primary DESC, connected_at ASC
            """
        )
    return [GoogleAccount._from_row(row) for row in rows]


async def get_google_account(
    pool: asyncpg.Pool,
    account: str | uuid.UUID | None = None,
) -> GoogleAccount:
    """Look up a Google account by email, UUID, or return the primary.

    Parameters
    ----------
    pool:
        asyncpg pool connected to the shared database.
    account:
        - ``None`` → return the primary account.
        - ``str`` that is a valid UUID → look up by id.
        - ``str`` (email) → look up by email.
        - ``uuid.UUID`` → look up by id.

    Returns
    -------
    GoogleAccount
        The matching account.

    Raises
    ------
    MissingGoogleCredentialsError
        When account is None and no primary account exists.
    GoogleAccountNotFoundError
        When the specified email or UUID is not found.
    """
    async with pool.acquire() as conn:
        if account is None:
            # Default: primary account.
            row = await conn.fetchrow(
                """
                SELECT id, entity_id, email, display_name, is_primary,
                       granted_scopes, status, connected_at, last_token_refresh_at
                FROM shared.google_accounts
                WHERE is_primary = true
                LIMIT 1
                """
            )
            if row is None:
                raise MissingGoogleCredentialsError(
                    "No primary Google account is configured. "
                    "Connect a Google account and re-run OAuth."
                )
            return GoogleAccount._from_row(row)

        # Resolve identifier type.
        account_id: uuid.UUID | None = None
        if isinstance(account, uuid.UUID):
            account_id = account
        else:
            # Try to parse as UUID string.
            try:
                account_id = uuid.UUID(str(account))
            except ValueError:
                account_id = None

        if account_id is not None:
            row = await conn.fetchrow(
                """
                SELECT id, entity_id, email, display_name, is_primary,
                       granted_scopes, status, connected_at, last_token_refresh_at
                FROM shared.google_accounts
                WHERE id = $1
                LIMIT 1
                """,
                account_id,
            )
            if row is None:
                raise GoogleAccountNotFoundError(f"No Google account found with id={account_id}")
            return GoogleAccount._from_row(row)

        # Treat as email string.
        row = await conn.fetchrow(
            """
            SELECT id, entity_id, email, display_name, is_primary,
                   granted_scopes, status, connected_at, last_token_refresh_at
            FROM shared.google_accounts
            WHERE email = $1
            LIMIT 1
            """,
            str(account),
        )
        if row is None:
            raise GoogleAccountNotFoundError(f"No Google account found with email={account!r}")
        return GoogleAccount._from_row(row)


async def set_primary_account(
    pool: asyncpg.Pool,
    account_id: uuid.UUID,
) -> GoogleAccount:
    """Atomically set a Google account as the primary.

    Clears the current primary (if any) and sets the target account as
    primary within a single transaction, relying on the partial unique index
    to enforce the singleton constraint.

    Parameters
    ----------
    pool:
        asyncpg pool connected to the shared database.
    account_id:
        UUID of the account to promote to primary.

    Returns
    -------
    GoogleAccount
        The updated account record.

    Raises
    ------
    GoogleAccountNotFoundError
        If the target account does not exist.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Verify target exists.
            target = await conn.fetchrow(
                "SELECT id FROM shared.google_accounts WHERE id = $1",
                account_id,
            )
            if target is None:
                raise GoogleAccountNotFoundError(f"No Google account found with id={account_id}")

            # Clear existing primary (any account that is currently primary).
            await conn.execute(
                "UPDATE shared.google_accounts SET is_primary = false WHERE is_primary = true"
            )

            # Set target as primary.
            row = await conn.fetchrow(
                """
                UPDATE shared.google_accounts
                SET is_primary = true
                WHERE id = $1
                RETURNING
                    id, entity_id, email, display_name, is_primary,
                    granted_scopes, status, connected_at, last_token_refresh_at
                """,
                account_id,
            )

    account = GoogleAccount._from_row(row)
    logger.info("Primary Google account set: email=%r id=%s", account.email, account.id)
    return account


async def disconnect_account(
    pool: asyncpg.Pool,
    account_id: uuid.UUID,
    *,
    hard_delete: bool = False,
) -> None:
    """Disconnect a Google account.

    Full disconnect flow:
    1. Fetch the refresh token from entity_info.
    2. Attempt token revocation with Google (failures are non-blocking).
    3. Delete the entity_info row for the refresh token.
    4. Update google_accounts.status to 'revoked' (or hard-delete the row).
    5. If the disconnected account was primary and other accounts exist,
       auto-promote the oldest remaining active account.

    Parameters
    ----------
    pool:
        asyncpg pool connected to the shared database.
    account_id:
        UUID of the account to disconnect.
    hard_delete:
        When True, the google_accounts row and companion entity are deleted
        (CASCADE removes entity_info).  When False (default), only the
        status is updated to 'revoked'.

    Raises
    ------
    GoogleAccountNotFoundError
        If the account does not exist.
    """
    async with pool.acquire() as conn:
        # Fetch account details first.
        account_row = await conn.fetchrow(
            """
            SELECT id, entity_id, email, is_primary, status
            FROM shared.google_accounts
            WHERE id = $1
            """,
            account_id,
        )
        if account_row is None:
            raise GoogleAccountNotFoundError(f"No Google account found with id={account_id}")

        entity_id: uuid.UUID = account_row["entity_id"]
        was_primary: bool = account_row["is_primary"]
        email = account_row["email"]

        # Fetch refresh token before deleting.
        refresh_token = await _get_refresh_token(conn, entity_id)

    # Revoke token with Google (outside DB transaction — non-blocking).
    # Failures (network error, already revoked) are caught and logged; they
    # must not block local cleanup.
    if refresh_token:
        try:
            await _revoke_token_with_google(refresh_token)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Token revocation call failed (non-blocking): %s", exc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            if hard_delete:
                # Hard delete: remove the companion entity (CASCADE handles the rest).
                await conn.execute(
                    "DELETE FROM shared.entities WHERE id = $1",
                    entity_id,
                )
                logger.info("Google account hard-deleted: email=%r id=%s", email, account_id)
            else:
                # Soft disconnect: delete entity_info token, mark status revoked.
                await conn.execute(
                    """
                    DELETE FROM shared.entity_info
                    WHERE entity_id = $1 AND type = 'google_oauth_refresh'
                    """,
                    entity_id,
                )
                await conn.execute(
                    "UPDATE shared.google_accounts SET status = 'revoked' WHERE id = $1",
                    account_id,
                )
                logger.info(
                    "Google account disconnected (revoked): email=%r id=%s", email, account_id
                )

            # Auto-promote oldest remaining active account if this was primary.
            if was_primary:
                if not hard_delete:
                    next_id = await _get_oldest_active_account_id(conn, exclude_id=account_id)
                else:
                    # After hard delete the row is gone; find oldest active without exclusion.
                    row = await conn.fetchrow(
                        """
                        SELECT id FROM shared.google_accounts
                        WHERE status = 'active'
                        ORDER BY connected_at ASC
                        LIMIT 1
                        """
                    )
                    next_id = row["id"] if row else None

                if next_id is not None:
                    await conn.execute(
                        "UPDATE shared.google_accounts SET is_primary = true WHERE id = $1",
                        next_id,
                    )
                    logger.info("Auto-promoted Google account to primary: id=%s", next_id)
                else:
                    logger.info(
                        "No remaining active Google accounts to auto-promote after disconnect."
                    )
