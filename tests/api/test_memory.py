"""Tests for memory system API endpoints.

Condensed from 82 tests to ~10 tests (bu-egmz6).
Keeps: paginated list structures, 503/404 error paths, key data transforms.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.memory import _get_db_manager

pytestmark = pytest.mark.unit

_LIST_ENDPOINTS = [
    "/api/memory/episodes",
    "/api/memory/facts",
    "/api/memory/rules",
    "/api/memory/entities",
]


def _app_with_mock_db(
    app: FastAPI, *, fetch_rows=None, fetchval_result=0, fetchrow_result=None, pool_available=True
):
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]

    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool")

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


@pytest.mark.parametrize("path", _LIST_ENDPOINTS)
async def test_list_returns_paginated_structure(app, path):
    """All list endpoints return 200 with data[] and meta."""
    _app_with_mock_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body
    assert isinstance(body["data"], list)


@pytest.mark.parametrize("path", _LIST_ENDPOINTS)
async def test_list_pool_unavailable_returns_empty_or_503(app, path):
    """When pool is unavailable, lists return empty page or 503."""
    _app_with_mock_db(app, pool_available=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    assert resp.status_code in (200, 503)


async def test_stats_returns_structure(app):
    """GET /api/memory/stats returns wrapped MemoryStats data."""
    _app_with_mock_db(app, fetchval_result=5)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/stats")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "total_episodes" in data
    assert "total_facts" in data
    assert "total_rules" in data


async def test_get_fact_returns_detail(app):
    """GET /api/memory/facts/{id} returns fact data when found."""
    row = {
        "id": "fact-001",
        "subject": "user",
        "predicate": "prefers",
        "content": "dark mode",
        "importance": 5.0,
        "confidence": 0.9,
        "decay_rate": 0.008,
        "permanence": "standard",
        "source_butler": "atlas",
        "source_episode_id": None,
        "supersedes_id": None,
        "validity": "active",
        "scope": "global",
        "reference_count": 2,
        "created_at": "2025-06-01T12:00:00",
        "last_referenced_at": None,
        "last_confirmed_at": None,
        "tags": [],
        "metadata": {},
    }
    _app_with_mock_db(app, fetchrow_result=row)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/facts/fact-001")
    assert resp.status_code == 200
    assert resp.json()["data"]["subject"] == "user"


async def test_get_fact_missing_returns_404(app):
    """GET /api/memory/facts/{id} returns 404 when fact not found."""
    _app_with_mock_db(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/facts/nonexistent")
    assert resp.status_code == 404


async def test_memory_activity_returns_list(app):
    """GET /api/memory/activity returns a list."""
    _app_with_mock_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/activity")
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"], list)


async def test_get_entity_paginates_recent_facts_and_includes_session_id(app):
    """GET /api/memory/entities/{id} returns paged fact rows with provenance."""
    entity_id = "d2521b5f-02f5-46b2-8eff-8c9f71dff688"
    session_id = "2e513477-a432-4d68-952b-b95226df0aa1"

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": entity_id,
            "canonical_name": "Test Entity",
            "entity_type": "person",
            "aliases": [],
            "metadata": {},
            "created_at": "2025-06-01T12:00:00",
            "updated_at": "2025-06-01T12:00:00",
            "unidentified": False,
            "linked_contact_id": None,
            "linked_contact_name": None,
            "linked_contact_roles": [],
        }
    )
    mock_pool.fetchval = AsyncMock(return_value=2)

    async def _fetch(sql, *args):
        if "FROM facts f" in sql:
            return [
                {
                    "id": "fact-001",
                    "subject": "user",
                    "predicate": "prefers",
                    "content": "coffee",
                    "importance": 5.0,
                    "confidence": 0.9,
                    "decay_rate": 0.008,
                    "permanence": "standard",
                    "source_butler": "general",
                    "source_episode_id": "ep-001",
                    "session_id": session_id,
                    "supersedes_id": None,
                    "entity_id": entity_id,
                    "object_entity_id": None,
                    "validity": "active",
                    "scope": "global",
                    "reference_count": 1,
                    "created_at": "2025-06-01T12:00:00",
                    "last_referenced_at": None,
                    "last_confirmed_at": None,
                    "tags": [],
                    "metadata": {},
                }
            ]
        if "FROM public.entity_info" in sql:
            return []
        if "FROM public.entities WHERE id = ANY($1)" in sql:
            return [{"id": entity_id, "canonical_name": "Test Entity"}]
        return []

    mock_pool.fetch = AsyncMock(side_effect=_fetch)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool.return_value = mock_pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/memory/entities/{entity_id}?facts_limit=1")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["recent_facts_total"] == 2
    assert data["recent_facts_limit"] == 1
    assert data["recent_facts_has_more"] is True
    assert len(data["recent_facts"]) == 1
    assert data["recent_facts"][0]["source_butler"] == "general"
    assert data["recent_facts"][0]["session_id"] == session_id
