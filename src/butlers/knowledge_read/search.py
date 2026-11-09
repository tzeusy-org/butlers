"""``search``, ``fetch`` and ``search_filtered`` over the published index (RFC 0039 D10, D11).

The index is the union of the published ``knowledge``, ``relationship``, ``time`` and ``travel``
sources the manifest assigns to ``search``; finance, health and proposal records are never in
it, so a broad search cannot sweep the ledger. Ranking is lexical: ``websearch_to_tsquery``
over each fact's replicated ``search_vector`` (a subscriber-side ``tsvector`` elsewhere) plus
trigram similarity on the title. No model runs.

An item's ``id`` is its ``butlers://`` uri, so ``fetch`` resolves exactly what ``search``
returned; ``proposal:<uuid>`` fetches a proposal's state and nothing else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import cache
from typing import Any

import asyncpg

from butlers.knowledge_read.chronology import leg_title
from butlers.knowledge_read.core import (
    Arguments,
    ReadContext,
    ReadError,
    decode_cursor,
    encode_cursor,
    fields,
    in_snapshot,
    published,
    record_uri,
    tables_for,
)
from butlers.knowledge_read.manifest import Table

SEARCH_LIMIT = 20
FILTERED_LIMIT = 50
QUERY_MAX = 256
TITLE_MAX = 200
SNIPPET_MAX = 240
FETCH_TEXT_BYTES = 16 * 1024
TRIGRAM_THRESHOLD = 0.3
INDEX_DOMAINS = ("knowledge", "relationship", "time", "travel")
_TS_CONFIG = "english"
_TRIGRAM_MISSING = (
    asyncpg.UndefinedFunctionError,
    asyncpg.InvalidSchemaNameError,
    asyncpg.InsufficientPrivilegeError,
)


def _vector(*keys: str) -> str:
    return f"to_tsvector('{_TS_CONFIG}', concat_ws(' ', {', '.join(f'f->>{k!r}' for k in keys)}))"


def _utc_day(key: str) -> str:
    return f"((f->>'{key}')::date::timestamp AT TIME ZONE 'UTC')"


# Per-source SQL over a view's ``fields`` (f): the ranked title, lexical vector and item time.
# A fact written without a search_vector is ranked on its content instead.
_FACT_SQL = (
    "f->>'content'",
    f"COALESCE((f->>'search_vector')::tsvector, to_tsvector('{_TS_CONFIG}', "
    "COALESCE(f->>'content', '')))",
    "(f->>'valid_at')::timestamptz",
)
_SQL: dict[str, tuple[str, str, str]] = {
    "public.entities": (
        "f->>'canonical_name'",
        f"to_tsvector('{_TS_CONFIG}', concat_ws(' ', f->>'canonical_name', "
        "(SELECT string_agg(a, ' ') FROM jsonb_array_elements_text("
        "COALESCE(f->'aliases', '[]'::jsonb)) AS a)))",
        "NULL::timestamptz",
    ),
    "chronicler.episodes_public": (
        "COALESCE(f->>'title', f->>'kind')",
        _vector("title", "kind", "category"),
        "(f->>'started_at')::timestamptz",
    ),
    "travel.trips": (
        "concat_ws(' ', f->>'name', f->>'destination')",
        _vector("name", "destination", "status"),
        _utc_day("start_date"),
    ),
    "travel.legs": (
        "concat_ws(' ', f->>'type', f->>'carrier', f->>'departure_city', f->>'arrival_city')",
        _vector("type", "carrier", "departure_city", "arrival_city"),
        "(f->>'departure_at')::timestamptz",
    ),
    "travel.documents": ("(f->>'type') || ' document'", _vector("type"), _utc_day("expiry_date")),
}
# The published fields ``fetch`` renders as text, in order; ids and filter flags stay out.
_TEXT_FIELDS = {
    "public.entities": ("canonical_name", "entity_type", "aliases", "roles"),
    "chronicler.episodes_public": ("title", "kind", "category", "started_at", "ended_at"),
    "travel.trips": ("name", "destination", "status", "start_date", "end_date"),
    "travel.legs": (
        "type",
        "carrier",
        "departure_city",
        "departure_at",
        "arrival_city",
        "arrival_at",
    ),
    "travel.documents": ("type", "expiry_date"),
}
_FACT_TEXT = ("predicate", "subject", "valid_at", "validity")
# Dashboard deep links by item kind: fixed paths, never an argument or a query (D10).
_LINKS = {
    "entity": "/entities/{id}",
    "fact": "/memory/facts/{id}",
    "episode": "/chronicles",
    "trip": "/butlers/travel",
    "leg": "/butlers/travel",
    "document": "/butlers/travel",
}


@dataclass(frozen=True)
class Source:
    """One published source of the search index and how it is ranked and rendered."""

    table: Table
    view: str
    kind: str
    prefix: str
    title_sql: str
    vector_sql: str
    at_sql: str

    @property
    def is_fact(self) -> bool:
        return self.table.source.endswith(".facts")

    def title(self, data: dict[str, Any]) -> str:
        source = self.table.source
        if self.is_fact:
            title = data.get("content")
        elif source == "public.entities":
            title = data.get("canonical_name")
        elif source == "chronicler.episodes_public":
            title = data.get("title") or data.get("kind")
        elif source == "travel.trips":
            title = data.get("name") or data.get("destination")
        elif source == "travel.legs":
            title = leg_title(data)
        else:
            title = f"{(data.get('type') or 'travel').capitalize()} document"
        return (title or self.kind.capitalize())[:TITLE_MAX]

    def text(self, data: dict[str, Any]) -> str:
        if self.is_fact:
            lines = [data.get("content") or ""]
            lines += [f"{k}: {data[k]}" for k in _FACT_TEXT if data.get(k) is not None]
        else:
            lines = [
                f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}"
                for k in _TEXT_FIELDS[self.table.source]
                if (v := data.get(k)) not in (None, [])
            ]
        return "\n".join(lines)

    def valid_at(self, data: dict[str, Any]) -> str | None:
        column = self.table.time_column
        return data.get(column) if column else None


@cache
def index_sources() -> tuple[Source, ...]:
    """Every published source of ``search``; a finance or health source is refused outright."""
    result = []
    for table in tables_for("search"):
        if table.domain not in INDEX_DOMAINS:
            raise LookupError(f"{table.source}: {table.domain} is never searchable")
        _, view = published(table.source, "search")
        kind, prefix = record_uri(table, "")
        shape = _FACT_SQL if table.source.endswith(".facts") else _SQL[table.source]
        result.append(Source(table, view, kind, prefix, *shape))
    return tuple(result)


def index_kinds() -> tuple[str, ...]:
    return tuple(sorted({s.kind for s in index_sources()}))


def _url(context: ReadContext, kind: str, row_id: str) -> str:
    return context.link(_LINKS[kind].format(id=row_id))


def _ranked_sql(sources: list[Source]) -> str:
    """Matching items as ``(source, item_id, source_id, f, at, score)``; ``$1`` is the query."""
    selects = " UNION ALL ".join(
        f"""SELECT {i} AS source, '{s.prefix}' || source_id AS item_id, source_id, f, at,
               vector, mcp_ext.similarity(left(title, {TITLE_MAX}), $1) AS similarity
        FROM (SELECT source_id, f, {s.title_sql} AS title, {s.vector_sql} AS vector,
                     {s.at_sql} AS at
              FROM (SELECT source_id, fields AS f FROM {s.view}) AS v) AS s"""
        for i, s in enumerate(sources)
    )
    return f"""SELECT source, item_id, source_id, f, at,
               (COALESCE(ts_rank(vector, q.q), 0) + COALESCE(similarity, 0))::float8 AS score
        FROM ({selects}) AS items,
             (SELECT websearch_to_tsquery('{_TS_CONFIG}', $1) AS q) AS q
        WHERE vector @@ q.q OR similarity >= {TRIGRAM_THRESHOLD}"""


def _sources(context: ReadContext, domains: tuple[str, ...] | None = None) -> list[Source]:
    return [
        s
        for s in index_sources()
        if context.enabled(s.table.domain) and (domains is None or s.table.domain in domains)
    ]


async def _ranked(conn: asyncpg.Connection, sql: str, *params: Any) -> list[asyncpg.Record]:
    async def read() -> list[asyncpg.Record]:
        try:
            return await conn.fetch(sql, *params)
        except _TRIGRAM_MISSING:
            # The owner step installs and grants mcp_ext; until it has, ranking cannot run.
            raise ReadError("unavailable") from None

    return await in_snapshot(conn, read)


def _refuse_stale(context: ReadContext) -> None:
    # Multi-domain: a disabled domain is omitted, but a stale projection refuses (D17).
    if context.freshness == "stale":
        raise ReadError("projection_stale")


async def search(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Exactly ``{results: [{id, title, url}]}``, at most 20, best first (ChatGPT contract).

    Disabled domains are omitted without error; the shape never gains a field.
    """
    args = Arguments(arguments, {"query"})
    query = args.text("query", max_length=QUERY_MAX, required=True)
    _refuse_stale(context)
    sources = _sources(context)
    if not sources:
        return {"results": []}
    rows = await _ranked(
        conn, _ranked_sql(sources) + f" ORDER BY score DESC, item_id LIMIT {SEARCH_LIMIT}", query
    )
    results = []
    for row in rows:
        source = sources[row["source"]]
        results.append(
            {
                "id": row["item_id"],
                "title": source.title(fields(row["f"])),
                "url": _url(context, source.kind, row["source_id"]),
            }
        )
    return {"results": results}


