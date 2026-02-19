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
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_TABLE = "butler_secrets"
_DEFAULT_SHARED_DB_NAME = "butler_shared"
_DEFAULT_LEGACY_DB_NAME = "butler_general"
_ENV_SHARED_DB_NAME = "BUTLER_SHARED_DB_NAME"
_ENV_LEGACY_DB_NAME = "BUTLER_LEGACY_SHARED_DB_NAME"

_SECRETS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    secret_key   TEXT PRIMARY KEY,
    secret_value TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'general',
    description  TEXT,
    is_sensitive BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ
)
"""

_SECRETS_CATEGORY_INDEX_DDL = f"""
CREATE INDEX IF NOT EXISTS ix_butler_secrets_category
ON {_TABLE} (category)
"""


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

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        fallback_pools: Iterable[asyncpg.Pool] | None = None,
    ) -> None:
        self.pool = pool
        self._fallback_pools: tuple[asyncpg.Pool, ...] = tuple(
            p for p in (fallback_pools or ()) if p is not pool
        )

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

        Returns ``None`` if the key does not exist in the configured
        credential stores.

        Parameters
        ----------
        key:
            The secret key to look up (case-sensitive).

        Returns
        -------
        str | None
            The stored value, or ``None`` if not found.  Lookup order is:
            local store first, then configured fallback stores.
        """
        for source_name, pool in self._iter_lookup_pools():
            row = await _safe_fetch_secret_row(pool, key, source_name=source_name)
            if row is None:
                continue
            value = row["secret_value"]
            logger.debug("Loaded secret %r from %s credential store", key, source_name)
            return value
        return None

    async def resolve(self, key: str, *, env_fallback: bool = True) -> str | None:
        """Resolve a secret — DB first, then environment variable.

        Resolution order:
        1. Local database (``butler_secrets`` table via ``load()``).
        2. Fallback database(s), in configured order.
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
        # 1. Try local/fallback databases
        value = await self.load(key)
        if value is not None:
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
        base_query = f"""
            SELECT secret_key, category, description, is_sensitive,
                   created_at, updated_at, expires_at
            FROM {_TABLE}
        """
        if category is not None:
            query = f"{base_query} WHERE category = $1 ORDER BY category, secret_key"
            params: tuple[Any, ...] = (category,)
        else:
            query = f"{base_query} ORDER BY category, secret_key"
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
        return f"CredentialStore(pool={self.pool!r}, fallback_pools={len(self._fallback_pools)})"

    def _iter_lookup_pools(self) -> list[tuple[str, asyncpg.Pool]]:
        pools: list[tuple[str, asyncpg.Pool]] = [("local", self.pool)]
        for idx, pool in enumerate(self._fallback_pools):
            label = "shared" if idx == 0 else f"compat_{idx}"
            pools.append((label, pool))
        return pools


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_utc(dt: datetime) -> datetime:
    """Attach UTC timezone to a naive datetime returned by asyncpg."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def shared_db_name_from_env() -> str:
    """Resolve the shared credential database name from env/defaults."""
    name = os.environ.get(_ENV_SHARED_DB_NAME, _DEFAULT_SHARED_DB_NAME).strip()
    return name or _DEFAULT_SHARED_DB_NAME


def legacy_shared_db_name_from_env() -> str:
    """Resolve the legacy centralized credential DB name for compatibility."""
    name = os.environ.get(_ENV_LEGACY_DB_NAME, _DEFAULT_LEGACY_DB_NAME).strip()
    return name or _DEFAULT_LEGACY_DB_NAME


async def ensure_secrets_schema(pool: asyncpg.Pool) -> None:
    """Ensure ``butler_secrets`` exists on the target database."""
    async with _acquire_conn(pool) as conn:
        await conn.execute(_SECRETS_TABLE_DDL)
        await conn.execute(_SECRETS_CATEGORY_INDEX_DDL)


async def backfill_shared_secrets(
    shared_pool: asyncpg.Pool,
    legacy_pool: asyncpg.Pool | None,
) -> int:
    """Copy missing rows from legacy centralized secrets into shared DB.

    Existing keys in the shared store are preserved; only missing keys are
    inserted.  Returns the number of inserted rows.
    """
    if legacy_pool is None or legacy_pool is shared_pool:
        return 0

    async with _acquire_conn(legacy_pool) as legacy_conn:
        try:
            rows = await legacy_conn.fetch(
                f"""
                SELECT secret_key, secret_value, category, description,
                       is_sensitive, created_at, updated_at, expires_at
                FROM {_TABLE}
                ORDER BY secret_key
                """
            )
        except Exception as exc:
            if _is_missing_table_error(exc):
                logger.debug(
                    "Legacy secrets table missing during shared backfill (table=%s); skipping",
                    _TABLE,
                )
                return 0
            raise

    if not rows:
        return 0

    inserted = 0
    async with _acquire_conn(shared_pool) as shared_conn:
        await shared_conn.execute(_SECRETS_TABLE_DDL)
        await shared_conn.execute(_SECRETS_CATEGORY_INDEX_DDL)
        for row in rows:
            result = await shared_conn.execute(
                f"""
                INSERT INTO {_TABLE}
                    (secret_key, secret_value, category, description,
                     is_sensitive, created_at, updated_at, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (secret_key) DO NOTHING
                """,
                row["secret_key"],
                row["secret_value"],
                row["category"],
                row["description"],
                row["is_sensitive"],
                row["created_at"],
                row["updated_at"],
                row["expires_at"],
            )
            if result and result.split()[-1] != "0":
                inserted += 1

    if inserted:
        logger.info("Backfilled %d secret(s) from legacy store into shared store", inserted)
    return inserted


async def _safe_fetch_secret_row(
    pool: asyncpg.Pool,
    key: str,
    *,
    source_name: str,
) -> Any:
    """Return ``secret_value`` row for *key* or ``None`` if not found."""
    try:
        async with _acquire_conn(pool) as conn:
            return await conn.fetchrow(
                f"SELECT secret_value FROM {_TABLE} WHERE secret_key = $1",
                key,
            )
    except Exception as exc:
        if _is_missing_table_error(exc):
            logger.debug(
                "Skipping %s credential store lookup for key %r; table %s is missing",
                source_name,
                key,
                _TABLE,
            )
            return None
        raise


def _is_missing_table_error(exc: Exception) -> bool:
    """Return whether an exception indicates missing ``butler_secrets`` table."""
    if exc.__class__.__name__ == "UndefinedTableError":
        return True
    msg = str(exc).lower()
    return "relation" in msg and _TABLE in msg and "does not exist" in msg


@asynccontextmanager
async def _acquire_conn(pool: asyncpg.Pool) -> AsyncIterator[Any]:
    """Acquire a DB connection, including AsyncMock-friendly test doubles."""
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
    # Last-resort fallback for non-context-manager connection stubs.
    yield acquired
