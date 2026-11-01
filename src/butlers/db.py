"""Database provisioning and connection pool management for butlers."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import asyncpg

logger = logging.getLogger(__name__)


class _RuntimeRoleError(RuntimeError):
    """Content-blind runtime boundary failure retaining its causal exception."""


# Known-default infrastructure credentials reported independently of runtime roles.
# Each entry is (env_var_name, known_default_value, service_label).
_KNOWN_DEFAULT_INFRA_CREDS: tuple[tuple[str, str, str], ...] = (
    ("MINIO_ROOT_USER", "minioadmin", "MinIO"),
    ("MINIO_ROOT_PASSWORD", "minioadmin", "MinIO"),
    ("GF_SECURITY_ADMIN_USER", "admin", "Grafana"),
    ("GF_SECURITY_ADMIN_PASSWORD", "admin", "Grafana"),
)


def has_insecure_infra_defaults() -> bool:
    """Return True when any known-default infra credential is active.

    Pure inspection — no logging, no side effects.  Reads the same
    ``_KNOWN_DEFAULT_INFRA_CREDS`` table used by ``check_infra_default_creds``.
    Absence of a credential is treated as the known default (Docker compose
    supplies the ``:-<default>`` fallback when the variable is unset).

    Returns ``True`` under any of:
    - one or more infra credentials are absent (Docker default applies), or
    - one or more infra credentials are explicitly set to the known default.

    Returns ``False`` only when every entry in ``_KNOWN_DEFAULT_INFRA_CREDS``
    is overridden with a non-default value.

    Intended for the dashboard health surface — call it at request time so live
    environment changes are reflected without restarting the app.
    """
    for env_var, known_default, _service in _KNOWN_DEFAULT_INFRA_CREDS:
        value = os.environ.get(env_var)
        if value is None or value == known_default:
            return True
    return False


def is_grafana_anonymous_enabled() -> bool:
    """Report configured anonymous Grafana access without a posture exemption.

    This observes this process's configuration, not a remote Grafana or the
    Kubernetes LGTM stack. Absence follows Compose's false default.
    """
    value = os.environ.get("GF_AUTH_ANONYMOUS_ENABLED", "false").strip().lower()
    return value not in ("false", "0", "no", "")


def check_infra_default_creds() -> None:
    """Warn about known-default infrastructure configuration without values.

    Retain the supported Compose default detection, independently of runtime
    role enforcement. This diagnostic does not attest fleet-wide credentials.
    """
    for env_var, known_default, service in _KNOWN_DEFAULT_INFRA_CREDS:
        value = os.environ.get(env_var)
        if value is None or value == known_default:
            logger.warning(
                "INSECURE DEFAULT: %s uses a known-default configuration for %s; "
                "configure a non-default credential.",
                service,
                env_var,
            )


def encode_jsonb(value: object) -> bytes:
    """Encode a Python object to the JSONB binary wire format.

    asyncpg's binary JSONB format requires a leading ``\\x01`` version byte
    followed by the UTF-8-encoded JSON string.
    """
    return b"\x01" + json.dumps(value).encode()


def _jsonb_decoder(data: bytes) -> object:
    """Decode the JSONB binary wire format to a Python object.

    asyncpg delivers a ``bytes`` value whose first byte is the JSONB format
    version (``\\x01``).  Strip it before passing to ``json.loads``.
    """
    return json.loads(data[1:])


async def register_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Register a JSONB type codec on an asyncpg connection.

    Without this, asyncpg returns JSONB columns as raw JSON strings instead of
    Python dicts.  Registering the codec ensures every JSONB value is
    automatically decoded to a Python object on read and encoded from a Python
    object on write, eliminating the need for call-site json.loads() guards.

    Uses ``format="binary"`` because asyncpg's JSONB binary wire protocol
    prepends a ``\\x01`` version byte; the text-format path does not reliably
    override asyncpg's internal codec for table column fetches.

    This function is designed to be passed as the ``init`` parameter of
    ``asyncpg.create_pool()`` so it runs once for every new physical connection.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=encode_jsonb,
        decoder=_jsonb_decoder,
        schema="pg_catalog",
        format="binary",
    )


_VALID_SSL_MODES = {"disable", "prefer", "allow", "require", "verify-ca", "verify-full"}
_SSL_UPGRADE_CONNECTION_LOST = "unexpected connection_lost() call"
_SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def pool_sizes_from_env(
    prefix: str,
    *,
    default_min: int,
    default_max: int,
) -> tuple[int, int]:
    """Read asyncpg pool size overrides from environment variables."""

    def _read_int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return default
        try:
            return int(raw)
        except ValueError:
            logger.warning("Ignoring invalid PostgreSQL pool size %s=%r", name, raw)
            return default

    min_size = _read_int(f"{prefix}_MIN_SIZE", default_min)
    max_size = _read_int(f"{prefix}_MAX_SIZE", default_max)

    if min_size < 0:
        logger.warning("Ignoring negative PostgreSQL pool min size for %s", prefix)
        min_size = default_min
    if max_size <= 0:
        logger.warning("Ignoring non-positive PostgreSQL pool max size for %s", prefix)
        max_size = default_max
    if min_size > max_size:
        logger.warning(
            "PostgreSQL pool min size exceeds max size for %s; clamping min to max",
            prefix,
        )
        min_size = max_size
    return min_size, max_size


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
    for part in (normalized, "public"):
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


def is_db_unreachable(exc: BaseException) -> bool:
    """Return whether *exc* is a transient database-connect failure.

    Walk the cause/context chain so asyncpg-wrapped socket failures are
    recognized. Configuration, authentication, and unrelated startup errors
    deliberately remain fatal instead of becoming an unbounded retry loop.
    """
    import errno
    import socket

    transient_errnos = {
        errno.ECONNREFUSED,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
        errno.ETIMEDOUT,
        errno.ECONNRESET,
        errno.EPIPE,
    }
    try:
        from butlers.storage import BlobStorageStartupError
    except ImportError:
        blob_startup_error: tuple[type[BaseException], ...] = ()
    else:
        blob_startup_error = (BlobStorageStartupError,)

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if blob_startup_error and isinstance(current, blob_startup_error):
            return False
        if isinstance(current, ConnectionRefusedError | ConnectionResetError | socket.gaierror):
            return True
        if isinstance(current, TimeoutError):
            return True
        if isinstance(current, OSError) and getattr(current, "errno", None) in transient_errnos:
            return True
        current = current.__cause__ or current.__context__
    return False


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


def database_name_from_env(fallback_db_name: str) -> str:
    """Resolve the canonical PostgreSQL target for pools and dedicated connections.

    ``DATABASE_URL`` is a complete connection target, so its decoded database
    path takes precedence over ``POSTGRES_DB`` and a caller's configured
    fallback. A supplied URL without a path is invalid rather than silently
    selecting a different database; PostgreSQL LISTEN/NOTIFY is scoped to the
    database, so divergent target selection would silently break event delivery.
    """
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        database_name = unquote(urlparse(database_url).path).removeprefix("/")
        if not database_name:
            raise ValueError("DATABASE_URL must include a database path")
        return database_name

    return os.environ.get("POSTGRES_DB", fallback_db_name)


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
        role: str | None = None,
        host: str = "localhost",
        port: int = 5432,
        user: str = "postgres",
        password: str = "postgres",
        ssl: str | None = None,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
    ) -> None:
        self.db_name = db_name
        self.schema = _normalize_schema_name(schema)
        # Set by daemon lifecycle from the loaded ButlerConfig. Modules use it
        # when a scheduler recovery needs an auditable configured identity.
        self.owner_butler: str | None = None
        # Set after runtime_config is seeded. Modules consume held operational
        # authority through this accessor rather than querying whichever domain
        # or private-memory pool a runtime hook happens to receive.
        self.runtime_config_accessor: Any | None = None
        self.role = role
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.ssl = ssl
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        self.pool: asyncpg.Pool | None = None
        self._role_verified: bool = False
        self._verified_role: str | None = None
        self._owner_database: Database | None = None

    @property
    def role_enforcement_disabled(self) -> bool:
        """Return True when SET ROLE enforcement is NOT active on this database.

        Enforcement is disabled when:
        - No role is configured (``self.role is None``), or
        - The role exists check ran but the role was not found / verification failed
          (``self._role_verified`` is False after ``connect()``).

        Returns ``True`` by default before ``connect()`` is called, since
        enforcement has not yet been established.  After ``connect()``, reflects
        the actual outcome of role-existence verification.

        Intended for the dashboard health surface — read after startup to report
        whether schema isolation via SET ROLE is active.
        """
        return self.role is None or not self._role_verified

    def set_schema(self, schema: str | None) -> None:
        """Set schema context for runtime query resolution."""
        self.schema = _normalize_schema_name(schema)

    def _server_settings(self) -> dict[str, str] | None:
        """Return asyncpg server settings for this database context."""
        search_path = schema_search_path(self.schema)
        if search_path is None:
            return None
        return {"search_path": search_path}

    async def _verify_role_exists(self, conn: asyncpg.Connection) -> bool:
        """Check if the configured role exists in pg_roles."""
        if self.role is None:
            return False
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)",
            self.role,
        )
        return exists is True

    def _runtime_role_required(self) -> bool:
        """Separate schema-less administrative utilities from runtime pools."""
        return self.schema is not None or self.role is not None

    @staticmethod
    def _owner_may_delegate(owner_schema: str | None, target: str | None) -> bool:
        """Own schema, Chronicler's private memory, or the shared ``public`` schema.

        ``public`` is already on every runtime search path, so a ``public``-only
        child under the owner's verified role confers no additional authority.
        """
        return (
            target == owner_schema
            or target == "public"
            or (owner_schema, target) == ("chronicler", "chronicler_mem")
        )

    def for_owned_schema(self, schema: str) -> Database:
        """Bind a module pool to this established primary owner, not a target role.

        This is search-path delegation only. PostgreSQL still decides schema and
        table privileges; no role, membership or grant is created here.
        """
        if not isinstance(self, Database) or self._owner_database is not None:
            raise RuntimeError("Runtime database requires an established primary owner")
        self._validate_runtime_role()
        if (
            self.schema is None
            or self.pool is None
            or self._role_verified is not True
            or self._verified_role != self.role
        ):
            raise RuntimeError("Runtime database primary owner is not established")
        target = _normalize_schema_name(schema)
        if not self._owner_may_delegate(self.schema, target):
            raise RuntimeError("Runtime database cannot access an unowned schema")
        child = Database(
            db_name=self.db_name,
            schema=target,
            role=self.role,
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            ssl=self.ssl,
            min_pool_size=self.min_pool_size,
            max_pool_size=self.max_pool_size,
        )
        child._owner_database = self
        return child

    def _validate_runtime_role(self) -> None:
        if not self._runtime_role_required():
            return
        if not isinstance(self.role, str) or not self.role.strip():
            raise RuntimeError("Runtime database requires a configured role")
        owner = self._owner_database
        if owner is not None:
            # Recheck the held primary identity on every acquisition. A private
            # search path never supplies authority to choose a sibling role.
            if owner._owner_database is not None:
                raise RuntimeError("Runtime database requires a primary owner")
            owner._validate_runtime_role()
            if (
                owner.schema is None
                or owner.pool is None
                or owner._role_verified is not True
                or owner._verified_role != owner.role
                or self.role != owner.role
                or not self._owner_may_delegate(owner.schema, self.schema)
            ):
                raise RuntimeError("Runtime database owned schema identity is unavailable")
        elif self.schema is not None and self.role != f"butler_{self.schema}_rw":
            raise RuntimeError("Runtime database role does not match its schema")

    async def _setup_connection(self, conn: asyncpg.Connection) -> None:
        """Establish the designated role before every runtime acquire returns."""
        self._validate_runtime_role()
        if not self._runtime_role_required():
            return
        if not self._role_verified or self.role != self._verified_role:
            raise RuntimeError("Runtime database role has not been established")
        quoted_role = '"' + self.role.replace('"', '""') + '"'
        try:
            await conn.execute(f"SET ROLE {quoted_role}")
            if self._owner_database is not None and self.schema != self._owner_database.schema:
                allowed = await conn.fetchval(
                    "SELECT has_schema_privilege($1, 'USAGE')", self.schema
                )
                if allowed is not True:
                    raise RuntimeError("Runtime private schema access is unavailable")
        except Exception as exc:
            raise _RuntimeRoleError("Runtime database role acquisition failed") from exc

    async def _init_connection(self, conn: asyncpg.Connection) -> None:
        """asyncpg init callback: register JSONB codec on each new connection.

        The init callback runs once when a physical connection is established,
        making it the right place for per-connection type codec registration.
        Unlike setup (which runs on every pool acquire), init only fires when
        the underlying TCP connection is created.
        """
        await register_jsonb_codec(conn)

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
            "init": self._init_connection,
        }
        server_settings = self._server_settings()
        if server_settings is not None:
            pool_kwargs["server_settings"] = server_settings
        if self.ssl is not None:
            pool_kwargs["ssl"] = self.ssl

        self._role_verified = False
        self._verified_role = None
        self._validate_runtime_role()
        if self._runtime_role_required():
            check_conn = None
            try:
                connect_kwargs = {
                    "host": self.host,
                    "port": self.port,
                    "user": self.user,
                    "password": self.password,
                    "database": self.db_name,
                    "ssl": self.ssl,
                }
                try:
                    check_conn = await asyncpg.connect(**connect_kwargs)
                except Exception as exc:
                    if not should_retry_with_ssl_disable(exc, self.ssl):
                        raise
                    connect_kwargs["ssl"] = "disable"
                    logger.info("Retrying role verification after SSL upgrade loss")
                    check_conn = await asyncpg.connect(**connect_kwargs)
                if await self._verify_role_exists(check_conn) is not True:
                    raise RuntimeError("Configured runtime role is unavailable")
                self._role_verified = True
                self._verified_role = self.role
                # Existence alone is insufficient: exercise SET ROLE using the
                # real connecting identity even for a lazy (min_size=0) pool.
                await self._setup_connection(check_conn)
                await check_conn.close()
                check_conn = None
            except BaseException as exc:
                self._role_verified = False
                self._verified_role = None
                if check_conn is not None:
                    try:
                        check_conn.terminate()
                    except Exception:
                        logger.error("Runtime role verification cleanup was incomplete")
                if isinstance(exc, _RuntimeRoleError):
                    raise
                if isinstance(exc, Exception):
                    # Keep the causal object for internal diagnosis, without
                    # interpolating private driver payload into the boundary.
                    raise _RuntimeRoleError("Runtime database role establishment failed") from exc
                raise
            pool_kwargs["setup"] = self._setup_connection

        candidate = None
        try:
            try:
                candidate = asyncpg.create_pool(**pool_kwargs)
                pool = await candidate
            except Exception as exc:
                if isinstance(candidate, asyncpg.Pool):
                    try:
                        candidate.terminate()
                    except Exception:
                        logger.error("Runtime pool initialization cleanup was incomplete")
                    candidate = None
                if not should_retry_with_ssl_disable(exc, self.ssl):
                    raise
                retry_kwargs = dict(pool_kwargs)
                retry_kwargs["ssl"] = "disable"
                logger.info("Retrying PostgreSQL pool creation after SSL upgrade loss")
                candidate = asyncpg.create_pool(**retry_kwargs)
                pool = await candidate
        except BaseException as exc:
            if isinstance(candidate, asyncpg.Pool):
                try:
                    candidate.terminate()
                except Exception:
                    logger.error("Runtime pool initialization cleanup was incomplete")
            self._role_verified = False
            self._verified_role = None
            if self._runtime_role_required() and isinstance(exc, Exception):
                raise _RuntimeRoleError("Runtime database pool initialization failed") from exc
            raise
        self.pool = pool
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

    async def fetch(self, query: str, *args: Any, timeout: float | None = None) -> list[Any]:  # noqa: ASYNC109 - asyncpg signature
        """Proxy to asyncpg Pool.fetch."""
        return await self._require_pool().fetch(query, *args, timeout=timeout)

    async def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> Any:  # noqa: ASYNC109 - asyncpg signature
        """Proxy to asyncpg Pool.fetchrow."""
        return await self._require_pool().fetchrow(query, *args, timeout=timeout)

    async def fetchval(self, query: str, *args: Any, timeout: float | None = None) -> Any:  # noqa: ASYNC109 - asyncpg signature
        """Proxy to asyncpg Pool.fetchval."""
        return await self._require_pool().fetchval(query, *args, timeout=timeout)

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:  # noqa: ASYNC109 - asyncpg signature
        """Proxy to asyncpg Pool.execute."""
        return await self._require_pool().execute(query, *args, timeout=timeout)

    @classmethod
    def from_env(cls, db_name: str) -> Database:
        """Create Database instance from environment variables.

        ``DATABASE_URL`` supplies both the connection parameters and, when set,
        the decoded database path. Without it, ``POSTGRES_DB`` overrides the
        caller's ``db_name`` fallback. Supports ``sslmode`` in DATABASE_URL
        query params and ``POSTGRES_SSLMODE``.

        DATABASE_URL format: postgres://user:password@host:port/database
        Default: postgres://butlers:butlers@localhost/postgres
        """
        params = db_params_from_env()
        min_pool_size, max_pool_size = pool_sizes_from_env(
            "BUTLERS_DB_POOL",
            default_min=1,
            default_max=10,
        )
        return cls(
            db_name=database_name_from_env(db_name),
            host=str(params["host"]),
            port=int(params["port"]),
            user=str(params["user"]),
            password=str(params["password"]),
            ssl=params["ssl"] if isinstance(params["ssl"], str) else None,
            min_pool_size=min_pool_size,
            max_pool_size=max_pool_size,
        )
