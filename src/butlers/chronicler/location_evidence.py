"""Typed diminished references from actual owning permanent point tombstones."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg


async def expired_evidence_links(pool: asyncpg.Pool, episode_id: UUID) -> list[dict[str, Any]]:
    # Optional standalone Chronicler fixtures are supported explicitly. An
    # installed relation with malformed columns must propagate, never be empty.
    exists = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
        "WHERE table_schema=current_schema() AND table_name='location_expired_evidence_links')"
    )
    if exists is False:
        return []
    rows = await pool.fetch(
        """SELECT t.event_id,t.occurred_at,t.privacy,t.decision_id,t.spatial_precision_m,l.relation
           FROM location_expired_evidence_links l JOIN location_evidence_tombstones t
             ON t.event_id=l.event_id WHERE l.episode_id=$1 ORDER BY t.occurred_at,t.event_id""",
        episode_id,
    )
    return [
        {
            "event_id": str(row["event_id"]),
            "source_name": "owntracks.points",
            "event_type": "location",
            "occurred_at": row["occurred_at"],
            "relation": row["relation"],
            "descriptor": "Exact location evidence forgotten",
            "privacy": row["privacy"],
            "retention_state": "forgotten",
            "spatial_precision_m": row["spatial_precision_m"],
            "retention_receipt": str(row["decision_id"]),
        }
        for row in rows
    ]
