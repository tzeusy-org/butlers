"""Tests for the secrets CRUD API endpoints.

Verifies the API contract (status codes, response shapes, security invariants)
for the secrets endpoints.  Uses mocked DatabaseManager and CredentialStore
so no real database is required.

Coverage:
- GET  /api/butlers/{name}/secrets   — list, filter by category, empty, 503
- GET  /api/butlers/{name}/secrets/{key} — found, 404, 503
- PUT  /api/butlers/{name}/secrets/{key} — upsert, validation error, 503
- DELETE /api/butlers/{name}/secrets/{key} — deleted, 404, 503
- Security: values never in response
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets import _get_db_manager
from butlers.credential_store import SecretMetadata

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_metadata(
    key: str = "MY_KEY",
    *,
    category: str = "general",
    description: str | None = None,
    is_sensitive: bool = True,
    is_set: bool = True,
    expires_at: datetime | None = None,
) -> SecretMetadata:
    """Build a SecretMetadata dataclass instance."""
    return SecretMetadata(
        key=key,
        category=category,
        description=description,
        is_sensitive=is_sensitive,
        is_set=is_set,
        created_at=_NOW,
        updated_at=_NOW,
        expires_at=expires_at,
        source="database",
    )


@contextmanager
def _app_with_mock_store(
    app: FastAPI,
    *,
    list_return: list[SecretMetadata] | None = None,
    store_side_effect: Exception | None = None,
    delete_return: bool = True,
    pool_side_effect: Exception | None = None,
):
    """Wire a FastAPI app with a mocked DatabaseManager and CredentialStore.

    All CredentialStore calls are patched at the module level so the router's
    ``_credential_store_for`` helper returns the same mock for every test.
    """
    mock_pool = MagicMock()

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_side_effect:
        mock_db.pool.side_effect = pool_side_effect
    else:
        mock_db.pool.return_value = mock_pool

    mock_store = AsyncMock()
    mock_store.list_secrets.return_value = list_return or []
    if store_side_effect:
        mock_store.store.side_effect = store_side_effect
    else:
        mock_store.store.return_value = None
    mock_store.delete.return_value = delete_return

    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    # Patch CredentialStore constructor so it returns our mock regardless of
    # which pool is provided.
    with patch("butlers.api.routers.secrets.CredentialStore", return_value=mock_store):
        yield app, mock_store


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/secrets — list
# ---------------------------------------------------------------------------


class TestListSecrets:
    async def test_returns_list_of_entries(self, app):
        """Response wraps a list of SecretEntry objects (no values)."""
        metas = [
            _make_metadata("KEY_A", category="telegram"),
            _make_metadata("KEY_B", category="email"),
        ]
        with _app_with_mock_store(app, list_return=metas) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 2
        assert body["data"][0]["key"] == "KEY_A"
        assert body["data"][1]["key"] == "KEY_B"

    async def test_empty_list_returns_empty_array(self, app):
        """When no secrets exist, return empty data list."""
        with _app_with_mock_store(app, list_return=[]) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets")

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_category_filter_passed_to_store(self, app):
        """Category query parameter is forwarded to CredentialStore.list_secrets()."""
        with _app_with_mock_store(app, list_return=[]) as (app, store):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.get("/api/butlers/atlas/secrets?category=telegram")

        store.list_secrets.assert_called_once_with(category="telegram")

    async def test_no_category_passes_none_to_store(self, app):
        """When ?category= is absent, None is passed to list_secrets()."""
        with _app_with_mock_store(app, list_return=[]) as (app, store):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.get("/api/butlers/atlas/secrets")

        store.list_secrets.assert_called_once_with(category=None)

    async def test_response_never_includes_value(self, app):
        """Response entries must not have a 'value' field."""
        metas = [_make_metadata("SECRET")]
        with _app_with_mock_store(app, list_return=metas) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets")

        entry = resp.json()["data"][0]
        assert "value" not in entry
        assert "secret_value" not in entry

    async def test_is_set_present_in_response(self, app):
        """Response entries must include is_set boolean."""
        metas = [_make_metadata("KEY", is_set=True)]
        with _app_with_mock_store(app, list_return=metas) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets")

        assert resp.json()["data"][0]["is_set"] is True

    async def test_db_unavailable_returns_503(self, app):
        """When the butler's DB pool is not available, return 503."""
        with _app_with_mock_store(app, pool_side_effect=KeyError("no pool")) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/nonexistent/secrets")

        assert resp.status_code == 503

    async def test_shared_target_uses_shared_credential_pool(self, app):
        """The reserved `shared` target must resolve via credential_shared_pool()."""
        mock_pool = MagicMock()
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        mock_store = AsyncMock()
        mock_store.list_secrets.return_value = []

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        with patch(
            "butlers.api.routers.secrets.CredentialStore",
            return_value=mock_store,
        ) as store_ctor:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/shared/secrets")

        assert resp.status_code == 200
        mock_db.credential_shared_pool.assert_called_once_with()
        mock_db.pool.assert_not_called()
        store_ctor.assert_called_once_with(mock_pool)

    async def test_shared_target_without_shared_pool_returns_503(self, app):
        """`shared` target should return 503 when shared credential pool is unavailable."""
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = KeyError("no shared pool")

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/shared/secrets")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Shared credential database is not available"

    async def test_metadata_fields_returned(self, app):
        """Response entries contain category, description, is_sensitive, expires_at."""
        meta = _make_metadata(
            "K",
            category="telegram",
            description="Bot token",
            is_sensitive=True,
            expires_at=None,
        )
        with _app_with_mock_store(app, list_return=[meta]) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets")

        entry = resp.json()["data"][0]
        assert entry["category"] == "telegram"
        assert entry["description"] == "Bot token"
        assert entry["is_sensitive"] is True
        assert entry["expires_at"] is None


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/secrets/{key} — get single secret
# ---------------------------------------------------------------------------


