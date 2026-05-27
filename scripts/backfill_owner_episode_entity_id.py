#!/usr/bin/env python3
"""Backfill entity_id and episode_entities for owner-only Chronicler adapters.

Background (bu-4c1ks)
---------------------
The eight owner-only adapters (focus, sessions, spotify, steam, meals,
owntracks, reading, google_health) were updated to resolve the owner
entity_id at projection time and write it to:
  1. ``chronicler.episodes.entity_id``
  2. ``chronicler.episode_entities`` (role='owner')

Episodes projected *before* the adapter change have ``entity_id = NULL``
and no ``episode_entities`` row for the owner.

This script iterates those NULL rows for the ten source_name values
covered by bu-4c1ks, resolves the owner entity_id from
``public.contacts WHERE 'owner' = ANY(roles)``, and:
  - UPDATEs ``episodes.entity_id``
  - INSERTs a row into ``episode_entities`` with ``role='owner'``
    when one does not already exist.

Resolution is idempotent: rows that already have a non-NULL entity_id
are skipped. episode_entities rows are inserted with ON CONFLICT DO NOTHING.

Note: meals, steps, and heart_rate adapters produce ``point_events``, not
``episodes``. They are listed as source names here only so the backfill
can be run as a single command. Episodes with those source names that
somehow appear in ``chronicler.episodes`` are covered, but in practice
those adapters write to ``point_events`` only.

Source names covered
--------------------
  1. focus.inferred           (FocusInferredAdapter)

     NOTE: the FocusInferredAdapter source name constant is
     ``chronicler.focus_inferred`` not ``focus.inferred``.
     Check the SOURCE_NAME constant in focus.py before running.

  2. core.sessions             (CoreSessionsAdapter)
  3. spotify.session_summary   (SpotifySessionAdapter)
  4. steam.play_history        (SteamPlayAdapter)
  5. health.meals              (MealsAdapter — point events only, no episodes)
  6. owntracks.points          (OwnTracksPointAdapter)
  7. chronicler.reading_inferred (ReadingInferredAdapter)
  8. google_health.measurements (GoogleHealthSleepAdapter + GoogleHealthWorkoutAdapter)
  9. health.steps              (GoogleHealthStepsAdapter — point events only)
 10. health.heart_rate         (GoogleHealthHeartRateAdapter — point events only)

Usage
-----
Dry run (reports counts, makes no changes)::

    uv run python scripts/backfill_owner_episode_entity_id.py --dry-run

Apply::

    uv run python scripts/backfill_owner_episode_entity_id.py

Target a single source name::

    uv run python scripts/backfill_owner_episode_entity_id.py \\
        --source-name google_health.measurements

Environment
-----------
BUTLERS_DATABASE_URL
    Required asyncpg DSN, e.g.
    ``postgresql://user:pass@localhost:5432/butlers``

BUTLERS_CHRONICLER_DSN
    Optional separate DSN for the chronicler DB when it differs from
    the main database. Falls back to BUTLERS_DATABASE_URL.

Issue: bu-4c1ks
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

# All source_name values covered by bu-4c1ks owner-only adapters.
# Verify against the SOURCE_NAME constant in each adapter module before use.
_SOURCE_NAMES: tuple[str, ...] = (
    "chronicler.focus_inferred",
    "core.sessions",
    "spotify.session_summary",
    "steam.play_history",
    "health.meals",
    "owntracks.points",
    "chronicler.reading_inferred",
    "google_health.measurements",
    "health.steps",
    "health.heart_rate",
)

# Chunk size for UPDATE batches (keeps transactions short).
_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Owner resolution
# ---------------------------------------------------------------------------


async def resolve_owner_entity_id(pool: asyncpg.Pool) -> UUID | None:
    """Return the entity_id of the owner contact, or None.

    Queries ``public.contacts WHERE 'owner' = ANY(roles)`` directly.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT entity_id
            FROM public.contacts
            WHERE 'owner' = ANY(roles)
            LIMIT 1
            """
        )
    except asyncpg.PostgresError:
        logger.debug("resolve_owner_entity_id: query failed", exc_info=True)
        return None

    if row is None:
        logger.debug("resolve_owner_entity_id: no owner contact found")
        return None

    raw = row["entity_id"]
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
    pool: asyncpg.Pool,
    chronicler_pool: asyncpg.Pool,
    *,
    source_name: str,
    owner_entity_id: UUID,
    dry_run: bool,
) -> dict[str, int]:
    """Backfill entity_id and episode_entities for one source_name.

    Returns a summary dict:
      - ``found``   — episodes with entity_id IS NULL
      - ``updated`` — episodes actually updated (0 in dry-run)
      - ``ee_inserted`` — episode_entities rows inserted (0 in dry-run)
    """
    rows = await chronicler_pool.fetch(
        """
        SELECT id
        FROM chronicler.episodes
        WHERE source_name = $1
          AND entity_id IS NULL
          AND tombstone_at IS NULL
        ORDER BY created_at ASC
        """,
        source_name,
    )
    ep_ids: list[UUID] = [r["id"] for r in rows]
    found = len(ep_ids)

    if found == 0:
        logger.info(
            "source_name=%r — no NULL entity_id episodes found; nothing to do.",
            source_name,
        )
        return {"found": 0, "updated": 0, "ee_inserted": 0}

    logger.info(
        "source_name=%r — found %d episode(s) with entity_id IS NULL.",
        source_name,
        found,
    )

    if dry_run:
        logger.info(
            "source_name=%r — dry run; skipping %d UPDATE(s).",
            source_name,
            found,
        )
        return {"found": found, "updated": 0, "ee_inserted": 0}

    updated_count = 0
    ee_inserted_count = 0

    for i in range(0, len(ep_ids), _BATCH_SIZE):
        chunk = ep_ids[i : i + _BATCH_SIZE]

        # Update episodes.entity_id.
        result = await chronicler_pool.execute(
            """
            UPDATE chronicler.episodes
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
            "  source_name=%r — updated %d episode(s) entity_id (batch %d/%d).",
            source_name,
            n,
            i // _BATCH_SIZE + 1,
            (len(ep_ids) + _BATCH_SIZE - 1) // _BATCH_SIZE,
        )

        # Insert episode_entities rows for each episode (owner role).
        # ON CONFLICT DO NOTHING makes this idempotent.
        ee_rows = [(ep_id, owner_entity_id, "owner") for ep_id in chunk]
        await chronicler_pool.executemany(
            """
            INSERT INTO chronicler.episode_entities (episode_id, entity_id, role)
            VALUES ($1, $2, $3)
            ON CONFLICT (episode_id, entity_id) DO NOTHING
            """,
            ee_rows,
        )
        ee_inserted_count += len(ee_rows)
        logger.info(
            "  source_name=%r — upserted %d episode_entities row(s).",
            source_name,
            len(ee_rows),
        )

    return {"found": found, "updated": updated_count, "ee_inserted": ee_inserted_count}


