"""Owner-only, content-blind away-safety declarations (bu-jv1moa.87).

REQ-home-deterministic-jobs-002 and 003: owner-confirmed values only, bounded, refused without
storing or echoing anything, audited by outcome only; no route creates the sensing group.
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from butlers.api import dashboard_audit_middleware
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db_optional
from butlers.api.owner_control import require_dashboard_owner_control

pytestmark = pytest.mark.unit

_SECRET = "Grandma's chest freezer"
_FREEZER = {
    "entity_id": "sensor.garage_freezer",
    "owner_label": _SECRET,
    "unit": "°C",
    "max_c": -15,
    "hysteresis_c": 2,
    "dwell_minutes": 30,
    "max_reading_age_minutes": 60,
}
_FREEZER_ROW = {
    **_FREEZER,
    "max_c": Decimal("-15.00"),
    "hysteresis_c": Decimal("2.00"),
    "updated_at": datetime(2026, 11, 14, 8, 0, tzinfo=UTC),
}


def _router(app) -> Any:
    return next(module for name, module in app.state.butler_routers if name == "home")


class _Harness:
    def __init__(self, app, monkeypatch: pytest.MonkeyPatch) -> None:
        self.pool = MagicMock()
        self.pool.fetch = AsyncMock(return_value=[])
        self.pool.fetchrow = AsyncMock(return_value=None)
        self.pool.fetchval = AsyncMock(return_value=None)
        self.pool.execute = AsyncMock(return_value="UPDATE 1")
        self.connection = MagicMock()
        self.connection.execute = AsyncMock()
        self.connection.fetchval = AsyncMock(side_effect=[False, 0])
        self.connection.fetchrow = AsyncMock(return_value=_FREEZER_ROW)

        @asynccontextmanager
        async def acquire():
            yield self.connection

        @asynccontextmanager
        async def transaction():
            yield

        self.pool.acquire = acquire
        self.connection.transaction = transaction
        manager = MagicMock(spec=DatabaseManager)
        manager.pool.return_value = self.pool
        app.dependency_overrides[get_db_optional] = lambda: manager

        self.audits: list[dict[str, Any]] = []
        self.generic: list[Any] = []

        async def audit(db, **kwargs):  # noqa: ANN001
            self.audits.append(kwargs)

        async def generic(*args, **kwargs):  # noqa: ANN002, ANN003
            self.generic.append((args, kwargs))

        router = _router(app)
        monkeypatch.setattr(router, "emit_dashboard_audit", audit)
        monkeypatch.setattr(router, "_open_presence_reader", AsyncMock(return_value=None))
        monkeypatch.setattr(dashboard_audit_middleware, "emit_dashboard_audit", generic)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


@pytest.fixture
def harness(app, monkeypatch) -> _Harness:
    return _Harness(app, monkeypatch)


async def test_an_empty_home_reports_every_producer_unconfigured(harness) -> None:
    async with harness.client as client:
        resp = await client.get("/api/home/away-safety")

    assert resp.status_code == 200
    assert resp.json() == {
        "freezers": [],
        "doors": [],
        "blind_after_minutes": None,
        "status": {
            "freezer": "unconfigured",
            "away_door": "unconfigured",
            "blind": "unconfigured",
            "sensing_group": "unknown",
        },
    }


@pytest.mark.parametrize(
    ("slot_state", "group", "blind"),
    [("unconfigured", "unconfigured", "unconfigured"), ("known", "registered", "ok")],
)
async def test_the_blind_check_needs_the_registered_group_and_a_dwell(
    harness, app, monkeypatch, slot_state, group, blind
) -> None:
    """The group shows unconfigured until the privileged registration exists."""
    reader = MagicMock()
    reader.close = AsyncMock()
    reader.fetchval = AsyncMock(return_value={"state": slot_state})
    monkeypatch.setattr(_router(app), "_open_presence_reader", AsyncMock(return_value=reader))
    harness.pool.fetch = AsyncMock(
        side_effect=[
            [_FREEZER_ROW],
            [
                {
                    "entity_id": "binary_sensor.door",
                    "owner_label": "Door",
                    "away_alert_after_minutes": 45,
                }
            ],
        ]
    )
    harness.pool.fetchval = AsyncMock(return_value=20)

    async with harness.client as client:
        resp = await client.get("/api/home/away-safety")

    body = resp.json()
    assert body["status"] == {
        "freezer": "ok",
        "away_door": "ok",
        "blind": blind,
        "sensing_group": group,
    }
    assert body["freezers"][0]["max_c"] == -15.0
    reader.close.assert_awaited_once()


async def test_a_freezer_is_declared_with_every_owner_value(harness) -> None:
    async with harness.client as client:
        resp = await client.put("/api/home/away-safety/freezers", json=_FREEZER)

    assert resp.status_code == 200
    assert harness.connection.fetchrow.await_args.args[1:] == (
        "sensor.garage_freezer",
        _SECRET,
        "°C",
        Decimal("-15"),
        Decimal("2"),
        30,
        60,
    )
    assert harness.audits[-1]["body"] == {"outcome": "applied"}
    assert harness.generic == [], "the generic middleware never reads this subtree"


@pytest.mark.parametrize(
    "change",
    [
        {"entity_id": "binary_sensor.freezer"},
        {"unit": "K"},
        {"max_c": 1},
        {"max_c": -31},
        {"hysteresis_c": 0.4},
        {"hysteresis_c": 11},
        {"dwell_minutes": 4},
        {"dwell_minutes": 241},
        {"dwell_minutes": 30.5},
        {"max_reading_age_minutes": 9},
        {"max_reading_age_minutes": 361},
        {"owner_label": "   "},
        {"owner_label": "x" * 81},
        {"suggested": True},
        {"max_c": None},
    ],
    ids=[
        "not-a-sensor",
        "unit",
        "max-above-zero",
        "max-below-floor",
        "hysteresis-low",
        "hysteresis-high",
        "dwell-low",
        "dwell-high",
        "dwell-fraction",
        "age-low",
        "age-high",
        "blank-label",
        "long-label",
        "extra-field",
        "missing-value",
    ],
)
async def test_an_out_of_bounds_freezer_is_refused_blind_and_stores_nothing(
    harness, change
) -> None:
    """away-safety requirement 002: refused without storing, echoing or auditing any value."""
    body = {**_FREEZER, **change}
    async with harness.client as client:
        resp = await client.put("/api/home/away-safety/freezers", json=body)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"
    assert _SECRET not in resp.text and "freezer" not in resp.text.lower()
    harness.connection.fetchrow.assert_not_awaited()
    assert harness.audits[-1]["body"] == {"outcome": "invalid_request"}
    assert harness.audits[-1]["result"] == "refused"


async def test_a_ninth_freezer_is_refused(harness) -> None:
    harness.connection.fetchval = AsyncMock(side_effect=[False, 8])
    async with harness.client as client:
        resp = await client.put("/api/home/away-safety/freezers", json=_FREEZER)

    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "FREEZER_LIMIT")
    harness.connection.fetchrow.assert_not_awaited()


@pytest.mark.parametrize(("minutes", "status"), [(29, 422), (30, 200), (1440, 200), (1441, 422)])
async def test_the_away_duration_floor_is_the_departure_window(harness, minutes, status) -> None:
    """away-safety requirement 005: at least 30 minutes, the card's whole delivery window."""
    harness.pool.fetchrow = AsyncMock(
        return_value={
            "entity_id": "binary_sensor.front_door",
            "owner_label": "Front door",
            "away_alert_after_minutes": minutes,
        }
    )
    async with harness.client as client:
        resp = await client.put(
            "/api/home/away-safety/doors/binary_sensor.front_door",
            json={"away_alert_after_minutes": minutes},
        )

    assert resp.status_code == status
    assert harness.pool.fetchrow.await_count == int(status == 200)


