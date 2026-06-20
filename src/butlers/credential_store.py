"""Generic credential store service backed by the butler_secrets table.

Implements the **Tier 1 (system)** layer of the three-tier credential model
defined in ``about/heart-and-soul/security.md`` (see "Credential Management").
Provides a DB-first resolution layer for ecosystem-wide secrets, replacing
direct ``os.environ.get()`` calls in modules and connectors.

Tier summary (full definitions in security.md):
- **Tier 0 (bootstrap):** Infrastructure env vars (``POSTGRES_*``, ``SWITCHBOARD_MCP_URL``,
  ``OTEL_*``). Read directly via ``os.environ``; required before the DB is available.
- **Tier 1 (system):** Ecosystem-wide secrets in ``butler_secrets`` (this module).
  Canonical access: ``await store.resolve(key)``.
- **Tier 2 (user/identity):** Owner-bound credentials in ``public.entity_info``.
  Canonical access: ``await resolve_owner_entity_info(pool, info_type)``.
  Never use ``CredentialStore`` for Tier 2 credentials.

Resolution order for ``resolve()`` — **see ``resolve()`` for the canonical
description of credential-fallback semantics**:

1. Local database (``butler_secrets`` table via ``load()``).
2. Fallback database pool(s), in configured order.
3. ``os.environ[key]`` — only when *env_fallback* is explicitly set to ``True``
   (disabled by default).  Reserve for Tier 0 bootstrap credentials only.

API keys, OAuth tokens, and integration secrets MUST NOT pass ``env_fallback=True``.

Usage — storing a secret::

    await store.store("telegram_bot_token", "1234:ABCD...", category="telegram")

Usage — resolving a secret (DB-first; env fallback disabled by default)::

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

# ---------------------------------------------------------------------------
# entity_info write-time guard (RFC 0004 Amendment 3, bu-oluyt.1)
# ---------------------------------------------------------------------------
#
# Seam law: public.entity_info holds ONLY secured=True credentials.
# Non-secret facts / identifiers / routing handles belong in
# relationship.entity_facts via relationship_assert_fact() instead.
#
# Approved non-secured types are technical configuration entries that have no
# predicate home in entity_facts (they are not contact-channel triples about a
# person/entity).  All channel handles (telegram, telegram_chat_id, email,
# phone, etc.) are non-secret and must NOT be written here — they belong in
# entity_facts as has-handle / has-email / has-phone triples.
_ENTITY_INFO_NON_SECRET_ALLOWED_TYPES: frozenset[str] = frozenset(
    {
        "telegram_api_id",  # technical API credential component, not a channel handle
        "home_assistant_url",  # service URL config entry; no predicate home in entity_facts
    }
)


def assert_entity_info_secured(info_type: str, secured: bool) -> None:
    """Raise ValueError when a non-secret type is about to be written to entity_info.

    This is the write-time enforcement of the seam law declared in RFC 0004
    Amendment 3: ``public.entity_info`` is a secrets store.  Non-secret
    identifiers and channel handles must go to ``relationship.entity_facts``
    via ``relationship_assert_fact()`` instead.  The only permitted
    ``secured=False`` types are technical configuration entries that have no
    predicate home in entity_facts (see
    ``_ENTITY_INFO_NON_SECRET_ALLOWED_TYPES``).

    Parameters
    ----------
    info_type:
        The ``type`` field about to be written.
    secured:
        The ``secured`` flag about to be written.

    Raises
    ------
    ValueError
        When ``secured`` is ``False`` and ``info_type`` is not in the approved
        whitelist of non-secret technical identifiers
        (``_ENTITY_INFO_NON_SECRET_ALLOWED_TYPES``).
    """
    if not secured and info_type not in _ENTITY_INFO_NON_SECRET_ALLOWED_TYPES:
        raise ValueError(
            f"entity_info write rejected: type={info_type!r} with secured=False is not allowed. "
            f"Non-secret identifiers belong in relationship.entity_facts (has-handle / has-email "
            f"/ has-phone triples), not in the entity_info credential store. "
            f"Approved non-secret types: {sorted(_ENTITY_INFO_NON_SECRET_ALLOWED_TYPES)!r}. "
            f"See RFC 0004 Amendment 3."
        )


_DEFAULT_SHARED_DB_NAME = "butlers"
_ENV_SHARED_DB_NAME = "BUTLER_SHARED_DB_NAME"

_SECRETS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    secret_key   TEXT PRIMARY KEY,
    secret_value TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'general',
    description  TEXT,
    is_sensitive BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ,
    last_verified     TIMESTAMPTZ,
    last_test_ok      BOOLEAN,
    last_test_code    INTEGER,
    last_test_message TEXT
)
"""

