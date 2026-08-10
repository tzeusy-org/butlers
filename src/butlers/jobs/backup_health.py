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
import re
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

# Details cross the executor persistence boundary into audit metadata and the
# dashboard API. Client output is untrusted (and can be a connection string or
# actual dump text), so only this small controlled vocabulary is retainable.
MAX_RESTORE_DRILL_DETAIL_CHARS = 512
_SAFE_RESTORE_DRILL_DETAIL = re.compile(
    r"^(?:"
    r"restored [1-9][0-9]* table(?:s)?|"
    r"restore produced zero tables|"
    r"postgresql-client tools not available|"
    r"failed to invoke (?:createdb|psql|psql \(integrity check\))|"
    r"backup artifact unreadable/corrupt|"
    r"restore timed out after [0-9]+s|"
    r"integrity check: unparseable table count|"
    r"(?:pre-cleanup|createdb|restore|integrity check|scratch cleanup) failed: "
    r"(?:permission denied|authentication failed|PostgreSQL client reported an error|"
    r"postgresql-client tools not available)"
    r")$"
)
_WITHHELD_RESTORE_DRILL_DETAIL = "restore drill diagnostic withheld"


@dataclass(frozen=True)
class RestoreDrillResult:
    """Outcome of one restore-drill attempt."""

    ok: bool
    detail: str
    table_count: int | None = None


def sanitize_restore_drill_detail(detail: object) -> str:
    """Return a bounded, controlled diagnostic safe for audit and API readers.

    In particular, this deliberately does not redact-and-retain arbitrary
    process output: a SQL dump can contain private data that is not identifiable
    by a credential-shaped pattern. Unknown text is withheld entirely.
    """
    if not isinstance(detail, str):
        return _WITHHELD_RESTORE_DRILL_DETAIL
    normalized = " ".join(detail.split())
    if not _SAFE_RESTORE_DRILL_DETAIL.fullmatch(normalized):
        return _WITHHELD_RESTORE_DRILL_DETAIL
    return normalized[:MAX_RESTORE_DRILL_DETAIL_CHARS]


def _result(ok: bool, detail: str, table_count: int | None = None) -> RestoreDrillResult:
    """Construct an outcome whose detail is safe before it can be persisted."""
    return RestoreDrillResult(
        ok=ok,
        detail=sanitize_restore_drill_detail(detail),
        table_count=table_count,
    )


def _client_failure_detail(stage: str, process: subprocess.CompletedProcess) -> str:
    """Classify untrusted PostgreSQL client output without retaining it."""
    raw = process.stderr or process.stdout or b""
    if isinstance(raw, bytes):
        message = raw[:1024].decode(errors="replace").lower()
    elif isinstance(raw, str):
        message = raw[:1024].lower()
    else:
        message = ""
    if "permission denied" in message:
        reason = "permission denied"
    elif "authentication failed" in message:
        reason = "authentication failed"
    else:
        reason = "PostgreSQL client reported an error"
    return f"{stage} failed: {reason}"


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

    def attempt() -> RestoreDrillResult:
        try:
            pre_cleanup = _run(["dropdb", *conn_args, *maintenance_args, "--if-exists", scratch_db])
        except FileNotFoundError:
            return _result(False, "postgresql-client tools not available")
        except OSError:
            return _result(False, "pre-cleanup failed: PostgreSQL client reported an error")
        if pre_cleanup.returncode != 0:
            return _result(False, _client_failure_detail("pre-cleanup", pre_cleanup))

        try:
            create = _run(["createdb", *conn_args, *maintenance_args, scratch_db])
        except FileNotFoundError:
            return _result(False, "postgresql-client tools not available")
        except OSError:
            return _result(False, "failed to invoke createdb")
        if create.returncode != 0:
            return _result(False, _client_failure_detail("createdb", create))

        try:
            with gzip.open(backup_path, "rb") as backup_file:
                sql_bytes = backup_file.read()
        except OSError:
            return _result(False, "backup artifact unreadable/corrupt")

        try:
            restore = _run(
                ["psql", *conn_args, "-d", scratch_db, "-v", "ON_ERROR_STOP=1"],
                input=sql_bytes,
                timeout=RESTORE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return _result(False, f"restore timed out after {RESTORE_TIMEOUT_S:.0f}s")
        except OSError:
            return _result(False, "failed to invoke psql")
        if restore.returncode != 0:
            return _result(False, _client_failure_detail("restore", restore))

        try:
            count_proc = _run(["psql", *conn_args, "-d", scratch_db, "-tAc", _INTEGRITY_QUERY])
        except OSError:
            return _result(False, "failed to invoke psql (integrity check)")
        if count_proc.returncode != 0:
            return _result(False, _client_failure_detail("integrity check", count_proc))

        raw_count = count_proc.stdout.decode(errors="replace").strip()
        try:
            table_count = int(raw_count)
        except ValueError:
            return _result(False, "integrity check: unparseable table count")
        if table_count == 0:
            return _result(False, "restore produced zero tables", table_count=0)
        return _result(True, f"restored {table_count} tables", table_count=table_count)

    result: RestoreDrillResult
    cleanup_failure: str | None = None
    try:
        result = attempt()
    except Exception:
        # A client wrapper should never take the deterministic executor down.
        result = _result(False, "restore failed: PostgreSQL client reported an error")
    finally:
        try:
            post_cleanup = _run(
                ["dropdb", *conn_args, *maintenance_args, "--if-exists", scratch_db]
            )
        except FileNotFoundError:
            cleanup_failure = "scratch cleanup failed: postgresql-client tools not available"
        except OSError:
            cleanup_failure = "scratch cleanup failed: PostgreSQL client reported an error"
        else:
            if post_cleanup.returncode != 0:
                cleanup_failure = _client_failure_detail("scratch cleanup", post_cleanup)

    if cleanup_failure is not None:
        return _result(False, cleanup_failure, table_count=result.table_count)
    return result


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
        "detail": None if row["error"] is None else sanitize_restore_drill_detail(row["error"]),
    }
