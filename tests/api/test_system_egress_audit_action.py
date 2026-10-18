"""Egress catalog actors name the audit-log action behind them (bu-vbznk).

The dashboard links each registered actor to ``/audit-log?action=<audit_action>``; the server,
not the client, says which action an actor stands for.
"""

from __future__ import annotations

import httpx
import pytest

from butlers.api.routers.system import _ACTOR_REGISTRY
from tests.api.test_system import _NOW, _make_app_with_db, _make_egress_db

pytestmark = pytest.mark.unit


def _row(operation: str, calls: int = 1) -> dict:
    return {
        "operation": operation,
        "last_seen_at": _NOW,
        "total_calls": calls,
        "first_seen_at": _NOW,
    }


async def _actors(rows: list[dict]) -> dict[str, dict]:
    mock_db = _make_egress_db(has_owner=True, audit_rows=rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/egress")
    assert resp.status_code == 200
    return {a["actor_id"]: a for a in resp.json()["data"]["actors"]}


def test_the_actor_registry_maps_one_action_to_one_actor() -> None:
    """The link is honest only while an actor has exactly one audit action."""
    actor_ids = [actor_id for actor_id, _ in _ACTOR_REGISTRY.values()]
    assert len(actor_ids) == len(set(actor_ids))


@pytest.mark.parametrize(("operation", "actor"), list(_ACTOR_REGISTRY.items()))
async def test_a_registered_actor_carries_its_audit_action(operation, actor) -> None:
    actors = await _actors([_row(operation)])

    assert actors[actor[0]]["audit_action"] == operation


async def test_aggregated_rows_keep_the_audit_action() -> None:
    actors = await _actors([_row("llm_api_call", 5), _row("llm_api_call", 3)])

    assert actors["anthropic.claude"]["total_calls"] == 8
    assert actors["anthropic.claude"]["audit_action"] == "llm_api_call"


async def test_the_other_bucket_has_no_audit_action() -> None:
    actors = await _actors([_row("exotic_op_a"), _row("exotic_op_b"), _row("telegram_send")])

    assert actors["other"]["total_calls"] == 2
    assert actors["other"]["audit_action"] is None
    assert actors["telegram.api"]["audit_action"] == "telegram_send"
