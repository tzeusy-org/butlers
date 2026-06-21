"""Tests for /api/relationship/entities/{id}/contacts CRUD endpoints.

Covers spec scenarios from
``openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md``
§ "Requirement: Owner-only authorization for entity endpoints" Amendment 12a/12b,
and the contact-fact CRUD requirement (tasks.md §9.4).

Three endpoints under test:
  GET  /api/relationship/entities/{id}/contacts
  POST /api/relationship/entities/{id}/contacts
  DELETE /api/relationship/entities/{id}/contacts/{pred}/{valueHash}

Each test uses httpx.AsyncClient with a mocked DB pool so no real Postgres or
Docker is required.  Tests are marked ``unit``.

Acceptance criteria:
1. GET returns flat list of active contact-fact triples (predicate LIKE 'has-%',
   validity='active', scope='relationship'), with all provenance fields.
2. GET returns empty list for entity with no contact facts.
3. GET returns 404 for unknown entity.
4. GET returns 403 (owner_required) when no owner entity registered.
5. POST creates a new contact fact via central writer; returns 201 + fact.
6. POST is idempotent (unchanged → returns fact row at 201).
7. POST with owner entity subject returns 202 + action_id (pending_approval).
8. POST returns 400 for non-has-* predicate.
9. POST returns 403 (owner_required) when no owner entity.
10. POST returns 404 for unknown entity.
11. DELETE retracts the fact; returns 200 + fact_id.
12. DELETE returns 404 when no active fact matches (entity_id, predicate, valueHash).
13. DELETE returns 404 for unknown entity.
14. DELETE returns 403 (owner_required) when no owner entity.
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

_EMAIL = "alice@example.com"
_EMAIL_HASH = hashlib.sha256(_EMAIL.encode("utf-8")).hexdigest()[:16]

_CONTACTS_PATH = f"/api/relationship/entities/{_ENT_ID}/contacts"


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_contact_fact_row(
    *,
    fact_id: UUID | None = None,
    predicate: str = "has-email",
    object_val: str = _EMAIL,
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
    """Simulate a row returned by the owner-entity check query.

    Must include ``roles`` so that ``_get_owner_roles`` can inspect it.
    The endpoint uses ``_get_owner_roles`` which reads ``row["roles"]`` to
    decide whether to grant access.
    """
    data = {"id": _OWNER_ENTITY_ID, "roles": ["owner"]}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_delete_candidate_row(
    *,
    fact_id: UUID | None = None,
    object_val: str = _EMAIL,
) -> MagicMock:
    """Build a MagicMock for a candidate row in the DELETE path."""
    data = {
        "id": fact_id or _FACT_ID,
        "object": object_val,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


# ---------------------------------------------------------------------------
# App factory helpers
# ---------------------------------------------------------------------------


def _make_app(
    *,
    owner_exists: bool = True,
    entity_exists: bool = True,
    fetch_rows: list | None = None,
    fetchrow_side_effect=None,
    fetchval_side_effect=None,
    execute_return: str = "UPDATE 1",
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mocked relationship DB pool.

    The mock pool is configured with:
    - ``fetchrow``: owner entity check (returns owner row or None)
    - ``fetchval``: entity-exists check (returns 1 or None)
    - ``fetch``: contact-fact rows for GET, or candidate rows for DELETE
    - ``execute``: UPDATE for DELETE retraction (returns execute_return)

    When ``fetchrow_side_effect`` is provided it overrides the default
    fetchrow behaviour entirely (useful for POST tests where fetchrow is called
    twice: once for owner gate, once for the fact row after write).
    When ``fetchval_side_effect`` is provided it overrides the default
    fetchval behaviour (useful when POST calls fetchval for entity-exists).
    """
    if fetchrow_side_effect is not None:
        mock_fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_fetchrow = AsyncMock(return_value=_make_owner_row() if owner_exists else None)

    if fetchval_side_effect is not None:
        mock_fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        mock_fetchval = AsyncMock(return_value=1 if entity_exists else None)

    mock_pool = AsyncMock()
    mock_pool.fetchrow = mock_fetchrow
    mock_pool.fetchval = mock_fetchval
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.execute = AsyncMock(return_value=execute_return)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _get(app: FastAPI, path: str = _CONTACTS_PATH) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


async def _post(
    app: FastAPI,
    path: str = _CONTACTS_PATH,
    json_body: dict | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, json=json_body or {})


