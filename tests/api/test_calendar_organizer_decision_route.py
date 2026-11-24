# Spec: REQ-module-calendar-042
"""Owner-only Vouch and Block route: the organizer comes from the stored row, never the body."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from butlers.api.deps import get_db
from butlers.modules.calendar import organizer_decisions
from butlers.modules.calendar.organizer_decisions import DecisionResult, NoCalendarEndpoint

pytestmark = pytest.mark.unit

_URL = "/api/calendar/workspace/organizer-decision"


def _db(rows, *, failed=()):
    return SimpleNamespace(
        configured_butlers_with_module=lambda module: ["general", "travel"],
        fan_out_with_status=AsyncMock(return_value=({"general": rows, "travel": []}, list(failed))),
        pool=lambda name: SimpleNamespace(name=name),
    )


@pytest.fixture
def wired(monkeypatch):
    apply = AsyncMock(return_value=DecisionResult("vouch", 2, 2, 0))
    restamp = AsyncMock(return_value=3)
    monkeypatch.setattr(organizer_decisions, "apply_decision", apply)
    monkeypatch.setattr(organizer_decisions, "restamp_organizer", restamp)
    monkeypatch.setattr("butlers.api.audit_emit.emit_dashboard_audit", AsyncMock())
    return apply, restamp


async def _post(app, db, body):
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(_URL, json=body)


_ROW = {"organizer": "sam@known.test", "own_email": "me@example.com"}


async def test_the_verb_uses_the_stored_organizer_and_restamps_every_calendar_butler(app, wired):
    apply, restamp = wired
    response = await _post(app, _db([_ROW]), {"entry_id": str(uuid.uuid4()), "verb": "vouch"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data == {
        "verb": "vouch",
        "endpoints": 2,
        "rules_written": 2,
        "rules_removed": 0,
        "events_restamped": 6,  # 3 per calendar butler, two butlers
        "restamp_degraded": False,
    }
    assert apply.await_args.args[1:] == ("sam@known.test", "vouch")
    assert restamp.await_count == 2


async def test_an_organizer_in_the_body_is_refused_not_used(app, wired):
    body = {"entry_id": str(uuid.uuid4()), "verb": "block", "organizer": "evil@attacker.test"}
    response = await _post(app, _db([_ROW]), body)
    assert response.status_code == 422
    assert wired[0].await_count == 0


@pytest.mark.parametrize(
    ("rows", "failed", "status", "detail"),
    [
        ([], (), 404, "entry_not_found"),
        ([_ROW, _ROW], (), 409, "entry_not_found"),
        ([_ROW], ("travel",), 503, "organizer_decision_lookup_degraded"),
        ([{"organizer": "", "own_email": "me@example.com"}], (), 409, "entry_has_no_organizer"),
        ([{"organizer": "nobody", "own_email": ""}], (), 409, "entry_has_no_organizer"),
        (
            [{"organizer": " Me@Example.com ", "own_email": "me@example.com"}],
            (),
            409,
            "organizer_is_the_owner",
        ),
    ],
)
async def test_an_unusable_entry_changes_no_rule(app, wired, rows, failed, status, detail):
    response = await _post(
        app, _db(rows, failed=failed), {"entry_id": str(uuid.uuid4()), "verb": "block"}
    )
    assert (response.status_code, response.json()["detail"]) == (status, detail)
    assert wired[0].await_count == 0


async def test_no_calendar_endpoint_is_a_conflict(app, wired):
    wired[0].side_effect = NoCalendarEndpoint("google_calendar")
    response = await _post(app, _db([_ROW]), {"entry_id": str(uuid.uuid4()), "verb": "vouch"})
    assert (response.status_code, response.json()["detail"]) == (409, "no_calendar_endpoint")


async def test_a_non_owner_principal_is_refused(app, wired, monkeypatch):
    def refuse():
        raise PermissionError("Authenticated owner principal is required")

    monkeypatch.setattr("butlers.api.audit_emit.authenticated_principal", refuse)
    response = await _post(app, _db([_ROW]), {"entry_id": str(uuid.uuid4()), "verb": "vouch"})
    assert response.status_code == 403
    assert wired[0].await_count == 0


async def test_a_failed_restamp_is_reported_not_hidden(app, wired):
    wired[1].side_effect = RuntimeError("boom")
    response = await _post(app, _db([_ROW]), {"entry_id": str(uuid.uuid4()), "verb": "vouch"})
    assert response.status_code == 200
    assert response.json()["data"]["restamp_degraded"] is True
