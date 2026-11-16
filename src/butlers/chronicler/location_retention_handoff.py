"""What Chronicler offers the OwnTracks connector to delete (bu-s11n0s.7 S4b-3b).

:func:`ready_grant_payloads` reads the retention grants that may be executed: a grant with a
``ready`` state (holder copies read back) and no ``reconciled`` or ``failed`` one. ``unknown``
does not settle a grant, so it is offered again; the connector's receipt makes a repeat a no-op.

Each payload carries only the grant id, its cutoff and, per granted raw row, the raw id, logical
key and v1 content digest, oldest grant first. It is the exact shape
:func:`butlers.connectors.owntracks_retention.parse_grant` accepts: nothing about coordinates,
episodes or the owner crosses.

``ready`` is reached only through the holder collector (S4c-3).

Nothing is offered unless the owner's switch is on (``location_retention_policy.enforced``,
bu-s11n0s.75), read in the same statement: switching it off withdraws every ready grant from
the connector's next fetch, and a missing policy row offers nothing (fail closed). A grant the
connector already fetched before the switch went off may still execute.
"""

from __future__ import annotations

from typing import Any

#: Offered grants per read. A grant holds at most 500 rows.
DEFAULT_LIMIT = 5

_READY = """
SELECT g.id, g.cutoff
FROM location_retention_grants g
WHERE EXISTS (SELECT 1 FROM location_retention_policy p WHERE p.id = 1 AND p.enforced)
  AND EXISTS (
        SELECT 1 FROM location_retention_grant_states s
        WHERE s.grant_id = g.id AND s.state = 'ready'
    )
  AND NOT EXISTS (
        SELECT 1 FROM location_retention_grant_states s
        WHERE s.grant_id = g.id AND s.state IN ('reconciled', 'failed')
    )
ORDER BY g.prepared_at, g.id
LIMIT $1
"""
_ITEMS = """
SELECT grant_id, raw_source_id, logical_source_key, content_digest
FROM location_retention_grant_items
WHERE grant_id = ANY($1::uuid[])
ORDER BY grant_id, raw_source_id
"""


async def ready_grant_payloads(conn: Any, *, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """The executable grants, oldest first, as connector payloads (at most ``limit``)."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        grants = await conn.fetch(_READY, limit)
        if not grants:
            return []
        items = await conn.fetch(_ITEMS, [grant["id"] for grant in grants])
    by_grant: dict[Any, list[dict[str, str]]] = {grant["id"]: [] for grant in grants}
    for item in items:
        by_grant[item["grant_id"]].append(
            {
                "raw_source_id": str(item["raw_source_id"]),
                "logical_source_key": item["logical_source_key"],
                "content_digest": item["content_digest"],
            }
        )
    return [
        {
            "grant_id": str(grant["id"]),
            "cutoff": grant["cutoff"].isoformat(),
            "items": by_grant[grant["id"]],
        }
        for grant in grants
    ]
