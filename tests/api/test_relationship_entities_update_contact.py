"""Tests for PUT /api/relationship/entities/{id}/contacts/{pred}/{valueHash}.

Covers the edit-in-place endpoint added in bu-690xu:
  PUT /api/relationship/entities/{id}/contacts/{predicate}/{value_hash}

Acceptance criteria:
1. PUT with new_value different from old: retracts old fact + asserts new; returns 200 + fact.
2. PUT with same new_value as old (provenance-only update): no retraction; returns 200 + fact.
3. PUT with value_hash that doesn't match any active fact: returns 404.
4. PUT with unknown entity UUID: returns 404.
5. PUT with non-has-* predicate: returns 400.
6. PUT with empty/whitespace new_value: returns 400.
7. PUT with no owner entity registered: returns 403.
8. PUT where relationship_assert_fact returns pending_approval:
   old fact is re-activated; returns 202 + action_id.
9. Atomicity: retract + assert happen inside a single transaction (conn= is passed
   to relationship_assert_fact so no nested transaction).

All tests are unit-level (mock pool — no Postgres or Docker required).
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch
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
_FACT_ID = uuid4()
_OWNER_ENTITY_ID = uuid4()
_MISSING_ENT_ID = uuid4()

_OLD_EMAIL = "old@example.com"
_OLD_HASH = hashlib.sha256(_OLD_EMAIL.encode("utf-8")).hexdigest()[:16]
_NEW_EMAIL = "new@example.com"

_PATH = f"/api/relationship/entities/{_ENT_ID}/contacts/has-email/{_OLD_HASH}"


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_contact_fact_row(
    *,
    fact_id: UUID | None = None,
    predicate: str = "has-email",
    object_val: str = _OLD_EMAIL,
    src: str = "relationship",
    conf: float = 1.0,
    last_seen=None,
    weight: int | None = None,
    verified: bool = False,
    primary: bool | None = None,
) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for a facts row."""
    data = {
        "id": fact_id or _FACT_ID,
        "predicate": predicate,
        "object": object_val,
        "src": src,
        "conf": conf,
        "last_seen": last_seen,
        "weight": weight,
        "verified": verified,
        "primary": primary,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_owner_row() -> MagicMock:
    """Simulate owner entity check row."""
    data = {"id": _OWNER_ENTITY_ID, "roles": ["owner"]}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_candidate_row(
    *,
    fact_id: UUID | None = None,
    object_val: str = _OLD_EMAIL,
) -> MagicMock:
    """Build a candidate row for the value-hash lookup."""
    data = {"id": fact_id or _FACT_ID, "object": object_val}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


# ---------------------------------------------------------------------------
# AssertResult helper
# ---------------------------------------------------------------------------


def _make_assert_result(
    outcome: str = "superseded",
    fact_id: UUID | None = None,
    action_id: UUID | None = None,
):
    from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult

    return AssertResult(
        outcome=AssertOutcome(outcome),
        fact_id=fact_id or uuid4(),
        action_id=action_id,
    )


# ---------------------------------------------------------------------------
# App factory
#
# The PUT endpoint calls the pool in several ways:
#   - pool.fetchrow: owner check (returns owner row)
#   - pool.fetchval: entity exists check (returns 1 or None)
#   - pool.fetch: candidate rows for value-hash lookup
#   - pool.execute: retract old fact (UPDATE validity='retracted')
#   - pool.fetchrow again: fetch new fact row after write
#   - pool.acquire() → conn.transaction() → conn.execute + relationship_assert_fact(conn=)
#
# We patch relationship_assert_fact at the module import level so the endpoint
# uses the mock regardless of which connection/pool it receives.
# ---------------------------------------------------------------------------


def _make_app(
    *,
    owner_exists: bool = True,
    entity_exists: bool = True,
    candidate_rows: list | None = None,
    new_fact_row: MagicMock | None = None,
    fetchrow_side_effect=None,
    fetchval_side_effect=None,
) -> tuple[FastAPI, MagicMock]:
    """Wire a FastAPI app with a mocked relationship DB pool."""
    # Mock connection returned by pool.acquire()
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value="UPDATE 1")
    # transaction() returns an async context manager
    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=None)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    # pool.acquire() is an async context manager returning mock_conn
    mock_acquire_ctx = AsyncMock()
    mock_acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire_ctx.__aexit__ = AsyncMock(return_value=False)

    if fetchrow_side_effect is not None:
        mock_fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        # Default: first call = owner check, subsequent calls = new fact row
        owner_row = _make_owner_row() if owner_exists else None
        fact_row = new_fact_row or _make_contact_fact_row(object_val=_NEW_EMAIL)
        mock_fetchrow = AsyncMock(side_effect=[owner_row, fact_row])

    if fetchval_side_effect is not None:
        mock_fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        mock_fetchval = AsyncMock(return_value=1 if entity_exists else None)

    mock_pool = AsyncMock()
    mock_pool.fetchrow = mock_fetchrow
    mock_pool.fetchval = mock_fetchval
    mock_pool.fetch = AsyncMock(return_value=candidate_rows or [])
    mock_pool.execute = AsyncMock(return_value="UPDATE 1")
    mock_pool.acquire = MagicMock(return_value=mock_acquire_ctx)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _put(
    app: FastAPI,
    path: str = _PATH,
    json_body: dict | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.put(path, json=json_body or {"new_value": _NEW_EMAIL})


# ===========================================================================
# PUT — happy path: different new value (retract + assert)
# ===========================================================================


class TestPutEntityContactDifferentValue:
    """PUT with a new value different from the old retracts + asserts atomically."""

    async def test_response_has_outcome_and_retracted_fact_id(self):
        candidate = _make_candidate_row(fact_id=_FACT_ID, object_val=_OLD_EMAIL)
        app, _ = _make_app(candidate_rows=[candidate])
        new_fact_id = uuid4()
        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(return_value=_make_assert_result("superseded", fact_id=new_fact_id)),
        ):
            resp = await _put(app, json_body={"new_value": _NEW_EMAIL})

        assert resp.status_code == 200
        body = resp.json()
        assert body["outcome"] == "superseded"
        assert body["retracted_fact_id"] == str(_FACT_ID)
        assert body["fact"] is not None
        assert body["action_id"] is None

    async def test_new_fact_value_and_hash_in_response(self):
        candidate = _make_candidate_row(fact_id=_FACT_ID, object_val=_OLD_EMAIL)
        new_fact_id = uuid4()
        new_row = _make_contact_fact_row(fact_id=new_fact_id, object_val=_NEW_EMAIL)
        app, _ = _make_app(candidate_rows=[candidate], new_fact_row=new_row)
        expected_hash = hashlib.sha256(_NEW_EMAIL.encode("utf-8")).hexdigest()[:16]
        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(return_value=_make_assert_result("superseded", fact_id=new_fact_id)),
        ):
            resp = await _put(app, json_body={"new_value": _NEW_EMAIL})

        body = resp.json()
        assert body["fact"]["object"] == _NEW_EMAIL
        assert body["fact"]["value_hash"] == expected_hash


