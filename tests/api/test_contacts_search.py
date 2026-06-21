"""Unit tests for GET /api/contacts/search (bu-mcz0o9).

Covers the contract of the read-only contact typeahead endpoint:
- blank / whitespace q returns 200 with an empty list and never touches the DB
- a name/alias match and a non-secret identifier match shape the response
- the identifier query reads relationship.entity_facts (the NON-secret store),
  never the dropped public.contact_info / secret public.entity_info
- the query is constrained to entity_type = 'person' and excludes merged/deleted
- entity_facts lookup degrades gracefully (name/alias matches survive)
- LIKE wildcards in q are escaped (literal substring search)
- 503 when the shared pool is unavailable

These mock ``pool.fetch`` and assert on the response shape + the SQL the
endpoint binds. The DB-level behaviour (real ILIKE / secret exclusion / join /
person-only filtering) is exercised in ``test_contacts_search_db.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.contacts import _get_db_manager

pytestmark = pytest.mark.unit

SEARCH_PATH = "/api/contacts/search"


def _make_record(row: dict) -> MagicMock:
    """Return a MagicMock that supports dict-style item access (asyncpg.Record-like)."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


def _name_row(*, canonical_name="Alice Smith", entity_id=None):
    return {"entity_id": entity_id or uuid4(), "canonical_name": canonical_name}


def _id_row(*, canonical_name="Bob Jones", entity_id=None, predicate="has-email", value=None):
    return {
        "entity_id": entity_id or uuid4(),
        "canonical_name": canonical_name,
        "matched_predicate": predicate,
        "matched_value": value if value is not None else "bob@example.com",
    }


