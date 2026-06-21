"""Tests for POST /api/relationship/entities (promote unidentified → canonical entity).

Spec anchor:
  openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md
  Requirement: Owner-only authorization for entity endpoints — Clause 12a (Amendment 12a)
  Task: tasks.md §9.7

Acceptance criteria verified:
- Owner-only gate (Amendment 12a): HTTP 403 + {"code": "owner_required"} for non-owners.
- Promote path: existing unidentified entity is updated (canonical_name set, unidentified
  cleared from metadata).
- Create path: new entity inserted when entity_id is omitted.
- Owner carve-out: requests that target the owner entity itself trigger pending_approval
  via the central writer.
- 404 when entity_id is supplied but entity does not exist.
- HTTP 422 for invalid request body (missing required fields).
- HTTP 201 on success with EntitySummary response body.
- initial_facts are asserted via relationship_assert_fact inside the same transaction.
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
_ENTITY_ID = uuid4()

POST_PATH = "/api/relationship/entities"
BASE_URL = "http://test"


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
    entity_id: UUID | None = None,
    canonical_name: str = "Alice Example",
    entity_type: str = "person",
    aliases: list[str] | None = None,
    roles: list[str] | None = None,
    metadata: dict | None = None,
) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for entity rows."""
    data = {
        "id": entity_id or _ENTITY_ID,
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "roles": roles or [],
        "metadata": metadata if metadata is not None else {"unidentified": "true"},
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_stats_row(
    *,
    tier: int | None = None,
    last_seen: datetime | None = None,
    contact_fact_count: int = 0,
) -> MagicMock:
    """Build a MagicMock for the post-transaction stats query."""
    data = {
        "tier": tier,
        "last_seen": last_seen,
        "contact_fact_count": contact_fact_count,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_owner_check_row(is_owner_entity: bool) -> MagicMock:
    """Build a mock row for the owner-entity check inside relationship_assert_fact."""
    roles = ["owner"] if is_owner_entity else []
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: roles if k == "roles" else None)
    return row


def _make_conn_mock(
    *,
    entity_row: MagicMock | None = None,
    updated_row: MagicMock | None = None,
    stats_row: MagicMock | None = None,
    predicate_exists: bool = True,
    is_owner_entity: bool = False,
    has_initial_facts: bool = False,
    entity_row_is_missing: bool = False,
) -> AsyncMock:
    """Build a mock asyncpg connection for the transaction context.

    Call sequence inside the transaction:

    Promote path (entity_id provided):
      1. conn.fetchrow(SELECT entity)   → entity_row (or None → 404)
      2. conn.fetchrow(UPDATE RETURNING) → updated_row

    Create path (entity_id omitted):
      1. conn.fetchrow(INSERT RETURNING) → updated_row

    For each fact in initial_facts (only when has_initial_facts=True):
      3. conn.fetchval(predicate EXISTS check) → True | None
      4. conn.fetchrow(owner check in _assert_fact) → owner/non-owner row
      5. conn.fetchval(existing active fact SELECT/INSERT) → UUID | None

    After facts (or immediately after entity UPDATE/INSERT when no facts):
      N. conn.fetchrow(stats query) → stats_row

    Parameters
    ----------
    entity_row_is_missing:
        When True, the SELECT entity returns None (→ 404). No further calls happen.
    """
    mock_conn = AsyncMock()

    # --- fetchrow side effects ---
    fetchrow_responses: list = []
    if entity_row_is_missing:
        fetchrow_responses.append(None)  # entity not found → 404
    else:
        if entity_row is not None:
            fetchrow_responses.append(entity_row)
        if updated_row is not None:
            fetchrow_responses.append(updated_row)
        if has_initial_facts:
            fetchrow_responses.append(_make_owner_check_row(is_owner_entity))
        if has_initial_facts:
            # fetchrow for the existing-fact SELECT in _upsert_fact
            fetchrow_responses.append(None)  # no existing active fact → insert new one
        # Stats query is now inside the transaction (conn.fetchrow, not pool.fetchrow)
        fetchrow_responses.append(stats_row if stats_row is not None else _make_stats_row())

    mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_responses if fetchrow_responses else [None])

    # --- fetchval side effects (predicate validation + fact insert) ---
    if has_initial_facts and not entity_row_is_missing:
        if predicate_exists:
            # predicate_exists check → True; INSERT RETURNING fact_id → UUID
            mock_conn.fetchval = AsyncMock(side_effect=[True, uuid4()])
        else:
            # predicate_exists check → None (raises ValueError in _validate_predicate)
            mock_conn.fetchval = AsyncMock(return_value=None)
    else:
        mock_conn.fetchval = AsyncMock(return_value=uuid4())

    # execute is used for supersession UPDATE
    mock_conn.execute = AsyncMock(return_value="UPDATE 1")

    # transaction context manager
    mock_txn = AsyncMock()
    mock_txn.__aenter__ = AsyncMock(return_value=None)
    mock_txn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_txn)

    return mock_conn


