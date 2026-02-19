"""Generic credential store service backed by the butler_secrets table.

Provides a DB-first resolution layer for arbitrary named secrets, replacing
direct ``os.environ.get()`` calls scattered across modules and connectors.

Resolution order (``resolve()``):
1. Database (``butler_secrets`` table, written by ``store()``).
2. Environment variable (when *env_fallback* is True, the default).

Usage — storing a secret::

    await store.store("telegram_bot_token", "1234:ABCD...", category="telegram")

Usage — resolving a secret (DB-first, then env)::

    token = await store.resolve("TELEGRAM_BOT_TOKEN")
    if token is None:
        raise RuntimeError("Telegram bot token is not configured")

Usage — listing metadata for the dashboard::

    secrets = await store.list_secrets(category="telegram")
    for meta in secrets:
        print(meta.key, meta.is_set, meta.source)

Note: raw secret values are NEVER exposed by ``list_secrets()`` or ``__repr__``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_TABLE = "butler_secrets"


# ---------------------------------------------------------------------------
# SecretMetadata dataclass
# ---------------------------------------------------------------------------


@dataclass
class SecretMetadata:
    """Metadata about a stored secret — never includes the raw value.

    Attributes
    ----------
    key:
        The unique name for this secret (e.g. ``"telegram_bot_token"``).
    category:
        Grouping label used by the dashboard (e.g. ``"telegram"``).
    description:
        Optional human-readable description.
    is_sensitive:
        When True the raw value must be masked in UI and log output.
    is_set:
        Whether the secret currently has a non-empty value.
    created_at:
        When the secret was first stored.
    updated_at:
        When the secret was last updated.
    expires_at:
        Optional expiry; ``None`` means the secret never expires.
    source:
        Where the value was resolved from: ``"database"`` or ``"environment"``.
    """

    key: str
    category: str
    description: str | None
    is_sensitive: bool
    is_set: bool
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    source: str  # 'database' or 'environment'

    def __repr__(self) -> str:
        return (
            f"SecretMetadata("
            f"key={self.key!r}, "
            f"category={self.category!r}, "
            f"is_set={self.is_set!r}, "
            f"source={self.source!r})"
        )


# ---------------------------------------------------------------------------
# CredentialStore
# ---------------------------------------------------------------------------


class CredentialStore:
    """Async credential store backed by the ``butler_secrets`` DB table.

    All operations are async and require an asyncpg pool (or connection).
    Instantiate once after pool creation and pass to modules via
    ``on_startup()``.

    Parameters
    ----------
    pool:
        An asyncpg connection pool.  Individual operations acquire a
        connection from the pool for the duration of the call, so multiple
        concurrent tool invocations are safe.
    """

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def store(
        self,
        key: str,
        value: str,
        *,
        category: str = "general",
        description: str | None = None,
        is_sensitive: bool = True,
        expires_at: datetime | None = None,
    ) -> None:
        """Persist a secret to the ``butler_secrets`` table.

        Uses INSERT … ON CONFLICT DO UPDATE so this is idempotent —
        calling it again with a new value replaces the previous one.

        Parameters
        ----------
        key:
            Unique name for the secret (case-sensitive).
        value:
            The secret value to store.
        category:
            Grouping label for dashboard display.  Defaults to
            ``"general"``.
        description:
            Optional human-readable description shown in the dashboard.
        is_sensitive:
            When ``True`` (the default) the value is masked in dashboard
            and log output.
        expires_at:
            Optional expiry time.  ``None`` means the secret never
            expires.

        Raises
        ------
        ValueError
            If *key* or *value* is an empty string.
        """
        key = key.strip()
        if not key:
            raise ValueError("key must be a non-empty string")
        if value == "":
            raise ValueError("value must be a non-empty string")

        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {_TABLE}
                    (secret_key, secret_value, category, description,
                     is_sensitive, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (secret_key) DO UPDATE SET
                    secret_value = EXCLUDED.secret_value,
                    category     = EXCLUDED.category,
                    description  = EXCLUDED.description,
                    is_sensitive = EXCLUDED.is_sensitive,
                    expires_at   = EXCLUDED.expires_at,
                    updated_at   = now()
                """,
                key,
                value,
                category,
                description,
                is_sensitive,
                expires_at,
            )

        # Log at info level; NEVER include the value.
        logger.info(
            "Secret stored: key=%r category=%r is_sensitive=%r",
            key,
            category,
            is_sensitive,
        )

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def load(self, key: str) -> str | None:
        """Load a secret value directly from the database.

        Returns ``None`` if the key does not exist in the database.
        Does NOT fall back to environment variables — use ``resolve()``
        for that behaviour.

        Parameters
        ----------
        key:
            The secret key to look up (case-sensitive).

        Returns
        -------
        str | None
            The stored value, or ``None`` if not found.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT secret_value FROM {_TABLE} WHERE secret_key = $1",
                key,
            )
        if row is None:
            return None
        return row["secret_value"]

    async def resolve(self, key: str, *, env_fallback: bool = True) -> str | None:
        """Resolve a secret — DB first, then environment variable.

        Resolution order:
        1. Database (``butler_secrets`` table via ``load()``).
        2. ``os.environ[key]`` if *env_fallback* is ``True``.

        Parameters
        ----------
        key:
            The secret key.  When falling back to env vars, the same
            key is looked up in ``os.environ`` (exact match, case-
            sensitive).
        env_fallback:
            Whether to fall back to environment variables if the DB
            has no value.  Defaults to ``True``.

        Returns
        -------
        str | None
            The resolved value, or ``None`` if not found in any source.
        """
        # 1. Try database
        value = await self.load(key)
        if value is not None:
            logger.debug("Resolved secret %r from database", key)
            return value

        # 2. Try environment variable
        if env_fallback:
            env_value = os.environ.get(key)
            if env_value:
                logger.debug("Resolved secret %r from environment variable", key)
                return env_value

        return None

    async def has(self, key: str) -> bool:
        """Return ``True`` if the key exists in the database.

        This does NOT check environment variables.

        Parameters
        ----------
        key:
            The secret key to check.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT 1 FROM {_TABLE} WHERE secret_key = $1",
                key,
            )
        return row is not None

    # ------------------------------------------------------------------
    # Delete operation
    # ------------------------------------------------------------------

    async def delete(self, key: str) -> bool:
        """Delete a secret from the database.

        Parameters
        ----------
        key:
            The secret key to delete.

        Returns
        -------
        bool
            ``True`` if a row was deleted, ``False`` if the key did not
            exist.
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {_TABLE} WHERE secret_key = $1",
                key,
            )
        # asyncpg returns a string like "DELETE 1" or "DELETE 0"
        deleted = result.split()[-1] != "0" if result else False
        if deleted:
            logger.info("Secret deleted: key=%r", key)
        else:
            logger.debug("Secret not found for deletion: key=%r", key)
        return deleted

    # ------------------------------------------------------------------
    # List operation (metadata only — never raw values)
    # ------------------------------------------------------------------

    async def list_secrets(self, *, category: str | None = None) -> list[SecretMetadata]:
        """List stored secrets as metadata records (no raw values).

        Parameters
        ----------
        category:
            When given, only secrets in this category are returned.
            When ``None`` (the default) all secrets are returned.

        Returns
        -------
        list[SecretMetadata]
            Metadata for each stored secret, ordered by
            ``(category, secret_key)``.  Raw values are never included.
        """
        if category is not None:
            query = f"""
                SELECT secret_key, category, description, is_sensitive,
                       created_at, updated_at, expires_at
                FROM {_TABLE}
                WHERE category = $1
                ORDER BY category, secret_key
            """
            params: tuple[Any, ...] = (category,)
        else:
            query = f"""
                SELECT secret_key, category, description, is_sensitive,
                       created_at, updated_at, expires_at
                FROM {_TABLE}
                ORDER BY category, secret_key
            """
            params = ()

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [
            SecretMetadata(
                key=row["secret_key"],
                category=row["category"],
                description=row["description"],
                is_sensitive=row["is_sensitive"],
                is_set=True,  # Row exists ⟹ a non-NULL value is stored
                created_at=_ensure_utc(row["created_at"]),
                updated_at=_ensure_utc(row["updated_at"]),
                expires_at=_ensure_utc(row["expires_at"]) if row["expires_at"] else None,
                source="database",
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Repr — never expose pool details or secrets
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"CredentialStore(pool={self.pool!r})"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_utc(dt: datetime) -> datetime:
    """Attach UTC timezone to a naive datetime returned by asyncpg."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
