"""A global-scope `block` ingestion rule cannot be stored, so none can be silently ignored.

The Gmail global gate and the Switchboard pipeline act on skip, metadata_only and route_to
decisions, so a stored global `block` rule would be inert (bu-gtf1kp). The product surface
refuses it at create and PATCH; these tests pin that refusal and show what the evaluator returns
for a row that bypassed it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

pytestmark = pytest.mark.unit

_MODULE_NAME = "switchboard_api_router"
_router_path = Path(__file__).resolve().parents[2] / "roster" / "switchboard" / "api" / "router.py"
_RULES = "/api/switchboard/ingestion-rules"
_RULE_ID = "11111111-1111-1111-1111-111111111111"


def _db_dep():
    if _MODULE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
    return sys.modules[_MODULE_NAME]._get_db_manager


def _row(data: dict) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    row.keys = lambda: data.keys()
    row.__iter__ = lambda self: iter(data)
    return row


def _existing(scope: str, action: str) -> dict:
    return {
        "id": _RULE_ID,
        "scope": scope,
        "rule_type": "sender_domain",
        "condition": {"domain": "spam.example", "match": "exact"},
        "action": action,
        "priority": 10,
        "enabled": True,
        "name": "rule",
        "description": None,
        "created_by": "dashboard",
        "created_at": "2026-10-19T00:00:00+00:00",
        "updated_at": "2026-10-19T00:00:00+00:00",
        "deleted_at": None,
    }


def _wire(app, *fetchrow_results) -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value="UPDATE 1")
    pool.fetchrow = AsyncMock(side_effect=list(fetchrow_results) or None)
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app.dependency_overrides[_db_dep()] = lambda: db
    return pool


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_creating_a_global_block_rule_is_refused_and_writes_nothing(app) -> None:
    pool = _wire(app)
    async with _client(app) as client:
        response = await client.post(
            _RULES,
            json={
                "scope": "global",
                "rule_type": "sender_domain",
                "condition": {"domain": "spam.example", "match": "exact"},
                "action": "block",
                "priority": 10,
            },
        )

    assert response.status_code == 422
    assert "block" in response.text and "global" in response.text
    pool.execute.assert_not_awaited()
    pool.fetchrow.assert_not_awaited()


@pytest.mark.parametrize(
    ("scope", "action", "patch"),
    [
        ("global", "skip", {"action": "block"}),
        ("connector:gmail:gmail:user:dev", "block", {"scope": "global"}),
    ],
)
async def test_patching_a_rule_into_a_global_block_is_refused(
    app, scope: str, action: str, patch: dict
) -> None:
    pool = _wire(app, _row(_existing(scope, action)))
    async with _client(app) as client:
        response = await client.patch(f"{_RULES}/{_RULE_ID}", json=patch)

    assert response.status_code == 422
    pool.execute.assert_not_awaited()


def test_a_row_that_bypassed_the_api_is_returned_by_the_evaluator_as_block() -> None:
    """Residual gap: the DB CHECK constrains only connector-scope actions, so a hand-inserted
    global row is evaluated as `block`, an action neither global gate acts on."""
    evaluator = IngestionPolicyEvaluator("global", None)
    evaluator._rules = [
        {
            "id": "r1",
            "rule_type": "sender_domain",
            "condition": {"domain": "spam.example", "match": "exact"},
            "action": "block",
            "priority": 10,
        }
    ]

    decision = evaluator.evaluate(
        IngestionEnvelope(sender_address="bot@spam.example", source_channel="email")
    )

    assert decision.action == "block"
