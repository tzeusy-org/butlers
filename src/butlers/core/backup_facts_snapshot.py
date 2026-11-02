"""Backup facts snapshot: hand the QA patrol backup recency without the dumps.

bu-deqpzd: ``InfraStateSource`` (QA butler, hosted in butlers-up) raises
``BackupStale`` / ``BackupRunFailed`` from the facts
:func:`butlers.core.backup_facts.read_backup_facts_from_dir` derives. Only
dashboard-api mounts the backups volume, and that is deliberate: the volume
holds full all-schema ``pg_dump`` artifacts, and every butler's runtime CLI in
butlers-up has shell and file access. Mounting it there would let one
prompt-injected session read every schema, defeating per-schema isolation.

So the facts cross the boundary instead of the files:

- Writer (dashboard-api, the only process with the mount;
  ``butlers.jobs.backup_facts_snapshot``): every ``interval_s`` reads the
  directory and upserts a content-free summary into ``public.state`` under
  :data:`STATE_KEY`, through the shared pool.
- Reader (:func:`load_backup_facts_snapshot`): ``InfraStateSource`` reads the
  row through its own pool. A missing, malformed, future-dated, or stale row
  is itself a finding; the patrol never falls back to "not configured" when it
  cannot see the facts.

Forgery: butler DB roles DO hold DML on ``public`` (``scripts/init-db.sql``),
so the role grants are not what protects this row. A runtime session cannot
forge it because it holds no DB credential (``butlers.core.child_env`` strips
``POSTGRES_*`` and ``DATABASE_URL`` from every runtime child) and no MCP tool
writes ``public.state`` (the core ``state_*`` tools write the butler's own
schema-local ``state`` table). Daemon code itself is trusted.

The summary holds timestamps, booleans, and the fixed run-reason vocabulary
only, never file names, sizes, or dump content.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from butlers.core.backup_facts import read_backup_facts_from_dir
from butlers.core.liveness import CLOCK_SKEW_TOLERANCE
from butlers.core.state import decode_jsonb

logger = logging.getLogger(__name__)

#: ``public.state`` key the writer upserts and the QA patrol reads.
STATE_KEY = "infra.backup_facts"

#: Missed writer intervals tolerated before the snapshot counts as stale.
STALE_MULTIPLIER = 3

_UPSERT_SQL = """
    INSERT INTO public.state (key, value, updated_at, version)
    VALUES ($1, $2, now(), 1)
    ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value,
            updated_at = now(),
            version = public.state.version + 1
"""


@dataclass(frozen=True)
class BackupFactsSnapshot:
    """The content-free backup summary the dashboard last published."""

    observed_at: datetime
    interval_s: float
    configured: bool
    reachable: bool
    last_backup_at: str | None
    backup_stale: bool
    last_run_result: str
    last_run_reason: str | None
    last_run_finished_at: str | None

    def is_stale(self, now: datetime) -> bool:
        """True once the writer has missed :data:`STALE_MULTIPLIER` intervals.

        A future ``observed_at`` beyond :data:`CLOCK_SKEW_TOLERANCE` is also
        stale: otherwise one bad clock would read as fresh forever.
        """
        age = now - self.observed_at
        if age < -CLOCK_SKEW_TOLERANCE:
            return True
        return age.total_seconds() > self.interval_s * STALE_MULTIPLIER


def build_snapshot_value(backup_dir_raw: str, *, interval_s: float) -> dict[str, Any]:
    """Read the backup directory into the stored summary (unset dir = unconfigured)."""
    observed_at = datetime.now(UTC).isoformat()
    if not backup_dir_raw:
        return {"observed_at": observed_at, "interval_s": interval_s, "configured": False}
    facts = read_backup_facts_from_dir(Path(backup_dir_raw))
    return {
        "observed_at": observed_at,
        "interval_s": interval_s,
        "configured": True,
        "reachable": facts.backup_source_reachable,
        "last_backup_at": facts.last_backup_at,
        "backup_stale": facts.backup_stale,
        "last_run": {
            "result": facts.last_run.result,
            "reason": facts.last_run.reason,
            "finished_at": facts.last_run.finished_at,
        },
    }


async def publish_backup_facts_snapshot(
    pool: asyncpg.Pool, backup_dir_raw: str, *, interval_s: float
) -> dict[str, Any]:
    """Upsert this tick's summary. Directory reads run off the event loop."""
    value = await asyncio.to_thread(build_snapshot_value, backup_dir_raw, interval_s=interval_s)
    await pool.execute(_UPSERT_SQL, STATE_KEY, value)
    return value


async def load_backup_facts_snapshot(pool: asyncpg.Pool) -> BackupFactsSnapshot | None:
    """Return the published summary, or ``None`` when absent or malformed.

    ``None`` means "the patrol cannot see backup facts"; callers treat it as a
    finding, never as "backups not configured".
    """
    raw = await pool.fetchval("SELECT value FROM public.state WHERE key = $1", STATE_KEY)
    if raw is None:
        return None
    value = decode_jsonb(raw)
    try:
        observed_at = datetime.fromisoformat(value["observed_at"])
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        interval_s = float(value["interval_s"])
        if not math.isfinite(interval_s) or interval_s <= 0:
            return None
        configured = value["configured"] is True
        last_run = value.get("last_run") or {}
        # Timestamps InfraStateSource parses must parse here, so a bad row is
        # "unavailable" rather than an exception out of the patrol.
        for stamp in (value.get("last_backup_at"), last_run.get("finished_at")):
            if stamp is not None:
                datetime.fromisoformat(stamp)
        return BackupFactsSnapshot(
            observed_at=observed_at,
            interval_s=interval_s,
            configured=configured,
            reachable=value.get("reachable") is True,
            last_backup_at=value.get("last_backup_at"),
            backup_stale=value.get("backup_stale") is True,
            last_run_result=last_run.get("result") or "unknown",
            last_run_reason=last_run.get("reason"),
            last_run_finished_at=last_run.get("finished_at"),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        logger.warning("backup facts snapshot: stored value is malformed")
        return None
