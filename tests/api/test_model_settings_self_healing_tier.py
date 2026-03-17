"""Tests for 'self_healing' complexity tier support in model settings API.

Covers:
- POST /api/settings/models — accepts 'self_healing' as a valid complexity_tier
- PUT /api/settings/models/{id} — accepts 'self_healing' when updating
- PUT /api/butlers/{name}/model-overrides — accepts 'self_healing' as override tier
- GET /api/butlers/{name}/resolve-model?complexity=self_healing — preview works
- 'self_healing' is NOT rejected by _validate_complexity_tier
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
# Helpers (mirrors test_model_settings_discretion_tier.py)
# ---------------------------------------------------------------------------


def _make_catalog_row(
    *,
    entry_id: uuid.UUID | None = None,
    alias: str = "healing-sonnet",
    runtime_type: str = "claude",
    model_id: str = "claude-sonnet-4-6",
    extra_args: list[str] | None = None,
    complexity_tier: str = "self_healing",
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
    butler_name: str = "email",
    catalog_entry_id: uuid.UUID | None = None,
    alias: str = "healing-sonnet",
    enabled: bool = True,
    priority: int | None = None,
    complexity_tier: str | None = "self_healing",
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


def test_complexity_tiers_includes_self_healing() -> None:
    """The _COMPLEXITY_TIERS constant must include 'self_healing'."""
    assert "self_healing" in _COMPLEXITY_TIERS


def test_complexity_tiers_still_includes_all_existing_tiers() -> None:
    """All prior tiers must still be present alongside 'self_healing'."""
    for tier in ("trivial", "medium", "high", "extra_high", "discretion"):
        assert tier in _COMPLEXITY_TIERS, f"Missing tier: {tier}"


# ---------------------------------------------------------------------------
# POST /api/settings/models — accepts self_healing tier
# ---------------------------------------------------------------------------


class TestCreateCatalogEntrySelfHealingTier:
    async def test_creates_entry_with_self_healing_tier(self) -> None:
        created_id = uuid.uuid4()
        row = _make_catalog_row(entry_id=created_id, complexity_tier="self_healing")
        app, _ = _build_app(fetchrow_result=row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/models",
                json={
                    "alias": "healing-sonnet",
                    "runtime_type": "claude",
                    "model_id": "claude-sonnet-4-6",
                    "complexity_tier": "self_healing",
                    "enabled": True,
                    "priority": 10,
                },
            )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["complexity_tier"] == "self_healing"
        assert data["id"] == str(created_id)

    async def test_self_healing_not_rejected_by_validator(self) -> None:
        """'self_healing' must pass _validate_complexity_tier (no 422)."""
        row = _make_catalog_row(complexity_tier="self_healing")
        app, _ = _build_app(fetchrow_result=row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/models",
                json={
                    "alias": "healing-test",
                    "runtime_type": "claude",
                    "model_id": "claude-sonnet-4-6",
                    "complexity_tier": "self_healing",
                },
            )

        # Must not be 422 (invalid tier) — DB layer may return success or error
        assert response.status_code != 422

    async def test_invalid_tier_still_rejected(self) -> None:
        """An invalid tier must still be rejected with 422 including self_healing in listing."""
        app, _ = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/models",
                json={
                    "alias": "bad-tier",
                    "runtime_type": "claude",
                    "model_id": "claude-sonnet-4-6",
                    "complexity_tier": "super_high",
                },
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "self_healing" in detail


# ---------------------------------------------------------------------------
# PUT /api/settings/models/{id} — update to self_healing tier
# ---------------------------------------------------------------------------


class TestUpdateCatalogEntrySelfHealingTier:
    async def test_updates_complexity_tier_to_self_healing(self) -> None:
        entry_id = uuid.uuid4()
        updated_row = _make_catalog_row(entry_id=entry_id, complexity_tier="self_healing")
        app, _ = _build_app(fetchrow_result=updated_row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/api/settings/models/{entry_id}",
                json={"complexity_tier": "self_healing"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["complexity_tier"] == "self_healing"


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/model-overrides — upsert with self_healing tier
# ---------------------------------------------------------------------------


class TestButlerModelOverrideSelfHealingTier:
    async def test_upsert_override_with_self_healing_tier(self) -> None:
        catalog_entry_id = uuid.uuid4()
        override_row = _make_override_row(
            catalog_entry_id=catalog_entry_id,
            complexity_tier="self_healing",
        )
        app, _ = _build_app(fetch_rows=[override_row])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/butlers/email/model-overrides",
                json=[
                    {
                        "catalog_entry_id": str(catalog_entry_id),
                        "enabled": True,
                        "complexity_tier": "self_healing",
                    }
                ],
            )

        # 422 would indicate the tier was rejected; any other status is acceptable
        assert response.status_code != 422


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/resolve-model?complexity=self_healing
# ---------------------------------------------------------------------------


class TestResolveModelSelfHealingComplexity:
    async def test_resolve_model_accepts_self_healing_complexity(self) -> None:
        """complexity=self_healing query parameter must not be rejected (422)."""
        resolve_row = {
            "runtime_type": "claude",
            "model_id": "claude-sonnet-4-6",
            "extra_args": json.dumps([]),
        }
        app, _ = _build_app(fetchrow_result=resolve_row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/butlers/email/resolve-model",
                params={"complexity": "self_healing"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["complexity"] == "self_healing"
        assert data["resolved"] is True
        assert data["model_id"] == "claude-sonnet-4-6"

    async def test_resolve_model_returns_unresolved_when_no_match(self) -> None:
        """resolve-model returns resolved=False gracefully when no self_healing entries exist."""
        app, _ = _build_app(fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/butlers/email/resolve-model",
                params={"complexity": "self_healing"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["resolved"] is False
        assert data["complexity"] == "self_healing"