async def test_only_a_declared_departure_item_can_be_armed(harness) -> None:
    async with harness.client as client:
        missing = await client.put(
            "/api/home/away-safety/doors/binary_sensor.nope", json={"away_alert_after_minutes": 45}
        )
        malformed = await client.put(
            "/api/home/away-safety/doors/Not%20An%20Entity", json={"away_alert_after_minutes": 45}
        )

    assert (missing.status_code, malformed.status_code) == (404, 404)


@pytest.mark.parametrize(
    ("path", "status_text", "expected"),
    [
        ("/api/home/away-safety/doors/binary_sensor.front_door", "UPDATE 1", 204),
        ("/api/home/away-safety/doors/binary_sensor.front_door", "UPDATE 0", 404),
        ("/api/home/away-safety/freezers/sensor.garage_freezer", "DELETE 1", 204),
        ("/api/home/away-safety/freezers/sensor.garage_freezer", "DELETE 0", 404),
        ("/api/home/away-safety/blind", "DELETE 1", 204),
        ("/api/home/away-safety/blind", "DELETE 0", 404),
    ],
)
async def test_removing_a_declaration_clears_it(harness, path, status_text, expected) -> None:
    """Removing it resolves its condition as watch_removed on the next healthy run."""
    harness.pool.execute = AsyncMock(return_value=status_text)
    async with harness.client as client:
        resp = await client.delete(path)

    assert resp.status_code == expected


@pytest.mark.parametrize(("minutes", "status"), [(4, 422), (5, 200), (1440, 200), (1441, 422)])
async def test_the_blind_dwell_is_bounded_and_owner_set(harness, minutes, status) -> None:
    async with harness.client as client:
        resp = await client.put(
            "/api/home/away-safety/blind", json={"blind_after_minutes": minutes}
        )

    assert resp.status_code == status
    assert harness.pool.execute.await_count == int(status == 200)


async def test_every_away_safety_route_requires_the_owner(app) -> None:
    routes = [r for r in _router(app).router.routes if "/away-safety" in r.path]
    assert len(routes) == 7
    for route in routes:
        assert require_dashboard_owner_control in [d.call for d in route.dependant.dependencies]

    with pytest.raises(HTTPException) as denied:
        require_dashboard_owner_control(SimpleNamespace(state=SimpleNamespace()))
    assert denied.value.status_code == 401


async def test_no_route_creates_or_changes_the_sensing_group(app) -> None:
    """away-safety requirement 003: registration is a privileged act outside Home."""
    module = _router(app)
    for route in module.router.routes:
        assert "sensing" not in route.path
    source = inspect.getsource(module)
    assert "perception_private" not in source
    assert "perception_write" not in source
    assert dashboard_audit_middleware._in_private_subtree("/api/home/away-safety/blind")
