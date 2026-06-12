#!/usr/bin/env python3
"""Backfill entity_id on existing point_events for owner-only adapters.

Background (bu-kihe8)
---------------------
Migration ``chronicler_015`` added the ``entity_id`` column to
``chronicler.point_events``.  Point events projected *before* the adapter
changes (meals, google_health steps, google_health heart_rate) have
``entity_id = NULL``.

This script stamps existing NULL rows with the owner entity_id resolved from
``public.contacts WHERE 'owner' = ANY(roles)``.

Resolution is idempotent: rows that already have a non-NULL entity_id are
skipped.  The script processes rows in batches so memory usage is bounded
regardless of how many NULL rows exist.

Source names covered
--------------------
  1. health.meals              (MealsAdapter)
  2. health.steps              (GoogleHealthStepsAdapter)
  3. health.heart_rate         (GoogleHealthHeartRateAdapter)

Usage
-----
Dry run (reports counts, makes no changes)::

    uv run python scripts/backfill_point_event_entity_id.py --dry-run

Apply::

    uv run python scripts/backfill_point_event_entity_id.py

Target a single source name::

    uv run python scripts/backfill_point_event_entity_id.py \\
        --source-name health.meals

Environment
-----------
BUTLERS_DATABASE_URL
    Required asyncpg DSN, e.g.
    ``postgresql://user:pass@localhost:5432/butlers``

BUTLERS_CHRONICLER_DSN
    Optional separate DSN for the chronicler DB when it differs from
    the main database. Falls back to BUTLERS_DATABASE_URL.

Issue: bu-kihe8
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from uuid import UUID

import asyncpg

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Source names whose point_events should receive owner entity_id.
_SOURCE_NAMES: tuple[str, ...] = (
    "health.meals",
    "health.steps",
    "health.heart_rate",
)

# Chunk size for UPDATE batches (keeps transactions short).
_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Owner resolution
# ---------------------------------------------------------------------------


async def resolve_owner_entity_id(pool: asyncpg.Pool) -> UUID | None:
    """Return the entity_id of the owner entity, or None.

    Queries ``public.entities WHERE 'owner' = ANY(roles)``, mirroring the
    canonical logic in ``src/butlers/owner_bootstrap.py``.  Guards on whether
    the ``public.entities`` table and its ``roles`` column exist before
    querying, so the function is safe to call against any migration state.

    Returns None gracefully on any failure (missing table/column, no row,
    NULL id).
    """
    try:
        async with pool.acquire() as conn:
            entities_table_exists = await conn.fetchval(
                "SELECT to_regclass('public.entities') IS NOT NULL"
            )
            if not entities_table_exists:
                logger.debug("resolve_owner_entity_id: public.entities does not exist")
                return None

            roles_column_exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'entities'
                      AND column_name = 'roles'
                )
                """
            )
            if not roles_column_exists:
                logger.debug("resolve_owner_entity_id: public.entities.roles column absent")
                return None

            row = await conn.fetchrow(
                """
                SELECT id
                FROM public.entities
                WHERE 'owner' = ANY(roles)
                LIMIT 1
                """
            )
    except asyncpg.PostgresError:
        logger.debug("resolve_owner_entity_id: query failed", exc_info=True)
        return None

    if row is None:
        logger.debug("resolve_owner_entity_id: no owner entity found")
        return None

    raw = row["id"]
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return raw
    if isinstance(raw, str):
        try:
            return UUID(raw)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Backfill core
# ---------------------------------------------------------------------------


