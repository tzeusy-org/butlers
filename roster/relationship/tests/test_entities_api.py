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
  9.7   PATCH /entities/{id}/dunbar-tier        — dunbar tier override (replaces promote-tier)
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
_CONTACTS_PATH = f"/api/relationship/entities/{_ENT_ID}/contacts"
_QUEUE_PATH = "/api/relationship/entities/queue"
_SEARCH_PATH = "/api/relationship/entities/search"
_CONCENTRATION_PATH = "/api/relationship/entities/concentration"
_DISMISS_PATH = "/api/relationship/entities/queue/dismiss"
_ACTIVITY_PATH = f"/api/relationship/entities/{_ENT_ID}/activity"
_NEIGHBOURS_PATH = f"/api/relationship/entities/{_ENT_ID}/neighbours"
_COMPARE_PATH = "/api/relationship/entities/compare"
_DISMISS_PAIR_PATH = "/api/relationship/entities/dismiss-pair"


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
    first_seen: datetime | None = None,
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
        "first_seen": first_seen,
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
    is_dismissed: bool = False,
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
        "is_dismissed": is_dismissed,
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

    async def test_ids_filter_passes_to_query(self):
        """ids= filter forwards a uuid[] condition and the id list to the DB query."""
        app, pool = self._make_app(total=0, fetch_rows=[])
        id_a = str(uuid4())
        id_b = str(uuid4())
        resp = await _get(app, _LIST_PATH, ids=[id_a, id_b])
        assert resp.status_code == 200
        data_sql = pool.fetch.call_args[0][0]
        assert "e.id = ANY(" in data_sql
        assert "::uuid[]" in data_sql
        # The id list is forwarded as a positional arg (order preserved).
        assert [id_a, id_b] in pool.fetch.call_args[0]

    async def test_ids_filter_present_but_empty_yields_empty_set(self):
        """ids= present with no values applies an empty-set filter (matches nothing)."""
        app, pool = self._make_app(total=0, fetch_rows=[])
        # httpx omits empty-list params, so exercise the empty case via a single
        # blank value which the endpoint strips to an empty id list.
        resp = await _get(app, _LIST_PATH, ids=[""])
        assert resp.status_code == 200
        data_sql = pool.fetch.call_args[0][0]
        assert "e.id = ANY(" in data_sql
        assert [] in pool.fetch.call_args[0]

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

        # conn.fetch serves the entity FOR UPDATE lock AND the memory-module
        # ``facts`` repoint scans added in bu-j820n.1. Only the lock query returns
        # entity rows; the facts scans return [] so the per-row repoint loops do
        # not run against the lock MagicMocks (which lack a ``valid_at`` key).
        async def _conn_fetch(query, *args):
            return lock_rows if "FOR UPDATE" in query else []

        mock_conn.fetch = AsyncMock(side_effect=_conn_fetch)
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
        # merge_entities computes the pre-transaction merge-review snapshot, which
        # issues: fetchrow(owner gate), fetchrow(summary A), fetchrow(summary B),
        # fetchrow(classify A), fetchrow(classify B); fetch(identity A/B,
        # narrative A/B, single-cardinality predicates); then fetchval(INSERT
        # merge_reviews RETURNING id) after the transaction.
        summary_a = _make_compare_summary_row(entity_id=_ENT_ID)
        summary_b = _make_compare_summary_row(entity_id=_ENT_ID_B)
        mock_pool.fetchrow = AsyncMock(
            side_effect=[
                owner_row,
                summary_a,
                summary_b,
                _make_classify_row(),
                _make_classify_row(),
            ]
        )
        mock_pool.fetch = AsyncMock(side_effect=[[], [], [], [], []])
        mock_pool.fetchval = AsyncMock(return_value=uuid4())
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

    def _make_tab_row(
        self, predicate: str = "knows", label: str = "Knows", entity_count: int = 0
    ) -> MagicMock:
        data = {
            "predicate": predicate,
            "label": label,
            "description": None,
            "entity_count": entity_count,
        }
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

    async def test_predicate_tabs_include_entity_count(self):
        """GET /entities/concentration predicate_tabs include entity_count per tab."""
        tab_rows = [
            self._make_tab_row("knows", "Knows", entity_count=3),
            self._make_tab_row("family-of", "Family Of", entity_count=1),
        ]
        agg_rows = [self._make_agg_row(weight_sum=5, fact_count=2)]
        app, _ = self._make_app(tab_rows=tab_rows, agg_rows=agg_rows)
        resp = await _get(app, _CONCENTRATION_PATH)
        assert resp.status_code == 200
        tabs = resp.json()["predicate_tabs"]
        assert len(tabs) == 2
        knows_tab = next(t for t in tabs if t["predicate"] == "knows")
        assert knows_tab["entity_count"] == 3
        family_tab = next(t for t in tabs if t["predicate"] == "family-of")
        assert family_tab["entity_count"] == 1

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
        weight: int | None = None,
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
            "weight": weight,
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

    async def test_unranked_default_has_empty_remainders(self):
        """Without rank/per_predicate, all neighbours return and remainders is empty."""
        rows = [self._make_fact_row(predicate="knows", weight=w) for w in (1, 2, 3)]
        app, _ = self._make_app(fact_rows=rows)
        resp = await _get(app, _NEIGHBOURS_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["neighbours"]["knows"]) == 3
        assert body["remainders"] == {}

    async def test_ranked_truncation_top_n_by_weight_with_remainder(self):
        """rank=weight&per_predicate=6 → 6 highest-weight + remainder count."""
        # 40 'knows' neighbours with weights 1..40.
        rows = [
            self._make_fact_row(predicate="knows", weight=w, canonical_name=f"E{w}")
            for w in range(1, 41)
        ]
        app, _ = self._make_app(fact_rows=rows)
        resp = await _get(app, _NEIGHBOURS_PATH, rank="weight", per_predicate=6)
        assert resp.status_code == 200
        body = resp.json()
        group = body["neighbours"]["knows"]
        assert len(group) == 6
        # Highest weights first: 40, 39, ..., 35.
        assert [n["weight"] for n in group] == [40, 39, 38, 37, 36, 35]
        assert body["remainders"]["knows"] == 34

    async def test_ranked_truncation_per_predicate_independent(self):
        """Truncation applies per predicate group independently."""
        rows = [self._make_fact_row(predicate="knows", weight=w) for w in range(1, 11)]
        rows += [self._make_fact_row(predicate="family-of", weight=w) for w in range(1, 4)]
        app, _ = self._make_app(fact_rows=rows)
        resp = await _get(app, _NEIGHBOURS_PATH, rank="weight", per_predicate=6)
        body = resp.json()
        assert len(body["neighbours"]["knows"]) == 6
        assert body["remainders"]["knows"] == 4
        # Group below the cap returns whole and has no remainder entry.
        assert len(body["neighbours"]["family-of"]) == 3
        assert "family-of" not in body["remainders"]

    async def test_invalid_rank_value_rejected(self):
        """An unknown rank value is a 422 (only rank=weight is supported in v1)."""
        app, _ = self._make_app(fact_rows=[])
        resp = await _get(app, _NEIGHBOURS_PATH, rank="bogus")
        assert resp.status_code == 422

    async def test_per_predicate_requires_positive_int(self):
        """per_predicate must be >= 1."""
        app, _ = self._make_app(fact_rows=[])
        resp = await _get(app, _NEIGHBOURS_PATH, rank="weight", per_predicate=0)
        assert resp.status_code == 422