class TestGetSecret:
    async def test_returns_single_entry_when_found(self, app):
        """When key exists, return SecretEntry wrapped in ApiResponse."""
        meta = _make_metadata("MY_KEY", category="core")
        with _app_with_mock_store(app, list_return=[meta]) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets/MY_KEY")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["key"] == "MY_KEY"
        assert body["data"]["category"] == "core"

    async def test_missing_key_returns_404(self, app):
        """A non-existent key should return 404."""
        with _app_with_mock_store(app, list_return=[]) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets/MISSING")

        assert resp.status_code == 404

    async def test_db_unavailable_returns_503(self, app):
        """When the butler's DB pool is not available, return 503."""
        with _app_with_mock_store(app, pool_side_effect=KeyError("no pool")) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/nonexistent/secrets/KEY")

        assert resp.status_code == 503

    async def test_response_never_includes_value(self, app):
        """Single-key response must not have a 'value' field."""
        meta = _make_metadata("KEY")
        with _app_with_mock_store(app, list_return=[meta]) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets/KEY")

        entry = resp.json()["data"]
        assert "value" not in entry
        assert "secret_value" not in entry

    async def test_is_set_in_response(self, app):
        """Single-key response includes is_set boolean."""
        meta = _make_metadata("KEY", is_set=True)
        with _app_with_mock_store(app, list_return=[meta]) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets/KEY")

        assert resp.json()["data"]["is_set"] is True


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/secrets/{key} — upsert
# ---------------------------------------------------------------------------


class TestUpsertSecret:
    async def test_upsert_calls_store_and_returns_entry(self, app):
        """PUT stores the secret and returns metadata (no value echo)."""
        meta = _make_metadata("NEW_KEY")
        with _app_with_mock_store(app, list_return=[meta]) as (app, store):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    "/api/butlers/atlas/secrets/NEW_KEY",
                    json={"value": "super-secret"},
                )

        assert resp.status_code == 200
        store.store.assert_called_once()
        call_kwargs = store.store.call_args
        # value passed to store but NOT in response
        assert call_kwargs[0][1] == "super-secret"
        body = resp.json()
        assert body["data"]["key"] == "NEW_KEY"
        assert "value" not in body["data"]

    async def test_upsert_passes_optional_fields(self, app):
        """All optional fields (category, description, is_sensitive, expires_at) forwarded."""
        meta = _make_metadata("K", category="telegram", description="Bot token")
        with _app_with_mock_store(app, list_return=[meta]) as (app, store):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.put(
                    "/api/butlers/atlas/secrets/K",
                    json={
                        "value": "tok_123",
                        "category": "telegram",
                        "description": "Bot token",
                        "is_sensitive": True,
                        "expires_at": None,
                    },
                )

        _, kwargs = store.store.call_args
        assert kwargs["category"] == "telegram"
        assert kwargs["description"] == "Bot token"
        assert kwargs["is_sensitive"] is True

    async def test_missing_value_field_returns_422(self, app):
        """If 'value' is omitted from PUT body, return 422 validation error."""
        with _app_with_mock_store(app) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    "/api/butlers/atlas/secrets/KEY",
                    json={"category": "core"},
                )

        assert resp.status_code == 422

    async def test_empty_value_returns_422(self, app):
        """An empty string 'value' should fail Pydantic validation (min_length=1)."""
        with _app_with_mock_store(app) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    "/api/butlers/atlas/secrets/KEY",
                    json={"value": ""},
                )

        assert resp.status_code == 422

    async def test_db_unavailable_returns_503(self, app):
        """When the butler's DB pool is not available, return 503."""
        with _app_with_mock_store(app, pool_side_effect=KeyError("no pool")) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    "/api/butlers/nonexistent/secrets/KEY",
                    json={"value": "v"},
                )

        assert resp.status_code == 503

    async def test_response_never_echoes_value(self, app):
        """PUT response must not include the submitted value."""
        meta = _make_metadata("KEY")
        with _app_with_mock_store(app, list_return=[meta]) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    "/api/butlers/atlas/secrets/KEY",
                    json={"value": "TOP_SECRET"},
                )

        body = resp.json()
        assert resp.status_code == 200
        assert "value" not in body["data"]
        assert "TOP_SECRET" not in str(body)

    async def test_store_value_error_returns_422(self, app):
        """When CredentialStore.store() raises ValueError, return 422."""
        err = ValueError("key must be a non-empty string")
        with _app_with_mock_store(app, store_side_effect=err) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    "/api/butlers/atlas/secrets/KEY",
                    json={"value": "val"},
                )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/butlers/{name}/secrets/{key} — delete