async def _delete(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.delete(path)


# ---------------------------------------------------------------------------
# Helper: AssertResult mock
# ---------------------------------------------------------------------------


def _make_assert_result(
    outcome: str = "inserted",
    fact_id: UUID | None = None,
    action_id: UUID | None = None,
):
    """Build a mock AssertResult-like object."""
    from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult

    return AssertResult(
        outcome=AssertOutcome(outcome),
        fact_id=fact_id or _FACT_ID,
        action_id=action_id,
    )


# ===========================================================================
# GET /entities/{id}/contacts
# ===========================================================================


class TestGetEntityContactsEmpty:
    """Entity with no contact facts returns empty list."""

    async def test_returns_200_with_empty_facts_list(self):
        app, _ = _make_app(fetch_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200
        body = resp.json()
        assert "facts" in body
        assert body["facts"] == []


class TestGetEntityContactsWithData:
    """Entity with contact facts returns them in the response."""

    async def test_single_email_fact_returned(self):
        rows = [_make_contact_fact_row(predicate="has-email", object_val=_EMAIL)]
        app, _ = _make_app(fetch_rows=rows)
        resp = await _get(app)

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["facts"]) == 1
        fact = body["facts"][0]
        assert fact["predicate"] == "has-email"
        assert fact["object"] == _EMAIL
        assert fact["value_hash"] == _EMAIL_HASH

    async def test_multiple_facts_for_different_predicates(self):
        rows = [
            _make_contact_fact_row(predicate="has-email", object_val="a@example.com"),
            _make_contact_fact_row(predicate="has-phone", object_val="+1-555-0100"),
        ]
        app, _ = _make_app(fetch_rows=rows)
        resp = await _get(app)

        assert resp.status_code == 200
        facts = resp.json()["facts"]
        assert len(facts) == 2
        predicates = {f["predicate"] for f in facts}
        assert predicates == {"has-email", "has-phone"}


class TestGetEntityContactsProvenance:
    """All six provenance fields must be present per spec contract."""

    async def test_all_provenance_fields_present(self):
        from datetime import UTC, datetime

        last_seen_dt = datetime(2026, 4, 30, 10, 0, 0, tzinfo=UTC)
        rows = [
            _make_contact_fact_row(
                predicate="has-email",
                src="relationship",
                conf=0.9,
                last_seen=last_seen_dt,
                weight=None,
                verified=True,
                primary=True,
            )
        ]
        app, _ = _make_app(fetch_rows=rows)
        resp = await _get(app)

        fact = resp.json()["facts"][0]
        for field in ("src", "conf", "last_seen", "weight", "verified", "primary"):
            assert field in fact, f"Provenance field {field!r} missing from response"

    async def test_nullable_provenance_fields_are_explicit_null(self):
        rows = [
            _make_contact_fact_row(
                last_seen=None,
                weight=None,
                primary=None,
            )
        ]
        app, _ = _make_app(fetch_rows=rows)
        resp = await _get(app)

        fact = resp.json()["facts"][0]
        assert fact["last_seen"] is None
        assert fact["weight"] is None
        assert fact["primary"] is None
        assert fact["conf"] == 1.0
        assert fact["verified"] is False


class TestGetEntityContactsOwnerGate:
    """Clause 12b: GET returns 403 + owner_required when no owner entity registered."""

    async def test_returns_403_when_no_owner_entity(self):
        app, _ = _make_app(owner_exists=False)
        resp = await _get(app)

        assert resp.status_code == 403
        body = resp.json()
        detail = body.get("detail", body)
        assert isinstance(detail, dict)
        assert detail.get("code") == "owner_required"

    async def test_returns_200_when_owner_entity_present(self):
        app, _ = _make_app(owner_exists=True, fetch_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200


class TestGetEntityContactsEntityNotFound:
    """Unknown entity UUID → 404."""

    async def test_returns_404_for_missing_entity(self):
        app, _ = _make_app(entity_exists=False)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/contacts")

        assert resp.status_code == 404


# ===========================================================================
# POST /entities/{id}/contacts
# ===========================================================================


class TestPostEntityContactsInsert:
    """POST creates a new contact-fact via the central writer."""

    async def test_returns_201_with_inserted_fact(self):
        fact_row = _make_contact_fact_row(predicate="has-email", object_val=_EMAIL)
        # fetchrow call sequence:
        #   1st call → owner entity check (passes)
        #   2nd call → fact row after write (the inserted fact)
        fetchrow_calls = [_make_owner_row(), fact_row]
        app, _ = _make_app(fetchrow_side_effect=fetchrow_calls)

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(return_value=_make_assert_result("inserted")),
        ):
            resp = await _post(
                app,
                json_body={"predicate": "has-email", "value": _EMAIL},
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["outcome"] == "inserted"
        assert body["fact"] is not None
        assert body["fact"]["predicate"] == "has-email"
        assert body["fact"]["object"] == _EMAIL
        assert body["action_id"] is None

    async def test_returns_201_with_unchanged_fact_on_idempotent_call(self):
        fact_row = _make_contact_fact_row(predicate="has-email", object_val=_EMAIL)
        fetchrow_calls = [_make_owner_row(), fact_row]
        app, _ = _make_app(fetchrow_side_effect=fetchrow_calls)

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(return_value=_make_assert_result("unchanged")),
        ):
            resp = await _post(
                app,
                json_body={"predicate": "has-email", "value": _EMAIL},
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["outcome"] == "unchanged"
        assert body["fact"] is not None

    async def test_value_hash_in_response_matches_object(self):
        fact_row = _make_contact_fact_row(predicate="has-email", object_val=_EMAIL)
        fetchrow_calls = [_make_owner_row(), fact_row]
        app, _ = _make_app(fetchrow_side_effect=fetchrow_calls)

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(return_value=_make_assert_result("inserted")),
        ):
            resp = await _post(
                app,
                json_body={"predicate": "has-email", "value": _EMAIL},
            )

        body = resp.json()
        assert body["fact"]["value_hash"] == _EMAIL_HASH


class TestPostEntityContactsOwnerCarveOut:
    """Owner entity subject → pending_approval; HTTP 202."""

    async def test_returns_202_with_action_id_for_owner_entity(self):
        action_id = uuid4()
        app, _ = _make_app(owner_exists=True)

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(
                return_value=_make_assert_result(
                    "pending_approval", fact_id=None, action_id=action_id
                )
            ),
        ):
            resp = await _post(
                app,
                json_body={"predicate": "has-email", "value": _EMAIL},
            )

        assert resp.status_code == 202
        body = resp.json()
        assert body["outcome"] == "pending_approval"
        assert body["fact"] is None
        assert UUID(body["action_id"]) == action_id


