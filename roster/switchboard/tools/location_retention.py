"""Switchboard forgets its copies of expired OwnTracks fixes (bu-s11n0s.7, slice S4c-2). Dark.

Switchboard keeps every accepted OwnTracks envelope in ``message_inbox``; its
``normalized_text`` carries the fix's exact coordinates even at the metadata tier. Before
Chronicler lets the connector delete a raw fix, Switchboard forgets these copies under its own
role (``sw_081``):

* :func:`forget_location_copies` takes ``location_retention_forget_lock()`` and, in one
  transaction, finds every OwnTracks inbox row whose ingest-stored
  ``request_context.idempotency_key`` is one of the grant's logical keys (exact equality).
* A row Switchboard handled by policy alone (``lifecycle_state = 'skipped'``, no session, a
  ``skip`` policy bypass) has its ``normalized_text`` and ``raw_payload.payload`` replaced by
  :data:`WITHHELD_TEXT`, and ``request_context.content_hash_key`` (a short hash of the text and
  sender, brute-forceable back to the fix) is dropped; everything else, including the dedupe
  key and lifecycle audit, stays. A row in any other state may still be processing or has been
  routed onward into sessions this cannot reach truthfully, so it is left as it is and its key
  counts as blocked.
* Every key that is not blocked (including a key with no inbox row) is tombstoned, so the
  ``message_inbox`` insert trigger withholds a later envelope for it.
* One count-only receipt is appended: ``complete`` when nothing is blocked (once per grant; a
  repeat returns it, ``replayed``), else ``blocked`` (may repeat until the block clears).

It never decides what to forget: the keys come from Chronicler's grant (the relay in S4c-3).
Nothing calls it yet.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

#: What a forgotten copy's text becomes (also written by sw_081's insert trigger).
WITHHELD_TEXT = "[location withheld: raw retention]"

_LOCK = "SELECT switchboard.location_retention_forget_lock()"
_COMPLETE = """
SELECT outcome, item_count, forgotten_count, already_forgotten_count, absent_count, blocked_count
FROM switchboard.location_retention_forgetting
WHERE grant_id = $1 AND outcome = 'complete'
"""
_COPIES = f"""
SELECT received_at, id, request_context ->> 'idempotency_key' AS key,
       normalized_text = '{WITHHELD_TEXT}' AS forgotten,
       lifecycle_state = 'skipped'
           AND session_id IS NULL
           AND decomposition_output ->> 'triage_decision' = 'skip'
           AND decomposition_output -> 'policy_bypass' = 'true'::jsonb AS policy_only
FROM switchboard.message_inbox
WHERE request_context ->> 'source_channel' = 'owntracks'
  AND request_context ->> 'idempotency_key' = ANY($1::text[])
FOR UPDATE
"""
_WITHHOLD = """
UPDATE switchboard.message_inbox
SET normalized_text = $3,
    raw_payload = jsonb_set(raw_payload, '{payload}',
        jsonb_build_object('raw', NULL, 'normalized_text', $3::text)),
    request_context = request_context - 'content_hash_key',
    updated_at = now()
WHERE (received_at, id) IN (SELECT * FROM unnest($1::timestamptz[], $2::uuid[]))
"""
_TOMBSTONE = """
INSERT INTO switchboard.location_retention_forgotten_keys (logical_source_key, grant_id)
SELECT key, $2 FROM unnest($1::text[]) AS key
ON CONFLICT (logical_source_key) DO NOTHING
"""
_RECEIPT = """
INSERT INTO switchboard.location_retention_forgetting (grant_id, outcome, item_count,
    forgotten_count, already_forgotten_count, absent_count, blocked_count)
VALUES ($1, $2, $3, $4, $5, $6, $7)
"""


@dataclass(frozen=True)
class ForgettingReceipt:
    grant_id: UUID
    outcome: str
    item_count: int
    forgotten_count: int
    already_forgotten_count: int
    absent_count: int
    blocked_count: int
    replayed: bool = False


async def forget_location_copies(
    conn: Any, grant_id: UUID, keys: Sequence[str]
) -> ForgettingReceipt:
    """Forget every inbox copy of ``keys`` that policy alone handled; one transaction."""
    keys = list(dict.fromkeys(keys))
    async with conn.transaction():
        await conn.execute(_LOCK)
        existing = await conn.fetchrow(_COMPLETE, grant_id)
        if existing is not None:
            return ForgettingReceipt(grant_id, *existing.values(), replayed=True)

        rows = await conn.fetch(_COPIES, keys)
        by_key: dict[str, list[Any]] = {}
        for row in rows:
            by_key.setdefault(row["key"], []).append(row)
        blocked = {
            key
            for key, copies in by_key.items()
            if any(not c["forgotten"] and not c["policy_only"] for c in copies)
        }
        pending = [
            row
            for row in rows
            if row["key"] not in blocked and not row["forgotten"] and row["policy_only"]
        ]
        if pending:
            await conn.execute(
                _WITHHOLD,
                [row["received_at"] for row in pending],
                [row["id"] for row in pending],
                WITHHELD_TEXT,
            )
        withheld_now = {row["key"] for row in pending}
        clear = [key for key in keys if key not in blocked]
        await conn.execute(_TOMBSTONE, clear, grant_id)

        receipt = ForgettingReceipt(
            grant_id=grant_id,
            outcome="blocked" if blocked else "complete",
            item_count=len(keys),
            forgotten_count=len(withheld_now),
            already_forgotten_count=sum(
                1 for key in by_key if key not in blocked and key not in withheld_now
            ),
            absent_count=sum(1 for key in keys if key not in by_key),
            blocked_count=len(blocked),
        )
        await conn.execute(
            _RECEIPT,
            grant_id,
            receipt.outcome,
            receipt.item_count,
            receipt.forgotten_count,
            receipt.already_forgotten_count,
            receipt.absent_count,
            receipt.blocked_count,
        )
    return receipt
