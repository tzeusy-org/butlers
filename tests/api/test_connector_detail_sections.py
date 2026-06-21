"""Tests for connector-scoped event, incident, and routing rule endpoints [bu-5ywn2].

Endpoints under test:
  GET /api/ingestion/connectors/{type}/{identity}/events
  GET /api/ingestion/connectors/{type}/{identity}/incidents
  GET /api/ingestion/connectors/{type}/{identity}/routing-rules

Each endpoint:
  - Returns HTTP 404 when the connector is not in the registry
  - Returns HTTP 200 with empty list when the connector exists but has no data
  - Returns HTTP 200 with populated data when events/incidents/rules exist
  - Enforces limit parameter bounds (ge=1, le=max)
  - Returns HTTP 503 when the database pool is unavailable

Routing rules use the structured scope 'connector:<type>:<identity>' (design.md D2).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.ingestion_connectors import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(data: dict) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    return row


def _connector_registry_row(
    *,
    connector_type: str = "gmail",
    endpoint_identity: str = "user@example.com",
) -> MagicMock:
    return _make_row(
        {
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
        }
    )


def _event_row(
    *,
    status: str = "ingested",
    received_at: datetime | None = None,
    source_channel: str = "gmail",
    source_sender_identity: str | None = "sender@example.com",
    filter_reason: str | None = None,
    error_detail: str | None = None,
) -> MagicMock:
    if received_at is None:
        received_at = datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC)
    return _make_row(
        {
            "id": str(uuid4()),
            "received_at": received_at,
            "source_channel": source_channel,
            "source_sender_identity": source_sender_identity,
            "status": status,
            "filter_reason": filter_reason,
            "error_detail": error_detail,
        }
    )


def _incident_row(
    *,
    status: str = "failed",
    received_at: datetime | None = None,
    source_channel: str = "gmail",
    error_detail: str | None = "Connection timeout",
    filter_reason: str | None = None,
) -> MagicMock:
    if received_at is None:
        received_at = datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC)
    return _make_row(
        {
            "id": str(uuid4()),
            "received_at": received_at,
            "source_channel": source_channel,
            "status": status,
            "error_detail": error_detail,
            "filter_reason": filter_reason,
        }
    )


def _rule_row(
    *,
    scope: str = "connector:gmail:user@example.com",
    rule_type: str = "sender_domain",
    condition: dict | None = None,
    action: str = "block",
    priority: int = 1,
    enabled: bool = True,
    name: str | None = None,
    description: str | None = None,
    created_by: str = "dashboard",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> MagicMock:
    if condition is None:
        condition = {"domain": "spam.example.com"}
    if created_at is None:
        created_at = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
    if updated_at is None:
        updated_at = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
    return _make_row(
        {
            "id": str(uuid4()),
            "scope": scope,
            "rule_type": rule_type,
            "condition": condition,
            "action": action,
            "priority": priority,
            "enabled": enabled,
            "name": name,
            "description": description,
            "created_by": created_by,
            "created_at": created_at,
            "updated_at": updated_at,
        }
    )


def _wire_pool(app, pool):
    """Override _get_db_manager with a mock DatabaseManager backed by pool."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db


def _wire_pool_unavailable(app):
    """Override _get_db_manager to simulate a missing switchboard pool."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError("switchboard pool not available")
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/{type}/{identity}/events
# ---------------------------------------------------------------------------


async def test_connector_events_404_unknown_connector(app):
    """Returns 404 when connector_registry lookup returns None."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    _wire_pool(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/gmail/unknown@example.com/events")

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


async def test_connector_events_empty(app):
    """Returns 200 with empty events list when connector has no events."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_connector_registry_row())
    pool.fetch = AsyncMock(return_value=[])
    _wire_pool(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/gmail/user@example.com/events")

    assert resp.status_code == 200
    body = resp.json()
    assert body["events"] == []
    assert body["total_returned"] == 0
    assert body["connector_type"] == "gmail"
    assert body["endpoint_identity"] == "user@example.com"


async def test_connector_events_populated(app):
    """Returns 200 with event rows when the connector has events."""
    event1 = _event_row(status="ingested")
    event2 = _event_row(status="failed", error_detail="timeout")
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_connector_registry_row())
    pool.fetch = AsyncMock(return_value=[event1, event2])
    _wire_pool(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/gmail/user@example.com/events")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_returned"] == 2
    assert len(body["events"]) == 2
    statuses = {e["status"] for e in body["events"]}
    assert "ingested" in statuses
    assert "failed" in statuses


async def test_connector_events_limit_enforced(app):
    """Limit parameter is passed through to the SQL query (validated by FastAPI)."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_connector_registry_row())
    pool.fetch = AsyncMock(return_value=[])
    _wire_pool(app, pool)

    # Limit exceeding max should be rejected with 422
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/gmail/user@example.com/events?limit=999")

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/{type}/{identity}/incidents
# ---------------------------------------------------------------------------


