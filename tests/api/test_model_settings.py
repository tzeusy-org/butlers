"""Tests for model catalog CRUD and butler model override endpoints.

Covers:
- GET /api/settings/models — list catalog entries
- POST /api/settings/models — create entry (409 on duplicate alias)
- PUT /api/settings/models/{id} — update entry
- DELETE /api/settings/models/{id} — delete with cascade check
- GET /api/butlers/{name}/model-overrides — list overrides
- PUT /api/butlers/{name}/model-overrides — batch upsert
- DELETE /api/butlers/{name}/model-overrides/{id} — single delete
- GET /api/butlers/{name}/resolve-model?complexity=X — preview
- TriggerRequest: complexity field is accepted and forwarded
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.model_settings import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_COMPLEXITY_TIERS = ("trivial", "medium", "high", "extra_high")


def _make_catalog_row(
    *,
    entry_id: uuid.UUID | None = None,
    alias: str = "claude-sonnet",
    runtime_type: str = "claude-code",
    model_id: str = "claude-sonnet-4-6",
    extra_args: list[str] | None = None,
    complexity_tier: str = "medium",
    enabled: bool = True,
    priority: int = 0,
) -> dict[str, Any]:
    """Build a fake asyncpg record dict for shared.model_catalog."""
    return {
        "id": entry_id or uuid.uuid4(),
        "alias": alias,
        "runtime_type": runtime_type,
        "model_id": model_id,
        "extra_args": json.dumps(extra_args or []),
        "complexity_tier": complexity_tier,
        "enabled": enabled,
        "priority": priority,
    }


def _make_override_row(
    *,
    override_id: uuid.UUID | None = None,
    butler_name: str = "general",
    catalog_entry_id: uuid.UUID | None = None,
    alias: str = "claude-sonnet",
    enabled: bool = True,
    priority: int | None = None,
    complexity_tier: str | None = None,
) -> dict[str, Any]:
    """Build a fake asyncpg record dict for shared.butler_model_overrides."""
    return {
        "id": override_id or uuid.uuid4(),
        "butler_name": butler_name,
        "catalog_entry_id": catalog_entry_id or uuid.uuid4(),
        "alias": alias,
        "enabled": enabled,
        "priority": priority,
        "complexity_tier": complexity_tier,
    }


def _mock_record(row: dict[str, Any]) -> MagicMock:
    """Create a MagicMock that behaves like an asyncpg Record for the given dict."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    for key, value in row.items():
        setattr(m, key, value)
    return m


def _build_app_with_pool(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    fetchrow_result: dict[str, Any] | None = None,
    fetchval_result: Any = None,
    execute_result: str = "DELETE 1",
) -> tuple[Any, MagicMock, MagicMock]:
    """Create a test app with a mocked shared credential pool.

    Returns (app, mock_pool, mock_db).
    """
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in (fetch_rows or [])])
    mock_pool.fetchrow = AsyncMock(
        return_value=_mock_record(fetchrow_result) if fetchrow_result else None
    )
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)

    # Simulate acquire() as async context manager returning mock_pool
    mock_conn = AsyncMock()
    mock_conn.transaction = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=None),
        )
    )
    mock_conn.fetchrow = mock_pool.fetchrow
    mock_pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        )
    )

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_pool, mock_db


# ---------------------------------------------------------------------------
# GET /api/settings/models
# ---------------------------------------------------------------------------


