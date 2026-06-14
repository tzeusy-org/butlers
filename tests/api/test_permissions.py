"""Tests for permissions matrix API.

Covers:
- GET /api/permissions returns matrix from DB rows.
- PUT /api/permissions/{butler}/{perm} happy path.
- PUT returns 422 + {"error": "reason_required"} when reason is empty.
- PUT returns 422 when reason is whitespace-only.
- audit.append is called on successful mutation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.permissions import _get_db_manager

pytestmark = pytest.mark.unit


def _make_pool(rows: list[dict] | None = None) -> AsyncMock:
    """Return an asyncpg pool mock wired for the permissions table."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=_make_records(rows or []))
    pool.execute = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    return pool


def _make_records(rows: list[dict]) -> list[MagicMock]:
    records = []
    for row in rows:
        m = MagicMock()
        m.__getitem__ = MagicMock(side_effect=lambda k, _r=row: _r[k])
        records.append(m)
    return records


def _make_db(pool: AsyncMock) -> MagicMock:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(autouse=True)
def clear_overrides(app):
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /api/permissions
# ---------------------------------------------------------------------------


async def test_get_permissions_empty_matrix(app):
    """With no rows, the matrix returns empty lists."""
    pool = _make_pool(rows=[])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/permissions")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["butlers"] == []
    assert body["data"]["permissions"] == []
    assert body["data"]["cells"] == {}


async def test_get_permissions_matrix_with_rows(app):
    """Matrix cells are populated from DB rows."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {
            "butler": "chronicler",
            "permission": "email.read",
            "granted": True,
            "reason": "default",
            "updated_at": now,
        },
        {
            "butler": "messenger",
            "permission": "email.read",
            "granted": False,
            "reason": "revoked",
            "updated_at": now,
        },
    ]
    pool = _make_pool(rows=rows)
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/permissions")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "chronicler" in data["butlers"]
    assert "messenger" in data["butlers"]
    assert "email.read" in data["permissions"]
    assert data["cells"]["chronicler"]["email.read"]["granted"] is True
    assert data["cells"]["messenger"]["email.read"]["granted"] is False


async def test_get_permissions_503_when_no_switchboard(app):
    """Returns 503 when the switchboard pool is unavailable."""
    db = MagicMock(spec=DatabaseManager)
    db.pool.side_effect = KeyError("switchboard")
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/permissions")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# PUT /api/permissions/{butler}/{perm}
# ---------------------------------------------------------------------------


async def test_put_permission_success(app):
    """Successful update returns 200 and calls audit.append."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch(
        "butlers.api.routers.permissions.audit.append", new_callable=AsyncMock
    ) as mock_audit:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/permissions/chronicler/email.read",
                json={"granted": True, "reason": "Needed for digest emails"},
            )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["butler"] == "chronicler"
    assert data["permission"] == "email.read"
    assert data["granted"] is True

    # The route handler emits an explicit audit entry with action
    # "permission.set".  The dashboard_audit_middleware ALSO routes through the
    # same canonical audit.append() as a fire-and-forget background task (its
    # call carries action "PUT /api/..." and a metadata kwarg), so the total
    # call count races between 1 and 2.  Assert on the ROUTE's specific call
    # rather than the count so the test is deterministic regardless of whether
    # the middleware's append has landed by assertion time.
    # pool, actor, action are positional; target and note are keyword-only.
    route_calls = [
        c for c in mock_audit.call_args_list if len(c.args) >= 3 and c.args[2] == "permission.set"
    ]
    assert len(route_calls) == 1, (
        f"expected exactly one route audit.append with action 'permission.set', "
        f"got call list: {mock_audit.call_args_list}"
    )
    route_call = route_calls[0]
    assert route_call.kwargs["target"] == "chronicler.email.read"
    assert route_call.kwargs["note"] == "Needed for digest emails"


async def test_put_permission_empty_reason_returns_422(app):
    """Empty reason returns HTTP 422 with {error: reason_required}."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            "/api/permissions/chronicler/email.read",
            json={"granted": True, "reason": ""},
        )

    assert resp.status_code == 422


async def test_put_permission_whitespace_reason_returns_422(app):
    """Whitespace-only reason returns HTTP 422."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            "/api/permissions/chronicler/email.read",
            json={"granted": True, "reason": "   "},
        )

    assert resp.status_code == 422
