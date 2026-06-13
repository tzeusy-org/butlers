#!/usr/bin/env python3
"""Backfill entity_id and episode_entities for home_assistant.history presence episodes.

Background (bu-v7hen)
---------------------
The Home Assistant history adapter was updated to resolve per-person entity_id at
projection time via ``connectors.home_assistant_persons``.  Episodes projected *before*
the adapter change have ``entity_id = NULL`` and no ``episode_entities`` row.

This script iterates those NULL rows for ``source_name = 'home_assistant.history'``,
resolves the entity_id for each episode from the payload's ``entity_id`` field
(the HA entity ID, e.g. ``person.alice``) via:

  ``connectors.home_assistant_persons.ha_entity_id``
  → ``connectors.home_assistant_persons.contact_id``
  → ``public.contacts.entity_id``

And then:
  - UPDATEs ``chronicler.episodes.entity_id``
  - INSERTs a row into ``chronicler.episode_entities`` with ``role='owner'``
    when one does not already exist.

Resolution is idempotent: rows that already have a non-NULL entity_id are skipped.
episode_entities rows are inserted with ON CONFLICT DO NOTHING.

Prerequisites
-------------
1. Run Alembic migration core_116 to create ``connectors.home_assistant_persons``.
2. Bootstrap person-entity mappings in that table.  For a single-person household
   where the HA person maps to the owner contact:

       INSERT INTO connectors.home_assistant_persons (ha_entity_id, contact_id)
       SELECT 'person.alice', id FROM public.contacts WHERE 'owner' = ANY(roles)
       ON CONFLICT DO NOTHING;

   For a multi-person household:

       INSERT INTO connectors.home_assistant_persons (ha_entity_id, contact_id)
       VALUES
           ('person.alice', '<alice_contact_uuid>'),
           ('person.bob',   '<bob_contact_uuid>')
       ON CONFLICT (ha_entity_id) DO UPDATE SET contact_id = EXCLUDED.contact_id;

   Unmapped persons are skipped (entity_id remains NULL) — that is a correct outcome.

Usage
-----
Dry run (reports counts, makes no changes)::

    uv run python scripts/backfill_ha_presence_entity_id.py --dry-run

Apply::

    uv run python scripts/backfill_ha_presence_entity_id.py

Environment
-----------
BUTLERS_DATABASE_URL
    Required asyncpg DSN for the main database (connectors schema and public schema).
    E.g. ``postgresql://user:pass@localhost:5432/butlers``

BUTLERS_CHRONICLER_DSN
    Optional separate DSN for the chronicler DB when it differs from the main
    database.  Falls back to BUTLERS_DATABASE_URL when unset.

Alternative: watermark reset
----------------------------
Instead of running this script, reset the adapter's watermark in
``chronicler.projection_checkpoints`` to ``NULL`` and let the next scheduled
run re-project all rows (they will receive entity_id at projection time):

    UPDATE chronicler.projection_checkpoints
    SET watermark_ts = NULL,
        watermark_id = NULL,
        updated_at   = now()
    WHERE source_name = 'home_assistant.history';

Issue: bu-v7hen
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

_SOURCE_NAME = "home_assistant.history"
_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------


async def load_ha_person_mapping(pool: asyncpg.Pool) -> dict[str, UUID]:
    """Load all HA person → entity_id mappings from connectors.home_assistant_persons.

    Returns a dict mapping ha_entity_id → entity_id (UUID).  Only entries whose
    contact has a non-NULL entity_id are included.

    Returns an empty dict if the table is absent or a query error occurs.
    """
    try:
        exists = await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'connectors'
                  AND table_name = 'home_assistant_persons'
            )
            """
        )
        if not exists:
            logger.error(
                "connectors.home_assistant_persons does not exist. "
                "Run migration core_116 before running this backfill."
            )
            return {}

        rows = await pool.fetch(
            """
            SELECT hap.ha_entity_id, c.entity_id
            FROM connectors.home_assistant_persons AS hap
            INNER JOIN public.contacts AS c ON c.id = hap.contact_id
            WHERE c.entity_id IS NOT NULL
            """
        )
    except asyncpg.PostgresError:
        logger.exception("Failed loading ha_persons mapping")
        return {}

    result: dict[str, UUID] = {}
    for row in rows:
        ha_id: str = row["ha_entity_id"]
        raw = row["entity_id"]
        if raw is None:
            continue
        if isinstance(raw, UUID):
            result[ha_id] = raw
        elif isinstance(raw, str):
            try:
                result[ha_id] = UUID(raw)
            except ValueError:
                logger.debug("Skipping invalid UUID %r for ha_entity_id %r", raw, ha_id)
    return result


# ---------------------------------------------------------------------------
# Backfill core
# ---------------------------------------------------------------------------


