"""Tests for permissions matrix API.

Covers:
- GET /api/permissions returns DENSE matrix (all active butlers × enforced perm set).
- inherited:true for a (butler, perm) pair with no explicit row.
- inherited:false for a pair with an explicit row.
- permissions list == enforced set from butlers.core.permissions.
- PUT /api/permissions/{butler}/{perm} happy path.
- PUT returns 422 + {"error": "reason_required"} when reason is empty.
- PUT returns 422 when reason is whitespace-only.
- audit.append is called on successful mutation.
- PUT returns 422 + {"error": "reason_contains_credential"} when reason matches a credential pattern.
- No state change and no audit row when reason_contains_credential fires.
- dispatch_event is called for permission.set on successful mutation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.permissions import _get_db_manager
from butlers.core.permissions import ENFORCED_PERMISSIONS

pytestmark = pytest.mark.unit

_ENFORCED_SORTED = sorted(ENFORCED_PERMISSIONS)


def _make_records(rows: list[dict]) -> list[MagicMock]:
    records = []
    for row in rows:
        m = MagicMock()
        m.__getitem__ = MagicMock(side_effect=lambda k, _r=row: _r[k])
        records.append(m)
    return records


def _make_pool(
    rows: list[dict] | None = None,
    butler_rows: list[dict] | None = None,
) -> AsyncMock:
    """Return an asyncpg pool mock wired for two sequential fetch calls.

    GET /api/permissions calls pool.fetch twice:
      1. butler_registry query  → butler_rows
      2. public.permissions query → rows
    PUT /api/permissions doesn't call pool.fetch at all.
    """
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(
        side_effect=[
            _make_records(butler_rows or []),
            _make_records(rows or []),
        ]
    )
    return pool


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
    """With no butlers and no rows the cells dict is empty; perms = enforced set."""
    pool = _make_pool(rows=[], butler_rows=[])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/permissions")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["butlers"] == []
    assert body["data"]["permissions"] == _ENFORCED_SORTED
    assert body["data"]["cells"] == {}


async def test_get_permissions_dense_matrix_enforced_perms(app):
    """Matrix is dense: all active butlers × enforced perms, inherited where no row."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    butler_rows = [{"name": "chronicler"}, {"name": "messenger"}]
    perm_rows = [
        {
            "butler": "chronicler",
            "permission": "spawn",
            "granted": False,
            "reason": "revoked by test",
            "updated_at": now,
        },
    ]
    pool = _make_pool(rows=perm_rows, butler_rows=butler_rows)
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/permissions")

    assert resp.status_code == 200
    data = resp.json()["data"]

    # Permissions list must equal the enforced set exactly.
    assert data["permissions"] == _ENFORCED_SORTED

    # Both butlers appear.
    assert sorted(data["butlers"]) == ["chronicler", "messenger"]

    # DENSE: every (butler × perm) cell is present.
    for butler in ["chronicler", "messenger"]:
        assert set(data["cells"][butler].keys()) == set(_ENFORCED_SORTED), (
            f"{butler} is missing some perm cells"
        )

    # Explicit row → inherited:false, value from DB.
    spawn_cell = data["cells"]["chronicler"]["spawn"]
    assert spawn_cell["inherited"] is False
    assert spawn_cell["granted"] is False
    assert spawn_cell["reason"] == "revoked by test"

    # Unset pair → inherited:true, system default (True).
    notify_cell = data["cells"]["chronicler"]["notify"]
    assert notify_cell["inherited"] is True
    assert notify_cell["granted"] is True

    # Unrelated butler — all cells inherited.
    for perm in _ENFORCED_SORTED:
        assert data["cells"]["messenger"][perm]["inherited"] is True


async def test_get_permissions_inherited_false_after_explicit_row(app):
    """A cell with an explicit row has inherited:false; all others inherited:true."""
    now = datetime(2026, 6, 1, tzinfo=UTC)
    butler_rows = [{"name": "finance"}]
    perm_rows = [
        {
            "butler": "finance",
            "permission": "email.send",
            "granted": True,
            "reason": "owner approved",
            "updated_at": now,
        },
    ]
    pool = _make_pool(rows=perm_rows, butler_rows=butler_rows)
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/permissions")

    data = resp.json()["data"]
    assert data["cells"]["finance"]["email.send"]["inherited"] is False
    assert data["cells"]["finance"]["email.send"]["granted"] is True

    # Every other perm for this butler must be inherited.
    for perm in _ENFORCED_SORTED:
        if perm != "email.send":
            assert data["cells"]["finance"][perm]["inherited"] is True, (
                f"expected inherited:true for finance/{perm}"
            )


async def test_get_permissions_butler_only_in_perm_rows(app):
    """A butler present in perm rows but not butler_registry still appears (dense)."""
    now = datetime(2026, 6, 1, tzinfo=UTC)
    # butler_registry is empty but perm rows reference "orphan"
    perm_rows = [
        {
            "butler": "orphan",
            "permission": "notify",
            "granted": False,
            "reason": "restricted",
            "updated_at": now,
        },
    ]
    pool = _make_pool(rows=perm_rows, butler_rows=[])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/permissions")

    data = resp.json()["data"]
    assert "orphan" in data["butlers"]
    assert data["cells"]["orphan"]["notify"]["inherited"] is False
    assert data["cells"]["orphan"]["notify"]["granted"] is False


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
                "/api/permissions/chronicler/spawn",
                json={"granted": True, "reason": "Needed for scheduled sessions"},
            )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["butler"] == "chronicler"
    assert data["permission"] == "spawn"
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
    assert route_call.kwargs["target"] == "chronicler.spawn"
    assert route_call.kwargs["note"] == "Needed for scheduled sessions"


