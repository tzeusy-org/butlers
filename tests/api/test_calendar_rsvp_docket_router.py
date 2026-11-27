"""The invitation docket routes (bu-5ii4gj.9 S6, REQ-calendar-invitation-docket-005).

The snapshot and the approve path are faked; the pure evaluator and the card judgement run as they
are. Real approve behaviour is covered by the approvals suites; here the point is that a submit
re-evaluates every ticked card, approves each one individually through ``approve_owning_action``,
and refuses and names the ones that changed while still applying the rest.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from butlers.api.deps import get_db, get_mcp_manager
from butlers.api.routers import calendar_rsvp_docket as route
from butlers.core.temporal.rsvp_policy import InvitationFacts, Policy, evaluate_policies
from butlers.jobs import calendar_rsvp_docket as docket
from butlers.modules.approvals.execution_context import approval_tool_args_digest

pytestmark = pytest.mark.unit

START = datetime(2026, 7, 3, 9, 0, tzinfo=UTC)
FACTS = InvitationFacts(overlaps_focus_or_out_of_office=True)
FACTS_ANY = FACTS


def _policy(pid="p1", *, version=1, verdict="declined", revoked=None) -> Policy:
    return Policy.from_row(
        {
            "id": pid,
            "position": 1,
            "predicates": [{"kind": "overlaps_focus_or_out_of_office", "value": True}],
            "verdict": verdict,
            "send_updates": "none",
            "version": version,
            "revoked_at": revoked,
        }
    )


def approved_ids(result: dict) -> list[str]:
    return [r["action_id"] for r in result["results"]]


class _World:
    """Open docket cards plus a snapshot of the current invitations."""

    def __init__(self, monkeypatch, *, policies=None):
        self.policies = policies or [_policy()]
        self.cards: list[dict] = []
        self.entries: dict[str, docket.EntryState] = {}
        self.snap = docket.Snapshot()
        self.approve = AsyncMock(
            return_value=SimpleNamespace(data=SimpleNamespace(dispatched=False))
        )
        monkeypatch.setattr(docket, "docket_butler_configured", lambda: True)
        monkeypatch.setattr(docket, "read_policies", AsyncMock(side_effect=lambda p: self.policies))
        monkeypatch.setattr(
            route, "_open_docket_cards", AsyncMock(side_effect=lambda p: self.cards)
        )
        monkeypatch.setattr(
            docket, "take_snapshot", AsyncMock(side_effect=lambda *a, **k: self.snap)
        )
        monkeypatch.setattr("butlers.api.routers.approvals.approve_owning_action", self.approve)
        monkeypatch.setattr(
            "butlers.api.routers.approvals._decision_memory_writer_for", lambda *a: None
        )
        monkeypatch.setattr("butlers.api.audit_emit.authenticated_principal", lambda: "owner")

    def add(self, *, etag="e1", title="Board offsite", readable=True, evaluation=True, policy=None):
        """One parked card, and (unless ``evaluation`` is None) its entry in the snapshot."""
        policy = policy or self.policies[0]
        entry_id = str(uuid.uuid4())
        match = evaluate_policies([policy], FACTS).prepared
        request_id = str(uuid.uuid4())
        dossier = docket.build_dossier(match, request_id=request_id, entry_id=entry_id, etag="e1")
        args = {
            "entry_id": entry_id,
            "response_status": match.verdict,
            "send_updates": match.send_updates,
            "request_id": request_id,
        }
        card = {
            "id": uuid.uuid4(),
            "marker": docket.parse_marker(dossier["_evidence"]),
            "tool_args": args,
            "evidence": dossier["_evidence"],
        }
        self.cards.append(card)
        if evaluation:
            self.snap.entries[entry_id] = docket.EntryState(
                entry_id,
                {
                    "title": title,
                    "instance_starts_at": START,
                    "instance_ends_at": START + timedelta(hours=1),
                },
                etag,
                evaluate_policies(self.policies, FACTS) if readable else None,
                readable,
            )
        return card

    def displayed(self, card) -> str:
        return approval_tool_args_digest(card["tool_args"])

    def client(self) -> TestClient:
        app = FastAPI()
        app.include_router(route.router)
        app.dependency_overrides[get_db] = lambda: MagicMock()
        app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
        app.state.owner_auth_service = SimpleNamespace(
            rsvp_ledger_counts=AsyncMock(return_value={"counts": [{"policy_id": "p1"}]})
        )

        @app.middleware("http")
        async def _state(request, call_next):
            request.state.owner_authority = object()
            return await call_next(request)

        return TestClient(app)

    def submit(self, *cards):
        body = {
            "items": [{"action_id": str(c["id"]), "args_digest": self.displayed(c)} for c in cards]
        }
        response = self.client().post("/api/calendar/rsvp-docket/submit", json=body)
        return response


def test_the_docket_shows_every_exact_call_with_its_policy_and_matched_facts(monkeypatch) -> None:
    world = _World(monkeypatch)
    card = world.add()
    data = world.client().get("/api/calendar/rsvp-docket").json()["data"]
    [shown] = data["cards"]
    assert shown["action_id"] == str(card["id"]) and shown["state"] == "current"
    assert (shown["verdict"], shown["send_updates"]) == ("declined", "none")
    assert (shown["policy_id"], shown["policy_version"]) == ("p1", 1)
    assert shown["facts"] == [
        {"kind": "overlaps_focus_or_out_of_office", "note": "policy wants is True; observed True"}
    ]
    assert shown["event"]["title"] == "Board offsite"
    assert shown["args_digest"] == world.displayed(card)
    assert data["complete"] is True and data["unprepared"] == 0
    assert data["policy_agreement"] == [{"policy_id": "p1"}]


def test_an_invitation_whose_policies_disagree_names_them_and_has_no_card(monkeypatch) -> None:
    accept = _policy("pa", verdict="accepted")
    decline = _policy("pd", verdict="declined")
    world = _World(monkeypatch, policies=[accept, decline])
    entry_id = "inv-1"
    world.snap.entries[entry_id] = docket.EntryState(
        entry_id,
        {"title": "Standup", "instance_starts_at": START, "instance_ends_at": START},
        "e1",
        evaluate_policies([accept, decline], FACTS_ANY),
        True,
    )
    data = world.client().get("/api/calendar/rsvp-docket").json()["data"]
    assert data["cards"] == []
    [clash] = data["disagreed"]
    assert clash["entry_id"] == entry_id and clash["event"]["title"] == "Standup"
    assert sorted((p["id"], p["verdict"]) for p in clash["policies"]) == [
        ("pa", "accepted"),
        ("pd", "declined"),
    ]


def test_a_changed_event_or_policy_shows_as_changed_with_its_reason(monkeypatch) -> None:
    world = _World(monkeypatch)
    world.add(etag="e2")
    data = world.client().get("/api/calendar/rsvp-docket").json()["data"]
    assert (data["cards"][0]["state"], data["cards"][0]["reason"]) == ("changed", "stale_event")
    world = _World(monkeypatch)
    world.add()
    world.policies = [_policy(version=2)]
    data = world.client().get("/api/calendar/rsvp-docket").json()["data"]
    assert data["cards"][0]["reason"] == "policy_changed"


def test_an_outage_is_never_a_clean_docket(monkeypatch) -> None:
    world = _World(monkeypatch)
    world.add(readable=False)
    world.snap.degraded = ["calendar"]
    world.snap.unprepared["facts_unreadable"] = 3
    data = world.client().get("/api/calendar/rsvp-docket").json()["data"]
    assert data["complete"] is False and data["unprepared"] == 3
    assert data["degraded"] == ["calendar"]
    assert data["cards"][0]["state"] == "unverifiable"

    world.snap = docket.Snapshot(failure="invitations_unavailable")
    data = world.client().get("/api/calendar/rsvp-docket").json()["data"]
    assert data["complete"] is False and data["unprepared"] >= 1


def test_a_submit_approves_each_ticked_card_individually_and_leaves_the_rest(monkeypatch) -> None:
    world = _World(monkeypatch)
    ticked_a, ticked_b, left = world.add(), world.add(), world.add()
    result = world.submit(ticked_a, ticked_b).json()["data"]
    assert result["refused"] == []
    assert sorted(approved_ids(result)) == sorted([str(ticked_a["id"]), str(ticked_b["id"])])
    assert world.approve.await_count == 2
    for call in world.approve.await_args_list:
        assert call.kwargs["expected_tool_name"] == "calendar_respond"
        assert call.kwargs["promotion_eligible"] is False
        assert call.kwargs["action_butler"] == docket.docket_butler()
        assert call.kwargs["edits"] is None
        assert call.kwargs["expected_tool_args"]["entry_id"]
    assert str(left["id"]) not in approved_ids(result)
    assert {c.kwargs["parsed_id"] for c in world.approve.await_args_list} == {
        ticked_a["id"],
        ticked_b["id"],
    }


def test_a_changed_card_is_refused_and_named_while_the_others_are_applied(monkeypatch) -> None:
    world = _World(monkeypatch)
    good, moved = world.add(), world.add(etag="e2", title="Moved meeting")
    result = world.submit(good, moved).json()["data"]
    assert approved_ids(result) == [str(good["id"])]
    [refusal] = result["refused"]
    assert refusal["action_id"] == str(moved["id"]) and refusal["reason"] == "stale_event"
    assert refusal["entry_id"] == moved["marker"]["entry_id"]
    assert refusal["event"]["title"] == "Moved meeting"
    assert world.approve.await_count == 1


def test_a_card_that_no_longer_matches_its_policy_is_refused(monkeypatch) -> None:
    world = _World(monkeypatch)
    card = world.add()
    world.snap.entries[card["marker"]["entry_id"]].evaluation = evaluate_policies(
        [_policy()], InvitationFacts(overlaps_focus_or_out_of_office=False)
    )
    result = world.submit(card).json()["data"]
    assert approved_ids(result) == []
    assert result["refused"][0]["reason"] == "policy_no_longer_matches"
    world.approve.assert_not_awaited()


@pytest.mark.parametrize(
    ("policies", "reason"),
    [([_policy(version=2)], "policy_changed"), ([_policy(revoked=START)], "policy_revoked")],
)
def test_an_edited_or_revoked_policy_refuses_its_cards(monkeypatch, policies, reason) -> None:
    world = _World(monkeypatch)
    card = world.add()
    world.policies = policies
    result = world.submit(card).json()["data"]
    assert result["refused"][0]["reason"] == reason
    world.approve.assert_not_awaited()


def test_an_unreadable_fact_refuses_rather_than_approves(monkeypatch) -> None:
    world = _World(monkeypatch)
    card = world.add(readable=False)
    result = world.submit(card).json()["data"]
    assert approved_ids(result) == [] and result["refused"][0]["reason"] == "facts"
    world.approve.assert_not_awaited()


def test_a_card_that_is_no_longer_pending_is_refused(monkeypatch) -> None:
    world = _World(monkeypatch)
    card = world.add()
    world.cards.clear()
    result = world.submit(card).json()["data"]
    assert result["refused"] == [
        {
            "action_id": str(card["id"]),
            "entry_id": None,
            "event": None,
            "reason": "no_longer_pending",
        }
    ]


def test_a_refusal_by_the_approve_path_is_named_and_does_not_stop_the_rest(monkeypatch) -> None:
    world = _World(monkeypatch)
    first, second = world.add(), world.add()
    world.approve.side_effect = [HTTPException(409, "call changed"), SimpleNamespace(data=None)]
    result = world.submit(first, second).json()["data"]
    assert approved_ids(result) == [str(second["id"])]
    assert result["refused"][0]["reason"] == "approval_refused:409"


def test_the_submit_shape_is_closed(monkeypatch) -> None:
    world = _World(monkeypatch)
    card = world.add()
    client = world.client()
    url = "/api/calendar/rsvp-docket/submit"
    assert client.post(url, json={"items": []}).status_code == 422
    one = {"action_id": str(card["id"]), "args_digest": world.displayed(card)}
    assert client.post(url, json={"items": [one | {"verdict": "accepted"}]}).status_code == 422
    assert client.post(url, json={"items": [one], "actor": "owner"}).status_code == 422
    world.approve.assert_not_awaited()


def test_the_routes_need_the_owner_and_the_docket_butler(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(route.router)
    assert TestClient(app).get("/api/calendar/rsvp-docket").status_code == 401
    world = _World(monkeypatch)
    monkeypatch.setattr(docket, "docket_butler_configured", lambda: False)
    assert world.client().get("/api/calendar/rsvp-docket").status_code == 503


def test_the_route_approves_only_through_the_existing_approve_path() -> None:
    source = open(route.__file__).read()
    assert "approve_owning_action" in source
    assert "transition_pending_action" not in source and "UPDATE pending_actions" not in source


def test_a_card_whose_call_moved_since_display_is_refused(monkeypatch) -> None:
    world = _World(monkeypatch)
    card = world.add()
    shown = world.displayed(card)
    card["tool_args"]["send_updates"] = "all"  # the stored call changed after it was displayed
    body = {"items": [{"action_id": str(card["id"]), "args_digest": shown}]}
    result = world.client().post("/api/calendar/rsvp-docket/submit", json=body).json()["data"]
    assert result["refused"][0]["reason"] == "call_changed"
    world.approve.assert_not_awaited()


def test_a_card_approved_but_not_sent_is_not_reported_as_dispatched(monkeypatch) -> None:
    world = _World(monkeypatch)
    card = world.add()
    world.approve.return_value = SimpleNamespace(
        data=SimpleNamespace(
            status="approved",
            dispatched=False,
            execution_failure=SimpleNamespace(
                model_dump=lambda mode="json": {"code": "daemon_unreachable"}
            ),
        )
    )
    [outcome] = world.submit(card).json()["data"]["results"]
    assert outcome == {
        "action_id": str(card["id"]),
        "status": "approved",
        "dispatched": False,
        "execution_failure": {"code": "daemon_unreachable"},
    }


def test_an_unexpected_failure_stops_the_batch_and_keeps_what_already_committed(
    monkeypatch,
) -> None:
    world = _World(monkeypatch)
    first, second, third = world.add(), world.add(), world.add()
    ok = SimpleNamespace(data=SimpleNamespace(status="executed", dispatched=True))
    world.approve.side_effect = [ok, RuntimeError("db went away"), ok]
    data = world.submit(first, second, third).json()["data"]
    assert approved_ids(data) == [str(first["id"])]
    assert data["stopped"] is True
    assert {r["action_id"]: r["reason"] for r in data["refused"]} == {
        str(second["id"]): "outcome_unknown",
        str(third["id"]): "not_attempted",
    }
    assert world.approve.await_count == 2


def test_a_hung_dispatch_times_out_as_outcome_unknown(monkeypatch) -> None:
    import asyncio

    world = _World(monkeypatch)
    card = world.add()

    async def hang(**_):
        await asyncio.sleep(5)

    world.approve.side_effect = hang
    monkeypatch.setattr(route, "DISPATCH_TIMEOUT_S", 0.05)
    data = world.submit(card).json()["data"]
    assert data["refused"][0]["reason"] == "outcome_unknown" and data["stopped"] is True


def test_a_submit_without_an_owner_cookie_session_is_refused(monkeypatch) -> None:
    world = _World(monkeypatch)
    card = world.add()
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    # The router's own owner gate admits this caller (an API key, say); the cookie rule is the
    # handler's, so lift the gate to prove it holds on its own.
    app.dependency_overrides[route.require_dashboard_owner_control] = lambda: None
    response = TestClient(app).post(
        "/api/calendar/rsvp-docket/submit",
        json={"items": [{"action_id": str(card["id"]), "args_digest": world.displayed(card)}]},
    )
    assert response.status_code == 403
    world.approve.assert_not_awaited()


def test_the_batch_starts_no_card_it_could_not_finish_before_the_deadline(monkeypatch) -> None:
    world = _World(monkeypatch)
    first, second, third = world.add(), world.add(), world.add()
    ok = SimpleNamespace(data=SimpleNamespace(status="executed", dispatched=True))
    world.approve.return_value = ok
    # Each approve "takes" 100s: after card 1 the batch is at 100s, and 100 + 75 <= 255 still
    # fits card 2; after card 2 it is at 200s and 200 + 75 > 255, so card 3 is not started.
    ticks = iter([0.0, 0.0, 100.0, 200.0, 300.0, 400.0])
    monkeypatch.setattr(route, "_clock", lambda: next(ticks))
    data = world.submit(first, second, third).json()["data"]
    assert approved_ids(data) == [str(first["id"]), str(second["id"])]
    assert data["stopped"] is True
    assert data["refused"] == [
        {
            "action_id": str(third["id"]),
            "entry_id": None,
            "event": None,
            "reason": "not_attempted",
        }
    ]
    assert world.approve.await_count == 2