async def backfill(
    chronicler_pool: asyncpg.Pool,
    *,
    ha_person_mapping: dict[str, UUID],
    dry_run: bool,
) -> dict[str, int]:
    """Backfill entity_id and episode_entities for home_assistant.history episodes.

    Processes episodes in batches of ``_BATCH_SIZE``.  Each episode's HA entity ID
    is extracted from ``payload->>'entity_id'`` and looked up in ``ha_person_mapping``.
    Episodes whose HA entity ID is not in the mapping are skipped (entity_id stays NULL).

    Stops early when an entire batch has no resolvable rows — since the mapping is
    loaded once before the loop, any rows that cannot be resolved in one batch will
    never be resolvable and continuing would loop forever.

    Returns a summary dict:
      - ``found``       — total episodes with entity_id IS NULL processed
      - ``updated``     — episodes updated with a non-NULL entity_id
      - ``skipped``     — episodes skipped (no mapping for their HA entity)
      - ``ee_upserted`` — episode_entities rows attempted (ON CONFLICT DO NOTHING)
    """
    if dry_run:
        count_row = await chronicler_pool.fetchrow(
            """
            SELECT COUNT(*) AS count
            FROM chronicler.episodes
            WHERE source_name = $1
              AND entity_id IS NULL
              AND tombstone_at IS NULL
            """,
            _SOURCE_NAME,
        )
        found = count_row["count"] if count_row else 0
        logger.info(
            "source_name=%r — found %d episode(s) with entity_id IS NULL.", _SOURCE_NAME, found
        )
        if found == 0:
            logger.info("Nothing to do.")
        else:
            logger.info("Dry run — skipping %d UPDATE(s).", found)
        return {"found": found, "updated": 0, "skipped": 0, "ee_upserted": 0}

    updated_count = 0
    skipped_count = 0
    ee_upserted_count = 0
    batch_num = 0

    while True:
        rows = await chronicler_pool.fetch(
            """
            SELECT id, payload->>'entity_id' AS ha_entity_id
            FROM chronicler.episodes
            WHERE source_name = $1
              AND entity_id IS NULL
              AND tombstone_at IS NULL
            ORDER BY created_at ASC
            LIMIT $2
            """,
            _SOURCE_NAME,
            _BATCH_SIZE,
        )
        if not rows:
            break

        batch_num += 1

        # Partition the batch into resolvable and unresolvable episodes.
        resolvable: list[tuple[UUID, UUID]] = []  # (episode_id, entity_id)
        for row in rows:
            ep_id: UUID = row["id"]
            ha_id: str | None = row["ha_entity_id"]
            if ha_id is None:
                skipped_count += 1
                continue
            entity_id = ha_person_mapping.get(ha_id)
            if entity_id is None:
                logger.debug(
                    "  episode %s — HA entity %r not in mapping; skipping",
                    ep_id,
                    ha_id,
                )
                skipped_count += 1
                continue
            resolvable.append((ep_id, entity_id))

        if not resolvable:
            logger.info(
                "  batch %d — no resolvable episodes in this batch; "
                "all remaining rows are unmapped — stopping.",
                batch_num,
            )
            break

        # Group updates by entity_id to keep UPDATE statements small and use ANY().
        by_entity: dict[UUID, list[UUID]] = {}
        for ep_id, entity_id in resolvable:
            by_entity.setdefault(entity_id, []).append(ep_id)

        for entity_id, ep_ids in by_entity.items():
            result = await chronicler_pool.execute(
                """
                UPDATE chronicler.episodes
                SET entity_id = $1,
                    updated_at = now()
                WHERE id = ANY($2::uuid[])
                  AND entity_id IS NULL
                """,
                entity_id,
                ep_ids,
            )
            n = int(result.split()[-1])
            updated_count += n
            logger.info(
                "  batch %d — updated %d episode(s) with entity_id=%s.",
                batch_num,
                n,
                entity_id,
            )

        # Insert episode_entities rows (ON CONFLICT DO NOTHING for idempotency).
        ee_rows = [(ep_id, entity_id, "owner") for ep_id, entity_id in resolvable]
        await chronicler_pool.executemany(
            """
            INSERT INTO chronicler.episode_entities (episode_id, entity_id, role)
            VALUES ($1, $2, $3)
            ON CONFLICT (episode_id, entity_id) DO NOTHING
            """,
            ee_rows,
        )
        ee_upserted_count += len(ee_rows)
        logger.info(
            "  batch %d — attempted %d episode_entities upsert(s) (ON CONFLICT DO NOTHING).",
            batch_num,
            len(ee_rows),
        )

    if batch_num == 0:
        logger.info(
            "source_name=%r — no NULL entity_id episodes found; nothing to do.", _SOURCE_NAME
        )

    return {
        "found": updated_count + skipped_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "ee_upserted": ee_upserted_count,
    }


async def run_backfill(
    pool: asyncpg.Pool,
    chronicler_pool: asyncpg.Pool,
    *,
    dry_run: bool,
) -> None:
    """Load mapping table and run the backfill."""
    ha_person_mapping = await load_ha_person_mapping(pool)
    if not ha_person_mapping:
        logger.warning(
            "No HA person → entity mappings found. "
            "Bootstrap connectors.home_assistant_persons before backfilling. "
            "Episodes with known HA entities will remain at entity_id=NULL."
        )

    logger.info(
        "Loaded %d HA person mapping(s): %s",
        len(ha_person_mapping),
        ", ".join(f"{k} → {v}" for k, v in ha_person_mapping.items()) or "(none)",
    )
    logger.info("Mode: %s", "DRY RUN" if dry_run else "APPLY")

    summary = await backfill(
        chronicler_pool,
        ha_person_mapping=ha_person_mapping,
        dry_run=dry_run,
    )

    print()
    print(f"Backfill complete (mode={'DRY RUN' if dry_run else 'APPLY'}):")
    print(f"  Source name: {_SOURCE_NAME}")
    print(f"  Total NULL episodes found:        {summary['found']}")
    if not dry_run:
        print(f"  Episodes updated (entity_id set): {summary['updated']}")
        print(f"  Episodes skipped (no mapping):    {summary['skipped']}")
        print(
            f"  episode_entities rows attempted:  {summary['ee_upserted']} "
            "(ON CONFLICT DO NOTHING)"
        )
    else:
        print("  (dry-run: no rows updated)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill entity_id and episode_entities for home_assistant.history "
            "presence episodes (bu-v7hen)"
        )
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
            dry_run=args.dry_run,
        )
    finally:
        await pool.close()
        if chronicler_pool is not pool:
            await chronicler_pool.close()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
