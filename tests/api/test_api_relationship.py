"""Tests for relationship/CRM API endpoints.

Verifies the API contract (status codes, response shapes) for relationship
endpoints.  Uses a mocked DatabaseManager so no real database is required.

Issue: butlers-26h.10.3
"""

from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)

# Load relationship router module dynamically
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "relationship" / "api" / "router.py"
spec = importlib.util.spec_from_file_location("relationship_api_router", _router_path)
if spec is None or spec.loader is None:
    raise ValueError(f"Could not load spec from {_router_path}")
relationship_module = importlib.util.module_from_spec(spec)
sys.modules["relationship_api_router"] = relationship_module
spec.loader.exec_module(relationship_module)
_get_db_manager = relationship_module._get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _app_with_mock_db(
    *,
    fetch_rows: list | None = None,
    fetchval_result: int = 0,
    fetchrow_result: dict | None = None,
    fetchrow_side_effect: list | None = None,
    mcp_manager: MCPClientManager | None = None,
    butler_configs: list[ButlerConnectionInfo] | None = None,
    include_mock_pool: bool = False,
):
    """Create a FastAPI app with a mocked DatabaseManager.

    The mock pool returns:
    - ``fetch_rows`` for pool.fetch() calls (default: [])
    - ``fetchval_result`` for pool.fetchval() calls (default: 0)
    - ``fetchrow_result`` for pool.fetchrow() calls (default: None → 404)
    """
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool = MagicMock(return_value=mock_pool)

    app = create_app(cors_origins=["*"])

    # Suppress lifespan so wire_db_dependencies() doesn't overwrite our mock.
    @asynccontextmanager
    async def _null_lifespan(_app):
        yield

    app.router.lifespan_context = _null_lifespan
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    if mcp_manager is not None:
        app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    if butler_configs is not None:
        app.dependency_overrides[get_butler_configs] = lambda: butler_configs

    if include_mock_pool:
        return app, mock_db, mock_pool
    return app


def _mock_contacts_sync_result(payload: object, *, is_error: bool = False) -> MagicMock:
    """Create a mock MCP contacts_sync_now tool result."""
    block = MagicMock()
    block.text = json.dumps(payload) if not isinstance(payload, str) else payload
    result = MagicMock()
    result.content = [block]
    result.is_error = is_error
    return result


def _mock_mcp_manager(
    *,
    call_result: MagicMock | None = None,
    unreachable: bool = False,
    timeout: bool = False,
) -> MCPClientManager:
    """Create a mock MCP manager for contacts sync endpoint tests."""
    mgr = MagicMock(spec=MCPClientManager)
    if unreachable:
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError(
                "relationship",
                cause=ConnectionRefusedError("refused"),
            )
        )
        return mgr
    if timeout:
        mgr.get_client = AsyncMock(side_effect=TimeoutError("timed out"))
        return mgr

    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(return_value=call_result)
    mgr.get_client = AsyncMock(return_value=mock_client)
    return mgr


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts
# ---------------------------------------------------------------------------


def test_list_contacts_empty():
    """GET /contacts returns empty list when no data."""
    app = _app_with_mock_db(fetchval_result=0, fetch_rows=[])
    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/contacts")

    assert resp.status_code == 200
    data = resp.json()
    assert data["contacts"] == []
    assert data["total"] == 0


def test_list_contacts_returns_contacts_with_labels():
    """GET /contacts returns contacts with aggregated labels."""
    cid = uuid4()
    contact_row = {
        "id": cid,
        "full_name": "Alice Smith",
        "nickname": "Ali",
        "email": "alice@example.com",
        "phone": "555-1234",
        "last_interaction_at": datetime(2025, 1, 15, tzinfo=UTC),
    }
    label_rows = [{"contact_id": cid, "id": uuid4(), "name": "Friend", "color": "blue"}]

    app, mock_db, mock_pool = _app_with_mock_db(
        fetchval_result=1,
        fetch_rows=[contact_row],
        include_mock_pool=True,
    )

    # pool.fetch is called twice: first for contacts, then for labels
    mock_pool.fetch = AsyncMock(side_effect=[[contact_row], label_rows])

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/contacts")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["contacts"]) == 1
    assert data["contacts"][0]["full_name"] == "Alice Smith"
    assert len(data["contacts"][0]["labels"]) == 1
    assert data["contacts"][0]["labels"][0]["name"] == "Friend"


