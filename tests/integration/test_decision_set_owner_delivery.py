"""Real-PostgreSQL proof of the decision set card tokens (bu-jv1moa.27 P2).

The subject, presentation and ``od1`` tokens are the real ledger. The resolver is a recording
double: what it is *called with* is the contract here (the default selection recomputed at redeem,
never carried), and ``tests/capability_broker`` proves what the real resolver does with it.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.capability_broker.owner_decisions import (
    ItemOutcome,
    OwnerContext,
    OwnerDecisionRefused,
    SetDecision,
)
from butlers.core import owner_delivery as od
from butlers.modules.approvals.decision_set_delivery import (
    close_set_subject,
    open_set_subject,
    present_set_card,
    redeem_set_token,
)
from butlers.modules.approvals.decision_set_view import SetView, build_set_view
from tests.modules.approvals.test_decision_set_view import NOW, SEND, _item, _row, _set

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

OTHER = {**SEND, "message": "Be there at 5"}
CONTEXT = OwnerContext(decided_by="connector:telegram", via="telegram")


class Resolver:
    def __init__(self, refuse: str | None = None) -> None:
        self.calls: list[tuple] = []
        self.refuse = refuse

    async def decide(self, set_id, version, selected, explicit, decision, owner) -> SetDecision:
        self.calls.append((set_id, version, dict(selected), dict(explicit), decision, owner))
        if self.refuse:
            raise OwnerDecisionRefused(self.refuse)
        return SetDecision(
            uuid4(),
            set_id,
            version,
            tuple(ItemOutcome(e, decision, decision) for e in (*selected, *explicit)),
        )


@pytest.fixture
async def pool(db) -> asyncpg.Pool:
    pool = await db("core", "approvals", max_size=4)
    if await pool.fetchval("SELECT count(*) FROM public.entities WHERE 'owner' = ANY(roles)") == 0:
        await pool.execute(
            "INSERT INTO public.entities (canonical_name, entity_type, roles) "
            "VALUES ('Synthetic owner', 'person', ARRAY['owner'])"
        )
    return pool


def _view(*pairs, expires=NOW + timedelta(days=3650)) -> SetView:
    shown = _set(*(i for i, _ in pairs), expires=expires)
    rows = {i.effect_id: _row(i.effect_id, args) for i, args in pairs}
    return build_set_view(shown, rows, now=NOW)


def _two() -> SetView:
    return _view(
        (_item(0), SEND), (_item(1, OTHER, explicit=True), OTHER), (_item(2, OTHER), OTHER)
    )


async def _present(pool, view: SetView) -> od.Presentation:
    async with pool.acquire() as conn, conn.transaction():
        return await present_set_card(conn, view, owner_recipient="9001")


async def _redeem(pool, token, view, resolver, *, action, surface_owner=None):
    async with pool.acquire() as conn, conn.transaction():
        owner = surface_owner or await od.owner_entity_id(conn)
        return await redeem_set_token(
            conn,
            token,
            view=view,
            resolver=resolver,
            expected_action=action,
            owner_entity=owner,
            owner=CONTEXT,
        )


async def _subject_state(pool, view) -> str:
    return await pool.fetchval(
        "SELECT state FROM public.owner_delivery_subjects WHERE kind = 'decision_set' "
        "AND source_ref = $1",
        f"{view.set_id}:{view.version}",
    )


async def test_approve_decides_the_default_selection_recomputed_at_redeem(pool):
    view = _two()
    presentation = await _present(pool, view)
    assert set(presentation.tokens) == {"approve", "reject"}
    resolver = Resolver()
    redeemed = await _redeem(pool, presentation.tokens["approve"], view, resolver, action="approve")
    assert redeemed.outcome == "won"
    (set_id, version, selected, explicit, decision, owner) = resolver.calls[0]
    plain = [i for i in view.items if not i.explicit_only]
    assert (set_id, version, decision, owner) == (view.set_id, view.version, "approve", CONTEXT)
    assert selected == {i.effect_id: i.item_digest for i in plain} and explicit == {}
    assert await _subject_state(pool, view) == "decided"


async def test_reject_rejects_every_item_with_explicit_only_in_its_own_map(pool):
    view = _two()
    presentation = await _present(pool, view)
    resolver = Resolver()
    redeemed = await _redeem(pool, presentation.tokens["reject"], view, resolver, action="reject")
    assert redeemed.outcome == "won"
    _, _, selected, explicit, decision, _ = resolver.calls[0]
    assert decision == "reject"
    assert set(selected) | set(explicit) == {i.effect_id for i in view.items}
    assert set(explicit) == {i.effect_id for i in view.items if i.explicit_only}


async def test_a_replayed_token_answers_duplicate_and_decides_nothing_again(pool):
    view = _two()
    presentation = await _present(pool, view)
    resolver = Resolver()
    token = presentation.tokens["approve"]
    assert (await _redeem(pool, token, view, resolver, action="approve")).outcome == "won"
    again = await _redeem(pool, token, view, resolver, action="approve")
    assert again.outcome == "duplicate" and len(resolver.calls) == 1


async def test_a_set_that_changed_since_the_card_refuses_action_changed(pool):
    view = _two()
    presentation = await _present(pool, view)
    resolver = Resolver()
    changed = replace(view, digest="e" * 64)
    refused = await _redeem(
        pool, presentation.tokens["approve"], changed, resolver, action="approve"
    )
    assert (refused.outcome, refused.reason) == ("refused", "action_changed")
    # An item that stopped being exact makes the set undecidable: the same refusal.
    gone = build_set_view(_set(*(_item(0),)), {}, now=NOW)
    refused = await _redeem(pool, presentation.tokens["reject"], gone, resolver, action="reject")
    assert refused.outcome == "refused"
    assert resolver.calls == [] and await _subject_state(pool, view) != "decided"


async def test_a_token_for_the_other_action_or_a_dashboard_surface_is_refused(pool):
    view = _two()
    presentation = await _present(pool, view)
    resolver = Resolver()
    wrong = await _redeem(pool, presentation.tokens["approve"], view, resolver, action="reject")
    assert (wrong.outcome, wrong.reason) == ("refused", "action_mismatch")
    async with pool.acquire() as conn, conn.transaction():
        dash = await od.redeem(
            conn,
            presentation.tokens["approve"],
            kind="decision_set",
            source_ref=f"{view.set_id}:{view.version}",
            expected_action="approve",
            owner_entity=await od.owner_entity_id(conn),
            surface="dashboard",
            current_action_digest="0" * 64,
        )
    assert (dash.outcome, dash.reason) == ("refused", "wrong_surface")
    assert resolver.calls == []


async def test_a_token_is_refused_for_another_owner(pool):
    view = _two()
    presentation = await _present(pool, view)
    resolver = Resolver()
    refused = await _redeem(
        pool,
        presentation.tokens["approve"],
        view,
        resolver,
        action="approve",
        surface_owner=UUID(int=7),
    )
    assert (refused.outcome, refused.reason) == ("refused", "wrong_owner")
    assert resolver.calls == []


async def test_a_resolver_refusal_rolls_the_subject_back_and_keeps_the_token(pool):
    view = _two()
    presentation = await _present(pool, view)
    token = presentation.tokens["approve"]
    with pytest.raises(OwnerDecisionRefused):
        await _redeem(pool, token, view, Resolver(refuse="stale"), action="approve")
    assert await _subject_state(pool, view) != "decided"
    # Nothing was consumed: once the cause is gone the same token decides.
    ok = await _redeem(pool, token, view, Resolver(), action="approve")
    assert ok.outcome == "won"


async def test_a_dashboard_decision_closes_the_subject_so_the_card_goes_stale(pool):
    view = _two()
    presentation = await _present(pool, view)
    async with pool.acquire() as conn, conn.transaction():
        await close_set_subject(conn, view, "approve")
    assert await _subject_state(pool, view) == "decided"
    resolver = Resolver()
    refused = await _redeem(pool, presentation.tokens["approve"], view, resolver, action="approve")
    assert refused.outcome in ("refused", "duplicate") and resolver.calls == []


async def test_a_set_with_nothing_a_card_may_approve_has_no_subject(pool):
    view = _view((_item(0, explicit=True), SEND))
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(od.OwnerDeliveryRefused) as refused:
            await open_set_subject(conn, view)
    assert refused.value.reason == "set_not_decidable"
    assert await _subject_state(pool, view) is None


# -- POST /api/owner-delivery/actions with a set card token ---------------------------------------


async def _post(pool, monkeypatch, token, view, resolver, *, callback=True):
    import httpx
    from fastapi import FastAPI

    from butlers.api.deps import get_db, get_mcp_manager
    from butlers.api.routers import owner_delivery as route
    from butlers.api.routers.decision_sets import get_decision_set_resolver

    app = FastAPI()
    app.include_router(route.router)

    class _Db:
        def pool(self, name):
            return pool

    async def read(db_mgr, set_id, version, tracker):
        return pool, view

    monkeypatch.setattr(route, "read_set_view", read)
    monkeypatch.setattr(route, "_callback_authenticated", lambda request: callback)
    app.dependency_overrides[get_db] = lambda: _Db()
    app.dependency_overrides[get_mcp_manager] = lambda: None
    app.dependency_overrides[get_decision_set_resolver] = lambda: resolver
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/api/owner-delivery/actions",
            json={"token": token},
            headers={"X-Butlers-Decision-Actor": "owner@telegram"} if callback else {},
        )


async def test_the_actions_route_decides_a_set_card_from_telegram(pool, monkeypatch):
    view = _two()
    presentation = await _present(pool, view)
    resolver = Resolver()
    response = await _post(pool, monkeypatch, presentation.tokens["approve"], view, resolver)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "approved" and data["set_id"] == str(view.set_id)
    assert resolver.calls[0][4] == "approve" and resolver.calls[0][5].via == "telegram"


async def test_the_actions_route_refuses_a_set_card_token_from_the_dashboard(pool, monkeypatch):
    view = _two()
    presentation = await _present(pool, view)
    resolver = Resolver()
    response = await _post(
        pool, monkeypatch, presentation.tokens["approve"], view, resolver, callback=False
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "action_unsupported" and resolver.calls == []


async def test_the_actions_route_reports_a_changed_set_as_action_changed(pool, monkeypatch):
    view = _two()
    presentation = await _present(pool, view)
    resolver = Resolver()
    changed = replace(view, digest="e" * 64)
    response = await _post(pool, monkeypatch, presentation.tokens["reject"], changed, resolver)
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "action_changed" and resolver.calls == []


async def test_the_actions_route_is_unavailable_while_no_resolver_is_wired(pool, monkeypatch):
    view = _two()
    presentation = await _present(pool, view)
    response = await _post(pool, monkeypatch, presentation.tokens["approve"], view, None)
    assert response.status_code == 503
    assert await _subject_state(pool, view) != "decided"
