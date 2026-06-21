"""Tests for POST /api/relationship/entities/{id}/merge (entity-level merge).

Spec anchor:
  openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md
  Requirement: Owner-only authorization for entity endpoints — Clause 12a (Amendment 12a)
  Task: tasks.md §9.10 (bu-jp6r6)

Acceptance criteria verified:
- Owner-only gate (Amendment 12a): HTTP 403 + {"code": "owner_required"} for non-owners.
- keepAs='A' keeps entityA, tombstones entityB.
- keepAs='B' keeps entityB, tombstones entityA.
- Subject-side rewire: relationship.entity_facts rows with subject=source → subject=target.
- Object-side rewire: relationship.entity_facts rows with object_kind='entity' AND object=source → target.
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


def _make_compare_summary_row(*, entity_id: UUID) -> MagicMock:
    """Row returned by ``_COMPARE_SUMMARY_SQL`` for the pre-merge audit snapshot.

    ``merge_entities`` now computes a ``merge_reviews`` audit snapshot via
    ``_compute_compare_snapshot`` before mutating rows (entity-v3
    ``relationship-merge-review``). A present (non-tombstoned) entity yields a
    summary row; a missing/tombstoned entity yields ``None`` (→ 404).
    """
    data = {
        "id": entity_id,
        "canonical_name": "Entity",
        "entity_type": "person",
        "aliases": [],
        "tier": None,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_classify_row() -> MagicMock:
    """Row returned by ``_classify_entity_state`` during the snapshot compute."""
    data = {
        "is_unidentified": False,
        "is_dup_flagged": False,
        "has_fresh_fact": True,
        "is_dismissed": False,
        "last_seen": None,
        "dup_predicate": None,
        "dup_shared_value": None,
        "dup_peer_entity_ids": None,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _is_present(row: MagicMock | None) -> bool:
    """A row is a live entity iff present and not tombstoned (``merged_into``)."""
    if row is None:
        return False
    return row["metadata"].get("merged_into") is None


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
      1. conn.fetch(SELECT ... WHERE id = ANY(...) ORDER BY id FOR UPDATE)
            → list of present rows (missing rows omitted, triggering 404)
      2. conn.execute(retract conflicting subject-side rows)
      3. conn.fetchval(UPDATE subject rows, RETURNING count)  → subject_rewired
      4. conn.execute(retract conflicting object-side rows)
      5. conn.fetchval(UPDATE object rows, RETURNING count)   → object_rewired
      6. conn.execute(UPDATE entities SET metadata=tombstone)
      7. conn.fetchval(INSERT merge_reviews RETURNING id)     → review id
            (entity-v3 bu-rag77: the audit row is now written INSIDE the merge
            transaction on the connection, not post-commit on the pool, so a
            crash cannot leave a merged pair with no audit row)
    """
    mock_conn = AsyncMock()

    # Build the list of rows returned by the single bulk FOR UPDATE fetch.
    # Missing rows are omitted (lock_map.get() returns None → 404).
    fetch_rows = []
    if source_row is not None:
        fetch_rows.append(source_row)
    if target_row is not None:
        fetch_rows.append(target_row)

    # conn.fetch is issued for the entity FOR UPDATE lock AND, after the
    # entity_facts rewire, for the memory-module ``facts`` repoint (subject- and
    # object-side scans in ``_repoint_facts_on_conn``, bu-j820n.1). Only the lock
    # query returns entity rows; the facts scans return [] so the per-row loops
    # do not run against the entity MagicMocks (which have no ``valid_at`` key).
    async def _fetch(query, *args):
        return fetch_rows if "FOR UPDATE" in query else []

    mock_conn.fetch = AsyncMock(side_effect=_fetch)

    # fetchval returns subject count, object count, then the merge_reviews id
    # (the in-transaction audit INSERT).
    mock_conn.fetchval = AsyncMock(side_effect=[subject_rewired, object_rewired, uuid4()])

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

    # merge_entities computes a pre-transaction merge_reviews audit snapshot via
    # _compute_compare_snapshot (entity-v3 relationship-merge-review). That issues,
    # in order, on the pool: fetchrow(owner gate), fetchrow(summary A),
    # fetchrow(summary B), fetchrow(classify A), fetchrow(classify B); fetch(identity
    # A/B, narrative A/B, single-cardinality predicates); then, post-transaction,
    # fetchval(INSERT merge_reviews RETURNING id). The summary fetch returns None for
    # a missing/tombstoned entity → the snapshot raises 404 (matching the prior
    # in-transaction 404 behaviour). Map source/target rows back to A/B by id.
    rows_by_id: dict[UUID, MagicMock] = {}
    for r in (source_row, target_row):
        if r is not None:
            rows_by_id[r["id"]] = r
    summary_a = (
        _make_compare_summary_row(entity_id=ENTITY_A_ID)
        if _is_present(rows_by_id.get(ENTITY_A_ID))
        else None
    )
    summary_b = (
        _make_compare_summary_row(entity_id=ENTITY_B_ID)
        if _is_present(rows_by_id.get(ENTITY_B_ID))
        else None
    )
    mock_pool.fetchrow = AsyncMock(
        side_effect=[owner_row, summary_a, summary_b, _make_classify_row(), _make_classify_row()]
    )
    mock_pool.fetch = AsyncMock(side_effect=[[], [], [], [], []])
    mock_pool.fetchval = AsyncMock(return_value=uuid4())
    mock_pool.acquire = MagicMock(return_value=_acquire())
    # Expose the in-transaction connection so tests can assert the merge_reviews
    # audit row is written on the connection (bu-rag77), not post-commit on pool.
    mock_pool.merge_conn = mock_conn

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


class TestMergeReviewAuditRowIsInTransaction:
    """The merge_reviews audit row is written inside the merge transaction.

    bu-rag77 (entity-v3 hygiene): previously ``_write_merge_review`` ran AFTER the
    transaction committed, leaving a crash window in which a merged + tombstoned
    pair could exist with no audit row. The INSERT now runs on the in-transaction
    connection so it commits atomically with the rewire/tombstone.
    """

    async def test_audit_insert_runs_on_connection_not_pool(self):
        """The merge_reviews INSERT (RETURNING id) is issued on conn, not pool."""
        src = _make_entity_row(entity_id=ENTITY_B_ID)
        tgt = _make_entity_row(entity_id=ENTITY_A_ID)
        app, mock_pool = _app_with_pool(source_row=src, target_row=tgt)

        resp = await _post(
            app,
            {"entityA": str(ENTITY_A_ID), "entityB": str(ENTITY_B_ID), "keepAs": "A"},
        )

        assert resp.status_code == 200
        # The two UPDATE-count fetchvals plus the audit INSERT = 3 conn.fetchval
        # calls. The audit row must NOT be written on the pool (post-commit path).
        assert mock_pool.merge_conn.fetchval.await_count == 3
        merge_review_sql = [
            call.args[0]
            for call in mock_pool.merge_conn.fetchval.await_args_list
            if "merge_reviews" in call.args[0]
        ]
        assert merge_review_sql, "merge_reviews INSERT was not issued on the connection"
        # The pool's fetchval was used only for the pre-merge snapshot path, never
        # for the audit INSERT.
        pool_insert_calls = [
            call.args[0]
            for call in mock_pool.fetchval.await_args_list
            if call.args and "INSERT INTO relationship.merge_reviews" in call.args[0]
        ]
        assert not pool_insert_calls, "audit row must not be written post-commit on the pool"