# ===========================================================================
# GET /entities/{entity_id}/facts — per-fact provenance grid (bu-mg4dk)
# ===========================================================================

_FACTS_PATH = f"/api/relationship/entities/{_ENT_ID}/facts"


class TestEntityFacts:
    """GET /entities/{id}/facts — drill endpoint (bu-tzvm6, entity v3).

    Keyset (cursor) pagination; ``predicate``/``validity``/``store`` filters;
    full provenance + ``staleness_band`` per row; owner-only authz.
    """

    def _make_fact_row(
        self,
        *,
        fact_id: UUID | None = None,
        predicate: str = "works-at",
        object_val: str = "Acme Corp",
        object_kind: str = "literal",
        src: str = "relationship",
        weight: int | None = 5,
        last_seen: datetime | None = None,
        validity: str = "active",
        created_at: datetime | None = None,
        store: str = "identity",
        staleness_band: str = "fresh",
    ) -> MagicMock:
        data = {
            "id": fact_id or uuid4(),
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
            "validity": validity,
            "created_at": created_at or _NOW,
            "store": store,
            # Derived in SQL by the staleness band expression in the real query.
            "staleness_band": staleness_band,
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
        narrative_rows: list | None = None,
    ) -> tuple[FastAPI, AsyncMock]:
        mock_pool = AsyncMock()
        owner_row = _make_owner_row() if owner_exists else None
        entity_val = 1 if entity_exists else None

        # GET /facts call sequence (keyset; no COUNT):
        #   1. fetchrow → owner-entity gate
        #   2. fetchval → entity existence check
        #   3. fetch → identity-store rows
        #   4. fetch → narrative-store rows (only when store=all)
        identity = fact_rows if fact_rows is not None else []
        mock_pool.fetchrow = AsyncMock(return_value=owner_row)
        mock_pool.fetchval = AsyncMock(return_value=entity_val)
        if narrative_rows is not None:
            mock_pool.fetch = AsyncMock(side_effect=[identity, narrative_rows])
        else:
            mock_pool.fetch = AsyncMock(return_value=identity)

        return _wire_app(mock_pool), mock_pool

    async def test_happy_path_returns_200_with_keyset_envelope(self):
        """Default call returns the keyset envelope {items, next_cursor, has_more}."""
        rows = [self._make_fact_row()]
        app, _ = self._make_app(fact_rows=rows)
        resp = await _get(app, _FACTS_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "next_cursor" in body
        assert body["has_more"] is False
        # No legacy offset/total fields.
        assert "total" not in body
        assert "offset" not in body

    async def test_response_shape_includes_provenance_and_staleness(self):
        """Each row carries full provenance + staleness_band + store label."""
        rows = [self._make_fact_row(weight=7, object_kind="literal", src="relationship")]
        app, _ = self._make_app(fact_rows=rows)
        resp = await _get(app, _FACTS_PATH)
        assert resp.status_code == 200
        fact = resp.json()["items"][0]
        assert fact["weight"] == 7
        assert fact["object_kind"] == "literal"
        assert fact["src"] == "relationship"
        assert "last_observed_at" in fact
        assert fact["staleness_band"] == "fresh"
        assert fact["store"] == "identity"

    async def test_default_filters_active_identity_rows(self):
        """No filters → only validity='active' identity-store rows are queried."""
        app, pool = self._make_app(fact_rows=[])
        await _get(app, _FACTS_PATH)
        sql = pool.fetch.call_args[0][0]
        assert "relationship.entity_facts" in sql
        assert "validity" in sql
        # Default store=identity must not query the narrative facts table.
        assert pool.fetch.await_count == 1

    async def test_predicate_filter_passed_to_query(self):
        """predicate= narrows the result set via a bound predicate filter."""
        app, pool = self._make_app(fact_rows=[])
        await _get(app, _FACTS_PATH, predicate="has-email")
        assert "has-email" in pool.fetch.call_args[0]

    async def test_validity_superseded_returns_history(self):
        """validity=superseded reaches superseded rows (Workbench history view)."""
        rows = [self._make_fact_row(validity="superseded")]
        app, pool = self._make_app(fact_rows=rows)
        resp = await _get(app, _FACTS_PATH, validity="superseded")
        assert resp.status_code == 200
        assert "superseded" in pool.fetch.call_args[0]
        assert resp.json()["items"][0]["validity"] == "superseded"

    async def test_invalid_validity_rejected(self):
        """An unknown validity value is a 422 (enum-guarded)."""
        app, _ = self._make_app(fact_rows=[])
        resp = await _get(app, _FACTS_PATH, validity="bogus")
        assert resp.status_code == 422

    async def test_store_all_layers_labeled_narrative_rows(self):
        """store=all appends labeled narrative-store rows after identity rows."""
        ident = [self._make_fact_row(predicate="works-at", store="identity")]
        narr = [
            self._make_fact_row(
                predicate="contact_note",
                object_val="met at conf",
                src="memory",
                store="narrative",
            )
        ]
        app, pool = self._make_app(fact_rows=ident, narrative_rows=narr)
        resp = await _get(app, _FACTS_PATH, store="all")
        assert resp.status_code == 200
        items = resp.json()["items"]
        stores = {i["store"] for i in items}
        assert stores == {"identity", "narrative"}
        # Two fetches issued: identity + narrative.
        assert pool.fetch.await_count == 2
        narr_sql = pool.fetch.await_args_list[1][0][0]
        assert "FROM facts" in narr_sql

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

    async def test_empty_facts_returns_empty_envelope(self):
        """Entity with no active triples returns an empty keyset envelope."""
        app, _ = self._make_app(fact_rows=[])
        resp = await _get(app, _FACTS_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["next_cursor"] is None
        assert body["has_more"] is False

    async def test_keyset_has_more_and_cursor_round_trip(self):
        """A full page yields has_more + a cursor that decodes to the last row's key."""
        import base64
        import json as _json

        # limit defaults to 20; return 21 rows so the handler trims to a page + cursor.
        base = _NOW
        rows = [
            self._make_fact_row(
                fact_id=uuid4(),
                created_at=base - timedelta(minutes=i),
            )
            for i in range(21)
        ]
        app, pool = self._make_app(fact_rows=rows)
        resp = await _get(app, _FACTS_PATH, limit=20)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 20
        assert body["has_more"] is True
        assert body["next_cursor"] is not None
        # Cursor must decode to the (created_at, id) of the last returned row.
        payload = _json.loads(base64.urlsafe_b64decode(body["next_cursor"].encode()))
        last = body["items"][-1]
        assert payload["id"] == last["id"]

        # Feeding the cursor back must add a keyset predicate bound to the values.
        app2, pool2 = self._make_app(fact_rows=[])
        await _get(app2, _FACTS_PATH, limit=20, cursor=body["next_cursor"])
        sql2 = pool2.fetch.call_args[0][0]
        assert "<" in sql2  # keyset comparison present

    async def test_malformed_cursor_rejected(self):
        """A malformed cursor is a 422 (decode failure surfaces as bad request)."""
        app, _ = self._make_app(fact_rows=[])
        resp = await _get(app, _FACTS_PATH, cursor="!!!not-base64!!!")
        assert resp.status_code == 422

    async def test_sql_uses_entity_facts_not_facts_table(self):
        """Default GET /entities/{id}/facts SQL must use relationship.entity_facts."""
        app, pool = self._make_app(fact_rows=[])
        await _get(app, _FACTS_PATH)
        sql = pool.fetch.call_args[0][0]
        assert "relationship.entity_facts" in sql
        assert "relationship.facts" not in sql


# ===========================================================================
# Activity binning (entity v3 — "Activity binning parameter")
# ===========================================================================


_VIEW_MARK_PATH = f"/api/relationship/entities/{_ENT_ID}/view-mark"
_DELTA_FACTS_PATH = f"/api/relationship/entities/{_ENT_ID}/delta-facts"
_CORE_DATES_PATH = f"/api/relationship/entities/{_ENT_ID}/core-dates"


class TestEntityActivityBinning:
    """GET /entities/{id}/activity?bins=daily&window=90d — sparkline source."""

    def _make_fact_row(self, *, last_seen: datetime) -> MagicMock:
        data = {
            "id": uuid4(),
            "predicate": "contact_note",
            "last_seen": last_seen,
            "created_at": last_seen,
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
    ) -> tuple[FastAPI, AsyncMock]:
        from butlers.api.deps import get_mcp_manager

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_make_owner_row() if owner_exists else None)
        mock_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)
        mock_pool.fetch = AsyncMock(return_value=fact_rows or [])

        mock_mcp = MagicMock()
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(
            return_value=self._make_mcp_result(chronicler_episodes or [])
        )
        mock_mcp.get_client = AsyncMock(return_value=mock_client)

        app = _wire_app(mock_pool)
        app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp
        return app, mock_pool

    async def test_bins_only_returns_dense_90_day_series(self):
        """bins_only=true returns exactly 90 daily bins including zero days."""
        # Activity on 3 distinct days within the window.
        rows = [
            self._make_fact_row(last_seen=_NOW),
            self._make_fact_row(last_seen=_NOW - timedelta(days=5)),
            self._make_fact_row(last_seen=_NOW - timedelta(days=40)),
        ]
        app, _ = self._make_app(fact_rows=rows, chronicler_episodes=[])
        resp = await _get(app, _ACTIVITY_PATH, bins="daily", window="90d", bins_only=True)
        assert resp.status_code == 200
        body = resp.json()
        assert "bins" in body
        # No merged stream when bins_only.
        assert "items" not in body
        assert len(body["bins"]) == 90
        # 3 days carry a count; the rest are zero (no day omitted).
        nonzero = [b for b in body["bins"] if b["count"] > 0]
        assert len(nonzero) == 3
        assert sum(b["count"] for b in body["bins"]) == 3

    async def test_bins_alongside_stream_when_not_bins_only(self):
        """bins=daily without bins_only returns both the merged stream and bins."""
        rows = [self._make_fact_row(last_seen=_NOW)]
        app, _ = self._make_app(fact_rows=rows, chronicler_episodes=[])
        resp = await _get(app, _ACTIVITY_PATH, bins="daily", window="90d")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "bins" in body
        assert len(body["bins"]) == 90

    async def test_bins_ascending_by_date(self):
        """The bin series is ordered ascending by date (oldest → newest)."""
        app, _ = self._make_app(fact_rows=[], chronicler_episodes=[])
        resp = await _get(app, _ACTIVITY_PATH, bins="daily", window="90d", bins_only=True)
        dates = [b["date"] for b in resp.json()["bins"]]
        assert dates == sorted(dates)

    async def test_window_30d_returns_30_bins(self):
        """The window param controls bin count (30d → 30 bins)."""
        app, _ = self._make_app(fact_rows=[], chronicler_episodes=[])
        resp = await _get(app, _ACTIVITY_PATH, bins="daily", window="30d", bins_only=True)
        assert len(resp.json()["bins"]) == 30

    async def test_chronicler_episode_counted_in_bins(self):
        """Chronicler episodes (MCP-sourced) contribute to the daily bins."""
        ep = {
            "id": str(uuid4()),
            "canonical_start_at": _NOW.isoformat(),
            "canonical_title": "Coffee",
        }
        app, pool = self._make_app(fact_rows=[], chronicler_episodes=[ep])
        resp = await _get(app, _ACTIVITY_PATH, bins="daily", window="90d", bins_only=True)
        assert sum(b["count"] for b in resp.json()["bins"]) == 1

    async def test_bins_owner_gated(self):
        app, _ = self._make_app(owner_exists=False)
        resp = await _get(app, _ACTIVITY_PATH, bins="daily", window="90d", bins_only=True)
        _assert_owner_required(resp)

    async def test_bins_missing_entity_404(self):
        app, _ = self._make_app(entity_exists=False)
        resp = await _get(app, _ACTIVITY_PATH, bins="daily", window="90d", bins_only=True)
        assert resp.status_code == 404

    async def test_default_call_unchanged_no_bins(self):
        """Without bins=daily the response is the legacy merged stream (no bins)."""
        rows = [self._make_fact_row(last_seen=_NOW)]
        app, _ = self._make_app(fact_rows=rows, chronicler_episodes=[])
        resp = await _get(app, _ACTIVITY_PATH)
        body = resp.json()
        assert "items" in body
        assert "bins" not in body


