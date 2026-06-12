#!/usr/bin/env python3
"""Backfill observed_at on existing relationship.entity_facts rows.

Background (bu-mxxjy)
---------------------
Migration ``rel_021`` added the nullable ``observed_at`` column to
``relationship.entity_facts``. Rows asserted *before* that migration have
``observed_at = NULL``. Per ``specs/relationship-facts/spec.md`` the historical
value is ``COALESCE(last_seen, created_at)`` — the best available estimate of
when the fact was actually observed.

This script stamps existing NULL rows in bounded batches so production writes
are not starved. It is idempotent: a second run finds no NULL rows and is a
no-op. The migration deliberately does NOT do this backfill in its DDL (no
table rewrite); this operator-run script is the separate, restartable path.

Usage
-----
Dry run (reports counts, makes no changes)::

    uv run python scripts/backfill_entity_fact_observed_at.py --dry-run

Apply::

    uv run python scripts/backfill_entity_fact_observed_at.py

Tune the batch size (default 500)::

    uv run python scripts/backfill_entity_fact_observed_at.py --batch-size 1000

Environment
-----------
BUTLERS_DATABASE_URL
    Required asyncpg DSN, e.g.
    ``postgresql://user:pass@localhost:5432/butlers``

CAUTION: confirm the target DB before applying. The live system is
``butlers-db-dev`` (``.env.dev``); ``butlers-db`` (``.env.prod``) is the empty
one (repo memory: butlers-db-host-topology).

Issue: bu-mxxjy
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# Default chunk size for UPDATE batches (keeps transactions short so concurrent
# production writes are not starved). Overridable via --batch-size.
_DEFAULT_BATCH_SIZE = 500

# Bounded, idempotent backfill. We select a batch of NULL-observed_at row ids,
# then UPDATE only those ids (re-checking observed_at IS NULL so a concurrent
# writer that already stamped a row is not clobbered). The loop terminates when
# no NULL rows remain.
_SELECT_BATCH_SQL = """
    SELECT id
    FROM relationship.entity_facts
    WHERE observed_at IS NULL
    LIMIT $1
"""

_UPDATE_BATCH_SQL = """
    UPDATE relationship.entity_facts
    SET observed_at = COALESCE(last_seen, created_at)
    WHERE id = ANY($1::uuid[])
      AND observed_at IS NULL
"""

_COUNT_NULL_SQL = """
    SELECT COUNT(*) AS count
    FROM relationship.entity_facts
    WHERE observed_at IS NULL
"""


async def count_remaining(pool: asyncpg.Pool) -> int:
    """Return the number of rows still needing a backfill (observed_at IS NULL)."""
    row = await pool.fetchrow(_COUNT_NULL_SQL)
    return int(row["count"]) if row else 0


async def backfill_observed_at(
    pool: asyncpg.Pool,
    *,
    batch_size: int,
    dry_run: bool,
) -> dict[str, int]:
    """Backfill observed_at := COALESCE(last_seen, created_at) for NULL rows.

    Processes rows in batches of ``batch_size`` so memory and transaction size
    are bounded regardless of table size. Idempotent: a run against a fully
    backfilled table updates nothing.

    Returns a summary dict:
      - ``found``   — rows with observed_at IS NULL at start
      - ``updated`` — rows actually updated (0 in dry-run)
      - ``batches`` — number of UPDATE batches executed (0 in dry-run)
    """
    found = await count_remaining(pool)
    if found == 0:
        logger.info("No rows with observed_at IS NULL; nothing to do.")
        return {"found": 0, "updated": 0, "batches": 0}

    logger.info("Found %d entity_facts row(s) with observed_at IS NULL.", found)

    if dry_run:
        logger.info("Dry run; skipping %d UPDATE(s).", found)
        return {"found": found, "updated": 0, "batches": 0}

    updated = 0
    batches = 0
    while True:
        rows = await pool.fetch(_SELECT_BATCH_SQL, batch_size)
        if not rows:
            break

        batches += 1
        ids = [r["id"] for r in rows]
        result = await pool.execute(_UPDATE_BATCH_SQL, ids)
        n = int(result.split()[-1])
        updated += n
        logger.info("  updated %d row(s) (batch %d).", n, batches)

        # Safety valve: if a batch selected rows but updated none (e.g. all were
        # concurrently stamped), stop to avoid an infinite loop.
        if n == 0:
            logger.info("  batch %d updated 0 rows; stopping.", batches)
            break

    logger.info("Backfill complete: updated=%d across %d batch(es).", updated, batches)
    return {"found": found, "updated": updated, "batches": batches}


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill observed_at := COALESCE(last_seen, created_at) on "
            "relationship.entity_facts (bu-mxxjy)"
        )
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=_DEFAULT_BATCH_SIZE,
        help=f"Rows per UPDATE batch (default: {_DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Report what would be updated without writing any changes",
    )
    args = parser.parse_args(argv)

    if args.batch_size <= 0:
        print("ERROR: --batch-size must be a positive integer", file=sys.stderr)
        return 1

    db_url = os.environ.get("BUTLERS_DATABASE_URL")
    if not db_url:
        print("ERROR: BUTLERS_DATABASE_URL environment variable is not set", file=sys.stderr)
        return 1

    try:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
    except Exception as exc:
        print(f"ERROR: Failed to connect to database: {exc}", file=sys.stderr)
        return 1

    try:
        summary = await backfill_observed_at(
            pool,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
    finally:
        await pool.close()

    print()
    print(f"Backfill complete (mode={'DRY RUN' if args.dry_run else 'APPLY'}):")
    print(f"  Rows with observed_at IS NULL found: {summary['found']}")
    if args.dry_run:
        print("  (dry-run: no rows updated)")
    else:
        print(f"  Rows updated:  {summary['updated']}")
        print(f"  Batches:       {summary['batches']}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
