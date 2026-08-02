#!/usr/bin/env python3
"""Recover per-sender identities on historical batch message_inbox rows.

Background
----------
Buffered chat envelopes (WhatsApp and Telegram user clients) collapse
``sender.identity`` to the literal string ``"multiple"`` because one flush
spans several senders.  The real per-sender identities were carried on
``sender.participants``, but ``ingest_v1`` dropped that field instead of
writing it to ``request_context``.  ``interaction_sync`` therefore resolved
every batch against the literal ``"multiple"``, matched nobody, and created
zero interaction facts — silently pinning the owner's most-contacted people to
Dunbar tier 1500 (specs ``passive-interaction-sync``, ``dunbar-tier-scoring``).

The forward fix populates ``request_context.source_sender_identities`` on new
rows.  This script repairs the historical ones, whose senders survive only
inside ``raw_payload``:

  * ``telegram_user_client`` → ``payload.raw.conversation_history[].sender_id``
    (bare numeric ids; the resolver applies the canonical ``telegram:`` prefix)
  * ``whatsapp_user_client`` → ``payload.raw.events[].raw.sender``, translating
    opaque ``<lid>@lid`` identifiers through ``public.whatsmeow_lid_map`` into
    the ``<phone>@s.whatsapp.net`` form that contact resolution can match

Rows that already carry ``source_sender_identities`` are skipped, so the script
is idempotent and safe to re-run.

After a successful run, re-run the relationship butler's ``interaction_sync``
job with a widened scan window to mint the recovered interaction facts.  That
job is itself idempotent — ``interaction_log()`` deduplicates on
``(entity_id, predicate, valid_at)`` — so re-running cannot double-count.

Usage
-----
Dry run (reports counts, makes no changes)::

    uv run python scripts/backfill_batch_sender_identities.py --dry-run

Apply::

    uv run python scripts/backfill_batch_sender_identities.py

Target a single channel::

    uv run python scripts/backfill_batch_sender_identities.py \\
        --channel whatsapp_user_client

Environment
-----------
BUTLERS_DATABASE_URL
    Required asyncpg DSN, e.g.
    ``postgresql://user:pass@localhost:5432/butlers``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

import asyncpg

logger = logging.getLogger("backfill_batch_sender_identities")

CHANNELS = ("telegram_user_client", "whatsapp_user_client")

BATCH_SIZE = 500

# Selects candidate rows and derives the sender list per channel in one pass.
# COALESCE guards a NULL aggregate (a row whose payload holds no usable
# senders) so such rows surface as empty and are reported rather than written.
SELECT_SQL = """
SELECT
    mi.id,
    mi.received_at,
    mi.request_context ->> 'source_channel' AS channel,
    CASE
        WHEN mi.request_context ->> 'source_channel' = 'telegram_user_client' THEN (
            SELECT array_agg(DISTINCT c ->> 'sender_id')
            FROM jsonb_array_elements(
                mi.raw_payload -> 'payload' -> 'raw' -> 'conversation_history'
            ) AS c
            WHERE c ->> 'sender_id' IS NOT NULL
              AND c ->> 'sender_id' <> ''
        )
        ELSE (
            -- The device ordinal in "<user>:<device>@<server>" identifies a
            -- handset, not a person, so it is stripped before the LID lookup
            -- and before the identity is emitted. The LID join is restricted
            -- to actual "@lid" senders so a lid numerically equal to a phone
            -- can never rewrite an already-valid phone JID.
            SELECT array_agg(DISTINCT COALESCE(
                       lm.pn || '@s.whatsapp.net',
                       j.user_part || '@' || j.server))
            FROM jsonb_array_elements(mi.raw_payload -> 'payload' -> 'raw' -> 'events') AS ev
            CROSS JOIN LATERAL (
                SELECT split_part(split_part(ev -> 'raw' ->> 'sender', '@', 1), ':', 1)
                           AS user_part,
                       split_part(ev -> 'raw' ->> 'sender', '@', 2)
                           AS server
            ) AS j
            LEFT JOIN public.whatsmeow_lid_map lm
                   ON j.server = 'lid' AND lm.lid = j.user_part
            WHERE ev -> 'raw' ->> 'sender' IS NOT NULL
              AND ev -> 'raw' ->> 'sender' <> ''
              AND j.user_part <> ''
              AND j.server <> ''
        )
    END AS senders
FROM switchboard.message_inbox mi
WHERE mi.request_context ->> 'source_sender_identity' = 'multiple'
  AND mi.request_context -> 'source_sender_identities' IS NULL
  AND mi.request_context ->> 'source_channel' = ANY($1::text[])
-- id breaks received_at ties: both modes page with OFFSET, so an unstable
-- sort between queries could skip a row.
ORDER BY mi.received_at, mi.id
LIMIT $2 OFFSET $3
"""

# received_at is part of the partitioned table's primary key, so it is included
# in the predicate to let the planner prune to a single partition.
UPDATE_SQL = """
UPDATE switchboard.message_inbox
SET request_context = request_context || $3::jsonb
WHERE id = $1 AND received_at = $2
"""


async def _process(
    pool: asyncpg.Pool,
    channels: list[str],
    *,
    dry_run: bool,
) -> dict[str, int]:
    stats = {"scanned": 0, "updated": 0, "empty": 0}
    # Rows left deliberately untouched (no recoverable sender) keep matching the
    # candidate query forever, so the read window must step past them or the
    # loop never terminates.  A dry run writes nothing at all, so every scanned
    # row is "left behind" and the window advances by the whole batch.
    offset = 0

    while True:
        rows = await pool.fetch(SELECT_SQL, channels, BATCH_SIZE, offset)
        if not rows:
            break

        for row in rows:
            stats["scanned"] += 1
            senders = [s for s in (row["senders"] or []) if s]
            if not senders:
                # No recoverable identity in the payload; leave the row alone so
                # a later, better extractor can still find it.
                stats["empty"] += 1
                offset += 1
                logger.debug("no senders recoverable for row %s", row["id"])
                continue

            if dry_run:
                stats["updated"] += 1
                offset += 1
                continue

            await pool.execute(
                UPDATE_SQL,
                row["id"],
                row["received_at"],
                json.dumps({"source_sender_identities": sorted(senders)}),
            )
            stats["updated"] += 1

    return stats


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    parser.add_argument(
        "--channel",
        action="append",
        choices=CHANNELS,
        help="Limit to one channel (repeatable). Defaults to all batch channels.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    dsn = os.environ.get("BUTLERS_DATABASE_URL")
    if not dsn:
        logger.error("BUTLERS_DATABASE_URL is not set")
        return 2

    channels = args.channel or list(CHANNELS)

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    if pool is None:
        logger.error("failed to create connection pool")
        return 2

    try:
        stats = await _process(pool, channels, dry_run=args.dry_run)
    finally:
        await pool.close()

    mode = "DRY RUN — no changes written" if args.dry_run else "applied"
    logger.info(
        "%s: scanned=%d updated=%d no_senders_recoverable=%d (channels=%s)",
        mode,
        stats["scanned"],
        stats["updated"],
        stats["empty"],
        ",".join(channels),
    )
    if not args.dry_run and stats["updated"]:
        logger.info(
            "Re-run the relationship butler's interaction_sync with a widened "
            "scan window to mint interaction facts from the recovered senders."
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
