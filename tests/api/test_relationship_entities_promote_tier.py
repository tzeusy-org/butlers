"""Tests for POST /api/relationship/entities/{id}/promote-tier.

Spec anchor:
  openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md
  Requirement: Owner-only authorization for entity endpoints — Clause 12a (Amendment 12a)
  Brief §6b Amendment 6 — tier promotion is a FACT, not a column write.
  Task: tasks.md §9.8 (bu-wmigz)

Acceptance criteria verified:
1. Success (inserted): returns HTTP 201 + outcome='inserted' + fact_id set.
2. Idempotent re-promote (unchanged): returns HTTP 201 + outcome='unchanged' + same fact_id.
3. Supersession to different tier: returns HTTP 201 + outcome='superseded' + new fact_id.
4. Invalid tier value: returns HTTP 422 + code='invalid_tier'.
5. Missing entity: returns HTTP 404.
6. Non-owner caller: returns HTTP 403 + code='owner_required'.
7. Owner-entity carve-out: returns HTTP 202 + outcome='pending_approval' + action_id set.
8. No column write to public.entities.tier (verified by absence of entity UPDATE calls).

The central writer (relationship_assert_fact) is patched so tests remain unit-level
with no real Postgres or Docker dependency.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.tools.relationship.relationship_assert_fact import AssertOutcome

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ENT_ID = uuid4()
_FACT_ID = uuid4()
_ACTION_ID = uuid4()

BASE_URL = "http://test"

_WRITER_PATCH_TARGET = (
    "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact"
)


def _promote_path(entity_id: UUID | None = None) -> str:
    eid = entity_id or _ENT_ID
    return f"/api/relationship/entities/{eid}/promote-tier"


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_owner_row(roles: list[str] | None = None) -> MagicMock:
    """Simulate a row returned by the owner-entity roles query.

    Includes both ``id`` and ``roles`` keys because ``_get_owner_roles``
    inspects ``row['roles']`` to verify the caller has the 'owner' role.
    """
    data: dict = {
        "id": uuid4(),
        "roles": roles if roles is not None else ["owner"],
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


# ---------------------------------------------------------------------------
# AssertResult fake
# ---------------------------------------------------------------------------


class _FakeAssertResult:
    """Mimics AssertResult returned by relationship_assert_fact."""

    def __init__(self, outcome: str, fact_id: UUID | None = None, action_id: UUID | None = None):
        self.outcome = AssertOutcome(outcome)
        self.fact_id = fact_id
        self.action_id = action_id


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app(
    *,
    owner_exists: bool = True,
    entity_exists: bool = True,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mocked pool.

    Call sequence inside the endpoint:
      1. pool.fetchrow   — owner roles check  (None → 403)
      2. pool.fetchval   — entity existence check (None → 404)
      3. relationship_assert_fact() — central writer (patched per-test)
    """
    owner_row = _make_owner_row() if owner_exists else None

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=owner_row)
    mock_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _post(
    app: FastAPI,
    body: dict,
    entity_id: UUID | None = None,
    assert_result: _FakeAssertResult | None = None,
) -> httpx.Response:
    """POST to promote-tier with an optionally patched central writer.

    ``patch()`` is a synchronous context manager; we use a plain ``with`` block
    wrapping the async httpx call rather than ``async with``.
    """

    async def _mock_writer(*_args, **_kwargs):
        return assert_result

    if assert_result is not None:
        with patch(_WRITER_PATCH_TARGET, side_effect=_mock_writer):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=BASE_URL
            ) as client:
                return await client.post(_promote_path(entity_id), json=body)
    else:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE_URL
        ) as client:
            return await client.post(_promote_path(entity_id), json=body)


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
# Scenario: Owner-only gate (Amendment 12a)
# ---------------------------------------------------------------------------


class TestOwnerOnlyGate:
    """POST /entities/{id}/promote-tier returns 403 when no owner entity exists."""

    async def test_post_entity_promote_tier_non_owner_403(self):
        """Non-owner / no-owner-entity configuration raises 403 + owner_required."""
        app, _ = _make_app(owner_exists=False)
        resp = await _post(app, {"tier": 15})
        _assert_owner_required(resp)


# ---------------------------------------------------------------------------
# Scenario: Tier value validation
# ---------------------------------------------------------------------------


