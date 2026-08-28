"""DB-free backup artifact and run-receipt facts.

Both the system API and QA infrastructure patrol need the same read-only
filesystem facts: artifact recency/integrity plus the most recent backup-run
receipt.  Keeping that reader below both callers prevents QA from depending on
the HTTP router.  Restore-drill ledger access deliberately remains with the
system API because it needs a database pool and its safe degradation boundary.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

__all__ = [
    "BACKUP_DIR_ENV",
    "BACKUP_STALE_THRESHOLD_HOURS",
    "BackupEvent",
    "BackupFacts",
    "BackupRunFacts",
    "RestoreDrillFacts",
    "read_backup_facts_from_dir",
]


class BackupEvent(BaseModel):
    """Single backup event in the backup history list.

    ``status`` is a real per-artifact verdict, never a fabricated constant:
    ``"healthy"`` read through gzip cleanly and cleared the size floor,
    ``"corrupt"`` failed gzip decompression, and ``"empty"`` is smaller than
    any real dump could plausibly be.
    """

    completed_at: str
    size_bytes: int
    status: str  # "healthy" | "corrupt" | "empty"


class RestoreDrillFacts(BaseModel):
    """Result of the most recent weekly restore-drill attempt.

    The DB-free reader returns the honest ``"pending"`` default.  The system
    API replaces it with the isolated executor's ledger result.
    """

    checked_at: str | None
    result: str  # "pass" | "fail" | "pending" | "degraded"
    detail: str | None


class BackupRunFacts(BaseModel):
    """Outcome of the most recent backup run, successful or not.

    This differs from artifact recency: a failed run publishes no dump, so a
    fresh surviving artifact alone cannot show that the most recent run failed.
    """

    result: str  # "success" | "failed" | "unknown"
    finished_at: str | None
    exit_code: int | None
    reason: str | None


class BackupFacts(BaseModel):
    """Backup recency, artifact health, and source reachability facts."""

    last_backup_at: str | None
    last_backup_size_bytes: int | None
    backup_source_reachable: bool
    backup_history: list[BackupEvent]
    last_backup_status: str  # "healthy" | "corrupt" | "empty" | "missing"
    backup_stale: bool
    last_run: BackupRunFacts
    restore_drill: RestoreDrillFacts


#: Env var naming the directory written by deploy/backup/pg_dump.sh.
BACKUP_DIR_ENV = "BUTLERS_BACKUP_DIR"

#: A gzip stream this small cannot hold a real pg_dump.
_BACKUP_MIN_SIZE_BYTES = 256

#: Daily backup cron plus slack for one missed or late run.
BACKUP_STALE_THRESHOLD_HOURS = 36

#: Memoize gzip integrity by artifact identity; dashboard polling must not
#: re-decompress an unchanged dump on every request.
_verify_cache: dict[tuple[str, float, int], tuple[str, str | None]] = {}
_VERIFY_CACHE_MAX_ENTRIES = 64


def _verify_backup_artifact(path: Path, stat: os.stat_result) -> tuple[str, str | None]:
    """Return ``(status, detail)`` for one backup file without raising."""
    key = (str(path), stat.st_mtime, stat.st_size)
    cached = _verify_cache.get(key)
    if cached is not None:
        return cached

    if stat.st_size < _BACKUP_MIN_SIZE_BYTES:
        result = ("empty", f"{stat.st_size} bytes, below the {_BACKUP_MIN_SIZE_BYTES}-byte floor")
    else:
        try:
            with gzip.open(path, "rb") as artifact:
                while artifact.read(1 << 20):
                    pass
            result = ("healthy", None)
        except OSError as exc:
            result = ("corrupt", f"gzip integrity check failed: {exc}")

    if len(_verify_cache) >= _VERIFY_CACHE_MAX_ENTRIES:
        _verify_cache.clear()
    _verify_cache[key] = result
    return result


#: Filename deploy/backup/pg_dump.sh rewrites at the end of every run.
BACKUP_RUN_SENTINEL_FILENAME = "last_run.json"

#: Fixed receipt vocabulary; the mounted-volume reason may reach the dashboard.
_BACKUP_RUN_REASONS = frozenset(
    {"ok", "pg_dump_failed", "artifact_undersize", "artifact_corrupt", "unexpected_error"}
)
_BACKUP_RUN_REASON_UNRECOGNIZED = "unrecognized reason"
_BACKUP_RUN_SENTINEL_ABSENT_DETAIL = "no run outcome recorded"
_BACKUP_RUN_SENTINEL_UNREADABLE_DETAIL = "run outcome unreadable"

_UNKNOWN_BACKUP_RUN = BackupRunFacts(
    result="unknown",
    finished_at=None,
    exit_code=None,
    reason=_BACKUP_RUN_SENTINEL_ABSENT_DETAIL,
)
_UNREADABLE_BACKUP_RUN = BackupRunFacts(
    result="unknown",
    finished_at=None,
    exit_code=None,
    reason=_BACKUP_RUN_SENTINEL_UNREADABLE_DETAIL,
)
_PENDING_RESTORE_DRILL = RestoreDrillFacts(checked_at=None, result="pending", detail=None)


def _read_backup_run_facts(backup_dir: Path) -> BackupRunFacts:
    """Return the most recent backup-run outcome without raising.

    Missing, malformed, or unexpected receipts are all honestly ``"unknown"``;
    none may read as a successful run.
    """
    path = backup_dir / BACKUP_RUN_SENTINEL_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _UNKNOWN_BACKUP_RUN
    except OSError:
        return _UNREADABLE_BACKUP_RUN

    try:
        payload = json.loads(raw)
    except ValueError:
        return _UNREADABLE_BACKUP_RUN
    if not isinstance(payload, dict):
        return _UNREADABLE_BACKUP_RUN

    result = payload.get("result")
    if result not in ("success", "failed"):
        return _UNREADABLE_BACKUP_RUN

    reason = payload.get("reason")
    if not isinstance(reason, str):
        reason = None
    elif reason not in _BACKUP_RUN_REASONS:
        reason = _BACKUP_RUN_REASON_UNRECOGNIZED

    exit_code = payload.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        exit_code = None

    return BackupRunFacts(
        result=result,
        finished_at=_parse_sentinel_timestamp(payload.get("finished_at")),
        exit_code=exit_code,
        reason=reason,
    )


def _parse_sentinel_timestamp(value: object) -> str | None:
    """Normalize a receipt timestamp to UTC ISO 8601, or return ``None``."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _unreachable_backup_facts() -> BackupFacts:
    return BackupFacts(
        last_backup_at=None,
        last_backup_size_bytes=None,
        backup_source_reachable=False,
        backup_history=[],
        last_backup_status="missing",
        backup_stale=False,
        last_run=_UNKNOWN_BACKUP_RUN,
        restore_drill=_PENDING_RESTORE_DRILL,
    )


