"""Tests for relationship identity API endpoints.

Covers the new identity-model endpoints added by butlers-h9fs.9:
- GET /contacts/{id} — now includes roles, entity_id, and masked contact_info
- GET /contacts/{id}/secrets/{info_id} — reveal secured contact_info value
- PATCH /contacts/{id} — partial update including roles
- GET /contacts/pending — contacts with needs_disambiguation=true
- POST /contacts/{id}/confirm — clear needs_disambiguation
- POST /contacts/{id}/merge — merge temp contact into target
- GET /owner/setup-status — owner telegram/email presence

Issue: butlers-h9fs.9
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

# Load relationship router module dynamically using the same module name as router_discovery
# so that dependency_overrides work correctly (same _get_db_manager object reference).
# We load eagerly here (not cached) to guarantee we have a fresh module for this test file.
# We must also update sys.modules so that router_discovery finds the same module, and so that
# the relationship-test module (imported in alphabetical order before this file) doesn't
# stomp our reference. We use a private alias so our _get_db_manager stays authoritative.
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "relationship" / "api" / "router.py"
_MODULE_NAME = "relationship_api_router"


def _get_rel_db_manager_fn():
    """Return the live _get_db_manager from the currently-loaded relationship router module.

    Looked up lazily so that whichever exec_module call ran last wins — the
    FastAPI router always uses the function that is current in the module.
    """
    mod = sys.modules.get(_MODULE_NAME)
    if mod is None:
        raise RuntimeError("relationship_api_router not loaded in sys.modules")
    return mod._get_db_manager


pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _app_with_mock_pool(
    *,
    fetchrow_side_effect=None,
    fetchrow_result=None,
    fetch_side_effect=None,
    fetch_rows=None,
    fetchval_result=None,
    execute_result=None,
) -> tuple:
    """Create a FastAPI test app with a mocked relationship database pool."""
    mock_pool = AsyncMock()

    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    if fetch_side_effect is not None:
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool = MagicMock(return_value=mock_pool)
    mock_db.butler_names = ["relationship"]

    app = create_app(cors_origins=["*"])

    @asynccontextmanager
    async def _null_lifespan(_app):
        yield

    app.router.lifespan_context = _null_lifespan
    app.dependency_overrides[_get_rel_db_manager_fn()] = lambda: mock_db

    return app, mock_db, mock_pool


def _contact_row(
    cid=None,
    *,
    name="Alice Smith",
    roles=None,
    entity_id=None,
    metadata=None,
):
    """Create a mock contact row dict."""
    return {
        "id": cid or uuid4(),
        "full_name": name,
        "nickname": None,
        "notes": None,
        "company": None,
        "job_title": None,
        "metadata": metadata or {},
        "created_at": _NOW,
        "updated_at": _NOW,
        "roles": roles or [],
        "entity_id": entity_id,
        "email": None,
        "phone": None,
        "last_interaction_at": None,
    }


def _contact_info_row(
    *,
    info_id=None,
    ci_type="telegram",
    value="123456",
    is_primary=True,
    secured=False,
):
    """Create a mock shared.contact_info row dict."""
    return {
        "id": info_id or uuid4(),
        "type": ci_type,
        "value": value,
        "is_primary": is_primary,
        "secured": secured,
    }


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{id} — identity fields
# ---------------------------------------------------------------------------


def test_get_contact_includes_roles_and_entity_id():
    """GET /contacts/{id} returns roles and entity_id fields."""
    cid = uuid4()
    eid = uuid4()
    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            _contact_row(cid, roles=["owner"], entity_id=eid),
            None,  # no birthday
            None,  # no address
        ],
        fetch_side_effect=[
            [],  # labels
            [],  # contact_info
        ],
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["roles"] == ["owner"]
    assert data["entity_id"] == str(eid)


def test_get_contact_roles_default_empty():
    """GET /contacts/{id} returns empty roles list when contact has no roles."""
    cid = uuid4()
    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            _contact_row(cid, roles=[]),
            None,
            None,
        ],
        fetch_side_effect=[[], []],
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["roles"] == []
    assert data["entity_id"] is None


def test_get_contact_masks_secured_contact_info():
    """GET /contacts/{id} returns value=None for secured contact_info entries."""
    cid = uuid4()
    secured_info_id = uuid4()
    plain_info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            _contact_row(cid),
            None,
            None,
        ],
        fetch_side_effect=[
            [],  # labels
            [
                _contact_info_row(
                    info_id=secured_info_id,
                    ci_type="telegram",
                    value="secret-chat-id",
                    secured=True,
                ),
                _contact_info_row(
                    info_id=plain_info_id,
                    ci_type="email",
                    value="alice@example.com",
                    secured=False,
                ),
            ],
        ],
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}")

    assert resp.status_code == 200
    data = resp.json()
    ci = data["contact_info"]
    assert len(ci) == 2

    secured = next(e for e in ci if e["type"] == "telegram")
    assert secured["secured"] is True
    assert secured["value"] is None  # masked

    plain = next(e for e in ci if e["type"] == "email")
    assert plain["secured"] is False
    assert plain["value"] == "alice@example.com"  # not masked


def test_get_contact_contact_info_not_masked_when_plain():
    """GET /contacts/{id} returns actual value for non-secured contact_info."""
    cid = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            _contact_row(cid),
            None,
            None,
        ],
        fetch_side_effect=[
            [],
            [_contact_info_row(ci_type="email", value="user@example.com", secured=False)],
        ],
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}")

    assert resp.status_code == 200
    ci = resp.json()["contact_info"]
    assert ci[0]["value"] == "user@example.com"


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{id}/secrets/{info_id}
# ---------------------------------------------------------------------------


def test_reveal_contact_secret_returns_value():
    """GET /contacts/{id}/secrets/{info_id} returns the real value for secured entry."""
    cid = uuid4()
    info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_result={
            "id": info_id,
            "type": "telegram",
            "value": "secret-chat-id",
            "secured": True,
        }
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}/secrets/{info_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["value"] == "secret-chat-id"
    assert data["type"] == "telegram"
    assert data["id"] == str(info_id)


def test_reveal_contact_secret_404_when_not_found():
    """GET /contacts/{id}/secrets/{info_id} returns 404 when info_id not found for contact."""
    cid = uuid4()
    info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}/secrets/{info_id}")

    assert resp.status_code == 404


def test_reveal_contact_secret_400_when_not_secured():
    """GET /contacts/{id}/secrets/{info_id} returns 400 when entry is not secured."""
    cid = uuid4()
    info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_result={
            "id": info_id,
            "type": "email",
            "value": "alice@example.com",
            "secured": False,
        }
    )

    with TestClient(app=app) as client:
        resp = client.get(f"/api/relationship/contacts/{cid}/secrets/{info_id}")

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# PATCH /api/relationship/contacts/{id}
# ---------------------------------------------------------------------------


def test_patch_contact_updates_roles():
    """PATCH /contacts/{id} with roles updates the roles field."""
    cid = uuid4()
    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            {"id": cid},  # existence check
            _contact_row(cid, roles=["owner"]),  # get_contact refetch (contact row)
            None,  # birthday
            None,  # address
        ],
        fetch_side_effect=[
            [],  # labels from get_contact
            [],  # contact_info from get_contact
        ],
    )

    with TestClient(app=app) as client:
        resp = client.patch(
            f"/api/relationship/contacts/{cid}",
            json={"roles": ["owner"]},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["roles"] == ["owner"]

    # Verify execute was called for update
    mock_pool.execute.assert_awaited_once()
    call_sql = mock_pool.execute.await_args.args[0]
    assert "roles" in call_sql


def test_patch_contact_updates_name():
    """PATCH /contacts/{id} with full_name updates the name field."""
    cid = uuid4()
    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            {"id": cid},
            _contact_row(cid, name="New Name"),
            None,
            None,
        ],
        fetch_side_effect=[[], []],
    )

    with TestClient(app=app) as client:
        resp = client.patch(
            f"/api/relationship/contacts/{cid}",
            json={"full_name": "New Name"},
        )

    assert resp.status_code == 200
    assert resp.json()["full_name"] == "New Name"


def test_patch_contact_404_when_not_found():
    """PATCH /contacts/{id} returns 404 when contact not found."""
    app, _, mock_pool = _app_with_mock_pool(fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.patch(f"/api/relationship/contacts/{uuid4()}", json={"roles": []})

    assert resp.status_code == 404


def test_patch_contact_no_update_when_all_none():
    """PATCH /contacts/{id} with all-None fields skips the UPDATE query."""
    cid = uuid4()
    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            {"id": cid},
            _contact_row(cid),
            None,
            None,
        ],
        fetch_side_effect=[[], []],
    )

    with TestClient(app=app) as client:
        resp = client.patch(f"/api/relationship/contacts/{cid}", json={})

    assert resp.status_code == 200
    mock_pool.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/pending
# ---------------------------------------------------------------------------


def test_list_pending_contacts_returns_needs_disambiguation():
    """GET /contacts/pending returns contacts with needs_disambiguation=true."""
    cid = uuid4()
    app, _, mock_pool = _app_with_mock_pool(
        fetch_side_effect=[
            [
                {
                    "id": cid,
                    "full_name": "Unknown (telegram 99999)",
                    "nickname": None,
                    "notes": None,
                    "company": None,
                    "job_title": None,
                    "metadata": {"needs_disambiguation": True, "source_channel": "telegram"},
                    "created_at": _NOW,
                    "updated_at": _NOW,
                    "roles": [],
                    "entity_id": None,
                }
            ],
            [  # contact_info for cid
                _contact_info_row(ci_type="telegram", value="99999"),
            ],
        ]
    )

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/contacts/pending")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == str(cid)
    assert data[0]["metadata"]["needs_disambiguation"] is True


def test_list_pending_contacts_returns_empty_when_none():
    """GET /contacts/pending returns empty list when no pending contacts."""
    app, _, mock_pool = _app_with_mock_pool(fetch_rows=[])

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/contacts/pending")

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /api/relationship/contacts/{id}/confirm
# ---------------------------------------------------------------------------


def test_confirm_contact_removes_needs_disambiguation():
    """POST /contacts/{id}/confirm clears needs_disambiguation from metadata."""
    cid = uuid4()
    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            {
                "id": cid,
                "metadata": {"needs_disambiguation": True, "source_channel": "telegram"},
            },
            # get_contact refetch after confirm
            _contact_row(cid, metadata={"source_channel": "telegram"}),
            None,  # birthday
            None,  # address
        ],
        fetch_side_effect=[
            [],  # labels
            [],  # contact_info
        ],
    )

    with TestClient(app=app) as client:
        resp = client.post(f"/api/relationship/contacts/{cid}/confirm")

    assert resp.status_code == 200
    # Verify execute was called with metadata not containing needs_disambiguation
    mock_pool.execute.assert_awaited_once()
    call_args = mock_pool.execute.await_args.args
    sql = call_args[0]
    metadata_json = call_args[1]
    assert "needs_disambiguation" not in metadata_json
    assert "UPDATE contacts SET metadata" in sql


def test_confirm_contact_404_when_not_found():
    """POST /contacts/{id}/confirm returns 404 for unknown contact."""
    app, _, mock_pool = _app_with_mock_pool(fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.post(f"/api/relationship/contacts/{uuid4()}/confirm")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/relationship/contacts/{id}/merge
# ---------------------------------------------------------------------------


def test_merge_contact_moves_contact_info_and_deletes_source():
    """POST /contacts/{id}/merge moves contact_info and deletes source."""
    target_id = uuid4()
    source_id = uuid4()
    moved_info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            {"id": target_id, "entity_id": None},  # target lookup
            {"id": source_id, "entity_id": None},  # source lookup
        ],
        fetch_side_effect=[
            [{"id": moved_info_id}],  # UPDATE contact_info RETURNING id
        ],
    )

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/contacts/{target_id}/merge",
            json={"source_contact_id": str(source_id)},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["target_contact_id"] == str(target_id)
    assert data["source_contact_id"] == str(source_id)
    assert data["contact_info_moved"] == 1
    assert data["entity_merged"] is False

    # Verify DELETE was called for source
    delete_call = mock_pool.execute.await_args
    assert "DELETE FROM contacts" in delete_call.args[0]
    assert delete_call.args[1] == source_id


def test_merge_contact_404_when_target_not_found():
    """POST /contacts/{id}/merge returns 404 when target contact not found."""
    source_id = uuid4()
    app, _, mock_pool = _app_with_mock_pool(fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/contacts/{uuid4()}/merge",
            json={"source_contact_id": str(source_id)},
        )

    assert resp.status_code == 404


def test_merge_contact_404_when_source_not_found():
    """POST /contacts/{id}/merge returns 404 when source contact not found."""
    target_id = uuid4()
    source_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            {"id": target_id, "entity_id": None},
            None,  # source not found
        ]
    )

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/contacts/{target_id}/merge",
            json={"source_contact_id": str(source_id)},
        )

    assert resp.status_code == 404


def test_merge_contact_400_when_same_contact():
    """POST /contacts/{id}/merge returns 400 when source == target."""
    cid = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            {"id": cid, "entity_id": None},
            {"id": cid, "entity_id": None},
        ]
    )

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/contacts/{cid}/merge",
            json={"source_contact_id": str(cid)},
        )

    assert resp.status_code == 400
    assert "different" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/relationship/owner/setup-status
# ---------------------------------------------------------------------------


def test_owner_setup_status_has_both():
    """GET /owner/setup-status returns true for both when owner has telegram and email."""
    app, _, mock_pool = _app_with_mock_pool(
        fetch_rows=[
            {"type": "telegram"},
            {"type": "email"},
        ]
    )

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/owner/setup-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["has_telegram"] is True
    assert data["has_email"] is True


def test_owner_setup_status_has_telegram_only():
    """GET /owner/setup-status returns has_telegram=true, has_email=false."""
    app, _, mock_pool = _app_with_mock_pool(fetch_rows=[{"type": "telegram"}])

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/owner/setup-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["has_telegram"] is True
    assert data["has_email"] is False


def test_owner_setup_status_has_neither():
    """GET /owner/setup-status returns false for both when owner has no channels."""
    app, _, mock_pool = _app_with_mock_pool(fetch_rows=[])

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/owner/setup-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["has_telegram"] is False
    assert data["has_email"] is False


def test_owner_setup_status_includes_contact_id():
    """GET /owner/setup-status includes the owner contact_id when found."""
    owner_id = uuid4()
    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_result={"id": owner_id},
        fetch_rows=[{"type": "email"}],
    )

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/owner/setup-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["contact_id"] == str(owner_id)
    assert data["has_email"] is True


def test_owner_setup_status_contact_id_null_when_no_owner():
    """GET /owner/setup-status returns null contact_id when no owner contact exists."""
    app, _, mock_pool = _app_with_mock_pool(fetch_rows=[])

    with TestClient(app=app) as client:
        resp = client.get("/api/relationship/owner/setup-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["contact_id"] is None


# ---------------------------------------------------------------------------
# POST /api/relationship/contacts/{id}/contact-info
# ---------------------------------------------------------------------------


def test_create_contact_info_success():
    """POST /contacts/{id}/contact-info creates a new contact_info entry."""
    contact_id = uuid4()
    info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            {"id": contact_id},  # contact exists check
            {  # INSERT RETURNING
                "id": info_id,
                "contact_id": contact_id,
                "type": "email",
                "value": "alice@example.com",
                "is_primary": True,
                "secured": False,
            },
        ],
    )

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/contacts/{contact_id}/contact-info",
            json={"type": "email", "value": "alice@example.com", "is_primary": True},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == str(info_id)
    assert data["type"] == "email"
    assert data["value"] == "alice@example.com"
    assert data["is_primary"] is True
    assert data["secured"] is False


def test_create_contact_info_contact_not_found():
    """POST /contacts/{id}/contact-info returns 404 for missing contact."""
    contact_id = uuid4()
    app, _, mock_pool = _app_with_mock_pool(fetchrow_result=None)

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/contacts/{contact_id}/contact-info",
            json={"type": "telegram", "value": "@alice"},
        )

    assert resp.status_code == 404


def test_merge_contact_deduplicates_contact_info():
    """POST /contacts/{id}/merge issues a dedup DELETE before moving contact_info rows."""
    target_id = uuid4()
    source_id = uuid4()
    moved_info_id = uuid4()

    app, _, mock_pool = _app_with_mock_pool(
        fetchrow_side_effect=[
            {"id": target_id, "entity_id": None},  # target lookup
            {"id": source_id, "entity_id": None},  # source lookup
        ],
        fetch_side_effect=[
            [{"id": moved_info_id}],  # UPDATE contact_info RETURNING id
        ],
    )

    with TestClient(app=app) as client:
        resp = client.post(
            f"/api/relationship/contacts/{target_id}/merge",
            json={"source_contact_id": str(source_id)},
        )

    assert resp.status_code == 200

    # execute should be called twice: dedup DELETE and final contact DELETE
    assert mock_pool.execute.await_count == 2
    calls = mock_pool.execute.await_args_list
    # First call: dedup delete — remove source rows that exist on target
    dedup_sql = calls[0].args[0]
    assert "DELETE FROM shared.contact_info" in dedup_sql
    assert calls[0].args[1] == source_id
    assert calls[0].args[2] == target_id
    # Second call: delete source contact
    contact_delete_sql = calls[1].args[0]
    assert "DELETE FROM contacts" in contact_delete_sql
    assert calls[1].args[1] == source_id
