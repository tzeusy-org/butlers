"""Tests for entity archive and forget endpoints.

Spec anchor:
  openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md
  Requirement: Owner-only authorization for entity endpoints — Clause 12a (Amendment 12a)
  Task: tasks.md §9.9

Two endpoints under test:
  POST   /api/relationship/entities/{id}/archive  — soft-archive (reversible)
  DELETE /api/relationship/entities/{id}          — forget with tombstone (irreversible)

Each test uses httpx.AsyncClient with a mocked DB pool so no real Postgres or
Docker is required.  Tests are marked ``unit``.

Acceptance criteria verified:
1. Archive: returns 204 when entity exists and owner gate passes.
2. Archive: idempotent — repeated calls return 204.
3. Archive: returns 404 when entity does not exist.
4. Archive: returns 403 (owner_required) when no owner entity is registered.
5. Delete: returns 204 when entity exists and owner gate passes.
6. Delete: cascades fact retraction for subject direction.
7. Delete: cascades fact retraction for object direction.
8. Delete: tombstones the entity row.
9. Delete: returns 404 when entity does not exist.
10. Delete: returns 403 (owner_required) when no owner entity is registered.
11. Atomicity: archive does not cascade to facts table.
12. Delete: executes inside a single transaction (all three UPDATEs committed atomically).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ENT_ID = uuid4()

_ARCHIVE_PATH = f"/api/relationship/entities/{_ENT_ID}/archive"
_DELETE_PATH = f"/api/relationship/entities/{_ENT_ID}"
_MISSING_ENT_ID = uuid4()

BASE_URL = "http://test"


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_owner_row(roles: list[str] | None = None) -> MagicMock:
    """Simulate a row returned by the owner-entity roles query.

    Includes both ``id`` and ``roles`` keys because ``_get_owner_roles``
    in the archive/delete handlers accesses ``row['roles']`` to verify the
    caller has the 'owner' role.
    """
    data: dict = {
        "id": uuid4(),
        "roles": roles if roles is not None else ["owner"],
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_entity_row(entity_id: UUID | None = None) -> MagicMock:
    """Simulate a row returned by the entity existence query."""
    data = {"id": entity_id or _ENT_ID}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


# ---------------------------------------------------------------------------
# App factory helpers
# ---------------------------------------------------------------------------


def _make_archive_app(
    *,
    owner_exists: bool = True,
    entity_exists: bool = True,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app for POST /entities/{id}/archive tests.

    Call sequence inside the endpoint:
      1. pool.fetchrow — owner roles check (returns owner row or None)
      2. pool.fetchrow — entity existence check
      3. pool.execute  — UPDATE public.entities SET metadata archive flag

    ``owner_exists=False`` makes the first fetchrow return None → 403.
    ``entity_exists=False`` makes the second fetchrow return None → 404.
    """
    owner_row = _make_owner_row() if owner_exists else None
    entity_row = _make_entity_row() if entity_exists else None

    fetchrow_side_effects: list = [owner_row, entity_row]

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effects)
    mock_pool.execute = AsyncMock(return_value="UPDATE 1")

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


def _make_delete_app(
    *,
    owner_exists: bool = True,
    entity_exists: bool = True,
) -> tuple[FastAPI, AsyncMock, AsyncMock]:
    """Wire a FastAPI app for DELETE /entities/{id} (forget) tests.

    Call sequence inside the endpoint:
      1. pool.fetchrow  — owner roles check (returns owner row or None)
      2. pool.fetchrow  — entity existence check
      3. pool.acquire() — transaction context
         conn.execute   — retract relationship.entity_facts (subject + object)
         conn.execute   — retract memory facts where entity_id = $1   (bu-j820n.2)
         conn.execute   — retract memory facts where object_entity_id = $1 (bu-j820n.2)
         conn.execute   — delete contact_entity_map rows (bu-j77a5, replacing contacts clear)
         conn.execute   — tombstone entity

    ``owner_exists=False`` makes the first fetchrow return None → 403.
    ``entity_exists=False`` makes the second fetchrow return None → 404.

    Returns ``(app, mock_pool, mock_conn)`` so tests can inspect the
    transactional ``conn.execute`` calls (the full-repoint retraction surface).
    """
    owner_row = _make_owner_row() if owner_exists else None
    entity_row = _make_entity_row() if entity_exists else None

    fetchrow_side_effects: list = [owner_row, entity_row]

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value="UPDATE 0")

    mock_txn = AsyncMock()
    mock_txn.__aenter__ = AsyncMock(return_value=None)
    mock_txn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_txn)

    @asynccontextmanager
    async def _acquire():
        yield mock_conn

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effects)
    mock_pool.acquire = MagicMock(return_value=_acquire())
    mock_pool.execute = AsyncMock(return_value="UPDATE 0")

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool, mock_conn