# Test-state columns (core_106 / core_117). Applied via ALTER on every
# ensure_secrets_schema call so tables created before these columns existed
# converge without depending on the alembic chain having run.
_SECRETS_TEST_STATE_DDL = f"""
ALTER TABLE {_TABLE}
    ADD COLUMN IF NOT EXISTS last_verified TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_test_ok BOOLEAN,
    ADD COLUMN IF NOT EXISTS last_test_code INTEGER,
    ADD COLUMN IF NOT EXISTS last_test_message TEXT
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
    last_verified:
        Timestamp of most recent successful probe.  ``None`` = never probed.
    last_test_ok:
        Outcome of most recent probe.  ``None`` = never probed.
    last_test_code:
        HTTP / provider response code from most recent probe.  ``None`` = never probed.
    last_test_message:
        Verbatim error tail from most recent probe (truncated to 512 chars by the
        application).  ``None`` = never probed or no error message.
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
    last_verified: datetime | None = None
    last_test_ok: bool | None = None
    last_test_code: int | None = None
    last_test_message: str | None = None

    def __repr__(self) -> str:
        return (
            f"SecretMetadata("
            f"key={self.key!r}, "
            f"category={self.category!r}, "
            f"is_set={self.is_set!r}, "
            f"source={self.source!r})"
        )


# ---------------------------------------------------------------------------
# EntityInfoRow dataclass
# ---------------------------------------------------------------------------


@dataclass
class EntityInfoRow:
    """A row from ``public.entity_info`` including test-state columns.

    Provides a typed Python representation of the full entity_info row
    so application code can read and write test-state columns without raw SQL
    casts.

    Attributes
    ----------
    id:
        Primary key (UUID).
    entity_id:
        FK to ``public.entities.id``.
    type:
        Credential / identifier type (e.g. ``"google_oauth_refresh"``,
        ``"telegram"``).
    value:
        Stored credential value.  ``None`` when the row has ``secured=True``
        and the caller does not hold value-read permission.
    label:
        Optional human-readable label for multi-account display.
    is_primary:
        Whether this is the primary entry for ``(entity_id, type)``.
        The DB column has no ``NOT NULL`` constraint so this may be ``None``
        for legacy rows inserted before a default was enforced.
    secured:
        When ``True``, the value must be masked in UI and log output.
    created_at:
        When the row was first inserted.
    last_verified:
        Timestamp of most recent successful probe.  ``None`` = never probed.
    last_test_ok:
        Outcome of most recent probe.  ``None`` = never probed.
    last_test_code:
        HTTP / provider response code from most recent probe.  ``None`` = never probed.
    last_test_message:
        Verbatim error tail from most recent probe (truncated to 512 chars by the
        application).  ``None`` = never probed or no error message.
    """

    id: str
    entity_id: str
    type: str
    value: str | None
    label: str | None
    is_primary: bool | None
    secured: bool
    created_at: datetime
    last_verified: datetime | None = None
    last_test_ok: bool | None = None
    last_test_code: int | None = None
    last_test_message: str | None = None

    def __repr__(self) -> str:
        return (
            f"EntityInfoRow("
            f"id={self.id!r}, "
            f"entity_id={self.entity_id!r}, "
            f"type={self.type!r}, "
            f"secured={self.secured!r})"
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

    @property
    def shared_pool(self) -> asyncpg.Pool | None:
        """Return the first fallback (shared) pool, or ``None``."""
        return self._fallback_pools[0] if self._fallback_pools else None

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
        value = value.strip().strip('"').strip("'")
        if not key:
            raise ValueError("key must be a non-empty string")
        if not value:
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

    async def store_shared(
        self,
        key: str,
        value: str,
        *,
        category: str = "general",
        description: str | None = None,
        is_sensitive: bool = True,
    ) -> bool:
        """Persist a secret to the **shared** (fallback) credential store.

        Returns ``True`` if the write succeeded, ``False`` if no shared pool
        is available (falls back to local store in that case).
        """
        pool = self.shared_pool
        if pool is None:
            await self.store(
                key, value, category=category, description=description, is_sensitive=is_sensitive
            )
            return False

        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            raise ValueError("key must be a non-empty string")
        if not value:
            raise ValueError("value must be a non-empty string")

        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {_TABLE}
                    (secret_key, secret_value, category, description,
                     is_sensitive)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (secret_key) DO UPDATE SET
                    secret_value = EXCLUDED.secret_value,
                    category     = EXCLUDED.category,
                    description  = EXCLUDED.description,
                    is_sensitive = EXCLUDED.is_sensitive,
                    updated_at   = now()
                """,
                key,
                value,
                category,
                description,
                is_sensitive,
            )
        logger.info(
            "Secret stored (shared): key=%r category=%r is_sensitive=%r",
            key,
            category,
            is_sensitive,
        )
        return True

    async def record_test_result(
        self,
        key: str,
        ok: bool,
        message: str | None = None,
    ) -> None:
        """Record a credential probe/spawn result without touching the stored value.

        Updates ``last_test_ok``, ``last_verified = now()``, and
        ``last_test_message`` on the row identified by *key*.  No-op when
        *key* does not exist in the store (the UPDATE matches zero rows and
        no error is raised).

        Parameters
        ----------
        key:
            The secret key to update (e.g. ``"cli-auth/codex"``).
        ok:
            ``True`` → probe succeeded; ``False`` → probe failed.
        message:
            Optional error detail (truncated to 512 chars).  Pass ``None``
            to clear any previously stored message on a successful probe.
        """
        truncated = message[:512] if message else None
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE {_TABLE}
                SET last_test_ok      = $1,
                    last_verified     = now(),
                    last_test_message = $2
                WHERE secret_key = $3
                """,
                ok,
                truncated,
                key,
            )
        logger.debug(
            "Credential test result recorded: key=%r ok=%r",
            key,
            ok,
        )

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def load_shared(self, key: str) -> str | None:
        """Load a secret from only the shared (fallback) pool.

        Returns ``None`` if the shared pool is unavailable or the key does
        not exist there.
        """
        pool = self.shared_pool
        if pool is None:
            return None
        row = await _safe_fetch_secret_row(pool, key, source_name="shared")
        if row is None:
            return None
        return row["secret_value"]

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

    async def resolve(self, key: str, *, env_fallback: bool = False) -> str | None:
        """Resolve a Tier 1 (system) secret — DB only by default.

        **Canonical credential-fallback semantics** for the three-tier model
        (``about/heart-and-soul/security.md`` → "Credential Management"):

        - **Tier 0 (bootstrap):** ``POSTGRES_*``, ``SWITCHBOARD_MCP_URL``, ``OTEL_*`` —
          read directly via ``os.environ`` before the DB is available.
        - **Tier 1 (system):** Ecosystem-wide API keys, OAuth client creds, tokens —
          stored in ``butler_secrets``.  Canonical access: ``await store.resolve(key)``
          (this function).
        - **Tier 2 (user/identity):** Owner-bound credentials in ``public.entity_info`` —
          use ``resolve_owner_entity_info(pool, info_type)`` instead, never this function.

        Resolution order within this function:

        1. **Local database** — ``butler_secrets`` table (via ``load()``).
        2. **Fallback database pool(s)** — in configured order (e.g. shared pool).
        3. **Environment variable** — ``os.environ[key]`` only if *env_fallback* is
           explicitly ``True``.  This step is **disabled by default** (``False``).

        Environment fallback must only be enabled for Tier 0 bootstrap credentials
        (database connection strings, OTEL endpoint) that must be available before
        the DB is reachable.  API keys, OAuth tokens, and integration credentials
        are Tier 1 and MUST NOT use ``env_fallback=True``.

        Parameters
        ----------
        key:
            The secret key.  When env fallback is active, the same key is looked
            up in ``os.environ`` (exact match, case-sensitive).
        env_fallback:
            When ``True``, fall back to ``os.environ[key]`` if the DB has no
            value.  Defaults to ``False`` — callers must explicitly opt in.

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
                   created_at, updated_at, expires_at,
                   last_verified, last_test_ok, last_test_code, last_test_message
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
                last_verified=_ensure_utc(row["last_verified"]) if row["last_verified"] else None,
                last_test_ok=row["last_test_ok"],
                last_test_code=row["last_test_code"],
                last_test_message=row["last_test_message"],
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