class TestPostEntityContactsInvalidPredicate:
    """POST with a non-has-* predicate returns 400."""

    @pytest.mark.parametrize("predicate", ["knows", "contact_note"])
    async def test_returns_400_for_non_contact_predicate(self, predicate):
        """Non-has-* predicates (and those missing the has- prefix) are rejected 400."""
        app, _ = _make_app(owner_exists=True)
        resp = await _post(
            app,
            json_body={"predicate": predicate, "value": "x"},
        )

        assert resp.status_code == 400
        detail = resp.json().get("detail", {})
        assert isinstance(detail, dict)
        assert detail.get("code") == "invalid_predicate"


class TestPostEntityContactsOwnerGate:
    """Clause 12a: POST returns 403 + owner_required when no owner entity registered."""

    async def test_returns_403_when_no_owner_entity(self):
        app, _ = _make_app(owner_exists=False)
        resp = await _post(
            app,
            json_body={"predicate": "has-email", "value": _EMAIL},
        )

        assert resp.status_code == 403
        body = resp.json()
        detail = body.get("detail", body)
        assert isinstance(detail, dict)
        assert detail.get("code") == "owner_required"


class TestPostEntityContactsEntityNotFound:
    """Unknown entity UUID → 404."""

    async def test_returns_404_for_missing_entity(self):
        app, _ = _make_app(owner_exists=True, entity_exists=False)
        resp = await _post(
            app,
            path=f"/api/relationship/entities/{_MISSING_ENT_ID}/contacts",
            json_body={"predicate": "has-email", "value": _EMAIL},
        )

        assert resp.status_code == 404


# ===========================================================================
# DELETE /entities/{id}/contacts/{pred}/{valueHash}
# ===========================================================================


