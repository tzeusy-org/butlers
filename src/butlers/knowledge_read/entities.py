"""``entity_lookup`` and ``entity_profile`` over the published relationship views (RFC 0039 D10).

Deleted, archived, unlisted, unidentified and placeholder-named entities never reach the
projection (REQ-public-mcp-projection-007), and every fact, edge and count view hides rows that
name such an entity, so these readers need no flag of their own.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from butlers.knowledge_read.core import (
    Arguments,
    ReadContext,
    ReadError,
    fields,
    in_snapshot,
    published,
    record_uri,
    source_domains,
)

LOOKUP_LIMIT = 10
PROFILE_FACT_LIMIT = 200
TRIGRAM_THRESHOLD = 0.3
_MERGE_HOPS = 8
_MATCH_RANK = {"exact": 0, "alias": 1, "trigram": 2}


async def _entities(conn: asyncpg.Connection, view: str, ids: list[str]) -> dict[str, dict]:
    rows = await conn.fetch(f"SELECT source_id, fields FROM {view} WHERE source_id = ANY($1)", ids)
    return {r["source_id"]: fields(r["fields"]) for r in rows}


async def _survivors(
    conn: asyncpg.Connection, view: str, found: dict[str, dict]
) -> dict[str, dict | None]:
    """Map each entity id to its final published survivor, or None when the chain leaves."""
    known = dict(found)
    for _ in range(_MERGE_HOPS):
        missing = sorted(
            {f["merged_into"] for f in known.values() if f and f.get("merged_into")} - set(known)
        )
        if not missing:
            break
        fetched = await _entities(conn, view, missing)
        known.update({m: fetched.get(m) for m in missing})
    result: dict[str, dict | None] = {}
    for start in found:
        current, seen = start, {start}
        while current is not None and known.get(current) and known[current].get("merged_into"):
            current = known[current]["merged_into"]
            if current in seen or len(seen) > _MERGE_HOPS:
                current = None
            else:
                seen.add(current)
        result[start] = known.get(current) if current is not None else None
    return result


def _entity(context: ReadContext, table: Any, row: dict) -> dict[str, Any]:
    kind, uri = record_uri(table, row["id"])
    return {
        "entity_id": row["id"],
        "name": row["canonical_name"],
        "type": row["entity_type"],
        "aliases": row.get("aliases") or [],
        "roles": row.get("roles") or [],
        "source": context.source(kind, uri),
    }


async def entity_lookup(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Up to 10 published entities by exact name, alias or trigram similarity.

    A merged entity's match is reported as its published survivor; a survivor that is not
    published drops the match.
    """
    args = Arguments(arguments, {"name", "type"})
    name = args.text("name", max_length=128, required=True)
    wanted_type = args.text("type", max_length=64)
    context.require("relationship")
    table, view = published("public.entities", "entity_lookup")

    async def read() -> dict[str, Any]:
        rows = await conn.fetch(
            f"""
            WITH candidates AS (
                SELECT source_id, fields, fields->>'canonical_name' AS name,
                       ARRAY(SELECT jsonb_array_elements_text(
                           COALESCE(fields->'aliases', '[]'::jsonb))) AS aliases
                FROM {view}
            ), scored AS (
                SELECT source_id, fields,
                       CASE WHEN lower(name) = lower($1) THEN 'exact'
                            WHEN EXISTS (SELECT 1 FROM unnest(aliases) AS a
                                         WHERE lower(a) = lower($1)) THEN 'alias'
                            ELSE 'trigram' END AS match,
                       GREATEST(mcp_ext.similarity(name, $1),
                                COALESCE((SELECT max(mcp_ext.similarity(a, $1))
                                          FROM unnest(aliases) AS a), 0)) AS similarity
                FROM candidates
            )
            SELECT source_id, fields, match, similarity FROM scored
            WHERE match <> 'trigram' OR similarity >= $2
            ORDER BY CASE match WHEN 'exact' THEN 0 WHEN 'alias' THEN 1 ELSE 2 END,
                     similarity DESC, source_id
            LIMIT 100
            """,
            name,
            TRIGRAM_THRESHOLD,
        )
        survivors = await _survivors(
            conn, view, {r["source_id"]: fields(r["fields"]) for r in rows}
        )
        best: dict[str, dict[str, Any]] = {}
        for row in rows:
            survivor = survivors[row["source_id"]]
            if survivor is None:
                continue
            if wanted_type and survivor["entity_type"].lower() != wanted_type.lower():
                continue
            match = row["match"]
            score = 1.0 if match != "trigram" else round(float(row["similarity"]), 4)
            current = best.get(survivor["id"])
            if current is None or (_MATCH_RANK[match], -score) < (
                _MATCH_RANK[current["match"]],
                -current["score"],
            ):
                best[survivor["id"]] = {
                    "entity_id": survivor["id"],
                    "name": survivor["canonical_name"],
                    "type": survivor["entity_type"],
                    "match": match,
                    "score": score,
                    "source": context.source(*record_uri(table, survivor["id"])),
                }
        matches = sorted(
            best.values(),
            key=lambda m: (_MATCH_RANK[m["match"]], -m["score"], m["name"], m["entity_id"]),
        )
        return {"matches": matches[:LOOKUP_LIMIT]}

    return await in_snapshot(conn, read)


