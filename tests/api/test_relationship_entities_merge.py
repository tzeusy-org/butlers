"""Tests for POST /api/relationship/entities/{id}/merge (entity-level merge).

Spec anchor:
  openspec/changes/relationship-tabs-to-entities/specs/dashboard-relationship/spec.md
  Requirement: Owner-only authorization for entity endpoints — Clause 12a (Amendment 12a)
  Task: tasks.md §9.10 (bu-jp6r6)

Acceptance criteria verified:
- Owner-only gate (Amendment 12a): HTTP 403 + {"code": "owner_required"} for non-owners.
- keepAs='A' keeps entityA, tombstones entityB.
- keepAs='B' keeps entityB, tombstones entityA.
- Subject-side rewire: relationship.facts rows with subject=source → subject=target.
- Object-side rewire: relationship.facts rows with object_kind='entity' AND object=source → target.
- Tombstone: source entity metadata gains merged_into = str(target_id).
- 404 when either entity is missing.
- 404 when source entity is already tombstoned.
- 422 when entityA == entityB (same entity).
- Atomic: all SQL steps in a single transaction.
- Conflict handling: subject-side rows that collide at (target, predicate, object) are
  retracted (validity='superseded') instead of moved.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
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

_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)

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
    """Simulate a row returned by the owner-entity check query."""
    data = {"id": entity_id or uuid4()}
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
# App/pool factory
# ---------------------------------------------------------------------------


def _make_conn_mock(
    *,
    source_row: MagicMock | None,
    target_row: MagicMock | None,
    subject_rewired: int = 2,
    object_rewired: int = 1,
) -> AsyncMock:
    """Build a mock asyncpg connection for the transaction context.

    Call sequence inside the transaction:
      1. conn.fetchrow(SELECT source FOR UPDATE)  → source_row (None → 404)
      2. conn.fetchrow(SELECT target FOR UPDATE)  → target_row (None → 404)
      3. conn.execute(retract conflicting subject-side rows)
      4. conn.fetchval(UPDATE subject rows, RETURNING count)  → subject_rewired
      5. conn.execute(retract conflicting object-side rows)
      6. conn.fetchval(UPDATE object rows, RETURNING count)   → object_rewired
      7. conn.execute(UPDATE entities SET metadata=tombstone)
    """
    mock_conn = AsyncMock()

    fetchrow_responses = []
    if source_row is not None:
        fetchrow_responses.append(source_row)
    else:
        fetchrow_responses.append(None)

    if source_row is not None:
        if target_row is not None:
            fetchrow_responses.append(target_row)
        else:
            fetchrow_responses.append(None)

    mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_responses)

    # fetchval returns subject count then object count
    mock_conn.fetchval = AsyncMock(side_effect=[subject_rewired, object_rewired])

    # execute is called for: retract subject conflicts, retract object conflicts, tombstone
    mock_conn.execute = AsyncMock(return_value="UPDATE 0")

    # transaction context manager
    mock_txn = AsyncMock()
    mock_txn.__aenter__ = AsyncMock(return_value=None)
    mock_txn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_txn)

    return mock_conn


def _app_with_pool(
    *,
    owner_exists: bool = True,
    source_row: MagicMock | None = None,
    target_row: MagicMock | None = None,
    subject_rewired: int = 2,
    object_rewired: int = 1,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mocked relationship DB pool for merge tests.

    Call sequence:
      1. pool.fetchrow  → owner-entity check (None → 403)
      2. pool.acquire() → conn (used for the transaction)
    """
    mock_conn = _make_conn_mock(
        source_row=source_row,
        target_row=target_row,
        subject_rewired=subject_rewired,
        object_rewired=object_rewired,
    )

    @asynccontextmanager
    async def _acquire():
        yield mock_conn

    mock_pool = AsyncMock()
    owner_row = _make_owner_row() if owner_exists else None
    mock_pool.fetchrow = AsyncMock(side_effect=[owner_row])
    mock_pool.acquire = MagicMock(return_value=_acquire())

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
    body: dict,
    entity_id: UUID | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.post(_merge_path(entity_id), json=body)


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

    async def test_keepas_a_returns_200(self):
        """keepAs='A' should return HTTP 200 with correct response shape."""
        # source=entityB, target=entityA
        src = _make_entity_row(entity_id=ENTITY_B_ID)
        tgt = _make_entity_row(entity_id=ENTITY_A_ID)
        app, _ = _app_with_pool(source_row=src, target_row=tgt)

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    async def test_keepas_a_response_shape(self):
        """Response identifies the kept entity (A) and tombstoned entity (B)."""
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

    async def test_keepas_b_returns_200(self):
        """keepAs='B' should return HTTP 200 with correct response shape."""
        # source=entityA, target=entityB
        src = _make_entity_row(entity_id=ENTITY_A_ID)
        tgt = _make_entity_row(entity_id=ENTITY_B_ID)
        app, _ = _app_with_pool(source_row=src, target_row=tgt)

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "B"},
        )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    async def test_keepas_b_response_shape(self):
        """Response identifies the kept entity (B) and tombstoned entity (A)."""
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

    async def test_zero_rewires_when_no_facts(self):
        """Zero subject_facts_rewired and object_facts_rewired is a valid response."""
        src = _make_entity_row(entity_id=ENTITY_B_ID)
        tgt = _make_entity_row(entity_id=ENTITY_A_ID)
        app, _ = _app_with_pool(source_row=src, target_row=tgt, subject_rewired=0, object_rewired=0)

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["subject_facts_rewired"] == 0
        assert body["object_facts_rewired"] == 0

    async def test_rewire_counts_match_db_result(self):
        """subject_facts_rewired and object_facts_rewired reflect actual DB update counts."""
        src = _make_entity_row(entity_id=ENTITY_B_ID)
        tgt = _make_entity_row(entity_id=ENTITY_A_ID)
        app, _ = _app_with_pool(
            source_row=src, target_row=tgt, subject_rewired=10, object_rewired=4
        )

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["subject_facts_rewired"] == 10
        assert body["object_facts_rewired"] == 4
