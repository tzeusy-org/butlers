"""Integration tests for the /api/relationship/entities/* API surface.

Spec anchor:
  openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/tasks.md §9.13 + §12.8
  openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md

Endpoints under test (§9.1–§9.12):
  9.1   GET  /entities                         — list + filter + pagination
  9.2   GET  /entities/{id}                    — entity detail
  9.3   POST /entities                         — create / promote entity
  9.4   GET  /entities/{id}/contacts           — contact-fact read surface
        POST /entities/{id}/contacts           — add contact fact
        DELETE /entities/{id}/contacts/{p}/{h} — retract contact fact
  9.5   GET  /entities/queue                   — curation queue
  9.6   GET  /entities/search                  — deterministic finder
  9.7   POST /entities/{id}/promote-tier       — tier promotion
  9.8   POST /entities/{id}/archive            — soft archive
        DELETE /entities/{id}                  — tombstone / forget
  9.9   POST /entities/{id}/merge              — entity merge
  9.10  POST /entities/queue/dismiss           — dismiss queue entry
  9.11  GET  /entities/concentration           — weight aggregation
  9.12  GET  /entities/{id}/activity           — cross-butler activity aggregator

Coverage per endpoint:
  • Happy path (200/201/204)
  • Owner-only authz gate (§12.8 Amendment 12a/12b): HTTP 403 + {"code":"owner_required"}
  • ≥1 error path (404 unknown entity, 400/422 malformed params, etc.)

Test conventions:
  • httpx.AsyncClient + mocked asyncpg pool — no real Postgres or Docker required
  • pytestmark = pytest.mark.unit (not the Docker-guarded roster integration mark)
  • SQL assertions reference relationship.entity_facts, NOT relationship.facts
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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

BASE_URL = "http://test"
_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
_OLDER = _NOW - timedelta(days=10)

_ENT_ID = uuid4()
_ENT_ID_B = uuid4()
_OWNER_ENTITY_ID = uuid4()
_MISSING_ENT_ID = uuid4()

_EMAIL = "alice@example.com"
_EMAIL_HASH = hashlib.sha256(_EMAIL.encode("utf-8")).hexdigest()[:16]

_LIST_PATH = "/api/relationship/entities"
_ENTITY_PATH = f"/api/relationship/entities/{_ENT_ID}"
_ARCHIVE_PATH = f"/api/relationship/entities/{_ENT_ID}/archive"
_MERGE_PATH = f"/api/relationship/entities/{_ENT_ID}/merge"
_PROMOTE_TIER_PATH = f"/api/relationship/entities/{_ENT_ID}/promote-tier"
_CONTACTS_PATH = f"/api/relationship/entities/{_ENT_ID}/contacts"
_QUEUE_PATH = "/api/relationship/entities/queue"
_SEARCH_PATH = "/api/relationship/entities/search"
_CONCENTRATION_PATH = "/api/relationship/entities/concentration"
_DISMISS_PATH = "/api/relationship/entities/queue/dismiss"
_ACTIVITY_PATH = f"/api/relationship/entities/{_ENT_ID}/activity"
_NEIGHBOURS_PATH = f"/api/relationship/entities/{_ENT_ID}/neighbours"


# ---------------------------------------------------------------------------
# Shared row helpers
# ---------------------------------------------------------------------------


def _make_owner_row(entity_id: UUID | None = None) -> MagicMock:
    """Row returned by owner-entity presence check (fetchrow)."""
    data: dict = {"id": entity_id or _OWNER_ENTITY_ID, "roles": ["owner"]}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _make_entity_row(
    *,
    entity_id: UUID | None = None,
    canonical_name: str = "Alice Example",
    entity_type: str = "person",
    aliases: list[str] | None = None,
    roles: list[str] | None = None,
    metadata: dict | None = None,
    tier: int | None = None,
    last_seen: datetime | None = None,
    contact_fact_count: int = 0,
) -> MagicMock:
    """Generic entity row mock (used across multiple endpoints)."""
    data = {
        "id": entity_id or _ENT_ID,
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "roles": roles or [],
        "metadata": metadata if metadata is not None else {},
        "tier": tier,
        "last_seen": last_seen,
        "contact_fact_count": contact_fact_count,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _make_entity_info_row(
    *,
    info_id: UUID | None = None,
    type: str = "email",
    value: str = "alice@example.com",
    label: str | None = None,
    is_primary: bool = True,
    secured: bool = False,
) -> MagicMock:
    data = {
        "id": info_id or uuid4(),
        "type": type,
        "value": value,
        "label": label,
        "is_primary": is_primary,
        "secured": secured,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _make_classify_row(
    *,
    is_unidentified: bool = False,
    is_dup_flagged: bool = False,
    has_fresh_fact: bool = True,
    last_seen: datetime | None = None,
    dup_predicate: str | None = None,
    dup_shared_value: str | None = None,
    dup_peer_entity_ids: list | None = None,
) -> MagicMock:
    """Row returned by _classify_entity_state's fetchrow call."""
    data = {
        "is_unidentified": is_unidentified,
        "is_dup_flagged": is_dup_flagged,
        "has_fresh_fact": has_fresh_fact,
        "last_seen": last_seen,
        "dup_predicate": dup_predicate,
        "dup_shared_value": dup_shared_value,
        "dup_peer_entity_ids": dup_peer_entity_ids,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


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


async def _get(app: FastAPI, path: str, **params) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(path, params=params or None)


async def _post(app: FastAPI, path: str, body: dict | None = None) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.post(path, json=body or {})


async def _delete(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.delete(path)


def _assert_owner_required(resp: httpx.Response) -> None:
    """Assert HTTP 403 with code='owner_required' in the response body."""
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
    body = resp.json()
    code = (
        body.get("code")
        or (body.get("error") or {}).get("code")
        or (body.get("detail") or {}).get("code")
    )
    assert code == "owner_required", f"Expected code='owner_required', got {code!r}: {body}"


# ===========================================================================
# §9.1 GET /entities — list + filter + pagination
# ===========================================================================


class TestListEntities:
    """GET /entities — §9.1."""

    def _make_app(
        self,
        *,
        total: int = 0,
        fetch_rows: list | None = None,
    ) -> tuple[FastAPI, AsyncMock]:
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=total)
        mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_returns_200_with_items(self):
        """GET /entities returns 200 with items and pagination fields."""
        rows = [_make_entity_row(canonical_name="Alice"), _make_entity_row(canonical_name="Bob")]
        app, _ = self._make_app(total=2, fetch_rows=rows)
        resp = await _get(app, _LIST_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2
        assert "limit" in body
        assert "offset" in body

    async def test_empty_list_returns_200(self):
        """GET /entities with no results returns 200 and empty items."""
        app, _ = self._make_app(total=0, fetch_rows=[])
        resp = await _get(app, _LIST_PATH)
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    async def test_limit_above_200_returns_422(self):
        """limit > 200 is rejected with 422 (FastAPI validation)."""
        app, _ = self._make_app()
        resp = await _get(app, _LIST_PATH, limit=201)
        assert resp.status_code == 422

    async def test_unknown_state_returns_400(self):
        """Unknown state= filter value returns 400."""
        app, _ = self._make_app()
        resp = await _get(app, _LIST_PATH, state="nonexistent")
        assert resp.status_code == 400

    async def test_entity_type_filter_passes_to_query(self):
        """Single entity_type= filter is forwarded to the DB query."""
        app, pool = self._make_app(total=0, fetch_rows=[])
        resp = await _get(app, _LIST_PATH, entity_type="organization")
        assert resp.status_code == 200
        fetch_call = pool.fetch.call_args[0]
        assert ["organization"] in fetch_call

    async def test_entity_type_filter_accepts_multiple_values(self):
        """Repeated entity_type= filters are forwarded as a type list."""
        app, pool = self._make_app(total=0, fetch_rows=[])
        resp = await _get(app, _LIST_PATH, entity_type=["person", "organization"])
        assert resp.status_code == 200
        fetch_call = pool.fetch.call_args[0]
        assert ["person", "organization"] in fetch_call

    async def test_entity_list_sorts_people_by_tier_then_last_seen(self):
        """List ordering keeps people first by tier ASC, then last_seen ASC."""
        app, pool = self._make_app(total=0, fetch_rows=[])
        resp = await _get(app, _LIST_PATH)
        assert resp.status_code == 200
        data_sql = pool.fetch.call_args[0][0]
        assert "CASE entity_type" in data_sql
        assert "WHEN 'person' THEN 0" in data_sql
        assert "WHEN 'organization' THEN 1" in data_sql
        assert "CASE WHEN entity_type = 'person' THEN tier END ASC NULLS LAST" in data_sql
        assert "CASE WHEN entity_type = 'person' THEN last_seen END ASC NULLS LAST" in data_sql


# ===========================================================================
# §9.2 GET /entities/{id} — entity detail
# ===========================================================================


class TestGetEntityDetail:
    """GET /entities/{id} — §9.2."""

    def _make_app(
        self,
        *,
        entity_row: MagicMock | None = None,
        info_rows: list | None = None,
        classify_row: MagicMock | None = None,
    ) -> tuple[FastAPI, AsyncMock]:
        mock_pool = AsyncMock()
        # fetchrow is called twice: (1) entity lookup, (2) _classify_entity_state.
        # Default classify_row to healthy so existing tests don't need to specify it.
        default_classify = classify_row if classify_row is not None else _make_classify_row()
        mock_pool.fetchrow = AsyncMock(side_effect=[entity_row, default_classify])
        mock_pool.fetch = AsyncMock(return_value=info_rows or [])
        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_returns_200_with_entity_detail(self):
        """GET /entities/{id} returns 200 and EntityDetail body."""
        entity = _make_entity_row(
            entity_id=_ENT_ID,
            canonical_name="Alice Example",
            entity_type="person",
            aliases=["Ali"],
            roles=["owner"],
        )
        app, _ = self._make_app(entity_row=entity)
        resp = await _get(app, _ENTITY_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(_ENT_ID)
        assert body["canonical_name"] == "Alice Example"
        assert body["entity_type"] == "person"
        assert "entity_info" in body
        assert "aliases" in body
        assert "roles" in body

    async def test_entity_with_info_entries_masks_secured_values(self):
        """Secured entity_info values are masked (value=None) in the response."""
        entity = _make_entity_row(entity_id=_ENT_ID)
        info = _make_entity_info_row(type="api_key", value="secret-key", secured=True)
        app, _ = self._make_app(entity_row=entity, info_rows=[info])
        resp = await _get(app, _ENTITY_PATH)
        assert resp.status_code == 200
        body = resp.json()
        info_entries = body["entity_info"]
        assert len(info_entries) == 1
        assert info_entries[0]["secured"] is True
        assert info_entries[0]["value"] is None  # masked

    async def test_missing_entity_returns_404(self):
        """GET /entities/{id} returns 404 when entity does not exist."""
        app, _ = self._make_app(entity_row=None)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}")
        assert resp.status_code == 404

    async def test_not_owner_gated(self):
        """GET /entities/{id} is NOT owner-gated per the spec exclusion clause."""
        # Even without an owner entity row, get_entity should not return 403 owner_required.
        entity = _make_entity_row(entity_id=_ENT_ID)
        app, _ = self._make_app(entity_row=entity)
        resp = await _get(app, _ENTITY_PATH)
        assert resp.status_code == 200

    async def test_state_and_state_evidence_present_in_response(self):
        """Response body always includes 'state' and 'state_evidence' fields."""
        entity = _make_entity_row(entity_id=_ENT_ID)
        app, _ = self._make_app(entity_row=entity)
        resp = await _get(app, _ENTITY_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert "state" in body
        assert "state_evidence" in body


# ===========================================================================
# §9.2 state classification — entity detail state field
# ===========================================================================


class TestGetEntityDetailState:
    """State classification field on GET /entities/{id} — §9.2 extension [bu-x8ztv]."""

    def _make_app_with_classify(
        self,
        *,
        classify_row: MagicMock,
        info_rows: list | None = None,
    ) -> FastAPI:
        """Wire app with a specific classification row for _classify_entity_state."""
        entity = _make_entity_row(entity_id=_ENT_ID)
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(side_effect=[entity, classify_row])
        mock_pool.fetch = AsyncMock(return_value=info_rows or [])
        return _wire_app(mock_pool)

    async def test_healthy_entity_state(self):
        """Healthy entity (no flags, fresh fact, no shared identifiers) → state='healthy', evidence=None."""
        classify = _make_classify_row(
            is_unidentified=False,
            is_dup_flagged=False,
            has_fresh_fact=True,
            dup_predicate=None,
        )
        app = self._make_app_with_classify(classify_row=classify)
        resp = await _get(app, _ENTITY_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "healthy"
        assert body["state_evidence"] is None

    async def test_unidentified_entity_state(self):
        """Entity with unidentified=true in metadata → state='unidentified', evidence={}."""
        classify = _make_classify_row(
            is_unidentified=True,
            is_dup_flagged=False,
            has_fresh_fact=False,
        )
        app = self._make_app_with_classify(classify_row=classify)
        resp = await _get(app, _ENTITY_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "unidentified"
        assert body["state_evidence"] == {}

    async def test_stale_entity_state(self):
        """Stale entity (no recent fact in 365 days) → state='stale', evidence has last_seen."""
        stale_dt = _NOW - timedelta(days=400)
        classify = _make_classify_row(
            is_unidentified=False,
            is_dup_flagged=False,
            has_fresh_fact=False,
            last_seen=stale_dt,
        )
        app = self._make_app_with_classify(classify_row=classify)
        resp = await _get(app, _ENTITY_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "stale"
        assert "last_seen" in body["state_evidence"]
        assert body["state_evidence"]["last_seen"] is not None

    async def test_stale_entity_with_no_facts(self):
        """Stale entity with no facts at all → state='stale', evidence has last_seen=null."""
        classify = _make_classify_row(
            is_unidentified=False,
            is_dup_flagged=False,
            has_fresh_fact=False,
            last_seen=None,
        )
        app = self._make_app_with_classify(classify_row=classify)
        resp = await _get(app, _ENTITY_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "stale"
        assert body["state_evidence"]["last_seen"] is None

    async def test_duplicate_candidate_via_metadata_flag(self):
        """Entity with duplicate_candidate=true flag → state='duplicate-candidate', evidence={}."""
        classify = _make_classify_row(
            is_unidentified=False,
            is_dup_flagged=True,
            has_fresh_fact=True,
            dup_predicate=None,
        )
        app = self._make_app_with_classify(classify_row=classify)
        resp = await _get(app, _ENTITY_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "duplicate-candidate"
        assert body["state_evidence"] == {}

    async def test_duplicate_candidate_via_shared_email(self):
        """Entity sharing a has-email value → state='duplicate-candidate', evidence has predicate."""
        peer_id = str(uuid4())
        classify = _make_classify_row(
            is_unidentified=False,
            is_dup_flagged=False,
            has_fresh_fact=True,
            dup_predicate="has-email",
            dup_shared_value="shared@example.com",
            dup_peer_entity_ids=[peer_id],
        )
        app = self._make_app_with_classify(classify_row=classify)
        resp = await _get(app, _ENTITY_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "duplicate-candidate"
        ev = body["state_evidence"]
        assert ev["predicate"] == "has-email"
        assert ev["shared_value"] == "shared@example.com"
        assert peer_id in ev["peer_entity_ids"]

    async def test_priority_unidentified_beats_stale(self):
        """Entity matching both unidentified and stale → reports 'unidentified' (higher priority)."""
        classify = _make_classify_row(
            is_unidentified=True,
            is_dup_flagged=False,
            has_fresh_fact=False,  # also stale — but unidentified wins
            last_seen=None,
        )
        app = self._make_app_with_classify(classify_row=classify)
        resp = await _get(app, _ENTITY_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "unidentified"
        assert body["state_evidence"] == {}


# ===========================================================================
# §9.3 / §9.7 POST /entities — create / promote entity
# ===========================================================================


class TestCreateEntity:
    """POST /entities — §9.3/§9.7."""

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        updated_row: MagicMock | None = None,
    ) -> FastAPI:
        """Wire a minimal app for POST /entities.

        Pool call sequence:
          1. pool.fetchrow → owner-entity gate
          2. pool.acquire()→ conn.fetchrow(entity lookup), conn.fetchrow(INSERT RETURNING), conn.fetchrow(stats)
        """
        mock_conn = AsyncMock()
        stats = MagicMock()
        stats.__getitem__ = MagicMock(
            side_effect=lambda k: {"tier": None, "last_seen": None, "contact_fact_count": 0}[k]
        )
        fetchrow_seq = []
        if updated_row is not None:
            fetchrow_seq.append(updated_row)
        fetchrow_seq.append(stats)
        mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_seq)
        mock_conn.fetchval = AsyncMock(return_value=uuid4())
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_txn = AsyncMock()
        mock_txn.__aenter__ = AsyncMock(return_value=None)
        mock_txn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.transaction = MagicMock(return_value=mock_txn)

        @asynccontextmanager
        async def _acquire():
            yield mock_conn

        mock_pool = AsyncMock()
        owner_row = _make_owner_row() if owner_exists else None
        mock_pool.fetchrow = AsyncMock(return_value=owner_row)
        mock_pool.acquire = MagicMock(return_value=_acquire())
        return _wire_app(mock_pool)

    async def test_happy_path_returns_201_on_create(self):
        """POST /entities with canonical_name returns 201 + EntitySummary."""
        new_entity = _make_entity_row(entity_id=uuid4(), canonical_name="New Person", metadata={})
        app = self._make_app(updated_row=new_entity)
        resp = await _post(app, _LIST_PATH, {"canonical_name": "New Person"})
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["canonical_name"] == "New Person"

    async def test_owner_gate_returns_403_when_no_owner(self):
        """POST /entities returns 403 + owner_required when no owner entity."""
        app = self._make_app(owner_exists=False)
        resp = await _post(app, _LIST_PATH, {"canonical_name": "Ghost"})
        _assert_owner_required(resp)

    async def test_missing_canonical_name_returns_422(self):
        """POST /entities without canonical_name returns 422 validation error."""
        new_entity = _make_entity_row(canonical_name="fallback", metadata={})
        app = self._make_app(updated_row=new_entity)
        resp = await _post(app, _LIST_PATH, {})
        assert resp.status_code == 422


# ===========================================================================
# §9.4 GET + POST + DELETE /entities/{id}/contacts
# ===========================================================================


class TestEntityContacts:
    """GET/POST/DELETE /entities/{id}/contacts — §9.4."""

    def _make_contact_fact_row(
        self,
        predicate: str = "has-email",
        object_val: str = _EMAIL,
    ) -> MagicMock:
        data = {
            "id": uuid4(),
            "predicate": predicate,
            "object": object_val,
            "src": "relationship",
            "conf": 1.0,
            "last_seen": None,
            "weight": None,
            "verified": False,
            "primary": None,
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    def _make_get_app(
        self,
        *,
        owner_exists: bool = True,
        entity_exists: bool = True,
        fact_rows: list | None = None,
    ) -> tuple[FastAPI, AsyncMock]:
        mock_pool = AsyncMock()
        owner_row = _make_owner_row() if owner_exists else None
        entity_val = 1 if entity_exists else None
        mock_pool.fetchrow = AsyncMock(return_value=owner_row)
        mock_pool.fetchval = AsyncMock(return_value=entity_val)
        mock_pool.fetch = AsyncMock(return_value=fact_rows or [])
        return _wire_app(mock_pool), mock_pool

    async def test_get_contacts_happy_path_returns_200(self):
        """GET /entities/{id}/contacts returns list of active contact facts under 'facts' key."""
        rows = [self._make_contact_fact_row("has-email")]
        app, _ = self._make_get_app(fact_rows=rows)
        resp = await _get(app, _CONTACTS_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert "facts" in body
        assert len(body["facts"]) == 1
        assert body["facts"][0]["predicate"] == "has-email"

    async def test_get_contacts_owner_gate_returns_403(self):
        """GET /entities/{id}/contacts returns 403 when no owner entity."""
        app, _ = self._make_get_app(owner_exists=False)
        resp = await _get(app, _CONTACTS_PATH)
        _assert_owner_required(resp)

    async def test_get_contacts_missing_entity_returns_404(self):
        """GET /entities/{id}/contacts returns 404 for unknown entity."""
        app, _ = self._make_get_app(entity_exists=False)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/contacts")
        assert resp.status_code == 404

    async def test_get_contacts_sql_uses_entity_facts_table(self):
        """GET /entities/{id}/contacts SQL must use relationship.entity_facts, not facts."""
        app, pool = self._make_get_app(fact_rows=[])
        await _get(app, _CONTACTS_PATH)
        fetch_call_sql = pool.fetch.call_args[0][0]
        assert "relationship.entity_facts" in fetch_call_sql
        assert "relationship.facts" not in fetch_call_sql

    def _make_fact_row_for_response(self, predicate: str = "has-email") -> MagicMock:
        """Build a row mock for the post-write fact fetch (pool.fetchrow)."""
        data = {
            "id": uuid4(),
            "predicate": predicate,
            "object": _EMAIL,
            "src": "relationship",
            "conf": 1.0,
            "last_seen": None,
            "weight": None,
            "verified": False,
            "primary": None,
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    _WRITER_PATCH = "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact"

    def _make_post_app(
        self,
        *,
        owner_exists: bool = True,
        entity_exists: bool = True,
    ) -> tuple[FastAPI, AsyncMock]:
        """App wired for POST /entities/{id}/contacts (pool setup only).

        POST /entities/{id}/contacts call sequence:
          1. pool.fetchrow → owner-entity gate (None → 403)
          2. pool.fetchval → entity existence check (None → 404)
          3. relationship_assert_fact(pool, ...) — patched by caller via with patch(...)
          4. pool.fetchrow → fetch the resulting fact row (for response body)
        """
        owner_row = _make_owner_row() if owner_exists else None
        entity_val = 1 if entity_exists else None
        # After writer: endpoint calls pool.fetchrow again to fetch the new fact row.
        fact_row = self._make_fact_row_for_response()

        mock_pool = AsyncMock()
        if owner_exists:
            # fetchrow: [owner-gate row, post-write fact row]
            mock_pool.fetchrow = AsyncMock(side_effect=[owner_row, fact_row])
        else:
            # fetchrow: [None] → 403 immediately, no further calls
            mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.fetchval = AsyncMock(return_value=entity_val)
        return _wire_app(mock_pool), mock_pool

    def _make_writer_result(self, outcome: str = "inserted") -> MagicMock:
        mock_result = MagicMock()
        mock_result.outcome = AssertOutcome(outcome)
        mock_result.fact_id = uuid4()
        mock_result.action_id = None
        return mock_result

    async def test_post_contact_fact_happy_path_returns_201(self):
        """POST /entities/{id}/contacts creates a contact fact and returns 201."""
        app, _ = self._make_post_app()
        with patch(self._WRITER_PATCH, new=AsyncMock(return_value=self._make_writer_result())):
            resp = await _post(
                app,
                _CONTACTS_PATH,
                {"predicate": "has-email", "value": "alice@example.com"},
            )
        assert resp.status_code == 201

    async def test_post_contact_owner_gate_returns_403(self):
        """POST /entities/{id}/contacts returns 403 + owner_required without owner entity."""
        app, _ = self._make_post_app(owner_exists=False)
        with patch(self._WRITER_PATCH, new=AsyncMock(return_value=self._make_writer_result())):
            resp = await _post(
                app,
                _CONTACTS_PATH,
                {"predicate": "has-email", "value": "alice@example.com"},
            )
        _assert_owner_required(resp)

    async def test_post_contact_non_has_predicate_returns_400(self):
        """POST /entities/{id}/contacts rejects predicates that don't start with 'has-'."""
        app, _ = self._make_post_app()
        with patch(self._WRITER_PATCH, new=AsyncMock(return_value=self._make_writer_result())):
            resp = await _post(
                app,
                _CONTACTS_PATH,
                {"predicate": "knows", "value": "some-value"},
            )
        assert resp.status_code == 400


# ===========================================================================
# §9.5 GET /entities/queue — curation queue
# ===========================================================================


class TestEntityQueue:
    """GET /entities/queue — §9.5."""

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        queue_rows: list | None = None,
    ) -> tuple[FastAPI, AsyncMock]:
        mock_pool = AsyncMock()
        owner_row = _make_owner_row() if owner_exists else None
        mock_pool.fetchrow = AsyncMock(return_value=owner_row)
        mock_pool.fetch = AsyncMock(return_value=queue_rows or [])
        mock_pool.fetchval = AsyncMock(return_value=0)
        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_returns_200_with_queue(self):
        """GET /entities/queue returns 200 with items list and pagination."""
        app, _ = self._make_app(queue_rows=[])
        resp = await _get(app, _QUEUE_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert isinstance(body["items"], list)

    async def test_owner_gate_returns_403_when_no_owner(self):
        """GET /entities/queue returns 403 + owner_required when no owner entity."""
        app, _ = self._make_app(owner_exists=False)
        resp = await _get(app, _QUEUE_PATH)
        _assert_owner_required(resp)

    async def test_limit_above_200_rejected(self):
        """GET /entities/queue rejects limit > 200 with 422."""
        app, _ = self._make_app()
        resp = await _get(app, _QUEUE_PATH, limit=201)
        assert resp.status_code == 422


# ===========================================================================
# §9.6 GET /entities/search — deterministic finder
# ===========================================================================


class TestEntitySearch:
    """GET /entities/search — §9.6."""

    def _make_search_row(
        self,
        *,
        entity_id: UUID | None = None,
        canonical_name: str = "Alice Example",
        entity_type: str = "person",
        score: int = 100,
        match_kind: str = "prefix",
    ) -> MagicMock:
        data = {
            "entity_id": entity_id or uuid4(),
            "canonical_name": canonical_name,
            "entity_type": entity_type,
            "score": score,
            "match_kind": match_kind,
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        search_rows: list | None = None,
    ) -> tuple[FastAPI, AsyncMock]:
        mock_pool = AsyncMock()
        owner_row = _make_owner_row() if owner_exists else None
        mock_pool.fetchrow = AsyncMock(return_value=owner_row)
        mock_pool.fetch = AsyncMock(return_value=search_rows or [])
        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_search_returns_200(self):
        """GET /entities/search?q=alice returns 200 with results."""
        rows = [self._make_search_row(canonical_name="Alice Example")]
        app, _ = self._make_app(search_rows=rows)
        resp = await _get(app, _SEARCH_PATH, q="alice")
        assert resp.status_code == 200
        body = resp.json()
        assert "results" in body
        assert len(body["results"]) == 1

    async def test_owner_gate_returns_403(self):
        """GET /entities/search returns 403 + owner_required when no owner entity."""
        app, _ = self._make_app(owner_exists=False)
        resp = await _get(app, _SEARCH_PATH, q="alice")
        _assert_owner_required(resp)

    async def test_empty_query_returns_200_with_empty_results(self):
        """GET /entities/search?q= returns 200 with empty results."""
        app, _ = self._make_app(search_rows=[])
        resp = await _get(app, _SEARCH_PATH, q="")
        assert resp.status_code == 200
        body = resp.json()
        assert body["results"] == []

    async def test_limit_above_50_rejected_with_422(self):
        """GET /entities/search rejects limit > 50 with 422."""
        app, _ = self._make_app()
        resp = await _get(app, _SEARCH_PATH, q="alice", limit=51)
        assert resp.status_code == 422


# ===========================================================================
# §9.7 POST /entities/{id}/promote-tier — tier promotion
# ===========================================================================


class TestPromoteTier:
    """POST /entities/{id}/promote-tier — §9.7/§9.8."""

    _WRITER_PATCH = "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact"

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        entity_exists: bool = True,
        outcome: str = "inserted",
    ) -> tuple[FastAPI, AsyncMock]:
        fact_id = uuid4()
        mock_result = MagicMock()
        mock_result.outcome = AssertOutcome(outcome)
        mock_result.fact_id = fact_id
        mock_result.action_id = None

        mock_pool = AsyncMock()
        owner_row = _make_owner_row() if owner_exists else None
        entity_val = 1 if entity_exists else None
        # Sequence: owner-roles check (fetchrow), then entity-existence check (fetchval)
        mock_pool.fetchrow = AsyncMock(return_value=owner_row)
        mock_pool.fetchval = AsyncMock(return_value=entity_val)
        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_returns_201_inserted(self):
        """POST /entities/{id}/promote-tier returns 201 + outcome='inserted'."""
        app, _ = self._make_app()
        with patch(self._WRITER_PATCH, new=AsyncMock(return_value=self._make_result("inserted"))):
            resp = await _post(app, _PROMOTE_TIER_PATH, {"tier": 15})
        assert resp.status_code == 201
        body = resp.json()
        assert body["outcome"] in ("inserted", "unchanged", "superseded")

    def _make_result(self, outcome: str) -> MagicMock:
        mock_result = MagicMock()
        mock_result.outcome = AssertOutcome(outcome)
        mock_result.fact_id = uuid4()
        mock_result.action_id = None
        return mock_result

    async def test_owner_gate_returns_403(self):
        """POST /entities/{id}/promote-tier returns 403 when no owner entity."""
        app, _ = self._make_app(owner_exists=False)
        with patch(self._WRITER_PATCH, new=AsyncMock(return_value=self._make_result("inserted"))):
            resp = await _post(app, _PROMOTE_TIER_PATH, {"tier": 15})
        _assert_owner_required(resp)

    async def test_invalid_tier_returns_422(self):
        """POST /entities/{id}/promote-tier rejects tier not in allowed set."""
        app, _ = self._make_app()
        with patch(self._WRITER_PATCH, new=AsyncMock(return_value=self._make_result("inserted"))):
            resp = await _post(app, _PROMOTE_TIER_PATH, {"tier": 999})
        assert resp.status_code == 422

    async def test_missing_entity_returns_404(self):
        """POST /entities/{id}/promote-tier returns 404 for unknown entity."""
        app, _ = self._make_app(entity_exists=False)
        with patch(self._WRITER_PATCH, new=AsyncMock(return_value=self._make_result("inserted"))):
            resp = await _post(
                app, f"/api/relationship/entities/{_MISSING_ENT_ID}/promote-tier", {"tier": 15}
            )
        assert resp.status_code == 404


# ===========================================================================
# §9.8 POST /entities/{id}/archive + DELETE /entities/{id}
# ===========================================================================


class TestArchiveEntity:
    """POST /entities/{id}/archive — §9.8."""

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        entity_exists: bool = True,
    ) -> tuple[FastAPI, AsyncMock]:
        owner_row = _make_owner_row() if owner_exists else None
        entity_row = _make_entity_row(entity_id=_ENT_ID) if entity_exists else None
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(side_effect=[owner_row, entity_row])
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")
        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_returns_204(self):
        """POST /entities/{id}/archive returns 204 on success."""
        app, _ = self._make_app()
        resp = await _post(app, _ARCHIVE_PATH)
        assert resp.status_code == 204

    async def test_owner_gate_returns_403(self):
        """POST /entities/{id}/archive returns 403 + owner_required without owner entity."""
        app, _ = self._make_app(owner_exists=False)
        resp = await _post(app, _ARCHIVE_PATH)
        _assert_owner_required(resp)

    async def test_missing_entity_returns_404(self):
        """POST /entities/{id}/archive returns 404 for unknown entity."""
        app, _ = self._make_app(entity_exists=False)
        resp = await _post(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/archive")
        assert resp.status_code == 404


class TestForgetEntity:
    """DELETE /entities/{id} — §9.8."""

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        entity_exists: bool = True,
    ) -> tuple[FastAPI, AsyncMock]:
        owner_row = _make_owner_row() if owner_exists else None
        entity_row = _make_entity_row(entity_id=_ENT_ID) if entity_exists else None

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
        mock_pool.fetchrow = AsyncMock(side_effect=[owner_row, entity_row])
        mock_pool.acquire = MagicMock(return_value=_acquire())
        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_returns_204(self):
        """DELETE /entities/{id} returns 204 on success."""
        app, _ = self._make_app()
        resp = await _delete(app, _ENTITY_PATH)
        assert resp.status_code == 204

    async def test_owner_gate_returns_403(self):
        """DELETE /entities/{id} returns 403 + owner_required without owner entity."""
        app, _ = self._make_app(owner_exists=False)
        resp = await _delete(app, _ENTITY_PATH)
        _assert_owner_required(resp)

    async def test_missing_entity_returns_404(self):
        """DELETE /entities/{id} returns 404 for unknown entity UUID."""
        app, _ = self._make_app(entity_exists=False)
        resp = await _delete(app, f"/api/relationship/entities/{_MISSING_ENT_ID}")
        assert resp.status_code == 404


# ===========================================================================
# §9.9 POST /entities/{id}/merge — entity merge
# ===========================================================================


class TestMergeEntities:
    """POST /entities/{id}/merge — §9.9."""

    def _make_lock_row(self, entity_id: UUID, metadata: dict | None = None) -> MagicMock:
        """Build a MagicMock for the FOR UPDATE lock SELECT row in the merge transaction."""
        data = {"id": entity_id, "metadata": metadata or {}}
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        entity_a_exists: bool = True,
        entity_b_exists: bool = True,
    ) -> tuple[FastAPI, AsyncMock]:
        """Wire an app for the merge endpoint.

        Pool call sequence:
          1. pool.fetchrow → owner-entity gate (None → 403)
          2. pool.acquire() → conn context
               conn.fetch()   → lock both entities (SELECT ... FOR UPDATE)
               conn.execute() → retract conflicting source subject-rows
               conn.fetchval()→ move remaining source subject-rows (count)
               conn.execute() → rewire object-side rows
               conn.execute() → tombstone source entity
        """
        owner_row = _make_owner_row() if owner_exists else None

        # Build the lock rows (conn.fetch returns a list of rows ordered by id)
        lock_rows: list = []
        if entity_a_exists:
            lock_rows.append(self._make_lock_row(_ENT_ID))
        if entity_b_exists:
            lock_rows.append(self._make_lock_row(_ENT_ID_B))
        # Sort by id ascending (mimics ORDER BY id in the query)
        lock_rows.sort(key=lambda r: r["id"])

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=lock_rows)
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_conn.fetchval = AsyncMock(return_value=0)  # moved row count

        mock_txn = AsyncMock()
        mock_txn.__aenter__ = AsyncMock(return_value=None)
        mock_txn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.transaction = MagicMock(return_value=mock_txn)

        @asynccontextmanager
        async def _acquire():
            yield mock_conn

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=owner_row)
        mock_pool.acquire = MagicMock(return_value=_acquire())
        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_returns_200_with_merge_response(self):
        """POST /entities/{id}/merge returns 200 + MergeEntitiesResponse on success."""
        app, _ = self._make_app()
        resp = await _post(
            app,
            _MERGE_PATH,
            {"entityA": str(_ENT_ID), "entityB": str(_ENT_ID_B), "keepAs": "B"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "kept_entity_id" in body
        assert "tombstoned_entity_id" in body

    async def test_owner_gate_returns_403(self):
        """POST /entities/{id}/merge returns 403 without owner entity."""
        app, _ = self._make_app(owner_exists=False)
        resp = await _post(
            app,
            _MERGE_PATH,
            {"entityA": str(_ENT_ID), "entityB": str(_ENT_ID_B), "keepAs": "B"},
        )
        _assert_owner_required(resp)

    async def test_missing_target_entity_returns_404(self):
        """POST /entities/{id}/merge returns 404 when entityB entity does not exist.

        entity_b_exists=False → lock query returns only entityA's row.
        The endpoint raises 404 because target_id (entityB) is absent from lock_map.
        """
        app, _ = self._make_app(entity_b_exists=False)
        resp = await _post(
            app,
            _MERGE_PATH,
            {"entityA": str(_ENT_ID), "entityB": str(_ENT_ID_B), "keepAs": "B"},
        )
        assert resp.status_code == 404

    async def test_same_entity_merge_returns_422(self):
        """POST /entities/{id}/merge returns 422 when entityA == entityB."""
        app, _ = self._make_app()
        resp = await _post(
            app,
            _MERGE_PATH,
            {"entityA": str(_ENT_ID), "entityB": str(_ENT_ID), "keepAs": "A"},
        )
        assert resp.status_code == 422


# ===========================================================================
# §9.10 POST /entities/queue/dismiss — dismiss queue entry
# ===========================================================================


class TestQueueDismiss:
    """POST /entities/queue/dismiss — §9.10."""

    _WRITER_PATCH = "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact"

    def _make_result(self, outcome: str = "inserted") -> MagicMock:
        mock_result = MagicMock()
        mock_result.outcome = AssertOutcome(outcome)
        mock_result.fact_id = uuid4()
        mock_result.action_id = None
        return mock_result

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        entity_exists: bool = True,
    ) -> tuple[FastAPI, AsyncMock]:
        owner_row = _make_owner_row() if owner_exists else None
        entity_row = _make_entity_row(entity_id=_ENT_ID) if entity_exists else None
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(side_effect=[owner_row, entity_row])
        mock_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)
        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_returns_200(self):
        """POST /entities/queue/dismiss returns 200 + outcome on success."""
        app, _ = self._make_app()
        with patch(self._WRITER_PATCH, new=AsyncMock(return_value=self._make_result("inserted"))):
            resp = await _post(app, _DISMISS_PATH, {"entity_id": str(_ENT_ID)})
        assert resp.status_code == 200

    async def test_owner_gate_returns_403(self):
        """POST /entities/queue/dismiss returns 403 + owner_required without owner entity."""
        app, _ = self._make_app(owner_exists=False)
        with patch(self._WRITER_PATCH, new=AsyncMock(return_value=self._make_result("inserted"))):
            resp = await _post(app, _DISMISS_PATH, {"entity_id": str(_ENT_ID)})
        _assert_owner_required(resp)

    async def test_missing_entity_returns_404(self):
        """POST /entities/queue/dismiss returns 404 when entity does not exist."""
        app, _ = self._make_app(entity_exists=False)
        with patch(self._WRITER_PATCH, new=AsyncMock(return_value=self._make_result("inserted"))):
            resp = await _post(app, _DISMISS_PATH, {"entity_id": str(_MISSING_ENT_ID)})
        assert resp.status_code == 404

    async def test_missing_entity_id_in_body_returns_422(self):
        """POST /entities/queue/dismiss without entity_id returns 422."""
        app, _ = self._make_app()
        with patch(self._WRITER_PATCH, new=AsyncMock(return_value=self._make_result("inserted"))):
            resp = await _post(app, _DISMISS_PATH, {})
        assert resp.status_code == 422


# ===========================================================================
# §9.11 GET /entities/concentration — weight aggregation
# ===========================================================================


class TestEntityConcentration:
    """GET /entities/concentration — §9.11."""

    def _make_tab_row(self, predicate: str = "knows", label: str = "Knows") -> MagicMock:
        data = {"predicate": predicate, "label": label, "description": None}
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    def _make_agg_row(
        self,
        *,
        entity_id: UUID | None = None,
        canonical_name: str = "Alice Example",
        weight_sum: int = 5,
        fact_count: int = 2,
    ) -> MagicMock:
        data = {
            "entity_id": entity_id or uuid4(),
            "canonical_name": canonical_name,
            "weight_sum": weight_sum,
            "fact_count": fact_count,
            "last_seen": _NOW,
            "src": "relationship",
            "conf": 1.0,
            "verified": False,
            "primary": None,
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        tab_rows: list | None = None,
        agg_rows: list | None = None,
    ) -> tuple[FastAPI, AsyncMock]:
        mock_pool = AsyncMock()
        owner_row = _make_owner_row() if owner_exists else None
        mock_pool.fetchrow = AsyncMock(return_value=owner_row)
        # fetch is called multiple times: once for tabs, once for aggregation
        mock_pool.fetch = AsyncMock(side_effect=[tab_rows or [], agg_rows or []])
        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_returns_200_with_rollup(self):
        """GET /entities/concentration returns 200 with items and rollup."""
        tab_rows = [self._make_tab_row("knows", "Knows")]
        agg_rows = [self._make_agg_row(weight_sum=10, fact_count=3)]
        app, _ = self._make_app(tab_rows=tab_rows, agg_rows=agg_rows)
        resp = await _get(app, _CONCENTRATION_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "rollup" in body
        assert "predicate_tabs" in body

    async def test_owner_gate_returns_403(self):
        """GET /entities/concentration returns 403 without owner entity."""
        app, _ = self._make_app(owner_exists=False)
        resp = await _get(app, _CONCENTRATION_PATH)
        _assert_owner_required(resp)

    async def test_empty_result_returns_empty_items(self):
        """GET /entities/concentration returns empty items when no data."""
        app, _ = self._make_app(tab_rows=[], agg_rows=[])
        resp = await _get(app, _CONCENTRATION_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["rollup"]["total"] == 0


# ===========================================================================
# §9.12 GET /entities/{id}/activity — cross-butler activity aggregator
# ===========================================================================


class TestEntityActivity:
    """GET /entities/{id}/activity — §9.12."""

    def _make_fact_row(
        self,
        *,
        predicate: str = "contact_note",
        last_seen: datetime | None = None,
    ) -> MagicMock:
        data = {
            "id": uuid4(),
            "predicate": predicate,
            "last_seen": last_seen or _NOW,
            "created_at": _NOW,
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    def _make_mcp_result(self, episodes: list[dict]) -> MagicMock:
        import json

        payload = json.dumps({"data": episodes, "count": len(episodes)})
        block = MagicMock()
        block.text = payload
        result = MagicMock()
        result.content = [block]
        result.is_error = False
        return result

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        entity_exists: bool = True,
        fact_rows: list | None = None,
        chronicler_episodes: list[dict] | None = None,
        chronicler_unreachable: bool = False,
    ) -> tuple[FastAPI, AsyncMock]:
        from butlers.api.deps import ButlerUnreachableError, get_mcp_manager

        mock_pool = AsyncMock()
        owner_row = _make_owner_row() if owner_exists else None
        entity_val = 1 if entity_exists else None
        mock_pool.fetchrow = AsyncMock(return_value=owner_row)
        mock_pool.fetchval = AsyncMock(return_value=entity_val)
        mock_pool.fetch = AsyncMock(return_value=fact_rows or [])

        mock_mcp_manager = MagicMock()
        if chronicler_unreachable:
            mock_mcp_manager.get_client = AsyncMock(side_effect=ButlerUnreachableError("offline"))
        else:
            mock_client = AsyncMock()
            episodes = chronicler_episodes or []
            mock_client.call_tool = AsyncMock(return_value=self._make_mcp_result(episodes))
            mock_mcp_manager.get_client = AsyncMock(return_value=mock_client)

        app = _wire_app(mock_pool)
        app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp_manager
        return app, mock_pool

    async def test_happy_path_returns_200_with_items(self):
        """GET /entities/{id}/activity returns 200 with items/total/limit/offset."""
        fact_row = self._make_fact_row(predicate="contact_note", last_seen=_NOW)
        app, _ = self._make_app(fact_rows=[fact_row], chronicler_episodes=[])
        resp = await _get(app, _ACTIVITY_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "limit" in body
        assert "offset" in body
        assert body["total"] == 1

    async def test_owner_gate_returns_403(self):
        """GET /entities/{id}/activity returns 403 + owner_required without owner entity."""
        app, _ = self._make_app(owner_exists=False)
        resp = await _get(app, _ACTIVITY_PATH)
        _assert_owner_required(resp)

    async def test_missing_entity_returns_404(self):
        """GET /entities/{id}/activity returns 404 for unknown entity."""
        app, _ = self._make_app(entity_exists=False)
        resp = await _get(app, _ACTIVITY_PATH)
        assert resp.status_code == 404

    async def test_sql_uses_entity_facts_not_facts(self):
        """Activity endpoint SQL must reference relationship.entity_facts, not relationship.facts."""
        fact_row = self._make_fact_row(last_seen=_NOW)
        app, pool = self._make_app(fact_rows=[fact_row], chronicler_episodes=[])
        await _get(app, _ACTIVITY_PATH)
        fetch_call_sql = pool.fetch.call_args_list[0][0][0]
        assert "relationship.entity_facts" in fetch_call_sql
        assert "relationship.facts" not in fetch_call_sql

    async def test_chronicler_unreachable_degrades_gracefully(self):
        """GET /entities/{id}/activity returns relationship facts only when chronicler offline."""
        fact_row = self._make_fact_row(last_seen=_NOW)
        app, _ = self._make_app(fact_rows=[fact_row], chronicler_unreachable=True)
        resp = await _get(app, _ACTIVITY_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["src"] == "relationship"

    async def test_merged_stream_sorted_desc(self):
        """Activity items from relationship and chronicler are merged and sorted desc by ts."""
        ep_id = uuid4()
        fact_row = self._make_fact_row(last_seen=_OLDER)
        episodes = [
            {
                "id": str(ep_id),
                "source_name": "test",
                "source_ref": "ref-001",
                "episode_type": "meeting",
                "start_at": _NOW.isoformat(),
                "end_at": None,
                "precision": "exact",
                "title": "Test Episode",
                "payload": {},
                "privacy": "normal",
                "retention_days": None,
                "tombstone_at": None,
                "canonical_start_at": _NOW.isoformat(),
                "canonical_end_at": None,
                "canonical_title": "Test Episode",
                "canonical_privacy": "normal",
                "corrected_at": None,
                "correction_note": None,
                "created_at": _NOW.isoformat(),
                "updated_at": _NOW.isoformat(),
            }
        ]
        app, _ = self._make_app(fact_rows=[fact_row], chronicler_episodes=episodes)
        resp = await _get(app, _ACTIVITY_PATH)
        body = resp.json()
        assert body["total"] == 2
        # Newer episode first (chronicler _NOW > relationship _OLDER)
        assert body["items"][0]["src"] == "chronicler"
        assert body["items"][1]["src"] == "relationship"


# ===========================================================================
# §9.10 (neighbours) GET /entities/{id}/neighbours
# ===========================================================================


class TestEntityNeighbours:
    """GET /entities/{id}/neighbours — §9.2 (neighbours sub-task)."""

    def _make_fact_row(
        self,
        *,
        subject: UUID | None = None,
        predicate: str = "knows",
        object_val: str | None = None,
        direction: str = "forward",
        canonical_name: str = "Test Entity",
    ) -> MagicMock:
        data = {
            "id": uuid4(),
            "subject": subject or _ENT_ID,
            "predicate": predicate,
            "object": object_val or str(uuid4()),
            "object_kind": "entity",
            "src": "relationship",
            "conf": 1.0,
            "last_seen": None,
            "weight": None,
            "verified": False,
            "primary": None,
            "direction": direction,
            "canonical_name": canonical_name,
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        entity_exists: bool = True,
        fact_rows: list | None = None,
    ) -> tuple[FastAPI, AsyncMock]:
        mock_pool = AsyncMock()
        owner_row = _make_owner_row() if owner_exists else None
        entity_val = 1 if entity_exists else None
        mock_pool.fetchrow = AsyncMock(return_value=owner_row)
        mock_pool.fetchval = AsyncMock(return_value=entity_val)
        mock_pool.fetch = AsyncMock(return_value=fact_rows or [])
        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_returns_200_with_neighbours(self):
        """GET /entities/{id}/neighbours returns 200 with grouped neighbours dict."""
        neighbour_id = uuid4()
        rows = [
            self._make_fact_row(
                predicate="knows", object_val=str(neighbour_id), direction="forward"
            )
        ]
        app, _ = self._make_app(fact_rows=rows)
        resp = await _get(app, _NEIGHBOURS_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert "neighbours" in body
        assert "knows" in body["neighbours"]

    async def test_owner_gate_returns_403(self):
        """GET /entities/{id}/neighbours returns 403 without owner entity."""
        app, _ = self._make_app(owner_exists=False)
        resp = await _get(app, _NEIGHBOURS_PATH)
        _assert_owner_required(resp)

    async def test_missing_entity_returns_404(self):
        """GET /entities/{id}/neighbours returns 404 for unknown entity."""
        app, _ = self._make_app(entity_exists=False)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/neighbours")
        assert resp.status_code == 404

    async def test_empty_entity_returns_empty_neighbours(self):
        """Entity with no relational triples returns empty neighbours dict."""
        app, _ = self._make_app(fact_rows=[])
        resp = await _get(app, _NEIGHBOURS_PATH)
        assert resp.status_code == 200
        assert resp.json()["neighbours"] == {}


# ===========================================================================
# GET /entities/{entity_id}/facts — per-fact provenance grid (bu-mg4dk)
# ===========================================================================

_FACTS_PATH = f"/api/relationship/entities/{_ENT_ID}/facts"


class TestEntityFacts:
    """GET /entities/{id}/facts — per-fact provenance fields (bu-mg4dk)."""

    def _make_fact_row(
        self,
        *,
        predicate: str = "works-at",
        object_val: str = "Acme Corp",
        object_kind: str = "literal",
        src: str = "relationship",
        weight: int | None = 5,
        last_seen: datetime | None = None,
    ) -> MagicMock:
        data = {
            "id": uuid4(),
            "subject": _ENT_ID,
            "predicate": predicate,
            "object": object_val,
            "object_kind": object_kind,
            "src": src,
            "conf": 1.0,
            "weight": weight,
            "last_seen": last_seen,
            "verified": False,
            "primary": None,
            "validity": "active",
            "created_at": _NOW,
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        entity_exists: bool = True,
        fact_rows: list | None = None,
        total_count: int | None = None,
    ) -> tuple[FastAPI, AsyncMock]:
        mock_pool = AsyncMock()
        owner_row = _make_owner_row() if owner_exists else None
        entity_val = 1 if entity_exists else None

        # GET /facts call sequence:
        #   1. fetchrow → owner-entity gate
        #   2. fetchval → entity existence check
        #   3. fetchval → COUNT(*) for total
        #   4. fetch → fact rows
        rows = fact_rows or []
        count = total_count if total_count is not None else len(rows)

        mock_pool.fetchrow = AsyncMock(return_value=owner_row)
        mock_pool.fetchval = AsyncMock(side_effect=[entity_val, count])
        mock_pool.fetch = AsyncMock(return_value=rows)

        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_returns_200_with_facts(self):
        """GET /entities/{id}/facts returns 200 with provenance facts."""
        rows = [self._make_fact_row()]
        app, _ = self._make_app(fact_rows=rows)
        resp = await _get(app, _FACTS_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert "facts" in body
        assert body["total"] == 1
        assert body["has_more"] is False

    async def test_response_shape_includes_provenance_fields(self):
        """Facts response includes weight, last_observed_at, object_kind, src."""
        rows = [self._make_fact_row(weight=7, object_kind="literal", src="relationship")]
        app, _ = self._make_app(fact_rows=rows)
        resp = await _get(app, _FACTS_PATH)
        assert resp.status_code == 200
        body = resp.json()
        fact = body["facts"][0]
        assert "weight" in fact
        assert fact["weight"] == 7
        assert "last_observed_at" in fact
        assert "object_kind" in fact
        assert fact["object_kind"] == "literal"
        assert "src" in fact
        assert fact["src"] == "relationship"

    async def test_owner_gate_returns_403(self):
        """GET /entities/{id}/facts returns 403 when no owner entity."""
        app, _ = self._make_app(owner_exists=False)
        resp = await _get(app, _FACTS_PATH)
        _assert_owner_required(resp)

    async def test_missing_entity_returns_404(self):
        """GET /entities/{id}/facts returns 404 for unknown entity."""
        app, _ = self._make_app(entity_exists=False)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/facts")
        assert resp.status_code == 404

    async def test_empty_facts_returns_empty_list(self):
        """Entity with no active triples returns empty facts list."""
        app, _ = self._make_app(fact_rows=[], total_count=0)
        resp = await _get(app, _FACTS_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["facts"] == []
        assert body["total"] == 0
        assert body["has_more"] is False

    async def test_has_more_is_true_when_total_exceeds_limit(self):
        """has_more is True when total > offset + limit."""
        rows = [self._make_fact_row() for _ in range(20)]
        app, _ = self._make_app(fact_rows=rows, total_count=50)
        resp = await _get(app, _FACTS_PATH, limit=20)
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_more"] is True
        assert body["total"] == 50

    async def test_sql_uses_entity_facts_not_facts_table(self):
        """GET /entities/{id}/facts SQL must use relationship.entity_facts."""
        app, pool = self._make_app(fact_rows=[])
        await _get(app, _FACTS_PATH)
        fetch_call_sql = pool.fetch.call_args[0][0]
        assert "relationship.entity_facts" in fetch_call_sql
        assert "relationship.facts" not in fetch_call_sql

    async def test_sql_selects_required_provenance_columns(self):
        """SQL must SELECT weight, last_seen, object_kind, src."""
        app, pool = self._make_app(fact_rows=[])
        await _get(app, _FACTS_PATH)
        fetch_call_sql = pool.fetch.call_args[0][0]
        assert "weight" in fetch_call_sql
        assert "last_seen" in fetch_call_sql
        assert "object_kind" in fetch_call_sql
        assert "src" in fetch_call_sql