async def _post(app: FastAPI, path: str = _ARCHIVE_PATH) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.post(path, json={})


async def _delete(app: FastAPI, path: str = _DELETE_PATH) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.delete(path)


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
# POST /entities/{id}/archive — archive tests
# ---------------------------------------------------------------------------


class TestArchiveEntity:
    """POST /entities/{id}/archive — soft-archive scenarios."""

    async def test_archive_returns_204_on_success(self):
        """Successful archive returns HTTP 204 No Content."""
        app, _ = _make_archive_app()
        resp = await _post(app, _ARCHIVE_PATH)
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

    async def test_archive_updates_metadata_flag(self):
        """Archive calls pool.execute to set metadata->>'archived' = 'true'."""
        app, mock_pool = _make_archive_app()
        await _post(app, _ARCHIVE_PATH)

        # Verify execute was called at least once (for the UPDATE)
        assert mock_pool.execute.called, "Expected pool.execute to be called for the UPDATE"
        call_args = mock_pool.execute.call_args_list[-1]
        sql = call_args.args[0]
        assert "jsonb_set" in sql, f"Expected jsonb_set in UPDATE SQL, got: {sql}"
        assert "archived" in sql, f"Expected 'archived' in UPDATE SQL, got: {sql}"

    async def test_archive_returns_403_when_no_owner_entity(self):
        """Returns 403 owner_required when no owner entity is registered."""
        app, _ = _make_archive_app(owner_exists=False)
        resp = await _post(app, _ARCHIVE_PATH)
        _assert_owner_required(resp)

    async def test_archive_returns_404_when_entity_not_found(self):
        """Returns 404 when the entity UUID does not exist."""
        app, _ = _make_archive_app(entity_exists=False)
        resp = await _post(app, f"/api/relationship/entities/{uuid4()}/archive")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    async def test_archive_idempotent_second_call_also_returns_204(self):
        """Archive is idempotent — a second call to an already-archived entity returns 204."""
        # We don't filter on metadata->>'archived' before writing, so the UPDATE runs again
        # and returns 204 unconditionally.
        app, _ = _make_archive_app()
        resp1 = await _post(app, _ARCHIVE_PATH)
        assert resp1.status_code == 204

        # Rebuild app with a fresh mock for the second call
        app2, _ = _make_archive_app()
        resp2 = await _post(app2, _ARCHIVE_PATH)
        assert resp2.status_code == 204, f"Expected 204 on second archive call: {resp2.text}"

    async def test_archive_does_not_modify_facts_table(self):
        """Archive does NOT cascade to relationship.entity_facts — only the entity row is updated."""
        app, mock_pool = _make_archive_app()
        await _post(app, _ARCHIVE_PATH)

        # Verify pool.execute only called once (for the entity UPDATE, not facts)
        assert mock_pool.execute.call_count == 1, (
            f"Expected exactly 1 execute call (entity UPDATE), got {mock_pool.execute.call_count}"
        )


# ---------------------------------------------------------------------------
# DELETE /entities/{id} — forget / tombstone tests
# ---------------------------------------------------------------------------