class TestDeleteEntityContactHappyPath:
    """DELETE retracts the matching fact and returns 200."""

    async def test_returns_200_with_deleted_true_and_fact_id(self):
        candidate = _make_delete_candidate_row(fact_id=_FACT_ID, object_val=_EMAIL)
        app, mock_pool = _make_app(fetch_rows=[candidate])
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")

        resp = await _delete(
            app, f"/api/relationship/entities/{_ENT_ID}/contacts/has-email/{_EMAIL_HASH}"
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] is True
        assert UUID(body["fact_id"]) == _FACT_ID

    async def test_execute_called_with_retracted_validity(self):
        candidate = _make_delete_candidate_row(fact_id=_FACT_ID, object_val=_EMAIL)
        app, mock_pool = _make_app(fetch_rows=[candidate])
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")

        await _delete(app, f"/api/relationship/entities/{_ENT_ID}/contacts/has-email/{_EMAIL_HASH}")

        # Confirm the retraction SQL was executed with the correct fact_id.
        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args[0]
        sql = call_args[0]
        assert "retracted" in sql.lower()
        assert call_args[1] == _FACT_ID

    async def test_hash_collision_on_multiple_candidates_picks_matching_one(self):
        """With two facts for same predicate, only the one matching valueHash is deleted."""
        email_a = "a@example.com"
        email_b = "b@example.com"
        hash_a = hashlib.sha256(email_a.encode("utf-8")).hexdigest()[:16]
        fact_id_a = uuid4()
        fact_id_b = uuid4()

        candidate_a = _make_delete_candidate_row(fact_id=fact_id_a, object_val=email_a)
        candidate_b = _make_delete_candidate_row(fact_id=fact_id_b, object_val=email_b)
        app, mock_pool = _make_app(fetch_rows=[candidate_a, candidate_b])
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")

        resp = await _delete(
            app, f"/api/relationship/entities/{_ENT_ID}/contacts/has-email/{hash_a}"
        )

        assert resp.status_code == 200
        body = resp.json()
        assert UUID(body["fact_id"]) == fact_id_a


class TestDeleteEntityContactNotFound:
    """No active fact matching (entity_id, predicate, valueHash) → 404."""

    async def test_returns_404_when_no_candidate_rows(self):
        app, _ = _make_app(fetch_rows=[])

        resp = await _delete(
            app, f"/api/relationship/entities/{_ENT_ID}/contacts/has-email/{_EMAIL_HASH}"
        )

        assert resp.status_code == 404
        body = resp.json()
        detail = body.get("detail", {})
        assert isinstance(detail, dict)
        assert detail.get("code") == "contact_fact_not_found"

    async def test_returns_404_when_hash_does_not_match_any_candidate(self):
        # Candidates exist for the predicate, but none match the given hash.
        candidate = _make_delete_candidate_row(object_val="different@example.com")
        app, _ = _make_app(fetch_rows=[candidate])

        resp = await _delete(
            app, f"/api/relationship/entities/{_ENT_ID}/contacts/has-email/{_EMAIL_HASH}"
        )

        assert resp.status_code == 404


class TestDeleteEntityContactInvalidPredicate:
    """DELETE with a non-has-* predicate returns 400."""

    @pytest.mark.parametrize("predicate", ["knows", "contact_note"])
    async def test_returns_400_for_non_contact_predicate(self, predicate):
        """Non-has-* predicates (and those missing the has- prefix) are rejected 400."""
        app, _ = _make_app(owner_exists=True)

        resp = await _delete(
            app,
            f"/api/relationship/entities/{_ENT_ID}/contacts/{predicate}/{_EMAIL_HASH}",
        )

        assert resp.status_code == 400
        detail = resp.json().get("detail", {})
        assert isinstance(detail, dict)
        assert detail.get("code") == "invalid_predicate"


class TestDeleteEntityContactOwnerGate:
    """Clause 12a: DELETE returns 403 + owner_required when no owner entity."""

    async def test_returns_403_when_no_owner_entity(self):
        app, _ = _make_app(owner_exists=False)

        resp = await _delete(
            app, f"/api/relationship/entities/{_ENT_ID}/contacts/has-email/{_EMAIL_HASH}"
        )

        assert resp.status_code == 403
        body = resp.json()
        detail = body.get("detail", body)
        assert isinstance(detail, dict)
        assert detail.get("code") == "owner_required"


class TestDeleteEntityContactEntityNotFound:
    """Unknown entity UUID → 404 from entity existence check."""

    async def test_returns_404_for_missing_entity(self):
        app, _ = _make_app(owner_exists=True, entity_exists=False)

        resp = await _delete(
            app,
            f"/api/relationship/entities/{_MISSING_ENT_ID}/contacts/has-email/{_EMAIL_HASH}",
        )

        assert resp.status_code == 404


# ===========================================================================
# Scope filter assertion (GET must only return has-* predicates)
# ===========================================================================


class TestGetContactsScopeFilter:
    """GET only surfaces contact (has-*) predicates; relational predicates are excluded."""

    async def test_only_has_prefix_predicates_returned(self):
        # DB mock returns contact facts (the SQL WHERE predicate LIKE 'has-%' is enforced
        # at the DB layer; here we verify the endpoint passes those rows through correctly).
        rows = [
            _make_contact_fact_row(predicate="has-email", object_val=_EMAIL),
            _make_contact_fact_row(predicate="has-phone", object_val="+15550100"),
        ]
        app, _ = _make_app(fetch_rows=rows)
        resp = await _get(app)

        assert resp.status_code == 200
        facts = resp.json()["facts"]
        for fact in facts:
            assert fact["predicate"].startswith("has-"), (
                f"Non-contact predicate {fact['predicate']!r} should not appear in response"
            )