async def ensure_secrets_schema(pool: asyncpg.Pool) -> None:
    """Ensure ``butler_secrets`` exists on the target database."""
    async with _acquire_conn(pool) as conn:
        await conn.execute(_SECRETS_TABLE_DDL)
        await conn.execute(_SECRETS_TEST_STATE_DDL)
        await conn.execute(_SECRETS_CATEGORY_INDEX_DDL)


async def resolve_owner_entity_info(pool: asyncpg.Pool, info_type: str) -> str | None:
    """Resolve a credential value from the owner entity's ``public.entity_info``.

    Queries ``public.entities`` for the owner entity (``'owner' = ANY(roles)``)
    and returns the ``value`` from the matching ``public.entity_info`` row for
    the given *info_type*.  Primary entries (``is_primary = true``) are preferred
    over non-primary entries.  Returns ``None`` when:

    - ``public.entities`` or ``public.entity_info`` do not exist.
    - No owner entity is found.
    - No ``entity_info`` row exists for the given type on the owner entity.

    This function is the DB-side counterpart to ``credential_store.resolve()``
    for identity-bound credentials that have been migrated to ``entity_info``
    (e.g. ``TELEGRAM_CHAT_ID`` → ``type='telegram'``).

    Parameters
    ----------
    pool:
        An asyncpg connection pool connected to the shared database.
    info_type:
        The ``type`` value to look up (e.g. ``'telegram'``, ``'email'``,
        ``'telegram_bot_token'``, etc.).

    Returns
    -------
    str | None
        The credential value, or ``None`` if not found.
    """
    try:
        async with _acquire_conn(pool) as conn:
            row = await conn.fetchrow(
                """
                SELECT ei.value
                FROM public.entity_info ei
                JOIN public.entities e ON e.id = ei.entity_id
                WHERE 'owner' = ANY(e.roles)
                  AND ei.type = $1
                ORDER BY ei.is_primary DESC NULLS LAST, ei.created_at ASC
                LIMIT 1
                """,
                info_type,
            )
            if row is None:
                return None
            value = row["value"]
            if not value:
                return None
            stripped = value.strip()
            if not stripped:
                return None
            logger.debug("Resolved owner entity_info type=%r from DB", info_type)
            return stripped
    except Exception as exc:  # noqa: BLE001
        if _is_missing_table_error(exc) or _is_missing_column_or_schema_error(exc):
            logger.debug(
                "resolve_owner_entity_info skipped for type=%r; table/column not available: %s",
                info_type,
                exc,
            )
            return None
        raise


