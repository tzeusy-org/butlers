"""The pinned tool catalogue and the handshake answers (RFC 0039 D10, D14 items 4 and 6).

Everything here is static: ``tools/list`` and ``initialize`` answer the same bytes whatever
the kill-switch state or caller (REQ-public-mcp-surface-002). ``server/discover`` is not
answered (-32601) until homelab cross-checks the gateway's catalogue probe, which then falls
back to ``initialize`` and ``tools/list``. Input
schemas advertise what the readers' closed argument parsing accepts; the readers stay the
authority and refuse anything else with ``invalid_argument``. Output schemas declare only
what every success result carries, so a validating client never rejects a real answer.
Enums and bounds come from the readers and the manifest, so a change there changes the
catalogue and fails the pin test (``tests/mcp_public/test_catalogue.py``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from typing import Any

import asyncpg
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.types import (
    LATEST_PROTOCOL_VERSION,
    Implementation,
    InitializeResult,
    ServerCapabilities,
    Tool,
    ToolAnnotations,
    ToolsCapability,
)

from butlers.knowledge_read import chronology, describe, finance, graph, health, search, travel
from butlers.knowledge_read.core import DOMAINS, Arguments, ReadContext, ReadError
from butlers.knowledge_read.records import RECORDS_LIMIT, RECORDS_MAX_DAYS, record_kinds

SERVER_NAME = "butlers-public-mcp"
# v1 proposals take relational-kind predicates only (D13); bu-y66o00.8 generates this list
# from knowledge_read/proposable.toml when propose_fact lands.
PROPOSABLE_PREDICATES = (
    "knows",
    "colleague-of",
    "friend-of",
    "co-attended",
    "visited",
    "works-at",
    "member-of",
)

Schema = dict[str, Any]

# -- input building blocks ----------------------------------------------------------------


def _text(max_length: int, description: str) -> Schema:
    return {"type": "string", "minLength": 1, "maxLength": max_length, "description": description}


def _int(low: int, high: int, description: str, default: int | None = None) -> Schema:
    schema: Schema = {"type": "integer", "minimum": low, "maximum": high}
    if default is not None:
        schema["default"] = default
    return {**schema, "description": description}


def _enum(options: tuple[str, ...] | list[str], description: str) -> Schema:
    return {"type": "string", "enum": list(options), "description": description}


def _set(item: Schema, max_items: int, description: str) -> Schema:
    return {
        "type": "array",
        "items": item,
        "minItems": 1,
        "maxItems": max_items,
        "uniqueItems": True,
        "description": description,
    }


_UUID = {"type": "string", "format": "uuid", "maxLength": 64}
_DAY = {"type": "string", "format": "date", "minLength": 10, "maxLength": 10}
_INSTANT = {
    "type": "string",
    "maxLength": 40,
    "description": "ISO 8601 date (UTC midnight) or offset-qualified date-time.",
}
_CURSOR = {
    "type": "string",
    "maxLength": 2048,
    "description": "Opaque next_cursor from the previous page (15 minutes).",
}


def _uuid(description: str) -> Schema:
    return {**_UUID, "description": description}


def _day(description: str) -> Schema:
    return {**_DAY, "description": description}


def _instant(description: str) -> Schema:
    return {**_INSTANT, "description": f"{description} {_INSTANT['description']}"}


def _input(properties: Mapping[str, Schema], *required: str) -> Schema:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


# -- output building blocks ---------------------------------------------------------------

_STR = {"type": "string"}
_NSTR = {"type": ["string", "null"]}
_ANY: Schema = {}
_SOURCE: Schema = {
    "type": "object",
    "properties": {
        "kind": _STR,
        "uri": _STR,
        "as_of": _NSTR,
        "content_authority": _ANY,
    },
    "required": ["kind", "uri", "as_of"],
}
_TRUNCATED = {"type": "boolean", "const": True}
_NEXT = _NSTR
_DEGRADED = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "domain": _STR,
            "reason": {"type": "string", "enum": ["disabled", "lagging"]},
        },
        "required": ["domain", "reason"],
    },
}


def _obj(properties: Mapping[str, Schema], *required: str) -> Schema:
    return {"type": "object", "properties": dict(properties), "required": list(required)}


def _list(item: Schema) -> Schema:
    return {"type": "array", "items": item}


def _output(properties: Mapping[str, Schema], *required: str, truncates: bool = True) -> Schema:
    props = dict(properties)
    if truncates:
        props["truncated"] = _TRUNCATED
    return _obj(props, *required)


def _records(domain: str, tool: str) -> tuple[Schema, Schema]:
    kinds = sorted(record_kinds(domain, tool))
    arguments = _input(
        {
            "kind": _enum(kinds, f"One published {domain} table."),
            "since": _instant("Lower bound on the kind's time column."),
            "until": _instant(
                f"Upper bound (exclusive); at most {RECORDS_MAX_DAYS} days after since."
            ),
            "predicate": _text(128, "facts only: an exact predicate."),
            "query": _text(256, "facts only: lexical search terms."),
            "cursor": _CURSOR,
            "limit": _int(1, RECORDS_LIMIT, "Items per page.", 50),
        },
        "kind",
    )
    item = _obj(
        {
            "kind": _STR,
            "id": _STR,
            "fields": {"type": "object"},
            "json_fields": {"type": "object"},
            "source": _SOURCE,
        },
        "kind",
        "id",
        "fields",
        "json_fields",
        "source",
    )
    return arguments, _output({"items": _list(item), "next_cursor": _NEXT}, "items", "next_cursor")


def _entity_ref() -> Schema:
    return _obj(
        {"entity_id": _STR, "name": _ANY, "type": _ANY, "source": _SOURCE},
        "entity_id",
        "source",
    )


_RECEIPT = _obj(
    {
        "id": _STR,
        "subject_entity_id": _ANY,
        "predicate": _ANY,
        "object_entity_id": _ANY,
        "source": _SOURCE,
    },
    "id",
    "source",
)


# -- the D10 table ------------------------------------------------------------------------


def _definitions() -> list[tuple[str, str, str, Schema, Schema]]:
    """``(name, title, description, inputSchema, outputSchema)`` in D10 table order."""
    q = search.QUERY_MAX
    finance_in, finance_out = _records("finance", "finance_records")
    health_in, health_out = _records("health", "health_records")
    return [
        (
            "search",
            "Search",
            "Lexical search over knowledge, relationship, time and travel records. Returns up "
            f"to {search.SEARCH_LIMIT} results; pass a result id to fetch.",
            _input({"query": _text(q, "Search terms.")}, "query"),
            _obj(
                {"results": _list(_obj({"id": _STR, "title": _STR, "url": _STR}))},
                "results",
            ),
        ),
        (
            "fetch",
            "Fetch",
            "The full text of one search result by id, or a proposal's state by proposal:<uuid>.",
            _input({"id": _text(256, "An id from search or search_filtered.")}, "id"),
            _obj(
                {
                    "id": _STR,
                    "title": _ANY,
                    "text": _STR,
                    "url": _STR,
                    "metadata": _obj(
                        {
                            "domain": _STR,
                            "kind": _STR,
                            "source": _SOURCE,
                            "valid_at": _ANY,
                            "truncated": {"type": "boolean"},
                        },
                        "domain",
                        "kind",
                        "source",
                        "valid_at",
                        "truncated",
                    ),
                },
                "id",
                "title",
                "text",
                "url",
                "metadata",
            ),
        ),
        (
            "search_filtered",
            "Filtered search",
            "Lexical search with domain, kind and time filters and paging; each item carries "
            "a snippet and its source.",
            _input(
                {
                    "query": _text(q, "Search terms."),
                    "domains": _set(
                        _enum(search.INDEX_DOMAINS, "A domain."), 4, "Domains to search."
                    ),
                    "kinds": _set(_enum(search.index_kinds(), "A kind."), 8, "Kinds to return."),
                    "since": _instant("Earliest item time."),
                    "until": _instant("Latest item time (exclusive)."),
                    "limit": _int(1, search.FILTERED_LIMIT, "Items per page.", 20),
                    "cursor": _CURSOR,
                },
                "query",
            ),
            _output(
                {
                    "items": _list(
                        _obj(
                            {
                                "id": _STR,
                                "title": _ANY,
                                "snippet": _STR,
                                "domain": _STR,
                                "kind": _STR,
                                "url": _STR,
                                "source": _SOURCE,
                            },
                            "id",
                            "domain",
                            "kind",
                            "url",
                            "source",
                        )
                    ),
                    "next_cursor": _NEXT,
                    "sources_degraded": _DEGRADED,
                },
                "items",
                "next_cursor",
                "sources_degraded",
                truncates=False,
            ),
        ),
        (
            "entity_lookup",
            "Find a person or entity",
            "Resolve a name to published entities by exact name, alias or similarity.",
            _input(
                {
                    "name": _text(128, "A name or alias."),
                    "type": _text(64, "Only entities of this type, e.g. person."),
                },
                "name",
            ),
            _obj(
                {
                    "matches": _list(
                        _obj(
                            {
                                "entity_id": _STR,
                                "name": _ANY,
                                "type": _ANY,
                                "match": {"type": "string", "enum": ["exact", "alias", "trigram"]},
                                "score": {"type": "number"},
                                "source": _SOURCE,
                            },
                            "entity_id",
                            "match",
                            "score",
                            "source",
                        )
                    )
                },
                "matches",
            ),
        ),
        (
            "entity_profile",
            "Entity profile",
            "One entity's published facts and edge counts.",
            _input(
                {
                    "entity_id": _uuid("An entity_id from entity_lookup."),
                    "sections": _set(
                        _enum(("facts", "edges"), "A section."), 2, "Sections (default both)."
                    ),
                },
                "entity_id",
            ),
            _output(
                {
                    "entity": _obj(
                        {
                            "entity_id": _STR,
                            "name": _ANY,
                            "type": _ANY,
                            "aliases": {"type": "array"},
                            "roles": {"type": "array"},
                            "source": _SOURCE,
                        },
                        "entity_id",
                        "source",
                    ),
                    "facts": _list(
                        _obj(
                            {
                                "id": _STR,
                                "predicate": _ANY,
                                "subject_entity_id": _ANY,
                                "object_entity_id": _ANY,
                                "object": _ANY,
                                "content": _ANY,
                                "valid_at": _ANY,
                                "source": _SOURCE,
                            },
                            "id",
                            "source",
                        )
                    ),
                    "edges": {
                        "anyOf": [
                            _obj(
                                {"known": {"type": "integer"}, "withheld": {"type": "integer"}},
                                "known",
                                "withheld",
                            ),
                            {"type": "null"},
                        ]
                    },
                    "coverage": {"type": "object"},
                },
                "entity",
                "facts",
                "edges",
                "coverage",
            ),
        ),
        (
            "graph_neighbors",
            "Graph neighbours",
            "Entities within one or two hops of an entity, with edge receipts and withheld counts.",
            _input(
                {
                    "entity_id": _uuid("The start entity."),
                    "predicates": _set(
                        {"type": "string", "minLength": 1, "maxLength": 64},
                        20,
                        "Only edges with these predicates.",
                    ),
                    "hops": _int(1, graph.NEIGHBOR_HOPS, "Traversal depth.", 1),
                    "limit": _int(1, graph.NEIGHBOR_LIMIT, "Most nodes returned.", 100),
                },
                "entity_id",
            ),
            _output(
                {
                    "entity": _entity_ref(),
                    "nodes": _list(
                        _obj(
                            {
                                "entity_id": _STR,
                                "name": _ANY,
                                "type": _ANY,
                                "source": _SOURCE,
                                "hop": {"type": "integer"},
                                "via_edge_id": _STR,
                                "withheld": {"type": "integer"},
                            },
                            "entity_id",
                            "hop",
                            "via_edge_id",
                            "withheld",
                        )
                    ),
                    "edges": _list(_RECEIPT),
                    "withheld": {"type": "integer"},
                },
                "entity",
                "nodes",
                "edges",
                "withheld",
            ),
        ),
        (
            "graph_path",
            "Graph path",
            "The shortest published path between two entities, as edge receipts, or null.",
            _input(
                {
                    "from_id": _uuid("The start entity."),
                    "to_id": _uuid("The end entity."),
                    "max_hops": _int(1, graph.PATH_HOPS, "Longest path searched.", graph.PATH_HOPS),
                },
                "from_id",
                "to_id",
            ),
            _obj({"path": {"anyOf": [_list(_RECEIPT), {"type": "null"}]}}, "path"),
        ),
        (
            "timeline",
            "Timeline",
            "Time episodes and trip legs between two instants, oldest first, with paging.",
            _input(
                {
                    "start": _instant("Window start."),
                    "end": _instant(f"Window end (at most {chronology.TIMELINE_MAX_DAYS} days)."),
                    "kinds": _set(
                        {"type": "string", "minLength": 1, "maxLength": 64},
                        20,
                        "Only items of these kinds.",
                    ),
                    "cursor": _CURSOR,
                    "limit": _int(1, chronology.TIMELINE_LIMIT, "Items per page.", 100),
                },
                "start",
                "end",
            ),
            _output(
                {
                    "items": _list(
                        _obj(
                            {
                                "at": _ANY,
                                "end": _ANY,
                                "kind": _ANY,
                                "title": _ANY,
                                "domain": _STR,
                                "source": _SOURCE,
                            },
                            "at",
                            "end",
                            "kind",
                            "title",
                            "domain",
                            "source",
                        )
                    ),
                    "next_cursor": _NEXT,
                    "sources_degraded": _DEGRADED,
                },
                "items",
                "next_cursor",
                "sources_degraded",
            ),
        ),
        (
            "time_use",
            "Time use",
            "Minutes per day, week or month, grouped by category or kind.",
            _input(
                {
                    "start": _day("First local day."),
                    "end": _day(f"Last local day (at most {chronology.TIME_USE_MAX_DAYS} days)."),
                    "bucket": _enum(("day", "week", "month"), "Bucket size."),
                    "group_by": _enum(("category", "kind"), "Grouping."),
                },
                "start",
                "end",
                "bucket",
                "group_by",
            ),
            _output(
                {
                    "series": _list(
                        _obj(
                            {
                                "bucket_start": _STR,
                                "group": _ANY,
                                "minutes": {"type": "integer"},
                            },
                            "bucket_start",
                            "group",
                            "minutes",
                        )
                    ),
                    "source": _SOURCE,
                },
                "series",
                "source",
            ),
        ),
        (
            "trips",
            "Trips",
            "Trips newest first, with legs, city-level places and document expiry dates.",
            _input(
                {
                    "status": _enum(travel.TRIP_STATUSES, "Only trips in this status."),
                    "trip_id": _uuid("One trip."),
                    "limit": _int(1, travel.TRIPS_LIMIT, "Most trips returned.", 20),
                }
            ),
            _output(
                {
                    "trips": _list(
                        _obj(
                            {
                                "id": _STR,
                                "legs": {"type": "array"},
                                "documents": {"type": "array"},
                                "source": _SOURCE,
                            },
                            "id",
                            "legs",
                            "documents",
                            "source",
                        )
                    )
                },
                "trips",
            ),
        ),
        (
            "describe",
            "Describe this server",
            "Server version, manifest digest, per-domain state, freshness, tools and the "
            "published columns.",
            _input({}),
            _obj(
                {
                    "server_version": _STR,
                    "manifest_digest": _NSTR,
                    "domains": {
                        "type": "object",
                        "properties": {
                            d: {"type": "string", "enum": ["enabled", "disabled", "stale"]}
                            for d in DOMAINS
                        },
                        "required": list(DOMAINS),
                    },
                    "as_of": _NSTR,
                    "tools": _list(_STR),
                    "columns": {"type": "object"},
                },
                "server_version",
                "manifest_digest",
                "domains",
                "as_of",
                "tools",
                "columns",
            ),
        ),
        (
            "finance_summary",
            "Finance summary",
            "Totals per currency and direction, grouped by category, merchant or month; "
            "never converted.",
            _input(
                {
                    "start": _day("First day (UTC)."),
                    "end": _day(f"Last day (at most {finance.LEDGER_MAX_DAYS} days)."),
                    "group_by": _enum(("category", "merchant", "month"), "Grouping."),
                    "currency": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 3,
                        "description": "Only this ISO 4217 currency.",
                    },
                },
                "start",
                "end",
                "group_by",
            ),
            _output(
                {
                    "totals": _list({"type": "object"}),
                    "groups": _list({"type": "object"}),
                    "source": _SOURCE,
                },
                "totals",
                "groups",
                "source",
            ),
        ),
        (
            "finance_ledger",
            "Finance ledger",
            "Transactions newest first, amounts as decimal strings, with account labels.",
            _input(
                {
                    "start": _day("First day (UTC)."),
                    "end": _day(f"Last day (at most {finance.LEDGER_MAX_DAYS} days)."),
                    "category": _text(128, "Only this display category."),
                    "merchant": _text(256, "Only this display merchant."),
                    "cursor": _CURSOR,
                    "limit": _int(1, finance.LEDGER_LIMIT, "Rows per page.", 100),
                },
                "start",
                "end",
            ),
            _output(
                {
                    "items": _list(_obj({"id": _STR, "source": _SOURCE}, "id", "source")),
                    "next_cursor": _NEXT,
                },
                "items",
                "next_cursor",
            ),
        ),
        (
            "finance_obligations",
            "Bills and subscriptions",
            "Bills and active subscriptions due within the horizon, soonest first.",
            _input(
                {"horizon_days": _int(1, 90, "Days ahead to include.")},
                "horizon_days",
            ),
            _output(
                {
                    "items": _list(
                        _obj(
                            {
                                "kind": {"type": "string", "enum": ["bill", "subscription"]},
                                "source": _SOURCE,
                            },
                            "kind",
                            "source",
                        )
                    )
                },
                "items",
            ),
        ),
        (
            "finance_records",
            "Finance records",
            "One page of one published finance table, newest first.",
            finance_in,
            finance_out,
        ),
        (
            "health_series",
            "Health series",
            "Numeric measurements of one metric, raw or as daily or weekly means.",
            _input(
                {
                    "metric": _text(128, "A published metric name, e.g. weight."),
                    "start": _day("First day (UTC)."),
                    "end": _day(f"Last day (at most {health.SERIES_MAX_DAYS} days)."),
                    "bucket": _enum(("raw", "day", "week"), "Bucket size."),
                },
                "metric",
                "start",
                "end",
                "bucket",
            ),
            _output(
                {
                    "unit": _ANY,
                    "points": _list(_obj({"t": _STR, "value": {"type": "number"}}, "t", "value")),
                    "source": _SOURCE,
                    "units_omitted": {"type": "array"},
                },
                "unit",
                "points",
                "source",
            ),
        ),
        (
            "health_profile",
            "Health profile",
            "Medications, conditions, allergies and providers.",
            _input(
                {
                    "sections": _set(
                        _enum(health.SECTIONS, "A section."), 4, "Sections (default all)."
                    )
                }
            ),
            _output(
                {s: _list(_obj({"source": _SOURCE}, "source")) for s in health.SECTIONS},
                *health.SECTIONS,
            ),
        ),
        (
            "health_records",
            "Health records",
            "One page of one published health table, newest first.",
            health_in,
            health_out,
        ),
        (
            "propose_fact",
            "Propose a relationship fact",
            "Propose a relationship between two existing entities for the owner to approve. "
            "Nothing changes until the owner approves it; check the outcome with fetch on "
            "proposal:<proposal_id>.",
            _input(
                {
                    "subject_entity_id": _uuid("The subject entity."),
                    "predicate": _enum(PROPOSABLE_PREDICATES, "The relationship."),
                    "object_entity_id": _uuid("The object entity."),
                    "evidence_url": _text(2048, "A link supporting the proposal."),
                    "note": _text(500, "A short note for the owner."),
                    "idempotency_key": _text(64, "Retry key; a retry returns the same id."),
                },
                "subject_entity_id",
                "predicate",
                "object_entity_id",
                "idempotency_key",
            ),
            _obj(
                {"proposal_id": _STR, "status": {"type": "string", "const": "received"}},
                "proposal_id",
                "status",
            ),
        ),
    ]


def _annotations(name: str, title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=name != "propose_fact",
        destructiveHint=False,
        idempotentHint=name != "propose_fact",  # each proposal creates an inbox row
        openWorldHint=False,
    )


@cache
def catalogue() -> tuple[Tool, ...]:
    """The 19 D10 tools, in table order."""
    tools = tuple(
        Tool(
            name=name,
            title=title,
            description=text,
            inputSchema=arguments,
            outputSchema=output,
            annotations=_annotations(name, title),
        )
        for name, title, text, arguments, output in _definitions()
    )
    if tuple(t.name for t in tools) != describe.TOOLS:
        raise RuntimeError("the catalogue drifted from knowledge_read.describe.TOOLS")
    return tools


def _wire(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


@cache
def tools_list_result() -> str:
    """The ``tools/list`` result as canonical JSON text: one value for every caller."""
    return json.dumps({"tools": [_wire(t) for t in catalogue()]}, sort_keys=True)


CAPABILITIES = ServerCapabilities(tools=ToolsCapability(listChanged=False))


def _server_info() -> Implementation:
    return Implementation(name=SERVER_NAME, title="Butlers", version=describe.server_version())


def initialize(params: Mapping[str, Any]) -> dict[str, Any]:
    """Echo a supported requested protocol version, else offer the latest (MCP lifecycle)."""
    requested = params.get("protocolVersion")
    version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
    return _wire(
        InitializeResult(
            protocolVersion=version, capabilities=CAPABILITIES, serverInfo=_server_info()
        )
    )


def tools_list(_params: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(tools_list_result())


STATIC_HANDLERS = {
    "initialize": initialize,
    "tools/list": tools_list,
}


async def propose_fact_pending(
    _conn: asyncpg.Connection, _context: ReadContext, arguments: object
) -> dict[str, Any]:
    """``propose_fact`` until its reader lands (bu-y66o00.8): closed argument parsing, then
    ``unavailable``, so a client fixes a bad argument but never rewrites a good one."""
    args = Arguments(
        arguments,
        {
            "subject_entity_id",
            "predicate",
            "object_entity_id",
            "evidence_url",
            "note",
            "idempotency_key",
        },
    )
    args.uuid("subject_entity_id")
    args.choice("predicate", PROPOSABLE_PREDICATES)
    args.uuid("object_entity_id")
    args.text("evidence_url", max_length=2048)
    args.text("note", max_length=500)
    args.text("idempotency_key", max_length=64, required=True)
    raise ReadError("unavailable", domain="proposals")