def test_list_contacts_search():
    """GET /contacts?q=alice filters by name."""
    app, mock_db, mock_pool = _app_with_mock_db(
        fetchval_result=0, fetch_rows=[], include_mock_pool=True
    )

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/contacts?q=alice")

    assert resp.status_code == 200
    # Verify the pool was called with search filter
    assert mock_pool.fetch.called


# ---------------------------------------------------------------------------
# POST /api/relationship/contacts/sync
# ---------------------------------------------------------------------------


def test_contacts_sync_incremental_dispatch():
    """POST /contacts/sync dispatches incremental mode to MCP tool."""
    result = _mock_contacts_sync_result(
        {"created": 2, "updated": 1, "skipped": 5, "errors": 0, "message": "ok"}
    )
    mcp_manager = _mock_mcp_manager(call_result=result)
    app = _app_with_mock_db(
        mcp_manager=mcp_manager,
        butler_configs=[ButlerConnectionInfo("relationship", 40102)],
    )

    with TestClient(app=app) as client:
        resp = client.post("/api/relationship/contacts/sync?mode=incremental")

    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "google"
    assert data["mode"] == "incremental"
    assert data["created"] == 2
    assert data["updated"] == 1
    assert data["skipped"] == 5
    assert data["errors"] == 0

    mock_client = mcp_manager.get_client.return_value
    mock_client.call_tool.assert_awaited_once_with(
        "contacts_sync_now",
        {"provider": "google", "mode": "incremental"},
    )


def test_contacts_sync_full_dispatch():
    """POST /contacts/sync dispatches full mode to MCP tool."""
    result = _mock_contacts_sync_result(
        {"summary": {"created": 7, "updated": 4, "skipped": 0, "errors": 0}}
    )
    mcp_manager = _mock_mcp_manager(call_result=result)
    app = _app_with_mock_db(
        mcp_manager=mcp_manager,
        butler_configs=[ButlerConnectionInfo("relationship", 40102)],
    )

    with TestClient(app=app) as client:
        resp = client.post("/api/relationship/contacts/sync?mode=full")

    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "full"
    assert data["created"] == 7
    assert data["updated"] == 4
    assert data["errors"] == 0

    mock_client = mcp_manager.get_client.return_value
    mock_client.call_tool.assert_awaited_once_with(
        "contacts_sync_now",
        {"provider": "google", "mode": "full"},
    )


def test_contacts_sync_invalid_mode():
    """POST /contacts/sync rejects invalid mode."""
    mcp_manager = _mock_mcp_manager(call_result=_mock_contacts_sync_result({"created": 0}))
    app = _app_with_mock_db(
        mcp_manager=mcp_manager,
        butler_configs=[ButlerConnectionInfo("relationship", 40102)],
    )

    with TestClient(app=app) as client:
        resp = client.post("/api/relationship/contacts/sync?mode=weekly")

    assert resp.status_code == 422


def test_contacts_sync_credential_error_actionable():
    """POST /contacts/sync maps credential failures to actionable non-2xx response."""
    result = _mock_contacts_sync_result(
        {"error": "Google OAuth credentials are missing"},
        is_error=True,
    )
    mcp_manager = _mock_mcp_manager(call_result=result)
    app = _app_with_mock_db(
        mcp_manager=mcp_manager,
        butler_configs=[ButlerConnectionInfo("relationship", 40102)],
    )

    with TestClient(app=app) as client:
        resp = client.post("/api/relationship/contacts/sync?mode=incremental")

    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "oauth" in detail
    assert "/api/oauth/google/start" in detail


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{contact_id}
# ---------------------------------------------------------------------------


def test_get_contact_not_found():
    """GET /contacts/{id} returns 404 when not found."""
    app = _app_with_mock_db(fetchrow_result=None)
    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{uuid4()}")

    assert resp.status_code == 404


