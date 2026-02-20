"""Database provisioning and connection pool management for butlers."""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import asyncpg

logger = logging.getLogger(__name__)

_VALID_SSL_MODES = {"disable", "prefer", "allow", "require", "verify-ca", "verify-full"}
_SSL_UPGRADE_CONNECTION_LOST = "unexpected connection_lost() call"
_SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _normalize_ssl_mode(value: str | None) -> str | None:
    """Normalize an SSL mode value for asyncpg or return None if unset/invalid."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in _VALID_SSL_MODES:
        return normalized
    logger.warning("Ignoring invalid PostgreSQL sslmode value: %s", value)
    return None


def _db_params_from_database_url(database_url: str) -> dict[str, str | int | None]:
    """Parse connection params from a libpq-style DATABASE_URL."""
    parsed = urlparse(database_url)
    sslmode = _normalize_ssl_mode(parse_qs(parsed.query).get("sslmode", [None])[0])
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "butlers",
        "password": parsed.password or "butlers",
        "ssl": sslmode,
    }


def _normalize_schema_name(value: str | None) -> str | None:
    """Normalize and validate a schema name."""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if _SCHEMA_NAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"Invalid schema name: {value!r}. Expected a SQL identifier-style string.")
    return normalized


def schema_search_path(schema: str | None) -> str | None:
    """Build a deterministic search_path for schema-scoped runtime access."""
    normalized = _normalize_schema_name(schema)
    if normalized is None:
        return None
    search_path: list[str] = []
    for part in (normalized, "shared", "public"):
        if part not in search_path:
            search_path.append(part)
    return ",".join(search_path)


def should_retry_with_ssl_disable(exc: Exception, configured_ssl: str | None) -> bool:
    """Return True when asyncpg SSL STARTTLS fallback should retry with ssl=disable."""
    return (
        configured_ssl is None
        and isinstance(exc, ConnectionError)
        and _SSL_UPGRADE_CONNECTION_LOST in str(exc)
    )


def db_params_from_env() -> dict[str, str | int | None]:
    """Read DB connection params from environment variables."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return _db_params_from_database_url(database_url)
    return {
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "butlers"),
        "password": os.environ.get("POSTGRES_PASSWORD", "butlers"),
        "ssl": _normalize_ssl_mode(os.environ.get("POSTGRES_SSLMODE")),
    }