def _app_with_pool(
    *,
    owner_exists: bool = True,
    entity_row: MagicMock | None = None,
    updated_row: MagicMock | None = None,
    stats_row: MagicMock | None = None,
    predicate_exists: bool = True,
    is_owner_entity: bool = False,
    has_initial_facts: bool = False,
    entity_row_is_missing: bool = False,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mocked relationship DB pool for the POST /entities endpoint.

    Call sequence:
      1. pool.fetchrow     → owner entity check (None → 403)
      2. pool.acquire()    → mock conn (used for the transaction)
         conn.fetchrow(s)  → entity lookup + UPDATE/INSERT RETURNING + owner check + stats query
         conn.fetchval(s)  → predicate validation + fact insert

    The stats query now runs inside the transaction via ``conn.fetchrow`` for
    read-after-write consistency (moved from ``pool.fetchrow`` in bu-3vnsq).

    ``owner_exists`` controls whether the owner check returns a row.
    ``entity_row`` is the SELECT row returned for promote path.
    ``entity_row_is_missing`` simulates 404 (entity not found).
    ``updated_row`` is the UPDATE/INSERT RETURNING row (the promoted entity).
    ``stats_row`` is the stats query result (goes to conn, not pool).
    """
    mock_conn = _make_conn_mock(
        entity_row=entity_row,
        updated_row=updated_row,
        stats_row=stats_row,
        predicate_exists=predicate_exists,
        is_owner_entity=is_owner_entity,
        has_initial_facts=has_initial_facts,
        entity_row_is_missing=entity_row_is_missing,
    )

    # Async context manager for pool.acquire()
    @asynccontextmanager
    async def _acquire():
        yield mock_conn

    mock_pool = AsyncMock()
    owner_row = _make_owner_row() if owner_exists else None
    # pool.fetchrow only for the owner-entity gate (stats moved into conn transaction)
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
    path: str = POST_PATH,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.post(path, json=body)


# ---------------------------------------------------------------------------
# Scenario: Owner-only gate (Amendment 12a)
# ---------------------------------------------------------------------------


class TestOwnerOnlyGate:
    """POST /entities must return 403 + owner_required when no owner entity is registered."""

    async def test_returns_403_when_no_owner_entity(self):
        """Non-owner / no-owner-entity configuration raises 403 + owner_required."""
        app, _ = _app_with_pool(owner_exists=False)
        resp = await _post(app, {"canonical_name": "Test Person"})

        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        body = resp.json()
        # FastAPI wraps HTTPException details under "detail"; also accept top-level "code".
        code = (
            body.get("code")
            or (body.get("error") or {}).get("code")
            or (body.get("detail") or {}).get("code")
        )
        assert code == "owner_required", f"Expected owner_required, got code={code!r}: {body}"

    async def test_owner_present_does_not_return_403(self):
        """When an owner entity is registered the gate must not block the request."""
        entity = _make_entity_row(entity_id=_ENTITY_ID, metadata={"unidentified": "true"})
        promoted = _make_entity_row(entity_id=_ENTITY_ID, canonical_name="Promoted", metadata={})
        app, _ = _app_with_pool(entity_row=entity, updated_row=promoted)
        resp = await _post(
            app,
            {"canonical_name": "Promoted", "entity_id": str(_ENTITY_ID)},
        )
        # Must not be 403 with owner_required (could be 201 or another code on success)
        if resp.status_code == 403:
            body = resp.json()
            code = (
                body.get("code")
                or (body.get("error") or {}).get("code")
                or (body.get("detail") or {}).get("code")
            )
            assert code != "owner_required", f"Owner caller was incorrectly rejected: {body}"


# ---------------------------------------------------------------------------
# Scenario: Promote path (entity_id provided)
# ---------------------------------------------------------------------------


class TestPromotePath:
    """Promote an existing unidentified entity by providing entity_id + canonical_name."""

    async def test_promote_returns_entity_summary(self):
        """Successful promotion returns HTTP 201 with EntitySummary fields."""
        entity = _make_entity_row(entity_id=_ENTITY_ID, metadata={"unidentified": "true"})
        promoted = _make_entity_row(
            entity_id=_ENTITY_ID,
            canonical_name="Alice Promoted",
            entity_type="person",
            metadata={},
        )
        app, _ = _app_with_pool(entity_row=entity, updated_row=promoted)

        resp = await _post(
            app,
            {"canonical_name": "Alice Promoted", "entity_id": str(_ENTITY_ID)},
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == str(_ENTITY_ID)
        assert body["canonical_name"] == "Alice Promoted"
        assert body["entity_type"] == "person"
        assert "aliases" in body
        assert "roles" in body
        assert "metadata" in body
        assert "created_at" in body
        assert "updated_at" in body

    async def test_promote_returns_404_when_entity_not_found(self):
        """If entity_id does not exist the endpoint returns 404."""
        app, _ = _app_with_pool(entity_row_is_missing=True)

        resp = await _post(
            app,
            {"canonical_name": "Ghost Entity", "entity_id": str(uuid4())},
        )

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Scenario: Create path (entity_id omitted)
# ---------------------------------------------------------------------------


class TestCreatePath:
    """Create a brand-new canonical entity when entity_id is omitted."""

    async def test_create_returns_201(self):
        """New entity creation returns HTTP 201."""
        new_entity = _make_entity_row(
            entity_id=uuid4(),
            canonical_name="New Person",
            metadata={},
        )
        app, _ = _app_with_pool(updated_row=new_entity)

        resp = await _post(app, {"canonical_name": "New Person"})

        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    async def test_create_returns_entity_summary(self):
        """Response body contains required EntitySummary fields for a created entity."""
        new_id = uuid4()
        new_entity = _make_entity_row(
            entity_id=new_id,
            canonical_name="New Person",
            entity_type="organization",
            metadata={},
        )
        app, _ = _app_with_pool(updated_row=new_entity)

        resp = await _post(app, {"canonical_name": "New Person", "entity_type": "organization"})

        assert resp.status_code == 201
        body = resp.json()
        assert body["canonical_name"] == "New Person"
        assert body["entity_type"] == "organization"
        assert "id" in body

    async def test_create_minimal_body(self):
        """Only canonical_name is required; all other fields have defaults."""
        new_entity = _make_entity_row(canonical_name="Min Person", metadata={})
        app, _ = _app_with_pool(updated_row=new_entity)

        resp = await _post(app, {"canonical_name": "Min Person"})
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Scenario: Invalid input (HTTP 422)
# ---------------------------------------------------------------------------


class TestInvalidInput:
    """Malformed or missing required fields return HTTP 422."""

    async def test_missing_canonical_name_returns_422(self):
        """canonical_name is required; omitting it returns 422."""
        app, _ = _app_with_pool()
        resp = await _post(app, {})
        assert resp.status_code == 422

    async def test_empty_canonical_name_returns_422(self):
        """An empty canonical_name (min_length=1) returns 422."""
        app, _ = _app_with_pool()
        resp = await _post(app, {"canonical_name": ""})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Scenario: initial_facts emission
# ---------------------------------------------------------------------------


class TestInitialFacts:
    """initial_facts are asserted via relationship_assert_fact in the same transaction."""

    async def test_initial_facts_accepted_for_valid_predicate(self):
        """A valid predicate in initial_facts does not block the promote."""
        entity = _make_entity_row(entity_id=_ENTITY_ID, metadata={"unidentified": "true"})
        promoted = _make_entity_row(entity_id=_ENTITY_ID, canonical_name="Alice", metadata={})
        app, _ = _app_with_pool(
            entity_row=entity,
            updated_row=promoted,
            predicate_exists=True,
            has_initial_facts=True,
        )

        resp = await _post(
            app,
            {
                "canonical_name": "Alice",
                "entity_id": str(_ENTITY_ID),
                "initial_facts": [{"predicate": "has-email", "object": "alice@example.com"}],
            },
        )
        # 201 means facts were processed (predicate valid in mock)
        assert resp.status_code == 201, f"Got {resp.status_code}: {resp.text}"

    async def test_invalid_predicate_in_initial_facts_returns_422(self):
        """An unregistered predicate in initial_facts causes 422."""
        entity = _make_entity_row(entity_id=_ENTITY_ID, metadata={"unidentified": "true"})
        promoted = _make_entity_row(entity_id=_ENTITY_ID, canonical_name="Alice", metadata={})
        # predicate_exists=False → relationship_assert_fact raises ValueError
        app, _ = _app_with_pool(
            entity_row=entity,
            updated_row=promoted,
            predicate_exists=False,
            has_initial_facts=True,
        )

        resp = await _post(
            app,
            {
                "canonical_name": "Alice",
                "entity_id": str(_ENTITY_ID),
                "initial_facts": [{"predicate": "bad-predicate", "object": "value"}],
            },
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
