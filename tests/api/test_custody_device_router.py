"""Owner device binding and last-fix read for a custody episode (bu-s11n0s.1 S5f)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from butlers.api import deps
from butlers.api.routers import custody_device
from butlers.api.routers.custody_device import judge_fix, router

pytestmark = pytest.mark.unit

EPISODE = uuid.uuid4()
CASE = str(uuid.uuid4())
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def _client(pool, *, owner: bool = True, method: str = "cookie") -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[deps.get_db] = lambda: SimpleNamespace(pool=lambda name: pool)

    @app.middleware("http")
    async def _state(request, call_next):
        if owner:
            request.state.owner_authority = SimpleNamespace(method=method)
        return await call_next(request)

    return TestClient(app)


def _pool(**kw):
    pool = SimpleNamespace(fetchrow=AsyncMock(), fetchval=AsyncMock())
    pool.fetchrow.return_value = kw.get("case", {"id": CASE, "state": "open"})
    pool.fetchval.side_effect = kw.get("vals", [1, None])
    return pool


def test_the_owner_binds_a_registered_device_as_case_evidence(monkeypatch) -> None:
    contribute = AsyncMock(return_value=({}, True))
    monkeypatch.setattr(custody_device, "contribute_evidence", contribute)
    response = _client(_pool()).put(
        f"/api/custody/episodes/{EPISODE}/device", json={"endpoint_identity": "owntracks:o:p"}
    )
    assert response.status_code == 200 and response.json()["data"]["bound"] is True
    kwargs = contribute.await_args.kwargs
    assert (kwargs["contributor"], kwargs["kind"]) == ("custody_owner", "device_binding")
    assert kwargs["ref"].startswith("selection:")
    assert kwargs["payload"] == {
        "endpoint_identity": "owntracks:o:p",
        "selected_by": "owner",
        "authority": "cookie",
    }
    assert kwargs["case_id"] == CASE


def test_selecting_a_then_b_then_a_writes_three_distinct_selections(monkeypatch) -> None:
    contribute = AsyncMock(return_value=({}, True))
    monkeypatch.setattr(custody_device, "contribute_evidence", contribute)
    url = f"/api/custody/episodes/{EPISODE}/device"
    current = [None, "a", "b"]  # the effective binding before each selection
    for endpoint, before in zip(("a", "b", "a"), current, strict=True):
        client = _client(_pool(vals=[1, before]))
        assert client.put(url, json={"endpoint_identity": endpoint}).status_code == 200
    refs = [call.kwargs["ref"] for call in contribute.await_args_list]
    assert len(set(refs)) == 3
    assert [call.kwargs["payload"]["endpoint_identity"] for call in contribute.await_args_list] == [
        "a",
        "b",
        "a",
    ]


def test_selecting_the_current_device_again_writes_nothing(monkeypatch) -> None:
    contribute = AsyncMock()
    monkeypatch.setattr(custody_device, "contribute_evidence", contribute)
    response = _client(_pool(vals=[1, "a"])).put(
        f"/api/custody/episodes/{EPISODE}/device", json={"endpoint_identity": "a"}
    )
    assert response.json()["data"] == {"bound": True, "newly_recorded": False}
    contribute.assert_not_awaited()


def test_a_non_cookie_authority_cannot_bind(monkeypatch) -> None:
    contribute = AsyncMock()
    monkeypatch.setattr(custody_device, "contribute_evidence", contribute)
    response = _client(_pool(), method="api_key").put(
        f"/api/custody/episodes/{EPISODE}/device", json={"endpoint_identity": "a"}
    )
    assert response.status_code == 403
    contribute.assert_not_awaited()


def test_an_unregistered_device_is_refused(monkeypatch) -> None:
    contribute = AsyncMock()
    monkeypatch.setattr(custody_device, "contribute_evidence", contribute)
    response = _client(_pool(vals=[None])).put(
        f"/api/custody/episodes/{EPISODE}/device", json={"endpoint_identity": "owntracks:x"}
    )
    assert response.status_code == 409
    contribute.assert_not_awaited()


def test_a_closed_or_missing_episode_is_refused(monkeypatch) -> None:
    contribute = AsyncMock()
    monkeypatch.setattr(custody_device, "contribute_evidence", contribute)
    for case in (None, {"id": CASE, "state": "closed"}):
        response = _client(_pool(case=case)).put(
            f"/api/custody/episodes/{EPISODE}/device", json={"endpoint_identity": "a"}
        )
        assert response.status_code == 404
    contribute.assert_not_awaited()


def test_no_owner_authority_is_refused_and_extra_fields_are_rejected() -> None:
    url = f"/api/custody/episodes/{EPISODE}/device"
    assert (
        _client(_pool(), owner=False).put(url, json={"endpoint_identity": "a"}).status_code == 401
    )
    assert _client(_pool()).put(url, json={"endpoint_identity": "a", "lat": 1}).status_code == 422


def test_an_unbound_episode_reads_unbound() -> None:
    response = _client(_pool(vals=[None])).get(f"/api/custody/episodes/{EPISODE}/device")
    assert response.json()["data"] == {"bound": False, "last_fix": None}


def _known(fix_at: datetime) -> dict:
    return {
        "status": "known",
        "fix_at": fix_at.isoformat(),
        "fresh_until": (fix_at + timedelta(hours=6)).isoformat(),
        "place": "Home",
        "observed_at": fix_at.isoformat(),
        "age_seconds_at_observation": 0,
    }


def test_a_fresh_fix_reports_age_and_place_now() -> None:
    judged = judge_fix(_known(NOW - timedelta(minutes=5)), now=NOW)
    assert (judged["status"], judged["place"], judged["age_seconds"]) == ("known", "Home", 300)


def test_a_fix_that_aged_out_since_observation_reads_unknown_stale() -> None:
    judged = judge_fix(_known(NOW - timedelta(hours=7)), now=NOW)
    assert (judged["status"], judged["reason"]) == ("unknown", "stale")
    assert "place" not in judged and "age_seconds" not in judged and "fix_at" not in judged


def test_no_observation_yet_and_stored_unknown_read_unknown() -> None:
    assert judge_fix(None, now=NOW)["status"] == "unknown"
    stored = {"status": "unknown", "reason": "no_fix", "observed_at": "x", "lat": 1.0}
    assert judge_fix(stored, now=NOW) == {
        "status": "unknown",
        "reason": "no_fix",
        "observed_at": "x",
    }


def test_the_read_returns_the_judged_evidence_and_marks_it_evidence_only() -> None:
    payload = _known(datetime.now(UTC) - timedelta(minutes=1))
    pool = _pool(vals=["owntracks:o:p", payload])
    data = _client(pool).get(f"/api/custody/episodes/{EPISODE}/device").json()["data"]
    assert data["bound"] is True and data["evidence_only"] is True
    assert data["last_fix"]["status"] == "known"


def test_the_case_lookup_prefers_the_active_case_over_a_lapsed_one() -> None:
    sql = custody_device._CASE_SQL
    assert "ORDER BY (state <> 'closed') DESC, opened_at DESC" in sql


def test_reading_the_device_needs_cookie_authority() -> None:
    url = f"/api/custody/episodes/{EPISODE}/device"
    assert _client(_pool(), method="api_key").get(url).status_code == 403
