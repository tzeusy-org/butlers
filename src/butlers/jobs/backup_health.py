"""Shared restore-drill mechanics and the dashboard's read-only result reader.

``restore_drill_executor`` owns scheduling, the file-backed recovery
credential, subprocess launch, and persistence through its constrained
database interface. This module deliberately contains no dashboard task or
shared-credential launch path. ``get_last_restore_drill`` remains here so
``GET /api/system/backups`` can read the durable verdict without receiving the
executor credential.
"""

from __future__ import annotations

import gzip
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

#: Scratch database the drill restores into, dropped before and after every
#: attempt. A fixed name lets the next single executor recover stale state
#: after an interrupted prior attempt.
DRILL_SCRATCH_DB = "butlers_restore_drill"

#: Ceiling for the restore subprocess. The executor records any failure via
#: its migration-owned persistence boundary rather than crashing its loop.
RESTORE_TIMEOUT_S = 1800.0

_DRILL_ACTION = "restore_drill_result"

_INTEGRITY_QUERY = (
    "SELECT count(*) FROM information_schema.tables "
    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
)


@dataclass(frozen=True)
class RestoreDrillResult:
    """Outcome of one restore-drill attempt."""

    ok: bool
    detail: str
    table_count: int | None = None


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _psql_env(db_params: dict[str, Any]) -> dict[str, str]:
    """Return the minimal subprocess environment for the isolated credential."""
    return {"PGPASSWORD": str(db_params.get("password") or "")}


def _connection_args(db_params: dict[str, Any]) -> list[str]:
    return [
        "-h",
        str(db_params["host"]),
        "-p",
        str(db_params["port"]),
        "-U",
        str(db_params["user"]),
    ]


def latest_backup_path(backup_dir: Path) -> Path | None:
    """Return the newest restore-drill candidate, or ``None`` when absent."""
    try:
        candidates = list(backup_dir.glob("butlers_*.sql.gz"))
    except OSError:
        return None
    stamped: list[tuple[float, Path]] = []
    for path in candidates:
        try:
            stamped.append((path.stat().st_mtime, path))
        except OSError:
            continue
    if not stamped:
        return None
    return max(stamped, key=lambda item: item[0])[1]


def _run_restore_drill_sync(
    backup_path: Path,
    *,
    db_params: dict[str, Any],
    scratch_db: str = DRILL_SCRATCH_DB,
    maintenance_db: str = "postgres",
    process_env: dict[str, str] | None = None,
) -> RestoreDrillResult:
    """Restore *backup_path* into a scratch database and verify it. Never raises.

    The caller supplies only the executor's dedicated connection parameters.
    ``createdb`` and ``dropdb`` name their maintenance database explicitly so
    they cannot rely on an ambient database default.
    """
    env = process_env if process_env is not None else _psql_env(db_params)
    conn_args = _connection_args(db_params)

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, env=env, capture_output=True, **kwargs)

    maintenance_args = ["--maintenance-db", maintenance_db]

    try:
        _run(["dropdb", *conn_args, *maintenance_args, "--if-exists", scratch_db])
        create = _run(["createdb", *conn_args, *maintenance_args, scratch_db])
    except FileNotFoundError as exc:
        return RestoreDrillResult(ok=False, detail=f"postgresql-client tools not available: {exc}")
    except OSError as exc:
        return RestoreDrillResult(ok=False, detail=f"failed to invoke createdb: {exc}")

    if create.returncode != 0:
        detail = (create.stderr or create.stdout or b"").decode(errors="replace").strip()
        return RestoreDrillResult(ok=False, detail=f"createdb failed: {detail[-2000:]}")

    try:
        try:
            with gzip.open(backup_path, "rb") as backup_file:
                sql_bytes = backup_file.read()
        except OSError as exc:
            return RestoreDrillResult(ok=False, detail=f"backup artifact unreadable/corrupt: {exc}")

        try:
            restore = _run(
                ["psql", *conn_args, "-d", scratch_db, "-v", "ON_ERROR_STOP=1"],
                input=sql_bytes,
                timeout=RESTORE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return RestoreDrillResult(
                ok=False, detail=f"restore timed out after {RESTORE_TIMEOUT_S:.0f}s"
            )
        except OSError as exc:
            return RestoreDrillResult(ok=False, detail=f"failed to invoke psql: {exc}")

        if restore.returncode != 0:
            detail = (restore.stderr or restore.stdout or b"").decode(errors="replace").strip()
            return RestoreDrillResult(ok=False, detail=f"restore failed: {detail[-2000:]}")

        try:
            count_proc = _run(["psql", *conn_args, "-d", scratch_db, "-tAc", _INTEGRITY_QUERY])
        except OSError as exc:
            return RestoreDrillResult(
                ok=False, detail=f"failed to invoke psql (integrity check): {exc}"
            )
        if count_proc.returncode != 0:
            detail = (
                (count_proc.stderr or count_proc.stdout or b"").decode(errors="replace").strip()
            )
            return RestoreDrillResult(ok=False, detail=f"integrity check failed: {detail[-2000:]}")

        raw_count = count_proc.stdout.decode(errors="replace").strip()
        try:
            table_count = int(raw_count)
        except ValueError:
            return RestoreDrillResult(
                ok=False, detail=f"integrity check: unparseable table count {raw_count!r}"
            )

        if table_count == 0:
            return RestoreDrillResult(
                ok=False, detail="restore produced zero tables", table_count=0
            )

        return RestoreDrillResult(
            ok=True, detail=f"restored {table_count} tables", table_count=table_count
        )
    finally:
        _run(["dropdb", *conn_args, *maintenance_args, "--if-exists", scratch_db])


async def get_last_restore_drill(pool: asyncpg.Pool) -> dict[str, Any] | None:
    """Return the latest durable restore-drill result for dashboard readers."""
    row = await pool.fetchrow(
        """
        SELECT ts, result, error
        FROM public.audit_log
        WHERE action = $1
        ORDER BY ts DESC LIMIT 1
        """,
        _DRILL_ACTION,
    )
    if row is None:
        return None
    return {
        "checked_at": _as_aware_utc(row["ts"]).isoformat(),
        "result": row["result"],
        "detail": row["error"],
    }
