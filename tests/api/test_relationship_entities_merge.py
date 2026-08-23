"""Tests for POST /api/relationship/entities/{id}/merge (entity-level merge).

Spec anchor:
  openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md
  Requirement: Owner-only authorization for entity endpoints — Clause 12a (Amendment 12a)
  Task: tasks.md §9.10 (bu-jp6r6)

Acceptance criteria verified at the HTTP boundary:
- Owner-only gate (Amendment 12a): HTTP 403 + {"code": "owner_required"} for non-owners.
- keepAs='A' keeps entityA, tombstones entityB.
- keepAs='B' keeps entityB, tombstones entityA.
- 404 when either entity is missing.
- 404 when source entity is already tombstoned.
- 422 when entityA == entityB (same entity).

SQL locking, rewiring, conflict handling, tombstoning, and audit atomicity belong
to ``test_entity_merge_service.py``. These tests mock that service so they only
assert authorization, request translation, domain-error translation, and response
conversion.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.tools.relationship.entity_merge import (
    EntityMergeResult,
    SourceEntityNotFoundError,
    SourceEntityTombstonedError,
    TargetEntityNotFoundError,
    TargetEntityTombstonedError,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENTITY_A_ID = uuid4()
ENTITY_B_ID = uuid4()

BASE_URL = "http://test"


def _merge_path(entity_id: UUID | None = None) -> str:
    eid = entity_id or ENTITY_A_ID
    return f"/api/relationship/entities/{eid}/merge"


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_owner_row(entity_id: UUID | None = None) -> MagicMock:
    """Simulate a row returned by the owner-entity check query.

    Must include ``roles`` so that ``_get_owner_roles`` can inspect it.
    The endpoint uses ``_get_owner_roles`` which reads ``row["roles"]`` to
    decide whether to grant access.
    """
    data = {"id": entity_id or uuid4(), "roles": ["owner"]}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_entity_row(
    *,
    entity_id: UUID,
    metadata: dict | None = None,
) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for entity rows."""
    data = {
        "id": entity_id,
        "metadata": metadata if metadata is not None else {},
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


# ---------------------------------------------------------------------------
# App/service factory
# ---------------------------------------------------------------------------


def _app_with_pool(
    *,
    owner_exists: bool = True,
    source_row: MagicMock | None = None,
    target_row: MagicMock | None = None,
    subject_rewired: int = 2,
    object_rewired: int = 1,
) -> tuple[FastAPI, AsyncMock]:
    """Wire the owner gate to a pool and replace the audited merge service."""
    mock_pool = AsyncMock()
    owner_row = _make_owner_row() if owner_exists else None
    mock_pool.fetchrow = AsyncMock(return_value=owner_row)

    async def _merge_service(
        pool,
        *,
        source_entity_id: UUID,
        target_entity_id: UUID,
        locked_guard=None,
        _audit_entity_order=None,
    ) -> EntityMergeResult:
        assert pool is mock_pool
        assert locked_guard is None
        if source_row is None:
            raise SourceEntityNotFoundError
        if "merged_into" in source_row["metadata"]:
            raise SourceEntityTombstonedError
        if target_row is None:
            raise TargetEntityNotFoundError
        if "merged_into" in target_row["metadata"]:
            raise TargetEntityTombstonedError
        assert _audit_entity_order == (ENTITY_A_ID, ENTITY_B_ID)
        assert source_entity_id == source_row["id"]
        assert target_entity_id == target_row["id"]
        return EntityMergeResult(
            kept_entity_id=target_entity_id,
            tombstoned_entity_id=source_entity_id,
            subject_facts_rewired=subject_rewired,
            object_facts_rewired=object_rewired,
            review_id=uuid4(),
        )

    merge_service = AsyncMock(side_effect=_merge_service)
    mock_pool.merge_service = merge_service

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    patcher = pytest.MonkeyPatch()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            patcher.setattr(router_module, "merge_entity_pair", merge_service)
            break
    app.state.merge_service_patcher = patcher

    return app, mock_pool


async def _post(
    app: FastAPI,
    body: dict,
    entity_id: UUID | None = None,
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE_URL
        ) as client:
            return await client.post(_merge_path(entity_id), json=body)
    finally:
        app.state.merge_service_patcher.undo()


# ---------------------------------------------------------------------------
# Scenario: Owner-only gate (Amendment 12a) — test_post_entity_merge_non_owner_403
# ---------------------------------------------------------------------------


class TestOwnerOnlyGate:
    """POST /entities/{id}/merge must return 403 + owner_required when no owner entity exists."""

    async def test_post_entity_merge_non_owner_403(self):
        """Non-owner / no-owner-entity configuration raises 403 + owner_required."""
        app, _ = _app_with_pool(owner_exists=False)
        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )

        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        body = resp.json()
        code = (
            body.get("code")
            or (body.get("error") or {}).get("code")
            or (body.get("detail") or {}).get("code")
        )
        assert code == "owner_required", f"Expected owner_required, got {code!r}: {body}"

    async def test_owner_present_is_not_rejected(self):
        """When an owner entity is registered, the gate must not block the request."""
        src = _make_entity_row(entity_id=ENTITY_B_ID)
        tgt = _make_entity_row(entity_id=ENTITY_A_ID)
        app, _ = _app_with_pool(source_row=src, target_row=tgt)
        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )
        # Must not be 403/owner_required
        if resp.status_code == 403:
            body = resp.json()
            code = (
                body.get("code")
                or (body.get("error") or {}).get("code")
                or (body.get("detail") or {}).get("code")
            )
            assert code != "owner_required", f"Owner caller was incorrectly rejected: {body}"


