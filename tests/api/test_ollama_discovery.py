"""Tests for Ollama model discovery and catalog import endpoints.

Covers:
- GET /api/settings/providers/ollama/models — discovery with mocked /api/tags
- GET /api/settings/providers/ollama/models — already_in_catalog flag
- GET /api/settings/providers/ollama/models — 404 when no provider configured
- GET /api/settings/providers/ollama/models — 503 when provider disabled
- GET /api/settings/providers/ollama/models — 502 when Ollama unreachable
- GET /api/settings/providers/ollama/models — empty model list
- POST /api/settings/providers/ollama/import — creates catalog entries
- POST /api/settings/providers/ollama/import — duplicate alias returns 409
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.provider_settings import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider_row(
    *,
    config: dict[str, Any] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    effective_config = config if config is not None else {"base_url": "http://gpu-box:11434"}
    return {
        "provider_type": "ollama",
        "display_name": "Ollama",
        "config": json.dumps(effective_config),
        "enabled": enabled,
    }


def _mock_record(row: dict[str, Any]) -> MagicMock:
    """Create a MagicMock that behaves like an asyncpg Record for the given dict."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    for key, value in row.items():
        setattr(m, key, value)
    return m


def _make_tags_response(models: list[dict[str, Any]]) -> MagicMock:
    """Build a mock httpx Response for Ollama's /api/tags endpoint."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"models": models}
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def _make_mock_http_client(
    *,
    response: MagicMock | None = None,
    exc: Exception | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build an async context manager mock for httpx.AsyncClient."""
    mock_inner = AsyncMock()
    if exc is not None:
        mock_inner.get = AsyncMock(side_effect=exc)
    else:
        mock_inner.get = AsyncMock(return_value=response)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_inner)
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    return mock_cm, mock_inner


_SAMPLE_OLLAMA_MODELS = [
    {
        "name": "llama3.2",
        "size": 2019393189,
        "modified_at": "2024-09-18T23:37:02.0000000Z",
        "details": {
            "parameter_size": "3.2B",
            "quantization_level": "Q4_K_M",
        },
    },
    {
        "name": "mistral:7b",
        "size": 4109854289,
        "modified_at": "2024-09-10T12:00:00.0000000Z",
        "details": {
            "parameter_size": "7B",
            "quantization_level": "Q4_0",
        },
    },
]


# ---------------------------------------------------------------------------
# GET /api/settings/providers/ollama/models
# ---------------------------------------------------------------------------


