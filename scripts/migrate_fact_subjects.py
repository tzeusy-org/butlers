#!/usr/bin/env python3
"""One-off migration: normalize fact subjects and backfill entity_id.

Fixes two issues:
1. Quick facts written by fact_set() used bare UUID subjects (e.g. '9e71ec52-...')
   instead of the 'contact:{uuid}' convention used by all other relationship tools.
2. Facts for contacts that already have a linked entity are missing entity_id,
   so they don't appear on the entity detail page.

Operations (all idempotent):
  - Rename bare-UUID subjects → contact:{uuid} where a matching contact exists
  - Backfill entity_id on facts whose subject matches 'contact:{cid}' and the
    contact has a linked entity

Usage:
  uv run python scripts/migrate_fact_subjects.py [--dry-run] [--schema SCHEMA]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg

DEFAULT_DATABASE_URL_ENV = "DATABASE_URL"
DEFAULT_SCHEMA = "relationship"


async def run(dsn: str, schema: str, dry_run: bool) -> None:
    search_path = f"{schema}, shared, public"
    pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=2,
        server_settings={"search_path": search_path},
    )
    try:
        await _migrate(pool, schema, dry_run)
    finally:
        await pool.close()


async def _migrate(pool: asyncpg.Pool, schema: str, dry_run: bool) -> None:

    # --- Step 1: Find bare-UUID subjects that match a contact id ---
    bare_rows = await pool.fetch(
        """
        SELECT f.id AS fact_id, f.subject, c.id AS contact_id, c.entity_id
        FROM facts f
        JOIN contacts c ON f.subject = c.id::text
        WHERE f.subject NOT LIKE 'contact:%'
          AND f.validity = 'active'
        ORDER BY f.created_at
        """
    )
    print(f"Step 1: {len(bare_rows)} active facts with bare-UUID subjects matching a contact")

    if bare_rows:
        for r in bare_rows:
            old = r["subject"]
            new = f"contact:{old}"
            print(f"  {r['fact_id']}  {old} → {new}  entity_id={r['entity_id']}")

        if not dry_run:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for r in bare_rows:
                        await conn.execute(
                            "UPDATE facts SET subject = $1 WHERE id = $2",
                            f"contact:{r['subject']}",
                            r["fact_id"],
                        )
            print(f"  ✓ Renamed {len(bare_rows)} subjects")
        else:
            print("  (dry-run, no changes)")

    # --- Step 2: Backfill entity_id on contact-prefixed facts ---
    unlinked_rows = await pool.fetch(
        """
        SELECT f.id AS fact_id, f.subject, c.entity_id
        FROM facts f
        JOIN contacts c ON f.subject = 'contact:' || c.id::text
        WHERE f.entity_id IS NULL
          AND c.entity_id IS NOT NULL
          AND f.validity = 'active'
        ORDER BY f.created_at
        """
    )
    print(f"\nStep 2: {len(unlinked_rows)} active facts missing entity_id (contact has one)")

    if unlinked_rows:
        for r in unlinked_rows:
            print(f"  {r['fact_id']}  {r['subject']}  → entity_id={r['entity_id']}")

        if not dry_run:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for r in unlinked_rows:
                        await conn.execute(
                            "UPDATE facts SET entity_id = $1 WHERE id = $2",
                            r["entity_id"],
                            r["fact_id"],
                        )
            print(f"  ✓ Backfilled entity_id on {len(unlinked_rows)} facts")
        else:
            print("  (dry-run, no changes)")

    if not bare_rows and not unlinked_rows:
        print("\nNothing to migrate — all facts are already consistent.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without modifying data",
    )
    parser.add_argument(
        "--schema",
        default=DEFAULT_SCHEMA,
        help=f"Butler schema to migrate (default: {DEFAULT_SCHEMA})",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help=f"Database URL (default: ${DEFAULT_DATABASE_URL_ENV})",
    )
    args = parser.parse_args()

    dsn = args.dsn or os.environ.get(DEFAULT_DATABASE_URL_ENV)
    if not dsn:
        # Fall back to individual POSTGRES_* env vars (same as butlers.db)
        host = os.environ.get("POSTGRES_HOST", "localhost")
        port = os.environ.get("POSTGRES_PORT", "5432")
        user = os.environ.get("POSTGRES_USER", "butlers")
        password = os.environ.get("POSTGRES_PASSWORD", "butlers")
        db = os.environ.get("POSTGRES_DB", "butlers")
        dsn = f"postgresql://{user}:{password}@{host}:{port}/{db}"

    asyncio.run(run(dsn, args.schema, args.dry_run))


if __name__ == "__main__":
    main()
