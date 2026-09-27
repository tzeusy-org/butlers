"""Canonical connector detail, stats, and settings API tests.

The ingestion namespace owns every live connector detail surface.  These
tests protect the migration from the retired Switchboard connector routes:

- detail preserves the flat dashboard payload, including auth/scopes and
  persisted settings;
- archived identities remain inspectable, while soft-deleted rows are absent;
- the stats histogram keeps its distinct filtered-event series; and
- settings validation and content-blind audit metadata survive the move.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.ingestion_connectors import ConnectorSettingsUpdateRequest, _get_db_manager

pytestmark = pytest.mark.unit


def _connector_row(**overrides: object) -> dict[str, object]:
    """Return the registry shape used by canonical detail and settings responses."""
    row: dict[str, object] = {
        "connector_type": "spotify",
        "endpoint_identity": "owner",
        "instance_id": None,
        "version": "1.0.0",
        "state": "healthy",
        "error_message": None,
        "uptime_s": 3600,
        "last_heartbeat_at": datetime(2026, 9, 10, 10, 0, tzinfo=UTC),
        "first_seen_at": datetime(2026, 9, 1, tzinfo=UTC),
        "registered_via": "self",
        "counter_messages_ingested": 42,
        "counter_messages_failed": 1,
        "counter_source_api_calls": 12,
        "counter_checkpoint_saves": 4,
        "counter_dedupe_accepted": 40,
        "today_messages_ingested": 7,
        "today_messages_failed": 0,
        "checkpoint_cursor": "cursor-1",
        "checkpoint_updated_at": datetime(2026, 9, 10, 9, 55, tzinfo=UTC),
        "operational_role": "runtime_instance",
        "parent_endpoint_identity": None,
        "observed_scopes": [],
        "required_scopes_version": 1,
        "settings": {"flush_interval_s": 900},
        "archived_at": datetime(2026, 9, 5, tzinfo=UTC),
    }
    row.update(overrides)
    return row


def _wire_db(app, pool: AsyncMock) -> MagicMock:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: db
    return db


def _wire_stats_connection(pool: AsyncMock) -> AsyncMock:
    """Make the stats route exercise one row lock and one query connection."""
    connection = AsyncMock()
    connection.fetchrow = pool.fetchrow
    connection.fetch = pool.fetch

    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=None)
    connection.transaction = MagicMock(return_value=transaction)

    acquired = MagicMock()
    acquired.__aenter__ = AsyncMock(return_value=connection)
    acquired.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquired)
    return connection


@pytest.mark.parametrize("flush_interval", [60, 7200])
def test_settings_accept_flush_interval_boundaries(flush_interval: int) -> None:
    """The batch-settings bounds remain inclusive after the namespace move."""
    request = ConnectorSettingsUpdateRequest(settings={"flush_interval_s": flush_interval})
    assert request.settings["flush_interval_s"] == flush_interval


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("GET", "/api/switchboard/connectors", None),
        ("GET", "/api/switchboard/connectors/spotify/owner", None),
        ("GET", "/api/switchboard/connectors/spotify/owner/stats", None),
        (
            "PATCH",
            "/api/switchboard/connectors/spotify/owner/settings",
            {"settings": {"flush_interval_s": 300}},
        ),
    ],
)
async def test_retired_switchboard_connector_routes_are_unmounted(
    app,
    method: str,
    path: str,
    json_body: dict[str, object] | None,
) -> None:
    """The removed Switchboard connector family is unreachable, not merely hidden."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.request(method, path, json=json_body)

    assert response.status_code == 404


