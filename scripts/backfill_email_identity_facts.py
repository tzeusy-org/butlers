#!/usr/bin/env python3
"""Backfill has-email facts for email senders with an unambiguous existing-entity match.

Background (bu-qeaou)
----------------------
Only a small fraction of distinct all-time email senders resolve to a
relationship entity via an active ``has-email`` fact — ingestion normalizes
``public.ingestion_events.source_sender_identity`` to a bare lowercased
address at ingest time (see ``connectors/gmail.py`` / ``butlers.identity
.normalize_email_sender``), but nothing previously *linked* that address to
the entity it obviously belongs to.

This script covers the "obvious existing matches" case only: for each email
sender address with no active ``has-email`` fact, it derives a display name
from the address local-part and looks for **exactly one** existing, real
(non-placeholder) ``person`` entity whose ``canonical_name`` or an alias
matches it. When such a match is found, it writes the ``has-email`` fact via
the real central-writer path (``relationship_assert_fact`` —
``roster/relationship/tools/relationship_assert_fact.py``), never a hand
``INSERT``. Ambiguous (>1 match) or unmatched (0 match) addresses are skipped
— they are the identity *enrichment loop*'s job (proposes entity
creation/linking via the approvals queue; see
``roster/relationship/jobs/relationship_jobs.py::run_email_identity_enrichment``,
which runs automatically as the ``email-identity-enrichment`` schedule), not
this backfill's.

The central writer's own safety rails still apply unchanged: if a matched
entity happens to be the OWNER, ``relationship_assert_fact`` parks the write
as a ``pending_actions`` approval instead of writing directly (RFC 0017
§2.3) — this script does not, and must not, bypass that.

Usage
-----
Dry run (reports what would be written, makes no changes)::

    uv run python scripts/backfill_email_identity_facts.py --dry-run

Apply::

    uv run python scripts/backfill_email_identity_facts.py

Tune the lookback window (default 180 days) or row cap (default 20000)::

    uv run python scripts/backfill_email_identity_facts.py --lookback-days 365 --row-limit 50000

Environment
-----------
BUTLERS_DATABASE_URL
    Required asyncpg DSN, e.g.
    ``postgresql://user:pass@localhost:5432/butlers``

CAUTION: confirm the target DB before applying. The live system is
``butlers-db-dev`` (``.env.dev``); ``butlers-db`` (``.env.prod``) is the empty
one (repo memory: butlers-db-host-topology). This script was NOT run against
the live DB as part of bu-qeaou — verify against the testcontainers
integration suite (``tests/integration/test_backfill_email_identity_facts_db.py``)
before applying in production.

Issue: bu-qeaou
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

_DEFAULT_LOOKBACK_DAYS = 180
_DEFAULT_ROW_LIMIT = 20_000


async def backfill_email_identity_facts(
    pool: asyncpg.Pool,
    *,
    lookback_days: int,
    row_limit: int,
    dry_run: bool,
) -> dict[str, int]:
    """Link unambiguous email-sender-to-entity matches via the central writer.

    Returns a summary dict:
      - ``senders_scanned``   — distinct normalized email senders considered
      - ``already_linked``    — senders with an active has-email fact already
      - ``ambiguous_or_unmatched`` — 0 or >1 candidate entity match (skipped)
      - ``linked``            — has-email facts written (0 in dry-run)
      - ``pending_approval``  — writes parked by the central writer's owner
        carve-out (counted separately from ``linked``; not a failure)
      - ``errors``            — writes that failed unexpectedly
      - ``truncated``         — 1 if the sender scan hit ``row_limit`` (0 else)
    """
    # Import lazily: triggers butlers.tools' dynamic butler-tool registration
    # (see src/butlers/tools/__init__.py) so relationship_assert_fact resolves.
    from butlers.modules.contacts.email_identity_matching import (
        derive_display_name_from_address,
        fetch_active_has_email_addresses,
        fetch_email_sender_stats,
        match_existing_person_entity,
    )
    from butlers.tools.relationship.relationship_assert_fact import relationship_assert_fact

    summary = {
        "senders_scanned": 0,
        "already_linked": 0,
        "ambiguous_or_unmatched": 0,
        "linked": 0,
        "pending_approval": 0,
        "errors": 0,
        "truncated": 0,
    }

    scan = await fetch_email_sender_stats(pool, lookback_days=lookback_days, row_limit=row_limit)
    summary["senders_scanned"] = len(scan.stats)
    summary["truncated"] = int(scan.truncated)
    if scan.truncated:
        logger.warning(
            "Sender scan hit row_limit=%d — some senders were not considered this run.",
            row_limit,
        )

    if not scan.stats:
        logger.info("No email senders found; nothing to do.")
        return summary

    already_linked = await fetch_active_has_email_addresses(pool, [c.address for c in scan.stats])

    for candidate in scan.stats:
        address = candidate.address
        if address in already_linked:
            summary["already_linked"] += 1
            continue

        display_name = derive_display_name_from_address(address)
        matched_entity_id = await match_existing_person_entity(pool, display_name)
        if matched_entity_id is None:
            summary["ambiguous_or_unmatched"] += 1
            logger.debug(
                "skip address=%s derived_name=%r — no unambiguous entity match",
                address,
                display_name,
            )
            continue

        logger.info(
            "%s address=%s -> entity=%s (derived_name=%r)",
            "would link" if dry_run else "linking",
            address,
            matched_entity_id,
            display_name,
        )
        if dry_run:
            summary["linked"] += 1
            continue

        try:
            result = await relationship_assert_fact(
                pool,
                matched_entity_id,
                "has-email",
                address,
                src="migration",
                object_kind="literal",
                primary=True,
            )
        except Exception:
            logger.exception("failed to assert has-email fact for address=%s", address)
            summary["errors"] += 1
            continue

        if result.outcome.value == "pending_approval":
            summary["pending_approval"] += 1
        else:
            summary["linked"] += 1

    return summary


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill has-email facts for unambiguous email-sender-to-entity matches (bu-qeaou)"
        )
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=_DEFAULT_LOOKBACK_DAYS,
        help=(
            "Only consider ingestion activity from the last N days "
            f"(default: {_DEFAULT_LOOKBACK_DAYS})"
        ),
    )
    parser.add_argument(
        "--row-limit",
        type=int,
        default=_DEFAULT_ROW_LIMIT,
        help=f"Max ingestion_events rows scanned (default: {_DEFAULT_ROW_LIMIT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Report what would be linked without writing any changes",
    )
    args = parser.parse_args(argv)

    if args.lookback_days <= 0:
        print("ERROR: --lookback-days must be a positive integer", file=sys.stderr)
        return 1
    if args.row_limit <= 0:
        print("ERROR: --row-limit must be a positive integer", file=sys.stderr)
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
        summary = await backfill_email_identity_facts(
            pool,
            lookback_days=args.lookback_days,
            row_limit=args.row_limit,
            dry_run=args.dry_run,
        )
    finally:
        await pool.close()

    print()
    print(f"Backfill complete (mode={'DRY RUN' if args.dry_run else 'APPLY'}):")
    print(f"  Senders scanned:          {summary['senders_scanned']}")
    print(f"  Already linked:           {summary['already_linked']}")
    print(f"  Ambiguous/unmatched:      {summary['ambiguous_or_unmatched']}")
    print(f"  Linked:                   {summary['linked']}")
    print(f"  Parked for approval:      {summary['pending_approval']}")
    print(f"  Errors:                   {summary['errors']}")
    if summary["truncated"]:
        print("  WARNING: sender scan was truncated by --row-limit; rerun to cover the rest.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