# ===========================================================================
# PUT — happy path: same value (provenance-only update via unchanged/superseded)
# ===========================================================================


class TestPutEntityContactSameValue:
    """PUT with the same value as the current one updates provenance fields only."""

    async def test_returns_200_on_unchanged(self):
        candidate = _make_candidate_row(fact_id=_FACT_ID, object_val=_OLD_EMAIL)
        # fetchrow: owner + unchanged fact row
        owner_row = _make_owner_row()
        same_row = _make_contact_fact_row(fact_id=_FACT_ID, object_val=_OLD_EMAIL)
        app, _ = _make_app(
            candidate_rows=[candidate],
            fetchrow_side_effect=[owner_row, same_row],
        )
        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(return_value=_make_assert_result("unchanged", fact_id=_FACT_ID)),
        ):
            resp = await _put(app, json_body={"new_value": _OLD_EMAIL})

        assert resp.status_code == 200
        body = resp.json()
        assert body["outcome"] == "unchanged"
        # No retraction for same-value update
        assert body["retracted_fact_id"] is None
        assert body["fact"] is not None

    async def test_returns_200_on_superseded_provenance(self):
        candidate = _make_candidate_row(fact_id=_FACT_ID, object_val=_OLD_EMAIL)
        owner_row = _make_owner_row()
        same_row = _make_contact_fact_row(fact_id=_FACT_ID, object_val=_OLD_EMAIL, verified=True)
        app, _ = _make_app(
            candidate_rows=[candidate],
            fetchrow_side_effect=[owner_row, same_row],
        )
        new_fact_id = uuid4()
        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(return_value=_make_assert_result("superseded", fact_id=new_fact_id)),
        ):
            resp = await _put(app, json_body={"new_value": _OLD_EMAIL, "verified": True})

        assert resp.status_code == 200
        body = resp.json()
        assert body["retracted_fact_id"] is None


# ===========================================================================
# PUT — 404 paths
# ===========================================================================


