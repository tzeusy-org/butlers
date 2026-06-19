"""Tests for /api/search contact search over person entities (bu-tzyuh).

Covers:
- Contact search matches a person entity on canonical_name ILIKE.
- Contact search matches on entity_facts object ILIKE (channel value).
- Email/phone snippet comes from entity_facts has-email / has-phone.
- Person entities still appear when matched by name only (no channels).
- Exception during the person-entity fetch is swallowed (warning logged, no crash).

Contact search now reads ``public.entities`` (``entity_type = 'person'``); the
SQL no longer references ``public.contacts``.  Test fetch stubs dispatch on the
``entity_type = 'person'`` marker (contact search) vs the ``has-email`` snippet
marker.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.search import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(row: dict) -> MagicMock:
    """Return a MagicMock that supports dict-style item access."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


def _wire_app(app: FastAPI, *, pool: AsyncMock) -> None:
    """Wire the search router with a pool that returns a fixed result."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["relationship"]
    mock_db.pool = MagicMock(return_value=pool)
    app.dependency_overrides[_get_db_manager] = lambda: mock_db


async def _get(app: FastAPI, q: str) -> dict:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/search?q={q}")
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# Contacts search — name match
# ---------------------------------------------------------------------------


async def test_contact_found_by_name(app: FastAPI) -> None:
    """Person entity matched by canonical_name ILIKE → appears with correct title."""
    entity_id = uuid4()

    contact_row = _make_record({"id": entity_id, "name": "Alice Smith", "entity_id": entity_id})
    ef_snippet_row = _make_record(
        {"entity_id": entity_id, "predicate": "has-email", "object": "alice@example.com"}
    )

    pool = AsyncMock()

    # fetch dispatch: contact search (person entities) vs entity_facts snippet
    async def _fetch(sql, *args, **kwargs):
        if "has-email" in sql or "has-phone" in sql:
            return [ef_snippet_row]
        if "entity_type = 'person'" in sql:
            return [contact_row]
        return []

    pool.fetch = AsyncMock(side_effect=_fetch)
    pool.fetchval = AsyncMock(return_value=None)  # fan_out not used here

    # fan_out must return empty dicts to avoid errors in session/state branches
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["relationship"]
    mock_db.pool = MagicMock(return_value=pool)
    mock_db.fan_out = AsyncMock(return_value={})
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    body = await _get(app, "alice")

    contacts = body["data"]["contacts"]
    assert len(contacts) == 1
    assert contacts[0]["title"] == "Alice Smith"
    assert contacts[0]["snippet"] == "alice@example.com"
    assert contacts[0]["id"] == str(entity_id)


async def test_contact_without_entity_no_snippet(app: FastAPI) -> None:
    """Contact with entity_id=None → no snippet, still appears."""
    contact_id = uuid4()

    contact_row = _make_record({"id": contact_id, "name": "Bob", "entity_id": None})

    pool = AsyncMock()

    async def _fetch(sql, *args, **kwargs):
        if "entity_type = 'person'" in sql:
            return [contact_row]
        return []

    pool.fetch = AsyncMock(side_effect=_fetch)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["relationship"]
    mock_db.pool = MagicMock(return_value=pool)
    mock_db.fan_out = AsyncMock(return_value={})
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    body = await _get(app, "bob")

    contacts = body["data"]["contacts"]
    assert len(contacts) == 1
    assert contacts[0]["title"] == "Bob"
    assert contacts[0]["snippet"] == ""


async def test_contact_search_exception_swallowed(app: FastAPI) -> None:
    """DB error during contact search → empty contacts, no 500."""
    pool = AsyncMock()

    async def _fetch(sql, *args, **kwargs):
        if "entity_type = 'person'" in sql:
            raise RuntimeError("db gone")
        return []

    pool.fetch = AsyncMock(side_effect=_fetch)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["relationship"]
    mock_db.pool = MagicMock(return_value=pool)
    mock_db.fan_out = AsyncMock(return_value={})
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    body = await _get(app, "anything")

    assert body["data"]["contacts"] == []


async def test_empty_query_returns_empty_results(app: FastAPI) -> None:
    """Empty query string → immediate empty response without DB calls."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.fan_out = AsyncMock(return_value={})
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/search?q=")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["contacts"] == []
    assert body["data"]["entities"] == []


# ---------------------------------------------------------------------------
# Snippet from entity_facts — email and phone
# ---------------------------------------------------------------------------


async def test_contact_snippet_includes_phone(app: FastAPI) -> None:
    """has-phone triple in entity_facts → phone appears in snippet."""
    contact_id = uuid4()
    entity_id = uuid4()

    contact_row = _make_record({"id": contact_id, "name": "Charlie", "entity_id": entity_id})
    ef_row = _make_record(
        {"entity_id": entity_id, "predicate": "has-phone", "object": "+6591234567"}
    )

    pool = AsyncMock()

    async def _fetch(sql, *args, **kwargs):
        if "has-email" in sql or "has-phone" in sql:
            return [ef_row]
        if "entity_type = 'person'" in sql:
            return [contact_row]
        return []

    pool.fetch = AsyncMock(side_effect=_fetch)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["relationship"]
    mock_db.pool = MagicMock(return_value=pool)
    mock_db.fan_out = AsyncMock(return_value={})
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    body = await _get(app, "charlie")

    contacts = body["data"]["contacts"]
    assert len(contacts) == 1
    assert "+6591234567" in contacts[0]["snippet"]


async def test_contact_snippet_email_and_phone_both_shown(app: FastAPI) -> None:
    """Both has-email and has-phone → snippet shows 'email · phone'."""
    contact_id = uuid4()
    entity_id = uuid4()

    contact_row = _make_record({"id": contact_id, "name": "Dana", "entity_id": entity_id})
    ef_email = _make_record(
        {"entity_id": entity_id, "predicate": "has-email", "object": "dana@example.com"}
    )
    ef_phone = _make_record(
        {"entity_id": entity_id, "predicate": "has-phone", "object": "+6599887766"}
    )

    pool = AsyncMock()

    async def _fetch(sql, *args, **kwargs):
        if "has-email" in sql or "has-phone" in sql:
            return [ef_email, ef_phone]
        if "entity_type = 'person'" in sql:
            return [contact_row]
        return []

    pool.fetch = AsyncMock(side_effect=_fetch)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["relationship"]
    mock_db.pool = MagicMock(return_value=pool)
    mock_db.fan_out = AsyncMock(return_value={})
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    body = await _get(app, "dana")

    contacts = body["data"]["contacts"]
    assert len(contacts) == 1
    snippet = contacts[0]["snippet"]
    assert "dana@example.com" in snippet
    assert "+6599887766" in snippet
    assert "·" in snippet