async def backfill_source(
    chronicler_pool: asyncpg.Pool,
    *,
    source_name: str,
    owner_entity_id: UUID,
    dry_run: bool,
) -> dict[str, int]:
    """Backfill entity_id for one source_name's point_events.

    Returns a summary dict:
      - ``found``   — point_events with entity_id IS NULL
      - ``updated`` — rows actually updated (0 in dry-run)

    In dry-run mode, uses ``COUNT(*)`` so no IDs are loaded into memory.
    In apply mode, fetches and processes rows in batches of ``_BATCH_SIZE``
    so memory usage is bounded regardless of how many NULL rows exist.
    """
    if dry_run:
        count_row = await chronicler_pool.fetchrow(
            """
            SELECT COUNT(*) AS count
            FROM chronicler.point_events
            WHERE source_name = $1
              AND entity_id IS NULL
              AND tombstone_at IS NULL
            """,
            source_name,
        )
        found = count_row["count"] if count_row else 0
        if found == 0:
            logger.info(
                "source_name=%r — no NULL entity_id point_events found; nothing to do.",
                source_name,
            )
            return {"found": 0, "updated": 0}
        logger.info(
            "source_name=%r — found %d point_event(s) with entity_id IS NULL.",
            source_name,
            found,
        )
        logger.info(
            "source_name=%r — dry run; skipping %d UPDATE(s).",
            source_name,
            found,
        )
        return {"found": found, "updated": 0}

    found_count = 0
    updated_count = 0
    batch_num = 0

    while True:
        rows = await chronicler_pool.fetch(
            """
            SELECT id
            FROM chronicler.point_events
            WHERE source_name = $1
              AND entity_id IS NULL
              AND tombstone_at IS NULL
            LIMIT $2
            """,
            source_name,
            _BATCH_SIZE,
        )
        if not rows:
            break

        batch_num += 1
        chunk: list[UUID] = [r["id"] for r in rows]
        found_count += len(chunk)

        # Update point_events.entity_id.
        result = await chronicler_pool.execute(
            """
            UPDATE chronicler.point_events
            SET entity_id = $1,
                updated_at = now()
            WHERE id = ANY($2::uuid[])
              AND entity_id IS NULL
            """,
            owner_entity_id,
            chunk,
        )
        n = int(result.split()[-1])
        updated_count += n
        logger.info(
            "  source_name=%r — updated %d point_event(s) entity_id (batch %d).",
            source_name,
            n,
            batch_num,
        )

    if found_count == 0:
        logger.info(
            "source_name=%r — no NULL entity_id point_events found; nothing to do.",
            source_name,
        )

    return {"found": found_count, "updated": updated_count}


async def run_backfill(
    pool: asyncpg.Pool,
    chronicler_pool: asyncpg.Pool,
    *,
    source_names: tuple[str, ...],
    dry_run: bool,
) -> bool:
    """Resolve owner entity_id and backfill all target source names.

    Returns True on success, False if the owner entity_id could not be resolved.
    """
    owner_entity_id = await resolve_owner_entity_id(pool)
    if owner_entity_id is None:
        logger.error(
            "Cannot resolve owner entity_id from public.entities. "
            "Ensure the owner entity exists with roles=['owner']."
        )
        return False

    logger.info("Owner entity_id resolved: %s", owner_entity_id)
    logger.info("Mode: %s", "DRY RUN" if dry_run else "APPLY")

    total_found = 0
    total_updated = 0

    for sn in source_names:
        summary = await backfill_source(
            chronicler_pool,
            source_name=sn,
            owner_entity_id=owner_entity_id,
            dry_run=dry_run,
        )
        total_found += summary["found"]
        total_updated += summary["updated"]

    print()
    print(f"Backfill complete (mode={'DRY RUN' if dry_run else 'APPLY'}):")
    print(f"  Source names processed: {len(source_names)}")
    print(f"  Total NULL point_events found:  {total_found}")
    if not dry_run:
        print(f"  Total point_events updated:     {total_updated}")
    else:
        print("  (dry-run: no rows updated)")

    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill entity_id on point_events for owner-only Chronicler adapters (bu-kihe8)"
        )
    )
    parser.add_argument(
        "--source-name",
        default=None,
        choices=list(_SOURCE_NAMES),
        help="Restrict backfill to one source name (default: all point-event owner-only sources)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Report what would be updated without writing any changes",
    )
    args = parser.parse_args(argv)

    db_url = os.environ.get("BUTLERS_DATABASE_URL")
    if not db_url:
        print("ERROR: BUTLERS_DATABASE_URL environment variable is not set", file=sys.stderr)
        return 1

    chronicler_dsn = os.environ.get("BUTLERS_CHRONICLER_DSN", db_url)

    target_sources: tuple[str, ...] = (args.source_name,) if args.source_name else _SOURCE_NAMES

    try:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
        chronicler_pool = (
            await asyncpg.create_pool(chronicler_dsn, min_size=1, max_size=5)
            if chronicler_dsn != db_url
            else pool
        )
    except Exception as exc:
        print(f"ERROR: Failed to connect to database: {exc}", file=sys.stderr)
        return 1

    success = False
    try:
        success = await run_backfill(
            pool,
            chronicler_pool,
            source_names=target_sources,
            dry_run=args.dry_run,
        )
    finally:
        await pool.close()
        if chronicler_pool is not pool:
            await chronicler_pool.close()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