def _cut(text: str, limit: int) -> tuple[str, bool]:
    """``text`` cut to ``limit`` UTF-8 bytes on a character boundary, and whether it was cut."""
    raw = text.encode()
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", "ignore"), True


async def _fetch_proposal(
    conn: asyncpg.Connection, context: ReadContext, item_id: str
) -> dict[str, Any]:
    """A proposal's state and decision time only: never its payload, note or submitter."""
    _refuse_stale(context)
    try:
        proposal_id = str(uuid.UUID(item_id.removeprefix("proposal:")))
    except ValueError:
        raise ReadError("not_found", domain="proposals") from None
    context.require("proposals")
    table, view = published("relationship.external_fact_proposals", "fetch")
    row = await in_snapshot(
        conn,
        lambda: conn.fetchrow(f"SELECT fields FROM {view} WHERE source_id = $1", proposal_id),
    )
    if row is None:
        raise ReadError("not_found", domain="proposals")
    data = fields(row["fields"])
    kind, uri = record_uri(table, proposal_id)
    return {
        "id": item_id,
        "title": "Proposal",
        "text": data["state"],
        "url": context.link("/approvals"),
        "metadata": {
            "domain": "proposals",
            "kind": "proposal",
            "source": context.source(kind, uri),
            "valid_at": None,
            "truncated": False,
            "state": data["state"],
            "decided_at": data.get("decided_at"),
        },
    }


