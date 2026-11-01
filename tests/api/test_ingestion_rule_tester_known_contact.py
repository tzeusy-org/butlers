"""bu-q7vx1q.88.3: the dry-run rule tester can try "except people I know".

``envelope.known_contact`` is optional. ``true`` passes a flagged skip rule over; omitted and
``false`` leave it in force, matching a live envelope whose classification is unknown or negative.
"""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.ingestion_policy import IngestionPolicyEvaluator

pytestmark = pytest.mark.unit

_MODULE_NAME = "switchboard_api_router"
_router_path = Path(__file__).resolve().parents[2] / "roster" / "switchboard" / "api" / "router.py"

_RULE = {
    "id": "r1",
    "rule_type": "sender_domain",
    "condition": {"domain": "bulk.example", "match": "exact", "except_known_contacts": True},
    "action": "skip",
    "priority": 10,
    "name": None,
    "created_at": "2026-02-01T00:00:00Z",
    "created_by": "dashboard",
    "promoted_from_suggestion_id": None,
}


def _db_dependency():
    if _MODULE_NAME not in sys.modules:
        importlib.import_module("butlers.api._roster.switchboard.router")
    return get_db


async def _test(app, envelope: dict) -> dict:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = AsyncMock()
    app.dependency_overrides[_db_dependency()] = lambda: db

    async def load(self) -> None:
        self._rules = [_RULE]
        self._last_loaded_at = time.monotonic()

    body = {"scope": "global", "envelope": {"sender_address": "a@bulk.example", **envelope}}
    with patch.object(IngestionPolicyEvaluator, "ensure_loaded", load):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/ingestion-rules/test", json=body)
    assert resp.status_code == 200
    return resp.json()["data"]


@pytest.mark.parametrize(
    ("envelope", "matched"),
    [({"known_contact": True}, False), ({"known_contact": False}, True), ({}, True)],
    ids=["known", "stranger", "unclassified"],
)
async def test_the_tester_honors_an_optional_known_contact_input(
    app, envelope: dict, matched: bool
) -> None:
    assert (await _test(app, envelope))["matched"] is matched
