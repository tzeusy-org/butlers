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
    "OFFSITE_COPY_ENABLED_ENV",
    "OffsiteCopyFacts",
    "RestoreDrillFacts",
    "offsite_copy_expected_from_env",
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


class OffsiteCopyFacts(BaseModel):
    """Outcome of the most recent off-node restic copy of the dump directory.

    ``"disabled"`` means this deployment does not run the copy job at all;
    ``"unknown"`` means it should, but no readable receipt exists yet. Neither
    is a success. ``stale`` is true when an enabled copy has not reported a
    run within :data:`BACKUP_STALE_THRESHOLD_HOURS`, whatever that run's result.
    """

    result: str  # "success" | "failed" | "unknown" | "disabled"
    finished_at: str | None
    reason: str | None
    snapshot_id: str | None
    stale: bool


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
    offsite_copy: OffsiteCopyFacts


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

#: Env var the chart sets on the dashboard API only when the off-node copy
#: CronJob exists (deploy/helm/butlers, ``offsiteCopy.enabled``).
OFFSITE_COPY_ENABLED_ENV = "BUTLERS_OFFSITE_COPY_ENABLED"

#: Filename deploy/backup/restic_offsite_copy.sh rewrites after every run.
OFFSITE_COPY_RECEIPT_FILENAME = "offsite_last_run.json"

#: Fixed receipt vocabulary, mirrored by the copy script. Keep them in step.
_OFFSITE_COPY_REASONS = frozenset(
    {
        "ok",
        "config_missing",
        "no_dumps",
        "repo_unreachable",
        "repo_init_failed",
        "backup_failed",
        "retention_failed",
        "unexpected_error",
    }
)
_OFFSITE_COPY_ABSENT_DETAIL = "no copy outcome recorded"
_OFFSITE_COPY_UNREADABLE_DETAIL = "copy outcome unreadable"
_SNAPSHOT_ID_CHARS = frozenset("0123456789abcdef")

_DISABLED_OFFSITE_COPY = OffsiteCopyFacts(
    result="disabled", finished_at=None, reason=None, snapshot_id=None, stale=False
)


def offsite_copy_expected_from_env() -> bool:
    """Return whether this deployment runs the off-node copy job."""
    return os.environ.get(OFFSITE_COPY_ENABLED_ENV, "").strip().lower() in {"1", "true", "yes"}


def _unknown_offsite_copy(reason: str) -> OffsiteCopyFacts:
    return OffsiteCopyFacts(
        result="unknown", finished_at=None, reason=reason, snapshot_id=None, stale=True
    )


def _read_offsite_copy_facts(backup_dir: Path | None, *, expected: bool) -> OffsiteCopyFacts:
    """Return the most recent off-node copy outcome without raising.

    The receipt sits on a mounted volume next to the dumps, so every field is
    reduced to a fixed vocabulary, a timestamp, or a short hex snapshot id
    before it can reach the dashboard. A missing or malformed receipt for an
    enabled copy is ``"unknown"`` and stale: absence is not an off-node copy.
    """
    if not expected:
        return _DISABLED_OFFSITE_COPY
    if backup_dir is None:
        return _unknown_offsite_copy(_OFFSITE_COPY_ABSENT_DETAIL)

    try:
        raw = (backup_dir / OFFSITE_COPY_RECEIPT_FILENAME).read_text(encoding="utf-8")
    except FileNotFoundError:
        return _unknown_offsite_copy(_OFFSITE_COPY_ABSENT_DETAIL)
    except OSError:
        return _unknown_offsite_copy(_OFFSITE_COPY_UNREADABLE_DETAIL)

    try:
        payload = json.loads(raw)
    except ValueError:
        return _unknown_offsite_copy(_OFFSITE_COPY_UNREADABLE_DETAIL)
    if not isinstance(payload, dict) or payload.get("result") not in ("success", "failed"):
        return _unknown_offsite_copy(_OFFSITE_COPY_UNREADABLE_DETAIL)

    reason = payload.get("reason")
    if not isinstance(reason, str):
        reason = None
    elif reason not in _OFFSITE_COPY_REASONS:
        reason = _BACKUP_RUN_REASON_UNRECOGNIZED

    snapshot_id = payload.get("snapshot_id")
    if not (
        isinstance(snapshot_id, str)
        and 0 < len(snapshot_id) <= 64
        and set(snapshot_id) <= _SNAPSHOT_ID_CHARS
    ):
        snapshot_id = None

    finished_at = _parse_sentinel_timestamp(payload.get("finished_at"))
    stale = (
        finished_at is None
        or (datetime.now(UTC) - datetime.fromisoformat(finished_at)).total_seconds() / 3600
        > BACKUP_STALE_THRESHOLD_HOURS
    )

    return OffsiteCopyFacts(
        result=payload["result"],
        finished_at=finished_at,
        reason=reason,
        snapshot_id=snapshot_id,
        stale=stale,
    )


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


def _unreachable_backup_facts(*, offsite_copy_expected: bool) -> BackupFacts:
    return BackupFacts(
        last_backup_at=None,
        last_backup_size_bytes=None,
        backup_source_reachable=False,
        backup_history=[],
        last_backup_status="missing",
        backup_stale=False,
        last_run=_UNKNOWN_BACKUP_RUN,
        restore_drill=_PENDING_RESTORE_DRILL,
        offsite_copy=_read_offsite_copy_facts(None, expected=offsite_copy_expected),
    )


def read_backup_facts_from_dir(
    backup_dir: Path | None, *, offsite_copy_expected: bool = False
) -> BackupFacts:
    """Read DB-free artifact and run-receipt facts from ``backup_dir``.

    Files match ``butlers_*.sql.gz`` and are sorted by mtime descending.  The
    returned restore-drill value is always the honest ``"pending"`` default;
    the system API overlays its DB-backed ledger result separately.  ``None``
    and inaccessible directories yield the existing degraded payload rather
    than raising.  ``offsite_copy_expected`` says whether this deployment runs
    the off-node copy job; without it the copy is reported ``"disabled"``.
    """
    if backup_dir is None or not backup_dir.is_dir():
        return _unreachable_backup_facts(offsite_copy_expected=offsite_copy_expected)

    try:
        candidates = list(backup_dir.glob("butlers_*.sql.gz"))
    except OSError as exc:
        logger.warning("Cannot read backup directory %s: %s", backup_dir, exc)
        return _unreachable_backup_facts(offsite_copy_expected=offsite_copy_expected)

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
            offsite_copy=_read_offsite_copy_facts(backup_dir, expected=offsite_copy_expected),
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
        offsite_copy=_read_offsite_copy_facts(backup_dir, expected=offsite_copy_expected),
    )
