"""Dashboard ``GET /api/now``: owner-only and dark (bu-dhloi1.21 PR2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Request

from butlers.api.owner_control import require_dashboard_owner_control
from butlers.api.routers import now as now_route
from butlers.perception import flags
from butlers.perception.now import NOW_KINDS
from tests.api.auth_helpers import create_authenticated_domain_app

pytestmark = pytest.mark.unit

OWNER = UUID("00000000-0000-4000-8000-000000000001")
AS_OF = datetime(2026, 7, 1, 9, 30, tzinfo=UTC)


class _Reader:
    """A login connection that must SET LOCAL ROLE into the owner reader in a transaction."""

    def __init__(self, *, fail=False):
        self.role = "dashboard_login"
        self.in_transaction = False
        self.statements: list[str] = []
        self.closed = False
        self.fail = fail

    def transaction(self):
        reader = self

        class _Tx:
            async def __aenter__(self):
                reader.in_transaction = True

            async def __aexit__(self, *exc):
                reader.in_transaction = False
                reader.role = "dashboard_login"
                return False

        return _Tx()

    async def execute(self, statement):
        assert self.in_transaction, "the role switch must be transaction-local"
        self.statements.append(statement)
        if statement == "SET LOCAL ROLE perception_owner_reader":
            self.role = "perception_owner_reader"

    async def fetchval(self, query, *args):
        if self.fail:
            raise OSError("reader down")
        if "current_user" in query:
            return self.role
        kind, subject, as_of = args
        assert (subject, as_of) == (OWNER, AS_OF)
        return {
            "kind": kind,
            "state": "unknown",
            "value": None,
            "stale_last_value": None,
            "source": None,
            "observed_at": None,
            "fresh_until": (AS_OF + timedelta(hours=1)).isoformat(),
            "age_seconds": None,
            "labels": {"version": 1, "label_instr": [], "label_data": [], "label_sens": []},
            "generation": None,
            "availability_reason": "unknown_evidence",
        }

    async def close(self):
        self.closed = True


def _app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def owner(request: Request, call_next):
        if request.headers.get("x-owner") == "yes":
            request.state.owner_authority = object()
        return await call_next(request)

    app.include_router(now_route.router)
    return app


async def _get(app, *, owner=True):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/now", headers={"x-owner": "yes"} if owner else {})


@pytest.fixture
def live(monkeypatch):
    reader = _Reader()

    async def open_reader():
        return reader

    async def owner_subject():
        return OWNER

    monkeypatch.setattr(flags, "NOW_LIVE", True)
    monkeypatch.setattr(now_route, "_open_reader", open_reader)
    monkeypatch.setattr(now_route, "_owner_subject", owner_subject)
    monkeypatch.setattr(now_route, "_now", lambda: AS_OF)
    return reader


def test_the_route_requires_the_owner():
    (route,) = now_route.router.routes
    assert require_dashboard_owner_control in [d.call for d in route.dependant.dependencies]


async def test_dark_the_production_app_answers_not_found():
    app = create_authenticated_domain_app()
    assert "/api/now" in app.openapi()["paths"]  # registered, so the 404 is the flag's
    response = await _get(app)
    assert response.status_code == 404


async def test_without_owner_authority_it_is_refused_even_when_live(live):
    response = await _get(_app(), owner=False)
    assert response.status_code == 401
    assert live.statements == []


async def test_live_owner_gets_the_card_read_in_the_owner_role(live):
    response = await _get(_app())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    card = response.json()["data"]
    assert [line["kind"] for line in card["lines"]] == list(NOW_KINDS)
    assert card["all_current"] is False
    assert card["headline"] == "5 of 5 signals not current"
    assert card["text"].startswith("Now (2026-07-01 09:30 UTC): 5 of 5 signals not current")
    assert live.statements == ["SET LOCAL ROLE perception_owner_reader"]
    assert live.closed is True


async def test_unactivated_reader_is_unavailable(live, monkeypatch):
    async def no_reader():
        return None

    monkeypatch.setattr(now_route, "_open_reader", no_reader)
    response = await _get(_app())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "now_unavailable"


async def test_no_single_owner_is_unavailable(live, monkeypatch):
    async def no_owner():
        return None

    monkeypatch.setattr(now_route, "_owner_subject", no_owner)
    response = await _get(_app())
    assert response.status_code == 503
    assert live.statements == []


async def test_a_reader_failure_is_unavailable_and_closes(live):
    live.fail = True
    response = await _get(_app())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "now_unavailable"
    assert live.closed is True
