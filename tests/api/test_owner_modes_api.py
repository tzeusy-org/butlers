"""The owner mode-controls dashboard routes (bu-dhloi1.21 PR8, dark).

The real writers run against an in-memory switchboard ledger with a movable
clock, and a fake step-up store that admits a proof only for the exact binding
its options call issued, as ``dashboard_stepup.consume`` does. The writers and
their SQL rules are covered against Postgres in the sw_076/sw_077 suites.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.deps import get_db
from butlers.api.routers import owner_modes as routes
from butlers.core.dashboard_stepup import (
    DashboardStepUpProof,
    dashboard_stepup_proof,
    verified_dashboard_session,
)
from butlers.perception import flags

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
SESSION = "session-digest"
ACTION = "00000000-0000-4000-8000-0000000000a1"
ACTION_2 = "00000000-0000-4000-8000-0000000000a2"
NONCE = "nonce-1"


class _Ledger:
    """The switchboard mode tables, the database clock and the step-up store."""

    def __init__(self):
        self.now = T0
        self.entries: dict[str, dict] = {}
        self.overlay: dict[str, dict] = {}
        self.issued: dict[str, tuple] = {}
        self.consumed: list[tuple] = []

    # connection interface -------------------------------------------------------

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchval(self, sql, *args):
        if sql == "SELECT now()":
            return self.now
        if "current_database()" in sql:
            return "butlers:switchboard"
        if "dashboard_stepup.consume" in sql:
            phase, nonce, session, kind, target, request_digest, action_digest, _result = args
            binding = (kind, target, request_digest, action_digest)
            self.consumed.append((phase, binding))
            admitted = session == SESSION and self.issued.get(nonce) == binding
            if phase == "begin":
                return {"state": "admitted" if admitted else "refused"}
            return {"state": "completed" if admitted else "refused"}
        raise AssertionError(f"unexpected fetchval {sql}")

    async def fetchrow(self, sql, mode):
        if "owner_mode_overlay" in sql:
            return self.overlay.get(mode)
        if "owner_mode_entries" in sql:
            return self.entries.get(mode)
        raise AssertionError(f"unexpected fetchrow {sql}")

    async def execute(self, sql, *values):
        if "owner_mode_entries" in sql:
            mode, version, since, until, cause, exited_at, _action = values
            self.entries[mode] = {
                "version": version,
                "since": since,
                "until": until,
                "cause": cause,
                "exited_at": exited_at,
            }
            return
        if "owner_mode_overlay" in sql:
            mode, version, duration_s, holds, break_through, _action = values
            self.overlay[mode] = {
                "version": version,
                "duration_s": duration_s,
                "holds": holds,
                "break_through": break_through,
            }
            return
        raise AssertionError(f"unexpected execute {sql}")


class _Pool:
    def __init__(self, ledger):
        self.ledger = ledger

    @asynccontextmanager
    async def acquire(self):
        yield self.ledger


class _Db:
    def __init__(self, ledger):
        self.ledger = ledger

    def pool(self, name):
        assert name == "switchboard"
        return _Pool(self.ledger)


class _AuthService:
    """Issues one assertion per options call and remembers the binding it bound."""

    def __init__(self, ledger):
        self.ledger = ledger
        self.calls: list[tuple] = []

    async def stepup_options(self, session, binding):
        assert binding.kind == "mode_loosen"
        self.calls.append((session, binding))
        self.ledger.issued[NONCE] = (
            binding.kind,
            binding.target,
            binding.request_digest,
            binding.action_digest,
        )
        return {"challenge": NONCE}


class _Explodes:
    """Any use at all fails the test: the dark route must not reach it."""

    def __getattr__(self, name):
        raise AssertionError(f"touched {name}")


def _app(db, *, session: str | None = SESSION, service=None) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def owner_auth(request, call_next):  # what OwnerAuthMiddleware sets
        token = verified_dashboard_session.set(session)
        nonce = request.headers.get("x-dashboard-step-up")
        proof = dashboard_stepup_proof.set(
            DashboardStepUpProof(session, nonce) if session and nonce else None
        )
        try:
            return await call_next(request)
        finally:
            dashboard_stepup_proof.reset(proof)
            verified_dashboard_session.reset(token)

    app.include_router(routes.router)
    app.state.owner_auth_service = service
    app.dependency_overrides[get_db] = lambda: db
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def ledger():
    return _Ledger()


@pytest.fixture
def live(monkeypatch, ledger):
    monkeypatch.setattr(flags, "MODE_CONTROLS_LIVE", True)
    service = _AuthService(ledger)
    return _app(_Db(ledger), service=service), service


def _enter(until=None, *, version=0, action=ACTION, cause="owner_declared"):
    return {
        "owner_action_id": action,
        "expected_version": version,
        "until": None if until is None else until.isoformat(),
        "cause": cause,
    }


ROUTES = [
    ("GET", "/api/owner-modes", None),
    ("POST", "/api/owner-modes/travel/enter", _enter()),
    ("POST", "/api/owner-modes/travel/exit", {"owner_action_id": ACTION, "expected_version": 1}),
    (
        "POST",
        "/api/owner-modes/sick/edit",
        {"owner_action_id": ACTION, "expected_version": 0, "holds": []},
    ),
    (
        "POST",
        "/api/owner-modes/step-up/options",
        {"change": "exit", "mode": "travel", "owner_action_id": ACTION, "expected_version": 1},
    ),
]


async def _call(client, method, path, body, **kwargs):
    if method == "GET":
        return await client.get(path, **kwargs)
    return await client.post(path, json=body, **kwargs)


# -- dark and auth ---------------------------------------------------------------


async def test_dark_every_route_is_not_found_and_touches_nothing():
    app = _app(_Explodes(), service=_Explodes())
    async with _client(app) as client:
        responses = [await _call(client, *route) for route in ROUTES]
    assert [r.status_code for r in responses] == [404] * len(ROUTES)


async def test_every_route_needs_the_owner_dashboard_session(monkeypatch, ledger):
    monkeypatch.setattr(flags, "MODE_CONTROLS_LIVE", True)
    app = _app(_Explodes(), session=None, service=_Explodes())
    async with _client(app) as client:
        responses = [await _call(client, *route) for route in ROUTES]
    assert [r.status_code for r in responses] == [403] * len(ROUTES)
    assert {r.json()["detail"]["error"] for r in responses} == {"owner_dashboard_session_required"}


async def test_an_alias_or_unknown_mode_is_refused_before_the_ledger(monkeypatch):
    monkeypatch.setattr(flags, "MODE_CONTROLS_LIVE", True)
    app = _app(_Explodes(), service=_Explodes())
    async with _client(app) as client:
        alias = await client.post("/api/owner-modes/sleep/enter", json=_enter())
        unknown = await client.post("/api/owner-modes/napping/exit", json=_enter())
    assert (alias.status_code, unknown.status_code) == (422, 422)


# -- reads and writes ------------------------------------------------------------


async def test_the_owner_reads_every_mode_with_its_entry_and_profile(live, ledger):
    app, _ = live
    ledger.entries["travel"] = {
        "version": 2,
        "since": T0 - timedelta(hours=1),
        "until": T0 + timedelta(days=2),
        "cause": "owner_confirmed",
        "exited_at": None,
    }
    ledger.entries["asleep"] = {  # ended exactly now: shown as no entry
        "version": 1,
        "since": T0 - timedelta(hours=8),
        "until": T0,
        "cause": "owner_declared",
        "exited_at": None,
    }
    ledger.overlay["focus"] = {
        "version": 3,
        "duration_s": 5400,
        "holds": ["proactive", "reply_to_person"],
        "break_through": ["partner"],
    }
    async with _client(app) as client:
        response = await client.get("/api/owner-modes")
    assert response.status_code == 200
    modes = {row["mode"]: row for row in response.json()["data"]["modes"]}
    assert set(modes) == {
        "asleep",
        "emergency",
        "focus",
        "holiday",
        "hosting",
        "house_unattended",
        "sick",
        "travel",
    }
    assert modes["travel"]["entry"]["cause"] == "owner_confirmed"
    assert modes["travel"]["entry_version"] == 2
    assert modes["asleep"]["entry"] is None and modes["asleep"]["entry_version"] == 1
    assert modes["sick"]["profile"] == {
        "duration_s": None,
        "holds": ["proactive"],
        "break_through": [],
    }
    assert modes["sick"]["profile_version"] == 0
    assert modes["focus"]["profile"] == {
        "duration_s": 5400,
        "holds": ["proactive", "reply_to_person"],
        "break_through": ["partner"],
    }
    assert modes["focus"]["profile_version"] == 3


async def test_entering_needs_no_step_up_and_exiting_without_one_is_refused(live, ledger):
    app, _ = live
    async with _client(app) as client:
        entered = await client.post("/api/owner-modes/travel/enter", json=_enter())
        refused = await client.post(
            "/api/owner-modes/travel/exit",
            json={"owner_action_id": ACTION_2, "expected_version": 1},
        )
    assert entered.status_code == 200
    assert entered.json()["data"] == {"mode": "travel", "version": 1, "verdict": "tighten"}
    assert refused.status_code == 403
    assert refused.json()["detail"]["error"] == "dashboard_step_up_required"
    assert ledger.entries["travel"]["exited_at"] is None
    assert ledger.consumed == []


async def test_an_enter_ending_at_or_before_now_is_invalid(live, ledger):
    app, _ = live
    async with _client(app) as client:
        at_now = await client.post("/api/owner-modes/travel/enter", json=_enter(T0))
        before = await client.post(
            "/api/owner-modes/travel/enter", json=_enter(T0 - timedelta(seconds=1))
        )
    assert (at_now.status_code, before.status_code) == (422, 422)
    assert at_now.json()["detail"]["error"] == "owner_mode_invalid"
    assert ledger.entries == {}


async def test_a_naive_until_is_refused_by_the_model(monkeypatch):
    monkeypatch.setattr(flags, "MODE_CONTROLS_LIVE", True)
    app = _app(_Explodes(), service=_Explodes())
    body = {**_enter(), "until": "2026-11-15T09:00:00"}
    async with _client(app) as client:
        response = await client.post("/api/owner-modes/travel/enter", json=body)
    assert response.status_code == 422


async def test_a_stale_version_is_a_conflict(live, ledger):
    app, _ = live
    async with _client(app) as client:
        await client.post("/api/owner-modes/travel/enter", json=_enter())
        stale = await client.post("/api/owner-modes/travel/enter", json=_enter(version=0))
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"] == "owner_mode_conflict"


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"holds": [], "break_through": []},
        {"holds": [], "duration_s": 3600},
        {"break_through": ["a"], "duration_s": None},
    ],
)
async def test_an_edit_names_exactly_one_field_before_any_ledger_call(monkeypatch, fields):
    monkeypatch.setattr(flags, "MODE_CONTROLS_LIVE", True)
    app = _app(_Explodes(), service=_Explodes())
    body = {"owner_action_id": ACTION, "expected_version": 0, **fields}
    async with _client(app) as client:
        response = await client.post("/api/owner-modes/sick/edit", json=body)
    assert response.status_code == 422


async def test_a_tightening_edit_writes_and_a_loosening_edit_needs_step_up(live, ledger):
    app, _ = live
    async with _client(app) as client:
        tighter = await client.post(
            "/api/owner-modes/focus/edit",
            json={"owner_action_id": ACTION, "expected_version": 0, "holds": ["proactive"]},
        )
        open_ended = await client.post(
            "/api/owner-modes/focus/edit",
            json={"owner_action_id": ACTION_2, "expected_version": 1, "duration_s": None},
        )
        looser = await client.post(
            "/api/owner-modes/sick/edit",
            json={"owner_action_id": ACTION, "expected_version": 0, "holds": []},
        )
    assert tighter.json()["data"] == {"mode": "focus", "version": 1, "verdict": "tighten"}
    assert open_ended.json()["data"]["verdict"] == "none"  # already open-ended
    assert looser.status_code == 403
    assert "sick" not in ledger.overlay


async def test_pinned_classes_cannot_be_held(live):
    app, _ = live
    async with _client(app) as client:
        response = await client.post(
            "/api/owner-modes/focus/edit",
            json={"owner_action_id": ACTION, "expected_version": 0, "holds": ["medication"]},
        )
    assert response.status_code == 422


# -- step-up options and spend --------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {
            "change": "enter",
            "mode": "travel",
            "owner_action_id": "00000000-0000-4000-8000-0000000000a1",
            "expected_version": 0,
            "until": None,
            "cause": "owner_declared",
        },
        {
            "change": "edit",
            "mode": "focus",
            "owner_action_id": "00000000-0000-4000-8000-0000000000a1",
            "expected_version": 0,
            "holds": ["proactive"],
        },
    ],
)
async def test_a_tightening_needs_no_step_up(live, body):
    app, service = live
    async with _client(app) as client:
        response = await client.post("/api/owner-modes/step-up/options", json=body)
    assert response.status_code == 200
    assert response.json()["data"] == {"required": False}
    assert service.calls == []


async def test_options_then_a_later_spend_uses_the_same_binding(live, ledger):
    """The binding never moves with the clock: options at T0, spend seconds later."""
    app, service = live
    exit_body = {"owner_action_id": ACTION_2, "expected_version": 1}
    async with _client(app) as client:
        await client.post("/api/owner-modes/travel/enter", json=_enter(T0 + timedelta(days=1)))
        options = await client.post(
            "/api/owner-modes/step-up/options",
            json={"change": "exit", "mode": "travel", **exit_body},
        )
        ledger.now = T0 + timedelta(seconds=7)
        spent = await client.post(
            "/api/owner-modes/travel/exit", json=exit_body, headers={"x-dashboard-step-up": NONCE}
        )
    assert options.json()["data"] == {"challenge": NONCE}
    ((session, binding),) = service.calls
    assert session == SESSION
    assert spent.status_code == 200
    assert spent.json()["data"] == {"mode": "travel", "version": 2, "verdict": "loosen"}
    issued = (binding.kind, binding.target, binding.request_digest, binding.action_digest)
    assert ledger.consumed == [("begin", issued), ("finish", issued)]
    assert ledger.entries["travel"]["exited_at"] == T0 + timedelta(seconds=7)


async def test_shortening_an_entry_binds_the_same_target_across_the_clock(live, ledger):
    app, service = live
    shorter = _enter(T0 + timedelta(hours=2), version=1, action=ACTION_2)
    async with _client(app) as client:
        await client.post("/api/owner-modes/travel/enter", json=_enter(T0 + timedelta(days=1)))
        await client.post(
            "/api/owner-modes/step-up/options",
            json={"change": "enter", "mode": "travel", **shorter},
        )
        ledger.now = T0 + timedelta(minutes=3)
        spent = await client.post(
            "/api/owner-modes/travel/enter", json=shorter, headers={"x-dashboard-step-up": NONCE}
        )
    assert spent.status_code == 200 and spent.json()["data"]["verdict"] == "loosen"
    assert ledger.consumed[0][1] == ledger.issued[NONCE]


async def test_an_entry_that_ends_between_options_and_spend_consumes_no_proof(live, ledger):
    """The classification flipped (nothing left to exit): refuse, never spend."""
    app, _ = live
    exit_body = {"owner_action_id": ACTION_2, "expected_version": 1}
    async with _client(app) as client:
        await client.post("/api/owner-modes/travel/enter", json=_enter(T0 + timedelta(seconds=5)))
        await client.post(
            "/api/owner-modes/step-up/options",
            json={"change": "exit", "mode": "travel", **exit_body},
        )
        ledger.now = T0 + timedelta(seconds=5)
        spent = await client.post(
            "/api/owner-modes/travel/exit", json=exit_body, headers={"x-dashboard-step-up": NONCE}
        )
    assert spent.status_code == 422
    assert ledger.consumed == []
    assert ledger.entries["travel"]["exited_at"] is None


async def test_a_proof_for_another_change_is_not_admitted(live, ledger):
    app, _ = live
    async with _client(app) as client:
        await client.post("/api/owner-modes/travel/enter", json=_enter(T0 + timedelta(days=1)))
        await client.post(
            "/api/owner-modes/step-up/options",
            json={
                "change": "enter",
                "mode": "travel",
                **_enter(T0 + timedelta(hours=2), version=1, action=ACTION_2),
            },
        )
        spent = await client.post(
            "/api/owner-modes/travel/exit",
            json={"owner_action_id": ACTION_2, "expected_version": 1},
            headers={"x-dashboard-step-up": NONCE},
        )
    assert spent.status_code == 403
    assert [phase for phase, _ in ledger.consumed] == ["begin"]
    assert ledger.entries["travel"]["exited_at"] is None


@pytest.mark.parametrize(
    "body",
    [
        {
            "change": "exit",
            "mode": "travel",
            "owner_action_id": "00000000-0000-4000-8000-0000000000a1",
            "expected_version": 3,
        },
        {
            "change": "edit",
            "mode": "focus",
            "owner_action_id": "00000000-0000-4000-8000-0000000000a1",
            "expected_version": 3,
            "holds": ["proactive"],
        },
    ],
)
async def test_options_for_a_stale_version_is_a_conflict(live, body):
    app, service = live
    async with _client(app) as client:
        response = await client.post("/api/owner-modes/step-up/options", json=body)
    assert response.status_code == 409
    assert service.calls == []


async def test_options_with_no_auth_service_is_unavailable(monkeypatch, ledger):
    monkeypatch.setattr(flags, "MODE_CONTROLS_LIVE", True)
    ledger.overlay["sick"] = None
    app = _app(_Db(ledger), service=None)
    async with _client(app) as client:
        response = await client.post(
            "/api/owner-modes/step-up/options",
            json={
                "change": "edit",
                "mode": "sick",
                "owner_action_id": ACTION,
                "expected_version": 0,
                "holds": [],
            },
        )
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "owner_mode_unavailable"


async def test_an_edit_options_body_names_exactly_one_field(monkeypatch):
    monkeypatch.setattr(flags, "MODE_CONTROLS_LIVE", True)
    app = _app(_Explodes(), service=_Explodes())
    async with _client(app) as client:
        response = await client.post(
            "/api/owner-modes/step-up/options",
            json={
                "change": "edit",
                "mode": "sick",
                "owner_action_id": ACTION,
                "expected_version": 0,
                "holds": [],
                "duration_s": 60,
            },
        )
    assert response.status_code == 422
