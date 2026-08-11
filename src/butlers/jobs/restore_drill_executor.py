"""Dedicated db-only executor for the bounded restore-drill lifecycle.

This process is intentionally separate from dashboard-api. It accepts only
purpose-scoped endpoint settings and a password read from its private Docker
secret file; it never resolves the shared ``POSTGRES_*`` or ``DATABASE_URL``
credential surface. Due state and result persistence pass only through the
migration-owned security-definer functions.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import ssl
import stat
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import asyncpg

from butlers.jobs.backup_health import (
    RestoreDrillResult,
    _run_restore_drill_sync,
    latest_backup_path,
    sanitize_restore_drill_detail,
)

logger = logging.getLogger(__name__)

DEFAULT_RESTORE_DRILL_INTERVAL_S = 7 * 24 * 3600
DEFAULT_RESTORE_DRILL_CHECK_INTERVAL_S = 3600
_DEFAULT_PASSWORD_FILE = Path("/run/secrets/restore_drill_executor_password")
_VALID_SSL_MODES = {"disable", "prefer", "allow", "require", "verify-ca", "verify-full"}
_VERIFYING_SSL_MODES = {"verify-ca", "verify-full"}
_CANONICAL_PORT = re.compile(r"^[1-9][0-9]{0,4}$")
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_LEGACY_NUMERIC_IPV4 = re.compile(
    r"^(?:0[xX][0-9A-Fa-f]+|[0-9]+)(?:\.(?:0[xX][0-9A-Fa-f]+|[0-9]+)){0,3}$"
)
_LOOPBACK_DNS_IDENTITIES = frozenset({"localhost", "localhost.localdomain"})
_COMPOSE_PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_FIREWALL_CAPABILITY_DIRECTORY = Path("/run/butlers/restore-drill-firewall")
_FIREWALL_CAPABILITY_VERSION = "butlers-restore-drill-firewall-v1"
_FIREWALL_CAPABILITY_SUFFIX = ".executor-capability-v1"
_FIREWALL_CAPABILITY_NONCE = re.compile(r"^[a-f0-9]{64}$")
_DOCKER_ID = re.compile(r"^[a-f0-9]{64}$")
_DOCKER_SHORT_ID = re.compile(r"^[a-f0-9]{12}$")


@dataclass(frozen=True)
class RestoreDrillExecutorConfig:
    """Only the purpose-bound configuration the executor needs."""

    host: str
    port: int
    application_db: str
    maintenance_db: str
    user: str
    password: str
    backup_dir: Path
    drill_interval_s: int
    check_interval_s: int
    sslmode: str | None = None
    sslrootcert_file: Path | None = None

    def cli_db_params(self) -> dict[str, str | int]:
        """Return the dedicated parameters passed to PostgreSQL client tools."""
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
        }


class RestoreDrillPersistence(Protocol):
    """Narrow persistence surface owned by the executor migration contract."""

    async def is_due(self, interval_s: int) -> bool: ...

    async def record_result(
        self, *, backup_name: str, result: str, detail: str, table_count: int | None
    ) -> int: ...


class PostgresRestoreDrillPersistence:
    """Call only the executor's fixed-search-path database functions."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def is_due(self, interval_s: int) -> bool:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT restore_drill_executor.is_due($1)", interval_s
            )
        return bool(value)

    async def record_result(
        self, *, backup_name: str, result: str, detail: str, table_count: int | None
    ) -> int:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                """
                SELECT restore_drill_executor.record_result($1, $2, $3, $4)
                """,
                backup_name,
                result,
                detail,
                table_count,
            )
        return int(value)


def _read_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _read_port(name: str, default: int) -> int:
    """Read the executor port with the same bounds as relay and firewall."""
    raw = os.environ.get(name, str(default))
    if not _CANONICAL_PORT.fullmatch(raw):
        raise ValueError(f"{name} must use canonical decimal 1..65535")
    value = int(raw)
    if value > 65535:
        raise ValueError(f"{name} must use canonical decimal 1..65535")
    return value


