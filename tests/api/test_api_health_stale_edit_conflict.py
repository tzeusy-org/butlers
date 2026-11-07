"""A refused stale edit is a 409 on the health routes that pass the supersession guard.

``medication_update`` and ``condition_update`` hand the fact store the id they read as
``expected_supersedes_id``; the store raises ``StaleSupersessionTargetError`` when that fact is
no longer current. The routes used to map every ``ValueError`` to 404, so the refusal read as
a missing record. The routes key on the error type, not its text.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import butlers.tools.health as health_tools
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.modules.memory.storage import (
    OwnerAuthoredSupersessionError,
    StaleSupersessionTargetError,
)
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

# Trigger router discovery so the dependency function is importable from sys.modules
# (FastAPI dependency_overrides keys on object identity).
_APP_SEED = create_app(api_key="")
_health_get_db_manager = get_db

_ROUTES = [
    ("medications", "medication_update", {"dosage": "20mg"}),
    ("conditions", "condition_update", {"status": "managed"}),
]
_ROUTES_THAT_CAN_REFUSE_AN_OWNER_FACT = [
    *_ROUTES,
    ("research", "research_update", {"title": "Revised"}),
]


def _client() -> httpx.AsyncClient:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = AsyncMock()
    app = create_app(api_key="")
    app.dependency_overrides[_health_get_db_manager] = lambda: db
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("collection,tool,body", _ROUTES)
async def test_stale_edit_is_a_conflict_not_a_missing_record(monkeypatch, collection, tool, body):
    stale = StaleSupersessionTargetError("a stale target, whatever its wording")
    monkeypatch.setattr(health_tools, tool, AsyncMock(side_effect=stale))

    async with _client() as client:
        resp = await client.put(f"/api/health/{collection}/{uuid.uuid4()}", json=body)

    assert resp.status_code == 409
    assert "changed since it was read" in resp.json()["detail"]


@pytest.mark.parametrize("collection,tool,body", _ROUTES)
async def test_a_missing_record_is_still_a_404(monkeypatch, collection, tool, body):
    missing = ValueError("Record not found")
    monkeypatch.setattr(health_tools, tool, AsyncMock(side_effect=missing))

    async with _client() as client:
        resp = await client.put(f"/api/health/{collection}/{uuid.uuid4()}", json=body)

    assert resp.status_code == 404


@pytest.mark.parametrize("collection,tool,body", _ROUTES_THAT_CAN_REFUSE_AN_OWNER_FACT)
async def test_an_owner_authored_refusal_is_its_own_conflict(monkeypatch, collection, tool, body):
    refused = OwnerAuthoredSupersessionError("cannot supersede, whatever the wording")
    monkeypatch.setattr(health_tools, tool, AsyncMock(side_effect=refused))

    async with _client() as client:
        resp = await client.put(f"/api/health/{collection}/{uuid.uuid4()}", json=body)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "authored by the owner" in detail
    assert "changed since it was read" not in detail