async def test_put_permission_empty_reason_returns_422(app):
    """Empty reason returns HTTP 422 with {error: reason_required}."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            "/api/permissions/chronicler/spawn",
            json={"granted": True, "reason": ""},
        )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "reason_required"
    pool.execute.assert_not_called()


async def test_put_permission_whitespace_reason_returns_422(app):
    """Whitespace-only reason returns HTTP 422 with {error: reason_required}."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            "/api/permissions/chronicler/spawn",
            json={"granted": True, "reason": "   "},
        )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "reason_required"
    pool.execute.assert_not_called()


async def test_put_permission_missing_reason_returns_422(app):
    """A request body with no reason field returns 422 with {error: reason_required}.

    The reason guard lives in the route handler (not a Pydantic validator), so a
    missing field defaults to "" and still yields the spec-mandated body shape
    rather than a generic Pydantic "field required" error.
    """
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            "/api/permissions/chronicler/spawn",
            json={"granted": True},
        )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "reason_required"
    pool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# validate_no_secrets — unit tests for the security helper
# ---------------------------------------------------------------------------


def test_validate_no_secrets_clean_passes():
    from butlers.api.security import validate_no_secrets

    assert validate_no_secrets("Needed for scheduled sessions") is True
    assert validate_no_secrets("Granting calendar access for butler") is True
    assert validate_no_secrets("") is True


def test_validate_no_secrets_rejects_credential_patterns():
    from butlers.api.security import validate_no_secrets

    assert validate_no_secrets("my password is here") is False
    assert validate_no_secrets("auth token") is False
    assert validate_no_secrets("SECRET value") is False
    assert validate_no_secrets("api_key=abc123") is False
    assert validate_no_secrets("api-key=abc123") is False
    assert validate_no_secrets("apikey abc") is False
    assert validate_no_secrets("credential leak") is False
    assert validate_no_secrets("private_key data") is False
    assert validate_no_secrets("private-key data") is False
    assert validate_no_secrets("Privatekey") is False


# ---------------------------------------------------------------------------
# PUT — credential-pattern guard
# ---------------------------------------------------------------------------


async def test_put_permission_credential_reason_returns_422(app):
    """Reason matching a credential pattern → 422 reason_contains_credential."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch(
        "butlers.api.routers.permissions.audit.append", new_callable=AsyncMock
    ) as mock_audit:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/permissions/chronicler/spawn",
                json={"granted": True, "reason": "my token for this butler"},
            )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "reason_contains_credential"
    # No DB write and no audit entry must be emitted.
    pool.execute.assert_not_called()
    route_calls = [
        c for c in mock_audit.call_args_list if len(c.args) >= 3 and c.args[2] == "permission.set"
    ]
    assert route_calls == [], f"No audit row expected, got: {mock_audit.call_args_list}"


async def test_put_permission_credential_reason_no_state_change(app):
    """Various credential patterns each produce 422 with no DB mutation."""
    credential_reasons = [
        "password required here",
        "api_key=sk-1234",
        "private_key material",
        "SECRET bearer",
    ]
    for reason in credential_reasons:
        pool = _make_pool()
        db = _make_db(pool)
        app.dependency_overrides[_get_db_manager] = lambda: db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/permissions/chronicler/spawn",
                json={"granted": True, "reason": reason},
            )

        assert resp.status_code == 422, f"Expected 422 for reason: {reason!r}"
        assert resp.json()["detail"]["error"] == "reason_contains_credential"
        pool.execute.assert_not_called()


async def test_put_permission_clean_reason_still_succeeds(app):
    """A reason with no credential keywords still goes through normally."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch(
        "butlers.api.routers.permissions.audit.append", new_callable=AsyncMock
    ) as mock_audit:
        with patch("butlers.api.routers.permissions.dispatch_event") as mock_dispatch:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    "/api/permissions/chronicler/spawn",
                    json={"granted": True, "reason": "Granting access for automated scheduling"},
                )

    assert resp.status_code == 200
    pool.execute.assert_called_once()
    route_calls = [
        c for c in mock_audit.call_args_list if len(c.args) >= 3 and c.args[2] == "permission.set"
    ]
    assert len(route_calls) == 1
    # Production dispatch must be triggered for permission.set.
    mock_dispatch.assert_called_once()
    dispatch_call = mock_dispatch.call_args
    assert dispatch_call[0][1] == "permission.set"


async def test_put_permission_dispatches_event_on_success(app):
    """PUT /api/permissions calls dispatch_event with permission.set on success."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    dispatched: list[tuple] = []

    async def _noop():
        pass

    def fake_dispatch(p, event_name, payload=None):
        dispatched.append((event_name, payload or {}))
        import asyncio

        return asyncio.ensure_future(_noop())

    with (
        patch("butlers.api.routers.permissions.audit.append", new_callable=AsyncMock),
        patch("butlers.api.routers.permissions.dispatch_event", side_effect=fake_dispatch),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/permissions/general/notify",
                json={"granted": True, "reason": "enabling notify"},
            )

    assert resp.status_code == 200
    assert len(dispatched) == 1
    event_name, payload = dispatched[0]
    assert event_name == "permission.set"
    assert payload.get("target") == "general.notify"
    assert payload.get("granted") is True