def _read_connection_host() -> str:
    """Require a DNS identity that Docker resolves to the internal relay alias."""
    host = os.environ.get("RESTORE_DRILL_EXECUTOR_DB_HOST", "")
    if _LEGACY_NUMERIC_IPV4.fullmatch(host):
        raise ValueError(
            "RESTORE_DRILL_EXECUTOR_DB_HOST must be a DNS hostname for the internal relay; "
            "numeric IPv4 literals are not supported"
        )
    if host.casefold() in _LOOPBACK_DNS_IDENTITIES:
        raise ValueError("RESTORE_DRILL_EXECUTOR_DB_HOST must be a DNS hostname, not localhost")
    if (
        not host
        or len(host) > 253
        or host.endswith(".")
        or not all(_DNS_LABEL.fullmatch(label) for label in host.split("."))
    ):
        raise ValueError(
            "RESTORE_DRILL_EXECUTOR_DB_HOST must be a DNS hostname for the internal relay"
        )
    return host


def _read_host_boot_id() -> str:
    """Return the host boot identifier that binds a marker to this boot."""
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("prepared firewall capability is unavailable") from exc
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", boot_id):
        raise ValueError("prepared firewall capability is unavailable")
    return boot_id


def _prepared_firewall_capability_error() -> ValueError:
    return ValueError("restore-drill executor prepared firewall capability is missing or invalid")