def _wire(app: FastAPI, *, name_rows=None, id_rows=None, id_error=None, shared_pool_error=None):
    """Wire the app with a mock DatabaseManager.

    The endpoint runs the name/alias query first, then the entity_facts query, so
    ``pool.fetch`` is dispatched by the SQL text it receives.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    if shared_pool_error is not None:
        mock_db.credential_shared_pool.side_effect = shared_pool_error
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        return None

    pool = AsyncMock()

    async def _fetch(sql, *args):
        if "entity_facts" in sql:
            if id_error is not None:
                raise id_error
            return [_make_record(r) for r in (id_rows or [])]
        return [_make_record(r) for r in (name_rows or [])]

    pool.fetch = AsyncMock(side_effect=_fetch)
    mock_db.credential_shared_pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return pool


async def _get(app: FastAPI, **params):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(SEARCH_PATH, params=params)


# ---------------------------------------------------------------------------
# Blank / whitespace q → 200 empty, no DB hit
# ---------------------------------------------------------------------------


async def test_blank_q_returns_empty_without_db(app):
    pool = _wire(app)
    resp = await _get(app)  # no q param → defaults to ""
    assert resp.status_code == 200
    assert resp.json() == {"results": []}
    pool.fetch.assert_not_called()


async def test_whitespace_q_returns_empty_without_db(app):
    pool = _wire(app)
    resp = await _get(app, q="   ")
    assert resp.status_code == 200
    assert resp.json() == {"results": []}
    pool.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Name match
# ---------------------------------------------------------------------------


async def test_name_match_returns_person_without_matched_identifier(app):
    eid = uuid4()
    _wire(app, name_rows=[_name_row(canonical_name="Alice Smith", entity_id=eid)])
    resp = await _get(app, q="ali")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["entity_id"] == str(eid)
    assert result["canonical_name"] == "Alice Smith"
    assert result["matched_identifier"] is None


# ---------------------------------------------------------------------------
# Non-secret identifier match surfaces the matched identifier
# ---------------------------------------------------------------------------


async def test_identifier_match_surfaces_matched_identifier(app):
    _wire(
        app,
        id_rows=[
            _id_row(canonical_name="Bob Jones", predicate="has-email", value="bob@example.com")
        ],
    )
    resp = await _get(app, q="bob@")
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["canonical_name"] == "Bob Jones"
    assert result["matched_identifier"] == {"type": "email", "value": "bob@example.com"}


async def test_telegram_handle_prefix_stripped(app):
    _wire(
        app,
        id_rows=[_id_row(canonical_name="Chloe", predicate="has-handle", value="telegram:chloe99")],
    )
    resp = await _get(app, q="chloe")
    result = resp.json()["results"][0]
    assert result["matched_identifier"] == {"type": "handle", "value": "chloe99"}


# ---------------------------------------------------------------------------
# Identifier query reads the NON-secret store, never the retired/secret tables
# ---------------------------------------------------------------------------


async def test_identifier_query_reads_entity_facts_not_retired_tables(app):
    pool = _wire(app)
    await _get(app, q="anything")
    sqls = [call.args[0] for call in pool.fetch.call_args_list]
    joined = "\n".join(sqls)
    # The non-secret identifier store is relationship.entity_facts, filtered to
    # active has-* literal triples.
    assert "relationship.entity_facts" in joined
    assert "ef.validity = 'active'" in joined
    assert "ef.predicate LIKE 'has-%'" in joined
    # The dropped public.contact_info / public.contacts and the secret
    # public.entity_info store must never be referenced.
    assert "contact_info" not in joined
    assert "public.contacts" not in joined
    assert "entity_info" not in joined


# ---------------------------------------------------------------------------
# Person-only + merged/deleted exclusion
# ---------------------------------------------------------------------------


async def test_query_is_person_only_and_excludes_tombstoned(app):
    pool = _wire(app)
    await _get(app, q="x")
    joined = "\n".join(call.args[0] for call in pool.fetch.call_args_list)
    assert "e.entity_type = 'person'" in joined
    assert "merged_into" in joined
    assert "deleted_at" in joined


# ---------------------------------------------------------------------------
# Graceful degradation when relationship schema is absent
# ---------------------------------------------------------------------------


async def test_identifier_lookup_failure_degrades_to_name_matches(app):
    _wire(
        app,
        name_rows=[_name_row(canonical_name="Alice Smith")],
        id_error=RuntimeError("relation relationship.entity_facts does not exist"),
    )
    resp = await _get(app, q="ali")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["canonical_name"] == "Alice Smith"


# ---------------------------------------------------------------------------
# De-duplication across the two queries
# ---------------------------------------------------------------------------


async def test_entity_matched_by_both_is_deduped(app):
    eid = uuid4()
    _wire(
        app,
        name_rows=[_name_row(canonical_name="Dana Doe", entity_id=eid)],
        id_rows=[_id_row(canonical_name="Dana Doe", entity_id=eid, value="dana@x.com")],
    )
    resp = await _get(app, q="dana")
    results = resp.json()["results"]
    assert len(results) == 1
    # The identifier upgrades the name-only entry.
    assert results[0]["matched_identifier"] == {"type": "email", "value": "dana@x.com"}


# ---------------------------------------------------------------------------
# No-match → empty list
# ---------------------------------------------------------------------------


async def test_no_match_returns_empty_list(app):
    _wire(app)
    resp = await _get(app, q="zzz-nobody")
    assert resp.status_code == 200
    assert resp.json() == {"results": []}


# ---------------------------------------------------------------------------
# Wildcard escaping
# ---------------------------------------------------------------------------


async def test_like_wildcards_in_query_are_escaped(app):
    pool = _wire(app)
    await _get(app, q="50%_off")
    pattern = pool.fetch.call_args_list[0].args[1]
    # % and _ are escaped so they match literally, wrapped in substring wildcards.
    assert pattern == "%50\\%\\_off%"


# ---------------------------------------------------------------------------
# limit is forwarded as the second bind param
# ---------------------------------------------------------------------------


async def test_limit_is_bound(app):
    pool = _wire(app)
    await _get(app, q="a", limit=5)
    assert pool.fetch.call_args_list[0].args[2] == 5


# ---------------------------------------------------------------------------
# 503 when shared pool unavailable
# ---------------------------------------------------------------------------


async def test_shared_pool_unavailable_returns_503(app):
    _wire(app, shared_pool_error=KeyError("shared"))
    resp = await _get(app, q="alice")
    assert resp.status_code == 503