class TestListCatalogEntries:
    async def test_returns_empty_list_when_no_entries(self, app):
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/models")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []

    async def test_returns_entries_from_db(self, app):
        rows = [
            _make_catalog_row(alias="claude-haiku", complexity_tier="trivial", priority=0),
            _make_catalog_row(alias="claude-sonnet", complexity_tier="medium", priority=0),
        ]
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in rows])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/models")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 2
        assert {e["alias"] for e in data} == {"claude-haiku", "claude-sonnet"}

    async def test_returns_503_when_shared_pool_unavailable(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = KeyError("No shared pool")

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/models")

        assert response.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/settings/models
# ---------------------------------------------------------------------------


class TestCreateCatalogEntry:
    async def test_creates_entry_successfully(self, app):
        created_id = uuid.uuid4()
        row = _make_catalog_row(
            entry_id=created_id,
            alias="new-model",
            runtime_type="codex",
            model_id="gpt-5.1",
            complexity_tier="medium",
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
                "/api/settings/models",
                json={
                    "alias": "new-model",
                    "runtime_type": "codex",
                    "model_id": "gpt-5.1",
                    "complexity_tier": "medium",
                    "enabled": True,
                    "priority": 0,
                },
            )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["alias"] == "new-model"
        assert data["id"] == str(created_id)

    async def test_returns_409_on_duplicate_alias(self, app):
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
                "/api/settings/models",
                json={
                    "alias": "claude-sonnet",
                    "runtime_type": "claude-code",
                    "model_id": "claude-sonnet-4-6",
                    "complexity_tier": "medium",
                },
            )

        assert response.status_code == 409

    async def test_returns_422_for_invalid_complexity_tier(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = AsyncMock()

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/models",
                json={
                    "alias": "bad-tier",
                    "runtime_type": "codex",
                    "model_id": "gpt-5.1",
                    "complexity_tier": "extreme",
                },
            )

        assert response.status_code == 422

    async def test_missing_required_fields_returns_422(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = AsyncMock()

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/settings/models", json={"alias": "only-alias"})

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# PUT /api/settings/models/{id}
# ---------------------------------------------------------------------------


class TestUpdateCatalogEntry:
    async def test_updates_entry_fields(self, app):
        entry_id = uuid.uuid4()
        updated_row = _make_catalog_row(
            entry_id=entry_id,
            alias="claude-sonnet",
            enabled=False,
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
                f"/api/settings/models/{entry_id}",
                json={"enabled": False},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["enabled"] is False

    async def test_returns_404_when_not_found(self, app):
        entry_id = uuid.uuid4()
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/api/settings/models/{entry_id}",
                json={"enabled": True},
            )

        assert response.status_code == 404

    async def test_returns_422_when_no_fields_provided(self, app):
        entry_id = uuid.uuid4()
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = AsyncMock()

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(f"/api/settings/models/{entry_id}", json={})

        assert response.status_code == 422

    async def test_returns_409_on_alias_collision(self, app):
        entry_id = uuid.uuid4()
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
            response = await client.put(
                f"/api/settings/models/{entry_id}",
                json={"alias": "claude-haiku"},
            )

        assert response.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /api/settings/models/{id}
# ---------------------------------------------------------------------------


class TestDeleteCatalogEntry:
    async def test_deletes_existing_entry(self, app):
        entry_id = uuid.uuid4()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="DELETE 1")
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(f"/api/settings/models/{entry_id}")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["deleted"] is True

    async def test_returns_404_when_not_found(self, app):
        entry_id = uuid.uuid4()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="DELETE 0")
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(f"/api/settings/models/{entry_id}")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/model-overrides
# ---------------------------------------------------------------------------


class TestListButlerModelOverrides:
    async def test_returns_empty_when_no_overrides(self, app):
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/model-overrides")

        assert response.status_code == 200
        assert response.json()["data"] == []

    async def test_returns_overrides_with_alias(self, app):
        entry_id = uuid.uuid4()
        override_id = uuid.uuid4()
        rows = [
            _make_override_row(
                override_id=override_id,
                butler_name="general",
                catalog_entry_id=entry_id,
                alias="claude-opus",
                enabled=True,
                priority=5,
                complexity_tier="high",
            )
        ]
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in rows])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/model-overrides")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 1
        item = data[0]
        assert item["alias"] == "claude-opus"
        assert item["butler_name"] == "general"
        assert item["priority"] == 5
        assert item["complexity_tier"] == "high"


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/model-overrides — batch upsert
# ---------------------------------------------------------------------------


class TestUpsertButlerModelOverrides:
    async def test_upserts_single_override(self, app):
        override_id = uuid.uuid4()
        entry_id = uuid.uuid4()

        upsert_row = {"id": override_id}
        result_row = _make_override_row(
            override_id=override_id,
            butler_name="general",
            catalog_entry_id=entry_id,
            alias="gpt-5.1",
            enabled=True,
            priority=None,
            complexity_tier=None,
        )

        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.transaction = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=None),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        mock_conn.fetchrow = AsyncMock(return_value=_mock_record(upsert_row))
        mock_pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        mock_pool.fetch = AsyncMock(return_value=[_mock_record(result_row)])

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/butlers/general/model-overrides",
                json=[
                    {
                        "catalog_entry_id": str(entry_id),
                        "enabled": True,
                        "priority": None,
                        "complexity_tier": None,
                    }
                ],
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["alias"] == "gpt-5.1"

    async def test_returns_422_for_empty_body(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = AsyncMock()

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put("/api/butlers/general/model-overrides", json=[])

        assert response.status_code == 422

    async def test_returns_422_for_invalid_complexity_tier(self, app):
        entry_id = uuid.uuid4()
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = AsyncMock()

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/butlers/general/model-overrides",
                json=[
                    {
                        "catalog_entry_id": str(entry_id),
                        "enabled": True,
                        "complexity_tier": "extreme",
                    }
                ],
            )

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/butlers/{name}/model-overrides/{id}
# ---------------------------------------------------------------------------


