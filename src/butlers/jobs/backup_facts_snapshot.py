"""Dashboard-api loop publishing the backup facts snapshot (bu-deqpzd).

See :mod:`butlers.core.backup_facts_snapshot` for why the QA patrol reads a
``public.state`` summary instead of mounting the backups volume.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os

from butlers.api.db import DatabaseManager
from butlers.core.backup_facts import BACKUP_DIR_ENV
from butlers.core.backup_facts_snapshot import publish_backup_facts_snapshot
from butlers.core.job_health import record_tick

logger = logging.getLogger(__name__)

#: Writer cadence. Matches the QA patrol's 10-minute interval.
DEFAULT_SNAPSHOT_INTERVAL_S = 600.0


async def run_backup_facts_snapshot_loop(
    db: DatabaseManager, *, interval_s: float = DEFAULT_SNAPSHOT_INTERVAL_S
) -> None:
    """Publish the summary every ``interval_s`` until cancelled.

    Sleeps first, mirroring the other dashboard-api loops. A failed tick is
    recorded and swallowed; the reader's staleness check reports a writer that
    keeps failing.
    """
    if not math.isfinite(interval_s) or interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        await asyncio.sleep(interval_s)
        try:
            pool = db.credential_shared_pool()
            value = await publish_backup_facts_snapshot(
                pool, os.environ.get(BACKUP_DIR_ENV, "").strip(), interval_s=interval_s
            )
            await record_tick()
            logger.debug("backup_facts_snapshot: configured=%s", value["configured"])
        except asyncio.CancelledError:
            raise
        except Exception:
            await record_tick(failed=True)
            logger.warning("backup_facts_snapshot: tick failed", exc_info=True)
