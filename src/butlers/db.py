"""Database provisioning and connection pool management for butlers."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import asyncpg

logger = logging.getLogger(__name__)


def is_hardened_posture() -> bool:
    """Return True when the deployment posture is ``hardened``.

    Reads the ``BUTLERS_POSTURE`` environment variable (set via ``--hardened``
    flag or ``BUTLERS_POSTURE=hardened`` in the environment).  Default-when-unset
    is ``dev`` (fail-open) so the running dev stack is never broken unless the
    operator explicitly opts in.

    See ``docs/operations/deployment-posture.md`` for the full posture reference.
    """
    return os.environ.get("BUTLERS_POSTURE", "dev").strip().lower() == "hardened"


# Known-default infra credentials that must be changed in hardened deployments.
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


def is_grafana_anon_outside_dev() -> bool:
    """Return True when Grafana anonymous viewer is enabled outside dev posture.

    In dev posture Grafana anon access is expected (convenient for local
    iteration) so this returns ``False``.  In hardened posture anon access
    should be disabled; if ``GF_AUTH_ANONYMOUS_ENABLED`` is not ``"false"``
    (absent counts as enabled via Docker's ``:-false`` default, which means
    safe for direct compose invocations — but explicit ``true`` in hardened
    posture is the concern) this returns ``True``.

    The intent is to surface a concrete operator action: disable anon access
    before switching to hardened posture.

    Returns ``False`` in dev posture unconditionally.
    """
    if not is_hardened_posture():
        return False
    # In hardened posture, GF_AUTH_ANONYMOUS_ENABLED must be "false".
    # Absent = safe (docker-compose.observability.yml defaults to "false").
    value = os.environ.get("GF_AUTH_ANONYMOUS_ENABLED", "false").strip().lower()
    return value not in ("false", "0", "no", "")


def check_infra_default_creds() -> None:
    """Detect known-default infra credentials and warn or refuse based on posture.

    Reads each infra credential from the environment (env-var indirection).
    When a credential is absent, the docker-compose default (``:-minioadmin`` /
    ``:-admin``) would be used by Docker, so absence is treated as default.

    Behaviour:
    - **dev posture** (``BUTLERS_POSTURE`` unset or ``"dev"``): logs a loud
      WARNING for each known-default credential found.  The stack continues
      to start so the dev workflow is never broken.
    - **hardened posture** (``BUTLERS_POSTURE=hardened``): raises
      ``RuntimeError`` listing all offending credentials.  Startup is refused
      until every default is replaced.

    Services whose compose profiles are not active will still expose the env
    vars if set, but the check is conservative: it only fires when the env var
    is explicitly set to a known default OR is absent (treated as default).
    """
    hardened = is_hardened_posture()
    offenders: list[str] = []

    for env_var, known_default, service in _KNOWN_DEFAULT_INFRA_CREDS:
        value = os.environ.get(env_var)
        # Absent → docker-compose uses the ``:-<default>`` fallback, so treat
        # absence the same as the explicit known default.
        is_default = value is None or value == known_default
        if is_default:
            offenders.append(f"{service} {env_var} (known default)")
            if not hardened:
                logger.warning(
                    "INSECURE DEFAULT: %s is using the known-default value for %s. "
                    "Set %s to a strong credential before deploying to production. "
                    "(posture=dev — startup continues)",
                    service,
                    env_var,
                    env_var,
                )

    if hardened and offenders:
        raise RuntimeError(
            "Hardened posture requires all infra credentials to be changed from "
            "known defaults.  The following credentials are still at their "
            f"known-default values: {', '.join(offenders)}.  "
            "Set each to a strong credential via the corresponding environment "
            "variable before starting with BUTLERS_POSTURE=hardened."
        )


def _jsonb_encoder(value: object) -> bytes:
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
        encoder=_jsonb_encoder,
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
        role: str | None = None,
        host: str = "localhost",
        port: int = 5432,
        user: str = "postgres",
        password: str = "postgres",
        ssl: str | None = None,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        strict_role_enforcement: bool | None = None,
    ) -> None:
        self.db_name = db_name
        self.schema = _normalize_schema_name(schema)
        self.role = role
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.ssl = ssl
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        # strict_role_enforcement: None → auto-detect from BUTLERS_POSTURE env var.
        # True → hardened (fail-closed on role missing/unverifiable).
        # False → dev (fail-open: log warning and continue without SET ROLE).
        if strict_role_enforcement is None:
            self.strict_role_enforcement: bool = is_hardened_posture()
        else:
            self.strict_role_enforcement = strict_role_enforcement
        self.pool: asyncpg.Pool | None = None
        self._role_verified: bool = False

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
        return bool(exists)

    async def _setup_connection(self, conn: asyncpg.Connection) -> None:
        """asyncpg setup callback: SET ROLE on every connection acquire."""
        if not self._role_verified or self.role is None:
            return
        quoted_role = '"' + self.role.replace('"', '""') + '"'
        await conn.execute(f"SET ROLE {quoted_role}")

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

        # Verify role existence before pool creation
        if self.role is not None:
            _check_ssl = self.ssl

            async def _open_check_conn(ssl: str | None) -> asyncpg.Connection:
                return await asyncpg.connect(
                    host=self.host,
                    port=self.port,
                    user=self.user,
                    password=self.password,
                    database=self.db_name,
                    ssl=ssl,
                )

            try:
                try:
                    check_conn = await _open_check_conn(_check_ssl)
                except Exception as exc:
                    if not should_retry_with_ssl_disable(exc, _check_ssl):
                        raise
                    logger.info(
                        "Retrying role verification connection with ssl=disable "
                        "after SSL upgrade loss"
                    )
                    _check_ssl = "disable"
                    check_conn = await _open_check_conn(_check_ssl)
                try:
                    self._role_verified = await self._verify_role_exists(check_conn)
                finally:
                    await check_conn.close()
            except (asyncpg.PostgresError, OSError) as exc:
                if self.strict_role_enforcement:
                    raise RuntimeError(
                        f"[hardened] Could not verify role {self.role!r} existence"
                        f" for {self.db_name!r};"
                        f" refusing to start with unverified role enforcement: {exc}"
                    ) from exc
                logger.warning(
                    "Could not verify role %r existence; SET ROLE enforcement disabled for %s: %s",
                    self.role,
                    self.db_name,
                    exc,
                )
                self._role_verified = False

            if self._role_verified:
                pool_kwargs["setup"] = self._setup_connection
                logger.info(
                    "SET ROLE enforcement enabled: %s (schema=%s)",
                    self.role,
                    self.schema,
                )
            else:
                if self.strict_role_enforcement:
                    raise RuntimeError(
                        f"[hardened] Role {self.role!r} not found in pg_roles"
                        f" for {self.db_name!r};"
                        f" refusing to start with SET ROLE enforcement disabled."
                    )
                logger.warning(
                    "Role %r not found; SET ROLE enforcement disabled. "
                    "Butler %s runs with shared-user privileges.",
                    self.role,
                    self.db_name,
                )

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
        min_pool_size, max_pool_size = pool_sizes_from_env(
            "BUTLERS_DB_POOL",
            default_min=1,
            default_max=10,
        )
        return cls(
            db_name=db_name,
            host=str(params["host"]),
            port=int(params["port"]),
            user=str(params["user"]),
            password=str(params["password"]),
            ssl=params["ssl"] if isinstance(params["ssl"], str) else None,
            min_pool_size=min_pool_size,
            max_pool_size=max_pool_size,
        )