class TestDeleteButlerModelOverride:
    async def test_deletes_existing_override(self, app):
        override_id = uuid.uuid4()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="DELETE 1")
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(f"/api/butlers/general/model-overrides/{override_id}")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["deleted"] is True

    async def test_returns_404_when_not_found(self, app):
        override_id = uuid.uuid4()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="DELETE 0")
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(f"/api/butlers/general/model-overrides/{override_id}")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/resolve-model
# ---------------------------------------------------------------------------


class TestResolveModelPreview:
    async def test_returns_resolved_model(self, app):
        row = {
            "runtime_type": "claude-code",
            "model_id": "claude-sonnet-4-6",
            "extra_args": "[]",
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(row))
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/resolve-model?complexity=medium")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["resolved"] is True
        assert data["runtime_type"] == "claude-code"
        assert data["model_id"] == "claude-sonnet-4-6"
        assert data["complexity"] == "medium"
        assert data["butler_name"] == "general"

    async def test_returns_resolved_false_when_no_match(self, app):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/resolve-model?complexity=trivial")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["resolved"] is False
        assert data["runtime_type"] is None

    async def test_defaults_to_medium_complexity(self, app):
        row = {
            "runtime_type": "claude-code",
            "model_id": "claude-sonnet-4-6",
            "extra_args": "[]",
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(row))
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # No complexity query param — defaults to "medium"
            response = await client.get("/api/butlers/general/resolve-model")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["complexity"] == "medium"

    async def test_returns_422_for_invalid_complexity(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = AsyncMock()

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/resolve-model?complexity=ultra")

        assert response.status_code == 422

    @pytest.mark.parametrize("tier", _VALID_COMPLEXITY_TIERS)
    async def test_all_valid_complexity_tiers_accepted(self, app, tier: str):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/butlers/general/resolve-model?complexity={tier}")

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# TriggerRequest complexity field
# ---------------------------------------------------------------------------


class TestTriggerRequestComplexityField:
    """Verify TriggerRequest now carries an optional complexity field."""

    def test_trigger_request_accepts_complexity(self):
        from butlers.api.models import TriggerRequest

        req = TriggerRequest(prompt="hello", complexity="high")
        assert req.complexity == "high"

    def test_trigger_request_defaults_complexity_to_medium(self):
        from butlers.api.models import TriggerRequest

        req = TriggerRequest(prompt="hello")
        assert req.complexity == "medium"

    async def test_trigger_endpoint_accepts_complexity_field(self, app):
        """POST /api/butlers/{name}/trigger accepts complexity and includes it in MCP call."""
        import json
        from unittest.mock import AsyncMock, MagicMock

        from butlers.api.db import DatabaseManager
        from butlers.api.deps import (
            ButlerConnectionInfo,
            MCPClientManager,
            get_butler_configs,
            get_mcp_manager,
        )
        from butlers.api.routers.butlers import _get_db_manager as butlers_get_db

        trigger_data = {"session_id": "s-123", "success": True, "output": "done"}
        content_block = MagicMock()
        content_block.text = json.dumps(trigger_data)
        result = MagicMock()
        result.content = [content_block]
        result.is_error = False

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=result)
        mock_mcp = MagicMock(spec=MCPClientManager)
        mock_mcp.get_client = AsyncMock(return_value=mock_client)

        mock_audit_pool = AsyncMock()
        mock_audit_pool.execute = AsyncMock()
        mock_audit_db = MagicMock(spec=DatabaseManager)
        mock_audit_db.pool.return_value = mock_audit_pool

        configs = [ButlerConnectionInfo("general", 40101)]
        app.dependency_overrides[get_butler_configs] = lambda: configs
        app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp
        app.dependency_overrides[butlers_get_db] = lambda: mock_audit_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/general/trigger",
                json={"prompt": "run something", "complexity": "high"},
            )

        assert response.status_code == 200
        # Verify complexity was forwarded to MCP call_tool
        mock_client.call_tool.assert_called_once_with(
            "trigger", {"prompt": "run something", "complexity": "high"}
        )
