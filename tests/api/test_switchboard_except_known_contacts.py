"""bu-q7vx1q.88.3: the ingestion rule API persists and validates "except people I know".

The flag lives in the rule's ``condition`` JSONB (no migration). It round-trips on create and
patch, is stored only when true, and is refused (422) on any action other than block or skip.
"""

from __future__ import annotations

import datetime
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db

pytestmark = pytest.mark.unit

_MODULE_NAME = "switchboard_api_router"
_router_path = Path(__file__).resolve().parents[2] / "roster" / "switchboard" / "api" / "router.py"

_RULE = {
    "id": "11111111-1111-1111-1111-111111111111",
    "scope": "global",
    "rule_type": "sender_domain",
    "condition": {"domain": "bulk.example", "match": "exact", "except_known_contacts": True},
    "action": "skip",
    "priority": 10,
    "enabled": True,
    "name": None,
    "description": None,
    "created_by": "dashboard",
    "created_at": datetime.datetime(2026, 3, 8, tzinfo=datetime.UTC),
    "updated_at": datetime.datetime(2026, 3, 8, tzinfo=datetime.UTC),
    "deleted_at": None,
}


def _db_dependency():
    if _MODULE_NAME not in sys.modules:
        importlib.import_module("butlers.api._roster.switchboard.router")
    return get_db


def _row(data: dict):
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    row.keys = lambda: data.keys()
    row.__iter__ = lambda self: iter(data)
    return row


def _mock_pool(app, *, fetchrow_side_effect) -> AsyncMock:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock(return_value="UPDATE 1")
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app.dependency_overrides[_db_dependency()] = lambda: db
    return pool


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _stored_condition(pool: AsyncMock) -> dict:
    sql, _scope, _type, condition, *_ = pool.fetchrow.await_args_list[-1].args
    assert "INSERT INTO switchboard.ingestion_rules" in sql
    return condition


_CREATE = {
    "scope": "global",
    "rule_type": "sender_domain",
    "condition": {"domain": "bulk.example", "match": "exact", "except_known_contacts": True},
    "action": "skip",
    "priority": 10,
}


async def test_the_flag_is_stored_on_create_and_returned(app) -> None:
    pool = _mock_pool(app, fetchrow_side_effect=[_row(_RULE)])
    async with _client(app) as client:
        resp = await client.post("/api/switchboard/ingestion-rules", json=_CREATE)

    assert resp.status_code == 201
    assert resp.json()["data"]["condition"]["except_known_contacts"] is True
    assert _stored_condition(pool)["except_known_contacts"] is True


async def test_a_false_flag_is_not_stored(app) -> None:
    rule = {**_RULE, "condition": {"domain": "bulk.example", "match": "exact"}}
    pool = _mock_pool(app, fetchrow_side_effect=[_row(rule)])
    body = {**_CREATE, "condition": {**_CREATE["condition"], "except_known_contacts": False}}
    async with _client(app) as client:
        resp = await client.post("/api/switchboard/ingestion-rules", json=body)

    assert resp.status_code == 201
    assert "except_known_contacts" not in _stored_condition(pool)


@pytest.mark.parametrize("action", ["metadata_only", "low_priority_queue", "pass_through"])
async def test_the_flag_is_refused_on_an_action_that_cannot_honor_it(app, action: str) -> None:
    _mock_pool(app, fetchrow_side_effect=[_row(_RULE)])
    async with _client(app) as client:
        resp = await client.post(
            "/api/switchboard/ingestion-rules", json={**_CREATE, "action": action}
        )

    assert resp.status_code == 422


async def test_changing_a_flagged_rule_to_another_action_is_refused(app) -> None:
    stored = {**_RULE, "condition": json.dumps(_RULE["condition"])}
    _mock_pool(app, fetchrow_side_effect=[_row(stored)])
    async with _client(app) as client:
        resp = await client.patch(
            f"/api/switchboard/ingestion-rules/{_RULE['id']}", json={"action": "metadata_only"}
        )

    assert resp.status_code == 422


async def test_patching_the_condition_round_trips_the_flag(app) -> None:
    unflagged = {**_RULE, "condition": {"domain": "bulk.example", "match": "exact"}}
    pool = _mock_pool(app, fetchrow_side_effect=[_row(unflagged), _row(_RULE)])
    async with _client(app) as client:
        resp = await client.patch(
            f"/api/switchboard/ingestion-rules/{_RULE['id']}",
            json={"condition": _CREATE["condition"]},
        )

    assert resp.status_code == 200
    assert resp.json()["data"]["condition"]["except_known_contacts"] is True
    assert pool.fetchrow.await_args_list[-1].args[1] == _CREATE["condition"]