async def test_connector_incidents_404_unknown_connector(app):
    """Returns 404 when connector_registry lookup returns None."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    _wire_pool(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/gmail/unknown@example.com/incidents")

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


async def test_connector_incidents_empty(app):
    """Returns 200 with empty incidents list when connector has no incidents."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_connector_registry_row())
    pool.fetch = AsyncMock(return_value=[])
    _wire_pool(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/gmail/user@example.com/incidents")

    assert resp.status_code == 200
    body = resp.json()
    assert body["incidents"] == []
    assert body["total_returned"] == 0
    assert body["connector_type"] == "gmail"
    assert body["endpoint_identity"] == "user@example.com"


async def test_connector_incidents_populated(app):
    """Returns 200 with incident rows when the connector has failures."""
    incident1 = _incident_row(status="failed", error_detail="DB timeout")
    incident2 = _incident_row(status="error", error_detail="parse error")
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_connector_registry_row())
    pool.fetch = AsyncMock(return_value=[incident1, incident2])
    _wire_pool(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/gmail/user@example.com/incidents")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_returned"] == 2
    assert len(body["incidents"]) == 2
    statuses = {i["status"] for i in body["incidents"]}
    assert statuses <= {"failed", "error", "replay_failed"}


async def test_connector_incidents_limit_enforced(app):
    """Limit exceeding max (50) is rejected with 422."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_connector_registry_row())
    pool.fetch = AsyncMock(return_value=[])
    _wire_pool(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/ingestion/connectors/gmail/user@example.com/incidents?limit=999"
        )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/{type}/{identity}/routing-rules
# ---------------------------------------------------------------------------


async def test_connector_routing_rules_404_unknown_connector(app):
    """Returns 404 when connector_registry lookup returns None."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    _wire_pool(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/gmail/unknown@example.com/routing-rules")

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


async def test_connector_routing_rules_empty(app):
    """Returns 200 with empty rules list when no rules reference this connector."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_connector_registry_row())
    pool.fetch = AsyncMock(return_value=[])
    _wire_pool(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/gmail/user@example.com/routing-rules")

    assert resp.status_code == 200
    body = resp.json()
    assert body["rules"] == []
    assert body["total_returned"] == 0
    assert body["connector_type"] == "gmail"
    assert body["endpoint_identity"] == "user@example.com"


async def test_connector_routing_rules_populated(app):
    """Returns 200 with rule rows matching connector:type:identity scope."""
    rule = _rule_row(scope="connector:gmail:user@example.com")
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_connector_registry_row())
    pool.fetch = AsyncMock(return_value=[rule])
    _wire_pool(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/gmail/user@example.com/routing-rules")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_returned"] == 1
    assert len(body["rules"]) == 1
    assert body["rules"][0]["scope"] == "connector:gmail:user@example.com"
    assert body["rules"][0]["action"] == "block"


async def test_connector_routing_rules_uses_structured_scope(app):
    """Verifies the SQL query uses the structured scope 'connector:type:identity'."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_connector_registry_row())
    pool.fetch = AsyncMock(return_value=[])
    _wire_pool(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.get("/api/ingestion/connectors/gmail/user@example.com/routing-rules")

    # Verify fetch was called with exactly the connector scope string
    fetch_call = pool.fetch.call_args
    assert fetch_call is not None
    # The first positional arg is the SQL; the second is the scope value
    args = fetch_call.args
    assert len(args) >= 2
    assert args[1] == "connector:gmail:user@example.com"


# ---------------------------------------------------------------------------
# 503 pool-unavailable — every section returns 503 when the pool is missing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "section",
    ["events", "incidents", "routing-rules"],
    ids=["events", "incidents", "routing-rules"],
)
async def test_connector_section_503_pool_unavailable(app, section):
    """Each connector section returns 503 when the switchboard pool is unavailable."""
    _wire_pool_unavailable(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/ingestion/connectors/gmail/user@example.com/{section}")

    assert resp.status_code == 503