def _read_current_container_identity() -> str:
    """Return Docker's exact cgroup ID, or its documented short hostname fallback."""
    try:
        cgroup = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError:
        cgroup = ""
    container_ids = set(re.findall(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", cgroup))
    if len(container_ids) == 1:
        return next(iter(container_ids))

    try:
        hostname = Path("/etc/hostname").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise _prepared_firewall_capability_error() from exc
    if _DOCKER_SHORT_ID.fullmatch(hostname):
        return hostname
    raise _prepared_firewall_capability_error()


def _read_current_executor_ipv4() -> str:
    """Read this container's single Docker endpoint address without shelling out."""
    try:
        hostname = socket.gethostname()
        addresses = {
            entry[4][0]
            for entry in socket.getaddrinfo(
                hostname,
                None,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as exc:
        raise _prepared_firewall_capability_error() from exc
    if len(addresses) != 1:
        raise _prepared_firewall_capability_error()
    return next(iter(addresses))


def _has_connected_route_for(executor_ip: str, gateway: str, routes: str) -> bool:
    """Return whether one connected route contains both executor and gateway.

    Docker intentionally omits a default route from an ``internal`` network,
    so an attestation must validate the connected subnet rather than require a
    host-routable default gateway.
    """
    try:
        executor_value = int(ipaddress.IPv4Address(executor_ip))
        gateway_value = int(ipaddress.IPv4Address(gateway))
    except ipaddress.AddressValueError:
        return False
    for route in routes.splitlines()[1:]:
        fields = route.split()
        if len(fields) >= 8 and fields[2] == "00000000":
            try:
                destination = int(
                    ipaddress.IPv4Address(socket.inet_ntoa(struct.pack("<L", int(fields[1], 16))))
                )
                mask = int(
                    ipaddress.IPv4Address(socket.inet_ntoa(struct.pack("<L", int(fields[7], 16))))
                )
            except (OSError, ValueError, struct.error, ipaddress.AddressValueError):
                continue
            if (
                mask
                and (executor_value & mask) == (destination & mask)
                and (gateway_value & mask) == (destination & mask)
            ):
                return True
    return False


def _current_executor_network_contains(executor_ip: str, gateway: str) -> bool:
    try:
        routes = Path("/proc/net/route").read_text(encoding="utf-8")
    except OSError as exc:
        raise _prepared_firewall_capability_error() from exc
    return _has_connected_route_for(executor_ip, gateway, routes)


def _resolve_internal_relay_ipv4(host: str, port: int) -> str:
    """Resolve the Docker-scoped TLS alias and require one relay endpoint."""
    try:
        addresses = {
            entry[4][0]
            for entry in socket.getaddrinfo(
                host,
                port,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as exc:
        raise _prepared_firewall_capability_error() from exc
    if len(addresses) != 1:
        raise _prepared_firewall_capability_error()
    return next(iter(addresses))


def _require_prepared_firewall_capability(host: str, port: int) -> None:
    """Require a current root attestation before reading the executor secret.

    The marker is written only after the wrapper fenced both bridges.  Its
    nonce and container/topology fields prevent a same-boot manual
    ``compose down``/``up`` from replaying an authorization for the prior
    Docker generation.
    """
    project = os.environ.get("RESTORE_DRILL_EXECUTOR_FIREWALL_PROJECT", "")
    if not _COMPOSE_PROJECT_NAME.fullmatch(project):
        raise _prepared_firewall_capability_error()
    nonce = os.environ.get("RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE", "")
    if not _FIREWALL_CAPABILITY_NONCE.fullmatch(nonce):
        raise _prepared_firewall_capability_error()

    capability_path = _FIREWALL_CAPABILITY_DIRECTORY / f"{project}{_FIREWALL_CAPABILITY_SUFFIX}"
    try:
        metadata = capability_path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise OSError("capability is not a read-only regular file")
        content = capability_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _prepared_firewall_capability_error() from exc

    lines = content.splitlines()
    if len(lines) != 12:
        raise _prepared_firewall_capability_error()
    expected_keys = (
        "project",
        "port",
        "boot_id",
        "nonce",
        "executor_container_id",
        "executor_network_id",
        "executor_ip",
        "executor_gateway",
        "relay_container_id",
        "relay_network_id",
        "relay_ip",
    )
    if lines[0] != _FIREWALL_CAPABILITY_VERSION:
        raise _prepared_firewall_capability_error()
    values: dict[str, str] = {}
    for line, key in zip(lines[1:], expected_keys, strict=True):
        prefix = f"{key}="
        if not line.startswith(prefix):
            raise _prepared_firewall_capability_error()
        values[key] = line.removeprefix(prefix)

    expected = (
        f"{_FIREWALL_CAPABILITY_VERSION}\n"
        f"project={project}\n"
        f"port={port}\n"
        f"boot_id={_read_host_boot_id()}\n"
        f"nonce={nonce}\n"
        f"executor_container_id={values['executor_container_id']}\n"
        f"executor_network_id={values['executor_network_id']}\n"
        f"executor_ip={values['executor_ip']}\n"
        f"executor_gateway={values['executor_gateway']}\n"
        f"relay_container_id={values['relay_container_id']}\n"
        f"relay_network_id={values['relay_network_id']}\n"
        f"relay_ip={values['relay_ip']}\n"
    )
    if (
        content != expected
        or not _DOCKER_ID.fullmatch(values["executor_container_id"])
        or not _DOCKER_ID.fullmatch(values["executor_network_id"])
        or not _DOCKER_ID.fullmatch(values["relay_container_id"])
        or not _DOCKER_ID.fullmatch(values["relay_network_id"])
    ):
        raise _prepared_firewall_capability_error()

    container_identity = _read_current_container_identity()
    if (
        _DOCKER_ID.fullmatch(container_identity)
        and container_identity != values["executor_container_id"]
    ) or (
        _DOCKER_SHORT_ID.fullmatch(container_identity)
        and not values["executor_container_id"].startswith(container_identity)
    ):
        raise _prepared_firewall_capability_error()
    current_executor_ip = _read_current_executor_ipv4()
    if current_executor_ip != values["executor_ip"]:
        raise _prepared_firewall_capability_error()
    if not _current_executor_network_contains(current_executor_ip, values["executor_gateway"]):
        raise _prepared_firewall_capability_error()
    if _resolve_internal_relay_ipv4(host, port) != values["relay_ip"]:
        raise _prepared_firewall_capability_error()


def _read_executor_password(path: Path) -> str:
    """Read the file-backed secret without copying it into process logs."""
    try:
        # Decode bytes directly: ``Path.read_text`` uses universal-newline
        # translation, which would turn a forbidden CR into an LF before the
        # secret contract can reject it.
        password = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("restore-drill executor password file is unreadable") from exc
    # Match the managed provisioner: one optional terminal LF is a file-format
    # convenience, while embedded/multiple newlines, CR, and NUL are invalid.
    if password.endswith("\n"):
        password = password[:-1]
    if not password or "\n" in password or "\r" in password or "\x00" in password:
        raise ValueError("restore-drill executor password file must contain one non-empty line")
    return password


def _read_sslmode() -> str | None:
    """Read an executor-specific SSL mode without consulting shared settings."""
    value = os.environ.get("RESTORE_DRILL_EXECUTOR_SSLMODE", "").strip().lower()
    if not value:
        return None
    if value not in _VALID_SSL_MODES:
        raise ValueError("RESTORE_DRILL_EXECUTOR_SSLMODE is invalid")
    return value


def _read_sslrootcert_file(sslmode: str | None) -> Path | None:
    """Validate the executor-only CA root before a verification-mode connection.

    ``require`` deliberately needs no CA root: it asks PostgreSQL for TLS but
    does not claim certificate verification.  Both verification modes instead
    fail before startup if their read-only, noncredential root file is absent
    or malformed.
    """
    if sslmode not in _VERIFYING_SSL_MODES:
        return None
    raw_path = os.environ.get("RESTORE_DRILL_EXECUTOR_SSLROOTCERT_FILE", "").strip()
    if not raw_path:
        raise ValueError(
            "RESTORE_DRILL_EXECUTOR_SSLROOTCERT_FILE CA root file is required "
            "for verification modes"
        )
    path = Path(raw_path)
    try:
        if not path.is_file():
            raise OSError("not a regular file")
        # Parse the PEM now rather than letting an asyncpg/libpq connection
        # attempt report a path-dependent error after the executor starts.
        ssl.create_default_context(cafile=str(path))
    except (OSError, ssl.SSLError) as exc:
        raise ValueError("restore-drill executor CA root file is invalid") from exc
    return path


def load_restore_drill_executor_config() -> RestoreDrillExecutorConfig:
    """Load only ``RESTORE_DRILL_EXECUTOR_*`` configuration and its secret file."""
    host = _read_connection_host()
    port = _read_port("RESTORE_DRILL_EXECUTOR_DB_PORT", 5432)
    _require_prepared_firewall_capability(host, port)
    password_file = Path(
        os.environ.get("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", str(_DEFAULT_PASSWORD_FILE))
    )
    application_db = os.environ.get("RESTORE_DRILL_EXECUTOR_APPLICATION_DB", "butlers").strip()
    maintenance_db = os.environ.get("RESTORE_DRILL_EXECUTOR_MAINTENANCE_DB", "postgres").strip()
    user = os.environ.get("RESTORE_DRILL_EXECUTOR_USER", "restore_drill_executor").strip()
    if not host or not application_db or not maintenance_db or not user:
        raise ValueError("restore-drill executor endpoint configuration must not be empty")
    sslmode = _read_sslmode()
    return RestoreDrillExecutorConfig(
        host=host,
        port=port,
        application_db=application_db,
        maintenance_db=maintenance_db,
        user=user,
        password=_read_executor_password(password_file),
        backup_dir=Path(os.environ.get("RESTORE_DRILL_EXECUTOR_BACKUP_DIR", "/backups")),
        drill_interval_s=_read_positive_int(
            "RESTORE_DRILL_EXECUTOR_INTERVAL_S", DEFAULT_RESTORE_DRILL_INTERVAL_S
        ),
        check_interval_s=_read_positive_int(
            "RESTORE_DRILL_EXECUTOR_CHECK_INTERVAL_S", DEFAULT_RESTORE_DRILL_CHECK_INTERVAL_S
        ),
        sslmode=sslmode,
        sslrootcert_file=_read_sslrootcert_file(sslmode),
    )


def _psql_env(config: RestoreDrillExecutorConfig) -> dict[str, str]:
    """Expose only the dedicated password and endpoint TLS mode to client tools."""
    env = {"PGPASSWORD": config.password}
    if config.sslmode is not None:
        env["PGSSLMODE"] = config.sslmode
    if config.sslrootcert_file is not None:
        env["PGSSLROOTCERT"] = str(config.sslrootcert_file)
    return env


def _asyncpg_ssl_context(config: RestoreDrillExecutorConfig) -> ssl.SSLContext | str | None:
    """Build the explicit asyncpg TLS setting for the isolated endpoint.

    A custom context is needed for verification modes because the CA root is
    an executor-specific read-only mount, not ambient PostgreSQL configuration.
    ``verify-full`` deliberately retains the DNS connection hostname passed to
    asyncpg, so TLS checks that name while the executor's internal-network
    alias routes it only to the uncredentialed relay.
    """
    if config.sslmode is None:
        return None
    if config.sslmode == "require":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    if config.sslmode not in _VERIFYING_SSL_MODES:
        return config.sslmode

    if config.sslrootcert_file is None:
        raise ValueError("restore-drill executor CA root file is required for verification modes")
    try:
        context = ssl.create_default_context(cafile=str(config.sslrootcert_file))
    except (OSError, ssl.SSLError) as exc:
        raise ValueError("restore-drill executor CA root file is invalid") from exc
    context.check_hostname = config.sslmode == "verify-full"
    context.verify_mode = ssl.CERT_REQUIRED
    return context


async def run_restore_drill_executor_tick(
    config: RestoreDrillExecutorConfig,
    persistence: RestoreDrillPersistence,
    *,
    runner: Callable[..., RestoreDrillResult] = _run_restore_drill_sync,
) -> dict[str, object]:
    """Run one due restore attempt, keeping the dashboard out of the lifecycle."""
    try:
        due = await persistence.is_due(config.drill_interval_s)
    except Exception:
        # Database exception text can include a DSN, SQL, or a server-provided
        # detail. Keep this fixed stage diagnostic out of the audit/API path.
        logger.warning("restore drill executor due check failed")
        return {"skipped": True, "reason": "due check unavailable"}
    if not due:
        return {"skipped": True, "reason": "not due"}

    backup_path = latest_backup_path(config.backup_dir)
    if backup_path is None:
        return {"skipped": True, "reason": "no backup file present"}

    result = await asyncio.to_thread(
        runner,
        backup_path,
        db_params=config.cli_db_params(),
        maintenance_db=config.maintenance_db,
        process_env=_psql_env(config),
    )
    try:
        await persistence.record_result(
            backup_name=backup_path.name,
            result="pass" if result.ok else "fail",
            # Enforce the audit/API boundary even when a future runner is
            # swapped in: arbitrary subprocess text must never persist.
            detail=sanitize_restore_drill_detail(result.detail),
            table_count=result.table_count,
        )
    except Exception:
        logger.warning("restore drill executor result persistence failed")
        return {"ok": result.ok, "recorded": False, "backup_file": backup_path.name}

    return {"ok": result.ok, "recorded": True, "backup_file": backup_path.name}


async def run_restore_drill_executor_loop(config: RestoreDrillExecutorConfig) -> None:
    """Run the single executor's due check on its fixed cadence."""
    pool_kwargs: dict[str, object] = {
        "host": config.host,
        "port": config.port,
        "user": config.user,
        "password": config.password,
        "database": config.application_db,
        "min_size": 1,
        "max_size": 1,
    }
    ssl_context = _asyncpg_ssl_context(config)
    if ssl_context is not None:
        pool_kwargs["ssl"] = ssl_context
    pool = await asyncpg.create_pool(**pool_kwargs)
    persistence = PostgresRestoreDrillPersistence(pool)
    try:
        while True:
            try:
                summary = await run_restore_drill_executor_tick(config, persistence)
                logger.info(
                    "restore-drill executor tick complete: recorded=%s skipped=%s",
                    summary.get("recorded"),
                    summary.get("skipped"),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("restore drill executor tick failed")
            await asyncio.sleep(config.check_interval_s)
    finally:
        await pool.close()


def main() -> None:
    """Start the deterministic, db-only restore-drill executor."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    try:
        config = load_restore_drill_executor_config()
    except ValueError:
        logger.error("restore-drill executor configuration is invalid")
        raise
    asyncio.run(run_restore_drill_executor_loop(config))


if __name__ == "__main__":  # pragma: no cover - exercised by the container entrypoint
    main()
