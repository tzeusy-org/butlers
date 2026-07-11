#!/usr/bin/env python3
"""Retract email-identity-enrichment proposals for bulk/automated senders.

Background (bu-qeaou follow-up)
-------------------------------
``roster/relationship/jobs/relationship_jobs.py::run_email_identity_enrichment``
proposes creating/linking a ``person`` entity for each recurring email sender,
surfaced as a ``relationship_assert_fact`` (``has-email``) row in the approvals
queue (``relationship.pending_actions``). Its bulk/automated-sender filter
(``is_bulk_or_noreply_address``) originally missed bare ``notice``/``notify``
local-parts and never inspected the domain, so transactional senders like
``notice@email.anthropic.com`` slipped through and produced spurious "Notice"
Person proposals (and eagerly-created placeholder entities).

The filter has since been tightened (``email_identity_matching.py``), so no new
bad proposals are generated. This one-off operator script cleans up the ones
already sitting in the queue: it re-runs the *current* filter over every pending
enrichment proposal and, for those now classified as bulk/automated:

  1. marks the ``pending_actions`` row ``expired`` (system retraction — not a
     human ``rejected`` decision), and
  2. deletes the placeholder ``public.entities`` row the job eagerly created,
     but ONLY when it is a genuine orphan — carries the
     ``metadata.proposed_source = 'email_identity_enrichment'`` marker, has no
     ``entity_facts`` referencing it, no linked contact, and no other live
     pending/approved/executed action pointing at it.

Human-looking proposals (addresses the current filter still considers real
correspondents) are left untouched.

Usage
-----
Dry run — the DEFAULT; reports what would change, mutates nothing::

    uv run python scripts/cleanup_bulk_email_identity_proposals.py

Apply::

    uv run python scripts/cleanup_bulk_email_identity_proposals.py --apply

Target a non-default butler schema (default ``relationship``)::

    uv run python scripts/cleanup_bulk_email_identity_proposals.py --schema relationship

Environment
-----------
BUTLERS_DATABASE_URL
    Required asyncpg DSN, e.g.
    ``postgresql://user:pass@localhost:5432/butlers``

CAUTION: confirm the target DB before applying. The live system is
``butlers-db-dev`` (``.env.dev``); ``butlers-db`` (``.env.prod``) is the empty
one (repo memory: butlers-db-host-topology).

Issue: bu-qeaou
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

_ENRICHMENT_MARKER = "source=email_identity_enrichment"


async def cleanup_bulk_email_identity_proposals(
    pool: asyncpg.Pool,
    *,
    schema: str,
    apply: bool,
) -> dict[str, int]:
    """Expire bulk-sender enrichment proposals and delete their orphan entities.

    Returns a summary dict:
      - ``proposals_scanned``   — pending enrichment has-email proposals seen
      - ``kept_human``          — proposals the current filter still treats as real
      - ``expired``             — proposals expired (0 in dry-run)
      - ``entities_deleted``    — placeholder orphan entities removed (0 in dry-run)
      - ``entities_kept``       — candidate entities skipped (not a clean orphan)
    """
    from butlers.modules.contacts.email_identity_matching import is_bulk_or_noreply_address

    summary = {
        "proposals_scanned": 0,
        "kept_human": 0,
        "expired": 0,
        "entities_deleted": 0,
        "entities_kept": 0,
    }

    rows = await pool.fetch(
        f"""
        SELECT id,
               agent_summary,
               tool_args->>'object'  AS address,
               tool_args->>'subject' AS entity_id
        FROM {schema}.pending_actions
        WHERE status = 'pending'
          AND tool_name = 'relationship_assert_fact'
          AND tool_args->>'predicate' = 'has-email'
          AND evidence @> $1::jsonb
        ORDER BY requested_at
        """,
        f'["{_ENRICHMENT_MARKER}"]',
    )
    summary["proposals_scanned"] = len(rows)
    if not rows:
        logger.info("No pending email-identity-enrichment proposals found; nothing to do.")
        return summary

    bulk_rows = []
    for row in rows:
        address = row["address"]
        if address and is_bulk_or_noreply_address(address):
            bulk_rows.append(row)
        else:
            summary["kept_human"] += 1
            logger.debug("keep (human-looking) address=%s", address)

    if not bulk_rows:
        logger.info(
            "All %d pending proposals still classify as human correspondents; nothing to retract.",
            len(rows),
        )
        return summary

    expire_ids = [row["id"] for row in bulk_rows]

    for row in bulk_rows:
        logger.info(
            "%s bulk proposal id=%s address=%s (%s)",
            "would expire" if not apply else "expiring",
            row["id"],
            row["address"],
            row["agent_summary"],
        )

    if apply:
        expired = await pool.fetch(
            f"""
            UPDATE {schema}.pending_actions
            SET status = 'expired',
                decided_by = 'system:bulk-sender-cleanup',
                decided_at = now()
            WHERE id = ANY($1::uuid[])
              AND status = 'pending'
            RETURNING id
            """,
            expire_ids,
        )
        summary["expired"] = len(expired)
    else:
        summary["expired"] = 0  # dry-run

    # --- Orphan placeholder-entity removal -------------------------------------
    # Only entities the job eagerly created (marker present) that are now clean
    # orphans. Exclude the very actions we just expired from the "live action"
    # guard so the check is meaningful in dry-run too.
    has_contacts_table = (
        await pool.fetchval("SELECT to_regclass($1)", f"{schema}.contacts") is not None
    )
    contacts_clause = (
        f"AND NOT EXISTS (SELECT 1 FROM {schema}.contacts c WHERE c.entity_id = e.id)"
        if has_contacts_table
        else ""
    )

    candidate_entity_ids = {row["entity_id"] for row in bulk_rows if row["entity_id"]}
    for entity_id in sorted(candidate_entity_ids):
        try:
            parsed = uuid.UUID(entity_id)
        except (ValueError, TypeError):
            logger.warning("skip non-uuid subject=%r", entity_id)
            continue

        orphan = await pool.fetchrow(
            f"""
            SELECT e.id, e.canonical_name
            FROM public.entities e
            WHERE e.id = $1
              AND e.metadata->>'proposed_source' = 'email_identity_enrichment'
              AND NOT EXISTS (
                  SELECT 1 FROM {schema}.entity_facts ef WHERE ef.subject = e.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM {schema}.entity_facts ef
                  WHERE ef.object = e.id::text AND ef.object_kind = 'entity'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM {schema}.pending_actions pa
                  WHERE pa.tool_args->>'subject' = e.id::text
                    AND pa.status IN ('pending', 'approved', 'executed')
                    AND pa.id <> ALL($2::uuid[])
              )
              {contacts_clause}
            """,
            parsed,
            expire_ids,
        )
        if orphan is None:
            summary["entities_kept"] += 1
            logger.info(
                "keep entity=%s — not a clean orphan (has facts/contact/other live action, "
                "or missing enrichment marker)",
                entity_id,
            )
            continue

        logger.info(
            "%s orphan entity=%s (%r)",
            "would delete" if not apply else "deleting",
            entity_id,
            orphan["canonical_name"],
        )
        if apply:
            await pool.execute("DELETE FROM public.entities WHERE id = $1", parsed)
            summary["entities_deleted"] += 1

    return summary


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Retract email-identity-enrichment proposals for bulk/automated senders "
            "and remove their orphan placeholder entities (bu-qeaou)"
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually mutate the database. Without this flag the script is a dry run.",
    )
    parser.add_argument(
        "--schema",
        default="relationship",
        help="Butler schema owning pending_actions/entity_facts (default: relationship)",
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
        summary = await cleanup_bulk_email_identity_proposals(
            pool,
            schema=args.schema,
            apply=args.apply,
        )
    finally:
        await pool.close()

    print()
    print(f"Cleanup complete (mode={'APPLY' if args.apply else 'DRY RUN'}):")
    print(f"  Proposals scanned:        {summary['proposals_scanned']}")
    print(f"  Kept (human-looking):     {summary['kept_human']}")
    print(f"  Expired (bulk):           {summary['expired']}")
    print(f"  Orphan entities deleted:  {summary['entities_deleted']}")
    print(f"  Entities kept (not orphan): {summary['entities_kept']}")
    if not args.apply:
        print()
        print("  DRY RUN — no changes written. Re-run with --apply to execute.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
