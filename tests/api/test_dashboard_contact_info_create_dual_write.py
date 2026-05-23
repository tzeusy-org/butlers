"""Parity tests for dual-write shim Group I — dashboard API contact_info INSERT.

``create_contact_info`` (roster/relationship/api/router.py) handles
``POST /api/relationship/contacts/{contact_id}/contact-info``.  After a
successful INSERT it calls ``emit_contact_info_fact()`` best-effort
(Amendment 14).

Design contract:
- SQL is authoritative.  The INSERT commits first; the shim is post-commit
  and best-effort.
- No ``ON CONFLICT DO NOTHING`` here: the endpoint raises HTTP 409 on
  ``asyncpg.UniqueViolationError``.  When the conflict path is taken the
  shim is NOT called because the function raises before reaching the shim.
- Shim failures are swallowed; the SQL commit is never rolled back and the
  HTTP response is still 201.
- The shim is gated by ``BUTLERS_CONTACT_INFO_DUAL_WRITE`` (checked inside
  the helper, not at the call site).

Test scope:
  (a) Successful INSERT → shim called with correct kwargs from RETURNING row.
  (b) UniqueViolationError (409) → shim NOT called.
  (c) Flag off → shim IS called at the call site (flag delegated to helper).
  (d) Shim raises → failure swallowed; HTTP 201 still returned.
  (e) SQL INSERT executes before the shim (Amendment 14 ordering).
  (f) Secured rows (credentials) → shim NOT called (spec: no triples for secured rows).

[bu-3jfvv]
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FLAG_ENV = "BUTLERS_CONTACT_INFO_DUAL_WRITE"
# Patch at the router module's namespace: router.py uses a static import so the
# bound name lives in ``relationship_api_router`` (the sys.modules key assigned
# by router_discovery.py: module_name = f"{butler_name}_api_router").
_EMIT_FACT_PATCH = "relationship_api_router.emit_contact_info_fact"
_AUDIT_PATCH = "butlers.api.audit_emit.emit_dashboard_audit"

BASE_URL = "http://test"

_CONTACT_ID = uuid.uuid4()
_CI_ID = uuid.uuid4()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ci_row(
    *,
    contact_id: uuid.UUID | None = None,
    ci_type: str = "email",
    value: str = "alice@example.com",
    is_primary: bool = True,
    secured: bool = False,
) -> MagicMock:
    """Simulate the asyncpg RETURNING row from the INSERT."""
    data = {
        "id": _CI_ID,
        "contact_id": contact_id or _CONTACT_ID,
        "type": ci_type,
        "value": value,
        "is_primary": is_primary,
        "secured": secured,
        "parent_id": None,
        "context": None,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _make_pool(
    *,
    contact_exists: bool = True,
    ci_row: MagicMock | None = None,
    unique_violation: bool = False,
) -> AsyncMock:
    """Build a mock asyncpg pool for create_contact_info.

    The endpoint makes two fetchrow calls:
      1. ``SELECT id FROM contacts WHERE id = $1 AND archived_at IS NULL``
         → contact existence check (returns a row or None).
      2. ``INSERT INTO public.contact_info ... RETURNING ...``
         → the actual INSERT (returns ci_row or raises UniqueViolationError).

    Parameters
    ----------
    contact_exists:
        When True, the first fetchrow returns a stub contact row.
        When False, returns None (404 path).
    ci_row:
        Row returned by the INSERT fetchrow.  Defaults to ``_make_ci_row()``.
    unique_violation:
        When True, the second fetchrow raises asyncpg.UniqueViolationError.
    """
    pool = AsyncMock()
    contact_row = MagicMock()
    contact_row.__getitem__ = MagicMock(side_effect=lambda k: {"id": _CONTACT_ID}[k])

    if unique_violation:
        pool.fetchrow = AsyncMock(
            side_effect=[
                contact_row if contact_exists else None,
                asyncpg.UniqueViolationError(),
            ]
        )
    else:
        pool.fetchrow = AsyncMock(
            side_effect=[
                contact_row if contact_exists else None,
                ci_row or _make_ci_row(),
            ]
        )
    return pool


def _wire_app(mock_pool: AsyncMock) -> FastAPI:
    """Attach a mock pool to a fresh create_app() instance."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break
    return app