# ---------------------------------------------------------------------------
# Scenario: keepAs='A' — entityA survives, entityB is tombstoned
# ---------------------------------------------------------------------------


class TestKeepAsA:
    """keepAs='A' keeps entityA and tombstones entityB."""

    async def test_keepas_a_response_shape(self):
        """keepAs='A' returns 200 and identifies the kept entity (A) and tombstoned entity (B)."""
        src = _make_entity_row(entity_id=ENTITY_B_ID)
        tgt = _make_entity_row(entity_id=ENTITY_A_ID)
        app, _ = _app_with_pool(source_row=src, target_row=tgt, subject_rewired=3, object_rewired=1)

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["kept_entity_id"] == str(ENTITY_A_ID)
        assert body["tombstoned_entity_id"] == str(ENTITY_B_ID)
        assert body["subject_facts_rewired"] == 3
        assert body["object_facts_rewired"] == 1


# ---------------------------------------------------------------------------
# Scenario: keepAs='B' — entityB survives, entityA is tombstoned
# ---------------------------------------------------------------------------


class TestKeepAsB:
    """keepAs='B' keeps entityB and tombstones entityA."""

    async def test_keepas_b_response_shape(self):
        """keepAs='B' returns 200 and identifies the kept entity (B) and tombstoned entity (A)."""
        src = _make_entity_row(entity_id=ENTITY_A_ID)
        tgt = _make_entity_row(entity_id=ENTITY_B_ID)
        app, _ = _app_with_pool(source_row=src, target_row=tgt, subject_rewired=5, object_rewired=2)

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "B"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["kept_entity_id"] == str(ENTITY_B_ID)
        assert body["tombstoned_entity_id"] == str(ENTITY_A_ID)
        assert body["subject_facts_rewired"] == 5
        assert body["object_facts_rewired"] == 2


# ---------------------------------------------------------------------------
# Scenario: 404 cases
# ---------------------------------------------------------------------------