class TestEntityViewMark:
    """POST /entities/{id}/view-mark — upsert the per-entity view mark."""

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        entity_exists: bool = True,
        marked_at: datetime | None = None,
    ) -> tuple[FastAPI, AsyncMock]:
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_make_owner_row() if owner_exists else None)
        mock_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)

        mark_row = None
        if entity_exists:
            data = {"entity_id": _ENT_ID, "marked_at": marked_at or _NOW}
            mark_row = MagicMock()
            mark_row.__getitem__ = MagicMock(side_effect=lambda k: data[k])

        async def _fetchrow(sql, *args):
            # Owner gate uses fetchrow on public.entities; the upsert RETURNING
            # also uses fetchrow on entity_view_marks. Discriminate by table.
            if "entity_view_marks" in sql:
                return mark_row
            return _make_owner_row() if owner_exists else None

        mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        return _wire_app(mock_pool), mock_pool

    async def test_view_mark_upserts_and_returns_marked_at(self):
        app, pool = self._make_app()
        resp = await _post(app, _VIEW_MARK_PATH)
        assert resp.status_code in (200, 201)
        body = resp.json()
        assert body["entity_id"] == str(_ENT_ID)
        assert body["marked_at"] is not None
        # The write must target the view-marks table with an upsert.
        upsert_sql = next(
            c[0][0] for c in pool.fetchrow.call_args_list if "entity_view_marks" in c[0][0]
        )
        assert "ON CONFLICT" in upsert_sql.upper()

    async def test_view_mark_idempotent_second_call(self):
        """Posting twice keeps a single mark (upsert, not insert) — both succeed."""
        app, _ = self._make_app()
        r1 = await _post(app, _VIEW_MARK_PATH)
        r2 = await _post(app, _VIEW_MARK_PATH)
        assert r1.status_code in (200, 201)
        assert r2.status_code in (200, 201)

    async def test_view_mark_owner_gated(self):
        app, _ = self._make_app(owner_exists=False)
        resp = await _post(app, _VIEW_MARK_PATH)
        _assert_owner_required(resp)

    async def test_view_mark_missing_entity_404(self):
        app, _ = self._make_app(entity_exists=False)
        resp = await _post(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/view-mark")
        assert resp.status_code == 404


class TestEntityDeltaFacts:
    """GET /entities/{id}/delta-facts — facts changed since the view mark."""

    def _make_delta_row(
        self,
        *,
        store: str = "identity",
        predicate: str = "has-email",
        changed_at: datetime | None = None,
    ) -> MagicMock:
        data = {
            "id": uuid4(),
            "subject": _ENT_ID,
            "predicate": predicate,
            "object": "alice@example.com",
            "object_kind": "literal",
            "src": "relationship",
            "conf": 1.0,
            "validity": "active",
            "created_at": _NOW,
            "changed_at": changed_at or _NOW,
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        entity_exists: bool = True,
        marked_at: datetime | None = _NOW - timedelta(days=10),
        identity_rows: list | None = None,
        narrative_rows: list | None = None,
    ) -> tuple[FastAPI, AsyncMock]:
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)

        async def _fetchrow(sql, *args):
            if "entity_view_marks" in sql:
                if marked_at is None:
                    return None
                data = {"marked_at": marked_at}
                row = MagicMock()
                row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
                return row
            return _make_owner_row() if owner_exists else None

        mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        # fetch sequence: identity-store delta rows, then narrative-store rows.
        mock_pool.fetch = AsyncMock(side_effect=[identity_rows or [], narrative_rows or []])
        return _wire_app(mock_pool), mock_pool

    async def test_two_new_facts_reported_with_mark(self):
        """An entity marked 10 days ago with 2 facts since reports both + the mark."""
        rows = [self._make_delta_row(), self._make_delta_row(predicate="has-phone")]
        app, _ = self._make_app(identity_rows=rows, narrative_rows=[])
        resp = await _get(app, _DELTA_FACTS_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["marked_at"] is not None
        assert len(body["items"]) == 2

    async def test_first_visit_no_mark_empty_delta(self):
        """No view-mark row → marked_at null and no delta items (first-visit case)."""
        app, pool = self._make_app(marked_at=None)
        resp = await _get(app, _DELTA_FACTS_PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["marked_at"] is None
        assert body["items"] == []
        # No fact query is issued when there is no mark to diff against.
        assert pool.fetch.await_count == 0

    async def test_delta_does_not_move_the_mark(self):
        """Reading the delta must not write the view mark (no upsert SQL issued)."""
        rows = [self._make_delta_row()]
        app, pool = self._make_app(identity_rows=rows, narrative_rows=[])
        await _get(app, _DELTA_FACTS_PATH)
        for call in pool.fetchrow.call_args_list:
            assert "INSERT" not in call[0][0].upper()
        # No execute-style write on the view-marks table.
        for call in pool.execute.call_args_list:
            assert "entity_view_marks" not in call[0][0]

    async def test_identity_change_predicate_uses_greatest(self):
        """Identity delta SQL diffs GREATEST(created_at, updated_at) against the mark."""
        app, pool = self._make_app(identity_rows=[], narrative_rows=[])
        await _get(app, _DELTA_FACTS_PATH)
        identity_sql = pool.fetch.await_args_list[0][0][0]
        assert "GREATEST" in identity_sql.upper()
        assert "updated_at" in identity_sql
        assert "relationship.entity_facts" in identity_sql

    async def test_narrative_change_predicate_uses_last_confirmed(self):
        """Narrative delta SQL diffs GREATEST(created_at, COALESCE(last_confirmed_at,...))."""
        app, pool = self._make_app(identity_rows=[], narrative_rows=[])
        await _get(app, _DELTA_FACTS_PATH)
        narrative_sql = pool.fetch.await_args_list[1][0][0]
        assert "last_confirmed_at" in narrative_sql
        assert "FROM facts" in narrative_sql

    async def test_delta_owner_gated(self):
        app, _ = self._make_app(owner_exists=False)
        resp = await _get(app, _DELTA_FACTS_PATH)
        _assert_owner_required(resp)

    async def test_delta_missing_entity_404(self):
        app, _ = self._make_app(entity_exists=False)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/delta-facts")
        assert resp.status_code == 404


class TestEntityCoreDates:
    """GET /entities/{id}/core-dates — server-extracted date-kind facts."""

    def _make_date_fact_row(
        self,
        *,
        predicate: str = "has-birthday",
        value: str = "1990-04-12",
        staleness_band: str = "fresh",
    ) -> MagicMock:
        data = {
            "id": uuid4(),
            "predicate": predicate,
            "object": value,
            "src": "relationship",
            "conf": 1.0,
            "verified": True,
            "staleness_band": staleness_band,
        }
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    def _make_app(
        self,
        *,
        owner_exists: bool = True,
        entity_exists: bool = True,
        date_rows: list | None = None,
    ) -> tuple[FastAPI, AsyncMock]:
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_make_owner_row() if owner_exists else None)
        mock_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)
        mock_pool.fetch = AsyncMock(return_value=date_rows or [])
        return _wire_app(mock_pool), mock_pool

    async def test_birthday_surfaces_with_next_occurrence(self):
        """An active has-birthday fact yields next_occurrence + days_until."""
        app, _ = self._make_app(date_rows=[self._make_date_fact_row(value="1990-04-12")])
        resp = await _get(app, _CORE_DATES_PATH)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        row = items[0]
        assert row["predicate"] == "has-birthday"
        assert row["month"] == 4
        assert row["day"] == 12
        assert row["next_occurrence"].endswith("-04-12")
        assert isinstance(row["days_until"], int)
        assert row["days_until"] >= 0

    async def test_core_date_carries_provenance(self):
        """Each core-date row carries provenance fields (src/conf/verified/staleness)."""
        app, _ = self._make_app(date_rows=[self._make_date_fact_row()])
        row = (await _get(app, _CORE_DATES_PATH)).json()["items"][0]
        assert row["src"] == "relationship"
        assert row["conf"] == 1.0
        assert row["verified"] is True
        assert row["staleness_band"] == "fresh"

    async def test_partial_date_without_year_supported(self):
        """A --MM-DD partial date (year unknown) still yields a next occurrence."""
        app, _ = self._make_app(date_rows=[self._make_date_fact_row(value="--12-25")])
        items = (await _get(app, _CORE_DATES_PATH)).json()["items"]
        assert len(items) == 1
        assert items[0]["month"] == 12
        assert items[0]["day"] == 25
        assert items[0]["year"] is None

    async def test_unparseable_date_skipped(self):
        """A malformed date object is skipped rather than 500-ing."""
        app, _ = self._make_app(date_rows=[self._make_date_fact_row(value="not-a-date")])
        resp = await _get(app, _CORE_DATES_PATH)
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    async def test_ordered_by_days_until(self):
        """Items are sorted by days_until ascending (soonest first)."""
        # Build two birthdays: one earlier in the year, one later.
        rows = [
            self._make_date_fact_row(value="1990-12-31"),
            self._make_date_fact_row(value="1990-01-01"),
        ]
        app, _ = self._make_app(date_rows=rows)
        items = (await _get(app, _CORE_DATES_PATH)).json()["items"]
        days = [i["days_until"] for i in items]
        assert days == sorted(days)

    async def test_query_filters_date_kind_predicates_server_side(self):
        """Extraction is server-side and registry-driven (two queries).

        The endpoint first reads the eligible date-kind predicates from
        ``relationship.entity_predicate_registry`` (registry-driven, #2230), then
        filters the identity store to exactly those predicates. The predicate set
        is passed as a bound array param (``= ANY($2)``), not interpolated, so
        assert it appears among the second query's bound arguments and that the
        second query targets the identity store.
        """
        # A date-fact row doubles as the registry-predicate source: the first
        # fetch (_fetch_date_kind_predicates) reads r["predicate"] -> ["has-birthday"],
        # which makes the second (entity_facts) query run with that bound set.
        app, pool = self._make_app(date_rows=[self._make_date_fact_row()])
        await _get(app, _CORE_DATES_PATH)

        fetch_sqls = [c[0][0] for c in pool.fetch.await_args_list]
        # Query 1: registry-driven predicate selection.
        assert any("relationship.entity_predicate_registry" in sql for sql in fetch_sqls), (
            "Date predicates must be sourced from the predicate registry, not hardcoded"
        )

        # Query 2: server-side identity-store filter, bound to the registry predicates.
        facts_calls = [
            c for c in pool.fetch.await_args_list if "relationship.entity_facts" in str(c[0][0])
        ]
        assert facts_calls, "Core-dates must filter the identity store server-side"
        facts_call = facts_calls[0]
        assert "ANY(" in facts_call[0][0]
        # The date-kind predicate set is a bound argument to the query.
        bound_predicates = next(a for a in facts_call[0][1:] if isinstance(a, list))
        assert "has-birthday" in bound_predicates

    async def test_core_dates_owner_gated(self):
        app, _ = self._make_app(owner_exists=False)
        resp = await _get(app, _CORE_DATES_PATH)
        _assert_owner_required(resp)

    async def test_core_dates_missing_entity_404(self):
        app, _ = self._make_app(entity_exists=False)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/core-dates")
        assert resp.status_code == 404


