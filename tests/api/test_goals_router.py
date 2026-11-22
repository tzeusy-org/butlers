"""Goal-ledger read API: list, detail and region summaries (bu-0rbswe, RFC 0038 D19)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _goal(**over):
    row = {
        "id": uuid4(),
        "origin": "owner",
        "status": "running",
        "exposed": False,
        "admitted_exposed": False,
        "chain_depth": 0,
        "caused_by_goal": None,
        "caused_by_effect": None,
        "created_at": _NOW,
        "updated_at": _NOW,
        "regions": ["calendar"],
        "outcome": None,
    }
    row.update(over)
    return row


def _db(pool=None, *, butlers=()):
    db = MagicMock(spec=DatabaseManager)
    if pool is None:
        db.pool.side_effect = KeyError("switchboard")
    else:
        db.pool.return_value = pool
    db.butler_names = list(butlers)
    return db


async def _get(app, db, path):
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


async def test_list_maps_rows_and_binds_every_filter(app):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_goal()])

    resp = await _get(
        app,
        _db(pool),
        "/api/goals?origin=owner&exposed=false&state=running&region=calendar&limit=5",
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["next_cursor"] is None and body["limit"] == 5
    assert body["items"][0]["state"] == "running"
    assert body["items"][0]["regions"] == ["calendar"]
    sql, *args = pool.fetch.await_args.args
    assert args == ["owner", False, "running", "calendar", 6]
    assert "$1" in sql and "ORDER BY g.created_at DESC, g.id DESC" in sql


async def test_list_pages_with_an_opaque_cursor(app):
    rows = [_goal(created_at=_NOW - timedelta(minutes=i)) for i in range(3)]
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)

    first = (await _get(app, _db(pool), "/api/goals?limit=2")).json()
    assert len(first["items"]) == 2 and first["next_cursor"]

    pool.fetch = AsyncMock(return_value=[])
    second = await _get(app, _db(pool), f"/api/goals?limit=2&cursor={first['next_cursor']}")
    assert second.status_code == 200
    args = pool.fetch.await_args.args
    assert args[-3:-1] == (rows[1]["created_at"], rows[1]["id"])


async def test_list_rejects_a_bad_cursor_and_unknown_filter_values(app):
    pool = AsyncMock()
    assert (await _get(app, _db(pool), "/api/goals?cursor=nope")).status_code == 400
    assert (await _get(app, _db(pool), "/api/goals?origin=stranger")).status_code == 422
    assert (await _get(app, _db(pool), "/api/goals?state=paused")).status_code == 422


async def test_list_names_a_ledger_outage_instead_of_an_empty_page(app):
    resp = await _get(app, _db(None), "/api/goals")
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    assert resp.json()["sources_degraded"] == ["goal_ledger"]

    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=ConnectionError("down"))
    failed = await _get(app, _db(pool), "/api/goals")
    assert failed.json()["sources_degraded"] == ["goal_ledger"]


async def test_list_omits_degraded_flag_when_the_ledger_answers(app):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    resp = await _get(app, _db(pool), "/api/goals")
    assert resp.json().get("sources_degraded") is None


async def test_detail_is_not_found_versus_unavailable(app):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    assert (await _get(app, _db(pool), f"/api/goals/{uuid4()}")).status_code == 404
    assert (await _get(app, _db(None), f"/api/goals/{uuid4()}")).status_code == 503
    assert (await _get(app, _db(pool), "/api/goals/not-a-uuid")).status_code == 422

    broken = AsyncMock()
    broken.fetchrow = AsyncMock(side_effect=ConnectionError("down"))
    assert (await _get(app, _db(broken), f"/api/goals/{uuid4()}")).status_code == 503


async def test_detail_projects_tree_receipts_lineage_budget_and_outcome(app):
    parent_goal, parent_effect = uuid4(), uuid4()
    goal = _goal(
        origin="obligation",
        caused_by_goal=parent_goal,
        caused_by_effect=parent_effect,
        chain_depth=1,
    )
    lead, child, effect, spawned = uuid4(), uuid4(), uuid4(), uuid4()
    inv = {
        "parent_invocation_id": None,
        "role": "lead",
        "depth": 0,
        "status": "returned",
        "exposed": True,
        "exposure_cause": "read",
        "used_turns": 2,
        "max_turns": 9,
        "used_tokens": 10,
        "max_tokens": 99,
        "end_reason": None,
        "started_at": _NOW,
        "ended_at": _NOW,
    }
    invocations = [
        {"id": lead, **inv},
        {"id": child, **inv, "parent_invocation_id": lead, "role": "child", "depth": 1},
    ]
    effects = [
        {
            "id": effect,
            "invocation_id": lead,
            "capability": "messenger__send_message",
            "status": "committed",
            "approval_ref": "approval-1",
            "receipt_ref": "receipt-1",
            "delivery_ref": "delivery-1",
            "recipient_entity": None,
            "recipient_channel": "telegram",
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    ]
    budget = {
        k: 1
        for k in (
            "max_invocations reserved_invocations used_invocations max_children active_children "
            "max_calls reserved_calls used_calls max_tokens reserved_tokens used_tokens"
        ).split()
    }
    budget.update(max_spend=5, reserved_spend=0, used_spend=1, deadline=_NOW)
    outcome = {
        "outcome": "completed",
        "final_status": "completed",
        "delivery_failure": None,
        "relied_effects": [effect],
        "reply_effect_id": effect,
        "created_at": _NOW,
        "resolved_at": _NOW,
    }
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=[goal, budget, outcome])
    pool.fetch = AsyncMock(
        side_effect=[invocations, effects, [{"id": spawned, "caused_by_effect": effect}]]
    )

    resp = await _get(app, _db(pool), f"/api/goals/{goal['id']}")

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert [i["parent_invocation_id"] for i in data["invocations"]] == [None, str(lead)]
    assert data["effects"][0]["region"] == "messenger"
    assert data["effects"][0]["receipt_ref"] == "receipt-1"
    assert data["approvals"] == [
        {
            "effect_id": str(effect),
            "approval_ref": "approval-1",
            "effect_status": "committed",
            "capability": "messenger__send_message",
            "caused_by_goal": str(parent_goal),
            "caused_by_effect": str(parent_effect),
            "caused_goals": [str(spawned)],
        }
    ]
    assert data["budget"]["max_spend"] == "5" or float(data["budget"]["max_spend"]) == 5
    assert data["goal_outcome"]["final_status"] == "completed"
    assert data["exposure_causes"] == ["read"]
    assert "args" not in str(data) and "grant" not in str(data)


async def test_regions_join_goals_and_obligations_and_name_failed_sources(app):
    goal_id = uuid4()
    ledger = AsyncMock()
    ledger.fetch = AsyncMock(
        side_effect=[
            [
                {
                    "region": "calendar",
                    "id": goal_id,
                    "status": "completed",
                    "origin": "owner",
                    "created_at": _NOW,
                }
            ],
            [{"name": "daily-sync", "source": "toml", "cron": "0 8 * * *", "next_run_at": _NOW}],
        ]
    )
    broken = AsyncMock()
    broken.fetch = AsyncMock(side_effect=ConnectionError("down"))
    db = MagicMock(spec=DatabaseManager)
    db.butler_names = ["calendar", "travel"]
    db.pool.side_effect = lambda name: (
        {"switchboard": ledger, "calendar": ledger}.get(name, broken)
        if name != "travel"
        else broken
    )

    resp = await _get(app, db, "/api/goals/regions")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    [region] = body["data"]["regions"]
    assert region["region"] == "calendar"
    assert region["recent_goals"][0]["id"] == str(goal_id)
    assert region["obligations_due"][0]["name"] == "daily-sync"
    assert body["data"]["sources_degraded"] == ["obligations:travel"]
    assert body["meta"]["sources_degraded"] == ["obligations:travel"]


async def test_regions_flag_the_ledger_when_it_is_unreachable(app):
    resp = await _get(app, _db(None), "/api/goals/regions")
    assert resp.status_code == 200
    assert resp.json()["data"]["sources_degraded"] == ["goal_ledger"]
    assert resp.json()["data"]["regions"] == []


async def test_list_filters_by_outcome_and_a_created_at_range(app):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    since, until = "2026-11-01T00:00:00%2B00:00", "2026-11-02T00:00:00%2B00:00"

    resp = await _get(app, _db(pool), f"/api/goals?outcome=failed&since={since}&until={until}")

    assert resp.status_code == 200, resp.text
    sql, *args = pool.fetch.await_args.args
    assert args == [
        "failed",
        datetime(2026, 11, 1, tzinfo=UTC),
        datetime(2026, 11, 2, tzinfo=UTC),
        26,
    ]
    assert "o.outcome = $1" in sql and "g.created_at >= $2" in sql and "g.created_at < $3" in sql


async def test_list_rejects_bad_outcome_and_time_input(app):
    db = _db(AsyncMock())
    assert (await _get(app, db, "/api/goals?outcome=won")).status_code == 422
    assert (await _get(app, db, "/api/goals?since=yesterday")).status_code == 422
    assert (await _get(app, db, "/api/goals?since=2026-11-01T00:00:00")).status_code == 422
    inverted = "since=2026-11-02T00:00:00%2B00:00&until=2026-11-01T00:00:00%2B00:00"
    assert (await _get(app, db, f"/api/goals?{inverted}")).status_code == 422


async def test_a_cursor_only_continues_the_filter_set_that_minted_it(app):
    rows = [_goal(created_at=_NOW - timedelta(minutes=i)) for i in range(3)]
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    first = (await _get(app, _db(pool), "/api/goals?limit=2&outcome=completed")).json()
    cursor = first["next_cursor"]

    same = await _get(app, _db(pool), f"/api/goals?limit=2&outcome=completed&cursor={cursor}")
    other = await _get(app, _db(pool), f"/api/goals?limit=2&outcome=failed&cursor={cursor}")
    bare = await _get(app, _db(pool), f"/api/goals?limit=2&cursor={cursor}")

    assert same.status_code == 200
    assert other.status_code == 400 and bare.status_code == 400
