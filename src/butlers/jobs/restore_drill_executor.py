"""Dedicated db-only executor for the bounded restore-drill lifecycle.

This process is intentionally separate from dashboard-api. It accepts only
purpose-scoped endpoint settings and a password read from its private Docker
secret file; it never resolves the shared ``POSTGRES_*`` or ``DATABASE_URL``
credential surface. Due state and result persistence pass only through the
migration-owned security-definer functions.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import asyncpg

from butlers.jobs.backup_health import (
    RestoreDrillResult,
    _run_restore_drill_sync,
    latest_backup_path,
)

logger = logging.getLogger(__name__)

DEFAULT_RESTORE_DRILL_INTERVAL_S = 7 * 24 * 3600
DEFAULT_RESTORE_DRILL_CHECK_INTERVAL_S = 3600
_DEFAULT_PASSWORD_FILE = Path("/run/secrets/restore_drill_executor_password")
_VALID_SSL_MODES = {"disable", "prefer", "allow", "require", "verify-ca", "verify-full"}


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


def _read_executor_password(path: Path) -> str:
    """Read the file-backed secret without copying it into process logs."""
    try:
        password = path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise ValueError("restore-drill executor password file is unreadable") from exc
    if not password or "\n" in password or "\r" in password:
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


def load_restore_drill_executor_config() -> RestoreDrillExecutorConfig:
    """Load only ``RESTORE_DRILL_EXECUTOR_*`` configuration and its secret file."""
    password_file = Path(
        os.environ.get("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", str(_DEFAULT_PASSWORD_FILE))
    )
    host = os.environ.get("RESTORE_DRILL_EXECUTOR_DB_HOST", "localhost").strip()
    application_db = os.environ.get("RESTORE_DRILL_EXECUTOR_APPLICATION_DB", "butlers").strip()
    maintenance_db = os.environ.get("RESTORE_DRILL_EXECUTOR_MAINTENANCE_DB", "postgres").strip()
    user = os.environ.get("RESTORE_DRILL_EXECUTOR_USER", "restore_drill_executor").strip()
    if not host or not application_db or not maintenance_db or not user:
        raise ValueError("restore-drill executor endpoint configuration must not be empty")
    return RestoreDrillExecutorConfig(
        host=host,
        port=_read_positive_int("RESTORE_DRILL_EXECUTOR_DB_PORT", 5432),
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
        sslmode=_read_sslmode(),
    )


def _psql_env(config: RestoreDrillExecutorConfig) -> dict[str, str]:
    """Expose only the dedicated password and endpoint TLS mode to client tools."""
    env = {"PGPASSWORD": config.password}
    if config.sslmode is not None:
        env["PGSSLMODE"] = config.sslmode
    return env


async def run_restore_drill_executor_tick(
    config: RestoreDrillExecutorConfig,
    persistence: RestoreDrillPersistence,
    *,
    runner: Callable[..., RestoreDrillResult] = _run_restore_drill_sync,
) -> dict[str, object]:
    """Run one due restore attempt, keeping the dashboard out of the lifecycle."""
    if not await persistence.is_due(config.drill_interval_s):
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
            detail=result.detail,
            table_count=result.table_count,
        )
    except Exception:
        logger.warning("restore drill executor could not persist its result", exc_info=True)
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
    if config.sslmode is not None:
        pool_kwargs["ssl"] = config.sslmode
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
                logger.exception("restore-drill executor tick failed")
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
