#!/usr/bin/env python3
"""Backfill entity_id on chronicler.episodes for the google_calendar.completed adapter.

Background (bu-f4755)
---------------------
Migration chronicler_013 added the ``entity_id`` column to
``chronicler.episodes``.  Episodes projected *before* the corresponding
adapter change (which now resolves entity_id at projection time) have
``entity_id = NULL``.

This script iterates those NULL rows and populates ``entity_id`` by
resolving the Google account owner from the episode's ``payload``:

  ``payload->>'schema'``  (the butler schema that owns the calendar)
  → ``{schema}.calendar_sources.metadata->>'account_email'``  (any user-lane source)
  → ``public.google_accounts.entity_id``

Resolution is idempotent: rows that already have a non-NULL
``entity_id`` are skipped.

Adapters supported
------------------
- ``google_calendar.completed``  — the only adapter that should set
  entity_id per bu-f4755 scope.  Pass ``--adapter google_calendar.completed``
  (the default) to target it.

Usage
-----
Dry run (reports counts, makes no changes)::

    uv run python scripts/backfill_episode_entity_id.py --dry-run

Apply::

    uv run python scripts/backfill_episode_entity_id.py

Target a specific adapter::

    uv run python scripts/backfill_episode_entity_id.py --adapter google_calendar.completed

Environment
-----------
BUTLERS_DATABASE_URL
    Required asyncpg DSN, e.g.
    ``postgresql://user:pass@localhost:5432/butlers``

Alternative: watermark reset
----------------------------
Instead of running this script, you can reset the adapter's watermark in
``chronicler.projection_checkpoints`` to ``NULL`` and let the next scheduled
run re-project all rows (they will receive entity_id at projection time).
See roster/chronicler/AGENTS.md for the exact SQL.

Issue: bu-f4755
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

# Chunk size for UPDATE batches (keeps transactions short).
_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Pure resolution function (also tested in test_backfill_episode_entity_id.py)
# ---------------------------------------------------------------------------


async def resolve_entity_id_for_episode(
    pool: asyncpg.Pool,
    *,
    schema: str,
) -> UUID | None:
    """Resolve the entity_id for the Google account that owns ``schema``'s calendar.

    Lookup path:
      ``{schema}.calendar_sources.metadata->>'account_email'``
      → ``public.google_accounts.entity_id``

    Returns ``None`` when any step cannot be resolved (table absent, no
    matching row, entity_id IS NULL on the account row).  All failures are
    logged at DEBUG level so the caller can continue without aborting.

    This function is intentionally pure (no side effects) so it can be
    exercised in unit tests with a mock pool.
    """
    quoted = _quote_ident(schema)
    try:
        async with pool.acquire() as conn:
            # Step 1: account_email from any user-lane calendar source.
            email_row = await conn.fetchrow(
                f"""
                SELECT metadata->>'account_email' AS account_email
                FROM {quoted}.calendar_sources
                WHERE lane = 'user'
                  AND metadata->>'account_email' IS NOT NULL
                LIMIT 1
                """
            )
            if email_row is None:
                logger.debug("No user-lane calendar source with account_email in schema %r", schema)
                return None

            account_email: str = email_row["account_email"]

            # Step 2: entity_id from google_accounts.
            entity_row = await conn.fetchrow(
                """
                SELECT entity_id
                FROM public.google_accounts
                WHERE email = $1
                LIMIT 1
                """,
                account_email,
            )
            if entity_row is None:
                logger.debug(
                    "google_accounts has no row for email %r (schema %r)", account_email, schema
                )
                return None

            raw = entity_row["entity_id"]
            if raw is None:
                return None
            if isinstance(raw, UUID):
                return raw
            if isinstance(raw, str):
                return UUID(raw)
            logger.debug("Unexpected entity_id type %r for schema %r", type(raw).__name__, schema)
            return None

    except asyncpg.PostgresError:
        logger.debug(
            "entity_id resolution failed for schema %r (table absent or query error)",
            schema,
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Backfill core
# ---------------------------------------------------------------------------


async def collect_null_episodes(
    pool: asyncpg.Pool,
    *,
    adapter: str,
) -> list[dict[str, Any]]:
    """Return all episodes with entity_id IS NULL for the given adapter.

    Each result dict contains ``id`` (UUID) and ``schema`` (str).
    """
    rows = await pool.fetch(
        """
        SELECT id, payload->>'schema' AS schema
        FROM chronicler.episodes
        WHERE source_name = $1
          AND entity_id IS NULL
          AND tombstone_at IS NULL
        ORDER BY created_at ASC
        """,
        adapter,
    )
    return [{"id": row["id"], "schema": row["schema"]} for row in rows]


async def backfill(
    pool: asyncpg.Pool,
    *,
    adapter: str,
    dry_run: bool,
) -> dict[str, int]:
    """Run the backfill for a single adapter.

    Returns a summary dict with keys:
      - ``total``      — episodes with entity_id IS NULL
      - ``resolved``   — episodes where entity_id was successfully resolved
      - ``unresolved`` — episodes where resolution returned NULL
      - ``updated``    — rows actually UPDATEd (0 in dry-run mode)
    """
    episodes = await collect_null_episodes(pool, adapter=adapter)
    total = len(episodes)

    if total == 0:
        logger.info("No NULL entity_id episodes found for adapter %r — nothing to do.", adapter)
        return {"total": 0, "resolved": 0, "unresolved": 0, "updated": 0}

    logger.info("Found %d episode(s) with entity_id IS NULL for adapter %r.", total, adapter)

    # Group by schema so we only do one DB round-trip per schema.
    by_schema: dict[str, list[UUID]] = {}
    for ep in episodes:
        schema = ep["schema"]
        if not schema:
            logger.debug("Episode %s has no 'schema' in payload — skipping.", ep["id"])
            continue
        by_schema.setdefault(schema, []).append(ep["id"])

    resolved_count = 0
    unresolved_count = 0
    updated_count = 0

    for schema, ep_ids in by_schema.items():
        entity_id = await resolve_entity_id_for_episode(pool, schema=schema)

        if entity_id is None:
            logger.debug(
                "Could not resolve entity_id for schema %r — %d episode(s) will remain NULL.",
                schema,
                len(ep_ids),
            )
            unresolved_count += len(ep_ids)
            continue

        resolved_count += len(ep_ids)
        logger.info(
            "Schema %r → entity_id %s (%d episode(s)%s)",
            schema,
            entity_id,
            len(ep_ids),
            " — dry run, skipping UPDATE" if dry_run else "",
        )

        if dry_run:
            continue

        # Update in batches to keep transactions short.
        for i in range(0, len(ep_ids), _BATCH_SIZE):
            chunk = ep_ids[i : i + _BATCH_SIZE]
            result = await pool.execute(
                """
                UPDATE chronicler.episodes
                SET entity_id = $1,
                    updated_at = now()
                WHERE id = ANY($2::uuid[])
                  AND entity_id IS NULL
                """,
                entity_id,
                chunk,
            )
            # asyncpg returns "UPDATE N"
            n = int(result.split()[-1])
            updated_count += n
            logger.info(
                "  Updated %d row(s) in schema %r (batch %d/%d).",
                n,
                schema,
                i // _BATCH_SIZE + 1,
                (len(ep_ids) + _BATCH_SIZE - 1) // _BATCH_SIZE,
            )

    unresolved_no_schema = total - resolved_count - unresolved_count
    unresolved_count += unresolved_no_schema

    return {
        "total": total,
        "resolved": resolved_count,
        "unresolved": unresolved_count,
        "updated": updated_count,
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
        description="Backfill entity_id on chronicler.episodes from calendar adapters"
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
        help="Report what would be updated without writing any changes",
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
    print(f"  Total NULL episodes:  {summary['total']}")
    print(f"  Resolved:             {summary['resolved']}")
    print(f"  Unresolved (no data): {summary['unresolved']}")
    if not args.dry_run:
        print(f"  Rows updated:         {summary['updated']}")
    else:
        print("  (dry-run: no rows updated)")

    if args.dry_run and summary["resolved"] > 0:
        print()
        print(f"Re-run without --dry-run to update {summary['resolved']} episode(s).")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