class TestNotFound:
    """Requests targeting missing or already-tombstoned entities return 404."""

    async def test_source_entity_not_found_returns_404(self):
        """HTTP 404 when source entity does not exist."""
        # source_row=None → 404 on first fetchrow
        app, _ = _app_with_pool(source_row=None, target_row=_make_entity_row(entity_id=ENTITY_A_ID))

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        assert resp.json() == {"detail": "Entity not found"}

    async def test_target_entity_not_found_returns_404(self):
        """HTTP 404 when target entity does not exist."""
        # source_row present but target_row=None → 404 on second fetchrow
        src = _make_entity_row(entity_id=ENTITY_B_ID)
        app, _ = _app_with_pool(source_row=src, target_row=None)

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        assert resp.json() == {"detail": "Entity not found"}

    async def test_source_already_tombstoned_returns_404(self):
        """HTTP 404 when source entity already has merged_into in metadata."""
        src = _make_entity_row(entity_id=ENTITY_B_ID, metadata={"merged_into": str(ENTITY_A_ID)})
        tgt = _make_entity_row(entity_id=ENTITY_A_ID)
        app, _ = _app_with_pool(source_row=src, target_row=tgt)

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        assert resp.json() == {"detail": "Entity not found"}

    async def test_target_already_tombstoned_returns_404(self):
        """HTTP 404 when target entity already has merged_into in metadata."""
        src = _make_entity_row(entity_id=ENTITY_B_ID)
        tgt = _make_entity_row(entity_id=ENTITY_A_ID, metadata={"merged_into": str(uuid4())})
        app, _ = _app_with_pool(source_row=src, target_row=tgt)

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        assert resp.json() == {"detail": "Entity not found"}


# ---------------------------------------------------------------------------
# Scenario: 422 — same entity
# ---------------------------------------------------------------------------


class TestSameEntity:
    """entityA == entityB must return 422."""

    async def test_same_entity_returns_422(self):
        """Merging an entity into itself returns HTTP 422."""
        same_id = uuid4()
        app, _ = _app_with_pool()

        resp = await _post(
            app,
            {"entityA": str(same_id), "entityB": str(same_id), "keepAs": "A"},
            entity_id=same_id,
        )

        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Scenario: Invalid input
# ---------------------------------------------------------------------------


class TestInvalidInput:
    """Missing or malformed fields return HTTP 422."""

    async def test_missing_entity_a_returns_422(self):
        """entityA is required; omitting it returns 422."""
        app, _ = _app_with_pool()
        resp = await _post(app, {"entityB": str(ENTITY_B_ID), "keepAs": "A"})
        assert resp.status_code == 422

    async def test_missing_keep_as_returns_422(self):
        """keepAs is required; omitting it returns 422."""
        app, _ = _app_with_pool()
        resp = await _post(app, {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID)})
        assert resp.status_code == 422

    async def test_invalid_keep_as_value_returns_422(self):
        """keepAs must be 'A' or 'B'; any other value returns 422."""
        app, _ = _app_with_pool()
        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "C"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Scenario: Response includes fact rewire counts
# ---------------------------------------------------------------------------


class TestRewireCounts:
    """The response accurately reports subject and object rewire counts."""

    @pytest.mark.parametrize("subject,obj", [(0, 0), (10, 4)])
    async def test_rewire_counts_match_db_result(self, subject, obj):
        """Response rewire counts reflect actual DB update counts (incl. the zero case)."""
        src = _make_entity_row(entity_id=ENTITY_B_ID)
        tgt = _make_entity_row(entity_id=ENTITY_A_ID)
        app, _ = _app_with_pool(
            source_row=src, target_row=tgt, subject_rewired=subject, object_rewired=obj
        )

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["subject_facts_rewired"] == subject
        assert body["object_facts_rewired"] == obj


class TestAuditedServiceDelegation:
    """The HTTP handler delegates SQL behavior to the audited merge service."""

    async def test_handler_passes_selected_source_and_target_to_service(self):
        src = _make_entity_row(entity_id=ENTITY_B_ID)
        tgt = _make_entity_row(entity_id=ENTITY_A_ID)
        app, mock_pool = _app_with_pool(source_row=src, target_row=tgt)

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )

        assert resp.status_code == 200
        mock_pool.merge_service.assert_awaited_once_with(
            mock_pool,
            source_entity_id=ENTITY_B_ID,
            target_entity_id=ENTITY_A_ID,
            _audit_entity_order=(ENTITY_A_ID, ENTITY_B_ID),
        )
        mock_pool.acquire.assert_not_called()