def _profile_fact(context: ReadContext, table: Any, row_id: str, row: dict) -> dict[str, Any]:
    kind, uri = record_uri(table, row_id)
    if table.source == "relationship.entity_facts":
        is_entity = row.get("object_kind") == "entity"
        return {
            "id": row_id,
            "predicate": row["predicate"],
            "subject_entity_id": row.get("subject"),
            "object_entity_id": row.get("object_entity_id"),
            "object": None if is_entity else row.get("object"),
            "content": None,
            "valid_at": row.get("created_at"),
            "source": context.source(kind, uri, row.get("content_authority")),
        }
    return {
        "id": row_id,
        "predicate": row["predicate"],
        "subject_entity_id": row.get("entity_id"),
        "object_entity_id": row.get("object_entity_id"),
        "object": None,
        "content": row.get("content"),
        "valid_at": row.get("valid_at"),
        "source": context.source(kind, uri, row.get("content_authority")),
    }


async def entity_profile(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """One published entity, its published relationship facts and its edge counts.

    ``facts`` holds the entity's ``relationship.entity_facts`` and ``relationship`` memory facts
    on either end, newest first (OD21). ``edges`` counts known edges on either end and withheld
    stubs on the subject side, dropping those whose fact source's domain is disabled.
    """
    args = Arguments(arguments, {"entity_id", "sections"})
    entity_id = args.uuid("entity_id")
    sections = args.subset("sections", max_items=2, max_length=8, options=("facts", "edges"))
    sections = sections or ("edges", "facts")
    context.require("relationship")
    entities, entity_view = published("public.entities", "entity_profile")
    fact_sources = [
        published(source, "entity_profile")
        for source in ("relationship.entity_facts", "relationship.facts")
    ]
    _, edge_view = published("public.entity_graph_edges", "entity_profile")
    _, count_view = published("public.entity_graph_withheld_counts", "entity_profile")
    domains = source_domains()
    disabled_sources = sorted(s for s, d in domains.items() if not context.enabled(d))
    disabled_domains = sorted({d for d in domains.values() if not context.enabled(d)})

    async def read() -> dict[str, Any]:
        found = await _entities(conn, entity_view, [entity_id])
        survivor = (await _survivors(conn, entity_view, found)).get(entity_id) if found else None
        if survivor is None:
            raise ReadError("not_found", domain="relationship")
        target = survivor["id"]
        facts: list[dict[str, Any]] = []
        complete = True
        if "facts" in sections:
            for table, view in fact_sources:
                subject, obj = (
                    ("subject", "object_entity_id")
                    if table.source == "relationship.entity_facts"
                    else ("entity_id", "object_entity_id")
                )
                rows = await conn.fetch(
                    f"SELECT source_id, fields FROM {view} "
                    f"WHERE fields->>'{subject}' = $1 OR fields->>'{obj}' = $1",
                    target,
                )
                facts += [
                    _profile_fact(context, table, r["source_id"], fields(r["fields"])) for r in rows
                ]
            facts.sort(key=lambda f: f["id"])
            facts.sort(key=lambda f: f["valid_at"] or "", reverse=True)
            complete = len(facts) <= PROFILE_FACT_LIMIT
            facts = facts[:PROFILE_FACT_LIMIT]
        if "edges" not in sections:
            edges = None
        else:
            edges = await _edge_counts(target)
        return {
            "entity": _entity(context, entities, survivor),
            "facts": facts,
            "edges": edges,
            "coverage": {
                "sections": list(sections),
                "facts_complete": complete,
                "facts_limit": PROFILE_FACT_LIMIT,
                "domains_omitted": disabled_domains,
            },
        }

    async def _edge_counts(target: str) -> dict[str, int]:
        known = await conn.fetchval(
            f"""SELECT count(*) FROM {edge_view}
            WHERE (fields->>'subject_entity_id' = $1 OR fields->>'object_entity_id' = $1)
              AND COALESCE(fields->>'source_domain', 'relationship') <> ALL($2::text[])""",
            target,
            disabled_domains,
        )
        withheld = await conn.fetchval(
            f"""SELECT COALESCE(sum((fields->>'withheld')::bigint), 0) FROM {count_view}
            WHERE fields->>'entity_id' = $1 AND fields->>'source_key' <> ALL($2::text[])""",
            target,
            disabled_sources,
        )
        return {"known": int(known or 0), "withheld": int(withheld or 0)}

    return await in_snapshot(conn, read)