async def fetch(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Exactly ``{id, title, text, url, metadata}`` for one ``search`` item or proposal.

    Any id that is not a published index item, a finance or health uri included, is
    ``not_found``; ``text`` is cut to 16 KiB and the cut is marked ``metadata.truncated``.
    """
    args = Arguments(arguments, {"id"})
    item_id = args.text("id", max_length=256, required=True) or ""
    # D17 precedence: staleness refuses before any id is judged unknown.
    _refuse_stale(context)
    if item_id.startswith("proposal:"):
        return await _fetch_proposal(conn, context, item_id)
    candidates = [
        s for s in index_sources() if "fetch" in s.table.tools and item_id.startswith(s.prefix)
    ]
    row_id = item_id[len(candidates[0].prefix) :] if candidates else ""
    if not row_id or "/" in row_id:
        raise ReadError("not_found")
    domain = candidates[0].table.domain
    context.require(domain)

    async def read() -> tuple[Source, dict[str, Any]] | None:
        for source in candidates:
            value = await conn.fetchval(
                f"SELECT fields FROM {source.view} WHERE source_id = $1", row_id
            )
            if value is not None:
                return source, fields(value)
        return None

    found = await in_snapshot(conn, read)
    if found is None:
        raise ReadError("not_found", domain=domain)
    source, data = found
    text, truncated = _cut(source.text(data), FETCH_TEXT_BYTES)
    return {
        "id": item_id,
        "title": source.title(data),
        "text": text,
        "url": _url(context, source.kind, row_id),
        "metadata": {
            "domain": domain,
            "kind": source.kind,
            "source": context.source(source.kind, item_id, data.get("content_authority")),
            "valid_at": source.valid_at(data),
            "truncated": truncated,
        },
    }


async def search_filtered(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Ranked index items with snippets, filtered by domain, kind and time, paged.

    Each spanned domain that is disabled is listed in ``sources_degraded`` with reason
    ``"disabled"``. ``since``/``until`` bound the item time ``[since, until)`` and so drop
    timeless items (entities).
    """
    args = Arguments(arguments, {"query", "domains", "kinds", "since", "until", "limit", "cursor"})
    query = args.text("query", max_length=QUERY_MAX, required=True)
    domains = args.subset("domains", max_items=4, max_length=16, options=INDEX_DOMAINS)
    kinds = args.subset("kinds", max_items=8, max_length=16, options=index_kinds())
    since, until = args.optional_instant("since"), args.optional_instant("until")
    if since is not None and until is not None and until <= since:
        raise ReadError("invalid_argument", argument="until")
    limit = args.integer("limit", low=1, high=FILTERED_LIMIT, default=SEARCH_LIMIT)
    after = decode_cursor(context, "search_filtered", args.values, args.cursor())
    if after is not None and (
        len(after) != 2 or type(after[0]) not in (int, float) or not isinstance(after[1], str)
    ):
        raise ReadError("invalid_argument", argument="cursor")
    _refuse_stale(context)
    degraded = context.degraded(tuple(d for d in INDEX_DOMAINS if d in (domains or INDEX_DOMAINS)))
    sources = [s for s in _sources(context, domains) if kinds is None or s.kind in kinds]
    if not sources:
        return {"items": [], "next_cursor": None, "sources_degraded": degraded}
    sql = f"""SELECT * FROM ({_ranked_sql(sources)}) AS ranked
        WHERE ($2::timestamptz IS NULL OR at >= $2) AND ($3::timestamptz IS NULL OR at < $3)
          AND ($4::float8 IS NULL OR score < $4 OR (score = $4 AND item_id > $5))
        ORDER BY score DESC, item_id LIMIT $6"""
    rows = await _ranked(
        conn,
        sql,
        query,
        since,
        until,
        None if after is None else float(after[0]),
        None if after is None else after[1],
        limit + 1,
    )
    items = []
    for row in rows[:limit]:
        source = sources[row["source"]]
        data = fields(row["f"])
        snippet, _ = _cut(source.text(data), SNIPPET_MAX * 4)
        items.append(
            {
                "id": row["item_id"],
                "title": source.title(data),
                "snippet": snippet[:SNIPPET_MAX],
                "domain": source.table.domain,
                "kind": source.kind,
                "url": _url(context, source.kind, row["source_id"]),
                "source": context.source(
                    source.kind, row["item_id"], data.get("content_authority")
                ),
            }
        )
    cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        cursor = encode_cursor(
            context, "search_filtered", args.values, [last["score"], last["item_id"]]
        )
    # 50 items of capped titles and snippets stay far inside the 256 KiB cap (D10).
    return {"items": items, "next_cursor": cursor, "sources_degraded": degraded}
