"""Weekly backup restore drill (bu-9r3hd.5, epic bu-9r3hd "Deploy spine").

Real incident this closes: ``GET /api/system/backups`` hardcoded every
discovered backup file's status to ``"success"`` -- a fabricated all-clear
that never actually checked whether the artifact could be restored. Verifying
that gzip decompresses cleanly (``butlers.api.routers.system`` now does that
live, per request -- see ``_verify_backup_artifact``) proves the *file* is
intact, but not that a real Postgres restore of its contents actually
succeeds. This module is the deeper check: once a week, it takes the most
recent backup, restores it into a scratch database, asserts the restore
produced real tables, tears the scratch database down, and records the
verdict -- pass or fail, never a silent skip that could be mistaken for
success.

Why weekly, why a background loop
----------------------------------
A restore drill is expensive (spawns ``createdb``/``psql``/``dropdb``
subprocesses against the real Postgres server, holds a scratch database for
the duration) and mutates state outside the running application -- unlike
the cheap, read-only per-request checks in ``butlers.api.routers.system``,
it cannot run inline with a dashboard page load. It runs as a periodic
``asyncio.Task`` inside the dashboard-api process (see
``butlers.api.app.lifespan``), mirroring ``butlers.jobs.deploy_drift`` and
``butlers.jobs.external_deadman``: a periodic, deterministic, zero-LLM check
that doesn't belong to any single butler daemon.

Where the result is recorded
------------------------------
``public.audit_log`` (no new migration) -- the same pattern
``butlers.jobs.deploy_drift`` established for its own debounce state and
``butlers.jobs.external_deadman`` established for its ping-success record.
:func:`get_last_restore_drill` reads the latest ``restore_drill_result`` row
for ``GET /api/system/backups`` to surface.

How the restore itself works
------------------------------
Backups are ``pg_dump --format=plain`` output (see
``deploy/backup/pg_dump.sh``), which embeds ``COPY ... FROM stdin`` blocks --
a libpq/psql protocol feature that a pure SQL-statement executor (e.g.
asyncpg's simple-query protocol) cannot replay. Restoring therefore shells
out to the real ``psql`` client (``postgresql-client``, added to
``Dockerfile.base`` alongside this bead), the same tool ``pg_dump.sh``'s
sibling sidecar already relies on. :func:`_run_restore_drill_sync` is a
plain synchronous function (subprocess calls, gzip reads) so it is trivially
mockable in tests; the async entry points run it via ``asyncio.to_thread``
so a multi-minute drill never blocks the dashboard-api event loop.

Fail-safe by construction
--------------------------
Every subprocess call is wrapped so a missing binary (``FileNotFoundError``,
e.g. the base image hasn't been rebuilt with ``postgresql-client`` yet), a
permission error (the configured Postgres role lacks ``CREATEDB``), a
timeout, or any other failure surfaces as a recorded "fail" result -- never
a crash of the loop, the daemon, or a fabricated "pass". A missing backup
file is a legitimate absence (nothing to drill yet) and is silently skipped,
matching the established convention in ``external_deadman``/``deploy_drift``
for "not yet configured/available" states.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from butlers.api.db import DatabaseManager
from butlers.api.routers import audit as audit_router
from butlers.api.routers.system import BACKUP_DIR_ENV, latest_backup_path
from butlers.db import db_params_from_env

logger = logging.getLogger(__name__)

# Cadence for the background loop below. Weekly, per the epic's acceptance
# criteria ("backup status is verified, never hardcoded 'success'" plus a
# restore drill).
DEFAULT_RESTORE_DRILL_INTERVAL_S = 7 * 24 * 3600.0

#: Scratch database the drill restores into, dropped before and after every
#: attempt. Fixed name (not per-tick unique) so a previous run's leftover
#: (e.g. the process was killed mid-drill) is reliably cleaned up by the
#: unconditional pre-drop rather than accumulating orphan databases.
DRILL_SCRATCH_DB = "butlers_restore_drill"

#: Ceiling for the restore subprocess -- generous for a personal-scale
#: instance, but bounded so a wedged psql (e.g. waiting on a prompt it never
#: expected) cannot pin the loop forever.
RESTORE_TIMEOUT_S = 1800.0

_DRILL_ACTOR = "restore_drill"
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
    env = os.environ.copy()
    env["PGPASSWORD"] = str(db_params.get("password") or "")
    return env


def _connection_args(db_params: dict[str, Any]) -> list[str]:
    return [
        "-h",
        str(db_params["host"]),
        "-p",
        str(db_params["port"]),
        "-U",
        str(db_params["user"]),
    ]


def _run_restore_drill_sync(
    backup_path: Path,
    *,
    db_params: dict[str, Any],
    scratch_db: str = DRILL_SCRATCH_DB,
) -> RestoreDrillResult:
    """Restore *backup_path* into a scratch database and verify it. Never raises.

    Sequence: drop any stale scratch DB from a previous run -> create fresh ->
    gunzip the backup and feed it to ``psql`` (the only client that
    understands the ``COPY ... FROM stdin`` blocks a plain-format pg_dump
    contains) -> assert the restore actually produced tables -> drop the
    scratch DB unconditionally (``finally``), so a failed drill never leaves
    a stray database behind.
    """
    env = _psql_env(db_params)
    conn_args = _connection_args(db_params)

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, env=env, capture_output=True, **kwargs)

    try:
        _run(["dropdb", *conn_args, "--if-exists", scratch_db])
        create = _run(["createdb", *conn_args, scratch_db])
    except FileNotFoundError as exc:
        return RestoreDrillResult(ok=False, detail=f"postgresql-client tools not available: {exc}")
    except OSError as exc:
        return RestoreDrillResult(ok=False, detail=f"failed to invoke createdb: {exc}")

    if create.returncode != 0:
        detail = (create.stderr or create.stdout or b"").decode(errors="replace").strip()
        return RestoreDrillResult(ok=False, detail=f"createdb failed: {detail[-2000:]}")

    try:
        try:
            with gzip.open(backup_path, "rb") as f:
                sql_bytes = f.read()
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
        _run(["dropdb", *conn_args, "--if-exists", scratch_db])


async def run_restore_drill_check(pool: asyncpg.Pool, backup_dir: Path) -> dict[str, Any]:
    """Run one restore-drill tick against the latest backup file. Never raises.

    Returns ``{"skipped": True, "reason": ...}`` when there is no backup file
    to drill yet (a legitimate absence, not a failure -- nothing is recorded
    to the ledger). Otherwise runs the drill via ``asyncio.to_thread`` (so a
    multi-minute restore never blocks the event loop) and records the result.
    """
    backup_path = latest_backup_path(backup_dir)
    if backup_path is None:
        return {"skipped": True, "reason": "no backup file present"}

    result = await asyncio.to_thread(
        _run_restore_drill_sync, backup_path, db_params=db_params_from_env()
    )

    try:
        await audit_router.append(
            pool,
            _DRILL_ACTOR,
            _DRILL_ACTION,
            target=backup_path.name,
            result="pass" if result.ok else "fail",
            error=None if result.ok else result.detail,
            metadata={
                "backup_file": backup_path.name,
                "table_count": result.table_count,
                "detail": result.detail,
            },
        )
    except Exception:
        logger.warning("restore drill: failed to record result", exc_info=True)
        return {"ok": result.ok, "detail": result.detail, "recorded": False}

    return {"ok": result.ok, "detail": result.detail, "recorded": True}


async def get_last_restore_drill(pool: asyncpg.Pool) -> dict[str, Any] | None:
    """Return the most recent restore-drill result, or ``None`` if none yet.

    Read-only -- safe to call from ``GET /api/system/backups`` and the
    background loop alike.
    """
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


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------


async def run_restore_drill_loop(
    db: DatabaseManager,
    *,
    interval_s: float = DEFAULT_RESTORE_DRILL_INTERVAL_S,
) -> None:
    """Run :func:`run_restore_drill_check` every ``interval_s`` until cancelled.

    Sleeps first, mirroring ``run_migration_drift_loop`` / ``run_external_
    deadman_loop`` -- avoids a real restore attempt at every process boot
    (dev reloads, full-lifespan tests) before the first tick actually
    matters. A single tick's failure is logged and swallowed so one bad tick
    (or one week of ``createdb: permission denied``) never kills the loop --
    it keeps retrying, and keeps recording the true state, every week.
    """
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        await asyncio.sleep(interval_s)
        backup_dir_value = os.environ.get(BACKUP_DIR_ENV, "").strip()
        if not backup_dir_value:
            continue  # unconfigured -- legitimate absence, nothing to drill
        try:
            pool = db.pool("switchboard")
        except KeyError:
            logger.warning("restore drill: switchboard pool unavailable, skipping tick")
            continue
        try:
            summary = await run_restore_drill_check(pool, Path(backup_dir_value))
            logger.info("restore_drill_check: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("restore_drill_check: tick failed")