async def _post_create_contact_info(
    app: FastAPI,
    contact_id: uuid.UUID = _CONTACT_ID,
    *,
    ci_type: str = "email",
    value: str = "alice@example.com",
    is_primary: bool = True,
    secured: bool = False,
) -> httpx.Response:
    """POST /api/relationship/contacts/{contact_id}/contact-info."""
    path = f"/api/relationship/contacts/{contact_id}/contact-info"
    body = {
        "type": ci_type,
        "value": value,
        "is_primary": is_primary,
        "secured": secured,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.post(path, json=body)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateContactInfoDualWriteShim:
    """create_contact_info: emit_contact_info_fact called after successful INSERT."""

    async def test_insert_success_shim_called_with_correct_kwargs(self, monkeypatch: Any) -> None:
        """(a) Successful INSERT → shim called with correct kwargs from RETURNING row."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        ci_row = _make_ci_row(
            contact_id=contact_id,
            ci_type="email",
            value="bob@example.com",
            is_primary=True,
        )
        pool = _make_pool(ci_row=ci_row)
        app = _wire_app(pool)

        with (
            patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit,
            patch(_AUDIT_PATCH, new_callable=AsyncMock),
        ):
            resp = await _post_create_contact_info(
                app,
                contact_id,
                ci_type="email",
                value="bob@example.com",
                is_primary=True,
            )

        assert resp.status_code == 201, resp.text
        mock_emit.assert_awaited_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["contact_id"] == contact_id
        assert kwargs["ci_type"] == "email"
        assert kwargs["value"] == "bob@example.com"
        assert kwargs["is_primary"] is True
        assert kwargs["src"] == "dual-write"

    async def test_unique_violation_raises_409_shim_not_called(self, monkeypatch: Any) -> None:
        """(b) UniqueViolationError → HTTP 409 raised, shim NOT called."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        pool = _make_pool(unique_violation=True)
        app = _wire_app(pool)

        with (
            patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit,
            patch(_AUDIT_PATCH, new_callable=AsyncMock),
        ):
            resp = await _post_create_contact_info(app)

        assert resp.status_code == 409, resp.text
        mock_emit.assert_not_awaited()

    async def test_flag_off_shim_still_called_at_call_site(self, monkeypatch: Any) -> None:
        """(c) Flag off → shim IS called (flag delegated to helper, not checked at call site).

        The call site always calls emit_contact_info_fact() after a successful
        INSERT.  The BUTLERS_CONTACT_INFO_DUAL_WRITE check lives inside the
        helper.  With the helper mocked, the flag check is bypassed and the
        mock is always invoked.  This verifies the call site does not add its
        own flag gate.
        """
        monkeypatch.delenv(_FLAG_ENV, raising=False)

        pool = _make_pool()
        app = _wire_app(pool)

        with (
            patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit,
            patch(_AUDIT_PATCH, new_callable=AsyncMock),
        ):
            resp = await _post_create_contact_info(app)

        assert resp.status_code == 201, resp.text
        # Flag check is inside the helper — mock bypasses it, so it IS called.
        mock_emit.assert_awaited_once()

    async def test_shim_failure_swallowed_response_still_201(self, monkeypatch: Any) -> None:
        """(d) Shim raises → failure swallowed; HTTP 201 still returned."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        pool = _make_pool()
        app = _wire_app(pool)

        with (
            patch(
                _EMIT_FACT_PATCH,
                new_callable=AsyncMock,
                side_effect=RuntimeError("triple store down"),
            ),
            patch(_AUDIT_PATCH, new_callable=AsyncMock),
        ):
            resp = await _post_create_contact_info(app)

        # Shim failure must NOT propagate as HTTP 500.
        assert resp.status_code == 201, resp.text

    async def test_sql_before_shim_ordering(self, monkeypatch: Any) -> None:
        """(e) SQL INSERT executes before the shim (Amendment 14 ordering)."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        call_order: list[str] = []

        contact_id = uuid.uuid4()

        # Track when fetchrow is called for the INSERT (second call).
        call_count: list[int] = [0]
        ci_row = _make_ci_row(contact_id=contact_id)
        contact_row = MagicMock()
        contact_row.__getitem__ = MagicMock(side_effect=lambda k: {"id": contact_id}[k])

        async def _fetchrow_side_effect(*_a: Any, **_kw: Any) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                return contact_row  # contact existence check
            call_order.append("sql")
            return ci_row  # INSERT RETURNING

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow_side_effect)

        app = _wire_app(pool)

        async def _record_emit(*_args: Any, **_kw: Any) -> None:
            call_order.append("shim")

        with (
            patch(_EMIT_FACT_PATCH, new_callable=AsyncMock, side_effect=_record_emit),
            patch(_AUDIT_PATCH, new_callable=AsyncMock),
        ):
            resp = await _post_create_contact_info(app, contact_id)

        assert resp.status_code == 201, resp.text
        assert call_order == ["sql", "shim"], f"Expected sql before shim, got: {call_order}"

    async def test_secured_row_shim_not_called(self, monkeypatch: Any) -> None:
        """(f) Secured rows MUST NOT be emitted to the triple store per spec.

        ``public.contact_info`` rows with ``secured=True`` are credentials/tokens
        and SHALL NOT be migrated to ``relationship.entity_facts`` as triples
        (per openspec/specs/relationship-facts/spec.md §Credentials carve-out).
        The call site guards ``emit_contact_info_fact()`` behind ``not row["secured"]``.
        """
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        ci_row = _make_ci_row(
            contact_id=contact_id,
            ci_type="telegram",
            value="telegram_session_token",
            is_primary=False,
            secured=True,
        )
        pool = _make_pool(ci_row=ci_row)
        app = _wire_app(pool)

        with (
            patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit,
            patch(_AUDIT_PATCH, new_callable=AsyncMock),
        ):
            resp = await _post_create_contact_info(
                app,
                contact_id,
                ci_type="telegram",
                value="telegram_session_token",
                is_primary=False,
                secured=True,
            )

        assert resp.status_code == 201, resp.text
        # Secured rows must never be emitted to the triple store.
        mock_emit.assert_not_awaited()
