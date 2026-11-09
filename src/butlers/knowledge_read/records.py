"""The generic records reader behind ``finance_records`` and ``health_records`` (RFC 0039 D10).

``kind`` is a closed enum generated from the manifest: the tables of one domain whose ``tools``
include the records tool. One call reads one table through its ``mcp_read`` view, so nothing
unpublished can be named. ``fields`` is the view's published columns; ``json_fields`` is the
row's published JSON paths from the domain's ``json_fields_public`` table (REQ-013).

``query`` is lexical: the view publishes ``search_vector`` as a jsonb string, which is cast back
to ``tsvector`` and matched with ``websearch_to_tsquery``; hits rank by ``ts_rank``.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import cache
from typing import Any

import asyncpg

from butlers.knowledge_read.core import (
    Arguments,
    ReadContext,
    ReadError,
    _manifest,
    decode_cursor,
    encode_cursor,
    fields,
    fit,
    in_snapshot,
    published,
    record_uri,
    span,
)
from butlers.knowledge_read.manifest import Table

RECORDS_MAX_DAYS = 366
RECORDS_LIMIT = 200
JSON_TABLE = "json_fields_public"
_TSV_COLUMN = "search_vector"


@cache
def record_kinds(domain: str, tool: str) -> dict[str, Table]:
    """Published tables of ``domain`` that ``tool`` reads, keyed by table name."""
    return {
        t.source.split(".", 1)[1]: t
        for t in _manifest().tables
        if t.domain == domain and tool in t.tools
    }


def _json_value(row: dict[str, Any]) -> Any:
    kind = row.get("value_type")
    if kind == "number":
        return row.get("value_numeric")
    if kind == "boolean":
        return row.get("value_text") == "true"
    if kind == "null":
        return None
    return row.get("value_text")


async def json_fields(
    conn: asyncpg.Connection, domain: str, source: str, ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Published JSON paths of rows of ``source``, by row id. Runs inside the caller's snapshot.

    The manifest assigns ``json_fields_public`` to the records tool only, so the lookup is
    declared under that tool; it is read-only and keyed by the exact source table and row id.
    """
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    _, view = published(f"{domain}.{JSON_TABLE}", f"{domain}_records")
    rows = await conn.fetch(
        f"""SELECT fields FROM {view}
        WHERE fields->>'source_table' = $1 AND fields->>'row_id' = ANY($2::text[])
        ORDER BY fields->>'row_id', fields->>'path'""",
        source,
        wanted,
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = fields(row["fields"])
        result.setdefault(data["row_id"], {})[data["path"]] = _json_value(data)
    return result


async def read_records(
    conn: asyncpg.Connection,
    context: ReadContext,
    arguments: object,
    *,
    tool: str,
    domain: str,
) -> dict[str, Any]:
    """One page of one published table of ``domain``, newest first."""
    kinds = record_kinds(domain, tool)
    args = Arguments(arguments, {"kind", "since", "until", "predicate", "query", "cursor", "limit"})
    kind = args.choice("kind", tuple(sorted(kinds)))
    table = kinds[kind]
    raw = arguments if isinstance(arguments, dict) else {}
    since = args.instant("since") if raw.get("since") is not None else None
    until = args.instant("until") if raw.get("until") is not None else None
    time_column = table.time_column
    if (since or until) and time_column is None:
        raise ReadError("invalid_argument", argument="since" if since else "until")
    if since and until:
        span(since.date(), until.date(), max_days=RECORDS_MAX_DAYS)
    predicate = args.text("predicate", max_length=128)
    query = args.text("query", max_length=256)
    if (predicate or query) and kind != "facts":
        raise ReadError("invalid_argument", argument="predicate" if predicate else "query")
    limit = args.integer("limit", low=1, high=RECORDS_LIMIT, default=50)
    mode = "rank" if query else "time" if time_column else "key"
    after = decode_cursor(context, tool, args.values, args.cursor())
    if after is not None:
        shape = {"rank": (float, str), "time": (str, str), "key": (str,)}[mode]
        if len(after) != len(shape) or any(
            not isinstance(x, shape[i]) or isinstance(x, bool) for i, x in enumerate(after)
        ):
            raise ReadError("invalid_argument", argument="cursor")
    context.require(domain)
    _, view = published(table.source, tool)
    declared = dict(table.types).get(time_column or "", "")
    cast = "date" if declared == "date" else "timestamptz"
    lower = since.date() if since and cast == "date" else since
    upper = until.date() if until and cast == "date" else until
    filters = f"""($2::{cast} IS NULL OR (fields->>$1::text)::{cast} >= $2::{cast})
          AND ($3::{cast} IS NULL OR (fields->>$1::text)::{cast} < $3::{cast})
          AND ($4::text IS NULL OR (fields->>'{_TSV_COLUMN}')::tsvector
               @@ websearch_to_tsquery('english', $4::text))
          AND ($5::text IS NULL OR fields->>'predicate' = $5::text)"""
    if mode == "rank":
        expr = (
            f"ts_rank((fields->>'{_TSV_COLUMN}')::tsvector, "
            "websearch_to_tsquery('english', $4::text))::float8"
        )
        page = (
            "($6::float8 IS NULL OR at < $6::float8 OR (at = $6::float8 AND source_id > $7::text))"
        )
        order = "at DESC, source_id"
    elif mode == "time":
        expr = f"COALESCE((fields->>$1::text)::{cast}, '-infinity')"
        page = f"($6::text IS NULL OR (at, source_id) < ($6::text::{cast}, $7::text))"
        order = "at DESC, source_id DESC"
    else:
        expr = "NULL::text"
        page = "(($6::text IS NULL AND $7::text IS NULL) OR source_id > $7::text)"
        order = "source_id"
    cursor_at: Any = None
    cursor_id: Any = None
    if after is not None:
        cursor_at, cursor_id = (None, after[0]) if mode == "key" else (after[0], after[1])

    async def read() -> tuple[list[asyncpg.Record], dict[str, dict[str, Any]]]:
        rows = await conn.fetch(
            f"""SELECT source_id, fields, at, at::text AS at_text FROM (
                SELECT source_id, fields, {expr} AS at FROM {view} WHERE {filters}) AS t
            WHERE {page} ORDER BY {order} LIMIT $8""",
            time_column,
            lower,
            upper,
            query,
            predicate,
            cursor_at,
            cursor_id,
            limit + 1,
        )
        if kind == JSON_TABLE:
            return rows, {}
        return rows, await json_fields(
            conn, domain, table.source, [r["source_id"] for r in rows[:limit]]
        )

    rows, extras = await in_snapshot(conn, read)
    more = len(rows) > limit
    items = []
    for row in rows[:limit]:
        data = fields(row["fields"])
        data.pop(_TSV_COLUMN, None)
        source_kind, uri = record_uri(table, row["source_id"])
        position = (
            [row["source_id"]]
            if mode == "key"
            else [row["at"] if mode == "rank" else row["at_text"], row["source_id"]]
        )
        items.append(
            (
                position,
                {
                    "kind": kind,
                    "id": row["source_id"],
                    "fields": data,
                    "json_fields": extras.get(row["source_id"], {}),
                    "source": context.source(source_kind, uri, data.get("content_authority")),
                },
            )
        )

    def build(kept: list, cut: bool) -> dict[str, Any]:
        cursor = None
        if kept and (more or cut):
            cursor = encode_cursor(context, tool, args.values, kept[-1][0])
        return {"items": [entry[1] for entry in kept], "next_cursor": cursor}

    return fit(items, build)