# ---------------------------------------------------------------------------


class TestDeleteSecret:
    async def test_delete_returns_200_with_status(self, app):
        """Successful delete returns 200 with key and status=deleted."""
        with _app_with_mock_store(app, delete_return=True) as (app, store):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete("/api/butlers/atlas/secrets/MY_KEY")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["key"] == "MY_KEY"
        assert body["data"]["status"] == "deleted"
        store.delete.assert_called_once_with("MY_KEY")

    async def test_missing_key_returns_404(self, app):
        """When key does not exist, delete returns 404."""
        with _app_with_mock_store(app, delete_return=False) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete("/api/butlers/atlas/secrets/MISSING")

        assert resp.status_code == 404

    async def test_db_unavailable_returns_503(self, app):
        """When the butler's DB pool is not available, return 503."""
        with _app_with_mock_store(app, pool_side_effect=KeyError("no pool")) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete("/api/butlers/nonexistent/secrets/KEY")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Partial-update behaviour — existing metadata must be preserved
# ---------------------------------------------------------------------------


class TestUpsertPreservesExistingMetadata:
    async def test_omitting_category_preserves_existing(self, app):
        """When category is omitted in PUT, the existing category must be preserved."""
        existing = _make_metadata("KEY", category="telegram", is_sensitive=True)
        # After upsert, CredentialStore.list_secrets returns the updated record
        # still with category='telegram' (preserved).
        updated = _make_metadata("KEY", category="telegram", is_sensitive=True)
        with _app_with_mock_store(app, list_return=[existing]) as (app, store):
            # Override list_secrets to return 'existing' on the first call and
            # 'updated' on the second (post-store re-read).
            call_count = 0

            async def _list_side_effect(**kwargs):
                nonlocal call_count
                call_count += 1
                return [existing] if call_count == 1 else [updated]

            store.list_secrets.side_effect = _list_side_effect

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    "/api/butlers/atlas/secrets/KEY",
                    json={"value": "new-value"},
                )

        assert resp.status_code == 200
        # The store call must have used the existing category, not "general"
        _, kwargs = store.store.call_args
        assert kwargs["category"] == "telegram"

    async def test_omitting_is_sensitive_preserves_existing(self, app):
        """When is_sensitive is omitted in PUT, existing is_sensitive is preserved."""
        existing = _make_metadata("KEY", is_sensitive=False)
        updated = _make_metadata("KEY", is_sensitive=False)
        with _app_with_mock_store(app, list_return=[existing]) as (app, store):
            call_count = 0

            async def _list_side_effect(**kwargs):
                nonlocal call_count
                call_count += 1
                return [existing] if call_count == 1 else [updated]

            store.list_secrets.side_effect = _list_side_effect

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.put(
                    "/api/butlers/atlas/secrets/KEY",
                    json={"value": "new-value"},
                )

        _, kwargs = store.store.call_args
        # is_sensitive should be False (existing), not True (the old default)
        assert kwargs["is_sensitive"] is False

    async def test_new_secret_uses_defaults_when_fields_omitted(self, app):
        """When creating a new secret (key not found), defaults are used for omitted fields."""
        new_meta = _make_metadata("NEW_KEY", category="general", is_sensitive=True)
        with _app_with_mock_store(app, list_return=[new_meta]) as (app, store):
            # No existing secret — first list_secrets returns empty
            call_count = 0

            async def _list_side_effect(**kwargs):
                nonlocal call_count
                call_count += 1
                return [] if call_count == 1 else [new_meta]

            store.list_secrets.side_effect = _list_side_effect

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    "/api/butlers/atlas/secrets/NEW_KEY",
                    json={"value": "val"},
                )

        assert resp.status_code == 200
        _, kwargs = store.store.call_args
        assert kwargs["category"] == "general"
        assert kwargs["is_sensitive"] is True