async def test_canonical_detail_preserves_auth_scopes_and_archived_history(app) -> None:
    """Archived rows remain inspectable through the canonical detail endpoint."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_connector_row())
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/ingestion/connectors/spotify/owner")

    assert response.status_code == 200
    detail = response.json()["data"]
    assert detail["settings"] == {"flush_interval_s": 900}
    assert detail["counter_messages_ingested"] == 42
    assert detail["auth"]["type"] == "oauth"
    assert isinstance(detail["scopes"], list)

    query = pool.fetchrow.await_args.args[0]
    assert "deleted_at IS NULL" in query
    assert "archived_at IS NULL" not in query


async def test_canonical_detail_withholds_opaque_observed_scope_values(app) -> None:
    """A malformed registry observation cannot echo a credential-shaped value."""
    opaque_value = "scope_" + "a+/=" * 16
    safe_extra_scope = "scope-undeclared-x"
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value=_connector_row(
            observed_scopes=[safe_extra_scope, opaque_value],
        )
    )
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/ingestion/connectors/spotify/owner")

    assert response.status_code == 200
    assert opaque_value not in response.text
    scope_names = {scope["name"] for scope in response.json()["data"]["scopes"]}
    assert safe_extra_scope in scope_names
    assert opaque_value not in scope_names


async def test_canonical_detail_and_stats_hide_soft_deleted_rows(app) -> None:
    """Deleted identities cannot expose a detail payload or historical stats."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    _wire_stats_connection(pool)
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        detail_response = await client.get("/api/ingestion/connectors/spotify/deleted")
        stats_response = await client.get("/api/ingestion/connectors/spotify/deleted/stats")
        settings_response = await client.patch(
            "/api/ingestion/connectors/spotify/deleted/settings",
            json={"settings": {"flush_interval_s": 300}},
        )

    assert detail_response.status_code == 404
    assert stats_response.status_code == 404
    assert settings_response.status_code == 404
    assert pool.fetch.await_count == 0
    for call in pool.fetchrow.await_args_list:
        assert "deleted_at IS NULL" in call.args[0]


async def test_canonical_stats_preserve_distinct_filtered_series(app) -> None:
    """Stats retain the filtered-event volume rather than folding it into ingested."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"connector_type": "gmail"})
    pool.fetch = AsyncMock(
        return_value=[
            {
                "bucket": datetime(2026, 9, 10, 10, 0, tzinfo=UTC),
                "messages_ingested": 2,
                "messages_failed": 1,
                "messages_filtered": 3,
            }
        ]
    )
    connection = _wire_stats_connection(pool)
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/ingestion/connectors/gmail/owner/stats?period=24h")

    assert response.status_code == 200
    bucket = response.json()["data"][0]
    assert bucket["messages_ingested"] == 2
    assert bucket["messages_filtered"] == 3
    assert bucket["heartbeat_count"] == 0
    assert bucket["healthy_count"] == 0
    assert response.json()["meta"]["hourly_events_available"] is True
    assert "connectors.filtered_events" in connection.fetch.await_args.args[0]
    assert "FOR UPDATE" in connection.fetchrow.await_args.args[0]


async def test_canonical_settings_keep_validation_and_content_blind_audit(app) -> None:
    """Settings stay shallow-merged and audits record keys, never setting values."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value=_connector_row(
            connector_type="telegram_user_client",
            endpoint_identity="owner",
            observed_scopes=None,
            required_scopes_version=None,
            settings={"flush_interval_s": 300},
        )
    )
    db = _wire_db(app, pool)

    with patch(
        "butlers.api.routers.ingestion_connectors.emit_dashboard_audit",
        new_callable=AsyncMock,
    ) as audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.patch(
                "/api/ingestion/connectors/telegram_user_client/owner/settings",
                json={"settings": {"flush_interval_s": 300}},
            )
            invalid_response = await client.patch(
                "/api/ingestion/connectors/telegram_user_client/owner/settings",
                json={"settings": {"flush_interval_s": 30}},
            )
            too_large_response = await client.patch(
                "/api/ingestion/connectors/telegram_user_client/owner/settings",
                json={"settings": {"flush_interval_s": 7201}},
            )

    assert response.status_code == 200
    assert response.json()["data"]["settings"] == {"flush_interval_s": 300}
    assert invalid_response.status_code == 422
    assert too_large_response.status_code == 422
    assert "deleted_at IS NULL" in pool.fetchrow.await_args.args[0]

    audit.assert_awaited_once_with(
        db,
        butler="switchboard",
        operation="connector_settings_patch",
        method="PATCH",
        path="/api/ingestion/connectors/telegram_user_client/owner/settings",
        path_params={"connector_type": "telegram_user_client", "endpoint_identity": "owner"},
        body={"setting_keys": ["flush_interval_s"]},
        response_status=200,
        request=ANY,
    )
