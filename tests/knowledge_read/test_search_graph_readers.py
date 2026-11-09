"""Argument contracts of search, fetch, search_filtered, graph, trips and describe.

Every case here refuses before the reader touches the connection; the replicated behaviour is
covered in ``tests/integration/test_knowledge_read_projection.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from butlers.knowledge_read.core import ReadContext, ReadError, tables_for
from butlers.knowledge_read.describe import TOOLS, describe, published_columns
from butlers.knowledge_read.graph import graph_neighbors, graph_path
from butlers.knowledge_read.search import (
    INDEX_DOMAINS,
    fetch,
    index_kinds,
    index_sources,
    search,
    search_filtered,
)
from butlers.knowledge_read.travel import trips

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
ID = "6f1c1c4e-8a55-4c2b-9d7e-0a1b2c3d4e5f"


class _Untouchable:
    """A connection any use of which fails the test: refusals must come first."""

    def __getattr__(self, name):
        raise AssertionError(f"the reader touched the connection ({name})")


_UNTOUCHABLE: Any = _Untouchable()


def _context(**changes) -> ReadContext:
    base = ReadContext(
        sub="github|1",
        as_of=NOW - timedelta(seconds=5),
        now=NOW,
        cursor_key=b"k" * 32,
        dashboard_base="https://dash.example.test/",
    )
    return replace(base, **changes)


async def _code(reader, arguments, context=None) -> tuple[str, str | None]:
    with pytest.raises(ReadError) as caught:
        await reader(_Untouchable(), context or _context(), arguments)
    return caught.value.code, caught.value.argument


def test_the_index_spans_only_the_four_searchable_domains():
    sources = index_sources()
    assert {s.table.domain for s in sources} == set(INDEX_DOMAINS)
    assert not any(s.table.domain in {"finance", "health", "proposals"} for s in sources)
    assert all(s.prefix.startswith(f"butlers://{s.table.domain}/") for s in sources)
    assert index_kinds() == ("document", "entity", "episode", "fact", "leg", "trip")
    # Every manifest source of fetch is an index source, except the proposal state.
    fetchable = {t.source for t in tables_for("fetch")}
    assert fetchable - {s.table.source for s in sources} == {"relationship.external_fact_proposals"}


@pytest.mark.parametrize(
    ("arguments", "argument"),
    [
        ({}, "query"),
        ({"query": ""}, "query"),
        ({"query": " "}, "query"),
        ({"query": "x" * 257}, "query"),
        ({"query": 5}, "query"),
        ({"query": "x", "limit": 5}, "limit"),
    ],
)
async def test_search_refuses_malformed_queries(arguments, argument):
    assert await _code(search, arguments) == ("invalid_argument", argument)


async def test_search_refuses_a_stale_projection_and_answers_empty_when_all_is_off():
    stale = _context(as_of=NOW - timedelta(minutes=16))
    assert (await _code(search, {"query": "x"}, stale))[0] == "projection_stale"
    off = _context(disabled_domains=frozenset(INDEX_DOMAINS))
    assert await search(_UNTOUCHABLE, off, {"query": "x"}) == {"results": []}


@pytest.mark.parametrize(
    "item_id",
    [
        f"butlers://finance/transactions/{ID}",
        f"butlers://health/medications_public/{ID}",
        f"butlers://relationship/edge/{ID}",
        "butlers://relationship/entity/",
        f"butlers://relationship/entity/{ID}/x",
        "https://example.test/",
    ],
)
async def test_fetch_never_resolves_an_id_outside_the_index(item_id):
    assert (await _code(fetch, {"id": item_id}))[0] == "not_found"


async def test_fetch_refuses_disabled_and_malformed_ids_before_reading():
    off = _context(disabled_domains=frozenset({"travel"}))
    code = await _code(fetch, {"id": f"butlers://travel/trip/{ID}"}, off)
    assert code == ("domain_disabled", None)
    assert (await _code(fetch, {"id": "proposal:nope"}))[0] == "not_found"
    assert await _code(fetch, {"id": "x" * 257}) == ("invalid_argument", "id")
    assert await _code(fetch, {"id": "x", "query": "y"}) == ("invalid_argument", "query")


@pytest.mark.parametrize(
    ("arguments", "argument"),
    [
        ({"query": "x", "domains": ["finance"]}, "domains"),
        ({"query": "x", "domains": []}, "domains"),
        ({"query": "x", "kinds": ["transaction"]}, "kinds"),
        ({"query": "x", "limit": 51}, "limit"),
        ({"query": "x", "limit": 0}, "limit"),
        ({"query": "x", "since": "2026-06-02", "until": "2026-06-01"}, "until"),
        ({"query": "x", "since": "yesterday"}, "since"),
        ({"query": "x", "cursor": "not-a-cursor"}, "cursor"),
    ],
)
async def test_search_filtered_refuses_out_of_contract_arguments(arguments, argument):
    assert await _code(search_filtered, arguments) == ("invalid_argument", argument)


async def test_search_filtered_names_requested_disabled_domains_without_reading():
    off = _context(disabled_domains=frozenset({"travel"}))
    result = await search_filtered(_UNTOUCHABLE, off, {"query": "x", "domains": ["travel"]})
    assert result == {
        "items": [],
        "next_cursor": None,
        "sources_degraded": [{"domain": "travel", "reason": "disabled"}],
    }


@pytest.mark.parametrize(
    ("arguments", "argument"),
    [
        ({}, "entity_id"),
        ({"entity_id": "nope"}, "entity_id"),
        ({"entity_id": ID, "hops": 3}, "hops"),
        ({"entity_id": ID, "hops": 0}, "hops"),
        ({"entity_id": ID, "limit": 201}, "limit"),
        ({"entity_id": ID, "predicates": []}, "predicates"),
        ({"entity_id": ID, "direction": "out"}, "direction"),
    ],
)
async def test_graph_neighbors_refuses_out_of_contract_arguments(arguments, argument):
    assert await _code(graph_neighbors, arguments) == ("invalid_argument", argument)


@pytest.mark.parametrize(
    ("arguments", "argument"),
    [
        ({"from_id": ID}, "to_id"),
        ({"from_id": ID, "to_id": ID, "max_hops": 5}, "max_hops"),
        ({"from_id": ID, "to_id": ID, "max_hops": 0}, "max_hops"),
    ],
)
async def test_graph_path_refuses_out_of_contract_arguments(arguments, argument):
    assert await _code(graph_path, arguments) == ("invalid_argument", argument)


async def test_graph_tools_refuse_a_disabled_relationship_domain():
    off = _context(disabled_domains=frozenset({"relationship"}))
    assert (await _code(graph_neighbors, {"entity_id": ID}, off))[0] == "domain_disabled"
    assert (await _code(graph_path, {"from_id": ID, "to_id": ID}, off))[0] == "domain_disabled"


@pytest.mark.parametrize(
    ("arguments", "argument"),
    [
        ({"status": "booked"}, "status"),
        ({"trip_id": "nope"}, "trip_id"),
        ({"limit": 51}, "limit"),
        ({"confirmation": "x"}, "confirmation"),
    ],
)
async def test_trips_refuses_out_of_contract_arguments(arguments, argument):
    assert await _code(trips, arguments) == ("invalid_argument", argument)


async def test_describe_takes_no_arguments_and_lists_the_pinned_catalogue():
    assert await _code(describe, {"domain": "x"}) == ("invalid_argument", "domain")
    assert len(TOOLS) == len(set(TOOLS)) == 19
    columns = published_columns()
    assert "publish_key" not in columns["public.entity_graph_edges"]
    assert "source_domain" in columns["public.entity_graph_edges"]
    assert not {"deleted", "unidentified", "placeholder_name"} & set(columns["public.entities"])


def test_described_columns_match_the_views_the_owner_program_builds():
    from butlers.knowledge_read.manifest import load_manifest
    from butlers.migrations.public_mcp_owner import subscriber_plan

    assert published_columns() == {
        plan.table.source: sorted(f.name for f in plan.read_fields)
        for plan in subscriber_plan(load_manifest())
    }


@pytest.mark.parametrize(
    "item_id",
    ["proposal:nope", "proposal:" + ID, f"butlers://finance/transactions/{ID}", "x"],
)
async def test_fetch_refuses_staleness_before_judging_the_id(item_id):
    stale = _context(as_of=NOW - timedelta(minutes=16))
    assert (await _code(fetch, {"id": item_id}, stale))[0] == "projection_stale"


class _Cancelling:
    """A connection whose statement the server's statement_timeout cancels."""

    def is_in_transaction(self):
        return True

    async def fetch(self, *args, **kwargs):
        raise asyncpg.QueryCanceledError("canceling statement due to statement timeout")

    fetchrow = fetchval = fetch


@pytest.mark.parametrize(
    ("reader", "arguments"),
    [
        (search, {"query": "x"}),
        (search_filtered, {"query": "x"}),
        (fetch, {"id": f"butlers://relationship/entity/{ID}"}),
        (graph_neighbors, {"entity_id": ID}),
        (graph_path, {"from_id": ID, "to_id": ID}),
        (trips, {}),
        (describe, {}),
    ],
)
async def test_a_cancelled_statement_is_unavailable(reader, arguments):
    with pytest.raises(ReadError) as caught:
        await reader(_Cancelling(), _context(), arguments)
    assert caught.value.code == "unavailable"


def test_deep_links_are_the_base_plus_a_fixed_path():
    context = _context()
    assert context.link("/entities/abc") == "https://dash.example.test/entities/abc"
    assert _context(dashboard_base="").link("/chronicles") == "/chronicles"
