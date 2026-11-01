"""Complete, bounded dashboard admission of Messenger review references."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from butlers.api.db import DatabaseManager
from butlers.core.approval_review import ApprovalReview

_MAX_REVIEW_IDS = 200
_MAX_REVIEW_SOURCES = 64
_REVIEW_READ_TIMEOUT_S = 3.0


async def resolve_messenger_reviews(
    db: DatabaseManager, action_ids: set[UUID]
) -> dict[UUID, tuple[str, Any]]:
    """Return only IDs found exactly once, in Messenger, after every source read.

    No cache or first-match admission: every call repeats the complete reads.
    Unknown module metadata falls back to catalog-probing every configured pool.
    Any failed enumeration/probe/read withholds the whole batch. Cancellation
    remains cancellation. SQL reads are confined to the dashboard's own pools.
    """
    if not action_ids or len(action_ids) > _MAX_REVIEW_IDS:
        return {}
    try:
        configured = db.configured_butlers_with_module("approvals")
        if configured is None and not db.configured_butler_names:
            configured = db.butlers_with_module("approvals")
        known_modules = configured is not None
        names = configured if known_modules else (db.configured_butler_names or db.butler_names)
        if (
            not isinstance(names, list)
            or not names
            or len(names) > _MAX_REVIEW_SOURCES
            or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names)
            or "messenger" not in names
        ):
            return {}
        pools = [(name, db.pool(name), db.schema_for_butler(name)) for name in names]
        if any(schema is not None and not isinstance(schema, str) for _, _, schema in pools):
            return {}
    except Exception:
        return {}

    async def read_source(name: str, pool: Any, schema: str | None):
        table = (
            '"' + schema.replace('"', '""') + '".pending_actions'
            if schema is not None
            else "pending_actions"
        )
        async with asyncio.timeout(_REVIEW_READ_TIMEOUT_S), pool.acquire() as conn:
            if not known_modules and not await conn.fetchval(
                "SELECT to_regclass($1) IS NOT NULL", table
            ):
                return name, pool, []
            rows = await conn.fetch(
                f"SELECT id FROM {table} WHERE id = ANY($1::uuid[])",
                sorted(action_ids, key=str),
            )
            ids = [row["id"] for row in rows]
            if any(not isinstance(value, UUID) or value not in action_ids for value in ids):
                raise ValueError("Invalid review lookup identity")
            return name, pool, ids

    results = await asyncio.gather(
        *(read_source(*source) for source in pools), return_exceptions=True
    )
    if any(isinstance(result, BaseException) for result in results):
        return {}
    matches: dict[UUID, list[tuple[str, Any]]] = {value: [] for value in action_ids}
    for name, pool, ids in results:
        for value in ids:
            matches[value].append((name, pool))
    return {
        value: sources[0]
        for value, sources in matches.items()
        if len(sources) == 1 and sources[0][0] == "messenger"
    }


def stored_review(row: Any) -> Any:
    """Read only the dedicated stored column, never legacy metadata or error text."""
    try:
        return row["approval_review"]
    except (KeyError, IndexError):
        return None


async def project_notification_reviews(db: DatabaseManager | None, rows: list[Any]):
    """Map a bounded notification page to its row-local review states."""
    raw = [stored_review(row) for row in rows]
    refs = [ApprovalReview.parse(value) for value in raw]
    admitted = (
        await resolve_messenger_reviews(db, {ref.action_id for ref in refs if ref is not None})
        if db is not None and any(ref is not None for ref in refs)
        else {}
    )
    return [
        (ref.as_dict(), "available")
        if ref is not None and ref.action_id in admitted
        else (None, "none" if value is None else "unavailable")
        for value, ref in zip(raw, refs, strict=True)
    ]
