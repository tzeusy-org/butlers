"""Tests for 'discretion' complexity tier support in model settings API.

Covers:
- POST /api/settings/models — accepts 'discretion' as a valid complexity_tier
- PUT /api/settings/models/{id} — accepts 'discretion' when updating
- PUT /api/butlers/{name}/model-overrides — accepts 'discretion' as override tier
- GET /api/butlers/{name}/resolve-model?complexity=discretion — preview works
- 'discretion' is NOT rejected by _validate_complexity_tier
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.model_settings import _COMPLEXITY_TIERS, _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers (mirrors test_model_settings.py)
# ---------------------------------------------------------------------------


def _make_catalog_row(
    *,
    entry_id: uuid.UUID | None = None,
    alias: str = "discretion-qwen3",
    runtime_type: str = "opencode",
    model_id: str = "ollama/qwen3.5:9b",
    extra_args: list[str] | None = None,
    complexity_tier: str = "discretion",
    enabled: bool = True,
    priority: int = 10,
) -> dict[str, Any]:
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
    butler_name: str = "connector",
    catalog_entry_id: uuid.UUID | None = None,
    alias: str = "discretion-qwen3",
    enabled: bool = True,
    priority: int | None = None,
    complexity_tier: str | None = "discretion",
) -> dict[str, Any]:
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
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    for key, value in row.items():
        setattr(m, key, value)
    return m


def _build_app(
    *,
    fetchrow_result: dict[str, Any] | None = None,
    fetch_rows: list[dict[str, Any]] | None = None,
    execute_result: str = "DELETE 1",
) -> tuple[Any, MagicMock]:
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(
        return_value=_mock_record(fetchrow_result) if fetchrow_result else None
    )
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in (fetch_rows or [])])
    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_conn = AsyncMock()
    mock_conn.transaction = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=None),
        )
    )
    override_id_row = _mock_record({"id": uuid.uuid4()})
    mock_conn.fetchrow = AsyncMock(return_value=override_id_row)
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
    return app, mock_db


# ---------------------------------------------------------------------------
# _COMPLEXITY_TIERS constant check
# ---------------------------------------------------------------------------


def test_complexity_tiers_includes_discretion() -> None:
    """The _COMPLEXITY_TIERS constant must include 'discretion'."""
    assert "discretion" in _COMPLEXITY_TIERS


def test_complexity_tiers_still_includes_existing_tiers() -> None:
    """All original tiers must still be present alongside 'discretion'."""
    for tier in ("trivial", "medium", "high", "extra_high"):
        assert tier in _COMPLEXITY_TIERS, f"Missing tier: {tier}"


# ---------------------------------------------------------------------------
# POST /api/settings/models — accepts discretion tier
# ---------------------------------------------------------------------------


class TestCreateCatalogEntryDiscretionTier:
    async def test_creates_entry_with_discretion_tier(self) -> None:
        created_id = uuid.uuid4()
        row = _make_catalog_row(entry_id=created_id, complexity_tier="discretion")
        app, _ = _build_app(fetchrow_result=row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/models",
                json={
                    "alias": "discretion-qwen3",
                    "runtime_type": "opencode",
                    "model_id": "ollama/qwen3.5:9b",
                    "complexity_tier": "discretion",
                    "enabled": True,
                    "priority": 10,
                },
            )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["complexity_tier"] == "discretion"
        assert data["id"] == str(created_id)

    async def test_discretion_not_rejected_by_validator(self) -> None:
        """'discretion' must pass _validate_complexity_tier (no 422)."""
        row = _make_catalog_row(complexity_tier="discretion")
        app, _ = _build_app(fetchrow_result=row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/models",
                json={
                    "alias": "discretion-test",
                    "runtime_type": "opencode",
                    "model_id": "ollama/qwen3.5:9b",
                    "complexity_tier": "discretion",
                },
            )

        # Must not be 422 (invalid tier) — DB layer may return success or error
        assert response.status_code != 422


# ---------------------------------------------------------------------------
# PUT /api/settings/models/{id} — update to discretion tier
# ---------------------------------------------------------------------------


class TestUpdateCatalogEntryDiscretionTier:
    async def test_updates_complexity_tier_to_discretion(self) -> None:
        entry_id = uuid.uuid4()
        updated_row = _make_catalog_row(entry_id=entry_id, complexity_tier="discretion")
        app, _ = _build_app(fetchrow_result=updated_row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/api/settings/models/{entry_id}",
                json={"complexity_tier": "discretion"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["complexity_tier"] == "discretion"


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/model-overrides — upsert with discretion tier
# ---------------------------------------------------------------------------


class TestButlerModelOverrideDiscretionTier:
    async def test_upsert_override_with_discretion_tier(self) -> None:
        catalog_entry_id = uuid.uuid4()
        override_row = _make_override_row(
            catalog_entry_id=catalog_entry_id,
            complexity_tier="discretion",
        )
        app, _ = _build_app(fetch_rows=[override_row])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/butlers/connector/model-overrides",
                json=[
                    {
                        "catalog_entry_id": str(catalog_entry_id),
                        "enabled": True,
                        "complexity_tier": "discretion",
                    }
                ],
            )

        # 422 would indicate the tier was rejected; any other status is acceptable
        assert response.status_code != 422


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/resolve-model?complexity=discretion
# ---------------------------------------------------------------------------


class TestResolveModelDiscretionComplexity:
    async def test_resolve_model_accepts_discretion_complexity(self) -> None:
        """complexity=discretion query parameter must not be rejected (422)."""
        resolve_row = {
            "runtime_type": "opencode",
            "model_id": "ollama/qwen3.5:9b",
            "extra_args": json.dumps([]),
        }
        app, _ = _build_app(fetchrow_result=resolve_row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/butlers/connector/resolve-model",
                params={"complexity": "discretion"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["complexity"] == "discretion"
        assert data["resolved"] is True
        assert data["model_id"] == "ollama/qwen3.5:9b"

    async def test_resolve_model_returns_unresolved_when_no_match(self) -> None:
        """resolve-model returns resolved=False gracefully for discretion tier with no entries."""
        app, _ = _build_app(fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/butlers/connector/resolve-model",
                params={"complexity": "discretion"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["resolved"] is False
        assert data["complexity"] == "discretion"