# ===========================================================================
# POST /entities/compare — structural diff (entity v3, relationship-merge-review)
# POST /entities/dismiss-pair — dismissal suppression key
# ===========================================================================


def _make_compare_summary_row(
    *,
    entity_id: UUID,
    canonical_name: str = "Alice",
    entity_type: str = "person",
    aliases: list[str] | None = None,
    tier: int | None = None,
) -> MagicMock:
    """Row returned by _COMPARE_SUMMARY_SQL (one per entity)."""
    data = {
        "id": entity_id,
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "tier": tier,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _make_identity_compare_row(
    *,
    subject: UUID,
    predicate: str,
    object_val: str,
    object_kind: str = "literal",
    src: str = "relationship",
    conf: float = 1.0,
    verified: bool = True,
    primary: bool | None = True,
    observed_at: datetime | None = None,
    last_seen: datetime | None = None,
    staleness_band: str = "fresh",
    fact_id: UUID | None = None,
) -> MagicMock:
    """Row for the identity-store compare fetch (relationship.entity_facts)."""
    data = {
        "id": fact_id or uuid4(),
        "subject": subject,
        "predicate": predicate,
        "object": object_val,
        "object_kind": object_kind,
        "src": src,
        "conf": conf,
        "verified": verified,
        "primary": primary,
        "observed_at": observed_at,
        "last_seen": last_seen,
        "staleness_band": staleness_band,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _make_narrative_compare_row(
    *,
    subject: UUID,
    predicate: str = "contact_note",
    object_val: str = "met at conference",
    src: str = "memory",
    conf: float = 0.9,
    observed_at: datetime | None = None,
    staleness_band: str = "fresh",
    fact_id: UUID | None = None,
) -> MagicMock:
    """Row for the narrative-store compare fetch (memory-module facts)."""
    data = {
        "id": fact_id or uuid4(),
        "subject": subject,
        "predicate": predicate,
        "object": object_val,
        "object_kind": "literal",
        "src": src,
        "conf": conf,
        "verified": False,
        "primary": None,
        "observed_at": observed_at,
        "staleness_band": staleness_band,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _make_single_predicate_row(predicate: str) -> MagicMock:
    data = {"predicate": predicate}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _wire_compare_app(
    *,
    owner_exists: bool = True,
    summary_a: MagicMock | None = None,
    summary_b: MagicMock | None = None,
    identity_a: list | None = None,
    identity_b: list | None = None,
    narrative_a: list | None = None,
    narrative_b: list | None = None,
    classify_a: MagicMock | None = None,
    classify_b: MagicMock | None = None,
    single_predicates: list | None = None,
    entity_exists: bool = True,
) -> tuple[FastAPI, AsyncMock]:
    """Wire an app for the compare/dismiss-pair endpoints.

    fetchrow sequence (compare): owner, summary_a, summary_b, classify_a, classify_b
      (summary rows are None when entity_exists=False → the snapshot raises 404)
    fetch sequence (compare): identity_a, identity_b, narrative_a, narrative_b,
                              single_predicate_rows
    dismiss-pair additionally calls fetchval (INSERT merge_reviews RETURNING id).
    """
    owner_row = _make_owner_row() if owner_exists else None
    if entity_exists:
        sa = summary_a if summary_a is not None else _make_compare_summary_row(entity_id=_ENT_ID)
        sb = summary_b if summary_b is not None else _make_compare_summary_row(entity_id=_ENT_ID_B)
    else:
        sa = None
        sb = None
    ca = classify_a if classify_a is not None else _make_classify_row()
    cb = classify_b if classify_b is not None else _make_classify_row()
    preds = single_predicates if single_predicates is not None else []

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(side_effect=[owner_row, sa, sb, ca, cb])
    mock_pool.fetch = AsyncMock(
        side_effect=[
            identity_a or [],
            identity_b or [],
            narrative_a or [],
            narrative_b or [],
            preds,
        ]
    )
    # fetchval is used by dismiss-pair for the INSERT ... RETURNING id.
    mock_pool.fetchval = AsyncMock(return_value=uuid4())
    return _wire_app(mock_pool), mock_pool


class TestCompareEntities:
    """POST /entities/compare — structural diff (relationship-merge-review)."""

    async def test_owner_gate_returns_403(self):
        app, _ = _wire_compare_app(owner_exists=False)
        resp = await _post(
            app, _COMPARE_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_ENT_ID_B)}
        )
        _assert_owner_required(resp)

    async def test_same_entity_returns_422(self):
        app, _ = _wire_compare_app()
        resp = await _post(app, _COMPARE_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_ENT_ID)})
        assert resp.status_code == 422

    async def test_blocks_carry_facts_from_both_stores_with_provenance(self):
        """a/b blocks carry identity + narrative facts, each with full provenance."""
        ident_a = [
            _make_identity_compare_row(subject=_ENT_ID, predicate="has-email", object_val="a@x.com")
        ]
        narr_a = [_make_narrative_compare_row(subject=_ENT_ID, staleness_band="aging")]
        app, _ = _wire_compare_app(
            identity_a=ident_a,
            identity_b=[],
            narrative_a=narr_a,
            narrative_b=[],
        )
        resp = await _post(
            app, _COMPARE_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_ENT_ID_B)}
        )
        assert resp.status_code == 200
        body = resp.json()
        a_ident = body["a"]["identity_facts"][0]
        assert a_ident["store"] == "identity"
        assert a_ident["staleness_band"] == "fresh"
        assert a_ident["src"] == "relationship"
        assert "conf" in a_ident and "verified" in a_ident and "observed_at" in a_ident
        a_narr = body["a"]["narrative_facts"][0]
        assert a_narr["store"] == "narrative"
        assert a_narr["staleness_band"] == "aging"
        # Narrative rows omit last_seen (no such column).
        assert a_narr["last_seen"] is None

    async def test_narrative_facts_exclude_interaction_log(self):
        """Compare narrative SQL excludes interaction-log facts (bu-xzxw4).

        Interaction rows are an unbounded temporal log that floods the merge
        dialog and never conflicts on merge; the compare narrative fetch must
        filter ``predicate NOT LIKE 'interaction_%'``.
        """
        app, pool = _wire_compare_app()
        resp = await _post(
            app, _COMPARE_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_ENT_ID_B)}
        )
        assert resp.status_code == 200
        # fetch order: identity_a, identity_b, narrative_a, narrative_b, preds.
        narrative_sql = pool.fetch.await_args_list[2][0][0]
        assert "FROM facts" in narrative_sql
        assert "NOT LIKE 'interaction_%'" in narrative_sql

    async def test_shared_holds_identical_identity_pairs_only(self):
        """shared = identity rows with identical (predicate, object) on BOTH; no narrative."""
        ident_a = [
            _make_identity_compare_row(
                subject=_ENT_ID, predicate="has-email", object_val="alice@x.com"
            )
        ]
        ident_b = [
            _make_identity_compare_row(
                subject=_ENT_ID_B, predicate="has-email", object_val="alice@x.com"
            )
        ]
        narr_a = [_make_narrative_compare_row(subject=_ENT_ID)]
        narr_b = [_make_narrative_compare_row(subject=_ENT_ID_B)]
        app, _ = _wire_compare_app(
            identity_a=ident_a,
            identity_b=ident_b,
            narrative_a=narr_a,
            narrative_b=narr_b,
            single_predicates=[_make_single_predicate_row("has-birthday")],
        )
        resp = await _post(
            app, _COMPARE_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_ENT_ID_B)}
        )
        assert resp.status_code == 200
        shared = resp.json()["shared"]
        # The pair is emitted once per entity (A row + B row).
        assert len(shared) == 2
        assert {s["entity_id"] for s in shared} == {str(_ENT_ID), str(_ENT_ID_B)}
        assert all(s["predicate"] == "has-email" for s in shared)
        assert all(s["store"] == "identity" for s in shared)

    async def test_divergent_only_single_cardinality_predicates(self):
        """divergent = single-cardinality predicates with differing objects only."""
        ident_a = [
            _make_identity_compare_row(
                subject=_ENT_ID, predicate="has-birthday", object_val="1990-01-01"
            ),
            # Multi-valued: different emails MUST NOT diverge (three-emails-three-rows).
            _make_identity_compare_row(
                subject=_ENT_ID, predicate="has-email", object_val="a@x.com"
            ),
        ]
        ident_b = [
            _make_identity_compare_row(
                subject=_ENT_ID_B, predicate="has-birthday", object_val="1991-02-02"
            ),
            _make_identity_compare_row(
                subject=_ENT_ID_B, predicate="has-email", object_val="b@x.com"
            ),
        ]
        app, _ = _wire_compare_app(
            identity_a=ident_a,
            identity_b=ident_b,
            single_predicates=[_make_single_predicate_row("has-birthday")],
        )
        resp = await _post(
            app, _COMPARE_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_ENT_ID_B)}
        )
        assert resp.status_code == 200
        divergent = resp.json()["divergent"]
        preds = {d["predicate"] for d in divergent}
        assert preds == {"has-birthday"}
        # The differing has-email rows are NOT divergences (multi-valued).
        assert "has-email" not in preds
        # Both sides' conflicting birthday rows appear.
        assert {d["object"] for d in divergent} == {"1990-01-01", "1991-02-02"}

    async def test_same_single_value_not_divergent(self):
        """A single-cardinality predicate with the SAME value on both is not divergent."""
        ident_a = [
            _make_identity_compare_row(
                subject=_ENT_ID, predicate="has-birthday", object_val="1990-01-01"
            )
        ]
        ident_b = [
            _make_identity_compare_row(
                subject=_ENT_ID_B, predicate="has-birthday", object_val="1990-01-01"
            )
        ]
        app, _ = _wire_compare_app(
            identity_a=ident_a,
            identity_b=ident_b,
            single_predicates=[_make_single_predicate_row("has-birthday")],
        )
        resp = await _post(
            app, _COMPARE_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_ENT_ID_B)}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["divergent"] == []
        # Identical birthday IS shared evidence.
        assert len(body["shared"]) == 2

    async def test_entity_block_tier_nullable(self):
        """The entity summary carries a nullable tier."""
        sa = _make_compare_summary_row(entity_id=_ENT_ID, tier=5)
        sb = _make_compare_summary_row(entity_id=_ENT_ID_B, tier=None)
        app, _ = _wire_compare_app(summary_a=sa, summary_b=sb)
        resp = await _post(
            app, _COMPARE_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_ENT_ID_B)}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["a"]["entity"]["tier"] == 5
        assert body["b"]["entity"]["tier"] is None

    async def test_unknown_entity_returns_404(self):
        """An unknown/tombstoned entity yields 404."""
        app, _ = _wire_compare_app(entity_exists=False)
        resp = await _post(
            app, _COMPARE_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_MISSING_ENT_ID)}
        )
        assert resp.status_code == 404