async def resolve_owner_telegram_recipient(pool: asyncpg.Pool) -> str | None:
    """Resolve the owner's *deliverable* Telegram identifier.

    Prefers the numeric chat id (``entity_info`` type ``'telegram_chat_id'``),
    which is the only form the Telegram send path can deliver to. Falls back to
    the ``'telegram'`` handle (a ``@username``) only when no chat id is stored.

    Sending to a bare ``@username`` fails both at the Telegram API (private
    users are addressable only by numeric id) and at the approval gate's
    owner-primacy check (the canonical primary handle in
    ``relationship.entity_facts`` is the numeric chat id), so the chat id must
    win when both are present.

    Returns the resolved identifier, or ``None`` when neither is configured.
    """
    chat_id = await resolve_owner_entity_info(pool, "telegram_chat_id")
    if chat_id:
        return chat_id
    return await resolve_owner_entity_info(pool, "telegram")


async def upsert_owner_entity_info(
    pool: asyncpg.Pool,
    info_type: str,
    value: str,
    *,
    secured: bool = True,
) -> bool:
    """Upsert a ``public.entity_info`` row on the owner entity.

    Finds the owner entity (``'owner' = ANY(e.roles)``), then inserts or
    updates the ``entity_info`` row for ``(entity_id, type)`` using the
    UNIQUE constraint for conflict resolution.

    Parameters
    ----------
    pool:
        An asyncpg pool connected to the shared database.
    info_type:
        The ``type`` value (e.g. ``'google_oauth_refresh'``).
    value:
        The credential value to store.
    secured:
        Whether to mark the row as ``secured`` (default ``True``).

    Returns
    -------
    bool
        ``True`` if the row was upserted, ``False`` if the owner entity
        or required tables do not exist.

    Raises
    ------
    ValueError
        When ``secured`` is ``False`` and ``info_type`` is not in the approved
        whitelist of non-secret technical identifiers.  Non-secret channel
        handles must go to ``relationship.entity_facts`` instead.
    """
    assert_entity_info_secured(info_type, secured)
    try:
        async with _acquire_conn(pool) as conn:
            owner = await conn.fetchrow(
                """
                SELECT e.id
                FROM public.entities e
                WHERE 'owner' = ANY(e.roles)
                LIMIT 1
                """,
            )
            if owner is None:
                logger.debug("upsert_owner_entity_info: no owner entity found")
                return False
            entity_id = owner["id"]
            await conn.execute(
                """
                INSERT INTO public.entity_info (entity_id, type, value, secured, is_primary)
                VALUES ($1, $2, $3, $4, true)
                ON CONFLICT (entity_id, type) DO UPDATE SET
                    value = EXCLUDED.value,
                    secured = EXCLUDED.secured,
                    is_primary = EXCLUDED.is_primary
                """,
                entity_id,
                info_type,
                value,
                secured,
            )
            logger.info("Upserted owner entity_info type=%r (secured=%s)", info_type, secured)
            return True
    except Exception as exc:  # noqa: BLE001
        if _is_missing_table_error(exc) or _is_missing_column_or_schema_error(exc):
            logger.debug(
                "upsert_owner_entity_info skipped for type=%r; table/column not available: %s",
                info_type,
                exc,
            )
            return False
        raise


