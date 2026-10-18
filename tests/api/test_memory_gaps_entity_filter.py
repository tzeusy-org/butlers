"""GET /api/memory/gaps?entity_id= reaches every pool query, and a bad id is a 400."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest

from butlers.api.routers.memory import _get_db_manager

pytestmark = pytest.mark.unit

_ENTITY = uuid.uuid4()
_NOW = datetime(2026, 6, 1, tzinfo=UTC)


class _GapDB:
    butler_names = ["atlas", "memory"]

    def pool(self, name: str):
        return name


def _gap_row(entity_id: uuid.UUID) -> dict:
    return {
        "id": uuid.uuid4(),
        "entity_id": entity_id,
        "entity_name": "Mei",
        "predicate": "child_name",
        "question_summary": "What is Mei's daughter's name?",
        "status": "open",
        "coverage_state": "unknown",
        "asked_at": _NOW,
        "expires_at": _NOW,
        "answered_by_ref": None,
        "delivery_failed": False,
        "last_error": None,
        "origins": [],
    }


def _wire(app, monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def fake_list_gaps(pool, **kwargs):
        calls.append({"pool": pool, **kwargs})
        return [_gap_row(kwargs["entity_id"])] if kwargs.get("entity_id") else []

    monkeypatch.setattr("butlers.modules.memory.knowledge_gaps.list_gaps", fake_list_gaps)
    app.dependency_overrides[_get_db_manager] = lambda: _GapDB()
    return calls


async def _get(app, **params):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/memory/gaps", params=params)


async def test_entity_id_is_passed_to_every_pool_query(app, monkeypatch):
    calls = _wire(app, monkeypatch)

    resp = await _get(app, entity_id=str(_ENTITY))

    assert resp.status_code == 200
    assert {c["pool"] for c in calls} == {"atlas", "memory"}
    assert all(c["entity_id"] == _ENTITY for c in calls)


async def test_an_unfiltered_listing_does_not_pass_entity_id(app, monkeypatch):
    calls = _wire(app, monkeypatch)

    resp = await _get(app)

    assert resp.status_code == 200
    assert calls and all("entity_id" not in c for c in calls)


async def test_a_malformed_entity_id_is_a_400_before_any_pool_is_queried(app, monkeypatch):
    calls = _wire(app, monkeypatch)

    resp = await _get(app, entity_id="not-a-uuid")

    assert resp.status_code == 400
    assert calls == []
