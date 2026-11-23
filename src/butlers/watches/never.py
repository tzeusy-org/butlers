"""The owner's ``never`` on a watch notice pauses that watch (REQ-owner-watches-004; dark).

``never`` is a narrowing: it pauses the watch, keeping everything the watch observed, and it
never deletes. It runs after the insight feedback has committed (the broker's family cooldown
is the immediate effect) and only for a reply whose origin the caller proved to be the owner:
the authenticated dashboard route passes ``owner``; a model-callable tool passes nothing, and
nothing here widens, renews or creates a watch. Pausing is idempotent, so a replay is harmless.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from butlers.watches.approval import PROVIDERS, ServiceFor
from butlers.watches.delivery import CATEGORY, ORIGIN
from butlers.watches.model import OWNER, WatchRefused

__all__ = ["pause_on_never"]


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value


async def pause_on_never(
    pool: Any,
    insight_id: str,
    *,
    origin: str,
    service_for: ServiceFor | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Pause the watch an owner-notice insight belongs to; ``ignored`` for anything else."""
    if origin != OWNER:
        return {"status": "ignored", "reason": "not_owner"}
    row = await pool.fetchrow(
        "SELECT origin_butler, category, dedup_key, metadata FROM insight_candidates "
        "WHERE id = $1::uuid",
        insight_id,
    )
    if row is None or row["origin_butler"] != ORIGIN or row["category"] != CATEGORY:
        return {"status": "ignored", "reason": "not_a_watch_notice"}
    metadata = _json(row["metadata"])
    watch_id = metadata.get("watch_id") if isinstance(metadata, dict) else None
    if type(watch_id) is not str or not str(row["dedup_key"]).startswith(
        f"{CATEGORY}:{watch_id}:g"
    ):
        return {"status": "ignored", "reason": "not_a_watch_notice"}
    service = (service_for or PROVIDERS.service_for)()
    if service is None:
        return {"status": "unavailable"}
    try:
        await service.pause(watch_id, origin=OWNER, now=now or datetime.now(UTC))
    except WatchRefused as exc:
        if str(exc) == "the watch is closed":
            return {"status": "already_closed"}
        return {"status": "ignored", "reason": "unknown_watch"}
    return {"status": "paused"}
