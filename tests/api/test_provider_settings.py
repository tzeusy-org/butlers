"""Tests for provider configuration CRUD and connectivity-test endpoints.

Covers:
- GET  /api/settings/providers            — list all configured providers
- POST /api/settings/providers            — register (409 on duplicate)
- PUT  /api/settings/providers/{type}     — update (404 not found, 422 no fields)
- DELETE /api/settings/providers/{type}   — remove (404 not found)
- POST /api/settings/providers/{type}/test-connectivity — probe URL (mock HTTP)
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


_DEFAULT_OLLAMA_CONFIG = {"base_url": "http://gpu-box:11434"}


def _make_provider_row(
    *,
    provider_type: str = "ollama",
    display_name: str = "Ollama (tailnet)",
    config: dict[str, Any] | None = None,
    enabled: bool = False,
) -> dict[str, Any]:
    """Build a fake asyncpg record dict for shared.provider_config.

    When ``config`` is None (the default), a sensible Ollama config is used.
    Pass an explicit ``{}`` for an empty config.
    """
    effective_config = _DEFAULT_OLLAMA_CONFIG if config is None else config
    return {
        "provider_type": provider_type,
        "display_name": display_name,
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


# ---------------------------------------------------------------------------
# GET /api/settings/providers
# ---------------------------------------------------------------------------


class TestListProviders:
    async def test_returns_empty_list_when_no_providers(self, app):
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/providers")

        assert response.status_code == 200
        assert response.json()["data"] == []

    async def test_returns_providers_from_db(self, app):
        rows = [
            _make_provider_row(provider_type="ollama", display_name="Ollama (tailnet)"),
            _make_provider_row(
                provider_type="lm-studio",
                display_name="LM Studio",
                config={"base_url": "http://localhost:1234"},
            ),
        ]
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in rows])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/providers")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 2
        types = {p["provider_type"] for p in data}
        assert types == {"ollama", "lm-studio"}

    async def test_returns_503_when_shared_pool_unavailable(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = KeyError("No shared pool")

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/providers")

        assert response.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/settings/providers
# ---------------------------------------------------------------------------


class TestCreateProvider:
    async def test_creates_provider_successfully(self, app):
        row = _make_provider_row(
            provider_type="ollama",
            display_name="Ollama (tailnet)",
            config={"base_url": "http://gpu-box:11434"},
            enabled=True,
        )
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(row))
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/providers",
                json={
                    "provider_type": "ollama",
                    "display_name": "Ollama (tailnet)",
                    "config": {"base_url": "http://gpu-box:11434"},
                    "enabled": True,
                },
            )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["provider_type"] == "ollama"
        assert data["display_name"] == "Ollama (tailnet)"
        assert data["config"]["base_url"] == "http://gpu-box:11434"
        assert data["enabled"] is True

    async def test_returns_409_on_duplicate_provider_type(self, app):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            side_effect=asyncpg.UniqueViolationError("uq_provider_config_type")
        )
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/providers",
                json={
                    "provider_type": "ollama",
                    "display_name": "Ollama duplicate",
                },
            )

        assert response.status_code == 409
        assert "ollama" in response.json()["detail"]

    async def test_returns_422_when_required_fields_missing(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = AsyncMock()

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Missing display_name
            response = await client.post(
                "/api/settings/providers",
                json={"provider_type": "ollama"},
            )

        assert response.status_code == 422

    async def test_defaults_config_to_empty_dict_and_enabled_to_false(self, app):
        row = _make_provider_row(
            provider_type="custom",
            display_name="Custom Provider",
            config={},
            enabled=False,
        )
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(row))
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/providers",
                json={"provider_type": "custom", "display_name": "Custom Provider"},
            )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["config"] == {}
        assert data["enabled"] is False


# ---------------------------------------------------------------------------
# PUT /api/settings/providers/{provider_type}
# ---------------------------------------------------------------------------


class TestUpdateProvider:
    async def test_updates_provider_fields(self, app):
        updated_row = _make_provider_row(
            provider_type="ollama",
            display_name="Ollama updated",
            config={"base_url": "http://new-host:11434"},
            enabled=True,
        )
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(updated_row))
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/settings/providers/ollama",
                json={"enabled": True, "config": {"base_url": "http://new-host:11434"}},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["enabled"] is True
        assert data["config"]["base_url"] == "http://new-host:11434"

    async def test_returns_404_when_provider_not_found(self, app):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/settings/providers/nonexistent",
                json={"enabled": True},
            )

        assert response.status_code == 404

    async def test_returns_422_when_no_fields_provided(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = AsyncMock()

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put("/api/settings/providers/ollama", json={})

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/settings/providers/{provider_type}
# ---------------------------------------------------------------------------


class TestDeleteProvider:
    async def test_deletes_existing_provider(self, app):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="DELETE 1")
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete("/api/settings/providers/ollama")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["deleted"] is True
        assert data["provider_type"] == "ollama"

    async def test_returns_404_when_not_found(self, app):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="DELETE 0")
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete("/api/settings/providers/nonexistent")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/settings/providers/{provider_type}/test-connectivity
# ---------------------------------------------------------------------------


class TestConnectivity:
    async def test_returns_404_when_provider_not_found(self, app):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/settings/providers/ollama/test-connectivity")

        assert response.status_code == 404

    def _make_mock_http_client(
        self, *, status_code: int | None = None, exc: Exception | None = None
    ):
        """Build an async context manager mock for httpx.AsyncClient.

        Returns (mock_cm, mock_inner) where mock_cm is the object returned by
        ``httpx.AsyncClient(...)`` and mock_inner is the object yielded from
        ``async with mock_cm``.
        """
        mock_inner = AsyncMock()
        if exc is not None:
            mock_inner.get = AsyncMock(side_effect=exc)
        else:
            mock_response = MagicMock()
            mock_response.status_code = status_code
            mock_inner.get = AsyncMock(return_value=mock_response)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_inner)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        return mock_cm, mock_inner

    async def test_ollama_probe_success(self, app):
        row = _make_provider_row(
            provider_type="ollama",
            config={"base_url": "http://gpu-box:11434"},
        )
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(row))
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        mock_cm, _ = self._make_mock_http_client(status_code=200)

        # Create test client before entering the patch to avoid the patch
        # affecting httpx.AsyncClient globally (both the test transport and
        # the production code use the same httpx module object).
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "butlers.api.routers.provider_settings.httpx.AsyncClient",
                return_value=mock_cm,
            ):
                response = await client.post("/api/settings/providers/ollama/test-connectivity")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["success"] is True
        assert data["provider_type"] == "ollama"
        assert data["url"] == "http://gpu-box:11434/api/version"
        assert data["status_code"] == 200
        assert data["latency_ms"] >= 0

    async def test_ollama_probe_http_error(self, app):
        row = _make_provider_row(
            provider_type="ollama",
            config={"base_url": "http://gpu-box:11434"},
        )
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(row))
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        mock_cm, _ = self._make_mock_http_client(status_code=503)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "butlers.api.routers.provider_settings.httpx.AsyncClient",
                return_value=mock_cm,
            ):
                response = await client.post("/api/settings/providers/ollama/test-connectivity")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["success"] is False
        assert data["status_code"] == 503
        assert "503" in data["error"]

    async def test_ollama_probe_connection_error(self, app):
        row = _make_provider_row(
            provider_type="ollama",
            config={"base_url": "http://unreachable-host:11434"},
        )
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(row))
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        mock_cm, _ = self._make_mock_http_client(exc=httpx.ConnectError("Connection refused"))

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "butlers.api.routers.provider_settings.httpx.AsyncClient",
                return_value=mock_cm,
            ):
                response = await client.post("/api/settings/providers/ollama/test-connectivity")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["success"] is False
        assert data["error"] is not None
        assert data["url"] == "http://unreachable-host:11434/api/version"

    async def test_unknown_provider_type_returns_no_probe_url_error(self, app):
        row = _make_provider_row(
            provider_type="unknown-type",
            display_name="Unknown",
            config={},
        )
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(row))
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/settings/providers/unknown-type/test-connectivity")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["success"] is False
        assert "No probe URL" in data["error"]

    async def test_503_when_shared_pool_unavailable(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = KeyError("No shared pool")

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/settings/providers/ollama/test-connectivity")

        assert response.status_code == 503