class TestDismissPair:
    """POST /entities/dismiss-pair — writes a dismissed merge_reviews row."""

    async def test_owner_gate_returns_403(self):
        app, _ = _wire_compare_app(owner_exists=False)
        resp = await _post(
            app, _DISMISS_PAIR_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_ENT_ID_B)}
        )
        _assert_owner_required(resp)

    async def test_dismiss_writes_merge_review_row(self):
        """A dismissal inserts a merge_reviews row (outcome=dismissed) and echoes evidence."""
        ident_a = [
            _make_identity_compare_row(
                subject=_ENT_ID, predicate="has-email", object_val="alice@x.com"
            )
        ]
        ident_b = [
            _make_identity_compare_row(
                subject=_ENT_ID_B, predicate="has-email", object_val="alice@x.com"
            )
        ]
        app, pool = _wire_compare_app(identity_a=ident_a, identity_b=ident_b)
        resp = await _post(
            app, _DISMISS_PAIR_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_ENT_ID_B)}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"] == "dismissed"
        assert "review_id" in body
        # The shared evidence snapshot is echoed.
        assert any(s["object"] == "alice@x.com" for s in body["shared_facts"])
        # An INSERT into merge_reviews was issued.
        insert_calls = [c for c in pool.fetchval.await_args_list if "merge_reviews" in str(c[0][0])]
        assert insert_calls, "Expected an INSERT into relationship.merge_reviews"
        assert "dismissed" in insert_calls[0][0]

    async def test_same_entity_returns_422(self):
        app, _ = _wire_compare_app()
        resp = await _post(
            app, _DISMISS_PAIR_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_ENT_ID)}
        )
        assert resp.status_code == 422

    async def test_unknown_entity_returns_404(self):
        app, _ = _wire_compare_app(entity_exists=False)
        resp = await _post(
            app, _DISMISS_PAIR_PATH, {"entity_a": str(_ENT_ID), "entity_b": str(_MISSING_ENT_ID)}
        )
        assert resp.status_code == 404