async def delete_owner_entity_info(
    pool: asyncpg.Pool,
    info_type: str,
) -> bool:
    """Delete a ``public.entity_info`` row from the owner entity.

    Parameters
    ----------
    pool:
        An asyncpg pool connected to the shared database.
    info_type:
        The ``type`` value to delete (e.g. ``'google_oauth_refresh'``).

    Returns
    -------
    bool
        ``True`` if a row was deleted, ``False`` otherwise.
    """
    try:
        async with _acquire_conn(pool) as conn:
            owner = await conn.fetchrow(
                """
                SELECT e.id
                FROM public.entities e
                WHERE 'owner' = ANY(e.roles)
                LIMIT 1
                """,
            )
            if owner is None:
                logger.debug("delete_owner_entity_info: no owner entity found")
                return False
            result = await conn.execute(
                "DELETE FROM public.entity_info WHERE entity_id = $1 AND type = $2",
                owner["id"],
                info_type,
            )
            deleted = result.split()[-1] != "0" if result else False
            if deleted:
                logger.info("Deleted owner entity_info type=%r", info_type)
            return deleted
    except Exception as exc:  # noqa: BLE001
        if _is_missing_table_error(exc) or _is_missing_column_or_schema_error(exc):
            logger.debug(
                "delete_owner_entity_info skipped for type=%r; table/column not available: %s",
                info_type,
                exc,
            )
            return False
        raise


def _is_missing_column_or_schema_error(exc: Exception) -> bool:
    """Return True when an exception indicates a missing column or schema.

    Uses asyncpg exception class names when available (preferred) and falls
    back to ``"does not exist"`` string matching.  Intentionally avoids bare
    ``"column"`` / ``"schema"`` substring matches to prevent false-positives on
    data-integrity errors (e.g. NOT NULL / FK violations) whose messages
    incidentally contain those words.
    """
    cls = exc.__class__.__name__
    if cls in ("UndefinedColumnError", "InvalidSchemaNameError", "UndefinedTableError"):
        return True
    msg = str(exc).lower()
    # "does not exist" is precise enough: covers both missing-table and
    # missing-column PostgreSQL error text without matching FK/NOT-NULL messages.
    return "does not exist" in msg


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