class TestTierValidation:
    """Invalid tier values return HTTP 422 before any DB write."""

    @pytest.mark.parametrize("bad_tier", [0, 1, 3, 10, 25, 100, 1000, 9999, -1])
    async def test_invalid_tier_returns_422(self, bad_tier: int):
        """Non-canonical tier values return 422 + code='invalid_tier'."""
        app, mock_pool = _make_app()
        result = _FakeAssertResult("inserted", _FACT_ID)
        resp = await _post(app, {"tier": bad_tier}, assert_result=result)
        assert resp.status_code == 422, f"Expected 422 for tier={bad_tier}: {resp.text}"
        body = resp.json()
        code = (
            body.get("code")
            or (body.get("detail") or {}).get("code")
            or (body.get("error") or {}).get("code")
        )
        assert code == "invalid_tier", f"Expected invalid_tier code, got {code!r}: {body}"

    @pytest.mark.parametrize("good_tier", [5, 15, 50, 150, 500, 1500])
    async def test_valid_tier_passes_validation(self, good_tier: int):
        """All six canonical tier values pass validation (not rejected with 422)."""
        app, _ = _make_app()
        result = _FakeAssertResult("inserted", _FACT_ID)
        resp = await _post(app, {"tier": good_tier}, assert_result=result)
        assert resp.status_code != 422, f"Valid tier={good_tier} should not return 422: {resp.text}"


# ---------------------------------------------------------------------------
# Scenario: Entity not found
# ---------------------------------------------------------------------------


class TestEntityNotFound:
    """Returns 404 when entity UUID does not exist."""

    async def test_missing_entity_returns_404(self):
        """Unknown entity UUID returns 404."""
        app, _ = _make_app(entity_exists=False)
        missing_id = uuid4()
        result = _FakeAssertResult("inserted", _FACT_ID)
        resp = await _post(app, {"tier": 15}, entity_id=missing_id, assert_result=result)
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Scenario: Successful promote (insert / supersede / unchanged)
# ---------------------------------------------------------------------------


class TestPromoteSuccess:
    """Happy-path promotion writes dunbar_tier_override via central writer."""

    async def test_promote_returns_201_and_fact_id(self):
        """Successful new promote returns HTTP 201 + entity_id + tier + fact_id."""
        fact_id = uuid4()
        app, _ = _make_app()
        result = _FakeAssertResult("inserted", fact_id)
        resp = await _post(app, {"tier": 15}, assert_result=result)
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["entity_id"] == str(_ENT_ID), f"entity_id mismatch: {body}"
        assert body["tier"] == 15, f"tier mismatch: {body}"
        assert body["outcome"] == "inserted", f"outcome mismatch: {body}"
        assert body["fact_id"] == str(fact_id), f"fact_id mismatch: {body}"
        assert body.get("action_id") is None, f"action_id should be null: {body}"

    async def test_promote_idempotent_returns_unchanged(self):
        """Re-promoting to same tier returns HTTP 201 + outcome='unchanged'."""
        fact_id = uuid4()
        app, _ = _make_app()
        result = _FakeAssertResult("unchanged", fact_id)
        resp = await _post(app, {"tier": 50}, assert_result=result)
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["outcome"] == "unchanged", f"outcome mismatch: {body}"
        assert body["fact_id"] == str(fact_id), f"fact_id should be set on unchanged: {body}"

    async def test_promote_to_different_tier_returns_superseded(self):
        """Promoting to a different tier returns HTTP 201 + outcome='superseded'."""
        new_fact_id = uuid4()
        app, _ = _make_app()
        result = _FakeAssertResult("superseded", new_fact_id)
        resp = await _post(app, {"tier": 150}, assert_result=result)
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["outcome"] == "superseded", f"outcome mismatch: {body}"
        assert body["fact_id"] == str(new_fact_id), f"fact_id mismatch: {body}"

    async def test_promote_does_not_write_to_entities_tier_column(self):
        """Endpoint never issues an UPDATE to public.entities (Amendment 6).

        The endpoint only calls fetchrow (owner check), fetchval (entity exists),
        and the patched central writer.  pool.execute must remain uncalled.
        """
        app, mock_pool = _make_app()
        result = _FakeAssertResult("inserted", _FACT_ID)
        await _post(app, {"tier": 15}, assert_result=result)
        assert not mock_pool.execute.called, (
            "pool.execute was called — implies direct column write (Amendment 6 violation)"
        )


# ---------------------------------------------------------------------------
# Scenario: Owner-entity carve-out (RFC 0017 §2.3)
# ---------------------------------------------------------------------------


class TestOwnerEntityCarveOut:
    """When entity is the owner, writer returns pending_approval → HTTP 202."""

    async def test_owner_entity_promote_returns_202_and_action_id(self):
        """Owner-entity carve-out: endpoint returns HTTP 202 + action_id."""
        action_id = uuid4()
        app, _ = _make_app()
        result = _FakeAssertResult("pending_approval", fact_id=None, action_id=action_id)
        resp = await _post(app, {"tier": 5}, assert_result=result)
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["outcome"] == "pending_approval", f"outcome mismatch: {body}"
        assert body["action_id"] == str(action_id), f"action_id mismatch: {body}"
        assert body.get("fact_id") is None, f"fact_id should be null on pending_approval: {body}"
