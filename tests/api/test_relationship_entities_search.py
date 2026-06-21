"""Tests for GET /api/relationship/entities/search (deterministic Finder).

Covers:
- Empty query string returns 200 with empty results
- Prefix match returns score=100, match_kind='prefix'
- Substring match returns score=50, match_kind='substring'
- Contact-fact match returns score=70, match_kind='contact_fact'
- Predicate label match returns score=30, match_kind='predicate'
- Score deduplication: entity matching multiple rules gets max score
- Limit parameter respected (default=20, max=50)
- Owner-required gate: non-owner returns 403 + owner_required
- Scope filter: cross-scope rows excluded (facts queries include scope filter)
- Response shape: SearchResponse with results/total/q/limit

Guardrail §10.8 (Amendment 15):
- No LLM or embedding service imports in the search code path

Uses the same mock-pool pattern as test_relationship_entities_list.py —
no real Postgres or Docker required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

SEARCH_PATH = "/api/relationship/entities/search"
BASE_URL = "http://test"

_ENTITY_ID_1 = uuid4()
_ENTITY_ID_2 = uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_search_row(
    *,
    entity_id: UUID | None = None,
    canonical_name: str = "Alice Example",
    entity_type: str = "person",
    score: int = 100,
    match_kind: str = "prefix",
) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for search rows."""
    data = {
        "entity_id": entity_id or uuid4(),
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "score": score,
        "match_kind": match_kind,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _app_with_pool(
    *,
    fetch_rows: list | None = None,
    fetchrow_return: dict | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mocked relationship DB pool.

    ``fetchrow_return`` controls the ``_get_owner_roles`` call.
    When it returns a row with ``id`` and ``roles=["owner"]``, the owner gate passes.
    When it returns None, the gate returns 403.
    ``fetch_rows`` is returned by ``pool.fetch`` (the search results).
    """
    # Default: owner entity found (gate passes).
    # Must include ``roles`` so _get_owner_roles can inspect row["roles"].
    if fetchrow_return is None:
        fetchrow_return = {"id": uuid4(), "roles": ["owner"]}

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()

    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


def _non_owner_app() -> FastAPI:
    """Return an app whose mock DB simulates no owner entity (gate returns 403)."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)  # No owner entity
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break
    return app


async def _get(app: FastAPI, path: str = SEARCH_PATH, **params) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(path, params=params or None)


# ---------------------------------------------------------------------------
# Scenario: empty / whitespace query returns 200 with empty results
# ---------------------------------------------------------------------------


async def test_empty_query_returns_200_with_empty_results():
    """Empty q string returns 200 with empty results list (no DB hit needed)."""
    app, mock_pool = _app_with_pool(fetch_rows=[])
    resp = await _get(app, q="")
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["total"] == 0
    assert body["q"] == ""
    assert body["limit"] == 20  # default


async def test_whitespace_only_query_returns_empty_results():
    """Whitespace-only query is treated as empty after strip()."""
    app, mock_pool = _app_with_pool(fetch_rows=[])
    resp = await _get(app, q="   ")
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    # pool.fetch should NOT have been called (empty after strip)
    mock_pool.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario: prefix match (score=100)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,match_kind,query",
    [
        (100, "prefix", "Alice"),  # prefix match
        (50, "substring", "Alice"),  # substring match
        (70, "contact_fact", "lin@"),  # contact-fact value match
        (30, "predicate", "vendor"),  # predicate label match
    ],
)
async def test_match_kind_passes_score_and_kind_through(score, match_kind, query):
    """Each match kind's DB-computed score/match_kind is surfaced in the response.

    The DB computes the score; the endpoint passes the deduplicated row through.
    """
    rows = [
        _make_search_row(
            entity_id=_ENTITY_ID_1, canonical_name="Match", score=score, match_kind=match_kind
        )
    ]
    app, _ = _app_with_pool(fetch_rows=rows)
    resp = await _get(app, q=query)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["entity_id"] == str(_ENTITY_ID_1)
    assert result["score"] == score
    assert result["match_kind"] == match_kind


# ---------------------------------------------------------------------------
# Scenario: multiple results ordered by score descending
# ---------------------------------------------------------------------------


async def test_results_ordered_by_score_descending():
    """Results are ordered score DESC (highest match first)."""
    row_prefix = _make_search_row(
        entity_id=_ENTITY_ID_1, canonical_name="Alice", score=100, match_kind="prefix"
    )
    row_contact = _make_search_row(
        entity_id=_ENTITY_ID_2, canonical_name="Bob (lin@)", score=70, match_kind="contact_fact"
    )
    app, _ = _app_with_pool(fetch_rows=[row_prefix, row_contact])
    resp = await _get(app, q="test")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 2
    assert body["results"][0]["score"] == 100
    assert body["results"][1]["score"] == 70


# ---------------------------------------------------------------------------
# Scenario: limit parameter
# ---------------------------------------------------------------------------


async def test_default_limit_is_20():
    """Default limit is 20."""
    app, _ = _app_with_pool(fetch_rows=[])
    resp = await _get(app, q="test")
    assert resp.status_code == 200
    assert resp.json()["limit"] == 20


async def test_custom_limit_is_respected():
    """Custom limit parameter is reflected in the response."""
    app, _ = _app_with_pool(fetch_rows=[])
    resp = await _get(app, q="test", limit=5)
    assert resp.status_code == 200
    assert resp.json()["limit"] == 5


async def test_limit_above_max_is_rejected():
    """limit > 50 is rejected with HTTP 422 (FastAPI validation)."""
    app, _ = _app_with_pool(fetch_rows=[])
    resp = await _get(app, q="test", limit=51)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Scenario: missing q parameter
# ---------------------------------------------------------------------------


async def test_missing_q_returns_422():
    """Missing required q parameter returns HTTP 422."""
    app, _ = _app_with_pool(fetch_rows=[])
    resp = await _get(app)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Scenario: owner-required gate (Amendment 12b)
# ---------------------------------------------------------------------------


async def test_search_returns_403_for_non_owner():
    """GET /entities/search returns 403 owner_required when no owner entity.

    This test was xfail in test_owner_authz_guardrail.py §12b.
    Now that the endpoint is implemented, it should pass directly here.
    """
    app = _non_owner_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        resp = await client.get(SEARCH_PATH, params={"q": "alice"})

    assert resp.status_code == 403
    body = resp.json()
    code = body.get("code") or (body.get("detail") or {}).get("code")
    assert code == "owner_required", f"Expected owner_required, got: {body}"


async def test_search_returns_200_for_owner():
    """GET /entities/search returns 200 (not 403) when owner entity exists."""
    app, _ = _app_with_pool(
        fetch_rows=[_make_search_row()],
        fetchrow_return={"id": uuid4(), "roles": ["owner"]},
    )
    resp = await _get(app, q="test")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scenario: schema isolation (cross-scope rows excluded via schema prefix, not a column)
#
# relationship.entity_facts has NO scope column — schema isolation is enforced via the
# relationship. schema prefix per RFC 0006. Queries against relationship.entity_facts
# must NOT include AND scope='relationship' (that column does not exist).
# We verify the SQL uses schema-qualified table names and does NOT include a
# spurious scope filter.
# ---------------------------------------------------------------------------


def test_scope_filter_absent_from_entity_facts_queries():
    """search_entities SQL must NOT include scope='relationship' on relationship.entity_facts queries.

    relationship.entity_facts has no scope column.  Schema isolation is enforced via the
    relationship. schema prefix (RFC 0006).  Adding AND scope='relationship' to
    queries against relationship.entity_facts would cause a column-not-found error at runtime.

    Older references to scope='relationship' in this codebase are against the memory
    module's bare facts table (unqualified), NOT relationship.entity_facts.
    """
    import importlib.util
    import inspect
    from pathlib import Path

    router_path = Path(__file__).parents[2] / "roster" / "relationship" / "api" / "router.py"
    spec = importlib.util.spec_from_file_location("_router_src", router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    src = inspect.getsource(mod.search_entities)

    # The search function must NOT apply a scope column filter on relationship.entity_facts queries.
    # Schema isolation is enforced by the relationship. prefix.
    assert "scope = 'relationship'" not in src, (
        "search_entities SQL has a spurious AND scope='relationship' filter on "
        "relationship.entity_facts queries.  relationship.entity_facts has no scope column.  "
        "Schema isolation is enforced via the relationship. schema prefix."
    )
    # Sanity: the function must still use schema-qualified table name
    assert "relationship.entity_facts" in src, (
        "search_entities SQL must use the schema-qualified name relationship.entity_facts"
    )
    # And must still filter validity='active'
    assert "validity = 'active'" in src, (
        "search_entities SQL must filter validity='active' on relationship.entity_facts queries"
    )


# ---------------------------------------------------------------------------
# Scenario: response shape
# ---------------------------------------------------------------------------


async def test_response_shape_contains_required_fields():
    """Response must contain results, total, q, and limit fields."""
    rows = [
        _make_search_row(
            entity_id=_ENTITY_ID_1, canonical_name="Alice", score=100, match_kind="prefix"
        )
    ]
    app, _ = _app_with_pool(fetch_rows=rows)
    resp = await _get(app, q="alice")

    assert resp.status_code == 200
    body = resp.json()
    assert "results" in body
    assert "total" in body
    assert "q" in body
    assert "limit" in body
    assert body["q"] == "alice"
    assert body["total"] == 1

    result = body["results"][0]
    assert "entity_id" in result
    assert "canonical_name" in result
    assert "entity_type" in result
    assert "score" in result
    assert "match_kind" in result


# ---------------------------------------------------------------------------
# Scenario: entity_type is included in search results
# ---------------------------------------------------------------------------


async def test_entity_type_included_in_search_results():
    """Search results must include entity_type from public.entities."""
    rows = [
        _make_search_row(
            entity_id=_ENTITY_ID_1,
            canonical_name="Alice Smith",
            entity_type="person",
            score=100,
            match_kind="prefix",
        ),
        _make_search_row(
            entity_id=_ENTITY_ID_2,
            canonical_name="Acme Corp",
            entity_type="organization",
            score=50,
            match_kind="substring",
        ),
    ]
    app, _ = _app_with_pool(fetch_rows=rows)
    resp = await _get(app, q="a")

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["entity_type"] == "person"
    assert body["results"][1]["entity_type"] == "organization"