class Database:
    """Manages asyncpg connection pool and database provisioning.

    Supports both legacy per-butler databases and one-db/multi-schema runtime
    topology. This class handles creating the target database (provisioning)
    and managing an asyncpg pool, optionally with schema-scoped search_path.
    """

    def __init__(
        self,
        db_name: str,
        schema: str | None = None,
        host: str = "localhost",
        port: int = 5432,
        user: str = "postgres",
        password: str = "postgres",
        ssl: str | None = None,
        min_pool_size: int = 2,
        max_pool_size: int = 10,
    ) -> None:
        self.db_name = db_name
        self.schema = _normalize_schema_name(schema)
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.ssl = ssl
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        self.pool: asyncpg.Pool | None = None

    def set_schema(self, schema: str | None) -> None:
        """Set schema context for runtime query resolution."""
        self.schema = _normalize_schema_name(schema)

    def _server_settings(self) -> dict[str, str] | None:
        """Return asyncpg server settings for this database context."""
        search_path = schema_search_path(self.schema)
        if search_path is None:
            return None
        return {"search_path": search_path}

    async def provision(self) -> None:
        """Create the database if it doesn't exist.

        Connects to the 'postgres' maintenance database to check for and
        optionally create the butler's database.
        """
        connect_kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": "postgres",
        }
        if self.ssl is not None:
            connect_kwargs["ssl"] = self.ssl
        try:
            conn = await asyncpg.connect(**connect_kwargs)
        except Exception as exc:
            if not should_retry_with_ssl_disable(exc, self.ssl):
                raise
            retry_kwargs = dict(connect_kwargs)
            retry_kwargs["ssl"] = "disable"
            logger.info(
                "Retrying PostgreSQL provision connection with ssl=disable after SSL upgrade loss"
            )
            conn = await asyncpg.connect(**retry_kwargs)
        try:
            # Refresh collation version on template1 to prevent CREATE DATABASE
            # failures when the OS collation library version differs from what
            # was recorded when the template was created (e.g. after a
            # container image or OS update).  This is a no-op when versions
            # already match.
            try:
                await conn.execute("ALTER DATABASE template1 REFRESH COLLATION VERSION")
            except Exception:
                logger.debug("Could not refresh template1 collation version (non-fatal)")

            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1",
                self.db_name,
            )
            if not exists:
                # Can't use parameterized query for CREATE DATABASE
                # Sanitize db_name to prevent SQL injection
                safe_name = self.db_name.replace('"', '""')
                await conn.execute(f'CREATE DATABASE "{safe_name}" TEMPLATE template0')
                logger.info("Created database: %s", self.db_name)
            else:
                logger.info("Database already exists: %s", self.db_name)
        finally:
            await conn.close()

    async def connect(self) -> asyncpg.Pool:
        """Create and return a connection pool to the butler's database."""
        pool_kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.db_name,
            "min_size": self.min_pool_size,
            "max_size": self.max_pool_size,
        }
        server_settings = self._server_settings()
        if server_settings is not None:
            pool_kwargs["server_settings"] = server_settings
        if self.ssl is not None:
            pool_kwargs["ssl"] = self.ssl
        try:
            self.pool = await asyncpg.create_pool(**pool_kwargs)
        except Exception as exc:
            if not should_retry_with_ssl_disable(exc, self.ssl):
                raise
            retry_kwargs = dict(pool_kwargs)
            retry_kwargs["ssl"] = "disable"
            logger.info("Retrying PostgreSQL pool creation with ssl=disable after SSL upgrade loss")
            self.pool = await asyncpg.create_pool(**retry_kwargs)
        logger.info("Connection pool created for: %s", self.db_name)
        return self.pool

    async def close(self) -> None:
        """Close the connection pool."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Connection pool closed for: %s", self.db_name)

    # -- Pool proxy methods ------------------------------------------------
    # Modules receive a Database instance but need to call asyncpg pool
    # methods (fetch, fetchrow, fetchval, execute) directly.

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError(f"Database '{self.db_name}' has no active connection pool")
        return self.pool

    async def fetch(self, query: str, *args: Any, timeout: float | None = None) -> list[Any]:
        """Proxy to asyncpg Pool.fetch."""
        return await self._require_pool().fetch(query, *args, timeout=timeout)

    async def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> Any:
        """Proxy to asyncpg Pool.fetchrow."""
        return await self._require_pool().fetchrow(query, *args, timeout=timeout)

    async def fetchval(self, query: str, *args: Any, timeout: float | None = None) -> Any:
        """Proxy to asyncpg Pool.fetchval."""
        return await self._require_pool().fetchval(query, *args, timeout=timeout)

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:
        """Proxy to asyncpg Pool.execute."""
        return await self._require_pool().execute(query, *args, timeout=timeout)

    @classmethod
    def from_env(cls, db_name: str) -> Database:
        """Create Database instance from environment variables.

        Checks DATABASE_URL first (spec requirement), then falls back to
        individual POSTGRES_* vars for backward compatibility. Supports
        ``sslmode`` in DATABASE_URL query params and ``POSTGRES_SSLMODE``.

        DATABASE_URL format: postgres://user:password@host:port/database
        Default: postgres://butlers:butlers@localhost/postgres
        """
        params = db_params_from_env()
        return cls(
            db_name=db_name,
            host=str(params["host"]),
            port=int(params["port"]),
            user=str(params["user"]),
            password=str(params["password"]),
            ssl=params["ssl"] if isinstance(params["ssl"], str) else None,
        )
