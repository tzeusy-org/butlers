"""``timeline`` and ``time_use`` over published Chronicler episodes, rollups and trip legs.

Only ``chronicler.episodes_public`` and ``rollups_public`` are published, never raw episodes,
payloads or overrides (REQ-public-mcp-projection-006): a restricted or tombstoned episode is
absent from both, so neither reader filters privacy itself.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from butlers.knowledge_read.core import (
    Arguments,
    ReadContext,
    ReadError,
    decode_cursor,
    encode_cursor,
    fields,
    fit,
    in_snapshot,
    published,
    record_uri,
    span,
)

TIMELINE_MAX_DAYS = 92
TIMELINE_LIMIT = 200
TIME_USE_MAX_DAYS = 366
TIME_USE_POINTS = 400
LEG_KIND = "trip_leg"
_TIMELINE_DOMAINS = ("time", "travel")


def _leg_title(leg: dict[str, Any]) -> str:
    route = " to ".join(x for x in (leg.get("departure_city"), leg.get("arrival_city")) if x)
    parts = [leg.get("type"), leg.get("carrier"), route]
    return " ".join(p for p in parts if p) or "Trip leg"


async def timeline(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Published episodes and trip legs starting in ``[start, end)``, oldest first, paged.

    A disabled domain's items are omitted and the domain is listed in ``sources_degraded``.
    """
    args = Arguments(arguments, {"start", "end", "kinds", "cursor", "limit"})
    start, end = args.instant("start"), args.instant("end")
    span(start, end, max_days=TIMELINE_MAX_DAYS)
    kinds = args.subset("kinds", max_items=20, max_length=64)
    limit = args.integer("limit", low=1, high=TIMELINE_LIMIT, default=100)
    after = decode_cursor(context, "timeline", args.values, args.cursor())
    if after is not None and (len(after) != 2 or not all(isinstance(x, str) for x in after)):
        raise ReadError("invalid_argument", argument="cursor")
    after_at = None if after is None else datetime.fromisoformat(after[0])
    # Multi-domain: a disabled domain is omitted and reported, never a refusal.
    if context.freshness == "stale":
        raise ReadError("projection_stale")
    episodes, episode_view = published("chronicler.episodes_public", "timeline")
    legs, leg_view = published("travel.legs", "timeline")
    selects = []
    if context.enabled("time"):
        selects.append(f"""SELECT 'time' AS domain, source_id, fields,
            (fields->>'started_at')::timestamptz AS at, fields->>'kind' AS kind
            FROM {episode_view}""")
    if context.enabled("travel"):
        selects.append(f"""SELECT 'travel', source_id, fields,
            (fields->>'departure_at')::timestamptz, '{LEG_KIND}' FROM {leg_view}""")

    async def read() -> list[asyncpg.Record]:
        if not selects:
            return []
        return await conn.fetch(
            f"""SELECT domain, source_id, fields, at FROM ({" UNION ALL ".join(selects)}) AS t
            WHERE at >= $1 AND at < $2 AND ($3::text[] IS NULL OR kind = ANY($3::text[]))
              AND ($4::timestamptz IS NULL OR (at, source_id) > ($4::timestamptz, $5::text))
            ORDER BY at, source_id LIMIT $6""",
            start,
            end,
            list(kinds) if kinds else None,
            after_at,
            None if after is None else after[1],
            limit + 1,
        )

    rows = await in_snapshot(conn, read)
    more = len(rows) > limit
    items = []
    for row in rows[:limit]:
        data = fields(row["fields"])
        if row["domain"] == "time":
            kind, uri = record_uri(episodes, row["source_id"])
            item = {
                "at": data.get("started_at"),
                "end": data.get("ended_at"),
                "kind": data.get("kind"),
                "title": data.get("title"),
            }
        else:
            kind, uri = record_uri(legs, row["source_id"])
            item = {
                "at": data.get("departure_at"),
                "end": data.get("arrival_at"),
                "kind": LEG_KIND,
                "title": _leg_title(data),
            }
        item["domain"] = row["domain"]
        item["source"] = context.source(kind, uri)
        items.append((row["at"].isoformat(), row["source_id"], item))
    degraded = context.degraded(_TIMELINE_DOMAINS)

    def build(kept: list, cut: bool) -> dict[str, Any]:
        cursor = None
        if kept and (more or cut):
            cursor = encode_cursor(context, "timeline", args.values, list(kept[-1][:2]))
        return {
            "items": [entry[2] for entry in kept],
            "next_cursor": cursor,
            "sources_degraded": degraded,
        }

    return fit(items, build)


async def time_use(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Minutes per bucket and category or kind from published rollups, ``start``..``end``.

    Days are the rollups' local days; ``week`` starts on Monday. At most 400 points; a longer
    series is cut and marked ``truncated``.
    """
    args = Arguments(arguments, {"start", "end", "bucket", "group_by"})
    start, end = args.day("start"), args.day("end")
    span(start, end, max_days=TIME_USE_MAX_DAYS)
    bucket = args.choice("bucket", ("day", "week", "month"))
    group_by = args.choice("group_by", ("category", "kind"))
    context.require("time")
    _, view = published("chronicler.rollups_public", "time_use")

    async def read() -> list[asyncpg.Record]:
        return await conn.fetch(
            f"""SELECT date_trunc($1, (fields->>'local_date')::date)::date AS bucket_start,
                   fields->>'group_key' AS grp, sum((fields->>'minutes')::bigint) AS minutes
            FROM {view}
            WHERE fields->>'group_kind' = $2
              AND (fields->>'local_date')::date BETWEEN $3 AND $4
            GROUP BY 1, 2 ORDER BY 1, 2 NULLS LAST LIMIT $5""",
            bucket,
            group_by,
            start,
            end,
            TIME_USE_POINTS + 1,
        )

    rows = await in_snapshot(conn, read)
    result: dict[str, Any] = {
        "series": [
            {
                "bucket_start": r["bucket_start"].isoformat(),
                "group": r["grp"],
                "minutes": int(r["minutes"]),
            }
            for r in rows[:TIME_USE_POINTS]
        ],
        "source": context.aggregate_source("time", "time_use"),
    }
    if len(rows) > TIME_USE_POINTS:
        result["truncated"] = True
    return result
