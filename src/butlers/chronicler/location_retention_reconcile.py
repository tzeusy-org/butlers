"""Reconcile ready retention grants against the connector's committed evidence (bu-s11n0s.7 S4b-4b).

The connector deletes a grant's raw rows in one transaction that also writes a tombstone per
item and the batch receipt (``connectors.owntracks_retention_tombstones`` / ``_batches``,
core_342). Chronicler holds ``SELECT`` on both and on ``connectors.owntracks_points``, and here
checks what was actually committed. The connector's reply is not evidence; neither is anything a
caller passes in.

For each grant that is ``ready`` and neither ``reconciled`` nor ``failed`` (oldest first), in one
transaction:

* No receipt yet: nothing is written; the grant is ``pending``. Nothing here re-mints a deletion
  set, so a lost connector acknowledgement is simply read again on the next run.
* The receipt's item count equals the grant's, its tombstones are exactly the grant's items
  (raw id, logical key and digest), and none of those rows remains: ``reconciled``, with a
  count-only receipt (``items``, ``deleted``, ``already_forgotten``).
* Anything else: ``failed``, with a count-only receipt (``items``, ``tombstones``, ``matched``,
  ``present``).

States are appended through :func:`~butlers.chronicler.location_retention_grant.append_grant_state`
under its per-grant lock, so a concurrent run that settled the grant first makes this one skip
it. Dark: nothing calls :func:`reconcile_ready_grants`
(``tests/chronicler/test_location_retention_dark_guard.py``), and no grant can be ``ready``
before holder copies are recorded (S4c).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from butlers.chronicler.location_retention_grant import GrantStateRefused, append_grant_state

__all__ = ["DEFAULT_LIMIT", "Reconciliation", "reconcile_ready_grants"]

logger = logging.getLogger(__name__)

#: Grants one run reconciles at most; the rest wait for the next run.
DEFAULT_LIMIT = 20

_READY = """
SELECT g.id
FROM location_retention_grants AS g
WHERE EXISTS (
    SELECT 1 FROM location_retention_grant_states AS s
    WHERE s.grant_id = g.id AND s.state = 'ready'
)
  AND NOT EXISTS (
    SELECT 1 FROM location_retention_grant_states AS s
    WHERE s.grant_id = g.id AND s.state IN ('reconciled', 'failed')
)
ORDER BY g.prepared_at, g.id
LIMIT $1
"""
_ITEMS = (
    "SELECT raw_source_id, logical_source_key, content_digest "
    "FROM location_retention_grant_items WHERE grant_id = $1"
)
_BATCH = (
    "SELECT item_count, deleted_count, already_forgotten_count "
    "FROM connectors.owntracks_retention_batches WHERE grant_id = $1"
)
_TOMBSTONES = (
    "SELECT raw_source_id, logical_source_key, content_digest "
    "FROM connectors.owntracks_retention_tombstones WHERE grant_id = $1"
)
_PRESENT = (
    "SELECT count(*) FROM connectors.owntracks_points "
    "WHERE id = ANY($1::uuid[]) OR idempotency_key = ANY($2::text[])"
)

Outcome = Literal["reconciled", "failed", "pending"]


@dataclass(frozen=True)
class Reconciliation:
    """What one grant's reconciliation found; counts only."""

    grant_id: UUID
    state: Outcome
    receipt: dict[str, int] = field(default_factory=dict)


def _triples(rows: list[Any]) -> set[tuple[UUID, str, str]]:
    return {(r["raw_source_id"], r["logical_source_key"], r["content_digest"]) for r in rows}


async def _reconcile_one(conn: Any, grant_id: UUID) -> Reconciliation | None:
    async with conn.transaction():
        batch = await conn.fetchrow(_BATCH, grant_id)
        if batch is None:
            return Reconciliation(grant_id, "pending")
        items = _triples(await conn.fetch(_ITEMS, grant_id))
        tombstones = _triples(await conn.fetch(_TOMBSTONES, grant_id))
        present = await conn.fetchval(_PRESENT, [i[0] for i in items], [i[1] for i in items])
        if batch["item_count"] == len(items) and tombstones == items and present == 0:
            state: Outcome = "reconciled"
            receipt = {
                "items": len(items),
                "deleted": batch["deleted_count"],
                "already_forgotten": batch["already_forgotten_count"],
            }
        else:
            state = "failed"
            receipt = {
                "items": len(items),
                "tombstones": len(tombstones),
                "matched": len(items & tombstones),
                "present": present,
            }
        try:
            await append_grant_state(conn, grant_id, state, receipt=receipt)
        except GrantStateRefused:
            return None  # settled by a concurrent run
    return Reconciliation(grant_id, state, receipt)


async def reconcile_ready_grants(conn: Any, *, limit: int = DEFAULT_LIMIT) -> list[Reconciliation]:
    """Reconcile up to ``limit`` ready, unsettled grants, oldest first.

    ``conn`` is a Chronicler connection. Raises ``ValueError`` for a bad limit. A grant a
    concurrent run settled first is left out of the result.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    outcomes: list[Reconciliation] = []
    for row in await conn.fetch(_READY, limit):
        outcome = await _reconcile_one(conn, row["id"])
        if outcome is not None:
            outcomes.append(outcome)
    settled = [o for o in outcomes if o.state != "pending"]
    if settled:
        logger.info(
            "location retention: %d grants reconciled, %d failed, %d pending",
            sum(o.state == "reconciled" for o in outcomes),
            sum(o.state == "failed" for o in outcomes),
            sum(o.state == "pending" for o in outcomes),
        )
    return outcomes