class TestMergeWritesAuditRow:
    """POST /entities/{id}/merge writes a merge_reviews row regardless of entry path."""

    def _make_lock_row(self, entity_id: UUID, metadata: dict | None = None) -> MagicMock:
        data = {"id": entity_id, "metadata": metadata or {}}
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
        return row

    async def test_merge_writes_merged_audit_row(self):
        """A successful merge writes a merge_reviews row with outcome='merged'."""
        owner_row = _make_owner_row()
        summary_a = _make_compare_summary_row(entity_id=_ENT_ID)
        summary_b = _make_compare_summary_row(entity_id=_ENT_ID_B)
        classify_a = _make_classify_row()
        classify_b = _make_classify_row()

        # merge_entities fetchrow sequence: owner gate, then the pre-transaction
        # snapshot (summary_a, summary_b, classify_a, classify_b).
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            side_effect=[owner_row, summary_a, summary_b, classify_a, classify_b]
        )
        # snapshot fetch: identity_a, identity_b, narrative_a, narrative_b, single-preds.
        mock_pool.fetch = AsyncMock(side_effect=[[], [], [], [], []])

        lock_rows = sorted(
            [self._make_lock_row(_ENT_ID), self._make_lock_row(_ENT_ID_B)],
            key=lambda r: r["id"],
        )
        mock_conn = AsyncMock()

        # conn.fetch serves the entity FOR UPDATE lock AND the memory-module
        # ``facts`` repoint scans (bu-j820n.1); only the lock returns entity rows,
        # the facts scans return [] (no narrative facts in this audit-row test).
        async def _conn_fetch(query, *args):
            return lock_rows if "FOR UPDATE" in query else []

        mock_conn.fetch = AsyncMock(side_effect=_conn_fetch)
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        # In-transaction conn.fetchval call order (#2230): subject-rewire count,
        # object-rewire count, then the merge_reviews INSERT ... RETURNING id.
        # The two counts are ints; the audit row returns a UUID review id.
        mock_conn.fetchval = AsyncMock(side_effect=[0, 0, uuid4()])
        mock_txn = AsyncMock()
        mock_txn.__aenter__ = AsyncMock(return_value=None)
        mock_txn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.transaction = MagicMock(return_value=mock_txn)

        @asynccontextmanager
        async def _acquire():
            yield mock_conn

        mock_pool.acquire = MagicMock(return_value=_acquire())

        app = _wire_app(mock_pool)
        resp = await _post(
            app,
            _MERGE_PATH,
            {"entityA": str(_ENT_ID), "entityB": str(_ENT_ID_B), "keepAs": "B"},
        )
        assert resp.status_code == 200, resp.text
        # The audit row is written in-transaction on the acquired connection
        # (#2230: "writing the audit row on `conn` (not `pool`) closes the crash
        # window where a committed merge could leave no audit trail"), so the
        # INSERT lands on mock_conn.fetchval — never on the pool.
        insert_calls = [
            c for c in mock_conn.fetchval.await_args_list if "merge_reviews" in str(c[0][0])
        ]
        assert insert_calls, "Merge MUST write a merge_reviews audit row (in-transaction)"
        assert "merged" in insert_calls[0][0]
        # And it must NOT leak onto the pool — the row commits atomically with the merge.
        assert not [
            c for c in mock_pool.fetchval.await_args_list if "merge_reviews" in str(c[0][0])
        ], "Merge audit row must be written on the in-tx connection, not the pool"


class TestQueueDismissedPairSuppression:
    """The queue + classify SQL suppress dismissed pairs (relationship-entity-lifecycle)."""

    def test_queue_sql_references_dismissed_suppression(self):
        """The queue duplicate-detection SQL filters out dismissed pairs.

        Confirms the suppression clause is wired into the duplicate-candidate
        derivation (deterministic, no LLM) in BOTH the queue dup-detected bucket
        and ``_classify_entity_state``.
        """
        from pathlib import Path

        router_src = (Path(__file__).resolve().parents[1] / "api" / "router.py").read_text(
            encoding="utf-8"
        )
        assert "_dismissed_pair_suppression_sql" in router_src
        # def + 2 call sites (queue bucket + classify CTE).
        assert router_src.count("_dismissed_pair_suppression_sql(") >= 3
        assert "merge_reviews mr" in router_src
        assert "jsonb_array_elements(mr.shared_facts)" in router_src
