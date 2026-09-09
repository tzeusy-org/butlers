"""capture_search -- bounded keyword search over collection_items.

Retires the unbounded JSONB-containment scan ``item_search`` was limited to
for text search: this queries the generated ``search_vector`` tsvector
(``gen_004_collection_items_search_vector``) with a keyset cursor. A
pre-migration butler (column/index absent) degrades to the old containment
scan and says so in the return payload rather than silently returning
nothing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import asyncpg

from butlers.core.keyset_cursor import decode_cursor as _decode_cursor
from butlers.core.keyset_cursor import encode_cursor as _encode_cursor

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def _row_to_result(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    if isinstance(d.get("data"), str):
        d["data"] = json.loads(d["data"])
    if isinstance(d.get("tags"), str):
        d["tags"] = json.loads(d["tags"])
    return d


async def capture_search(
    pool: asyncpg.Pool,
    query: str,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = _DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Keyword-search collection items, bounded by limit and a keyset cursor.

    Returns ``{"items": [...], "next_cursor": str | None, "has_more": bool,
    "degraded": bool, "degraded_reason": str | None}``. ``degraded=True``
    means the tsvector search index was unavailable and a slower JSONB
    containment fallback was used instead -- results are still correct, just
    not full-text ranked.
    """
    bounded_limit = max(1, min(limit, _MAX_LIMIT))

    cursor_created_at: datetime | None = None
    cursor_id: str | None = None
    if cursor:
        cursor_created_at, cursor_id = _decode_cursor(cursor)

    try:
        items = await _search_vector_path(
            pool, query, date_from, date_to, bounded_limit, cursor_created_at, cursor_id
        )
        degraded = False
        degraded_reason = None
    except asyncpg.UndefinedColumnError:
        logger.warning(
            "capture_search: search_vector column absent (pre-migration butler);"
            " degrading to containment search"
        )
        items = await _containment_path(
            pool, query, date_from, date_to, bounded_limit, cursor_created_at, cursor_id
        )
        degraded = True
        degraded_reason = "search_vector_unavailable"

    has_more = len(items) > bounded_limit
    page = items[:bounded_limit]
    next_cursor = (
        _encode_cursor(page[-1]["created_at"], page[-1]["id"]) if has_more and page else None
    )

    return {
        "items": page,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "degraded": degraded,
        "degraded_reason": degraded_reason,
    }


async def _search_vector_path(
    pool: asyncpg.Pool,
    query: str,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
    cursor_created_at: datetime | None,
    cursor_id: str | None,
) -> list[dict[str, Any]]:
    conditions = ["e.search_vector @@ plainto_tsquery('english', $1)"]
    params: list[Any] = [query]
    idx = 2

    if date_from is not None:
        conditions.append(f"e.created_at >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to is not None:
        conditions.append(f"e.created_at <= ${idx}")
        params.append(date_to)
        idx += 1
    if cursor_created_at is not None and cursor_id is not None:
        conditions.append(f"(e.created_at, e.id) < (${idx}, ${idx + 1})")
        params.append(cursor_created_at)
        params.append(cursor_id)
        idx += 2

    params.append(limit + 1)
    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""
        SELECT e.id, e.collection_id, c.name AS collection_name, e.data, e.tags,
               e.created_at, e.updated_at,
               ts_rank(e.search_vector, plainto_tsquery('english', $1)) AS rank
        FROM collection_items e
        JOIN collections c ON c.id = e.collection_id
        WHERE {where}
        ORDER BY e.created_at DESC, e.id DESC
        LIMIT ${idx}
        """,
        *params,
    )
    return [_row_to_result(r) for r in rows]


async def _containment_path(
    pool: asyncpg.Pool,
    query: str,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
    cursor_created_at: datetime | None,
    cursor_id: str | None,
) -> list[dict[str, Any]]:
    conditions = ["e.data::text ILIKE '%' || $1 || '%'"]
    params: list[Any] = [query]
    idx = 2

    if date_from is not None:
        conditions.append(f"e.created_at >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to is not None:
        conditions.append(f"e.created_at <= ${idx}")
        params.append(date_to)
        idx += 1
    if cursor_created_at is not None and cursor_id is not None:
        conditions.append(f"(e.created_at, e.id) < (${idx}, ${idx + 1})")
        params.append(cursor_created_at)
        params.append(cursor_id)
        idx += 2

    params.append(limit + 1)
    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""
        SELECT e.id, e.collection_id, c.name AS collection_name, e.data, e.tags,
               e.created_at, e.updated_at
        FROM collection_items e
        JOIN collections c ON c.id = e.collection_id
        WHERE {where}
        ORDER BY e.created_at DESC, e.id DESC
        LIMIT ${idx}
        """,
        *params,
    )
    return [_row_to_result(r) for r in rows]
