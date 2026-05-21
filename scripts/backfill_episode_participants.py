#!/usr/bin/env python3
"""Backfill episode_entities on chronicler.episodes for the google_calendar.completed adapter.

Background (bu-xuqyo)
---------------------
Migration chronicler_014 added the ``episode_entities`` join table to
``chronicler``.  Episodes projected *before* the corresponding adapter change
(bu-3zve1, which now writes ``episode_entities`` at projection time) have no
rows in that table.

This script iterates every ``google_calendar.completed`` episode, derives the
upstream calendar event's attendee set from the calendar module's
``{schema}.calendar_event_entities`` join table, and upserts the matching
``chronicler.episode_entities`` rows.

Join path
---------
For each episode the backfill derives:

  ``chronicler.episodes.payload->>'schema'``  (the butler schema that owns the calendar)
  ``chronicler.episodes.payload->>'origin_instance_ref'``  (upstream Google Calendar instance ID)
  → ``{schema}.calendar_event_instances.origin_instance_ref`` → ``event_id``
  → ``{schema}.calendar_event_entities.event_id`` → ``entity_id`` (participant)

The owner row (``role='owner'``) is written using the episode's existing
``chronicler.episodes.entity_id`` column.  Participant rows (``role='participant'``)
are written for each non-owner attendee resolved from the upstream join table.

Role-precedence collapse
------------------------
If the same ``entity_id`` appears as both the owner and a calendar participant,
the script writes exactly one row with ``role='owner'`` (owner > organizer >
participant, same rule as the adapter in bu-3zve1).

Idempotency
-----------
The script uses DELETE + INSERT (matching the adapter's strategy in ``_upsert_episode_entities``).
Re-running on the same data produces the same final state — the DELETE clears any prior
rows before the INSERT, so the set always converges to the current attendee set.

Adapters supported
------------------
- ``google_calendar.completed``  — the only adapter that should set
  ``episode_entities`` per bu-xuqyo scope.  Pass ``--adapter google_calendar.completed``
  (the default) to target it.

Usage
-----
Dry run (reports counts, makes no changes)::

    uv run python scripts/backfill_episode_participants.py --dry-run

Apply::

    uv run python scripts/backfill_episode_participants.py

Target a specific adapter::

    uv run python scripts/backfill_episode_participants.py --adapter google_calendar.completed

Environment
-----------
BUTLERS_DATABASE_URL
    Required asyncpg DSN, e.g.
    ``postgresql://user:pass@localhost:5432/butlers``

Alternative: watermark reset
----------------------------
Instead of running this script, you can reset the adapter's watermark in
``chronicler.projection_checkpoints`` to ``NULL`` and let the next scheduled
run re-project all rows (they will receive ``episode_entities`` rows at
projection time).  See roster/chronicler/AGENTS.md for the exact SQL.

Issue: bu-xuqyo
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any
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

_SUPPORTED_ADAPTERS: frozenset[str] = frozenset({"google_calendar.completed"})
_DEFAULT_ADAPTER = "google_calendar.completed"

# Role-precedence used when collapsing multiple roles for the same entity_id.
# Higher = more important; the highest-precedence role wins.
_ROLE_PRECEDENCE: dict[str, int] = {
    "owner": 2,
    "organizer": 1,
    "participant": 0,
}

# Chunk size for episode batches (keeps transactions short).
_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Pure resolution function (also tested in test_backfill_episode_participants.py)
# ---------------------------------------------------------------------------


async def fetch_participant_entity_ids(
    pool: asyncpg.Pool,
    *,
    schema: str,
    origin_instance_ref: str,
) -> list[UUID] | None:
    """Resolve the participant entity_ids for one calendar instance.

    Join path:
      ``{schema}.calendar_event_instances.origin_instance_ref``
      → ``event_id``
      → ``{schema}.calendar_event_entities.entity_id``

    Returns a (possibly empty) list of ``entity_id`` UUIDs for all attendees
    resolved by the calendar module for this instance.

    Returns ``None`` when:
    - ``calendar_event_instances`` or ``calendar_event_entities`` is absent
      (calendar module not installed on this schema).
    - A query error occurs.

    Returns ``[]`` when the tables exist but no attendee rows are found for
    this ``origin_instance_ref``.

    All failures are logged at DEBUG level so the caller can continue.
    """
    quoted = _quote_ident(schema)
    try:
        async with pool.acquire() as conn:
            # Check that the calendar tables exist for this schema.
            instances_exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = $1
                      AND table_name = 'calendar_event_instances'
                )
                """,
                schema,
            )
            if not instances_exists:
                logger.debug(
                    "calendar_event_instances absent for schema %r — no participants to backfill",
                    schema,
                )
                return None

            entities_exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = $1
                      AND table_name = 'calendar_event_entities'
                )
                """,
                schema,
            )
            if not entities_exists:
                logger.debug(
                    "calendar_event_entities absent for schema %r — "
                    "falling back to owner-only episode_entities",
                    schema,
                )
                return []

            rows = await conn.fetch(
                f"""
                SELECT DISTINCT cee.entity_id
                FROM {quoted}.calendar_event_instances AS cei
                JOIN {quoted}.calendar_event_entities AS cee
                  ON cee.event_id = cei.event_id
                WHERE cei.origin_instance_ref = $1
                """,
                origin_instance_ref,
            )

    except asyncpg.PostgresError:
        logger.debug(
            "Participant resolution failed for schema %r, origin_instance_ref %r",
            schema,
            origin_instance_ref,
            exc_info=True,
        )
        return None

    result: list[UUID] = []
    for row in rows:
        raw = row["entity_id"]
        if raw is None:
            continue
        eid: UUID = raw if isinstance(raw, UUID) else UUID(str(raw))
        result.append(eid)
    return result


def _build_entity_role_map(
    *,
    owner_id: UUID | None,
    participant_ids: list[UUID],
) -> dict[UUID, str]:
    """Build a deduplicated (entity_id → role) map applying role-precedence.

    Participants go in first at the lowest precedence.  The owner goes in last
    (highest precedence — always overwrites participant).

    This mirrors the logic in ``CalendarCompletedAdapter._upsert_episode_entities``.
    """
    entity_role: dict[UUID, str] = {}

    # Participants at lowest precedence.
    for pid in participant_ids:
        if pid not in entity_role:
            entity_role[pid] = "participant"

    # Owner at highest precedence — always wins.
    if owner_id is not None:
        entity_role[owner_id] = "owner"

    return entity_role


# ---------------------------------------------------------------------------
# Backfill core
# ---------------------------------------------------------------------------


async def collect_episodes(
    pool: asyncpg.Pool,
    *,
    adapter: str,
) -> list[dict[str, Any]]:
    """Return all non-tombstoned episodes for the given adapter.

    Each result dict contains ``id`` (UUID), ``schema`` (str),
    ``origin_instance_ref`` (str), and ``entity_id`` (UUID | None).
    """
    rows = await pool.fetch(
        """
        SELECT id,
               payload->>'schema'               AS schema,
               payload->>'origin_instance_ref'  AS origin_instance_ref,
               entity_id
        FROM chronicler.episodes
        WHERE source_name = $1
          AND tombstone_at IS NULL
        ORDER BY created_at ASC
        """,
        adapter,
    )
    return [
        {
            "id": row["id"],
            "schema": row["schema"],
            "origin_instance_ref": row["origin_instance_ref"],
            "entity_id": row["entity_id"],
        }
        for row in rows
    ]


async def upsert_episode_entities(
    pool: asyncpg.Pool,
    *,
    episode_id: UUID,
    entity_role: dict[UUID, str],
    dry_run: bool,
) -> int:
    """Delete existing episode_entities for the episode and insert the new set.

    Returns the number of rows written (0 in dry-run mode).

    Uses DELETE + INSERT inside a single transaction to match the adapter's
    ``_upsert_episode_entities`` strategy (bu-3zve1): stale attendee removals
    propagate and idempotent replays are safe.
    """
    if dry_run:
        return 0

    rows_to_insert = list(entity_role.items())  # [(entity_id, role), ...]

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM chronicler.episode_entities WHERE episode_id = $1",
                episode_id,
            )
            if rows_to_insert:
                await conn.executemany(
                    """
                    INSERT INTO chronicler.episode_entities (episode_id, entity_id, role)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (episode_id, entity_id)
                    DO UPDATE SET role = EXCLUDED.role
                    """,
                    [(episode_id, eid, role) for eid, role in rows_to_insert],
                )

    return len(rows_to_insert)


async def backfill(
    pool: asyncpg.Pool,
    *,
    adapter: str,
    dry_run: bool,
) -> dict[str, int]:
    """Run the backfill for a single adapter.

    Returns a summary dict with keys:
      - ``total``           — total episodes processed
      - ``rows_written``    — total episode_entities rows written (0 in dry-run)
      - ``skipped``         — episodes skipped (no schema or origin_instance_ref in payload)
      - ``table_absent``    — episodes skipped because calendar tables are absent
    """
    episodes = await collect_episodes(pool, adapter=adapter)
    total = len(episodes)

    if total == 0:
        logger.info("No episodes found for adapter %r — nothing to do.", adapter)
        return {"total": 0, "rows_written": 0, "skipped": 0, "table_absent": 0}

    logger.info("Found %d episode(s) for adapter %r.", total, adapter)

    rows_written = 0
    skipped = 0
    table_absent = 0

    for ep in episodes:
        schema = ep["schema"]
        origin_instance_ref = ep["origin_instance_ref"]
        episode_id: UUID = ep["id"]
        owner_id: UUID | None = ep["entity_id"]

        if not schema or not origin_instance_ref:
            logger.debug(
                "Episode %s missing schema or origin_instance_ref in payload — skipping.",
                episode_id,
            )
            skipped += 1
            continue

        participant_ids = await fetch_participant_entity_ids(
            pool,
            schema=schema,
            origin_instance_ref=origin_instance_ref,
        )

        if participant_ids is None:
            # Calendar tables absent for this schema.
            table_absent += 1
            # Still write the owner row if we have one.
            participant_ids = []

        # Build deduplicated role map applying role-precedence.
        entity_role = _build_entity_role_map(
            owner_id=owner_id,
            participant_ids=participant_ids,
        )

        n = await upsert_episode_entities(
            pool,
            episode_id=episode_id,
            entity_role=entity_role,
            dry_run=dry_run,
        )
        rows_written += n

        if dry_run:
            logger.info(
                "DRY RUN episode %s (schema=%r): would write %d row(s) — owner=%s, participants=%d",
                episode_id,
                schema,
                len(entity_role),
                owner_id,
                len(participant_ids),
            )
        else:
            logger.info(
                "Episode %s (schema=%r): wrote %d row(s) — owner=%s, participants=%d",
                episode_id,
                schema,
                n,
                owner_id,
                len(participant_ids),
            )

    return {
        "total": total,
        "rows_written": rows_written,
        "skipped": skipped,
        "table_absent": table_absent,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _quote_ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Unsafe schema identifier: {name!r}")
    return '"' + name.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill episode_entities on chronicler.episodes from calendar adapters"
    )
    parser.add_argument(
        "--adapter",
        default=_DEFAULT_ADAPTER,
        choices=sorted(_SUPPORTED_ADAPTERS),
        help=f"Source adapter name (default: {_DEFAULT_ADAPTER!r})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Report what would be written without making any changes",
    )
    args = parser.parse_args(argv)

    db_url = os.environ.get("BUTLERS_DATABASE_URL")
    if not db_url:
        print("ERROR: BUTLERS_DATABASE_URL environment variable is not set", file=sys.stderr)
        return 1

    try:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
    except Exception as exc:
        print(f"ERROR: Failed to connect to database: {exc}", file=sys.stderr)
        return 1

    mode = "DRY RUN" if args.dry_run else "APPLY"
    logger.info("Mode: %s | Adapter: %s", mode, args.adapter)

    try:
        summary = await backfill(pool, adapter=args.adapter, dry_run=args.dry_run)
    finally:
        await pool.close()

    print()
    print(f"Summary (adapter={args.adapter!r}, mode={mode}):")
    print(f"  Total episodes:           {summary['total']}")
    print(f"  Skipped (bad payload):    {summary['skipped']}")
    print(f"  Calendar tables absent:   {summary['table_absent']}")
    if not args.dry_run:
        print(f"  episode_entities rows written: {summary['rows_written']}")
    else:
        print("  (dry-run: no rows written)")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