async def run_backfill(
    pool: asyncpg.Pool,
    chronicler_pool: asyncpg.Pool,
    *,
    source_names: tuple[str, ...],
    dry_run: bool,
) -> None:
    """Resolve owner entity_id and backfill all target source names."""
    owner_entity_id = await resolve_owner_entity_id(pool)
    if owner_entity_id is None:
        logger.error(
            "Cannot resolve owner entity_id from public.contacts. "
            "Ensure the owner contact exists and has an entity_id set."
        )
        return

    logger.info("Owner entity_id resolved: %s", owner_entity_id)
    logger.info("Mode: %s", "DRY RUN" if dry_run else "APPLY")

    total_found = 0
    total_updated = 0
    total_ee = 0

    for sn in source_names:
        summary = await backfill_source(
            pool,
            chronicler_pool,
            source_name=sn,
            owner_entity_id=owner_entity_id,
            dry_run=dry_run,
        )
        total_found += summary["found"]
        total_updated += summary["updated"]
        total_ee += summary["ee_inserted"]

    print()
    print(f"Backfill complete (mode={'DRY RUN' if dry_run else 'APPLY'}):")
    print(f"  Source names processed: {len(source_names)}")
    print(f"  Total NULL episodes found:  {total_found}")
    if not dry_run:
        print(f"  Total episodes updated:     {total_updated}")
        print(f"  Total episode_entities rows: {total_ee}")
    else:
        print("  (dry-run: no rows updated)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill entity_id and episode_entities for owner-only Chronicler adapters "
            "(bu-4c1ks)"
        )
    )
    parser.add_argument(
        "--source-name",
        default=None,
        choices=list(_SOURCE_NAMES),
        help="Restrict backfill to one source name (default: all owner-only sources)",
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

    target_sources = (args.source_name,) if args.source_name else _SOURCE_NAMES

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

    try:
        await run_backfill(
            pool,
            chronicler_pool,
            source_names=target_sources,
            dry_run=args.dry_run,
        )
    finally:
        await pool.close()
        if chronicler_pool is not pool:
            await chronicler_pool.close()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
