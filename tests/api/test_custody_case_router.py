"""Owner read of one custody case's evidence summaries (bu-s11n0s.1 S5g).

REQ-endpoint-custody-holds-011: ordinary surfaces emit only bounded states, counts and times, never
identifiers, coordinates, amounts or free text. The summary is an allow-list per (contributor, kind).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from butlers.api import deps
from butlers.api.routers.custody_case import router, summarize

pytestmark = pytest.mark.unit

EPISODE = uuid.uuid4()
AT = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def _client(pool, *, owner: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[deps.get_db] = lambda: SimpleNamespace(pool=lambda name: pool)

    @app.middleware("http")
    async def _state(request, call_next):
        if owner:
            request.state.owner_authority = object()
        return await call_next(request)

    return TestClient(app)


def test_the_finance_window_keeps_counts_and_drops_everything_else() -> None:
    payload = {
        "window_start": "a",
        "window_end": "b",
        "transactions": 3,
        "debits": 2,
        "credits": 1,
        "first_seen_merchants": 1,
        "anomalies": {"total": 2, "by_type": {"amount_anomaly": 2}},
        "merchant": "ACME SHOP",
        "amount": "12.00",
        "target_kind": "has-email",
    }
    summary = summarize("finance", "held_window", payload)
    assert summary["transactions"] == 3 and summary["anomalies"] == {"total": 2}
    assert not {"merchant", "amount", "target_kind"} & set(summary)


def test_the_device_fix_keeps_place_and_age_never_coordinates_or_the_endpoint() -> None:
    payload = {
        "status": "known",
        "place": "Home",
        "fix_at": "x",
        "age_seconds_at_observation": 60,
        "fresh_until": "y",
        "lat": 1.3,
        "lon": 103.8,
        "endpoint_identity": "owntracks:o:p",
        "evidence_only": True,
    }
    summary = summarize("chronicler", "device_last_fix", payload)
    assert summary["place"] == "Home"
    assert not {"lat", "lon", "endpoint_identity", "evidence_only"} & set(summary)


def test_an_unknown_contributor_or_kind_contributes_nothing_and_nested_values_are_dropped() -> None:
    assert summarize("someone", "thing", {"secret": "x"}) == {}
    assert summarize("finance", "held_window", {"transactions": {"nested": 1}}) == {}
    assert summarize("finance", "held_window", "not json") == {}
    assert summarize("finance", "held_window", '{"transactions": 4}') == {"transactions": 4}


def _pool(case, rows):
    return SimpleNamespace(
        fetchrow=AsyncMock(return_value=case), fetch=AsyncMock(return_value=rows)
    )


def test_the_case_read_returns_state_and_summaries_only() -> None:
    case = {
        "id": str(uuid.uuid4()),
        "state": "open",
        "outcome": None,
        "opened_at": AT,
        "closed_at": None,
    }
    rows = [
        {
            "contributor": "finance",
            "kind": "held_window",
            "payload": {"transactions": 1, "merchant": "ACME"},
            "contributed_at": AT,
        }
    ]
    response = _client(_pool(case, rows)).get(f"/api/custody/episodes/{EPISODE}/case")
    data = response.json()["data"]
    assert data["state"] == "open" and data["truncated"] is False
    assert data["evidence"][0]["summary"] == {"transactions": 1}
    assert "ACME" not in response.text


def test_a_missing_episode_is_404_and_an_unauthenticated_caller_is_401() -> None:
    assert _client(_pool(None, [])).get(f"/api/custody/episodes/{EPISODE}/case").status_code == 404
    assert (
        _client(_pool(None, []), owner=False)
        .get(f"/api/custody/episodes/{EPISODE}/case")
        .status_code
        == 401
    )