# ===========================================================================
# POST /entities/{id}/contacts/{pred}/{valueHash}/verify  (bu-e90i6)
# ===========================================================================


async def _post_verify(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path)


_VERIFY_PATH = f"/api/relationship/entities/{_ENT_ID}/contacts/has-email/{_EMAIL_HASH}/verify"


class TestVerifyEntityContactHappyPath:
    """POST /verify marks an active fact as verified and returns 200."""

    async def test_returns_200_with_verified_true_and_fact_id(self):
        candidate = _make_delete_candidate_row(fact_id=_FACT_ID, object_val=_EMAIL)
        app, mock_pool = _make_app(fetch_rows=[candidate])
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")

        resp = await _post_verify(app, _VERIFY_PATH)

        assert resp.status_code == 200
        body = resp.json()
        assert body["verified"] is True
        assert UUID(body["fact_id"]) == _FACT_ID

    async def test_execute_called_with_verified_true(self):
        candidate = _make_delete_candidate_row(fact_id=_FACT_ID, object_val=_EMAIL)
        app, mock_pool = _make_app(fetch_rows=[candidate])
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")

        await _post_verify(app, _VERIFY_PATH)

        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args[0]
        sql = call_args[0]
        assert "verified" in sql.lower()
        assert "true" in sql.lower()
        assert call_args[1] == _FACT_ID

    async def test_hash_match_picks_correct_candidate(self):
        email_a = "a@example.com"
        email_b = "b@example.com"
        hash_a = hashlib.sha256(email_a.encode("utf-8")).hexdigest()[:16]
        fact_id_a = uuid4()
        fact_id_b = uuid4()

        candidate_a = _make_delete_candidate_row(fact_id=fact_id_a, object_val=email_a)
        candidate_b = _make_delete_candidate_row(fact_id=fact_id_b, object_val=email_b)
        app, mock_pool = _make_app(fetch_rows=[candidate_a, candidate_b])
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")

        resp = await _post_verify(
            app,
            f"/api/relationship/entities/{_ENT_ID}/contacts/has-email/{hash_a}/verify",
        )

        assert resp.status_code == 200
        assert UUID(resp.json()["fact_id"]) == fact_id_a


class TestVerifyEntityContactNotFound:
    """No active fact matching (entity_id, predicate, valueHash) → 404."""

    async def test_returns_404_when_no_candidate_rows(self):
        app, _ = _make_app(fetch_rows=[])

        resp = await _post_verify(app, _VERIFY_PATH)

        assert resp.status_code == 404
        body = resp.json()
        detail = body.get("detail", {})
        assert isinstance(detail, dict)
        assert detail.get("code") == "contact_fact_not_found"

    async def test_returns_404_when_hash_does_not_match_any_candidate(self):
        candidate = _make_delete_candidate_row(object_val="different@example.com")
        app, _ = _make_app(fetch_rows=[candidate])

        resp = await _post_verify(app, _VERIFY_PATH)

        assert resp.status_code == 404


class TestVerifyEntityContactInvalidPredicate:
    """POST /verify with a non-has-* predicate returns 400."""

    async def test_returns_400_for_non_contact_predicate(self):
        app, _ = _make_app(owner_exists=True)

        resp = await _post_verify(
            app,
            f"/api/relationship/entities/{_ENT_ID}/contacts/knows/{_EMAIL_HASH}/verify",
        )

        assert resp.status_code == 400
        body = resp.json()
        detail = body.get("detail", {})
        assert isinstance(detail, dict)
        assert detail.get("code") == "invalid_predicate"


class TestVerifyEntityContactOwnerGate:
    """Clause 12a: POST /verify returns 403 when no owner entity registered."""

    async def test_returns_403_when_no_owner_entity(self):
        app, _ = _make_app(owner_exists=False)

        resp = await _post_verify(app, _VERIFY_PATH)

        assert resp.status_code == 403
        body = resp.json()
        detail = body.get("detail", body)
        assert isinstance(detail, dict)
        assert detail.get("code") == "owner_required"


class TestVerifyEntityContactEntityNotFound:
    """Unknown entity UUID → 404 from entity existence check."""

    async def test_returns_404_for_missing_entity(self):
        app, _ = _make_app(owner_exists=True, entity_exists=False)

        resp = await _post_verify(
            app,
            f"/api/relationship/entities/{_MISSING_ENT_ID}/contacts/has-email/{_EMAIL_HASH}/verify",
        )

        assert resp.status_code == 404