class TestPutEntityContactNotFound:
    """No active fact matching (entity_id, predicate, value_hash) → 404."""

    async def test_returns_404_when_no_candidate_rows(self):
        app, _ = _make_app(candidate_rows=[])
        resp = await _put(app, json_body={"new_value": _NEW_EMAIL})
        assert resp.status_code == 404
        detail = resp.json().get("detail", {})
        assert isinstance(detail, dict)
        assert detail.get("code") == "contact_fact_not_found"

    async def test_returns_404_when_hash_does_not_match(self):
        # Candidate exists but hash is for a different value.
        candidate = _make_candidate_row(object_val="other@example.com")
        app, _ = _make_app(candidate_rows=[candidate])
        resp = await _put(app, json_body={"new_value": _NEW_EMAIL})
        assert resp.status_code == 404

    async def test_returns_404_for_unknown_entity(self):
        app, _ = _make_app(owner_exists=True, entity_exists=False)
        resp = await _put(
            app,
            path=f"/api/relationship/entities/{_MISSING_ENT_ID}/contacts/has-email/{_OLD_HASH}",
            json_body={"new_value": _NEW_EMAIL},
        )
        assert resp.status_code == 404


# ===========================================================================
# PUT — 400 paths
# ===========================================================================


class TestPutEntityContactInvalidInput:
    """Malformed requests return 400."""

    @pytest.mark.parametrize("predicate", ["knows", "email"])
    async def test_returns_400_for_non_contact_predicate(self, predicate):
        """Non-has-* predicates (and those missing the has- prefix) are rejected 400."""
        app, _ = _make_app()
        resp = await _put(
            app,
            path=f"/api/relationship/entities/{_ENT_ID}/contacts/{predicate}/{_OLD_HASH}",
            json_body={"new_value": _NEW_EMAIL},
        )
        assert resp.status_code == 400
        detail = resp.json().get("detail", {})
        assert isinstance(detail, dict)
        assert detail.get("code") == "invalid_predicate"

    @pytest.mark.parametrize("new_value", ["", "   "])
    async def test_returns_400_for_empty_new_value(self, new_value):
        """Empty or whitespace-only new_value is rejected 400 (invalid_value)."""
        candidate = _make_candidate_row(fact_id=_FACT_ID, object_val=_OLD_EMAIL)
        app, _ = _make_app(candidate_rows=[candidate])
        resp = await _put(app, json_body={"new_value": new_value})
        assert resp.status_code == 400
        detail = resp.json().get("detail", {})
        assert isinstance(detail, dict)
        assert detail.get("code") == "invalid_value"


# ===========================================================================
# PUT — 403 owner gate
# ===========================================================================


class TestPutEntityContactOwnerGate:
    """Clause 12a: PUT returns 403 when no owner entity is registered."""

    async def test_returns_403_when_no_owner_entity(self):
        candidate = _make_candidate_row(fact_id=_FACT_ID, object_val=_OLD_EMAIL)
        app, _ = _make_app(owner_exists=False, candidate_rows=[candidate])
        resp = await _put(app, json_body={"new_value": _NEW_EMAIL})
        assert resp.status_code == 403
        body = resp.json()
        # The 403 response body may be the detail dict directly or wrapped under "detail"
        detail = body.get("detail", body)
        assert isinstance(detail, dict)
        assert detail.get("code") == "owner_required"


# ===========================================================================
# PUT — owner entity carve-out → 202 pending_approval
# ===========================================================================


class TestPutEntityContactOwnerCarveOut:
    """When the subject is the owner entity, the write is parked for approval."""

    async def test_returns_202_with_action_id_for_owner_entity(self):
        candidate = _make_candidate_row(fact_id=_FACT_ID, object_val=_OLD_EMAIL)
        action_id = uuid4()
        app, mock_pool = _make_app(candidate_rows=[candidate])
        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(
                return_value=_make_assert_result(
                    "pending_approval", fact_id=None, action_id=action_id
                )
            ),
        ):
            resp = await _put(app, json_body={"new_value": _NEW_EMAIL})

        assert resp.status_code == 202
        body = resp.json()
        assert body["outcome"] == "pending_approval"
        assert body["fact"] is None
        assert body["retracted_fact_id"] is None
        assert UUID(body["action_id"]) == action_id

    async def test_transaction_rolled_back_when_pending_approval(self):
        """When pending_approval fires, the transaction is rolled back atomically.

        The fix raises _PendingApproval inside the transaction block so the retract
        is never committed — no separate re-activation UPDATE is needed or issued.
        pool.execute should NOT have been called on the pool directly (only on
        the mock connection inside the transaction, which is rolled back).
        """
        candidate = _make_candidate_row(fact_id=_FACT_ID, object_val=_OLD_EMAIL)
        action_id = uuid4()
        app, mock_pool = _make_app(candidate_rows=[candidate])
        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(
                return_value=_make_assert_result(
                    "pending_approval", fact_id=None, action_id=action_id
                )
            ),
        ):
            await _put(app, json_body={"new_value": _NEW_EMAIL})

        # The transaction is rolled back via exception — pool.execute (top-level pool,
        # outside the connection) must NOT have been called with a re-activation SQL.
        for call in mock_pool.execute.call_args_list:
            sql = call[0][0] if call[0] else ""
            assert "active" not in sql.lower(), (
                "pool.execute should not re-activate the old fact; the rollback handles it"
            )