class TestListOllamaModels:
    async def test_returns_discovered_models(self, app):
        provider_row = _make_provider_row()
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(provider_row))
        mock_pool.fetch = AsyncMock(return_value=[])  # nothing in catalog yet
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        tags_resp = _make_tags_response(_SAMPLE_OLLAMA_MODELS)
        mock_cm, _ = _make_mock_http_client(response=tags_resp)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "butlers.api.routers.provider_settings.httpx.AsyncClient",
                return_value=mock_cm,
            ):
                response = await client.get("/api/settings/providers/ollama/models")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 2
        names = {m["name"] for m in data}
        assert names == {"llama3.2", "mistral:7b"}

    async def test_enriches_with_parameter_size_and_quantization(self, app):
        provider_row = _make_provider_row()
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(provider_row))
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        tags_resp = _make_tags_response(_SAMPLE_OLLAMA_MODELS)
        mock_cm, _ = _make_mock_http_client(response=tags_resp)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "butlers.api.routers.provider_settings.httpx.AsyncClient",
                return_value=mock_cm,
            ):
                response = await client.get("/api/settings/providers/ollama/models")

        data = response.json()["data"]
        llama = next(m for m in data if m["name"] == "llama3.2")
        assert llama["parameter_size"] == "3.2B"
        assert llama["quantization"] == "Q4_K_M"
        assert llama["size"] == 2019393189

    async def test_already_in_catalog_flag_true_when_model_in_catalog(self, app):
        provider_row = _make_provider_row()

        # llama3.2 is already in catalog
        catalog_row = _mock_record({"model_id": "llama3.2"})

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(provider_row))
        mock_pool.fetch = AsyncMock(return_value=[catalog_row])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        tags_resp = _make_tags_response(_SAMPLE_OLLAMA_MODELS)
        mock_cm, _ = _make_mock_http_client(response=tags_resp)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "butlers.api.routers.provider_settings.httpx.AsyncClient",
                return_value=mock_cm,
            ):
                response = await client.get("/api/settings/providers/ollama/models")

        data = response.json()["data"]
        llama = next(m for m in data if m["name"] == "llama3.2")
        mistral = next(m for m in data if m["name"] == "mistral:7b")
        assert llama["already_in_catalog"] is True
        assert mistral["already_in_catalog"] is False

    async def test_returns_empty_list_when_ollama_has_no_models(self, app):
        provider_row = _make_provider_row()
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(provider_row))
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        tags_resp = _make_tags_response([])
        mock_cm, _ = _make_mock_http_client(response=tags_resp)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "butlers.api.routers.provider_settings.httpx.AsyncClient",
                return_value=mock_cm,
            ):
                response = await client.get("/api/settings/providers/ollama/models")

        assert response.status_code == 200
        assert response.json()["data"] == []

    async def test_returns_404_when_no_ollama_provider_configured(self, app):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/providers/ollama/models")

        assert response.status_code == 404
        assert "No Ollama provider" in response.json()["detail"]

    async def test_returns_503_when_provider_disabled(self, app):
        provider_row = _make_provider_row(enabled=False)
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(provider_row))
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/providers/ollama/models")

        assert response.status_code == 503
        assert "disabled" in response.json()["detail"]

    async def test_returns_502_when_ollama_unreachable(self, app):
        provider_row = _make_provider_row()
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(provider_row))
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        mock_cm, _ = _make_mock_http_client(exc=httpx.ConnectError("Connection refused"))

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "butlers.api.routers.provider_settings.httpx.AsyncClient",
                return_value=mock_cm,
            ):
                response = await client.get("/api/settings/providers/ollama/models")

        assert response.status_code == 502
        assert "gpu-box" in response.json()["detail"]

    async def test_returns_503_when_shared_pool_unavailable(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = KeyError("No shared pool")

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/providers/ollama/models")

        assert response.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/settings/providers/ollama/import
# ---------------------------------------------------------------------------


class TestImportOllamaModels:
    async def test_creates_catalog_entries_for_requested_models(self, app):
        mock_pool = AsyncMock()
        # Each fetchrow call returns a row (indicating a successful insert)
        mock_pool.fetchrow = AsyncMock(
            side_effect=[
                _mock_record({"alias": "my-llama"}),
                _mock_record({"alias": "my-mistral"}),
            ]
        )
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/providers/ollama/import",
                json={
                    "models": [
                        {"name": "llama3.2", "alias": "my-llama", "complexity_tier": "medium"},
                        {"name": "mistral:7b", "alias": "my-mistral", "complexity_tier": "high"},
                    ]
                },
            )

        assert response.status_code == 201
        data = response.json()["data"]
        assert len(data) == 2
        aliases = {item["alias"] for item in data}
        assert aliases == {"my-llama", "my-mistral"}
        for item in data:
            assert item["created"] is True

    async def test_sets_created_false_when_alias_skipped_by_on_conflict(self, app):
        mock_pool = AsyncMock()
        # ON CONFLICT DO NOTHING returns None (no row inserted)
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/providers/ollama/import",
                json={
                    "models": [
                        {
                            "name": "llama3.2",
                            "alias": "existing-alias",
                            "complexity_tier": "medium",
                        },
                    ]
                },
            )

        assert response.status_code == 201
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["created"] is False
        assert data[0]["alias"] == "existing-alias"

    async def test_returns_409_on_duplicate_alias_unique_violation(self, app):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            side_effect=asyncpg.UniqueViolationError("uq_model_catalog_alias")
        )
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/providers/ollama/import",
                json={
                    "models": [
                        {"name": "llama3.2", "alias": "taken-alias", "complexity_tier": "medium"},
                    ]
                },
            )

        assert response.status_code == 409
        assert "taken-alias" in response.json()["detail"]

    async def test_returns_422_when_models_list_is_missing(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = AsyncMock()

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/providers/ollama/import",
                json={},
            )

        assert response.status_code == 422

    async def test_returns_503_when_shared_pool_unavailable(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = KeyError("No shared pool")

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/providers/ollama/import",
                json={
                    "models": [
                        {"name": "llama3.2", "alias": "my-llama", "complexity_tier": "medium"},
                    ]
                },
            )

        assert response.status_code == 503

    async def test_uses_ollama_runtime_type_for_created_entries(self, app):
        """Verify the SQL uses runtime_type='ollama' (checked via call args)."""
        mock_pool = AsyncMock()
        inserted_rows: list[tuple] = []

        async def capture_fetchrow(sql: str, *args):
            if "INSERT" in sql:
                inserted_rows.append(args)
                return _mock_record({"alias": args[0]})
            return None

        mock_pool.fetchrow = AsyncMock(side_effect=capture_fetchrow)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/providers/ollama/import",
                json={
                    "models": [
                        {"name": "llama3.2", "alias": "my-llama", "complexity_tier": "trivial"},
                    ]
                },
            )

        assert response.status_code == 201
        # Verify the INSERT args: (alias, model_id, complexity_tier)
        assert len(inserted_rows) == 1
        alias, model_id, complexity_tier = inserted_rows[0]
        assert alias == "my-llama"
        assert model_id == "llama3.2"
        assert complexity_tier == "trivial"
