# Spec: REQ-module-calendar-037
"""POST /api/calendar/sources/role: the owner sets a calendar source's role (bu-5ii4gj.1 S2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.testing.fake_pool import fake_pool
from butlers.testing.records import row

pytestmark = pytest.mark.unit

KEY = "provider:google:partner@example.test"


def _wire(app, pools: dict, *, audit=None):
    db = MagicMock(spec=DatabaseManager)
    db.butler_names = sorted(pools)
    db.butlers_with_module = MagicMock(return_value=sorted(pools))
    db.pool = MagicMock(side_effect=lambda name: audit if name == "switchboard" else pools[name])
    app.dependency_overrides[get_db] = lambda: db
    return db


async def _post(app, body):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/api/calendar/sources/role", json=body)


def _updated(source_id=None):
    return row(id=source_id or uuid4(), source_key=KEY, calendar_id="partner@example.test")


async def test_the_role_is_written_confirmed_in_every_schema(app):
    general = fake_pool(fetch=[_updated()])
    travel = fake_pool(fetch=[_updated()])
    _wire(app, {"general": general, "travel": travel}, audit=fake_pool(execute=None))

    resp = await _post(app, {"source_key": KEY, "role": "household_member"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["role"] == "household_member" and data["role_basis"] == "owner_confirmed"
    assert sorted(data["updated_butlers"]) == ["general", "travel"]
    for pool in (general, travel):
        sql, *args = pool.fetch.await_args.args
        assert "role_basis = 'owner_confirmed'" in sql
        assert args == ["household_member", KEY]


async def test_clearing_returns_the_source_to_inference(app):
    general = fake_pool(fetch=[_updated()])
    _wire(app, {"general": general}, audit=fake_pool(execute=None))

    resp = await _post(app, {"source_key": KEY, "role": None})

    data = resp.json()["data"]
    assert data["role"] == "unclassified" and data["role_basis"] == "provider_inferred"
    sql, *args = general.fetch.await_args.args
    assert "role_basis = 'provider_inferred'" in sql and args == ["unclassified", KEY]


async def test_an_unknown_role_is_refused_and_nothing_is_written(app):
    general = fake_pool(fetch=[_updated()])
    _wire(app, {"general": general})

    resp = await _post(app, {"source_key": KEY, "role": "mine"})

    assert resp.status_code == 422
    general.fetch.assert_not_awaited()


async def test_an_unknown_source_is_404(app):
    _wire(app, {"general": fake_pool(fetch=[])}, audit=fake_pool(execute=None))
    assert (await _post(app, {"source_key": "nope", "role": "owner"})).status_code == 404


async def test_a_schema_that_fails_is_reported_not_hidden(app):
    good = fake_pool(fetch=[_updated()])
    bad = MagicMock()
    bad.fetch = AsyncMock(side_effect=RuntimeError("down"))
    _wire(app, {"general": good, "travel": bad}, audit=fake_pool(execute=None))

    data = (await _post(app, {"source_key": KEY, "role": "team"})).json()["data"]

    assert data["updated_butlers"] == ["general"]
    assert data["failed_butlers"] == ["travel"]


async def test_every_schema_failing_is_a_503_not_a_404(app):
    bad = MagicMock()
    bad.fetch = AsyncMock(side_effect=RuntimeError("down"))
    _wire(app, {"general": bad, "travel": bad}, audit=fake_pool(execute=None))

    resp = await _post(app, {"source_key": KEY, "role": "team"})

    assert resp.status_code == 503
    assert "general" in resp.json()["detail"]
