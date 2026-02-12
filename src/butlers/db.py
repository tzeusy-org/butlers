"""Database provisioning and connection pool management for butlers."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import asyncpg

logger = logging.getLogger(__name__)

_VALID_SSL_MODES = {"disable", "prefer", "allow", "require", "verify-ca", "verify-full"}
_SSL_UPGRADE_CONNECTION_LOST = "unexpected connection_lost() call"


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

    Each butler owns a dedicated PostgreSQL database. This class handles
    creating the database if it doesn't exist (provisioning) and managing
    the asyncpg connection pool for runtime queries.
    """

    def __init__(
        self,
        db_name: str,
        host: str = "localhost",
        port: int = 5432,
        user: str = "postgres",
        password: str = "postgres",
        ssl: str | None = None,
        min_pool_size: int = 2,
        max_pool_size: int = 10,
    ) -> None:
        self.db_name = db_name
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.ssl = ssl
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        self.pool: asyncpg.Pool | None = None

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
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1",
                self.db_name,
            )
            if not exists:
                # Can't use parameterized query for CREATE DATABASE
                # Sanitize db_name to prevent SQL injection
                safe_name = self.db_name.replace('"', '""')
                await conn.execute(f'CREATE DATABASE "{safe_name}"')
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
        if self.ssl is not None:
            pool_kwargs["ssl"] = self.ssl
        try:
            self.pool = await asyncpg.create_pool(**pool_kwargs)
        except Exception as exc:
            if not should_retry_with_ssl_disable(exc, self.ssl):
                raise
            retry_kwargs = dict(pool_kwargs)
            retry_kwargs["ssl"] = "disable"
            logger.info(
                "Retrying PostgreSQL pool creation with ssl=disable after SSL upgrade loss"
            )
            self.pool = await asyncpg.create_pool(**retry_kwargs)
        logger.info("Connection pool created for: %s", self.db_name)
        return self.pool

    async def close(self) -> None:
        """Close the connection pool."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Connection pool closed for: %s", self.db_name)

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
