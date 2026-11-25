"""Dashboard ``GET /api/now``: owner-only and dark (bu-dhloi1.21 PR2)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

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
    # The slot signals, then the agenda line, which here has no calendar to read.
    assert [line["kind"] for line in card["lines"]] == [*NOW_KINDS, "owner.agenda"]
    agenda = card["lines"][-1]
    assert agenda["state"] == "unknown" and agenda["text"] == "Unknown (calendar unreadable)"
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


def _verdict(**kw):
    from butlers.core.temporal.day_sheet import DayItem, Freshness
    from butlers.core.temporal.day_verdict import DayVerdict

    zone = ZoneInfo("Asia/Singapore")
    item = DayItem(
        ref="inst-1",
        start=datetime(2026, 7, 1, 18, 0, tzinfo=zone),
        end=datetime(2026, 7, 1, 19, 0, tzinfo=zone),
        all_day=False,
        kind="attended",
        title="Secret title",
        label_class="third_party",
        people_count=1,
        conflict_ref=None,
        door="/calendar?entry=inst-1",
    )
    base = dict(
        available=True,
        date="2026-07-01",
        zone="Asia/Singapore",
        freshness=Freshness(state="current", as_of=AS_OF, sources_unreadable=[], sources_total=1),
        current=None,
        next=item,
        committed_minutes=60,
        committed_count=1,
        tentative_count=0,
        free_window=None,
        empty=False,
    )
    return DayVerdict(**{**base, **kw})


@pytest.fixture
def calendar(monkeypatch, live):
    """A db manager and a calendar verdict the route reads for today."""
    state = {"verdict": _verdict(), "raise": False}

    class _Db:
        def credential_shared_pool(self):
            raise KeyError("none")

    async def zone(_pool):
        return ZoneInfo("Asia/Singapore")

    async def verdict(_db, *, local_date, zone, now):
        assert local_date == date(2026, 7, 1)  # today in the owner's zone (17:30 local)
        if state["raise"]:
            raise RuntimeError("calendar down")
        return state["verdict"]

    monkeypatch.setattr(now_route, "get_db_optional", lambda: _Db())
    monkeypatch.setattr(now_route, "owner_zoneinfo", zone)
    monkeypatch.setattr(now_route, "query_calendar_day_verdict", verdict)
    monkeypatch.setattr(now_route, "_last_agenda", None)
    return state


async def _agenda():
    card = (await _get(_app())).json()["data"]
    return card["lines"][-1], card


async def test_the_card_carries_the_agenda_line_with_its_door(calendar):
    line, card = await _agenda()

    assert line["kind"] == "owner.agenda" and line["label"] == "Next"
    assert line["state"] == "known" and line["text"] == "Free until 18:00"
    assert line["door"] == "/calendar?entry=inst-1"
    assert line["fresh_until"] is not None
    assert "Secret title" not in card["text"] and "Secret title" not in str(card)


async def test_an_unreadable_calendar_keeps_the_last_known_line_and_claims_nothing(calendar):
    first, _ = await _agenda()
    assert first["text"] == "Free until 18:00"

    calendar["raise"] = True
    line, _ = await _agenda()

    assert line["state"] == "unknown" and line["stale"] is True
    assert line["text"] == "Unknown (last known: Free until 18:00, as of 17:30)"
    assert line["door"] is None


async def test_an_unavailable_verdict_is_unknown_not_free(calendar):
    calendar["verdict"] = _verdict(available=False, next=None, empty=False)

    line, _ = await _agenda()

    assert line["state"] == "unknown" and "free" not in line["text"].lower()