def read_backup_facts_from_dir(backup_dir: Path | None) -> BackupFacts:
    """Read DB-free artifact and run-receipt facts from ``backup_dir``.

    Files match ``butlers_*.sql.gz`` and are sorted by mtime descending.  The
    returned restore-drill value is always the honest ``"pending"`` default;
    the system API overlays its DB-backed ledger result separately.  ``None``
    and inaccessible directories yield the existing degraded payload rather
    than raising.
    """
    if backup_dir is None or not backup_dir.is_dir():
        return _unreachable_backup_facts()

    try:
        candidates = list(backup_dir.glob("butlers_*.sql.gz"))
    except OSError as exc:
        logger.warning("Cannot read backup directory %s: %s", backup_dir, exc)
        return _unreachable_backup_facts()

    stamped: list[tuple[float, os.stat_result, Path]] = []
    for candidate in candidates:
        try:
            stat = candidate.stat()
            stamped.append((stat.st_mtime, stat, candidate))
        except OSError:
            continue
    stamped.sort(key=lambda item: item[0], reverse=True)

    history: list[BackupEvent] = []
    for _mtime, stat, candidate in stamped[:7]:
        completed_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        status, _detail = _verify_backup_artifact(candidate, stat)
        history.append(
            BackupEvent(
                completed_at=completed_at.isoformat(),
                size_bytes=stat.st_size,
                status=status,
            )
        )

    if not history:
        return BackupFacts(
            last_backup_at=None,
            last_backup_size_bytes=None,
            backup_source_reachable=True,
            backup_history=[],
            last_backup_status="missing",
            backup_stale=False,
            last_run=_read_backup_run_facts(backup_dir),
            restore_drill=_PENDING_RESTORE_DRILL,
        )

    latest = history[0]
    age_hours = (
        datetime.now(UTC) - datetime.fromisoformat(latest.completed_at)
    ).total_seconds() / 3600
    return BackupFacts(
        last_backup_at=latest.completed_at,
        last_backup_size_bytes=latest.size_bytes,
        backup_source_reachable=True,
        backup_history=history,
        last_backup_status=latest.status,
        backup_stale=age_hours > BACKUP_STALE_THRESHOLD_HOURS,
        last_run=_read_backup_run_facts(backup_dir),
        restore_drill=_PENDING_RESTORE_DRILL,
    )
