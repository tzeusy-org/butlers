"""GET /api/decision-sets/{set_id}: the owner-facing read of one decision set version."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.api.routers import decision_sets as route
from butlers.core.capability_json import arguments_digest
from butlers.core.decision_sets import DecisionSet, DecisionSetItem, item_digest
from butlers.modules.approvals import decision_set_view

pytestmark = pytest.mark.unit

NOW = datetime.now(UTC)
GOAL = uuid4()
ARGS = {"channel": "telegram", "recipient": "12345", "message": "Running late"}


def _shown(args=ARGS):
    effect = uuid4()
    digest = arguments_digest(args)
    item = DecisionSetItem(
        0,
        effect,
        "messenger__notify",
        digest,
        None,
        "telegram",
        False,
        item_digest(effect, "messenger__notify", digest, None, "telegram", False),
    )
    return DecisionSet(
        uuid4(), 1, GOAL, 1, 1, uuid4(), "d" * 64, NOW + timedelta(hours=1), "open", (item,)
    ), effect


def _row(effect, args=ARGS):
    return {
        "id": effect,
        "tool_name": "notify",
        "tool_args": args,
        "status": "pending",
        "requested_at": NOW,
        "goal_id": GOAL,
        "goal_invocation_id": uuid4(),
        "goal_depth": 0,
        "goal_exposed": True,
        "goal_origin": "third_party",
        "goal_exposure_cause": "read",
        "goal_rule_possible": False,
    }


class _Pool:
    def __init__(self, rows=None, *, fail=False):
        self.rows, self.fail = rows or [], fail

    def acquire(self):
        pool = self

        class _Conn:
            async def fetch(self, sql, *args):
                if pool.fail:
                    raise RuntimeError("pool down")
                return pool.rows

        class _Ctx:
            async def __aenter__(self):
                return _Conn()

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _client(app, monkeypatch, shown, pools, *, switchboard=True):
    db = MagicMock(spec=DatabaseManager)
    if switchboard:
        db.pool.return_value = object()
    else:
        db.pool.side_effect = KeyError("switchboard")
    db.butler_names = ["general"]
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_dashboard_owner_control] = lambda: "owner"

    async def named(db_mgr, table, tracker=None):
        return pools

    async def ledger_read(self, set_id, version=None):
        return shown

    monkeypatch.setattr(route, "_find_named_approvals_pools", named)
    monkeypatch.setattr(decision_set_view.DecisionSetLedger, "read", ledger_read)
    monkeypatch.setattr(decision_set_view, "DecisionSetLedger", decision_set_view.DecisionSetLedger)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class _LedgerPool:
    def acquire(self):
        class _Ctx:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


async def test_an_exact_set_is_returned_with_facts_digests_and_degraded_meta_absent(
    app, monkeypatch
):
    shown, effect = _shown()
    async with _client(app, monkeypatch, shown, [("messenger", _Pool([_row(effect)]))]) as client:
        app.dependency_overrides[get_db]().pool.return_value = _LedgerPool()
        response = await client.get(f"/api/decision-sets/{shown.set_id}")
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert data["decidable"] is True and data["reason"] is None
    assert data["digest"] == "d" * 64 and data["unticked_outcome"] in {"left", "rejected"}
    [item] = data["items"]
    assert item["exact"] and item["item_digest"] == shown.items[0].item_digest
    assert {f["value"] for f in item["facts"]} >= {"Running late"}
    assert {f["label"] for f in item["lineage"]["facts"]} >= {"Origin", "Exposed by"}
    assert not (body.get("meta") or {}).get("sources_degraded")
    assert "tool_args" not in str(data)  # no raw args dump


async def test_a_changed_call_is_open_only_and_the_set_not_decidable(app, monkeypatch):
    shown, effect = _shown()
    changed = _row(effect, {**ARGS, "message": "Something else"})
    async with _client(app, monkeypatch, shown, [("messenger", _Pool([changed]))]) as client:
        app.dependency_overrides[get_db]().pool.return_value = _LedgerPool()
        data = (await client.get(f"/api/decision-sets/{shown.set_id}")).json()["data"]
    assert data["decidable"] is False and data["reason"] == "items"
    assert data["items"][0]["exact"] is False and data["items"][0]["reason"] == "changed"
    assert data["items"][0]["facts"] == []


async def test_an_unreadable_pool_is_named_in_degraded_meta_and_items_are_unavailable(
    app, monkeypatch
):
    shown, effect = _shown()
    pools = [("messenger", _Pool(fail=True))]
    async with _client(app, monkeypatch, shown, pools) as client:
        app.dependency_overrides[get_db]().pool.return_value = _LedgerPool()
        body = (await client.get(f"/api/decision-sets/{shown.set_id}")).json()
    assert body["meta"]["sources_degraded"] == ["messenger"]
    assert body["data"]["items"][0]["reason"] == "unavailable"
    assert body["data"]["decidable"] is False


async def test_unknown_set_is_404_and_a_missing_ledger_pool_is_503(app, monkeypatch):
    async with _client(app, monkeypatch, None, []) as client:
        app.dependency_overrides[get_db]().pool.return_value = _LedgerPool()
        assert (await client.get(f"/api/decision-sets/{uuid4()}")).status_code == 404
    async with _client(app, monkeypatch, None, [], switchboard=False) as client:
        assert (await client.get(f"/api/decision-sets/{uuid4()}")).status_code == 503
        assert (await client.get("/api/decision-sets/not-a-uuid")).status_code == 422
        assert (await client.get(f"/api/decision-sets/{uuid4()}?version=0")).status_code == 422


def test_the_route_is_guarded_by_the_owner_context_dependency():
    [read] = [r for r in route.router.routes if getattr(r, "path", "").endswith("{set_id}")]
    assert require_dashboard_owner_control in [d.call for d in read.dependant.dependencies]


# -- POST /api/decision-sets/{set_id}/decision ---------------------------------------------------


class _Resolver:
    def __init__(self, refuse=None):
        self.calls, self.refuse = [], refuse

    async def decide(self, set_id, version, selected, explicit, decision, owner):
        from butlers.capability_broker.owner_decisions import (
            ItemOutcome,
            OwnerDecisionRefused,
            SetDecision,
        )

        self.calls.append((set_id, version, dict(selected), dict(explicit), decision, owner))
        if self.refuse:
            raise OwnerDecisionRefused(self.refuse)
        return SetDecision(
            uuid4(), set_id, version, tuple(ItemOutcome(e, decision, decision) for e in selected)
        )


class _TxLedgerPool:
    """A ledger pool whose connection takes a transaction and has no owner-delivery ledger."""

    def acquire(self):
        class _Tx:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        class _Conn:
            def transaction(self):
                return _Tx()

            async def fetchval(self, *args):
                return False

        class _Ctx:
            async def __aenter__(self):
                return _Conn()

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _body(shown, **kw):
    item = shown.items[0]
    return {
        "version": 1,
        "decision": "approve",
        "selected": [{"effect_id": str(item.effect_id), "item_digest": item.item_digest}],
        **kw,
    }


async def _decide(app, monkeypatch, resolver, body_for, *, row=None):
    from butlers.api.routers.decision_sets import get_decision_set_resolver

    shown, effect = _shown()
    pools = [("messenger", _Pool([row or _row(effect)]))]
    client = _client(app, monkeypatch, shown, pools)
    app.dependency_overrides[get_db]().pool.return_value = _TxLedgerPool()
    app.dependency_overrides[get_decision_set_resolver] = lambda: resolver
    async with client:
        return shown, await client.post(
            f"/api/decision-sets/{shown.set_id}/decision", json=body_for(shown)
        )


async def test_the_dashboard_decides_the_ticked_items_of_the_shown_version(app, monkeypatch):
    resolver = _Resolver()
    shown, response = await _decide(app, monkeypatch, resolver, _body)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "approved"
    [(set_id, version, selected, explicit, decision, owner)] = resolver.calls
    item = shown.items[0]
    assert (set_id, version, decision) == (shown.set_id, 1, "approve")
    assert selected == {item.effect_id: item.item_digest} and explicit == {}
    assert (owner.decided_by, owner.via) == ("owner", "dashboard")


async def test_an_explicit_only_item_ticked_alone_goes_to_the_explicit_map(app, monkeypatch):
    # The shown item is rewritten explicit-only: its digest binds that flag.
    effect = uuid4()
    digest = arguments_digest(ARGS)
    item = DecisionSetItem(
        0,
        effect,
        "messenger__notify",
        digest,
        None,
        "telegram",
        True,
        item_digest(effect, "messenger__notify", digest, None, "telegram", True),
    )
    shown = DecisionSet(
        uuid4(), 1, GOAL, 1, 1, uuid4(), "d" * 64, NOW + timedelta(hours=1), "open", (item,)
    )
    from butlers.api.routers.decision_sets import get_decision_set_resolver

    resolver = _Resolver()
    client = _client(app, monkeypatch, shown, [("messenger", _Pool([_row(effect)]))])
    app.dependency_overrides[get_db]().pool.return_value = _TxLedgerPool()
    app.dependency_overrides[get_decision_set_resolver] = lambda: resolver
    async with client:
        response = await client.post(
            f"/api/decision-sets/{shown.set_id}/decision", json=_body(shown)
        )
    assert response.status_code == 200, response.text
    assert resolver.calls[0][2] == {} and resolver.calls[0][3] == {effect: item.item_digest}


async def test_no_resolver_wired_means_unavailable_and_nothing_is_decided(app, monkeypatch):
    shown, response = await _decide(app, monkeypatch, None, _body)
    assert response.status_code == 503
    assert response.json()["detail"] == {"error": "decision_set_refused", "reason": "unavailable"}


@pytest.mark.parametrize(
    ("refuse", "status"),
    [
        ("stale", 409),
        ("policy", 409),
        ("expired", 409),
        ("goal", 409),
        ("resource", 404),
        ("unavailable", 503),
    ],
)
async def test_a_resolver_refusal_is_a_closed_reason_and_status(app, monkeypatch, refuse, status):
    _, response = await _decide(app, monkeypatch, _Resolver(refuse=refuse), _body)
    assert response.status_code == status
    assert response.json()["detail"] == {"error": "decision_set_refused", "reason": refuse}


async def test_an_open_only_set_is_refused_before_the_resolver(app, monkeypatch):
    resolver = _Resolver()
    changed = _row(_shown()[1])  # a different effect id: this item's row is missing
    _, response = await _decide(app, monkeypatch, resolver, _body, row=changed)
    assert response.status_code == 409 and resolver.calls == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: {**b, "selected": []},
        lambda b: {**b, "decision": "ack"},
        lambda b: {**b, "selected": [{**b["selected"][0], "item_digest": "nothex"}]},
        lambda b: {**b, "selected": [b["selected"][0], b["selected"][0]]},
        lambda b: {**b, "selected": [{**b["selected"][0], "effect_id": str(uuid4())}]},
        lambda b: {**b, "version": 0},
    ],
)
async def test_a_malformed_or_foreign_selection_decides_nothing(app, monkeypatch, mutate):
    resolver = _Resolver()
    _, response = await _decide(app, monkeypatch, resolver, lambda s: mutate(_body(s)))
    assert response.status_code in (409, 422) and resolver.calls == []


def test_the_decision_route_is_owner_guarded():
    [post] = [r for r in route.router.routes if getattr(r, "path", "").endswith("/decision")]
    assert require_dashboard_owner_control in [d.call for d in post.dependant.dependencies]
