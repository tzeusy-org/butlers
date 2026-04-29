#!/usr/bin/env python3
"""Tombstone pre-existing chronicler.episodes rows for butler-internal sessions.

Before bu-x096m (PR #1239), the CoreSessionsAdapter projected every session row
into chronicler.episodes — including operational heartbeats emitted by the
scheduler (trigger_source IN ('tick','qa','healing') or 'schedule:*').  Those
rows carry no "lived past time" signal and inflate lane counts in the Chronicler
dashboard.

bu-x096m added a projection-time filter so *future* sessions with those sources
are skipped.  This script handles the historical backfill: it sets
``tombstone_at = now()`` on any pre-existing episode row in
``chronicler.episodes`` that was produced from an operational session.

Matching criteria (reuses constants from bu-x096m):
  - ``source_name = 'core.sessions'``
  - ``payload->>'trigger_source' IN ('tick', 'qa', 'healing')``
    OR ``payload->>'trigger_source' LIKE 'schedule:%'``

Behaviour:
  - Default: dry run — prints per-category counts, makes no DB changes.
  - ``--apply``: tombstones matching rows (idempotent; re-running touches zero
    additional rows because already-tombstoned rows are excluded by the WHERE).
  - Logs per-category counts (tick, qa, healing, schedule:*).

Usage:
    # Dry run — print counts only:
    uv run python scripts/backfill_tombstone_heartbeat_episodes.py

    # Apply tombstones:
    uv run python scripts/backfill_tombstone_heartbeat_episodes.py --apply

Environment:
    BUTLERS_DATABASE_URL  — required asyncpg DSN, e.g.
                            postgresql://user:pass@localhost:5432/butlers

Issue: bu-noocq
Sibling: bu-x096m (PR #1239) — added projection-time filter
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg

# Re-use the constants defined by bu-x096m so the matching criteria stay in sync
# with the projection-time filter.
from butlers.chronicler.adapters.sessions import (
    EXCLUDED_TRIGGER_SOURCE_PREFIX,
    EXCLUDED_TRIGGER_SOURCES,
)

# ---------------------------------------------------------------------------
# Source name constant — matches CoreSessionsAdapter.SOURCE_NAME
# ---------------------------------------------------------------------------
_CORE_SESSIONS_SOURCE = "core.sessions"


# ---------------------------------------------------------------------------
# Core logic (pure asyncpg — no ORM, no migration tooling)
# ---------------------------------------------------------------------------


async def count_by_category(pool: asyncpg.Pool) -> dict[str, int]:
    """Return tombstone-candidate counts grouped by trigger_source category.

    Categories:
        tick      — exactly 'tick'
        qa        — exactly 'qa'
        healing   — exactly 'healing'
        schedule  — any 'schedule:*' prefix
        total     — sum of the above

    Only rows with ``tombstone_at IS NULL`` are counted (already-tombstoned
    rows are excluded so the diagnostic reflects remaining work).
    """
    exact_sources = list(EXCLUDED_TRIGGER_SOURCES)
    prefix_pattern = EXCLUDED_TRIGGER_SOURCE_PREFIX + "%"

    rows = await pool.fetch(
        """
        SELECT
            payload->>'trigger_source' AS trigger_source,
            COUNT(*)::int               AS n
        FROM chronicler.episodes
        WHERE source_name = $1
          AND tombstone_at IS NULL
          AND (
              payload->>'trigger_source' = ANY($2::text[])
              OR payload->>'trigger_source' LIKE $3
          )
        GROUP BY payload->>'trigger_source'
        ORDER BY payload->>'trigger_source'
        """,
        _CORE_SESSIONS_SOURCE,
        exact_sources,
        prefix_pattern,
    )

    counts: dict[str, int] = {src: 0 for src in sorted(EXCLUDED_TRIGGER_SOURCES)}
    counts["schedule:*"] = 0

    for row in rows:
        ts = row["trigger_source"] or ""
        if ts in EXCLUDED_TRIGGER_SOURCES:
            counts[ts] = row["n"]
        elif ts.startswith(EXCLUDED_TRIGGER_SOURCE_PREFIX):
            counts["schedule:*"] += row["n"]

    counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return counts


async def apply_tombstones(pool: asyncpg.Pool) -> int:
    """Set tombstone_at = now() on all matching, not-yet-tombstoned episodes.

    Returns the number of rows updated.  Idempotent: rows already tombstoned
    are excluded by the WHERE clause.
    """
    exact_sources = list(EXCLUDED_TRIGGER_SOURCES)
    prefix_pattern = EXCLUDED_TRIGGER_SOURCE_PREFIX + "%"

    result = await pool.execute(
        """
        UPDATE chronicler.episodes
        SET tombstone_at = now()
        WHERE source_name = $1
          AND tombstone_at IS NULL
          AND (
              payload->>'trigger_source' = ANY($2::text[])
              OR payload->>'trigger_source' LIKE $3
          )
        """,
        _CORE_SESSIONS_SOURCE,
        exact_sources,
        prefix_pattern,
    )
    # asyncpg returns a string like "UPDATE 4217"
    return int(result.split()[-1])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_counts(counts: dict[str, int], *, label: str = "Candidates") -> None:
    print(f"\n{label}:")
    for key in sorted(k for k in counts if k != "total"):
        print(f"  {key:<20} {counts[key]:>8}")
    print(f"  {'total':<20} {counts.get('total', 0):>8}")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tombstone historical chronicler.episodes rows for heartbeat sessions"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually tombstone the rows (default: dry-run diagnostic only)",
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

    try:
        mode = "APPLY" if args.apply else "DRY RUN (use --apply to write changes)"
        print(f"Mode: {mode}")
        print(f"Matching source_name = '{_CORE_SESSIONS_SOURCE}'")
        print(
            f"  trigger_source IN {sorted(EXCLUDED_TRIGGER_SOURCES)!r}"
            f"  OR trigger_source LIKE '{EXCLUDED_TRIGGER_SOURCE_PREFIX}%'"
        )

        before_counts = await count_by_category(pool)
        _print_counts(before_counts, label="Untombstoned candidates (before)")

        if before_counts["total"] == 0:
            print("\nNothing to do — no untombstoned heartbeat episodes found.")
            return 0

        if not args.apply:
            print(
                "\nDry run complete. Re-run with --apply to set tombstone_at on "
                f"{before_counts['total']} row(s)."
            )
            return 0

        updated = await apply_tombstones(pool)
        print(f"\nTombstoned {updated} episode row(s).")

        after_counts = await count_by_category(pool)
        _print_counts(after_counts, label="Untombstoned candidates (after, expect all zeros)")

        if after_counts["total"] != 0:
            print(
                f"WARNING: {after_counts['total']} row(s) still untombstoned after apply. "
                "This is unexpected — check for concurrent inserts or rerun.",
                file=sys.stderr,
            )
            return 1

        print("\nBackfill complete. Run verify_counts() or check Chronicler dashboard.")
        return 0

    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
