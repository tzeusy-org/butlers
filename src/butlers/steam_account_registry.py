"""Steam Account Registry — CRUD and lookup for connected Steam accounts.

Provides the single source of truth for Steam account identities.
Each Steam account has:
  - A row in ``public.steam_accounts`` (steam_id, display_name, is_primary, status, ...).
  - A companion entity in ``public.entities`` (roles=['steam_account']) that
    anchors the API key in ``public.entity_info`` (type='steam_api_key', secured=True).

Public API
----------
- :func:`resolve_steam_account` — look up by SteamID (int), UUID, or get primary
- :func:`create_steam_account` — register a new Steam account
- :func:`list_steam_accounts` — list all connected accounts
- :func:`get_steam_account` — look up by UUID or get primary (no SteamID path)
- :func:`set_primary_account` — atomic swap of the primary flag
- :func:`disconnect_account` — soft-revoke or hard-delete an account

Design notes
------------
- ``public.steam_accounts.entity_id`` references a *companion* entity, not
  the owner entity.  The companion entity is created automatically by
  :func:`create_steam_account`.
- The partial unique index ``ix_steam_accounts_primary_singleton`` enforces
  at most one primary account at the DB level.  :func:`set_primary_account`
  uses a single transaction to clear the old primary before setting the new one.
- :func:`resolve_steam_account` is the primary entry point for connectors and
  modules: it resolves by SteamID (BIGINT), by UUID, or returns the primary
  account.  Missing credentials raise :exc:`MissingSteamCredentialsError`.
- All SQL uses fully qualified ``public.*`` schemas.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SteamAccountNotFoundError(Exception):
    """Raised when a requested Steam account does not exist."""


class SteamAccountAlreadyExistsError(Exception):
    """Raised when attempting to create an account with an already-registered SteamID."""


class MissingSteamCredentialsError(Exception):
    """Raised when no Steam account is available (e.g. no primary set)."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SteamAccount:
    """Represents a connected Steam account row.

    Attributes
    ----------
    id:
        UUID primary key of the steam_accounts row.
    entity_id:
        UUID of the companion entity in public.entities.
    steam_id:
        Steam 64-bit account ID.
    display_name:
        Steam display name (persona name).
    profile_url:
        URL to the Steam profile page.
    avatar_url:
        URL to the Steam avatar image.
    is_primary:
        Whether this is the active primary account.
    status:
        One of 'active', 'suspended', 'revoked'.
    connected_at:
        Timestamp when the account was first connected.
    last_poll_at:
        Timestamp of the last successful poll (may be None).
    metadata:
        Per-account configuration overrides (poll intervals, tracked games, etc.).
    """

    id: uuid.UUID
    entity_id: uuid.UUID
    steam_id: int
    display_name: str | None
    profile_url: str | None
    avatar_url: str | None
    is_primary: bool
    status: str
    connected_at: datetime
    last_poll_at: datetime | None
    metadata: dict[str, Any]

    @classmethod
    def _from_row(cls, row: Any) -> SteamAccount:
        return cls(
            id=row["id"],
            entity_id=row["entity_id"],
            steam_id=row["steam_id"],
            display_name=row["display_name"],
            profile_url=row["profile_url"],
            avatar_url=row["avatar_url"],
            is_primary=row["is_primary"],
            status=row["status"],
            connected_at=row["connected_at"],
            last_poll_at=row["last_poll_at"],
            metadata=dict(row["metadata"]) if row["metadata"] else {},
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _has_primary_account(conn: Any) -> bool:
    """Return True if any account currently has is_primary=true."""
    row = await conn.fetchrow("SELECT 1 FROM public.steam_accounts WHERE is_primary = true LIMIT 1")
    return row is not None


async def _create_companion_entity(conn: Any, steam_id: int) -> uuid.UUID:
    """Create a companion entity for a Steam account.

    Returns the UUID of the created (or pre-existing) companion entity.
    The entity is identified by canonical_name = 'steam-account:<steam_id>'.
    """
    canonical_name = f"steam-account:{steam_id}"
    row = await conn.fetchrow(
        """
        INSERT INTO public.entities (tenant_id, canonical_name, entity_type, roles)
        VALUES ('shared', $1, 'other', ARRAY['steam_account'])
        ON CONFLICT (tenant_id, canonical_name, entity_type)
            WHERE (metadata->>'merged_into') IS NULL
              AND (metadata->>'deleted_at') IS NULL
            DO UPDATE SET roles = ARRAY['steam_account']
        RETURNING id
        """,
        canonical_name,
    )
    return row["id"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve_steam_account(
    pool: asyncpg.Pool,
    steam_id: int | None = None,
    account: str | uuid.UUID | None = None,
) -> SteamAccount:
    """Resolve a Steam account by SteamID, UUID, or return the primary.

    This is the primary entry point for connectors and modules.

    Parameters
    ----------
    pool:
        asyncpg pool connected to the shared database.
    steam_id:
        Steam 64-bit integer ID.  If provided, looks up by steam_id.
    account:
        UUID (str or object) of the steam_accounts row.  Used when steam_id
        is not provided and a specific account is requested.  If both
        steam_id and account are None, returns the primary account.

    Returns
    -------
    SteamAccount
        The matching account.

    Raises
    ------
    MissingSteamCredentialsError
        When both steam_id and account are None and no primary account exists.
    SteamAccountNotFoundError
        When the specified steam_id or UUID is not found.
    """
    async with pool.acquire() as conn:
        # --- Lookup by SteamID (BIGINT) ---
        if steam_id is not None:
            row = await conn.fetchrow(
                """
                SELECT id, entity_id, steam_id, display_name, profile_url, avatar_url,
                       is_primary, status, connected_at, last_poll_at, metadata
                FROM public.steam_accounts
                WHERE steam_id = $1
                LIMIT 1
                """,
                steam_id,
            )
            if row is None:
                raise SteamAccountNotFoundError(
                    f"No Steam account found with steam_id={steam_id}. "
                    "Connect a Steam account via the dashboard first."
                )
            return SteamAccount._from_row(row)

        # --- Lookup by UUID ---
        if account is not None:
            account_id: uuid.UUID
            if isinstance(account, uuid.UUID):
                account_id = account
            else:
                try:
                    account_id = uuid.UUID(str(account))
                except ValueError:
                    raise SteamAccountNotFoundError(
                        f"Invalid account identifier {account!r}: expected a UUID."
                    )
            row = await conn.fetchrow(
                """
                SELECT id, entity_id, steam_id, display_name, profile_url, avatar_url,
                       is_primary, status, connected_at, last_poll_at, metadata
                FROM public.steam_accounts
                WHERE id = $1
                LIMIT 1
                """,
                account_id,
            )
            if row is None:
                raise SteamAccountNotFoundError(f"No Steam account found with id={account_id}.")
            return SteamAccount._from_row(row)

        # --- Default: primary account ---
        row = await conn.fetchrow(
            """
            SELECT id, entity_id, steam_id, display_name, profile_url, avatar_url,
                   is_primary, status, connected_at, last_poll_at, metadata
            FROM public.steam_accounts
            WHERE is_primary = true
            LIMIT 1
            """
        )
        if row is None:
            raise MissingSteamCredentialsError(
                "No primary Steam account is configured. "
                "Connect a Steam account via the dashboard and set it as primary."
            )
        return SteamAccount._from_row(row)


async def create_steam_account(
    pool: asyncpg.Pool,
    *,
    steam_id: int,
    display_name: str | None = None,
    profile_url: str | None = None,
    avatar_url: str | None = None,
    api_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> SteamAccount:
    """Register a new Steam account.

    Creates a companion entity in public.entities and inserts a row in
    public.steam_accounts.  If no other accounts exist, the new account
    is automatically set as primary.

    If api_key is provided, it is stored in public.entity_info on the
    companion entity (type='steam_api_key', secured=true).

    Parameters
    ----------
    pool:
        asyncpg pool connected to the shared database.
    steam_id:
        Steam 64-bit account ID.
    display_name:
        Steam display name (persona name).
    profile_url:
        URL to the Steam profile page.
    avatar_url:
        URL to the Steam avatar image.
    api_key:
        Steam Web API key to persist on the companion entity.
    metadata:
        Per-account configuration overrides (optional, defaults to {}).

    Returns
    -------
    SteamAccount
        The newly created account record.

    Raises
    ------
    SteamAccountAlreadyExistsError
        If an account with the given steam_id already exists.
    """
    account_metadata = metadata or {}

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Duplicate steam_id check.
            existing = await conn.fetchrow(
                "SELECT id FROM public.steam_accounts WHERE steam_id = $1",
                steam_id,
            )
            if existing is not None:
                raise SteamAccountAlreadyExistsError(
                    f"A Steam account with steam_id={steam_id} is already connected. "
                    "Use disconnect_account + create or reconnect the existing account."
                )

            # Determine primary flag: first account wins.
            is_primary = not await _has_primary_account(conn)

            # Create companion entity.
            entity_id = await _create_companion_entity(conn, steam_id)

            # Insert steam_accounts row.
            row = await conn.fetchrow(
                """
                INSERT INTO public.steam_accounts (
                    entity_id, steam_id, display_name, profile_url, avatar_url,
                    is_primary, status, metadata
                )
                VALUES ($1, $2, $3, $4, $5, $6, 'active', $7::jsonb)
                RETURNING
                    id, entity_id, steam_id, display_name, profile_url, avatar_url,
                    is_primary, status, connected_at, last_poll_at, metadata
                """,
                entity_id,
                steam_id,
                display_name,
                profile_url,
                avatar_url,
                is_primary,
                account_metadata,
            )

            # Persist API key if provided.
            if api_key:
                await conn.execute(
                    """
                    INSERT INTO public.entity_info (entity_id, type, value, secured, is_primary)
                    VALUES ($1, 'steam_api_key', $2, true, true)
                    ON CONFLICT (entity_id, type) DO UPDATE SET
                        value = EXCLUDED.value,
                        secured = EXCLUDED.secured
                    """,
                    entity_id,
                    api_key,
                )

            account = SteamAccount._from_row(row)

    logger.info(
        "Steam account created: id=%s steam_id=%s is_primary=%s",
        account.id,
        steam_id,
        is_primary,
    )
    return account


async def list_steam_accounts(pool: asyncpg.Pool) -> list[SteamAccount]:
    """List all connected Steam accounts.

    Returns accounts ordered by is_primary DESC, connected_at ASC.

    Parameters
    ----------
    pool:
        asyncpg pool connected to the shared database.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, entity_id, steam_id, display_name, profile_url, avatar_url,
                   is_primary, status, connected_at, last_poll_at, metadata
            FROM public.steam_accounts
            ORDER BY is_primary DESC, connected_at ASC
            """
        )
    return [SteamAccount._from_row(row) for row in rows]


async def get_steam_account(
    pool: asyncpg.Pool,
    account: str | uuid.UUID | None = None,
) -> SteamAccount:
    """Look up a Steam account by UUID or return the primary.

    For SteamID-based lookup use :func:`resolve_steam_account` instead.

    Parameters
    ----------
    pool:
        asyncpg pool connected to the shared database.
    account:
        - ``None`` → return the primary account.
        - ``str`` that is a valid UUID → look up by id.
        - ``uuid.UUID`` → look up by id.

    Returns
    -------
    SteamAccount
        The matching account.

    Raises
    ------
    MissingSteamCredentialsError
        When account is None and no primary account exists.
    SteamAccountNotFoundError
        When the specified UUID is not found.
    """
    return await resolve_steam_account(pool, steam_id=None, account=account)


async def set_primary_account(
    pool: asyncpg.Pool,
    account_id: uuid.UUID,
) -> SteamAccount:
    """Atomically set a Steam account as the primary.

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
    SteamAccount
        The updated account record.

    Raises
    ------
    SteamAccountNotFoundError
        If the target account does not exist.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Verify target exists.
            target = await conn.fetchrow(
                "SELECT id FROM public.steam_accounts WHERE id = $1",
                account_id,
            )
            if target is None:
                raise SteamAccountNotFoundError(f"No Steam account found with id={account_id}.")

            # Clear existing primary (any account that is currently primary).
            await conn.execute(
                "UPDATE public.steam_accounts SET is_primary = false WHERE is_primary = true"
            )

            # Set target as primary.
            row = await conn.fetchrow(
                """
                UPDATE public.steam_accounts
                SET is_primary = true
                WHERE id = $1
                RETURNING
                    id, entity_id, steam_id, display_name, profile_url, avatar_url,
                    is_primary, status, connected_at, last_poll_at, metadata
                """,
                account_id,
            )

    account = SteamAccount._from_row(row)
    logger.info("Primary Steam account set: id=%s steam_id=%s", account.id, account.steam_id)
    return account


async def disconnect_account(
    pool: asyncpg.Pool,
    account_id: uuid.UUID,
    *,
    hard_delete: bool = False,
) -> None:
    """Disconnect a Steam account.

    Soft disconnect (default): sets status to 'revoked'.  The connector stops
    polling this account on the next discovery cycle.  Credentials are retained.
    No automatic primary promotion occurs — the user must manually reassign.

    Hard delete: removes the steam_accounts row.  CASCADE deletes the companion
    entity and its entity_info rows (including the API key).

    Parameters
    ----------
    pool:
        asyncpg pool connected to the shared database.
    account_id:
        UUID of the account to disconnect.
    hard_delete:
        When True, the steam_accounts row and companion entity are deleted
        (CASCADE removes entity_info).  When False (default), only the
        status is updated to 'revoked'.

    Raises
    ------
    SteamAccountNotFoundError
        If the account does not exist.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            account_row = await conn.fetchrow(
                """
                SELECT id, entity_id, is_primary, status
                FROM public.steam_accounts
                WHERE id = $1
                """,
                account_id,
            )
            if account_row is None:
                raise SteamAccountNotFoundError(f"No Steam account found with id={account_id}.")

            entity_id: uuid.UUID = account_row["entity_id"]

            if hard_delete:
                # Hard delete: remove the companion entity (CASCADE handles the rest).
                await conn.execute(
                    "DELETE FROM public.entities WHERE id = $1",
                    entity_id,
                )
                logger.info("Steam account hard-deleted: id=%s", account_id)
            else:
                # Soft disconnect: mark status revoked, retain credentials.
                await conn.execute(
                    "UPDATE public.steam_accounts SET status = 'revoked' WHERE id = $1",
                    account_id,
                )
                logger.info("Steam account disconnected (revoked): id=%s", account_id)
