"""Tests for POST /api/relationship/entities/queue/dismiss.

Covers spec scenarios from
``openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md``
§ "Requirement: Entity curation queue" (dismiss action) and Amendment 12a
(owner-only write gate).

Each test hits the FastAPI router via httpx.AsyncClient with a mocked DB pool
so no real Postgres or Docker is required.  Tests are marked ``unit``.

Acceptance criteria verified:
1. Single dismiss: writes queue.dismissed triple via relationship_assert_fact().
2. Idempotent re-dismiss: outcome='unchanged' returns 200 OK.
3. 404 when entity does not exist.
4. 403 (owner_required) when no owner entity is registered (Amendment 12a).
5. Owner entity carve-out: pending_approval outcome → HTTP 202 with action_id.
6. relationship_assert_fact() called with correct subject/predicate/object/src.
"""

from __future__ import annotations

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

DISMISS_PATH = "/api/relationship/entities/queue/dismiss"
BASE_URL = "http://test"


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_owner_row(roles: list[str] | None = None) -> MagicMock:
    """Simulate a row returned by the owner-entity roles query.

    ``_get_owner_roles`` accesses ``row['roles']`` to check for the 'owner'
    role, so the mock must expose both ``id`` and ``roles``.
    """
    data: dict = {
        "id": uuid4(),
        "roles": roles if roles is not None else ["owner"],
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_entity_row(entity_id: UUID | None = None) -> MagicMock:
    """Simulate a row returned by the entity existence check."""
    data = {"id": entity_id or _ENT_ID}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


# ---------------------------------------------------------------------------
# AssertResult mock factory
# ---------------------------------------------------------------------------


def _make_assert_result(
    outcome: str = "inserted",
    fact_id: UUID | None = None,
    action_id: UUID | None = None,
):
    """Build a real AssertResult with the given outcome."""
    from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult

    return AssertResult(
        outcome=AssertOutcome(outcome),
        fact_id=fact_id
        if fact_id is not None
        else (_FACT_ID if outcome != "pending_approval" else None),
        action_id=action_id,
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_dismiss_app(
    *,
    owner_exists: bool = True,
    entity_exists: bool = True,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app for POST /entities/queue/dismiss tests.

    Call sequence inside the endpoint:
      1. pool.fetchrow  — owner roles check (returns owner row or None)
      2. pool.fetchrow  — entity existence check (returns entity row or None)

    The dismiss write goes through relationship_assert_fact() which is patched
    per-test via unittest.mock.patch to avoid real DB calls.

    ``owner_exists=False`` → first fetchrow returns None → 403.
    ``entity_exists=False`` → second fetchrow returns None → 404.
    """
    owner_row = _make_owner_row() if owner_exists else None
    entity_row = _make_entity_row() if entity_exists else None

    fetchrow_side_effects: list = [owner_row, entity_row]

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effects)
    mock_pool.execute = AsyncMock(return_value="UPDATE 0")

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _post(
    app: FastAPI,
    json_body: dict | None = None,
) -> httpx.Response:
    if json_body is None:
        json_body = {"entity_id": str(_ENT_ID)}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.post(DISMISS_PATH, json=json_body)


def _assert_owner_required(resp: httpx.Response) -> None:
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
    body = resp.json()
    code = (
        body.get("code")
        or (body.get("error") or {}).get("code")
        or (body.get("detail") or {}).get("code")
    )
    assert code == "owner_required", f"Expected owner_required, got code={code!r}: {body}"


# ---------------------------------------------------------------------------
# Tests: successful dismiss
# ---------------------------------------------------------------------------


class TestDismissQueueEntitySuccess:
    """POST /entities/queue/dismiss happy-path scenarios."""

    async def test_returns_200_idempotent_redismiss(self):
        """Re-dismissing an already-dismissed entity returns outcome='unchanged'."""
        app, _ = _make_dismiss_app()

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(return_value=_make_assert_result("unchanged")),
        ):
            resp = await _post(app)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        item = body["dismissed"][0]
        assert item["outcome"] == "unchanged"

    async def test_assert_fact_called_with_correct_args(self):
        """relationship_assert_fact() is called with the expected triple shape."""
        app, _ = _make_dismiss_app()

        mock_assert = AsyncMock(return_value=_make_assert_result("inserted"))
        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=mock_assert,
        ):
            await _post(app)

        mock_assert.assert_called_once()
        call_kwargs = mock_assert.call_args

        # The router calls: relationship_assert_fact(pool, subject=..., predicate=..., ...)
        # Pool is positional (args[0]); everything else is keyword.
        kwargs = call_kwargs.kwargs

        subject = kwargs.get("subject")
        predicate = kwargs.get("predicate")
        object_val = kwargs.get("object")
        src = kwargs.get("src")
        object_kind = kwargs.get("object_kind")

        assert subject == _ENT_ID, f"Expected subject={_ENT_ID}, got {subject!r}"

        assert predicate == "queue.dismissed", f"Expected 'queue.dismissed', got {predicate!r}"
        assert object_val == "dismissed", f"Expected 'dismissed', got {object_val!r}"
        assert src == "relationship", f"Expected src='relationship', got {src!r}"
        assert object_kind == "literal", f"Expected object_kind='literal', got {object_kind!r}"


# ---------------------------------------------------------------------------
# Tests: 404 (entity not found)
# ---------------------------------------------------------------------------


class TestDismissQueueEntityNotFound:
    """POST /entities/queue/dismiss returns 404 when entity is missing."""

    async def test_returns_404_for_missing_entity(self):
        """Endpoint returns 404 when entity_id does not exist in public.entities."""
        app, _ = _make_dismiss_app(entity_exists=False)

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(),
        ):
            resp = await _post(app)

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Tests: 403 owner_required (Amendment 12a)
# ---------------------------------------------------------------------------


class TestDismissQueueOwnerGate:
    """POST /entities/queue/dismiss enforces owner-only gate (Amendment 12a)."""

    async def test_returns_403_when_no_owner_entity(self):
        """Returns 403 + owner_required when no owner entity is registered."""
        app, _ = _make_dismiss_app(owner_exists=False)

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(),
        ):
            resp = await _post(app)

        _assert_owner_required(resp)

    async def test_assert_fact_not_called_when_owner_gate_fails(self):
        """relationship_assert_fact() MUST NOT be called when the 403 fires."""
        app, _ = _make_dismiss_app(owner_exists=False)

        mock_assert = AsyncMock()
        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=mock_assert,
        ):
            await _post(app)

        mock_assert.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: owner entity carve-out → HTTP 202
# ---------------------------------------------------------------------------


class TestDismissQueueOwnerCarveOut:
    """When the subject entity has role='owner', write is parked for approval."""

    async def test_returns_202_with_action_id_for_owner_entity_carve_out(self):
        """Owner-entity subjects trigger the carve-out: HTTP 202 + action_id."""
        action_id = uuid4()
        app, _ = _make_dismiss_app()

        with patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            new=AsyncMock(
                return_value=_make_assert_result(
                    "pending_approval", fact_id=None, action_id=action_id
                )
            ),
        ):
            resp = await _post(app)

        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["status"] == "pending_approval"
        assert len(body["dismissed"]) == 1
        item = body["dismissed"][0]
        assert item["outcome"] == "pending_approval"
        assert item["fact_id"] is None
        assert UUID(item["action_id"]) == action_id
