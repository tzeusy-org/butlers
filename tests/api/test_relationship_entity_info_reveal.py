"""Unit tests for GET /entities/{entity_id}/secrets/{info_id}.

Covers the secured credential reveal endpoint for ``public.entity_info`` rows
written via the contact_info write-path cut-over (RFC 0004 Amendment 2, bu-fa5ex).

Acceptance criteria verified:
1. Authorized reveal: owner present + secured entry → 200 with plaintext value.
2. Unauthorized reveal: no owner entity registered → 403 (owner_required).
3. Entry not found: info_id absent or belongs to wrong entity → 404.
4. Not secured: entry exists but secured=False → 400 (value already in detail).
5. Audit emission: successful reveal emits a dashboard audit event.

All tests are unit-level (mock pool — no Postgres or Docker required).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ENTITY_ID = uuid4()
_INFO_ID = uuid4()
_SECRET_VALUE = "super-secret-refresh-token"
_SECRET_TYPE = "google_oauth_refresh"

_REVEAL_PATH = f"/api/relationship/entities/{_ENTITY_ID}/secrets/{_INFO_ID}"

BASE_URL = "http://test"


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_row(data: dict) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    row.get = MagicMock(side_effect=lambda key, default=None: data.get(key, default))
    return row


def _make_owner_row(roles: list[str] | None = None) -> MagicMock:
    """Simulate a row returned by the owner-entity roles query.

    ``_get_owner_roles`` in the router accesses ``row['roles']``.
    """
    return _make_row(
        {
            "id": uuid4(),
            "roles": roles if roles is not None else ["owner"],
        }
    )


def _make_entity_info_row(
    *,
    secured: bool = True,
    value: str = _SECRET_VALUE,
    info_type: str = _SECRET_TYPE,
) -> MagicMock:
    """Simulate a row returned by the public.entity_info SELECT."""
    return _make_row(
        {
            "id": _INFO_ID,
            "type": info_type,
            "value": value,
            "secured": secured,
        }
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_reveal_app(
    *,
    owner_exists: bool = True,
    entry_exists: bool = True,
    secured: bool = True,
    value: str = _SECRET_VALUE,
) -> tuple[FastAPI, AsyncMock, object]:
    """Wire a FastAPI app for GET /entities/{id}/secrets/{info_id} tests.

    Call sequence inside the endpoint:
      1. pool.fetchrow — owner roles check (returns owner row or None)
      2. pool.fetchrow — entity_info SELECT by id + entity_id

    ``owner_exists=False`` makes the first fetchrow return None → 403.
    ``entry_exists=False`` makes the second fetchrow return None → 404.
    ``secured=False`` makes the second fetchrow return a non-secured row → 400.

    Returns (app, mock_pool, router_module) — router_module is exposed so callers
    can patch ``emit_dashboard_audit`` on the dynamically loaded module.
    """
    owner_row = _make_owner_row() if owner_exists else None
    entry_row = _make_entity_info_row(secured=secured, value=value) if entry_exists else None

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(side_effect=[owner_row, entry_row])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    router_mod = None
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            router_mod = router_module
            break

    if router_mod is None:
        raise RuntimeError(
            "Relationship router not found by router discovery. "
            "Cannot wire dependency overrides for reveal endpoint tests."
        )

    return app, mock_pool, router_mod


async def _get(app: FastAPI, path: str = _REVEAL_PATH) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(path)


def _assert_owner_required(resp: httpx.Response) -> None:
    """Assert HTTP 403 with code='owner_required'."""
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
    body = resp.json()
    code = (
        body.get("code")
        or (body.get("error") or {}).get("code")
        or (body.get("detail") or {}).get("code")
    )
    assert code == "owner_required", f"Expected owner_required, got code={code!r}: {body}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRevealEntitySecret:
    """GET /entities/{entity_id}/secrets/{info_id} — secured credential reveal."""

    async def test_authorized_reveal_returns_200_with_value(self):
        """Owner present + secured entry → 200 with plaintext credential value."""
        app, _, router_mod = _make_reveal_app(owner_exists=True, entry_exists=True, secured=True)

        with patch.object(router_mod, "emit_dashboard_audit", new_callable=AsyncMock):
            resp = await _get(app)

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["value"] == _SECRET_VALUE
        assert body["type"] == _SECRET_TYPE
        assert "id" in body

    async def test_unauthorized_returns_403_owner_required(self):
        """No owner entity registered → 403 with owner_required code.

        This is the core RFC 0004 Amendment 2 credential carve-out guard:
        credentials in public.entity_info may only be revealed when the owner
        entity is bootstrapped.
        """
        app, _, _router_mod = _make_reveal_app(owner_exists=False, entry_exists=True, secured=True)
        resp = await _get(app)
        _assert_owner_required(resp)

    async def test_missing_entry_returns_404(self):
        """Owner present but info_id not found → 404."""
        app, _, router_mod = _make_reveal_app(owner_exists=True, entry_exists=False)
        with patch.object(router_mod, "emit_dashboard_audit", new_callable=AsyncMock):
            resp = await _get(app)
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        assert "not found" in resp.json().get("detail", "").lower()

    async def test_non_secured_entry_returns_400(self):
        """Owner present + entry exists but secured=False → 400.

        Non-secured entity_info values are returned in plain view by the entity
        detail endpoint; no reveal is needed and the endpoint enforces this.
        """
        app, _, router_mod = _make_reveal_app(owner_exists=True, entry_exists=True, secured=False)
        with patch.object(router_mod, "emit_dashboard_audit", new_callable=AsyncMock):
            resp = await _get(app)
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        detail = resp.json().get("detail", "")
        assert "not secured" in detail.lower(), f"Expected 'not secured' in detail: {detail}"

    async def test_audit_emitted_on_successful_reveal(self):
        """Successful reveal emits a dashboard audit event with the credential type.

        The audit body must include ``type`` but must NOT include the secret value.
        """
        app, _, router_mod = _make_reveal_app(owner_exists=True, entry_exists=True, secured=True)
        mock_audit = AsyncMock()
        with patch.object(router_mod, "emit_dashboard_audit", mock_audit):
            resp = await _get(app)

        assert resp.status_code == 200
        mock_audit.assert_awaited_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs.get("operation") == "reveal_entity_secret"
        # The audit body must not contain the secret value, only the type.
        audit_body = call_kwargs.get("body", {})
        assert "value" not in audit_body, "Secret value must not appear in audit body"
        assert audit_body.get("type") == _SECRET_TYPE

    async def test_audit_not_emitted_when_owner_gate_fails(self):
        """403 (owner gate) does not emit an audit event."""
        app, _, router_mod = _make_reveal_app(owner_exists=False)
        mock_audit = AsyncMock()
        with patch.object(router_mod, "emit_dashboard_audit", mock_audit):
            resp = await _get(app)

        assert resp.status_code == 403
        mock_audit.assert_not_awaited()

    async def test_owner_with_wrong_roles_returns_403(self):
        """Owner entity exists but 'owner' role not present → 403.

        Regression guard: _assert_owner_role checks the roles list, not just entity
        existence.  An entity with only e.g. ['admin'] roles must be rejected.
        """
        # Build a pool where the owner row has roles=['admin'] (no 'owner' role)
        non_owner_row = _make_row({"id": uuid4(), "roles": ["admin"]})
        entry_row = _make_entity_info_row(secured=True)

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(side_effect=[non_owner_row, entry_row])

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        for butler_name, router_module in app.state.butler_routers:
            if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
                app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
                break

        resp = await _get(app)
        _assert_owner_required(resp)