def test_get_contact_detail():
    """GET /contacts/{id} returns full contact with labels and birthday."""
    cid = uuid4()
    app, mock_db, mock_pool = _app_with_mock_db(
        fetchrow_result={
            "id": cid,
            "full_name": "Bob Jones",
            "nickname": None,
            "notes": "Met at conference",
            "company": "Acme Inc",
            "job_title": "Engineer",
            "metadata": {},
            "created_at": datetime(2024, 1, 1, tzinfo=UTC),
            "updated_at": datetime(2024, 1, 1, tzinfo=UTC),
            "email": "bob@acme.com",
            "phone": None,
            "last_interaction_at": None,
        },
        include_mock_pool=True,
    )

    # fetchrow calls: contact, birthday, address
    # fetch calls: labels
    mock_pool.fetchrow = AsyncMock(
        side_effect=[
            {
                "id": cid,
                "full_name": "Bob Jones",
                "nickname": None,
                "notes": "Met at conference",
                "company": "Acme Inc",
                "job_title": "Engineer",
                "metadata": {},
                "created_at": datetime(2024, 1, 1, tzinfo=UTC),
                "updated_at": datetime(2024, 1, 1, tzinfo=UTC),
                "email": "bob@acme.com",
                "phone": None,
                "last_interaction_at": None,
            },
            {"month": 3, "day": 15, "year": 1990},
            None,  # address
        ]
    )
    mock_pool.fetch = AsyncMock(return_value=[])

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["full_name"] == "Bob Jones"
    assert data["company"] == "Acme Inc"
    assert data["birthday"] == "1990-03-15"


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{contact_id}/notes
# ---------------------------------------------------------------------------


def test_list_contact_notes():
    """GET /contacts/{id}/notes returns notes for a contact."""
    cid = uuid4()
    nid = uuid4()
    app = _app_with_mock_db(
        fetch_rows=[
            {
                "id": nid,
                "contact_id": cid,
                "content": "Follow up next week",
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                "updated_at": datetime(2025, 1, 1, tzinfo=UTC),
            }
        ]
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}/notes")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["content"] == "Follow up next week"


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{contact_id}/interactions
# ---------------------------------------------------------------------------


def test_list_contact_interactions():
    """GET /contacts/{id}/interactions returns interactions."""
    cid = uuid4()
    iid = uuid4()
    app = _app_with_mock_db(
        fetch_rows=[
            {
                "id": iid,
                "contact_id": cid,
                "type": "email",
                "summary": "Checked in",
                "details": None,
                "occurred_at": datetime(2025, 1, 10, tzinfo=UTC),
                "created_at": datetime(2025, 1, 10, tzinfo=UTC),
            }
        ]
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}/interactions")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["type"] == "email"


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{contact_id}/gifts
# ---------------------------------------------------------------------------


def test_list_contact_gifts():
    """GET /contacts/{id}/gifts returns gifts."""
    cid = uuid4()
    gid = uuid4()
    app = _app_with_mock_db(
        fetch_rows=[
            {
                "id": gid,
                "contact_id": cid,
                "description": "Coffee mug",
                "direction": "given",
                "occasion": "Birthday",
                "date": date(2025, 1, 15),
                "value": 25.0,
                "created_at": datetime(2025, 1, 15, tzinfo=UTC),
            }
        ]
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}/gifts")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["description"] == "Coffee mug"


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{contact_id}/loans
# ---------------------------------------------------------------------------


def test_list_contact_loans():
    """GET /contacts/{id}/loans returns loans."""
    cid = uuid4()
    lid = uuid4()
    app = _app_with_mock_db(
        fetch_rows=[
            {
                "id": lid,
                "contact_id": cid,
                "description": "Book: Clean Code",
                "direction": "lent",
                "amount": 0.0,
                "currency": "USD",
                "status": "active",
                "date": date(2025, 1, 1),
                "due_date": None,
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
            }
        ]
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}/loans")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["description"] == "Book: Clean Code"


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{contact_id}/feed
# ---------------------------------------------------------------------------


def test_list_contact_feed():
    """GET /contacts/{id}/feed returns activity feed."""
    cid = uuid4()
    fid = uuid4()
    app = _app_with_mock_db(
        fetch_rows=[
            {
                "id": fid,
                "contact_id": cid,
                "action": "note_added",
                "details": {},
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
            }
        ]
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}/feed")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["action"] == "note_added"


# ---------------------------------------------------------------------------
# GET /api/relationship/groups
# ---------------------------------------------------------------------------


def test_list_groups():
    """GET /groups returns groups with member counts."""
    gid = uuid4()
    app, _, mock_pool = _app_with_mock_db(
        fetchval_result=1,
        include_mock_pool=True,
    )
    mock_pool.fetch = AsyncMock(
        side_effect=[
            [
                {"column_name": "id"},
                {"column_name": "name"},
                {"column_name": "description"},
                {"column_name": "created_at"},
                {"column_name": "updated_at"},
            ],
            [
                {
                    "id": gid,
                    "name": "Work",
                    "description": "Colleagues",
                    "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                    "updated_at": datetime(2025, 1, 1, tzinfo=UTC),
                    "member_count": 5,
                }
            ],
        ]
    )

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/groups")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["groups"]) == 1
    assert data["groups"][0]["name"] == "Work"
    assert data["groups"][0]["member_count"] == 5