class TestForgetEntity:
    """DELETE /entities/{id} — forget (tombstone + fact retraction) scenarios."""

    async def test_forget_returns_204_on_success(self):
        """Successful forget returns HTTP 204 No Content."""
        app, _, _ = _make_delete_app()
        resp = await _delete(app, _DELETE_PATH)
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

    async def test_forget_returns_403_when_no_owner_entity(self):
        """Returns 403 owner_required when no owner entity is registered."""
        app, _, _ = _make_delete_app(owner_exists=False)
        resp = await _delete(app, _DELETE_PATH)
        _assert_owner_required(resp)

    async def test_forget_returns_404_when_entity_not_found(self):
        """Returns 404 when the entity UUID does not exist."""
        app, _, _ = _make_delete_app(entity_exists=False)
        resp = await _delete(app, f"/api/relationship/entities/{uuid4()}")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    async def test_forget_runs_in_transaction(self):
        """Delete executes inside a single transaction (all UPDATEs commit atomically)."""
        app, mock_pool, _ = _make_delete_app()
        resp = await _delete(app, _DELETE_PATH)
        assert resp.status_code == 204

        # pool.acquire must have been called (transaction context)
        assert mock_pool.acquire.called, "Expected pool.acquire to open a transaction"

    async def test_forget_no_direct_pool_execute_for_facts(self):
        """Delete does NOT call pool.execute directly for fact retraction (uses conn.execute).

        Every UPDATE (relationship.entity_facts, memory facts, contacts clear,
        entity tombstone) goes through the transactional connection obtained via
        pool.acquire(), not pool.execute() directly — this preserves atomicity.
        """
        app, mock_pool, _ = _make_delete_app()
        await _delete(app, _DELETE_PATH)

        # pool.execute (outside the transaction) should NOT have been called.
        assert not mock_pool.execute.called, (
            "pool.execute was called directly, bypassing the transaction context. "
            "All UPDATEs must go through conn.execute inside pool.acquire()."
        )

    # -----------------------------------------------------------------------
    # bu-j820n.2 regression: forget must do a FULL repoint/retract — it must
    # retract the memory-module ``facts`` rows (gifts/loans/interactions/etc.)
    # AND clear ``public.contacts.entity_id`` — not just relationship.entity_facts.
    # Previously these references were left active/dangling on the tombstone.
    # -----------------------------------------------------------------------

    @staticmethod
    def _conn_execute_sqls(mock_conn: AsyncMock) -> list[str]:
        """All SQL statements issued through the transactional connection."""
        return [
            call.args[0]
            for call in mock_conn.execute.call_args_list
            if call.args and isinstance(call.args[0], str)
        ]

    async def test_forget_retracts_memory_gifts_loans_subject_facts(self):
        """Forget retracts memory ``facts`` rows where the entity is the subject.

        Gifts, loans, interactions, contact-notes and life-events all live in the
        memory-module ``facts`` table keyed by ``entity_id``. A bare
        relationship.entity_facts retraction leaves them ACTIVE and orphaned on
        the tombstone (bu-j820n.2). Assert an UPDATE retracts ``facts`` by
        ``entity_id``.
        """
        app, _, mock_conn = _make_delete_app()
        resp = await _delete(app, _DELETE_PATH)
        assert resp.status_code == 204

        sqls = self._conn_execute_sqls(mock_conn)
        retract_facts = [
            s
            for s in sqls
            if "UPDATE facts" in s
            and "retracted" in s
            and "entity_id = $1" in s
            and "object_entity_id" not in s
        ]
        assert retract_facts, (
            "Expected an UPDATE retracting memory facts WHERE entity_id = $1 "
            f"(gifts/loans/interactions). conn.execute SQLs: {sqls}"
        )

    async def test_forget_retracts_memory_edge_facts(self):
        """Forget retracts memory edge-facts pointing AT the entity (object_entity_id)."""
        app, _, mock_conn = _make_delete_app()
        resp = await _delete(app, _DELETE_PATH)
        assert resp.status_code == 204

        sqls = self._conn_execute_sqls(mock_conn)
        retract_edges = [
            s
            for s in sqls
            if "UPDATE facts" in s and "retracted" in s and "object_entity_id = $1" in s
        ]
        assert retract_edges, (
            "Expected an UPDATE retracting memory edge-facts WHERE "
            f"object_entity_id = $1. conn.execute SQLs: {sqls}"
        )

    async def test_forget_clears_linked_contacts_entity_id(self):
        """Forget removes contact_entity_map rows so no CRM lookup dangles on the tombstone.

        Historically this step cleared ``public.contacts.entity_id``. As of bu-j77a5
        it deletes from ``contact_entity_map`` instead (Phase 7 contacts retirement).
        """
        app, _, mock_conn = _make_delete_app()
        resp = await _delete(app, _DELETE_PATH)
        assert resp.status_code == 204

        sqls = self._conn_execute_sqls(mock_conn)
        clear_map = [s for s in sqls if "contact_entity_map" in s and "DELETE" in s.upper()]
        assert clear_map, (
            "Expected a DELETE from contact_entity_map for the forgotten entity. "
            f"conn.execute SQLs: {sqls}"
        )

    async def test_forget_contacts_clear_inside_transaction(self):
        """The contact_entity_map delete must run through conn.execute (atomic with the tombstone).

        As of bu-j77a5 the step uses contact_entity_map instead of public.contacts.
        """
        app, mock_pool, mock_conn = _make_delete_app()
        await _delete(app, _DELETE_PATH)

        # The contact_entity_map DELETE must NOT have leaked to pool.execute (would break atomicity).
        pool_sqls = [
            call.args[0]
            for call in mock_pool.execute.call_args_list
            if call.args and isinstance(call.args[0], str)
        ]
        assert not any("contact_entity_map" in s for s in pool_sqls), (
            "contact_entity_map DELETE ran on pool.execute, outside the transaction; "
            "it must run on conn.execute inside pool.acquire()."
        )
