#!/usr/bin/env python3
"""Backfill closed public.fleet_cases rows from resolved owner_conditions episodes.

Background (bu-vl0kh, bu-8cdl1.7 Slice 6, RFC 0032)
----------------------------------------------------
RFC 0032 introduces ``public.fleet_cases`` as the durable "one situation, one
case" object. This script is the one-time (idempotent-rerun) historical
backfill: it creates ONLY closed/lapsed cases from data that already existed
before the feature shipped, and never opens a case as active. See
``butlers.core.fleet_cases.backfill_from_owner_conditions`` for the full
source-selection rationale and outcome-mapping rule; this script is a thin
operator entry point over that function.

Unlike Slice 5's lapse sweep, this is NOT wired into the scheduler -- it is
meant to be run once (or re-run harmlessly; reruns skip every
correlation_key already backfilled).

Write authority: only ``butler_switchboard_rw`` may INSERT
``public.fleet_cases`` (enforced by row-level security, see
``alembic/versions/core/core_217_fleet_case_file.py``). This script always
connects with that role.

Usage
-----
Dry run (reports the resolved-episode count, makes no changes)::

    uv run python scripts/backfill_fleet_cases.py --dry-run

Apply::

    uv run python scripts/backfill_fleet_cases.py

Environment
-----------
Reads the same POSTGRES_*/DATABASE_URL variables every butler daemon
resolves through (``butlers.db.db_params_from_env``/``database_name_from_env``).

Issue: bu-vl0kh
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

_SWITCHBOARD_ROLE = "butler_switchboard_rw"


async def run(*, dry_run: bool) -> dict[str, object]:
    """Connect as ``butler_switchboard_rw`` and run (or preview) the backfill."""
    from butlers.core.fleet_cases import backfill_from_owner_conditions
    from butlers.db import Database, database_name_from_env, db_params_from_env

    params = db_params_from_env()
    db_name = database_name_from_env("butlers")
    db = Database(db_name=db_name, role=_SWITCHBOARD_ROLE, **params)
    pool = await db.connect()
    try:
        if dry_run:
            found = await pool.fetchval(
                "SELECT count(*) FROM public.owner_conditions WHERE state = 'resolved'"
            )
            found = int(found) if found is not None else 0
            logger.info(
                "Dry run: %d resolved owner_conditions episode(s) would be considered "
                "(already-backfilled correlation_keys are skipped at apply time).",
                found,
            )
            return {"created_case_ids": [], "created_count": 0, "skipped_count": found}
        return await backfill_from_owner_conditions(pool)
    finally:
        await pool.close()


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill closed public.fleet_cases rows from resolved "
        "owner_conditions episodes (bu-vl0kh, RFC 0032 Slice 6)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Report the resolved-episode count without writing any changes",
    )
    args = parser.parse_args(argv)

    try:
        result = await run(dry_run=args.dry_run)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"Backfill complete (mode={'DRY RUN' if args.dry_run else 'APPLY'}):")
    print(f"  Cases created: {result['created_count']}")
    print(
        f"  Skipped (already backfilled, unresolved, or dry-run preview): {result['skipped_count']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
