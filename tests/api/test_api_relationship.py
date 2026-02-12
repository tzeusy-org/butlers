"""Tests for relationship/CRM API endpoints.

Verifies the API contract (status codes, response shapes) for relationship
endpoints.  Uses a mocked DatabaseManager so no real database is required.

Issue: butlers-26h.10.3
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.relationship import _get_db_manager

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
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.state.mock_pool = mock_pool

    if include_mock_pool:
        return app, mock_pool

    return app


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts
# ---------------------------------------------------------------------------


class TestListContacts:
    async def test_returns_contact_list_response_structure(self):
        """Response must have 'contacts' array and 'total' integer."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/relationship/contacts")

        assert resp.status_code == 200
        body = resp.json()
        assert "contacts" in body
        assert "total" in body
        assert isinstance(body["contacts"], list)
        assert isinstance(body["total"], int)

    async def test_search_param_accepted(self):
        """The ?q= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/relationship/contacts", params={"q": "alice"})

        assert resp.status_code == 200

    async def test_label_filter_accepted(self):
        """The ?label= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/relationship/contacts", params={"label": "family"})

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{contact_id}
# ---------------------------------------------------------------------------


class TestGetContact:
    async def test_missing_contact_returns_404(self):
        """A non-existent contact should return 404 when fetchrow returns None."""
        app = _app_with_mock_db(fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/relationship/contacts/00000000-0000-0000-0000-000000000000"
            )

        assert resp.status_code == 404

    async def test_birthday_lookup_uses_label_column(self):
        """Birthday lookup should filter important_dates by label, not date_type."""
        contact_id = uuid4()
        now = datetime.now(UTC)
        app, mock_pool = _app_with_mock_db(
            fetchrow_side_effect=[
            {
                "id": contact_id,
                "full_name": "Alice Example",
                "nickname": None,
                "notes": None,
                "company": None,
                "job_title": None,
                "metadata": {},
                "created_at": now,
                "updated_at": now,
                "email": None,
                "phone": None,
                "last_interaction_at": None,
            },
            {"month": 3, "day": 15, "year": 1990},
            None,
            ],
            include_mock_pool=True,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/relationship/contacts/{contact_id}")

        assert resp.status_code == 200
        assert resp.json()["birthday"] == "1990-03-15"

        important_dates_sql = [
            call.args[0]
            for call in mock_pool.fetchrow.await_args_list
            if "FROM important_dates" in call.args[0]
        ]
        assert len(important_dates_sql) == 1, (
            "Expected exactly one fetchrow call to important_dates"
        )
        birthday_sql = important_dates_sql[0]
        assert "label = 'birthday'" in birthday_sql
        assert "date_type" not in birthday_sql


# ---------------------------------------------------------------------------
# GET /api/relationship/groups
# ---------------------------------------------------------------------------


class TestListGroups:
    async def test_returns_group_list_response_structure(self):
        """Response must have 'groups' array and 'total' integer."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/relationship/groups")

        assert resp.status_code == 200
        body = resp.json()
        assert "groups" in body
        assert "total" in body
        assert isinstance(body["groups"], list)
        assert isinstance(body["total"], int)


# ---------------------------------------------------------------------------
# GET /api/relationship/labels
# ---------------------------------------------------------------------------


class TestListLabels:
    async def test_returns_list_of_labels(self):
        """Response must be a JSON array (each element a Label object)."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/relationship/labels")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)


# ---------------------------------------------------------------------------
# GET /api/relationship/upcoming-dates
# ---------------------------------------------------------------------------


class TestListUpcomingDates:
    async def test_returns_list_of_upcoming_dates(self):
        """Response must be a JSON array of UpcomingDate objects."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/relationship/upcoming-dates")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)

    async def test_days_param_accepted(self):
        """The ?days= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/relationship/upcoming-dates", params={"days": 30})

        assert resp.status_code == 200

    async def test_uses_label_column_for_upcoming_dates(self):
        """Upcoming-date query should read date kind from important_dates.label."""
        today = date.today()
        app, mock_pool = _app_with_mock_db(
            fetch_rows=[
                {
                    "contact_id": uuid4(),
                    "contact_name": "Alice",
                    "label": "birthday",
                    "month": today.month,
                    "day": today.day,
                    "year": None,
                }
            ],
            include_mock_pool=True,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/relationship/upcoming-dates")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["date_type"] == "birthday"

        upcoming_sql = mock_pool.fetch.await_args.args[0]
        assert "id.label" in upcoming_sql
        assert "id.label AS date_type" not in upcoming_sql
        assert "id.date_type" not in upcoming_sql


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{contact_id}/notes
# ---------------------------------------------------------------------------


class TestListContactNotes:
    async def test_returns_list_of_notes(self):
        """Response must be a JSON array of Note objects."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/relationship/contacts/00000000-0000-0000-0000-000000000000/notes"
            )

        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{contact_id}/interactions
# ---------------------------------------------------------------------------


class TestListContactInteractions:
    async def test_returns_list_of_interactions(self):
        """Response must be a JSON array of Interaction objects."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/relationship/contacts/00000000-0000-0000-0000-000000000000/interactions"
            )

        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{contact_id}/gifts
# ---------------------------------------------------------------------------


class TestListContactGifts:
    async def test_returns_list_of_gifts(self):
        """Response must be a JSON array of Gift objects."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/relationship/contacts/00000000-0000-0000-0000-000000000000/gifts"
            )

        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# GET /api/relationship/contacts/{contact_id}/loans
# ---------------------------------------------------------------------------


class TestListContactLoans:
    async def test_returns_list_of_loans(self):
        """Response must be a JSON array of Loan objects."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/relationship/contacts/00000000-0000-0000-0000-000000000000/loans"
            )

        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