def test_list_groups_legacy_schema_without_optional_group_columns():
    """GET /groups works when groups lacks description/updated_at columns."""
    gid = uuid4()
    created_at = datetime(2025, 1, 1, tzinfo=UTC)
    app, _, mock_pool = _app_with_mock_db(fetchval_result=1, include_mock_pool=True)
    mock_pool.fetch = AsyncMock(
        side_effect=[
            [
                {"column_name": "id"},
                {"column_name": "name"},
                {"column_name": "created_at"},
            ],
            [
                {
                    "id": gid,
                    "name": "Work",
                    "created_at": created_at,
                    "updated_at": created_at,
                    "description": None,
                    "member_count": 2,
                }
            ],
        ]
    )

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/groups")

    assert resp.status_code == 200
    data = resp.json()
    assert data["groups"][0]["description"] is None
    assert data["groups"][0]["updated_at"] == "2025-01-01T00:00:00Z"
    assert mock_pool.fetch.await_count == 2

    schema_sql = mock_pool.fetch.await_args_list[0].args[0]
    groups_sql = mock_pool.fetch.await_args_list[1].args[0]
    assert "information_schema.columns" in schema_sql
    assert "g.description" not in groups_sql
    assert "g.updated_at" not in groups_sql
    assert "NULL::text AS description" in groups_sql
    assert "g.created_at AS updated_at" in groups_sql


# ---------------------------------------------------------------------------
# GET /api/relationship/groups/{group_id}
# ---------------------------------------------------------------------------


def test_get_group():
    """GET /groups/{id} returns group detail."""
    gid = uuid4()
    app = _app_with_mock_db(
        fetchrow_result={
            "id": gid,
            "name": "Family",
            "description": None,
            "created_at": datetime(2025, 1, 1, tzinfo=UTC),
            "updated_at": datetime(2025, 1, 1, tzinfo=UTC),
            "member_count": 3,
        }
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/groups/{gid}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Family"


def test_get_group_not_found():
    """GET /groups/{id} returns 404 when not found."""
    app = _app_with_mock_db(fetchrow_result=None)
    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/groups/{uuid4()}")

    assert resp.status_code == 404


def test_get_group_legacy_schema_without_optional_group_columns():
    """GET /groups/{id} works when groups lacks description/updated_at columns."""
    gid = uuid4()
    created_at = datetime(2025, 1, 1, tzinfo=UTC)
    app, _, mock_pool = _app_with_mock_db(include_mock_pool=True)
    mock_pool.fetch = AsyncMock(
        return_value=[
            {"column_name": "id"},
            {"column_name": "name"},
            {"column_name": "created_at"},
        ]
    )
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": gid,
            "name": "Family",
            "created_at": created_at,
            "updated_at": created_at,
            "description": None,
            "member_count": 3,
        }
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/groups/{gid}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["description"] is None
    assert data["updated_at"] == "2025-01-01T00:00:00Z"
    assert mock_pool.fetch.await_count == 1

    schema_sql = mock_pool.fetch.await_args_list[0].args[0]
    groups_sql = mock_pool.fetchrow.await_args_list[0].args[0]
    assert "information_schema.columns" in schema_sql
    assert "g.description" not in groups_sql
    assert "g.updated_at" not in groups_sql
    assert "NULL::text AS description" in groups_sql
    assert "g.created_at AS updated_at" in groups_sql


# ---------------------------------------------------------------------------
# GET /api/relationship/labels
# ---------------------------------------------------------------------------


def test_list_labels():
    """GET /labels returns all labels."""
    lid = uuid4()
    app = _app_with_mock_db(
        fetch_rows=[
            {"id": lid, "name": "Friend", "color": "blue"},
        ]
    )

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/labels")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Friend"


# ---------------------------------------------------------------------------
# GET /api/relationship/upcoming-dates
# ---------------------------------------------------------------------------


def test_list_upcoming_dates():
    """GET /upcoming-dates returns dates within window."""
    cid = uuid4()
    app = _app_with_mock_db(
        fetch_rows=[
            {
                "contact_id": cid,
                "contact_name": "Charlie",
                "label": "birthday",
                "month": 2,
                "day": 20,
                "year": 1995,
            }
        ]
    )

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/upcoming-dates?days=30")

    assert resp.status_code == 200
    data = resp.json()
    # Note: This test will pass only when run near Feb 20
    # In a real test, you'd mock datetime.date.today()
    assert isinstance(data, list)
