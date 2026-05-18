"""Test that credentials are NOT surfaced on /entities/{id}/contacts endpoint.

Covers Requirement: Credentials carve-out § Scenario:
"Credentials are not surfaced on entity contacts endpoint" from
``openspec/changes/relationship-tabs-to-entities/specs/relationship-facts/spec.md``.

When GET /api/butlers/relationship/entities/{id}/contacts is called for an entity
that has both:
  - a non-secured `has-email` triple in `relationship.entity_facts`, AND
  - a secured credential row in `relationship.credentials`,

The response MUST:
  - Include the non-secured email row
  - NOT include the credential row from `relationship.credentials`

Tests are marked ``unit`` to avoid the Docker-availability guard applied to
roster/ integration tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENT_ID = uuid4()
_NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)


def _make_entity_fact_row(**kwargs) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for entity_facts.

    Used for has-* contact predicates.
    """
    data = {
        "id": uuid4(),
        "predicate": "has-email",
        "object": "alice@example.com",
        "src": "relationship",
        "conf": 1.0,
        "last_seen": None,
        "weight": None,
        "verified": False,
        "primary": None,
        **kwargs,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _app_with_pool(
    *,
    entity_exists: bool = True,
    fetch_rows: list | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app whose relationship DB pool returns controlled rows.

    ``entity_exists`` controls the ``fetchval`` response for the entity-exists
    check (returns 1 if True, None if False).
    ``fetch_rows`` is returned by pool.fetch for the facts query.
    """
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()

    # Find the relationship router module to override its _get_db_manager.
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _get(app: FastAPI, path: str, **params) -> httpx.Response:
    """Make a GET request to the app via httpx.AsyncClient."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, params=params or None)


# ---------------------------------------------------------------------------
# Tests: Credentials exclusion from /entities/{id}/contacts
# ---------------------------------------------------------------------------


class TestEntityContactsCredentialsExclusion:
    """Tests for credential carve-out on GET /api/relationship/entities/{id}/contacts."""

    async def test_non_secured_email_returned_when_credential_exists(self):
        """Scenario: Entity has both a non-secured email triple and a credential.

        The endpoint MUST return the email triple but NOT the credential.
        """
        # Setup: one has-email triple (non-secured)
        email_fact = _make_entity_fact_row(
            id=uuid4(),
            predicate="has-email",
            object="alice@example.com",
            src="migration",
            conf=1.0,
            verified=False,
        )

        # Note: relationship.credentials is a separate non-triple table.
        # The endpoint query MUST NOT fetch from it or join it.
        # In this test, we mock the pool.fetch() to return only the email fact.
        # The pool.fetch() call in the endpoint handler queries ONLY
        # relationship.entity_facts with WHERE predicate LIKE 'has-%'.
        # If credentials were incorrectly fetched, they would appear here.
        app, mock_pool = _app_with_pool(fetch_rows=[email_fact])

        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/contacts")

        assert resp.status_code == 200
        body = resp.json()
        assert "facts" in body
        assert len(body["facts"]) == 1

        # Verify the email fact is present
        fact = body["facts"][0]
        assert fact["predicate"] == "has-email"
        assert fact["object"] == "alice@example.com"

        # Verify that the pool.fetch() was called with the correct query
        # that queries ONLY entity_facts with the has-% predicate filter
        mock_pool.fetch.assert_called_once()
        call_args = mock_pool.fetch.call_args
        sql_query = call_args[0][0]

        # Assert: the query MUST NOT reference relationship.credentials
        assert "credentials" not in sql_query.lower(), (
            "Query incorrectly references relationship.credentials table; "
            "credentials should be excluded from the contacts endpoint"
        )

        # Assert: the query MUST query relationship.entity_facts
        assert "entity_facts" in sql_query.lower(), "Query must query relationship.entity_facts"

        # Assert: the query MUST filter on predicate LIKE 'has-%'
        assert "LIKE" in sql_query and "has-" in sql_query, (
            "Query must filter on predicate LIKE 'has-%'"
        )

    async def test_multiple_contact_facts_without_credentials_join(self):
        """Scenario: Entity has multiple contact facts (no credentials table join).

        When an entity has multiple has-* predicates (has-email, has-phone, etc.),
        the endpoint MUST return all of them without querying relationship.credentials.
        """
        email_fact = _make_entity_fact_row(
            id=uuid4(),
            predicate="has-email",
            object="alice@example.com",
            src="migration",
        )
        phone_fact = _make_entity_fact_row(
            id=uuid4(),
            predicate="has-phone",
            object="+1-555-0100",
            src="migration",
        )

        app, mock_pool = _app_with_pool(fetch_rows=[email_fact, phone_fact])

        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/contacts")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["facts"]) == 2

        predicates = [f["predicate"] for f in body["facts"]]
        assert "has-email" in predicates
        assert "has-phone" in predicates

        # Verify the query does not reference credentials
        call_args = mock_pool.fetch.call_args
        sql_query = call_args[0][0]
        assert "credentials" not in sql_query.lower()

    async def test_empty_contacts_when_only_credentials_exist(self):
        """Scenario: Entity has only credentials, no contact facts.

        When an entity has NO active has-* triples but HAS credentials,
        the endpoint MUST return an empty facts list.
        """
        # No triples returned, only credentials would exist in relationship.credentials
        app, mock_pool = _app_with_pool(fetch_rows=[])

        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/contacts")

        assert resp.status_code == 200
        body = resp.json()
        assert body["facts"] == []

        # Verify the query does not join to credentials
        call_args = mock_pool.fetch.call_args
        sql_query = call_args[0][0]
        assert "credentials" not in sql_query.lower()

    async def test_contact_facts_response_schema_unchanged(self):
        """Scenario: ContactFact response schema includes provenance fields.

        Verify that the response includes all provenance fields but no
        credential-specific fields.
        """
        fact = _make_entity_fact_row(
            id=uuid4(),
            predicate="has-email",
            object="bob@example.com",
            src="migration",
            conf=0.95,
            verified=True,
            primary=True,
            weight=None,
            last_seen=_NOW,
        )

        app, _ = _app_with_pool(fetch_rows=[fact])

        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/contacts")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["facts"]) == 1

        contact_fact = body["facts"][0]

        # Verify all ContactFact fields are present
        assert "id" in contact_fact
        assert "predicate" in contact_fact
        assert "object" in contact_fact
        assert "value_hash" in contact_fact
        assert "src" in contact_fact
        assert "conf" in contact_fact
        assert "last_seen" in contact_fact
        assert "weight" in contact_fact
        assert "verified" in contact_fact
        assert "primary" in contact_fact

        # Verify values
        assert contact_fact["predicate"] == "has-email"
        assert contact_fact["object"] == "bob@example.com"
        assert contact_fact["src"] == "migration"
        assert contact_fact["conf"] == 0.95
        assert contact_fact["verified"] is True
        assert contact_fact["primary"] is True
